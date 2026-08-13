from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.domain.travel_preferences import (
    TravelPreferences,
    TravelPreferencesPatch,
    TravelPreferencesPlanningProjection,
    TravelPreferencesView,
)
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.localization import AirportCatalog
from agent_system.repositories.events import AuditRepository
from agent_system.repositories.travel_preferences import (
    _MUTABLE_FIELDS,
    TravelPreferencesRepository,
)


def _db_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


class TravelPreferenceService:
    """Validates and owns the safe travel-preference lifecycle."""

    def __init__(
        self,
        session: Session,
        *,
        clock: Clock | None = None,
        catalog: AirportCatalog | None = None,
    ) -> None:
        self.session = session
        self.clock = clock or SystemClock()
        self.catalog = catalog or AirportCatalog.from_v2_package_data()

    def _repository(self, principal: AuthenticatedPrincipal) -> TravelPreferencesRepository:
        return TravelPreferencesRepository(self.session, principal)

    def _validate_airport(self, value: str | None) -> None:
        if value is None:
            return
        reference = self.catalog.resolve_location(value)
        if reference.kind.value != "airport" or reference.airport_candidates != (value,):
            raise ValueError("default_origin_airport must resolve to one exact airport")

    def _view(self, record) -> TravelPreferencesView:
        preferences = TravelPreferences(
            user_id=record.user_id,
            default_origin_airport=record.default_origin_airport,
            timezone=record.timezone,
            preferred_cabin=record.preferred_cabin,
            max_stops=record.max_stops,
            baggage_required=record.baggage_required,
            preferred_departure_start=record.preferred_departure_start,
            preferred_departure_end=record.preferred_departure_end,
            version=record.version,
            created_at=_db_utc(record.created_at),
            updated_at=_db_utc(record.updated_at),
        )
        return TravelPreferencesView(**preferences.model_dump())

    def _merged_values(self, record, patch: TravelPreferencesPatch) -> dict[str, Any]:
        return {
            field: (
                getattr(patch, field)
                if field in patch.model_fields_set
                else getattr(record, field)
                if record is not None
                else None
            )
            for field in _MUTABLE_FIELDS
        }

    def _validate_patch(self, record, patch: TravelPreferencesPatch, principal) -> None:
        values = self._merged_values(record, patch)
        self._validate_airport(values["default_origin_airport"])
        # The domain model validates timezone, cabin, stops, and the paired window.
        now = self.clock.now()
        version = record.version if record is not None else 1
        created_at = _db_utc(record.created_at) if record is not None else now
        TravelPreferences(
            user_id=principal.user_id,
            **values,
            version=version,
            created_at=created_at,
            updated_at=now,
        )

    def get_for_user(self, principal: AuthenticatedPrincipal) -> TravelPreferencesView | None:
        record = self._repository(principal).get_for_user(principal.user_id)
        return self._view(record) if record is not None else None

    def planning_projection(
        self,
        principal: AuthenticatedPrincipal,
    ) -> dict[str, Any] | None:
        view = self.get_for_user(principal)
        if view is None:
            return None
        projection = TravelPreferencesPlanningProjection(
            default_origin_airport=view.default_origin_airport,
            timezone=view.timezone,
            preferred_cabin=view.preferred_cabin,
            max_stops=view.max_stops,
            baggage_required=view.baggage_required,
            preferred_departure_start=view.preferred_departure_start,
            preferred_departure_end=view.preferred_departure_end,
            version=view.version,
        )
        return projection.model_dump(mode="json")

    def upsert(
        self,
        principal: AuthenticatedPrincipal,
        patch: TravelPreferencesPatch,
    ) -> TravelPreferencesView:
        repository = self._repository(principal)
        existing = repository.get_for_user(principal.user_id)
        self._validate_patch(existing, patch, principal)
        changed_fields = sorted(
            field for field in _MUTABLE_FIELDS if field in patch.model_fields_set
        )
        record = repository.upsert_for_user(
            principal.user_id,
            patch,
            patch.expected_version,
            now=self.clock.now(),
        )
        if existing is None:
            action = "travel_preferences.created"
        elif changed_fields:
            action = "travel_preferences.updated"
        else:
            action = None
        if action is not None:
            AuditRepository(self.session, principal).record(
                action=action,
                resource_type="travel_preferences",
                resource_id=principal.user_id,
                metadata={
                    "changed_fields": changed_fields,
                    "version": record.version,
                },
                occurred_at=self.clock.now(),
            )
        return self._view(record)

    def delete(self, principal: AuthenticatedPrincipal) -> bool:
        repository = self._repository(principal)
        existing = repository.get_for_user(principal.user_id)
        if existing is None:
            return False
        deleted = repository.delete_for_user(principal.user_id)
        if deleted:
            AuditRepository(self.session, principal).record(
                action="travel_preferences.deleted",
                resource_type="travel_preferences",
                resource_id=principal.user_id,
                metadata={"version": existing.version},
                occurred_at=self.clock.now(),
            )
        return deleted


__all__ = ["TravelPreferenceService"]
