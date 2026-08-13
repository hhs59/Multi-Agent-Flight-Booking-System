from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from importlib import resources

from pydantic import Field, RootModel, field_validator, model_validator

from agent_system.domain.accounts import CountryCode
from agent_system.domain.recommendations import (
    LocaleCode,
    PlaceCandidate,
    PlaceCategory,
    PlaceSourceEnvironment,
)
from agent_system.domain.values import AirportCode, DomainModel, UTCInstant


class LocalizedText(RootModel[dict[str, str]]):
    @field_validator("root")
    @classmethod
    def validate_values(cls, value: dict[str, str]) -> dict[str, str]:
        normalized = {}
        for locale, text in value.items():
            if not isinstance(locale, str) or len(locale) > 12:
                raise ValueError("localized text locale keys must be bounded strings")
            if not isinstance(text, str) or not text.strip() or len(text) > 600:
                raise ValueError("localized text values must be bounded non-empty strings")
            normalized[locale.strip().casefold()] = text.strip()
        if "en" not in normalized:
            raise ValueError("localized text must include an English value")
        return normalized

    @property
    def values(self) -> dict[str, str]:
        return self.root

    def for_locale(self, locale: str) -> str:
        return self.values.get(locale.casefold(), self.values["en"])


class CuratedDestinationRecord(DomainModel):
    place_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    airport_codes: tuple[AirportCode, ...] = Field(min_length=1, max_length=10)
    city_code: str = Field(min_length=2, max_length=16, pattern=r"^[A-Z0-9_-]+$")
    country_code: CountryCode
    city: LocalizedText
    country: LocalizedText
    name: LocalizedText
    categories: tuple[PlaceCategory, ...] = Field(min_length=1, max_length=8)
    latitude: Decimal | None = Field(default=None, ge=Decimal("-90"), le=Decimal("90"))
    longitude: Decimal | None = Field(default=None, ge=Decimal("-180"), le=Decimal("180"))
    short_facts: LocalizedText
    source_name: str = Field(min_length=1, max_length=80)
    source_url: str | None = Field(default=None, max_length=2048)
    last_verified_at: UTCInstant

    @model_validator(mode="after")
    def validate_coordinates(self) -> CuratedDestinationRecord:
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        if len(set(self.airport_codes)) != len(self.airport_codes):
            raise ValueError("airport codes must be unique per catalog record")
        return self


class CuratedCatalogFile(DomainModel):
    catalog_version: str = Field(min_length=1, max_length=80)
    destinations: tuple[CuratedDestinationRecord, ...] = Field(min_length=1, max_length=10_000)


@dataclass(frozen=True)
class CatalogDestination:
    airport_code: str
    city_code: str
    country_code: str
    city: LocalizedText
    country: LocalizedText

    def city_label(self, locale: str) -> str:
        return self.city.for_locale(locale)

    def country_label(self, locale: str) -> str:
        return self.country.for_locale(locale)


class CuratedDestinationCatalog:
    """Validated, versioned catalog with no destination-specific code branches."""

    def __init__(
        self,
        records: tuple[CuratedDestinationRecord, ...],
        *,
        catalog_version: str = "curated_destinations.v1",
    ) -> None:
        if not records:
            raise ValueError("curated destination catalog cannot be empty")
        self.catalog_version = catalog_version
        self.records = tuple(records)
        by_id: dict[str, CuratedDestinationRecord] = {}
        by_airport: dict[str, list[CuratedDestinationRecord]] = {}
        destinations: dict[str, CatalogDestination] = {}
        for record in self.records:
            if record.place_id in by_id:
                raise ValueError(f"duplicate curated place ID: {record.place_id}")
            by_id[record.place_id] = record
            for airport in record.airport_codes:
                by_airport.setdefault(airport, []).append(record)
                destination = CatalogDestination(
                    airport_code=airport,
                    city_code=record.city_code,
                    country_code=record.country_code,
                    city=record.city,
                    country=record.country,
                )
                existing = destinations.get(airport)
                if existing is not None and existing != destination:
                    raise ValueError(f"conflicting destination metadata for airport: {airport}")
                destinations[airport] = destination
        self._by_id = by_id
        self._by_airport = {key: tuple(value) for key, value in by_airport.items()}
        self._destinations = destinations

    @classmethod
    def from_package_data(cls) -> CuratedDestinationCatalog:
        package = resources.files("agent_system.data")
        raw = package.joinpath("curated_destinations.v1.json").read_text(encoding="utf-8")
        return cls.from_json_text(raw)

    @classmethod
    def from_json_text(cls, raw: str) -> CuratedDestinationCatalog:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ValueError("curated destination catalog is not valid JSON") from exc
        parsed = CuratedCatalogFile.model_validate(payload)
        return cls(parsed.destinations, catalog_version=parsed.catalog_version)

    @classmethod
    def from_records(
        cls,
        records: tuple[CuratedDestinationRecord, ...],
        *,
        catalog_version: str = "curated_destinations.v1",
    ) -> CuratedDestinationCatalog:
        return cls(records, catalog_version=catalog_version)

    @property
    def supported_airports(self) -> tuple[str, ...]:
        return tuple(sorted(self._destinations))

    def resolve(self, destination_airport: str) -> CatalogDestination | None:
        return self._destinations.get(destination_airport.strip().upper())

    def get(self, destination_airport: str) -> CatalogDestination | None:
        return self.resolve(destination_airport)

    def records_for_airport(self, destination_airport: str) -> tuple[CuratedDestinationRecord, ...]:
        return self._by_airport.get(destination_airport.strip().upper(), ())

    def candidates(
        self,
        destination_airport: str,
        *,
        locale: LocaleCode,
        retrieved_at: datetime,
        expires_at: datetime | None = None,
    ) -> tuple[PlaceCandidate, ...]:
        normalized_airport = destination_airport.strip().upper()
        records = self.records_for_airport(normalized_airport)
        if not records:
            return ()
        if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
            raise ValueError("catalog candidate retrieval time must be timezone-aware")
        if expires_at is not None and expires_at <= retrieved_at:
            raise ValueError("catalog candidate expiry must be after retrieval")
        result = []
        for record in records:
            result.append(
                PlaceCandidate(
                    place_id=record.place_id,
                    destination_airport=normalized_airport,
                    city_code=record.city_code,
                    country_code=record.country_code,
                    name=record.name.for_locale(locale),
                    categories=record.categories,
                    latitude=record.latitude,
                    longitude=record.longitude,
                    short_facts=(record.short_facts.for_locale(locale),),
                    facts_as_of=record.last_verified_at,
                    source_name=record.source_name,
                    source_url=record.source_url,
                    environment=PlaceSourceEnvironment.CURATED,
                    is_live=False,
                    retrieved_at=retrieved_at,
                    expires_at=expires_at,
                )
            )
        return tuple(result)


__all__ = [
    "CatalogDestination",
    "CuratedCatalogFile",
    "CuratedDestinationCatalog",
    "CuratedDestinationRecord",
    "LocalizedText",
]
