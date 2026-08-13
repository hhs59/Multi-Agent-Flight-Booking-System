from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import desc, func, select

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import (
    FlightWatchRecord,
    PurchaseMandateRecord,
    WatchMatchRecord,
    WatchNotificationRecord,
    WatchRunRecord,
)
from agent_system.domain.flights import FlightOffer
from agent_system.domain.provider_services import NotificationChannel
from agent_system.domain.values import ExecutionMode
from agent_system.domain.watches import (
    FlightWatchCriteria,
    PurchaseMandateCreate,
    PurchaseMandateStatus,
    WatchActionMode,
    WatchMatchStatus,
    WatchMatchSummary,
    WatchNotificationSummary,
    WatchResponse,
    WatchStatus,
)
from agent_system.security.encryption import FieldEncryptor
from agent_system.services.travelers import TravelerProfileService


class WatchWorkflowError(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.safe_message = message


@dataclass(frozen=True)
class WatchGateSettings:
    auto_buy_enabled: bool = False
    provider_order_ready: bool = False
    off_session_payment_ready: bool = False
    mandate_terms_reviewed: bool = False
    sandbox_fault_tests_passed: bool = False
    duplicate_purchase_zero: bool = False
    spend_limits_configured: bool = False
    monitoring_ready: bool = False
    operator_ready: bool = False
    pilot_approved: bool = False
    kill_switch: bool = False
    global_spend_limit: Decimal = Decimal("0")
    per_user_spend_limit: Decimal = Decimal("0")
    per_watch_spend_limit: Decimal = Decimal("0")

    @classmethod
    def from_environment(cls) -> WatchGateSettings:
        def flag(name: str) -> bool:
            return os.getenv(name, "false").lower() in {"1", "true", "yes"}

        def money(name: str) -> Decimal:
            try:
                return Decimal(os.getenv(name, "0"))
            except Exception as exc:
                raise ValueError(f"{name} must be a decimal amount") from exc

        return cls(
            auto_buy_enabled=flag("AUTO_BUY_ENABLED"),
            provider_order_ready=flag("AUTO_BUY_PROVIDER_ORDER_READY"),
            off_session_payment_ready=flag("AUTO_BUY_OFF_SESSION_PAYMENT_READY"),
            mandate_terms_reviewed=flag("AUTO_BUY_MANDATE_TERMS_REVIEWED"),
            sandbox_fault_tests_passed=flag("AUTO_BUY_SANDBOX_FAULT_TESTS_PASSED"),
            duplicate_purchase_zero=flag("AUTO_BUY_DUPLICATE_RATE_ZERO"),
            spend_limits_configured=flag("AUTO_BUY_SPEND_LIMITS_CONFIGURED"),
            monitoring_ready=flag("AUTO_BUY_MONITORING_READY"),
            operator_ready=flag("AUTO_BUY_OPERATOR_READY"),
            pilot_approved=flag("AUTO_BUY_PILOT_APPROVED"),
            kill_switch=flag("AUTO_BUY_KILL_SWITCH"),
            global_spend_limit=money("AUTO_BUY_GLOBAL_SPEND_LIMIT"),
            per_user_spend_limit=money("AUTO_BUY_PER_USER_SPEND_LIMIT"),
            per_watch_spend_limit=money("AUTO_BUY_PER_WATCH_SPEND_LIMIT"),
        )

    def allows_auto_buy(self, *, execution_mode: ExecutionMode) -> bool:
        return (
            execution_mode is ExecutionMode.PRODUCTION
            and self.auto_buy_enabled
            and not self.kill_switch
            and self.provider_order_ready
            and self.off_session_payment_ready
            and self.mandate_terms_reviewed
            and self.sandbox_fault_tests_passed
            and self.duplicate_purchase_zero
            and self.spend_limits_configured
            and self.monitoring_ready
            and self.operator_ready
            and self.pilot_approved
            and min(self.global_spend_limit, self.per_user_spend_limit, self.per_watch_spend_limit)
            > 0
        )


class WatchService:
    def __init__(
        self,
        session_factory,
        encryptor: FieldEncryptor,
        *,
        execution_mode: ExecutionMode = ExecutionMode.MOCK,
        gates: WatchGateSettings | None = None,
        clock=None,
    ) -> None:
        self.session_factory = session_factory
        self.encryptor = encryptor
        self.execution_mode = execution_mode
        self.gates = gates or WatchGateSettings.from_environment()
        self.clock = clock

    def _now(self) -> datetime:
        return (self.clock.now() if self.clock is not None else datetime.now(UTC)).astimezone(UTC)

    @staticmethod
    def _payment_aad(user_id: UUID, watch_id: UUID, version: int) -> bytes:
        return f"watch-mandate:{user_id}:{watch_id}:v{version}".encode()

    @staticmethod
    def _json_instant(value: datetime | None) -> datetime | None:
        if value is None:
            return None
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)

    @staticmethod
    def _match_summary(record: WatchMatchRecord) -> WatchMatchSummary | None:
        try:
            offer = FlightOffer.model_validate(record.offer_snapshot)
            segment = offer.segments[0]
            return WatchMatchSummary(
                match_id=record.id,
                offer_id=record.source_offer_id,
                status=record.status,
                price=offer.total.amount,
                currency=offer.total.currency,
                origin=segment.origin,
                destination=offer.segments[-1].destination,
                departure_at=segment.departure_at,
                provider=record.provider,
                environment=record.environment,
                expires_at=offer.metadata.expires_at,
                matched_at=WatchService._json_instant(record.matched_at),
            )
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _notification_summaries(
        records: list[WatchNotificationRecord],
    ) -> tuple[WatchNotificationSummary, ...]:
        summaries: list[WatchNotificationSummary] = []
        for record in records:
            try:
                channel = NotificationChannel(record.channel)
            except ValueError:
                continue
            summaries.append(
                WatchNotificationSummary(
                    channel=channel,
                    status=record.status,
                    sent_at=WatchService._json_instant(record.sent_at),
                    error_code=record.error_code,
                )
            )
        return tuple(sorted(summaries, key=lambda item: item.channel.value))

    def _summary_rows(
        self, session, records: list[FlightWatchRecord]
    ) -> tuple[
        dict[UUID, WatchRunRecord],
        dict[UUID, WatchMatchRecord],
        dict[UUID, list[WatchNotificationRecord]],
    ]:
        if not records:
            return {}, {}, {}
        user_id = records[0].user_id
        watch_ids = [record.id for record in records]
        run_ranked = (
            select(
                WatchRunRecord.id.label("run_id"),
                func.row_number()
                .over(
                    partition_by=WatchRunRecord.watch_id,
                    order_by=desc(WatchRunRecord.completed_at),
                )
                .label("row_number"),
            )
            .where(
                WatchRunRecord.user_id == user_id,
                WatchRunRecord.watch_id.in_(watch_ids),
                WatchRunRecord.completed_at.is_not(None),
            )
            .subquery()
        )
        latest_runs = session.scalars(
            select(WatchRunRecord)
            .join(run_ranked, WatchRunRecord.id == run_ranked.c.run_id)
            .where(run_ranked.c.row_number == 1)
        ).all()
        match_ranked = (
            select(
                WatchMatchRecord.id.label("match_id"),
                func.row_number()
                .over(
                    partition_by=WatchMatchRecord.watch_id,
                    order_by=desc(WatchMatchRecord.matched_at),
                )
                .label("row_number"),
            )
            .where(
                WatchMatchRecord.user_id == user_id,
                WatchMatchRecord.watch_id.in_(watch_ids),
                WatchMatchRecord.status != WatchMatchStatus.REJECTED.value,
            )
            .subquery()
        )
        latest_matches = session.scalars(
            select(WatchMatchRecord)
            .join(match_ranked, WatchMatchRecord.id == match_ranked.c.match_id)
            .where(match_ranked.c.row_number == 1)
        ).all()
        match_ids = [record.id for record in latest_matches]
        notifications = (
            session.scalars(
                select(WatchNotificationRecord)
                .where(
                    WatchNotificationRecord.user_id == user_id,
                    WatchNotificationRecord.match_id.in_(match_ids),
                )
                .order_by(WatchNotificationRecord.channel)
            ).all()
            if match_ids
            else []
        )
        notifications_by_match: dict[UUID, list[WatchNotificationRecord]] = {}
        for notification in notifications:
            notifications_by_match.setdefault(notification.match_id, []).append(notification)
        return (
            {run.watch_id: run for run in latest_runs},
            {match.watch_id: match for match in latest_matches},
            notifications_by_match,
        )

    def _safe(
        self,
        record: FlightWatchRecord,
        *,
        session=None,
        summaries=None,
    ) -> dict[str, Any]:
        criteria = FlightWatchCriteria.model_validate(record.criteria)
        if summaries is None:
            summaries = (
                self._summary_rows(session, [record]) if session is not None else ({}, {}, {})
            )
        latest_runs, latest_matches, notifications_by_match = summaries
        latest_run = latest_runs.get(record.id)
        latest_match = latest_matches.get(record.id)
        response = WatchResponse(
            watch_id=record.id,
            status=record.status,
            criteria=criteria,
            next_run_at=self._json_instant(record.next_run_at),
            last_checked_at=self._json_instant(latest_run.completed_at if latest_run else None),
            run_count=record.run_count,
            consecutive_failures=record.consecutive_failures,
            last_error_code=record.last_error_code,
            latest_match=self._match_summary(latest_match) if latest_match else None,
            latest_notifications=self._notification_summaries(
                notifications_by_match.get(latest_match.id, []) if latest_match else []
            ),
        )
        return response.model_dump(mode="json")

    def create(
        self, principal: AuthenticatedPrincipal, criteria: FlightWatchCriteria
    ) -> dict[str, Any]:
        if criteria.action_mode is WatchActionMode.AUTO_BUY:
            raise WatchWorkflowError(
                "auto_buy_disabled", "auto-buy cannot be enabled from a client or chat request"
            )
        session = self.session_factory()
        try:
            with session.begin():
                record = FlightWatchRecord(
                    user_id=principal.user_id,
                    criteria=criteria.model_dump(mode="json"),
                    status=WatchStatus.DRAFT.value,
                    next_run_at=None,
                )
                session.add(record)
                session.flush()
                return self._safe(record, session=session)
        finally:
            session.close()

    def get(self, principal: AuthenticatedPrincipal, watch_id: UUID) -> dict[str, Any]:
        session = self.session_factory()
        try:
            record = session.scalar(
                select(FlightWatchRecord).where(
                    FlightWatchRecord.id == watch_id, FlightWatchRecord.user_id == principal.user_id
                )
            )
            if record is None:
                raise WatchWorkflowError("not_found", "watch was not found")
            return self._safe(record, session=session)
        finally:
            session.close()

    def list(self, principal: AuthenticatedPrincipal) -> list[dict[str, Any]]:
        session = self.session_factory()
        try:
            rows = session.scalars(
                select(FlightWatchRecord)
                .where(FlightWatchRecord.user_id == principal.user_id)
                .order_by(FlightWatchRecord.created_at.desc())
                .limit(100)
            ).all()
            summaries = self._summary_rows(session, rows)
            return [self._safe(row, summaries=summaries) for row in rows]
        finally:
            session.close()

    def transition(
        self, principal: AuthenticatedPrincipal, watch_id: UUID, target: WatchStatus
    ) -> dict[str, Any]:
        session = self.session_factory()
        try:
            with session.begin():
                record = session.scalar(
                    select(FlightWatchRecord)
                    .where(
                        FlightWatchRecord.id == watch_id,
                        FlightWatchRecord.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise WatchWorkflowError("not_found", "watch was not found")
                current = WatchStatus(record.status)
                allowed = {
                    WatchStatus.DRAFT: {WatchStatus.ACTIVE, WatchStatus.CANCELLED},
                    WatchStatus.ACTIVE: {
                        WatchStatus.PAUSED,
                        WatchStatus.CANCELLED,
                        WatchStatus.EXPIRED,
                        WatchStatus.NEEDS_USER_ACTION,
                    },
                    WatchStatus.PAUSED: {WatchStatus.ACTIVE, WatchStatus.CANCELLED},
                    WatchStatus.MATCHED: {
                        WatchStatus.ACTIVE,
                        WatchStatus.PAUSED,
                        WatchStatus.CANCELLED,
                        WatchStatus.EXECUTING,
                        WatchStatus.AWAITING_CONFIRMATION,
                    },
                    WatchStatus.AWAITING_CONFIRMATION: {
                        WatchStatus.ACTIVE,
                        WatchStatus.PAUSED,
                        WatchStatus.EXECUTING,
                        WatchStatus.CANCELLED,
                    },
                    WatchStatus.EXECUTING: {
                        WatchStatus.BOOKED,
                        WatchStatus.NEEDS_USER_ACTION,
                        WatchStatus.FAILED,
                    },
                    WatchStatus.BOOKED: {WatchStatus.CANCELLED},
                    WatchStatus.NEEDS_USER_ACTION: {
                        WatchStatus.ACTIVE,
                        WatchStatus.PAUSED,
                        WatchStatus.CANCELLED,
                        WatchStatus.EXECUTING,
                    },
                    WatchStatus.FAILED: {
                        WatchStatus.ACTIVE,
                        WatchStatus.PAUSED,
                        WatchStatus.CANCELLED,
                    },
                    WatchStatus.EXPIRED: set(),
                    WatchStatus.CANCELLED: set(),
                    WatchStatus.COMPLETED: set(),
                }
                if target not in allowed[current]:
                    raise WatchWorkflowError(
                        "invalid_state",
                        f"cannot transition watch from {current.value} to {target.value}",
                    )
                if target is WatchStatus.ACTIVE:
                    criteria = FlightWatchCriteria.model_validate(record.criteria)
                    if criteria.action_mode is WatchActionMode.AUTO_BUY:
                        if not self.gates.allows_auto_buy(execution_mode=self.execution_mode):
                            raise WatchWorkflowError(
                                "auto_buy_gate_closed", "auto-buy production gates are incomplete"
                            )
                        if (
                            session.scalar(
                                select(PurchaseMandateRecord.id).where(
                                    PurchaseMandateRecord.watch_id == record.id,
                                    PurchaseMandateRecord.user_id == principal.user_id,
                                    PurchaseMandateRecord.status
                                    == PurchaseMandateStatus.ACTIVE.value,
                                )
                            )
                            is None
                        ):
                            raise WatchWorkflowError(
                                "mandate_required", "an active purchase mandate is required"
                            )
                    record.next_run_at = self._now()
                else:
                    record.next_run_at = None
                record.status = target.value
                record.version += 1
                session.flush()
                return self._safe(record, session=session)
        finally:
            session.close()

    def update(
        self, principal: AuthenticatedPrincipal, watch_id: UUID, fields: dict
    ) -> dict[str, Any]:
        session = self.session_factory()
        try:
            with session.begin():
                record = session.scalar(
                    select(FlightWatchRecord)
                    .where(
                        FlightWatchRecord.id == watch_id,
                        FlightWatchRecord.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise WatchWorkflowError("not_found", "watch was not found")
                if "criteria" in fields:
                    updated = FlightWatchCriteria.model_validate(fields["criteria"])
                    record.criteria = updated.model_dump(mode="json")
                record.version += 1
                session.flush()
                return self._safe(record, session=session)
        finally:
            session.close()

    def delete(self, principal: AuthenticatedPrincipal, watch_id: UUID) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                record = session.scalar(
                    select(FlightWatchRecord)
                    .where(
                        FlightWatchRecord.id == watch_id,
                        FlightWatchRecord.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                if record is None:
                    raise WatchWorkflowError("not_found", "watch was not found")
                session.delete(record)
                session.flush()
        finally:
            session.close()

    def create_mandate(
        self, principal: AuthenticatedPrincipal, watch_id: UUID, request: PurchaseMandateCreate
    ) -> dict[str, Any]:
        session = self.session_factory()
        try:
            with session.begin():
                watch = session.scalar(
                    select(FlightWatchRecord)
                    .where(
                        FlightWatchRecord.id == watch_id,
                        FlightWatchRecord.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                if watch is None:
                    raise WatchWorkflowError("not_found", "watch was not found")
                criteria = FlightWatchCriteria.model_validate(watch.criteria)
                if criteria.action_mode is not WatchActionMode.AUTO_BUY:
                    raise WatchWorkflowError(
                        "auto_buy_watch_required", "a mandate must belong to an auto-buy watch"
                    )
                if not self.gates.allows_auto_buy(execution_mode=self.execution_mode):
                    raise WatchWorkflowError(
                        "auto_buy_gate_closed", "auto-buy production gates are incomplete"
                    )
                if not request.acknowledged_terms or not request.off_session_permission:
                    raise WatchWorkflowError(
                        "mandate_consent_required", "terms and off-session consent are required"
                    )
                TravelerProfileService(session, self.encryptor).select_for_booking(
                    principal,
                    criteria.traveler_profile_ids,
                    international=True,
                )
                current_version = (
                    session.scalar(
                        select(func.max(PurchaseMandateRecord.version)).where(
                            PurchaseMandateRecord.watch_id == watch_id,
                            PurchaseMandateRecord.user_id == principal.user_id,
                        )
                    )
                    or 0
                )
                encrypted = self.encryptor.encrypt_text(
                    request.payment_method_reference.get_secret_value(),
                    associated_data=self._payment_aad(
                        principal.user_id, watch_id, current_version + 1
                    ),
                )
                mandate = PurchaseMandateRecord(
                    user_id=principal.user_id,
                    watch_id=watch_id,
                    traveler_profile_ids=[str(item) for item in criteria.traveler_profile_ids],
                    criteria_snapshot=criteria.model_dump(mode="json"),
                    maximum_amount=criteria.maximum_total.amount,
                    currency=criteria.maximum_total.currency,
                    purchase_deadline=criteria.purchase_deadline,
                    payment_method_reference_encrypted=encrypted.ciphertext,
                    payment_reference_key_version=encrypted.key_version,
                    off_session_permission=True,
                    terms_version=request.terms_version,
                    consent_version=request.consent_version,
                    consented_at=self._now(),
                    status=PurchaseMandateStatus.ACTIVE.value,
                    version=current_version + 1,
                )
                session.add(mandate)
                session.flush()
                return {
                    "mandate_id": str(mandate.id),
                    "watch_id": str(watch_id),
                    "status": mandate.status,
                    "version": mandate.version,
                    "terms_version": mandate.terms_version,
                }
        finally:
            session.close()

    def set_mandate_status(
        self, principal: AuthenticatedPrincipal, mandate_id: UUID, status: PurchaseMandateStatus
    ) -> dict[str, Any]:
        session = self.session_factory()
        try:
            with session.begin():
                mandate = session.scalar(
                    select(PurchaseMandateRecord)
                    .where(
                        PurchaseMandateRecord.id == mandate_id,
                        PurchaseMandateRecord.user_id == principal.user_id,
                    )
                    .with_for_update()
                )
                if mandate is None:
                    raise WatchWorkflowError("not_found", "purchase mandate was not found")
                if status is PurchaseMandateStatus.REVOKED:
                    mandate.revoked_at = self._now()
                mandate.status = status.value
                mandate.version += 1
                session.flush()
                return {
                    "mandate_id": str(mandate.id),
                    "watch_id": str(mandate.watch_id),
                    "status": mandate.status,
                    "version": mandate.version,
                }
        finally:
            session.close()
