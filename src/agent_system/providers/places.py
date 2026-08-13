from __future__ import annotations

from collections.abc import Iterable
from datetime import timedelta
from typing import TYPE_CHECKING

from agent_system.domain.recommendations import (
    PlaceCandidate,
    PlaceSearchRequest,
    PlaceSourceEnvironment,
)
from agent_system.domain.values import ExecutionMode
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.contracts import PlacesProvider
from agent_system.providers.errors import CapabilityUnavailable

if TYPE_CHECKING:
    from agent_system.services.destination_catalog import CuratedDestinationCatalog


def place_environment(
    value: PlaceSourceEnvironment | ExecutionMode | str,
) -> PlaceSourceEnvironment:
    if isinstance(value, PlaceSourceEnvironment):
        return value
    normalized = value.value if isinstance(value, ExecutionMode) else str(value).strip().lower()
    return PlaceSourceEnvironment(normalized)


class CuratedPlacesProvider:
    name = "curated_v1"
    environment = PlaceSourceEnvironment.CURATED

    def __init__(
        self,
        *,
        catalog: CuratedDestinationCatalog | None = None,
        clock: Clock | None = None,
        cache_ttl_seconds: float = 300.0,
    ) -> None:
        if cache_ttl_seconds <= 0:
            raise ValueError("curated places cache TTL must be positive")
        if catalog is None:
            from agent_system.services.destination_catalog import CuratedDestinationCatalog

            catalog = CuratedDestinationCatalog.from_package_data()
        self.catalog = catalog
        self.clock = clock or SystemClock()
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)

    async def search(self, request: PlaceSearchRequest) -> tuple[PlaceCandidate, ...]:
        retrieved_at = self.clock.now()
        expires_at = retrieved_at + self.cache_ttl
        candidates = self.catalog.candidates(
            request.destination_airport,
            locale=request.locale,
            retrieved_at=retrieved_at,
            expires_at=expires_at,
        )
        return candidates[: request.limit]


class FixturePlacesProvider:
    """Deterministic provider for provider-boundary and failure-path tests."""

    name = "fixture_places"
    environment = PlaceSourceEnvironment.MOCK

    def __init__(
        self,
        candidates: Iterable[PlaceCandidate] = (),
        *,
        clock: Clock | None = None,
    ) -> None:
        self.candidates = tuple(candidates)
        self.clock = clock or SystemClock()

    async def search(self, request: PlaceSearchRequest) -> tuple[PlaceCandidate, ...]:
        return tuple(
            candidate
            for candidate in self.candidates
            if candidate.destination_airport == request.destination_airport
        )[: request.limit]


class UnavailablePlacesProvider:
    def __init__(
        self,
        name: str = "unavailable",
        environment: PlaceSourceEnvironment | ExecutionMode | str = PlaceSourceEnvironment.MOCK,
    ) -> None:
        self.name = name
        self.environment = place_environment(environment)

    async def search(self, _request: PlaceSearchRequest) -> tuple[PlaceCandidate, ...]:
        raise CapabilityUnavailable(
            provider=self.name,
            operation="search",
            safe_message="places capability is not configured",
        )


__all__ = [
    "CuratedPlacesProvider",
    "FixturePlacesProvider",
    "PlacesProvider",
    "UnavailablePlacesProvider",
    "place_environment",
]
