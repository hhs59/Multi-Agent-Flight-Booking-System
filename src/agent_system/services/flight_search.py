from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime

from agent_system.domain.flights import (
    FlightOffer,
    FlightSearchCriteria,
    RepriceResult,
    SearchResultPage,
)
from agent_system.domain.limits import MAX_PROVIDER_OFFERS_PER_ATTEMPT
from agent_system.providers.cache import SearchCache, SearchCacheKey
from agent_system.providers.clock import Clock
from agent_system.providers.contracts import FlightProvider
from agent_system.providers.errors import ProviderValidationError
from agent_system.providers.resilience import ProviderExecutor

logger = logging.getLogger(__name__)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _offer_fingerprint(offer: FlightOffer) -> str:
    encoded = json.dumps(
        offer.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _bound_provider_page(page: SearchResultPage, *, now: datetime) -> SearchResultPage:
    retained: list[FlightOffer] = []
    seen: set[str] = set()
    dropped = 0
    for offer in page.offers:
        if offer.is_expired(now):
            dropped += 1
            continue
        fingerprint = _offer_fingerprint(offer)
        if fingerprint in seen:
            dropped += 1
            continue
        seen.add(fingerprint)
        if len(retained) >= MAX_PROVIDER_OFFERS_PER_ATTEMPT:
            dropped += 1
            continue
        retained.append(offer)
    if dropped:
        logger.info(
            "flight_search_truncation_metric",
            extra={
                "metric_name": "flight_search_truncations_total",
                "boundary": "provider_attempt",
                "provider": page.metadata.provider,
                "environment": page.metadata.environment.value,
            },
        )
    return page.model_copy(update={"offers": tuple(retained)})


class FlightSearchService:
    def __init__(
        self,
        provider: FlightProvider,
        cache: SearchCache,
        executor: ProviderExecutor,
        clock: Clock,
    ) -> None:
        if executor.environment is not provider.environment:
            raise ValueError("provider and executor environments must match")
        self.provider = provider
        self.cache = cache
        self.executor = executor
        self.clock = clock

    async def search(
        self,
        criteria: FlightSearchCriteria,
        *,
        correlation_id: str | None = None,
    ) -> SearchResultPage:
        key = SearchCacheKey.from_criteria(
            self.provider.name,
            self.provider.environment,
            criteria,
        )
        now = self.clock.now()
        cached = self.cache.get(key, now=now)
        if cached is not None:
            return _bound_provider_page(cached, now=now)
        page = await self.executor.execute(
            provider=self.provider.name,
            operation="search",
            call=lambda: self.provider.search(
                criteria,
                correlation_id=correlation_id,
            ),
            retry_safe=True,
        )
        page = _bound_provider_page(page, now=self.clock.now())
        self.cache.put(key, page, now=self.clock.now())
        return page

    async def reprice(
        self,
        provider_offer_id: str,
        expected: FlightOffer,
        *,
        correlation_id: str | None = None,
    ) -> RepriceResult:
        if (
            expected.metadata.provider != self.provider.name
            or expected.metadata.environment is not self.provider.environment
        ):
            raise ProviderValidationError(
                provider=self.provider.name,
                operation="reprice",
                safe_message="offer provenance does not match selected provider",
            )
        return await self.executor.execute(
            provider=self.provider.name,
            operation="reprice",
            call=lambda: self.provider.reprice(
                provider_offer_id,
                expected,
                correlation_id=correlation_id,
            ),
            retry_safe=True,
        )
