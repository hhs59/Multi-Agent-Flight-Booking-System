from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from threading import Lock
from typing import Protocol

from agent_system.domain.location_resolution import LocationLookupRequest, LocationSuggestion
from agent_system.domain.values import ExecutionMode
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.contracts import LocationProvider
from agent_system.providers.errors import ProviderError, ProviderMalformedResponseError
from agent_system.providers.localization import normalize_vietnamese_alias
from agent_system.providers.resilience import ProviderExecutor


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("location cache timestamps must be timezone-aware")
    return value.astimezone(UTC)


@dataclass(frozen=True)
class LocationCacheKey:
    provider: str
    environment: ExecutionMode
    query: str
    locale: str


class LocationResolutionCache:
    """Bounded cache containing typed place suggestions only."""

    def __init__(self, max_entries: int = 512) -> None:
        if not 1 <= max_entries <= 10_000:
            raise ValueError("location cache max entries must be between 1 and 10000")
        self.max_entries = max_entries
        self._items: OrderedDict[
            LocationCacheKey,
            tuple[datetime, tuple[LocationSuggestion, ...]],
        ] = OrderedDict()
        self._lock = Lock()

    def get(
        self,
        key: LocationCacheKey,
        *,
        now: datetime,
    ) -> tuple[LocationSuggestion, ...] | None:
        checked_at = _utc(now)
        with self._lock:
            item = self._items.get(key)
            if item is None:
                return None
            expires_at, suggestions = item
            if checked_at >= expires_at:
                del self._items[key]
                return None
            self._items.move_to_end(key)
            return tuple(suggestion.model_copy(deep=True) for suggestion in suggestions)

    def put(
        self,
        key: LocationCacheKey,
        suggestions: tuple[LocationSuggestion, ...],
        *,
        now: datetime,
        ttl: timedelta,
    ) -> None:
        stored_at = _utc(now)
        if ttl <= timedelta(0):
            raise ValueError("location cache TTL must be positive")
        expires_at = stored_at + ttl
        safe_suggestions = tuple(suggestion.model_copy(deep=True) for suggestion in suggestions)
        with self._lock:
            self._items[key] = (expires_at, safe_suggestions)
            self._items.move_to_end(key)
            while len(self._items) > self.max_entries:
                self._items.popitem(last=False)

    def clear(self) -> None:
        with self._lock:
            self._items.clear()

    @property
    def size(self) -> int:
        with self._lock:
            return len(self._items)


@dataclass(frozen=True)
class LocationLookupMetric:
    metric: str
    provider: str
    environment: ExecutionMode
    outcome: str
    result_count: int | None = None
    latency_seconds: float | None = None


class LocationMetricSink(Protocol):
    def record(self, metric: LocationLookupMetric) -> None: ...


class LoggingLocationMetricSink:
    def __init__(self, logger=None) -> None:
        import logging

        self.logger = logger or logging.getLogger("agent_system.locations")

    def record(self, metric: LocationLookupMetric) -> None:
        self.logger.info("location_lookup_metric", extra={"location_metric": metric.__dict__})


class InMemoryLocationMetricSink:
    def __init__(self) -> None:
        self._metrics: list[LocationLookupMetric] = []
        self._lock = Lock()

    def record(self, metric: LocationLookupMetric) -> None:
        with self._lock:
            self._metrics.append(metric)

    @property
    def metrics(self) -> tuple[LocationLookupMetric, ...]:
        with self._lock:
            return tuple(self._metrics)


@dataclass(frozen=True)
class LocationResolutionResult:
    provider: str
    environment: ExecutionMode
    suggestions: tuple[LocationSuggestion, ...]


