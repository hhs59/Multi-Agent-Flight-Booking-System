from __future__ import annotations

import re
from enum import StrEnum
from typing import Literal, Self

from pydantic import Field, field_validator, model_validator

from agent_system.domain.accounts import CountryCode
from agent_system.domain.values import AirportCode, DomainModel


class LocationSuggestionKind(StrEnum):
    AIRPORT = "airport"
    CITY = "city"


_CONTROL_CHARACTER = re.compile(r"[\x00-\x1f\x7f]")
_URL_PATTERN = re.compile(r"(?i)(?:https?://|ftp://|www\.)")


def normalize_location_query(value: str) -> str:
    """Normalize untrusted place text before it reaches a provider."""

    if not isinstance(value, str):
        raise ValueError("location query must be a string")
    if _CONTROL_CHARACTER.search(value):
        raise ValueError("location query cannot contain control characters")
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError("location query cannot be blank")
    if len(normalized) > 160:
        raise ValueError("location query cannot exceed 160 characters")
    if _URL_PATTERN.search(normalized):
        raise ValueError("location query cannot contain a URL")
    return normalized


class LocationLookupRequest(DomainModel):
    query: str = Field(min_length=1, max_length=160)
    locale: Literal["vi", "en"]
    limit: int = Field(default=8, ge=1, le=10)

    @field_validator("query")
    @classmethod
    def normalize_query(cls, value: str) -> str:
        return normalize_location_query(value)


class LocationSuggestion(DomainModel):
    kind: LocationSuggestionKind
    display_name: str = Field(min_length=1, max_length=160)
    city_name: str | None = Field(default=None, max_length=160)
    country_code: CountryCode
    iata_code: AirportCode | None = None
    airport_codes: tuple[AirportCode, ...] = Field(default_factory=tuple, max_length=5)
    timezone: str | None = Field(default=None, max_length=64)

    @field_validator("display_name", "city_name", "timezone")
    @classmethod
    def normalize_optional_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if len(set(self.airport_codes)) != len(self.airport_codes):
            raise ValueError("location suggestion airport codes must be unique")
        if self.kind is LocationSuggestionKind.AIRPORT:
            if self.iata_code is None:
                raise ValueError("airport suggestions require an IATA code")
            if self.airport_codes != (self.iata_code,):
                raise ValueError("airport suggestions must contain exactly their IATA code")
        elif not 1 <= len(self.airport_codes) <= 5:
            raise ValueError("city suggestions require one to five airport codes")
        return self


__all__ = [
    "LocationLookupRequest",
    "LocationSuggestion",
    "LocationSuggestionKind",
    "normalize_location_query",
]
