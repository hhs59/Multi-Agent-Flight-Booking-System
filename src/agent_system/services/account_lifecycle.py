from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from typing import Any

from sqlalchemy import delete, select
from sqlalchemy.inspection import inspect
from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import (
    OWNED_RECORD_TYPES,
    AgentCheckpointRecord,
    AuditEventRecord,
    BookingOperationRecord,
    BookingQuoteRecord,
    OutboxEventRecord,
    PurchaseMandateRecord,
    TravelerProfileRecord,
    UserRecord,
    UserSessionRecord,
    UserTravelPreferenceRecord,
    WatchHoldRecord,
    WatchNotificationRecord,
)
from agent_system.repositories.sessions import SessionRepository
from agent_system.repositories.users import UserRepository
from agent_system.security.encryption import FieldEncryptor
from agent_system.services.travelers import TravelerProfileService

_EXPORT_EXCLUDED_COLUMNS = {
    "birth_date_encrypted",
    "csrf_token_hash",
    "encryption_key_version",
    "nationality_encrypted",
    "passport_expiry_date_encrypted",
    "passport_issuing_country_encrypted",
    "passport_number_encrypted",
    "payment_customer_reference",
    "payment_method_reference",
    "payment_method_reference_encrypted",
    "provider_hold_id_encrypted",
    "provider_reference_key_version",
    "payment_authorization_reference_encrypted",
    "captured_payment_reference_encrypted",
    "consent_snapshot",
    "provider_offer_id",
    "provider_order_id",
    "snapshot_encryption_key_version",
    "traveler_snapshots_encrypted",
    "session_token_hash",
    "user_agent_hash",
}


def _serialize_record(record) -> dict[str, Any]:
    data: dict[str, Any] = {}
    for column in inspect(record).mapper.column_attrs:
        key = column.key
        if key in _EXPORT_EXCLUDED_COLUMNS:
            continue
        value = getattr(record, key)
        if isinstance(value, (datetime, date, time)):
            value = value.isoformat()
        elif hasattr(value, "hex") and not isinstance(value, str):
            value = str(value)
        data[key] = value
    return data


class AccountLifecycleService:
    def __init__(self, session: Session, encryptor: FieldEncryptor) -> None:
        self.session = session
        self.encryptor = encryptor

    def export_account(self, principal: AuthenticatedPrincipal) -> dict[str, Any]:
        user = UserRepository(self.session).require_principal(principal)
        traveler_profiles = TravelerProfileService(self.session, self.encryptor).list(principal)
        travelers = []
        for profile in traveler_profiles:
            profile_data = profile.model_dump(mode="json")
            profile_data["passport_number"] = (
                profile.passport_number.get_secret_value() if profile.passport_number else None
            )
            travelers.append(profile_data)

        resources: dict[str, list[dict[str, Any]]] = {}
        for record_type in OWNED_RECORD_TYPES:
            if record_type in {
                # Operational quotes and idempotency records are not portable account data.
                BookingQuoteRecord,
                BookingOperationRecord,
                PurchaseMandateRecord,
                WatchHoldRecord,
                WatchNotificationRecord,
                AgentCheckpointRecord,
                AuditEventRecord,
                OutboxEventRecord,
                UserSessionRecord,
                TravelerProfileRecord,
            }:
                continue
            rows = self.session.scalars(
                select(record_type).where(record_type.user_id == principal.user_id)
            ).all()
            resources[record_type.__tablename__] = [_serialize_record(row) for row in rows]
        return {
            "exported_at": datetime.now(UTC).isoformat(),
            "user": _serialize_record(user),
            "traveler_profiles": travelers,
            "travel_preferences": resources.get(UserTravelPreferenceRecord.__tablename__, []),
            "resources": resources,
        }

    def request_deletion(
        self,
        principal: AuthenticatedPrincipal,
        *,
        now: datetime | None = None,
        grace_period: timedelta = timedelta(days=30),
    ) -> datetime:
        now = now or datetime.now(UTC)
        user = UserRepository(self.session).require_principal(principal)
        user.status = "pending_deletion"
        user.deletion_requested_at = now
        user.purge_after = now + grace_period
        SessionRepository(self.session).revoke_all_for_user(user.id, now=now)
        self.session.flush()
        return user.purge_after

    def cancel_deletion(self, principal: AuthenticatedPrincipal) -> None:
        user = UserRepository(self.session).require_principal(principal)
        if user.status != "pending_deletion":
            raise ValueError("account has no pending deletion")
        user.status = "active"
        user.deletion_requested_at = None
        user.purge_after = None
        self.session.flush()

    def purge_due_accounts(self, *, now: datetime | None = None) -> int:
        now = now or datetime.now(UTC)
        result = self.session.execute(
            delete(UserRecord).where(
                UserRecord.status == "pending_deletion",
                UserRecord.purge_after <= now,
            )
        )
        return result.rowcount
