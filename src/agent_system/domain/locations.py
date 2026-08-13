from __future__ import annotations

from decimal import Decimal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator

from agent_system.domain.values import AirportCode, DomainModel


class GeoPoint(DomainModel):
    latitude: Decimal = Field(ge=Decimal("-90"), le=Decimal("90"))
    longitude: Decimal = Field(ge=Decimal("-180"), le=Decimal("180"))


class AirportLocation(DomainModel):
    iata_code: AirportCode
    city_name_en: str = Field(min_length=1, max_length=200)
    city_name_vi: str = Field(min_length=1, max_length=200)
    airport_name_en: str = Field(min_length=1, max_length=300)
    airport_name_vi: str = Field(min_length=1, max_length=300)
    timezone: str = Field(min_length=1, max_length=64)
    coordinates: GeoPoint

    @field_validator(
        "city_name_en",
        "city_name_vi",
        "airport_name_en",
        "airport_name_vi",
        "timezone",
    )
    @classmethod
    def reject_blank_values(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("location text cannot be blank")
        return normalized

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value
