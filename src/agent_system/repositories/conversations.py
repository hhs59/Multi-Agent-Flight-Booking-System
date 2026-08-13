from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy import desc, func, or_, select
from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import AgentCheckpointRecord, ChatMessageRecord, ChatThreadRecord
from agent_system.repositories.base import OwnedRepository, ResourceNotFoundError


class ThreadRepository(OwnedRepository[ChatThreadRecord]):
    model = ChatThreadRecord

    def list_page(
        self,
        *,
        archived: bool,
        limit: int,
        before: tuple[datetime, UUID] | None = None,
    ) -> Sequence[ChatThreadRecord]:
        statement = select(ChatThreadRecord).where(
            ChatThreadRecord.user_id == self.principal.user_id,
            ChatThreadRecord.archived.is_(archived),
        )
        if before is not None:
            updated_at, thread_id = before
            statement = statement.where(
                or_(
                    ChatThreadRecord.updated_at < updated_at,
                    (
                        (ChatThreadRecord.updated_at == updated_at)
                        & (ChatThreadRecord.id < thread_id)
                    ),
                )
            )
        statement = statement.order_by(
            desc(ChatThreadRecord.updated_at), desc(ChatThreadRecord.id)
        ).limit(limit)
        return self.session.scalars(statement).all()

    def lock(self, thread_id: UUID) -> ChatThreadRecord:
        statement = (
            select(ChatThreadRecord)
            .where(
                ChatThreadRecord.id == thread_id,
                ChatThreadRecord.user_id == self.principal.user_id,
            )
            .with_for_update()
        )
        record = self.session.scalar(statement)
        if record is None:
            raise ResourceNotFoundError("resource was not found")
        return record


class MessageRepository(OwnedRepository[ChatMessageRecord]):
    model = ChatMessageRecord

    def get_by_client_id(self, thread_id: UUID, client_message_id: str) -> ChatMessageRecord | None:
        return self.session.scalar(
            select(ChatMessageRecord).where(
                ChatMessageRecord.user_id == self.principal.user_id,
                ChatMessageRecord.thread_id == thread_id,
                ChatMessageRecord.client_message_id == client_message_id,
            )
        )

    def next_sequence(self, thread_id: UUID) -> int:
        maximum = self.session.scalar(
            select(func.max(ChatMessageRecord.sequence)).where(
                ChatMessageRecord.user_id == self.principal.user_id,
                ChatMessageRecord.thread_id == thread_id,
            )
        )
        return (maximum or 0) + 1

    def list_for_thread(
        self,
        thread_id: UUID,
        *,
        limit: int,
        before_sequence: int | None = None,
        after_sequence: int | None = None,
        ascending: bool = True,
    ) -> Sequence[ChatMessageRecord]:
        statement = select(ChatMessageRecord).where(
            ChatMessageRecord.user_id == self.principal.user_id,
            ChatMessageRecord.thread_id == thread_id,
        )
        if before_sequence is not None:
            statement = statement.where(ChatMessageRecord.sequence < before_sequence)
        if after_sequence is not None:
            statement = statement.where(ChatMessageRecord.sequence > after_sequence)
        order = ChatMessageRecord.sequence.asc() if ascending else ChatMessageRecord.sequence.desc()
        return self.session.scalars(statement.order_by(order).limit(limit)).all()


class CheckpointRepository(OwnedRepository[AgentCheckpointRecord]):
    model = AgentCheckpointRecord

    def latest(self, thread_id: UUID) -> AgentCheckpointRecord | None:
        return self.session.scalar(
            select(AgentCheckpointRecord)
            .where(
                AgentCheckpointRecord.user_id == self.principal.user_id,
                AgentCheckpointRecord.thread_id == thread_id,
            )
            .order_by(AgentCheckpointRecord.version.desc())
            .limit(1)
        )

    def list_versions(
        self, thread_id: UUID, *, limit: int = 100
    ) -> Sequence[AgentCheckpointRecord]:
        return self.session.scalars(
            select(AgentCheckpointRecord)
            .where(
                AgentCheckpointRecord.user_id == self.principal.user_id,
                AgentCheckpointRecord.thread_id == thread_id,
            )
            .order_by(AgentCheckpointRecord.version.desc())
            .limit(limit)
        ).all()


def repositories_for(
    session: Session, principal: AuthenticatedPrincipal
) -> tuple[ThreadRepository, MessageRepository, CheckpointRepository]:
    return (
        ThreadRepository(session, principal),
        MessageRepository(session, principal),
        CheckpointRepository(session, principal),
    )
