from __future__ import annotations

from datetime import time
from typing import Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import ConfigDict, Field, field_validator, model_validator

from agent_system.domain.flights import CabinClass
from agent_system.domain.values import AirportCode, DomainModel, UTCInstant


def _validate_timezone(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    if not normalized:
        raise ValueError("timezone cannot be blank")
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {normalized}") from exc
    return normalized


def _validate_departure_window(start: time | None, end: time | None) -> None:
    if (start is None) != (end is None):
        raise ValueError("preferred departure start and end must be provided together")
    # A start later than the end is intentionally valid: it describes an overnight window.


class TravelPreferencesPatch(DomainModel):
    """Explicit unset/null/value patch for user-owned travel preferences."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    default_origin_airport: AirportCode | None = None
    timezone: str | None = None
    preferred_cabin: CabinClass | None = None
    max_stops: int | None = Field(default=None, ge=0, le=4)
    baggage_required: bool | None = None
    preferred_departure_start: time | None = None
    preferred_departure_end: time | None = None
    expected_version: int | None = Field(default=None, ge=1)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return _validate_timezone(value)

    @model_validator(mode="after")
    def validate_patch_window(self) -> Self:
        fields = self.model_fields_set
        window_fields = {
            "preferred_departure_start",
            "preferred_departure_end",
        }
        if fields.intersection(window_fields):
            if not window_fields.issubset(fields):
                raise ValueError("preferred departure start and end must be patched together")
            _validate_departure_window(
                self.preferred_departure_start,
                self.preferred_departure_end,
            )
        return self


class TravelPreferencesPlanningProjection(DomainModel):
    """The only preference data allowed into planning and checkpoint state."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    default_origin_airport: AirportCode | None = None
    timezone: str | None = None
    preferred_cabin: CabinClass | None = None
    max_stops: int | None = Field(default=None, ge=0, le=4)
    baggage_required: bool | None = None
    preferred_departure_start: time | None = None
    preferred_departure_end: time | None = None
    version: int = Field(ge=1)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return _validate_timezone(value)

    @model_validator(mode="after")
    def validate_projection_window(self) -> Self:
        _validate_departure_window(
            self.preferred_departure_start,
            self.preferred_departure_end,
        )
        return self


class TravelPreferences(DomainModel):
    """Immutable, validated preference data returned by the persistence boundary."""

    model_config = ConfigDict(extra="forbid", frozen=True, validate_assignment=True)

    user_id: UUID
    default_origin_airport: AirportCode | None = None
    timezone: str | None = None
    preferred_cabin: CabinClass | None = None
    max_stops: int | None = Field(default=None, ge=0, le=4)
    baggage_required: bool | None = None
    preferred_departure_start: time | None = None
    preferred_departure_end: time | None = None
    version: int = Field(ge=1)
    created_at: UTCInstant
    updated_at: UTCInstant

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str | None) -> str | None:
        return _validate_timezone(value)

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        _validate_departure_window(
            self.preferred_departure_start,
            self.preferred_departure_end,
        )
        return self


class TravelPreferencesView(TravelPreferences):
    """Safe API view; it contains no passenger, payment, or provider data."""

    configured: Literal[True] = True


__all__ = [
    "TravelPreferences",
    "TravelPreferencesPatch",
    "TravelPreferencesPlanningProjection",
    "TravelPreferencesView",
]
