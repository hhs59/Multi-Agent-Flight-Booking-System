from __future__ import annotations

import base64
import hashlib
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import (
    AgentCheckpointRecord,
    BookingIntentRecord,
    ChatMessageRecord,
    ChatThreadRecord,
    FlightOfferRecord,
)
from agent_system.domain.accounts import ChatRole, Locale
from agent_system.domain.conversations import (
    AppendResult,
    CheckpointState,
    CheckpointView,
    ConversationContext,
    MessagePage,
    MessageView,
    ThreadPage,
    ThreadView,
)
from agent_system.providers.clock import Clock, SystemClock
from agent_system.repositories.base import ConcurrencyConflictError, ResourceNotFoundError
from agent_system.repositories.conversations import (
    CheckpointRepository,
    MessageRepository,
    ThreadRepository,
)
from agent_system.security.messages import sanitize_message_text
from agent_system.security.safe_results import SafeResultError, sanitize_safe_result

CHECKPOINT_SCHEMA_VERSION = 3


def _db_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


SUMMARY_PROMPT_VERSION = "conversation-summary-v1"


def _thread_view(record: ChatThreadRecord) -> ThreadView:
    return ThreadView(
        id=record.id,
        user_id=record.user_id,
        title=record.title,
        locale=record.locale,
        archived=record.archived,
        summary=record.summary,
        summary_version=record.summary_version,
        summary_prompt_version=record.summary_prompt_version,
        summarized_through_sequence=record.summarized_through_sequence,
        created_at=_db_utc(record.created_at),
        updated_at=_db_utc(record.updated_at),
    )


def _message_view(record: ChatMessageRecord) -> MessageView:
    result = None
    if isinstance(record.safe_result, dict):
        try:
            result = sanitize_safe_result(record.safe_result)
        except SafeResultError:
            # Old or corrupt structured payloads must not break chat history.
            result = None
    return MessageView(
        id=record.id,
        user_id=record.user_id,
        thread_id=record.thread_id,
        role=record.role,
        content=record.content,
        sequence=record.sequence,
        client_message_id=record.client_message_id,
        result=result,
        created_at=_db_utc(record.created_at),
    )


def migrate_checkpoint_state(raw: dict, schema_version: int) -> CheckpointState:
    if schema_version < 1 or schema_version > CHECKPOINT_SCHEMA_VERSION:
        raise ValueError(f"unsupported checkpoint schema version: {schema_version}")
    migrated = dict(raw)
    if schema_version == 1:
        migrated = {
            "current_intent": migrated.pop("intent", None),
            "plan": migrated.pop("plan", ()),
            "selected_offer_id": migrated.pop("selected_offer_id", None),
            "booking_intent_id": migrated.pop("booking_intent_id", None),
            "watch_draft_id": migrated.pop("watch_draft_id", None),
            "safe_context": migrated.pop("safe_context", migrated),
        }
    safe_context = migrated.get("safe_context")
    if not isinstance(safe_context, dict):
        safe_context = {}
    else:
        # Phase 1/2 checkpoints could contain the complete action result here.
        # Discard that legacy field explicitly while retaining bounded projections.
        safe_context = dict(safe_context)
        safe_context.pop("last_result", None)
    migrated["safe_context"] = safe_context
    migrated.pop("state_schema_version", None)
    try:
        return CheckpointState.model_validate(migrated)
    except ValueError:
        # A legacy checkpoint with an untrusted projection is safely discarded,
        # while the durable checkpoint record remains available for audit.
        migrated["safe_context"] = {}
        return CheckpointState.model_validate(migrated)


def _checkpoint_view(record: AgentCheckpointRecord, state: CheckpointState) -> CheckpointView:
    return CheckpointView(
        id=record.id,
        user_id=record.user_id,
        thread_id=record.thread_id,
        version=record.version,
        state_schema_version=CHECKPOINT_SCHEMA_VERSION,
        state=state,
        last_message_id=record.last_message_id,
        created_at=_db_utc(record.created_at),
    )


