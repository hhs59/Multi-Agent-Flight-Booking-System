from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from threading import Lock

from agent_system.domain.flights import (
    CabinClass,
    FlightSearchCriteria,
    SearchResultPage,
)
from agent_system.domain.provider_services import WeatherForecast
from agent_system.domain.recommendations import PlaceCandidate, PlaceSourceEnvironment
from agent_system.domain.values import ExecutionMode


def _utc(instant: datetime) -> datetime:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("cache timestamps must be timezone-aware")
    return instant.astimezone(UTC)


@dataclass(frozen=True)
class SearchCacheKey:
    provider: str
    environment: ExecutionMode
    origin: str
    destination: str
    departure_date: date
    return_date: date | None
    adults: int
    children: int
    infants: int
    cabin: CabinClass
    currency: str
    max_stops: int | None
    preferred_carriers: tuple[str, ...]

    @classmethod
    def from_criteria(
        cls,
        provider: str,
        environment: ExecutionMode,
        criteria: FlightSearchCriteria,
    ) -> SearchCacheKey:
        return cls(
            provider=provider,
            environment=environment,
            origin=criteria.origin,
            destination=criteria.destination,
            departure_date=criteria.departure_date,
            return_date=criteria.return_date,
            adults=criteria.passengers.adults,
            children=criteria.passengers.children,
            infants=criteria.passengers.infants,
            cabin=criteria.cabin,
            currency=criteria.currency,
            max_stops=criteria.max_stops,
            preferred_carriers=tuple(sorted(criteria.preferred_carriers)),
        )


class SearchCache:
    def __init__(
        self,
        max_entries: int = 256,
        negative_ttl: timedelta = timedelta(seconds=30),
    ) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        if negative_ttl <= timedelta(0):
            raise ValueError("negative_ttl must be positive")
        self.max_entries = max_entries
        self.negative_ttl = negative_ttl
        self._items: OrderedDict[SearchCacheKey, tuple[datetime, SearchResultPage]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: SearchCacheKey, *, now: datetime) -> SearchResultPage | None:
        checked_at = _utc(now)
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, page = item
            if checked_at >= expires_at:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return page.model_copy(deep=True)

    def put(self, key: SearchCacheKey, page: SearchResultPage, *, now: datetime) -> None:
        stored_at = _utc(now)
        if page.offers:
            expiries = [
                offer.metadata.expires_at
                for offer in page.offers
                if offer.metadata.expires_at is not None
            ]
            if len(expiries) != len(page.offers):
                return
            expires_at = min(expiries)
            if page.metadata.expires_at is not None:
                expires_at = min(expires_at, page.metadata.expires_at)
        else:
            expires_at = stored_at + self.negative_ttl
            if page.metadata.expires_at is not None:
                expires_at = min(expires_at, page.metadata.expires_at)
        if expires_at <= stored_at:
            return
        with self._lock:
            self._items[key] = (expires_at, page.model_copy(deep=True))
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


@dataclass(frozen=True)
class WeatherCacheKey:
    provider: str
    environment: ExecutionMode
    airport_code: str
    forecast_bucket: datetime
    units: str = "metric"
    language: str = "vi"


class WeatherCache:
    def __init__(self, max_entries: int = 256) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._items: OrderedDict[WeatherCacheKey, tuple[datetime, WeatherForecast]] = OrderedDict()
        self._lock = Lock()

    def get(self, key: WeatherCacheKey, *, now: datetime) -> WeatherForecast | None:
        checked_at = _utc(now)
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, forecast = item
            if checked_at >= expires_at:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return forecast.model_copy(deep=True)

    def put(self, key: WeatherCacheKey, forecast: WeatherForecast, *, now: datetime) -> None:
        stored_at = _utc(now)
        expires_at = forecast.metadata.expires_at
        if expires_at is None or expires_at <= stored_at:
            return
        with self._lock:
            self._items[key] = (expires_at, forecast.model_copy(deep=True))
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()


@dataclass(frozen=True)
class DestinationRecommendationCacheKey:
    provider: str
    environment: PlaceSourceEnvironment
    destination_airport: str
    locale: str
    interests: tuple[str, ...]
    catalog_version: str
    travel_date_bucket: tuple[date | None, date | None]
    budget_category: str | None
    pace: str | None
    maximum_places: int
    maximum_candidates: int


class DestinationRecommendationCache:
    """Thread-safe cache for safe candidate data only; stale entries are never returned."""

    def __init__(self, max_entries: int = 256) -> None:
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self.max_entries = max_entries
        self._items: OrderedDict[
            DestinationRecommendationCacheKey,
            tuple[datetime, tuple[PlaceCandidate, ...]],
        ] = OrderedDict()
        self._lock = Lock()

    def get(
        self,
        key: DestinationRecommendationCacheKey,
        *,
        now: datetime,
    ) -> tuple[PlaceCandidate, ...] | None:
        checked_at = _utc(now)
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, candidates = item
            if checked_at >= expires_at:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return tuple(candidate.model_copy(deep=True) for candidate in candidates)

    def put(
        self,
        key: DestinationRecommendationCacheKey,
        candidates: tuple[PlaceCandidate, ...],
        *,
        now: datetime,
        ttl: timedelta = timedelta(seconds=300),
    ) -> None:
        stored_at = _utc(now)
        if ttl <= timedelta(0):
            raise ValueError("recommendation cache TTL must be positive")
        expires_at = stored_at + ttl
        candidate_expiries = [
            candidate.expires_at for candidate in candidates if candidate.expires_at is not None
        ]
        if candidate_expiries:
            expires_at = min(expires_at, min(candidate_expiries))
        if expires_at <= stored_at:
            return
        safe_candidates = tuple(candidate.model_copy(deep=True) for candidate in candidates)
        with self._lock:
            self._items[key] = (expires_at, safe_candidates)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()
