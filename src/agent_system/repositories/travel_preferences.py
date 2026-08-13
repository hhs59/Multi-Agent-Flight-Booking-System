from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import UserTravelPreferenceRecord
from agent_system.domain.travel_preferences import TravelPreferencesPatch
from agent_system.repositories.base import (
    ConcurrencyConflictError,
    OwnershipViolationError,
)

_MUTABLE_FIELDS = (
    "default_origin_airport",
    "timezone",
    "preferred_cabin",
    "max_stops",
    "baggage_required",
    "preferred_departure_start",
    "preferred_departure_end",
)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("preference timestamps require a timezone-aware instant")
    return value.astimezone(UTC)


class TravelPreferencesRepository:
    """User-scoped persistence for the one-row travel preference record."""

    model = UserTravelPreferenceRecord

    def __init__(self, session: Session, principal: AuthenticatedPrincipal) -> None:
        self.session = session
        self.principal = principal

    def _require_owned(self, user_id: UUID) -> None:
        if user_id != self.principal.user_id:
            raise OwnershipViolationError("travel preferences must use the authenticated principal")

    def get_for_user(self, user_id: UUID) -> UserTravelPreferenceRecord | None:
        self._require_owned(user_id)
        return self.session.scalar(
            select(self.model).where(self.model.user_id == self.principal.user_id)
        )

    def upsert_for_user(
        self,
        user_id: UUID,
        patch: TravelPreferencesPatch,
        expected_version: int | None = None,
        *,
        now: datetime | None = None,
    ) -> UserTravelPreferenceRecord:
        self._require_owned(user_id)
        expected = patch.expected_version if expected_version is None else expected_version
        timestamp = _utc(now or datetime.now(UTC))
        record = self.session.scalar(
            select(self.model).where(self.model.user_id == self.principal.user_id).with_for_update()
        )
        if record is None:
            if expected is not None:
                raise ConcurrencyConflictError("resource version changed")
            values = {
                field: getattr(patch, field) if field in patch.model_fields_set else None
                for field in _MUTABLE_FIELDS
            }
            if values["preferred_cabin"] is not None:
                values["preferred_cabin"] = values["preferred_cabin"].value
            record = self.model(
                user_id=self.principal.user_id,
                **values,
                version=1,
                created_at=timestamp,
                updated_at=timestamp,
            )
            self.session.add(record)
            self.session.flush()
            return record

        if expected is not None and record.version != expected:
            raise ConcurrencyConflictError("resource version changed")
        changed = False
        for field in _MUTABLE_FIELDS:
            if field not in patch.model_fields_set:
                continue
            value = getattr(patch, field)
            if field == "preferred_cabin" and value is not None:
                value = value.value
            if getattr(record, field) != value:
                setattr(record, field, value)
                changed = True
        if changed:
            record.version += 1
            record.updated_at = timestamp
        self.session.flush()
        return record

    def delete_for_user(self, user_id: UUID) -> bool:
        self._require_owned(user_id)
        record = self.session.scalar(
            select(self.model).where(self.model.user_id == self.principal.user_id).with_for_update()
        )
        if record is None:
            return False
        self.session.delete(record)
        self.session.flush()
        return True


__all__ = ["TravelPreferencesRepository"]