def _encode_cursor(updated_at: datetime, resource_id: UUID) -> str:
    payload = json.dumps(
        {"updated_at": _db_utc(updated_at).isoformat(), "id": str(resource_id)},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        payload = json.loads(base64.urlsafe_b64decode(padded))
        updated_at = datetime.fromisoformat(payload["updated_at"])
        if updated_at.tzinfo is None:
            raise ValueError
        return updated_at, UUID(payload["id"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError("invalid pagination cursor") from exc


class ThreadService:
    def __init__(self, session: Session, *, clock: Clock | None = None) -> None:
        self.session = session
        self.clock = clock or SystemClock()

    def create(
        self,
        principal: AuthenticatedPrincipal,
        *,
        title: str | None = None,
        locale: Locale = Locale.VI,
    ) -> ThreadView:
        if title is not None:
            title = title.strip() or None
        if title is not None and len(title) > 200:
            raise ValueError("thread title must be at most 200 characters")
        record = ThreadRepository(self.session, principal).add(
            ChatThreadRecord(
                user_id=principal.user_id,
                title=title,
                locale=locale.value,
            )
        )
        return _thread_view(record)

    def get(self, principal: AuthenticatedPrincipal, thread_id: UUID) -> ThreadView:
        return _thread_view(ThreadRepository(self.session, principal).require(thread_id))

    def list(
        self,
        principal: AuthenticatedPrincipal,
        *,
        archived: bool = False,
        cursor: str | None = None,
        limit: int = 20,
    ) -> ThreadPage:
        if limit < 1 or limit > 100:
            raise ValueError("limit must be between 1 and 100")
        before = _decode_cursor(cursor) if cursor else None
        rows = ThreadRepository(self.session, principal).list_page(
            archived=archived,
            limit=limit + 1,
            before=before,
        )
        has_more = len(rows) > limit
        page_rows = rows[:limit]
        next_cursor = None
        if has_more and page_rows:
            last = page_rows[-1]
            next_cursor = _encode_cursor(last.updated_at, last.id)
        return ThreadPage(
            items=tuple(_thread_view(row) for row in page_rows), next_cursor=next_cursor
        )

    def rename(
        self, principal: AuthenticatedPrincipal, thread_id: UUID, *, title: str | None
    ) -> ThreadView:
        normalized = title.strip() if title else None
        if normalized is not None and len(normalized) > 200:
            raise ValueError("thread title must be at most 200 characters")
        record = ThreadRepository(self.session, principal).update_fields(
            thread_id,
            title=normalized,
            updated_at=self.clock.now(),
        )
        return _thread_view(record)

    def set_archived(
        self, principal: AuthenticatedPrincipal, thread_id: UUID, *, archived: bool
    ) -> ThreadView:
        record = ThreadRepository(self.session, principal).update_fields(
            thread_id,
            archived=archived,
            updated_at=self.clock.now(),
        )
        return _thread_view(record)

    def delete(self, principal: AuthenticatedPrincipal, thread_id: UUID) -> None:
        repository = ThreadRepository(self.session, principal)
        repository.require(thread_id)
        self.session.execute(
            update(BookingIntentRecord)
            .where(
                BookingIntentRecord.user_id == principal.user_id,
                BookingIntentRecord.thread_id == thread_id,
            )
            .values(thread_id=None)
        )
        if not repository.delete(thread_id):
            raise ResourceNotFoundError("resource was not found")


class CheckpointService:
    def __init__(self, session: Session, *, clock: Clock | None = None) -> None:
        self.session = session
        self.clock = clock or SystemClock()

    def latest(
        self,
        principal: AuthenticatedPrincipal,
        thread_id: UUID,
        *,
        validate_offer_freshness: bool = True,
        now: datetime | None = None,
    ) -> CheckpointView | None:
        ThreadRepository(self.session, principal).require(thread_id)
        record = CheckpointRepository(self.session, principal).latest(thread_id)
        if record is None:
            return None
        state = migrate_checkpoint_state(record.state, record.state_schema_version)
        if validate_offer_freshness and state.selected_offer_id is not None:
            now = now or self.clock.now()
            fresh_offer = self.session.scalar(
                select(FlightOfferRecord.id).where(
                    FlightOfferRecord.id == state.selected_offer_id,
                    FlightOfferRecord.user_id == principal.user_id,
                    FlightOfferRecord.expires_at > now,
                )
            )
            if fresh_offer is None:
                context = dict(state.safe_context)
                context["reprice_required"] = True
                state = state.model_copy(
                    update={"selected_offer_id": None, "safe_context": context}
                )
        return _checkpoint_view(record, state)

    def save(
        self,
        principal: AuthenticatedPrincipal,
        thread_id: UUID,
        *,
        state: CheckpointState,
        expected_version: int,
        last_message_id: UUID | None = None,
    ) -> CheckpointView:
        if expected_version < 0:
            raise ValueError("expected checkpoint version cannot be negative")
        ThreadRepository(self.session, principal).lock(thread_id)
        repository = CheckpointRepository(self.session, principal)
        current = repository.latest(thread_id)
        actual_version = current.version if current else 0
        if actual_version != expected_version:
            raise ConcurrencyConflictError("checkpoint version changed")
        if last_message_id is not None:
            message = MessageRepository(self.session, principal).require(last_message_id)
            if message.thread_id != thread_id:
                raise ResourceNotFoundError("resource was not found")
        record = repository.add(
            AgentCheckpointRecord(
                user_id=principal.user_id,
                thread_id=thread_id,
                version=actual_version + 1,
                state_schema_version=CHECKPOINT_SCHEMA_VERSION,
                state=state.model_dump(mode="json"),
                last_message_id=last_message_id,
            )
        )
        return _checkpoint_view(record, state)


@dataclass(frozen=True)
class TurnAppendResult:
    created: bool
    user_message: MessageView
    assistant_message: MessageView | None
    checkpoint: CheckpointView | None


SummaryGenerator = Callable[[tuple[MessageView, ...], str | None], str]


def _default_summary(messages: tuple[MessageView, ...], previous: str | None) -> str:
    prefix = f"{previous}\n" if previous else ""
    rendered = "\n".join(f"{message.role.value}: {message.content}" for message in messages)
    return (prefix + rendered)[-12_000:]


class MessageService:
    def __init__(
        self,
        session: Session,
        *,
        summary_generator: SummaryGenerator = _default_summary,
        recent_window: int = 24,
        summary_trigger: int = 40,
        clock: Clock | None = None,
    ) -> None:
        if recent_window < 1 or summary_trigger <= recent_window:
            raise ValueError("summary trigger must exceed the recent message window")
        self.session = session
        self.summary_generator = summary_generator
        self.recent_window = recent_window
        self.summary_trigger = summary_trigger
        self.clock = clock or SystemClock()

    def append_user(
        self,
        principal: AuthenticatedPrincipal,
        thread_id: UUID,
        *,
        content: str,
        client_message_id: str,
        checkpoint_state: CheckpointState | None = None,
        expected_checkpoint_version: int | None = None,
    ) -> AppendResult:
        if not content.strip():
            raise ValueError("message content cannot be empty")
        if len(content) > 50_000:
            raise ValueError("message content must be at most 50000 characters")
        if not client_message_id or len(client_message_id) > 120:
            raise ValueError("client_message_id must be between 1 and 120 characters")
        threads = ThreadRepository(self.session, principal)
        thread = threads.lock(thread_id)
        messages = MessageRepository(self.session, principal)
        existing = messages.get_by_client_id(thread_id, client_message_id)
        if existing is not None:
            checkpoint = CheckpointService(self.session, clock=self.clock).latest(
                principal, thread_id
            )
            return AppendResult(
                created=False,
                message=_message_view(existing),
                checkpoint=checkpoint,
            )
        sanitized = sanitize_message_text(content)
        record = messages.add(
            ChatMessageRecord(
                user_id=principal.user_id,
                thread_id=thread_id,
                role=ChatRole.USER.value,
                content=sanitized.text,
                sequence=messages.next_sequence(thread_id),
                client_message_id=client_message_id,
            )
        )
        thread.updated_at = self.clock.now()
        checkpoint = None
        if checkpoint_state is not None:
            if expected_checkpoint_version is None:
                raise ValueError("expected checkpoint version is required with checkpoint state")
            checkpoint = CheckpointService(self.session, clock=self.clock).save(
                principal,
                thread_id,
                state=checkpoint_state,
                expected_version=expected_checkpoint_version,
                last_message_id=record.id,
            )
        return AppendResult(created=True, message=_message_view(record), checkpoint=checkpoint)

    def append_turn(
        self,
        principal: AuthenticatedPrincipal,
        thread_id: UUID,
        *,
        user_content: str,
        client_message_id: str,
        assistant_content: str,
        checkpoint_state: CheckpointState | None = None,
        expected_checkpoint_version: int | None = None,
    ) -> TurnAppendResult:
        user_result = self.append_user(
            principal,
            thread_id,
            content=user_content,
            client_message_id=client_message_id,
            checkpoint_state=checkpoint_state,
            expected_checkpoint_version=expected_checkpoint_version,
        )
        assistant_key = "assistant:" + hashlib.sha256(client_message_id.encode()).hexdigest()
        messages = MessageRepository(self.session, principal)
        existing_assistant = messages.get_by_client_id(thread_id, assistant_key)
        if existing_assistant is not None:
            return TurnAppendResult(
                user_result.created,
                user_result.message,
                _message_view(existing_assistant),
                user_result.checkpoint,
            )
        ThreadRepository(self.session, principal).lock(thread_id)
        sanitized = sanitize_message_text(assistant_content)
        assistant = messages.add(
            ChatMessageRecord(
                user_id=principal.user_id,
                thread_id=thread_id,
                role=ChatRole.ASSISTANT.value,
                content=sanitized.text,
                sequence=messages.next_sequence(thread_id),
                client_message_id=assistant_key,
            )
        )
        return TurnAppendResult(
            user_result.created,
            user_result.message,
            _message_view(assistant),
            user_result.checkpoint,
        )

    def list(
        self,
        principal: AuthenticatedPrincipal,
        thread_id: UUID,
        *,
        before_sequence: int | None = None,
        limit: int = 50,
    ) -> MessagePage:
        if limit < 1 or limit > 200:
            raise ValueError("limit must be between 1 and 200")
        ThreadRepository(self.session, principal).require(thread_id)
        rows = MessageRepository(self.session, principal).list_for_thread(
            thread_id,
            limit=limit + 1,
            before_sequence=before_sequence,
            ascending=False,
        )
        has_more = len(rows) > limit
        page_rows = list(reversed(rows[:limit]))
        cursor = str(page_rows[0].sequence) if has_more and page_rows else None
        return MessagePage(
            items=tuple(_message_view(row) for row in page_rows),
            next_cursor=cursor,
        )

    def build_context(
        self,
        principal: AuthenticatedPrincipal,
        thread_id: UUID,
        *,
        prompt_version: str = SUMMARY_PROMPT_VERSION,
        include_current_extra: bool = False,
    ) -> ConversationContext:
        thread_repository = ThreadRepository(self.session, principal)
        thread = thread_repository.lock(thread_id)
        message_repository = MessageRepository(self.session, principal)
        recent_rows = message_repository.list_for_thread(
            thread_id,
            # Preserve a full prior-message window when the newly appended current turn
            # must be excluded before planning.
            limit=self.recent_window + int(include_current_extra),
            ascending=False,
        )
        recent_rows = list(reversed(recent_rows))
        first_recent = recent_rows[0].sequence if recent_rows else 1
        summary_needed = (
            first_recent - 1 > thread.summarized_through_sequence
            and first_recent - 1 >= self.summary_trigger - self.recent_window
        )
        prompt_changed = (
            thread.summary is not None and thread.summary_prompt_version != prompt_version
        )
        if summary_needed or prompt_changed:
            through = max(0, first_recent - 1)
            older_rows = message_repository.list_for_thread(
                thread_id,
                limit=10_000,
                before_sequence=first_recent,
                after_sequence=None if prompt_changed else thread.summarized_through_sequence,
                ascending=True,
            )
            old_summary = None if prompt_changed else thread.summary
            thread.summary = self.summary_generator(
                tuple(_message_view(row) for row in older_rows),
                old_summary,
            )
            thread.summary_version += 1
            thread.summary_prompt_version = prompt_version
            thread.summarized_through_sequence = through
            thread.updated_at = self.clock.now()
        checkpoint = CheckpointService(self.session, clock=self.clock).latest(principal, thread_id)
        return ConversationContext(
            thread=_thread_view(thread),
            summary=thread.summary,
            messages=tuple(_message_view(row) for row in recent_rows),
            checkpoint=checkpoint,
        )
