from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import TypeAdapter

from agent_system.domain.accounts import CountryCode
from agent_system.domain.location_resolution import (
    LocationLookupRequest,
    LocationSuggestion,
    LocationSuggestionKind,
)
from agent_system.domain.values import AirportCode
from agent_system.providers.duffel.client import DuffelClient
from agent_system.providers.duffel.settings import DuffelSettings
from agent_system.providers.errors import ProviderMalformedResponseError
from agent_system.providers.localization import normalize_vietnamese_alias

_COUNTRY_ADAPTER = TypeAdapter(CountryCode)
_AIRPORT_ADAPTER = TypeAdapter(AirportCode)


def _airport_code(value: object) -> str | None:
    try:
        # LocationSuggestion performs the final AirportCode validation. This
        # pre-check keeps malformed optional provider entries skippable.
        normalized = str(value).strip().upper()
    except (AttributeError, TypeError):
        return None
    if len(normalized) != 3 or not normalized.isalpha():
        return None
    try:
        return _AIRPORT_ADAPTER.validate_python(normalized)
    except (TypeError, ValueError):
        return None


def _country_code(value: object) -> str | None:
    try:
        return _COUNTRY_ADAPTER.validate_python(value)
    except (TypeError, ValueError):
        return None


def _text(value: object, *, maximum: int = 160) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = " ".join(value.strip().split())
    if not normalized or len(normalized) > maximum:
        return None
    return normalized


def _timezone(value: object) -> str | None:
    return _text(value, maximum=64)


def _airport_rows(raw: object) -> list[Mapping[str, Any]]:
    if not isinstance(raw, list):
        return []
    return [item for item in raw if isinstance(item, Mapping)]


def _map_entry(raw: Mapping[str, Any]) -> LocationSuggestion | None:
    entry_type = raw.get("type")
    if entry_type == LocationSuggestionKind.AIRPORT.value:
        code = _airport_code(raw.get("iata_code"))
        country = _country_code(raw.get("iata_country_code"))
        if code is None or country is None:
            return None
        display_name = _text(raw.get("name")) or _text(raw.get("city_name"))
        if display_name is None:
            return None
        try:
            return LocationSuggestion(
                kind=LocationSuggestionKind.AIRPORT,
                display_name=display_name,
                city_name=_text(raw.get("city_name")),
                country_code=country,
                iata_code=code,
                airport_codes=(code,),
                timezone=_timezone(raw.get("time_zone")),
            )
        except (TypeError, ValueError):
            return None

    if entry_type != LocationSuggestionKind.CITY.value:
        return None
    city_details = raw.get("city")
    city_details = city_details if isinstance(city_details, Mapping) else {}
    display_name = (
        _text(city_details.get("name")) or _text(raw.get("city_name")) or _text(raw.get("name"))
    )
    if display_name is None:
        return None
    codes: list[str] = []
    timezones: list[str] = []
    country = _country_code(raw.get("iata_country_code"))
    for airport in _airport_rows(raw.get("airports")):
        code = _airport_code(airport.get("iata_code"))
        if code is None or code in codes:
            continue
        codes.append(code)
        if country is None:
            country = _country_code(airport.get("iata_country_code"))
        timezone = _timezone(airport.get("time_zone"))
        if timezone is not None:
            timezones.append(timezone)
        if len(codes) == 5:
            break
    if country is None or not codes:
        return None
    try:
        return LocationSuggestion(
            kind=LocationSuggestionKind.CITY,
            display_name=display_name,
            city_name=display_name,
            country_code=country,
            airport_codes=tuple(codes),
            timezone=_timezone(raw.get("time_zone")) or (timezones[0] if timezones else None),
        )
    except (TypeError, ValueError):
        return None


class DuffelLocationProvider:
    name = "duffel"

    def __init__(self, settings: DuffelSettings, client: DuffelClient) -> None:
        self.settings = settings
        self.client = client
        self.environment = settings.environment

    async def suggest(
        self,
        request: LocationLookupRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[LocationSuggestion, ...]:
        payload = await self.client.request_json(
            "GET",
            "/places/suggestions",
            params={"query": request.query},
            operation="location_suggestions",
            correlation_id=correlation_id,
        )
        raw_data = payload.get("data")
        if not isinstance(raw_data, list):
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation="location_suggestions",
                safe_message="provider returned malformed place suggestions",
            )
        if not raw_data:
            return ()

        mapped = [
            suggestion
            for raw in raw_data
            if isinstance(raw, Mapping)
            for suggestion in (_map_entry(raw),)
            if suggestion is not None
        ]
        if not mapped:
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation="location_suggestions",
                safe_message="provider returned no valid place suggestions",
            )

        city_airports = {
            (suggestion.country_code, code)
            for suggestion in mapped
            if suggestion.kind is LocationSuggestionKind.CITY
            for code in suggestion.airport_codes
        }
        seen: set[tuple[object, ...]] = set()
        result: list[LocationSuggestion] = []
        for suggestion in mapped:
            if (
                suggestion.kind is LocationSuggestionKind.AIRPORT
                and suggestion.iata_code is not None
                and (suggestion.country_code, suggestion.iata_code) in city_airports
            ):
                continue
            if suggestion.kind is LocationSuggestionKind.AIRPORT:
                identity = (
                    suggestion.kind,
                    suggestion.country_code,
                    suggestion.iata_code,
                )
            else:
                identity = (
                    suggestion.kind,
                    suggestion.country_code,
                    normalize_vietnamese_alias(suggestion.display_name),
                    suggestion.airport_codes,
                )
            if identity in seen:
                continue
            seen.add(identity)
            result.append(suggestion)
            if len(result) >= request.limit:
                break
        return tuple(result)


__all__ = ["DuffelLocationProvider"]
