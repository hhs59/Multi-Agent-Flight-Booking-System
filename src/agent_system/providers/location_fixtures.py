from __future__ import annotations

from collections.abc import Mapping

from agent_system.domain.location_resolution import (
    LocationLookupRequest,
    LocationSuggestion,
    LocationSuggestionKind,
    normalize_location_query,
)
from agent_system.domain.trip_discovery import LocationKind
from agent_system.domain.values import ExecutionMode
from agent_system.providers.errors import CapabilityUnavailable
from agent_system.providers.localization import (
    AirportCatalog,
    normalize_vietnamese_alias,
)


class CatalogLocationProvider:
    """Expose only the validated local catalog through the location contract."""

    name = "catalog"

    def __init__(
        self,
        *,
        catalog: AirportCatalog | None = None,
        environment: ExecutionMode = ExecutionMode.MOCK,
    ) -> None:
        self.catalog = catalog or AirportCatalog.from_v2_package_data()
        self.environment = environment

    def _airport(self, code: str, *, country_code: str) -> LocationSuggestion:
        airport = self.catalog.get(code)
        return LocationSuggestion(
            kind=LocationSuggestionKind.AIRPORT,
            display_name=airport.airport_name_en,
            city_name=airport.city_name_en,
            country_code=country_code,
            iata_code=airport.iata_code,
            airport_codes=(airport.iata_code,),
            timezone=airport.timezone,
        )

    def _city(self, reference) -> LocationSuggestion:
        timezone = None
        if reference.airport_candidates:
            timezone = self.catalog.get(reference.airport_candidates[0]).timezone
        return LocationSuggestion(
            kind=LocationSuggestionKind.CITY,
            display_name=reference.normalized_name,
            city_name=reference.normalized_name,
            country_code=reference.country_code,
            airport_codes=reference.airport_candidates,
            timezone=timezone,
        )

    async def suggest(
        self,
        request: LocationLookupRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[LocationSuggestion, ...]:
        del correlation_id
        reference = self.catalog.resolve_location(request.query)
        if reference.kind is LocationKind.UNKNOWN:
            mentions = self.catalog.find_mentions(request.query)
            if len(mentions) == 1 and mentions[0].start == 0:
                reference = mentions[0].reference
        if reference.kind is LocationKind.COUNTRY and reference.country_code:
            return tuple(
                self._city(city)
                for city in self.catalog.supported_city_references(reference.country_code)[
                    : request.limit
                ]
            )
        if reference.kind is LocationKind.CITY and reference.country_code:
            return (self._city(reference),)
        if reference.kind is LocationKind.AIRPORT and reference.country_code:
            return (
                self._airport(
                    reference.airport_candidates[0],
                    country_code=reference.country_code,
                ),
            )
        return ()


def _fixture(
    *,
    kind: LocationSuggestionKind,
    display_name: str,
    country_code: str,
    airport_codes: tuple[str, ...],
    city_name: str | None = None,
    timezone: str | None = None,
) -> LocationSuggestion:
    if kind is LocationSuggestionKind.AIRPORT:
        return LocationSuggestion(
            kind=kind,
            display_name=display_name,
            city_name=city_name,
            country_code=country_code,
            iata_code=airport_codes[0],
            airport_codes=airport_codes,
            timezone=timezone,
        )
    return LocationSuggestion(
        kind=kind,
        display_name=display_name,
        city_name=city_name or display_name,
        country_code=country_code,
        airport_codes=airport_codes,
        timezone=timezone,
    )


_FIXTURES: Mapping[str, tuple[LocationSuggestion, ...]] = {
    "sydney": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Sydney",
            country_code="AU",
            airport_codes=("SYD",),
            timezone="Australia/Sydney",
        ),
    ),
    "melbourne": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Melbourne",
            country_code="AU",
            airport_codes=("MEL",),
            timezone="Australia/Melbourne",
        ),
    ),
    "beijing": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Beijing",
            country_code="CN",
            airport_codes=("PEK", "PKX"),
            timezone="Asia/Shanghai",
        ),
    ),
    "shanghai": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Shanghai",
            country_code="CN",
            airport_codes=("PVG", "SHA"),
            timezone="Asia/Shanghai",
        ),
    ),
    "new york": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="New York",
            country_code="US",
            airport_codes=("JFK", "EWR", "LGA"),
            timezone="America/New_York",
        ),
    ),
    "los angeles": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Los Angeles",
            country_code="US",
            airport_codes=("LAX",),
            timezone="America/Los_Angeles",
        ),
    ),
    "australia": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Sydney",
            country_code="AU",
            airport_codes=("SYD",),
            timezone="Australia/Sydney",
        ),
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Melbourne",
            country_code="AU",
            airport_codes=("MEL",),
            timezone="Australia/Melbourne",
        ),
    ),
    "china": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Beijing",
            country_code="CN",
            airport_codes=("PEK", "PKX"),
            timezone="Asia/Shanghai",
        ),
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Shanghai",
            country_code="CN",
            airport_codes=("PVG", "SHA"),
            timezone="Asia/Shanghai",
        ),
    ),
    "chinese": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Beijing",
            country_code="CN",
            airport_codes=("PEK", "PKX"),
            timezone="Asia/Shanghai",
        ),
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Shanghai",
            country_code="CN",
            airport_codes=("PVG", "SHA"),
            timezone="Asia/Shanghai",
        ),
    ),
    "united states": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="New York",
            country_code="US",
            airport_codes=("JFK", "EWR", "LGA"),
            timezone="America/New_York",
        ),
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Los Angeles",
            country_code="US",
            airport_codes=("LAX",),
            timezone="America/Los_Angeles",
        ),
    ),
    "america": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="New York",
            country_code="US",
            airport_codes=("JFK", "EWR", "LGA"),
            timezone="America/New_York",
        ),
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Los Angeles",
            country_code="US",
            airport_codes=("LAX",),
            timezone="America/Los_Angeles",
        ),
    ),
    "bangkok": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Bangkok",
            country_code="TH",
            airport_codes=("BKK", "DMK"),
            timezone="Asia/Bangkok",
        ),
    ),
    "bang coc": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Bangkok",
            country_code="TH",
            airport_codes=("BKK", "DMK"),
            timezone="Asia/Bangkok",
        ),
    ),
    "bangcoc": (
        _fixture(
            kind=LocationSuggestionKind.CITY,
            display_name="Bangkok",
            country_code="TH",
            airport_codes=("BKK", "DMK"),
            timezone="Asia/Bangkok",
        ),
    ),
    "lax": (
        _fixture(
            kind=LocationSuggestionKind.AIRPORT,
            display_name="Los Angeles International",
            city_name="Los Angeles",
            country_code="US",
            airport_codes=("LAX",),
            timezone="America/Los_Angeles",
        ),
    ),
}


class FixtureLocationProvider:
    """Deterministic global suggestions for mock-mode and unit tests only."""

    name = "fixture"
    environment = ExecutionMode.MOCK

    async def suggest(
        self,
        request: LocationLookupRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[LocationSuggestion, ...]:
        del correlation_id
        normalized = normalize_vietnamese_alias(normalize_location_query(request.query))
        return tuple(_FIXTURES.get(normalized, ()))[: request.limit]


class UnavailableLocationProvider:
    def __init__(
        self,
        name: str = "unavailable",
        environment: ExecutionMode = ExecutionMode.MOCK,
    ) -> None:
        self.name = name
        self.environment = environment

    async def suggest(
        self,
        request: LocationLookupRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[LocationSuggestion, ...]:
        del request, correlation_id
        raise CapabilityUnavailable(
            provider=self.name,
            operation="location_suggestions",
            safe_message="location capability is not configured",
        )


__all__ = [
    "CatalogLocationProvider",
    "FixtureLocationProvider",
    "UnavailableLocationProvider",
]
