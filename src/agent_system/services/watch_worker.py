from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import select

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import (
    FlightOfferRecord,
    FlightSearchRecord,
    FlightWatchRecord,
    PurchaseMandateRecord,
    UserRecord,
    WatchHoldRecord,
    WatchMatchRecord,
    WatchNotificationRecord,
    WatchRunRecord,
)
from agent_system.domain.booking_workflow import BookingConfirmationRequest, BookingPrepareRequest
from agent_system.domain.bookings import BookingQuote, TravelerSnapshot
from agent_system.domain.flights import FlightOffer, FlightSearchCriteria, RepriceStatus
from agent_system.domain.provider_services import NotificationChannel, NotificationDestination
from agent_system.domain.watches import (
    HoldStatus,
    PurchaseMandateStatus,
    WatchActionMode,
    WatchMatchStatus,
    WatchRunStatus,
    WatchStatus,
)
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.contracts import FlightProvider, NotificationProvider
from agent_system.providers.errors import (
    CapabilityUnavailable,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
)
from agent_system.repositories.events import OutboxRepository
from agent_system.security.encryption import FieldEncryptor
from agent_system.services.booking_workflow import BookingWorkflowService
from agent_system.services.flight_search import FlightSearchService
from agent_system.services.travelers import TravelerProfileService
from agent_system.services.watch_matching import MatchDecision, WatchPolicyEvaluator
from agent_system.services.watches import WatchGateSettings, WatchWorkflowError


@dataclass(frozen=True)
class ClaimedWatch:
    watch_id: UUID
    run_id: UUID
    user_id: UUID
    run_key: str