class LocationResolutionService:
    def __init__(
        self,
        provider: LocationProvider,
        executor: ProviderExecutor,
        *,
        catalog_provider: LocationProvider | None = None,
        cache: LocationResolutionCache | None = None,
        result_limit: int = 8,
        cache_ttl_seconds: float = 3600.0,
        clock: Clock | None = None,
        metric_sink: LocationMetricSink | None = None,
    ) -> None:
        if not 1 <= result_limit <= 10:
            raise ValueError("location result limit must be between 1 and 10")
        if not 1 <= cache_ttl_seconds <= 86_400:
            raise ValueError("location cache TTL must be between 1 and 86400 seconds")
        if provider.environment is not executor.environment:
            raise ValueError("location provider and executor environments must match")
        if (
            catalog_provider is not None
            and catalog_provider.environment is not executor.environment
        ):
            raise ValueError("catalog location provider and executor environments must match")
        self.provider = provider
        self.executor = executor
        self.catalog_provider = (
            catalog_provider
            if catalog_provider is not None and catalog_provider is not provider
            else None
        )
        self.cache = cache or LocationResolutionCache()
        self.result_limit = result_limit
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self.clock = clock or SystemClock()
        self.metric_sink = metric_sink or LoggingLocationMetricSink()
        self._inflight: dict[LocationCacheKey, asyncio.Task[tuple[LocationSuggestion, ...]]] = {}
        self._inflight_lock = asyncio.Lock()

    @staticmethod
    def _cache_query(query: str) -> str:
        return normalize_vietnamese_alias(" ".join(query.strip().split()))

    def _record(
        self,
        metric: str,
        provider: LocationProvider,
        outcome: str,
        *,
        result_count: int | None = None,
        latency_seconds: float | None = None,
    ) -> None:
        self.metric_sink.record(
            LocationLookupMetric(
                metric=metric,
                provider=provider.name[:80].lower(),
                environment=provider.environment,
                outcome=outcome,
                result_count=result_count,
                latency_seconds=latency_seconds,
            )
        )

    async def _fetch(
        self,
        provider: LocationProvider,
        request: LocationLookupRequest,
        key: LocationCacheKey,
        *,
        correlation_id: str | None,
    ) -> tuple[LocationSuggestion, ...]:
        started = time.monotonic()
        try:
            raw = await self.executor.execute(
                provider=provider.name,
                operation="location_suggestions",
                call=lambda: provider.suggest(request, correlation_id=correlation_id),
                retry_safe=True,
            )
            if not isinstance(raw, tuple):
                raise ProviderMalformedResponseError(
                    provider=provider.name,
                    operation="location_suggestions",
                    safe_message="location provider returned an invalid result",
                )
            suggestions = tuple(
                LocationSuggestion.model_validate(item) for item in raw[: self.result_limit]
            )
            outcome = "success" if suggestions else "empty"
            self.cache.put(
                key,
                suggestions,
                now=self.clock.now(),
                ttl=min(self.cache_ttl, timedelta(seconds=60))
                if not suggestions
                else self.cache_ttl,
            )
            self._record(
                "location_lookup_requests_total",
                provider,
                outcome,
                result_count=len(suggestions),
            )
            self._record(
                "location_lookup_result_count",
                provider,
                outcome,
                result_count=len(suggestions),
            )
            return suggestions
        except ProviderError:
            self._record(
                "location_lookup_requests_total",
                provider,
                "provider_error",
                result_count=0,
            )
            raise
        except (TypeError, ValueError) as exc:
            self._record(
                "location_lookup_requests_total",
                provider,
                "malformed",
                result_count=0,
            )
            raise ProviderMalformedResponseError(
                provider=provider.name,
                operation="location_suggestions",
                safe_message="location provider returned an invalid result",
            ) from exc
        finally:
            self._record(
                "location_lookup_latency_seconds",
                provider,
                "completed",
                latency_seconds=time.monotonic() - started,
            )

    async def _lookup_provider(
        self,
        provider: LocationProvider,
        request: LocationLookupRequest,
        *,
        correlation_id: str | None,
    ) -> tuple[LocationSuggestion, ...]:
        key = LocationCacheKey(
            provider=provider.name[:80].lower(),
            environment=provider.environment,
            query=self._cache_query(request.query),
            locale=request.locale,
        )
        cached = self.cache.get(key, now=self.clock.now())
        if cached is not None:
            self._record("location_lookup_cache_total", provider, "hit", result_count=len(cached))
            return cached[: request.limit]
        self._record("location_lookup_cache_total", provider, "miss")
        internal_request = request.model_copy(update={"limit": self.result_limit})
        async with self._inflight_lock:
            task = self._inflight.get(key)
            if task is None:
                task = asyncio.create_task(
                    self._fetch(
                        provider,
                        internal_request,
                        key,
                        correlation_id=correlation_id,
                    )
                )
                self._inflight[key] = task
        try:
            return (await asyncio.shield(task))[: request.limit]
        finally:
            if task.done():
                async with self._inflight_lock:
                    if self._inflight.get(key) is task:
                        del self._inflight[key]

    async def resolve(
        self,
        request: LocationLookupRequest,
        *,
        correlation_id: str | None = None,
    ) -> LocationResolutionResult:
        providers = tuple(
            provider for provider in (self.catalog_provider, self.provider) if provider is not None
        )
        seen: set[tuple[str, ExecutionMode]] = set()
        for provider in providers:
            identity = (provider.name, provider.environment)
            if identity in seen:
                continue
            seen.add(identity)
            suggestions = await self._lookup_provider(
                provider,
                request,
                correlation_id=correlation_id,
            )
            if suggestions:
                return LocationResolutionResult(
                    provider=provider.name,
                    environment=provider.environment,
                    suggestions=suggestions,
                )
        fallback = self.provider
        return LocationResolutionResult(
            provider=fallback.name,
            environment=fallback.environment,
            suggestions=(),
        )

    async def suggest(
        self,
        request: LocationLookupRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[LocationSuggestion, ...]:
        return (await self.resolve(request, correlation_id=correlation_id)).suggestions


__all__ = [
    "InMemoryLocationMetricSink",
    "LocationCacheKey",
    "LocationLookupMetric",
    "LocationMetricSink",
    "LocationResolutionCache",
    "LocationResolutionResult",
    "LocationResolutionService",
]