class WatchWorker:
    """Database-backed worker intended to run as a separate process from FastAPI."""

    def __init__(
        self,
        session_factory,
        *,
        flight_provider: FlightProvider,
        flight_search: FlightSearchService,
        notification_provider: NotificationProvider,
        encryptor: FieldEncryptor,
        booking_workflow: BookingWorkflowService | None = None,
        evaluator: WatchPolicyEvaluator | None = None,
        gates: WatchGateSettings | None = None,
        clock: Clock | None = None,
        lease_seconds: int = 120,
        interval_seconds: int = 900,
        worker_id: str | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.flight_provider = flight_provider
        self.flight_search = flight_search
        self.notification_provider = notification_provider
        self.encryptor = encryptor
        self.booking_workflow = booking_workflow
        self.evaluator = evaluator or WatchPolicyEvaluator()
        self.gates = gates or WatchGateSettings.from_environment()
        self.clock = clock or SystemClock()
        self.lease_seconds = lease_seconds
        self.interval_seconds = interval_seconds
        self.worker_id = worker_id or f"watch-worker-{uuid4()}"

    def _now(self) -> datetime:
        return self.clock.now().astimezone(UTC)

    def recover_abandoned_leases(self) -> int:
        now = self._now()
        session = self.session_factory()
        try:
            with session.begin():
                runs = session.scalars(
                    select(WatchRunRecord)
                    .where(
                        WatchRunRecord.status == WatchRunStatus.RUNNING.value,
                        WatchRunRecord.lease_expires_at.is_not(None),
                        WatchRunRecord.lease_expires_at <= now,
                    )
                    .with_for_update()
                ).all()
                count = 0
                for run in runs:
                    run.status = WatchRunStatus.ABANDONED.value
                    run.completed_at = now
                    run.outcome = {"status": "abandoned", "reason": "worker_lease_expired"}
                    watch = session.scalar(
                        select(FlightWatchRecord)
                        .where(
                            FlightWatchRecord.id == run.watch_id,
                            FlightWatchRecord.user_id == run.user_id,
                        )
                        .with_for_update()
                    )
                    if watch is not None and watch.status in {
                        WatchStatus.ACTIVE.value,
                        WatchStatus.NEEDS_USER_ACTION.value,
                    }:
                        watch.lease_owner = None
                        watch.lease_expires_at = None
                        watch.next_run_at = now
                    count += 1
                return count
        finally:
            session.close()

    def claim_due_watch(self) -> ClaimedWatch | None:
        now = self._now()
        session = self.session_factory()
        try:
            with session.begin():
                watch = session.scalar(
                    select(FlightWatchRecord)
                    .where(
                        FlightWatchRecord.status == WatchStatus.ACTIVE.value,
                        FlightWatchRecord.next_run_at.is_not(None),
                        FlightWatchRecord.next_run_at <= now,
                        (
                            FlightWatchRecord.lease_expires_at.is_(None)
                            | (FlightWatchRecord.lease_expires_at <= now)
                        ),
                    )
                    .order_by(FlightWatchRecord.next_run_at, FlightWatchRecord.created_at)
                    .with_for_update(skip_locked=True)
                )
                if watch is None:
                    return None
                run_key = f"watch-run:{watch.id}:{watch.run_count + 1}"
                watch.lease_owner = self.worker_id
                watch.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
                watch.run_count += 1
                watch.version += 1
                run = WatchRunRecord(
                    user_id=watch.user_id,
                    watch_id=watch.id,
                    status=WatchRunStatus.RUNNING.value,
                    scheduled_for=now,
                    started_at=now,
                    lease_owner=self.worker_id,
                    lease_expires_at=watch.lease_expires_at,
                    idempotency_key=run_key,
                    outcome={},
                )
                session.add(run)
                session.flush()
                return ClaimedWatch(watch.id, run.id, watch.user_id, run_key)
        finally:
            session.close()

    async def run_once(self) -> dict[str, Any] | None:
        self.recover_abandoned_leases()
        claimed = self.claim_due_watch()
        if claimed is None:
            return None
        try:
            result = await self._execute(claimed)
        except ProviderRateLimitError as exc:
            result = await self._finish_failure(
                claimed, "provider_rate_limit", exc.safe_message, exc.retry_after_seconds
            )
        except ProviderTimeoutError as exc:
            result = await self._finish_failure(claimed, "provider_timeout", exc.safe_message, None)
        except ProviderError as exc:
            result = await self._finish_failure(claimed, "provider_error", exc.safe_message, None)
        except (WatchWorkflowError, ValueError) as exc:
            result = await self._finish_failure(claimed, "watch_error", str(exc), None)
        return result

    async def _execute(self, claimed: ClaimedWatch) -> dict[str, Any]:
        session = self.session_factory()
        try:
            with session.begin():
                watch = session.scalar(
                    select(FlightWatchRecord)
                    .where(
                        FlightWatchRecord.id == claimed.watch_id,
                        FlightWatchRecord.user_id == claimed.user_id,
                    )
                    .with_for_update()
                )
                if watch is None:
                    raise WatchWorkflowError("not_found", "watch was not found")
                criteria = self._criteria(watch.criteria)
                if (
                    criteria.purchase_deadline is not None
                    and self._now() >= criteria.purchase_deadline
                ):
                    watch.status = WatchStatus.EXPIRED.value
                    return await self._finish_success_in_transaction(
                        session, claimed, {"status": "expired"}, next_run=None
                    )
                user = session.get(UserRecord, claimed.user_id)
                user_email = user.email if user else None
        finally:
            session.close()

        offers = []
        current = criteria.departure_date_from
        while current <= criteria.departure_date_to and len(offers) < 100:
            search_criteria = FlightSearchCriteria(
                origin=criteria.origin,
                destination=criteria.destination,
                departure_date=current,
                passengers=criteria.passengers,
                cabin=criteria.cabin,
                max_stops=criteria.max_stops,
                preferred_carriers=criteria.preferred_carriers,
            )
            page = await self.flight_search.search(search_criteria, correlation_id=claimed.run_key)
            offers.extend(page.offers)
            current += timedelta(days=1)

        matched = 0
        rejected = 0
        notification_failures: list[str] = []
        for offer in offers:
            decision = self.evaluator.evaluate(criteria, offer, now=self._now())
            match_id = await self._persist_candidate(claimed, criteria, offer, decision)
            if not decision.matched:
                rejected += 1
                continue
            repriced = await self.flight_search.reprice(
                offer.metadata.provider_offer_id or str(offer.id),
                offer,
                correlation_id=claimed.run_key,
            )
            if (
                repriced.status not in {RepriceStatus.UNCHANGED, RepriceStatus.CHANGED}
                or repriced.repriced_offer is None
            ):
                await self._mark_match_rejected(match_id, claimed.user_id, "reprice_unavailable")
                rejected += 1
                continue
            final_offer = repriced.repriced_offer
            final_decision = self.evaluator.evaluate(criteria, final_offer, now=self._now())
            if not final_decision.matched:
                await self._mark_match_rejected(
                    match_id,
                    claimed.user_id,
                    final_decision.rejection_reason or "reprice_no_longer_matches",
                )
                rejected += 1
                continue
            await self._update_match_offer(match_id, claimed.user_id, final_offer, final_decision)
            matched += 1
            if criteria.action_mode is WatchActionMode.NOTIFY:
                notification_failures.extend(
                    await self._notify_match(claimed, match_id, final_offer, criteria, user_email)
                )
            elif criteria.action_mode is WatchActionMode.CONFIRM:
                notification_failures.extend(
                    await self._notify_match(claimed, match_id, final_offer, criteria, user_email)
                )
                await self._prepare_confirmation(claimed, match_id, final_offer, criteria)
            else:
                await self._execute_auto_buy(claimed, match_id, final_offer, criteria)
            if criteria.notification_behavior.value == "first_match":
                break
        result = {
            "status": "completed",
            "offers_seen": len(offers),
            "matched": matched,
            "rejected": rejected,
            "notification_failures": notification_failures,
        }
        return await self._finish_success(
            claimed,
            result,
            next_run=None
            if matched and criteria.notification_behavior.value == "first_match"
            else self.interval_seconds,
        )

    @staticmethod
    def _criteria(payload: dict[str, Any]):
        from agent_system.domain.watches import FlightWatchCriteria

        return FlightWatchCriteria.model_validate(payload)

    async def _persist_candidate(
        self, claimed: ClaimedWatch, criteria, offer: FlightOffer, decision: MatchDecision
    ) -> UUID:
        session = self.session_factory()
        try:
            with session.begin():
                search = FlightSearchRecord(
                    user_id=claimed.user_id,
                    provider=offer.metadata.provider,
                    environment=offer.metadata.environment.value,
                    criteria={
                        "origin": criteria.origin,
                        "destination": criteria.destination,
                        "departure_date": offer.segments[0].departure_at.date().isoformat(),
                    },
                    expires_at=offer.metadata.expires_at,
                )
                session.add(search)
                session.flush()
                provider_offer_id = offer.metadata.provider_offer_id or str(offer.id)
                source_offer = session.scalar(
                    select(FlightOfferRecord)
                    .where(
                        FlightOfferRecord.user_id == claimed.user_id,
                        FlightOfferRecord.search_id == search.id,
                        FlightOfferRecord.provider == offer.metadata.provider,
                        FlightOfferRecord.environment == offer.metadata.environment.value,
                        FlightOfferRecord.provider_offer_id == provider_offer_id,
                    )
                    .with_for_update()
                )
                if source_offer is None:
                    source_offer = FlightOfferRecord(
                        user_id=claimed.user_id,
                        search_id=search.id,
                        provider=offer.metadata.provider,
                        environment=offer.metadata.environment.value,
                        provider_offer_id=provider_offer_id,
                        offer_snapshot=offer.model_dump(mode="json"),
                        retrieved_at=offer.metadata.retrieved_at,
                        expires_at=offer.metadata.expires_at,
                    )
                    session.add(source_offer)
                    session.flush()
                else:
                    source_offer.offer_snapshot = offer.model_dump(mode="json")
                    source_offer.expires_at = offer.metadata.expires_at
                    source_offer.retrieved_at = offer.metadata.retrieved_at
                    source_offer.version += 1
                key = hashlib.sha256(
                    f"{offer.metadata.provider}:{offer.metadata.provider_offer_id}:{offer.total.amount}:{offer.total.currency}".encode()
                ).hexdigest()
                existing = session.scalar(
                    select(WatchMatchRecord)
                    .where(
                        WatchMatchRecord.watch_id == claimed.watch_id,
                        WatchMatchRecord.user_id == claimed.user_id,
                        WatchMatchRecord.deduplication_key == key,
                    )
                    .with_for_update()
                )
                if existing is not None:
                    if existing.source_offer_id is None:
                        existing.source_offer_id = source_offer.id
                    return existing.id
                match = WatchMatchRecord(
                    user_id=claimed.user_id,
                    watch_id=claimed.watch_id,
                    source_offer_id=source_offer.id,
                    deduplication_key=key,
                    offer_snapshot=offer.model_dump(mode="json"),
                    provider=offer.metadata.provider,
                    environment=offer.metadata.environment.value,
                    expires_at=offer.metadata.expires_at,
                    status=(
                        WatchMatchStatus.MATCHED.value
                        if decision.matched
                        else WatchMatchStatus.REJECTED.value
                    ),
                    match_reasons=list(decision.reasons),
                    rejection_reason=decision.rejection_reason,
                )
                session.add(match)
                session.flush()
                return match.id
        finally:
            session.close()

    async def _mark_match_rejected(self, match_id: UUID, user_id: UUID, reason: str) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                match = session.scalar(
                    select(WatchMatchRecord)
                    .where(WatchMatchRecord.id == match_id, WatchMatchRecord.user_id == user_id)
                    .with_for_update()
                )
                if match:
                    match.status = WatchMatchStatus.REJECTED.value
                    match.rejection_reason = reason
        finally:
            session.close()

    async def _update_match_offer(
        self, match_id: UUID, user_id: UUID, offer: FlightOffer, decision: MatchDecision
    ) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                match = session.scalar(
                    select(WatchMatchRecord)
                    .where(WatchMatchRecord.id == match_id, WatchMatchRecord.user_id == user_id)
                    .with_for_update()
                )
                if match:
                    match.offer_snapshot = offer.model_dump(mode="json")
                    match.expires_at = offer.metadata.expires_at
                    match.match_reasons = list(decision.reasons)
                    if match.source_offer_id is not None:
                        source_offer = session.scalar(
                            select(FlightOfferRecord)
                            .where(
                                FlightOfferRecord.id == match.source_offer_id,
                                FlightOfferRecord.user_id == user_id,
                            )
                            .with_for_update()
                        )
                        if source_offer is not None:
                            metadata = offer.metadata
                            source_offer.offer_snapshot = offer.model_dump(mode="json")
                            source_offer.provider = metadata.provider
                            source_offer.environment = metadata.environment.value
                            if metadata.provider_offer_id is not None:
                                source_offer.provider_offer_id = metadata.provider_offer_id
                            source_offer.retrieved_at = metadata.retrieved_at
                            source_offer.expires_at = metadata.expires_at
                            source_offer.version += 1
                    if match.status not in {
                        WatchMatchStatus.NOTIFIED.value,
                        WatchMatchStatus.ACTION_REQUIRED.value,
                        WatchMatchStatus.BOOKED.value,
                    }:
                        match.status = WatchMatchStatus.MATCHED.value
        finally:
            session.close()

    async def _notify_match(
        self,
        claimed: ClaimedWatch,
        match_id: UUID,
        offer: FlightOffer,
        criteria,
        user_email: str | None,
    ) -> list[str]:
        failures: list[str] = []
        destination_hash = (
            hashlib.sha256(user_email.encode()).hexdigest()
            if user_email is not None
            else hashlib.sha256(claimed.user_id.bytes).hexdigest()
        )
        for channel in criteria.notification_channels:
            key = f"watch-notification:{match_id}:{channel.value}"
            if channel is NotificationChannel.IN_APP:
                await self._record_in_app_notification(claimed, match_id, key)
                continue
            if channel is not NotificationChannel.EMAIL:
                code = f"{channel.value}:provider_not_configured"
                await self._record_notification_failure(
                    claimed,
                    match_id,
                    channel.value,
                    "provider_not_configured",
                    destination_hash=destination_hash,
                )
                failures.append(code)
                continue
            if user_email is None:
                code = f"{channel.value}:destination_missing"
                await self._record_notification_failure(
                    claimed,
                    match_id,
                    channel.value,
                    "destination_missing",
                    destination_hash=destination_hash,
                )
                failures.append(code)
                continue
            session = self.session_factory()
            try:
                with session.begin():
                    existing = session.scalar(
                        select(WatchNotificationRecord)
                        .where(
                            WatchNotificationRecord.match_id == match_id,
                            WatchNotificationRecord.channel == channel.value,
                        )
                        .with_for_update()
                    )
                    if existing is None:
                        record = WatchNotificationRecord(
                            user_id=claimed.user_id,
                            watch_id=claimed.watch_id,
                            match_id=match_id,
                            channel=channel.value,
                            destination_hash=destination_hash,
                            idempotency_key=key,
                            status="pending",
                        )
                        session.add(record)
                        session.flush()
                    elif existing.status == "sent":
                        continue
            finally:
                session.close()
            try:
                result = await self.notification_provider.send(
                    "flight_watch_match",
                    NotificationDestination(channel=channel, address=SecretStr(user_email)),
                    key,
                    variables={
                        "match_id": str(match_id),
                        "price": str(offer.total.amount),
                        "currency": offer.total.currency,
                        "expires_at": offer.metadata.expires_at.isoformat(),
                        "origin": offer.segments[0].origin,
                        "destination": offer.segments[-1].destination,
                    },
                )
            except ProviderError as exc:
                error_code = self._notification_error_code(exc)
                await self._record_notification_failure(
                    claimed,
                    match_id,
                    channel.value,
                    error_code,
                    destination_hash=destination_hash,
                )
                failures.append(f"{channel.value}:{error_code}")
                continue
            session = self.session_factory()
            try:
                with session.begin():
                    record = session.scalar(
                        select(WatchNotificationRecord)
                        .where(
                            WatchNotificationRecord.match_id == match_id,
                            WatchNotificationRecord.channel == channel.value,
                            WatchNotificationRecord.user_id == claimed.user_id,
                        )
                        .with_for_update()
                    )
                    if record:
                        record.status = "sent" if result.accepted else "failed"
                        record.error_code = None if result.accepted else "provider_rejected"
                        record.provider_message_reference = (
                            result.provider_message_reference.get_secret_value()
                            if result.provider_message_reference
                            else None
                        )
                        record.sent_at = self._now() if result.accepted else None
                    if result.accepted:
                        OutboxRepository(
                            session,
                            AuthenticatedPrincipal(
                                user_id=claimed.user_id, issuer="worker", subject="worker"
                            ),
                        ).enqueue(
                            topic="watch.match",
                            aggregate_type="watch_match",
                            aggregate_id=match_id,
                            payload={
                                "watch_id": str(claimed.watch_id),
                                "match_id": str(match_id),
                                "status": "notified",
                            },
                            idempotency_key=f"watch-match:{match_id}",
                            available_at=self._now(),
                        )
                    else:
                        failures.append(f"{channel.value}:provider_rejected")
            finally:
                session.close()
        return failures

    @staticmethod
    def _notification_error_code(exc: ProviderError) -> str:
        if isinstance(exc, CapabilityUnavailable):
            return "provider_not_configured"
        if isinstance(exc, ProviderTimeoutError):
            return "provider_timeout"
        return "provider_rejected"

    async def _record_in_app_notification(
        self, claimed: ClaimedWatch, match_id: UUID, idempotency_key: str
    ) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                record = session.scalar(
                    select(WatchNotificationRecord)
                    .where(
                        WatchNotificationRecord.match_id == match_id,
                        WatchNotificationRecord.channel == NotificationChannel.IN_APP.value,
                        WatchNotificationRecord.user_id == claimed.user_id,
                    )
                    .with_for_update()
                )
                if record is None:
                    record = WatchNotificationRecord(
                        user_id=claimed.user_id,
                        watch_id=claimed.watch_id,
                        match_id=match_id,
                        channel=NotificationChannel.IN_APP.value,
                        destination_hash=None,
                        idempotency_key=idempotency_key,
                        status="sent",
                        sent_at=self._now(),
                    )
                    session.add(record)
                elif record.status != "sent":
                    record.status = "sent"
                    record.destination_hash = None
                    record.provider_message_reference = None
                    record.error_code = None
                    record.sent_at = self._now()
        finally:
            session.close()

    async def _record_notification_failure(
        self,
        claimed: ClaimedWatch,
        match_id: UUID,
        channel: str,
        code: str,
        *,
        destination_hash: str | None = None,
    ) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                existing = session.scalar(
                    select(WatchNotificationRecord)
                    .where(
                        WatchNotificationRecord.match_id == match_id,
                        WatchNotificationRecord.channel == channel,
                    )
                    .with_for_update()
                )
                if existing is None:
                    session.add(
                        WatchNotificationRecord(
                            user_id=claimed.user_id,
                            watch_id=claimed.watch_id,
                            match_id=match_id,
                            channel=channel,
                            destination_hash=destination_hash,
                            idempotency_key=f"watch-notification:{match_id}:{channel}",
                            status="failed",
                            error_code=code,
                        )
                    )
                else:
                    existing.status = "failed"
                    existing.error_code = code
                    existing.destination_hash = destination_hash
                    existing.provider_message_reference = None
                    existing.sent_at = None
        finally:
            session.close()

    async def _prepare_confirmation(
        self, claimed: ClaimedWatch, match_id: UUID, _offer: FlightOffer, criteria
    ) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                existing = session.scalar(
                    select(WatchMatchRecord)
                    .where(
                        WatchMatchRecord.id == match_id, WatchMatchRecord.user_id == claimed.user_id
                    )
                    .with_for_update()
                )
                if existing is None:
                    return
                offer_record = session.scalar(
                    select(FlightOfferRecord).where(
                        FlightOfferRecord.id == existing.source_offer_id,
                        FlightOfferRecord.user_id == claimed.user_id,
                    )
                )
                if offer_record is None:
                    return
                intent_key = f"watch-confirm:{claimed.watch_id}:{match_id}"
                from agent_system.db.models import BookingIntentRecord

                intent = session.scalar(
                    select(BookingIntentRecord).where(
                        BookingIntentRecord.user_id == claimed.user_id,
                        BookingIntentRecord.idempotency_key == intent_key,
                    )
                )
                if intent is None:
                    intent = BookingIntentRecord(
                        user_id=claimed.user_id,
                        source_offer_id=offer_record.id,
                        status="draft",
                        traveler_profile_ids=[str(item) for item in criteria.traveler_profile_ids],
                        idempotency_key=intent_key,
                    )
                    session.add(intent)
                existing.status = WatchMatchStatus.ACTION_REQUIRED.value
                watch = session.get(FlightWatchRecord, claimed.watch_id)
                if watch:
                    watch.status = WatchStatus.AWAITING_CONFIRMATION.value
                session.flush()
        finally:
            session.close()

    async def _execute_auto_buy(
        self, claimed: ClaimedWatch, match_id: UUID, offer: FlightOffer, criteria
    ) -> None:
        if self.booking_workflow is None or not self.gates.allows_auto_buy(
            execution_mode=self.flight_provider.environment
        ):
            await self._mark_watch_action_required(claimed, match_id, "auto_buy_gate_closed")
            return
        # The configured caps are a final server-side guard, even when the watch
        # criteria and its mandate both contain a larger or stale maximum.
        spend_limit = min(
            self.gates.global_spend_limit,
            self.gates.per_user_spend_limit,
            self.gates.per_watch_spend_limit,
        )
        if offer.total.amount > spend_limit:
            await self._mark_watch_action_required(claimed, match_id, "spend_limit_exceeded")
            return
        session = self.session_factory()
        try:
            with session.begin():
                mandate = session.scalar(
                    select(PurchaseMandateRecord)
                    .where(
                        PurchaseMandateRecord.watch_id == claimed.watch_id,
                        PurchaseMandateRecord.user_id == claimed.user_id,
                        PurchaseMandateRecord.status == PurchaseMandateStatus.ACTIVE.value,
                    )
                    .with_for_update()
                )
                if mandate is None:
                    raise WatchWorkflowError(
                        "mandate_missing", "active purchase mandate is missing"
                    )
                if self._now() >= mandate.purchase_deadline:
                    raise WatchWorkflowError(
                        "mandate_expired", "purchase mandate deadline has passed"
                    )
                payment_ref = self.encryptor.decrypt_text(
                    mandate.payment_method_reference_encrypted,
                    key_version=mandate.payment_reference_key_version,
                    associated_data=f"watch-mandate:{claimed.user_id}:{claimed.watch_id}:v{mandate.version}".encode(),
                )
                match = session.scalar(
                    select(WatchMatchRecord)
                    .where(
                        WatchMatchRecord.id == match_id,
                        WatchMatchRecord.user_id == claimed.user_id,
                    )
                    .with_for_update()
                )
                if match is None or match.source_offer_id is None:
                    raise WatchWorkflowError(
                        "offer_missing", "matched offer is unavailable for booking"
                    )
                offer_record = session.scalar(
                    select(FlightOfferRecord).where(
                        FlightOfferRecord.id == match.source_offer_id,
                        FlightOfferRecord.user_id == claimed.user_id,
                    )
                )
                if offer_record is None:
                    raise WatchWorkflowError(
                        "offer_missing", "matched offer is unavailable for booking"
                    )
                intent_key = f"watch-auto-buy:{claimed.watch_id}:{match_id}"
                from agent_system.db.models import BookingIntentRecord

                intent = session.scalar(
                    select(BookingIntentRecord).where(
                        BookingIntentRecord.user_id == claimed.user_id,
                        BookingIntentRecord.idempotency_key == intent_key,
                    )
                )
                if intent is None:
                    intent = BookingIntentRecord(
                        user_id=claimed.user_id,
                        source_offer_id=offer_record.id,
                        status="draft",
                        traveler_profile_ids=[str(item) for item in criteria.traveler_profile_ids],
                        idempotency_key=intent_key,
                    )
                    session.add(intent)
                    session.flush()
                intent_id = intent.id
                profile_ids = tuple(UUID(item) for item in criteria.traveler_profile_ids)
        finally:
            session.close()
        principal = AuthenticatedPrincipal(user_id=claimed.user_id)
        prepared = await self.booking_workflow.prepare(
            principal,
            intent_id,
            BookingPrepareRequest(traveler_profile_ids=profile_ids, international=True),
            correlation_id=claimed.run_key,
        )
        if prepared.status != "awaiting_confirmation":
            await self._mark_watch_action_required(claimed, match_id, "auto_buy_prepare_failed")
            return
        confirmed = await self.booking_workflow.confirm(
            principal,
            intent_id,
            BookingConfirmationRequest(
                quote_version=prepared.quote_version,
                acknowledged_fare_terms=True,
                payment_method_reference=SecretStr(payment_ref),
            ),
            idempotency_key=f"{claimed.run_key}:confirm",
        )
        if confirmed.status not in {"ticketing_pending", "booked"}:
            await self._mark_watch_action_required(claimed, match_id, "auto_buy_needs_user_action")
        else:
            await self._mark_match_status(
                claimed, match_id, WatchMatchStatus.BOOKED.value, WatchStatus.BOOKED.value
            )

    async def _mark_watch_action_required(
        self, claimed: ClaimedWatch, match_id: UUID, reason: str
    ) -> None:
        await self._mark_match_status(
            claimed,
            match_id,
            WatchMatchStatus.ACTION_REQUIRED.value,
            WatchStatus.NEEDS_USER_ACTION.value,
            reason,
        )

    async def _mark_match_status(
        self,
        claimed: ClaimedWatch,
        match_id: UUID,
        match_status: str,
        watch_status: str | None = None,
        reason: str | None = None,
    ) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                match = session.scalar(
                    select(WatchMatchRecord)
                    .where(
                        WatchMatchRecord.id == match_id, WatchMatchRecord.user_id == claimed.user_id
                    )
                    .with_for_update()
                )
                if match:
                    match.status = match_status
                    if reason:
                        match.rejection_reason = reason
                if watch_status:
                    watch = session.scalar(
                        select(FlightWatchRecord)
                        .where(
                            FlightWatchRecord.id == claimed.watch_id,
                            FlightWatchRecord.user_id == claimed.user_id,
                        )
                        .with_for_update()
                    )
                    if watch:
                        watch.status = watch_status
        finally:
            session.close()

    async def _finish_success(
        self, claimed: ClaimedWatch, outcome: dict[str, Any], next_run: int | None
    ) -> dict[str, Any]:
        session = self.session_factory()
        try:
            with session.begin():
                return await self._finish_success_in_transaction(
                    session, claimed, outcome, next_run=next_run
                )
        finally:
            session.close()

    async def _finish_success_in_transaction(
        self, session, claimed: ClaimedWatch, outcome: dict[str, Any], next_run: int | None
    ) -> dict[str, Any]:
        now = self._now()
        run = session.scalar(
            select(WatchRunRecord)
            .where(WatchRunRecord.id == claimed.run_id, WatchRunRecord.user_id == claimed.user_id)
            .with_for_update()
        )
        watch = session.scalar(
            select(FlightWatchRecord)
            .where(
                FlightWatchRecord.id == claimed.watch_id,
                FlightWatchRecord.user_id == claimed.user_id,
            )
            .with_for_update()
        )
        if run:
            run.status = WatchRunStatus.SUCCEEDED.value
            run.completed_at = now
            run.lease_expires_at = None
            run.outcome = outcome
            run.backoff_seconds = self.interval_seconds if next_run else 0
        if watch:
            watch.lease_owner = None
            watch.lease_expires_at = None
            watch.consecutive_failures = 0
            watch.last_error_code = None
            watch.next_run_at = now + timedelta(seconds=next_run) if next_run else None
            if next_run is None and watch.status == WatchStatus.ACTIVE.value:
                watch.status = WatchStatus.MATCHED.value if outcome.get("matched") else watch.status
        return outcome

    async def _finish_failure(
        self, claimed: ClaimedWatch, code: str, detail: str, retry_after: int | None
    ) -> dict[str, Any]:
        session = self.session_factory()
        try:
            with session.begin():
                now = self._now()
                run = session.scalar(
                    select(WatchRunRecord)
                    .where(
                        WatchRunRecord.id == claimed.run_id,
                        WatchRunRecord.user_id == claimed.user_id,
                    )
                    .with_for_update()
                )
                watch = session.scalar(
                    select(FlightWatchRecord)
                    .where(
                        FlightWatchRecord.id == claimed.watch_id,
                        FlightWatchRecord.user_id == claimed.user_id,
                    )
                    .with_for_update()
                )
                failures = (watch.consecutive_failures + 1) if watch else 1
                backoff = (
                    int(retry_after)
                    if retry_after is not None
                    else min(self.interval_seconds * (2 ** min(failures - 1, 4)), 86_400)
                )
                if run:
                    run.status = WatchRunStatus.FAILED.value
                    run.completed_at = now
                    run.lease_expires_at = None
                    run.outcome = {"status": "failed", "code": code, "detail": detail[:240]}
                    run.backoff_seconds = backoff
                if watch:
                    watch.lease_owner = None
                    watch.lease_expires_at = None
                    watch.consecutive_failures = failures
                    watch.last_error_code = code
                    watch.next_run_at = now + timedelta(seconds=backoff)
                    if failures >= 3:
                        watch.status = WatchStatus.NEEDS_USER_ACTION.value
                return {"status": "failed", "code": code, "backoff_seconds": backoff}
        finally:
            session.close()

    async def _update_hold(
        self, hold_id: UUID, user_id: UUID, status: str, released_at: datetime | None = None
    ) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                hold = session.scalar(
                    select(WatchHoldRecord)
                    .where(WatchHoldRecord.id == hold_id, WatchHoldRecord.user_id == user_id)
                    .with_for_update()
                )
                if hold:
                    hold.status = status
                    hold.released_at = released_at
        finally:
            session.close()

    async def create_hold(
        self, principal: AuthenticatedPrincipal, match_id: UUID
    ) -> dict[str, Any]:
        session = self.session_factory()
        try:
            with session.begin():
                match = session.scalar(
                    select(WatchMatchRecord)
                    .where(
                        WatchMatchRecord.id == match_id,
                        WatchMatchRecord.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                if match is None:
                    raise WatchWorkflowError("not_found", "watch match was not found")
                if not self.flight_provider.capabilities().can_hold:
                    raise WatchWorkflowError(
                        "hold_not_supported", "the selected provider does not support holds"
                    )
                offer = FlightOffer.model_validate(match.offer_snapshot)
                if offer.is_expired(self._now()):
                    raise WatchWorkflowError("offer_expired", "the matched offer has expired")
                watch = session.scalar(
                    select(FlightWatchRecord)
                    .where(
                        FlightWatchRecord.id == match.watch_id,
                        FlightWatchRecord.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                criteria = self._criteria(watch.criteria)
                snapshots = TravelerProfileService(session, self.encryptor).select_for_booking(
                    principal, criteria.traveler_profile_ids, international=True
                )
                travelers = tuple(
                    TravelerSnapshot(
                        traveler_profile_id=item.traveler_profile_id,
                        legal_name=item.legal_name or "",
                        birth_date=item.birth_date,
                        gender_marker=item.gender_marker,
                        email=item.email,
                        phone=item.phone,
                        nationality=item.nationality,
                        passport_number=item.passport_number,
                        passport_issuing_country=item.passport_issuing_country,
                        passport_expiry_date=item.passport_expiry_date,
                    )
                    for item in snapshots
                )
                quote = BookingQuote(
                    user_id=principal.user_id,
                    booking_intent_id=match.watch_id,
                    source_offer_id=match.watch_id,
                    offer=offer,
                    travelers=travelers,
                    created_at=self._now(),
                    expires_at=offer.metadata.expires_at,
                )
                hold_key = f"watch-hold:{match.watch_id}:{match.id}"
        finally:
            session.close()
        hold = await self.flight_provider.create_hold(quote, travelers, hold_key)
        expiry = min(hold.expires_at, offer.metadata.expires_at)
        session = self.session_factory()
        try:
            with session.begin():
                encrypted = self.encryptor.encrypt_text(
                    hold.provider_hold_id.get_secret_value(),
                    associated_data=f"watch-hold:{principal.user_id}:{match_id}".encode(),
                )
                record = WatchHoldRecord(
                    user_id=principal.user_id,
                    watch_id=match.watch_id,
                    match_id=match_id,
                    provider=hold.metadata.provider,
                    environment=hold.metadata.environment.value,
                    provider_hold_id_encrypted=encrypted.ciphertext,
                    provider_reference_key_version=encrypted.key_version,
                    expires_at=expiry,
                    status=HoldStatus.ACTIVE.value,
                    idempotency_key=hold_key,
                )
                session.add(record)
                session.flush()
                return {
                    "hold_id": str(record.id),
                    "status": "active",
                    "expires_at": expiry.isoformat(),
                    "is_ticket": False,
                    "message": "This is a provider hold, not a ticket.",
                }
        finally:
            session.close()

    async def release_hold(
        self, principal: AuthenticatedPrincipal, hold_id: UUID
    ) -> dict[str, Any]:
        session = self.session_factory()
        try:
            with session.begin():
                hold = session.scalar(
                    select(WatchHoldRecord)
                    .where(
                        WatchHoldRecord.id == hold_id, WatchHoldRecord.user_id == principal.user_id
                    )
                    .with_for_update()
                )
                if hold is None:
                    raise WatchWorkflowError("not_found", "hold was not found")
                value = self.encryptor.decrypt_text(
                    hold.provider_hold_id_encrypted,
                    key_version=hold.provider_reference_key_version,
                    associated_data=f"watch-hold:{principal.user_id}:{hold.match_id}".encode(),
                )
                key = hold.idempotency_key + ":release"
        finally:
            session.close()
        await self.flight_provider.release_hold(value, key)
        await self._update_hold(hold_id, principal.user_id, HoldStatus.RELEASED.value, self._now())
        return {"hold_id": str(hold_id), "status": "released"}

    async def expire_holds(self) -> int:
        now = self._now()
        session = self.session_factory()
        expired: list[tuple[UUID, UUID, str, str]] = []
        try:
            with session.begin():
                rows = session.scalars(
                    select(WatchHoldRecord)
                    .where(
                        WatchHoldRecord.status == HoldStatus.ACTIVE.value,
                        WatchHoldRecord.expires_at <= now,
                    )
                    .with_for_update()
                ).all()
                for row in rows:
                    provider_hold_id = self.encryptor.decrypt_text(
                        row.provider_hold_id_encrypted,
                        key_version=row.provider_reference_key_version,
                        associated_data=f"watch-hold:{row.user_id}:{row.match_id}".encode(),
                    )
                    row.status = HoldStatus.EXPIRED.value
                    expired.append(
                        (
                            row.id,
                            row.user_id,
                            provider_hold_id,
                            row.idempotency_key + ":expiry-release",
                        )
                    )
        finally:
            session.close()
        for hold_id, user_id, provider_hold_id, key in expired:
            try:
                await self.flight_provider.release_hold(provider_hold_id, key)
                await self._update_hold(hold_id, user_id, HoldStatus.RELEASED.value, now)
            except ProviderError:
                await self._update_hold(hold_id, user_id, HoldStatus.NEEDS_USER_ACTION.value)
        return len(expired)
