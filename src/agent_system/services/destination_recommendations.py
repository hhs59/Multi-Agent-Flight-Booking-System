from __future__ import annotations

import asyncio
import hashlib
import logging
import re
import time
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Protocol

from pydantic import ValidationError

from agent_system.domain.recommendations import (
    BudgetCategory,
    DestinationRecommendationResult,
    DestinationRecommendationStatus,
    Pace,
    PlaceCandidate,
    PlaceRankingCandidate,
    PlaceRankingRequest,
    PlaceRankingResult,
    PlaceSearchRequest,
    PlaceSourceEnvironment,
    PlaceSuggestionRequest,
    PlaceSuggestionResult,
    RecommendationPreferences,
    RecommendedPlace,
)
from agent_system.llm_providers import LLMOutputError, LLMProvider, LLMUnavailableError
from agent_system.providers.cache import (
    DestinationRecommendationCache,
    DestinationRecommendationCacheKey,
)
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.contracts import PlacesProvider
from agent_system.providers.errors import (
    CapabilityUnavailable,
    CircuitOpenError,
    ProviderError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from agent_system.providers.places import CuratedPlacesProvider, place_environment
from agent_system.providers.resilience import ProviderExecutor
from agent_system.services.destination_catalog import CatalogDestination, CuratedDestinationCatalog

logger = logging.getLogger(__name__)

_AIRPORT_RE = re.compile(r"^[A-Z]{3}$")
_UNKNOWN_CITY = {"en": "Unknown destination", "vi": "Điểm đến chưa hỗ trợ"}
_UNKNOWN_COUNTRY = {"en": "Unknown country", "vi": "Quốc gia chưa hỗ trợ"}


@dataclass(frozen=True)
class DestinationRecommendationMetric:
    metric: str
    provider: str
    environment: PlaceSourceEnvironment
    outcome: str | None = None
    reason: str | None = None
    latency_seconds: float | None = None


class DestinationRecommendationMetricSink(Protocol):
    def record(self, metric: DestinationRecommendationMetric) -> None: ...


class LoggingDestinationRecommendationMetricSink:
    def record(self, metric: DestinationRecommendationMetric) -> None:
        logger.info("destination_recommendation_metric", extra={"metric": asdict(metric)})


class InMemoryDestinationRecommendationMetricSink:
    def __init__(self) -> None:
        self._metrics: list[DestinationRecommendationMetric] = []

    def record(self, metric: DestinationRecommendationMetric) -> None:
        self._metrics.append(metric)

    @property
    def metrics(self) -> tuple[DestinationRecommendationMetric, ...]:
        return tuple(self._metrics)


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("recommendation times must be timezone-aware")
    return value.astimezone(UTC)


def _safe_airport(value: str) -> str:
    normalized = value.strip().upper()
    return normalized if _AIRPORT_RE.fullmatch(normalized) else "XXX"


def _safe_locale(value: str | None) -> str:
    return "vi" if (value or "").strip().casefold() == "vi" else "en"


def _safe_trace(value: str | None) -> str:
    normalized = str(value).strip() if value is not None else ""
    return normalized[:160] or "destination-recommendation"


def _environment(value: object) -> PlaceSourceEnvironment:
    try:
        return place_environment(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return PlaceSourceEnvironment.MOCK


def _destination_labels(
    destination: CatalogDestination | None,
    *,
    locale: str,
) -> tuple[str, str]:
    if destination is None:
        return _UNKNOWN_CITY[locale], _UNKNOWN_COUNTRY[locale]
    return destination.city_label(locale), destination.country_label(locale)


def _notice(status: DestinationRecommendationStatus, locale: str) -> str:
    if locale == "vi":
        messages = {
            DestinationRecommendationStatus.COMPLETED: (
                "Gợi ý được tuyển chọn từ nguồn đã gắn nhãn; không phải dữ liệu trực tiếp. "
                "Hãy kiểm tra giờ mở cửa, giá vé và điều kiện hiện hành trước chuyến đi."
            ),
            DestinationRecommendationStatus.NO_RESULTS: (
                "Chưa có địa điểm phù hợp từ nguồn đã cấu hình cho điểm đến này."
            ),
            DestinationRecommendationStatus.UNSUPPORTED_DESTINATION: (
                "Điểm đến này chưa có nguồn gợi ý được cấu hình; không tự động thay bằng quốc gia khác."
            ),
            DestinationRecommendationStatus.PROVIDER_UNAVAILABLE: (
                "Nguồn địa điểm tạm thời không khả dụng. Chuyến bay hoặc bản nháp chính vẫn được giữ nguyên."
            ),
            DestinationRecommendationStatus.TIMED_OUT: (
                "Nguồn địa điểm không phản hồi trong thời hạn cho phép. Chuyến bay hoặc bản nháp chính vẫn được giữ nguyên."
            ),
            DestinationRecommendationStatus.DISABLED: (
                "Gợi ý điểm đến đang tắt theo cấu hình máy chủ."
            ),
        }
    else:
        messages = {
            DestinationRecommendationStatus.COMPLETED: (
                "Suggestions are source-labeled and curated/provider-supplied, not live travel advice. "
                "Check current opening hours, admission prices, and entry requirements before travel."
            ),
            DestinationRecommendationStatus.NO_RESULTS: (
                "No suitable places were returned by the configured source for this destination."
            ),
            DestinationRecommendationStatus.UNSUPPORTED_DESTINATION: (
                "This destination has no configured recommendation source; another country was not substituted."
            ),
            DestinationRecommendationStatus.PROVIDER_UNAVAILABLE: (
                "The places source is temporarily unavailable. The primary flight or draft result is unchanged."
            ),
            DestinationRecommendationStatus.TIMED_OUT: (
                "The places source exceeded its deadline. The primary flight or draft result is unchanged."
            ),
            DestinationRecommendationStatus.DISABLED: (
                "Destination recommendations are disabled by server configuration."
            ),
        }
    return messages[status]


def _interest_values(
    preferences: RecommendationPreferences | None,
    interests: Iterable[str],
) -> tuple[str, ...]:
    if preferences is not None:
        return preferences.interests
    return RecommendationPreferences(interests=tuple(interests)).interests


def _canonical_candidate_key(candidate: PlaceCandidate) -> str:
    """Return a stable comparison key covering every safe candidate field."""
    return candidate.model_dump_json()


def rank_place_candidates(
    candidates: Iterable[PlaceCandidate],
    *,
    destination_airport: str | None = None,
    preferences: RecommendationPreferences | None = None,
    interests: Iterable[str] = (),
    locale: str = "en",
    maximum_candidates: int = 20,
    now: datetime | None = None,
) -> tuple[PlaceCandidate, ...]:
    """Pure baseline ordering.

    Candidates are filtered by expiry, destination, validity, and stable ID. The score is
    (interest-category matches descending, localized-fact presence descending, source name
    ascending, place ID ascending). No popularity or unprovided quality claim is used.
    """
    if not 1 <= maximum_candidates <= 20:
        raise ValueError("maximum_candidates must be between one and twenty")
    checked_at = _utc(now or datetime.now(UTC))
    normalized_destination = (
        destination_airport.strip().upper() if destination_airport is not None else None
    )
    selected_interests = set(_interest_values(preferences, interests))
    requested_locale = locale.strip().casefold()
    by_id: dict[str, PlaceCandidate] = {}
    for raw in candidates:
        try:
            candidate = (
                raw if isinstance(raw, PlaceCandidate) else PlaceCandidate.model_validate(raw)
            )
        except (TypeError, ValueError, ValidationError):
            continue
        if (
            normalized_destination is not None
            and candidate.destination_airport != normalized_destination
        ):
            continue
        if candidate.expires_at is not None and _utc(candidate.expires_at) <= checked_at:
            continue
        existing = by_id.get(candidate.place_id)
        if existing is None:
            by_id[candidate.place_id] = candidate
            continue
        if _canonical_candidate_key(candidate) < _canonical_candidate_key(existing):
            by_id[candidate.place_id] = candidate
    valid = list(by_id.values())

    def score(candidate: PlaceCandidate) -> tuple[int, int, str, str]:
        matches = len(selected_interests.intersection(candidate.categories))
        localized = int(bool(candidate.short_facts) and requested_locale in {"vi", "en"})
        return (-matches, -localized, candidate.source_name.casefold(), candidate.place_id)

    return tuple(sorted(valid, key=score)[:maximum_candidates])


def _baseline_reason(candidate: PlaceCandidate, *, preferences: RecommendationPreferences) -> str:
    matches = tuple(
        category for category in candidate.categories if category in preferences.interests
    )
    if preferences.locale == "vi":
        if matches:
            return f"Phù hợp với sở thích: {', '.join(matches)}."
        if candidate.short_facts:
            return "Địa điểm có thông tin nguồn được tuyển chọn."
        return "Địa điểm được nguồn cấu hình cung cấp."
    if matches:
        return f"Matches your selected interests: {', '.join(matches)}."
    if candidate.short_facts:
        return "A source-backed place with localized facts."
    return "A place returned by the configured source."


class DestinationRecommendationService:
    def __init__(
        self,
        provider: PlacesProvider | None = None,
        *,
        catalog: CuratedDestinationCatalog | None = None,
        cache: DestinationRecommendationCache | None = None,
        executor: ProviderExecutor | None = None,
        llm: LLMProvider | None = None,
        clock: Clock | None = None,
        enabled: bool = True,
        llm_enabled: bool = False,
        llm_generation_enabled: bool = False,
        maximum_places: int = 5,
        maximum_candidates: int = 20,
        timeout_seconds: float = 2.0,
        llm_generation_timeout_seconds: float | None = None,
        cache_ttl_seconds: float = 300.0,
        curated_fallback_enabled: bool = False,
        metric_sink: DestinationRecommendationMetricSink | None = None,
    ) -> None:
        if isinstance(provider, CuratedDestinationCatalog):
            catalog, provider = provider, None
        if not 1 <= maximum_places <= maximum_candidates <= 20:
            raise ValueError("recommendation limits must satisfy 1 <= places <= candidates <= 20")
        if timeout_seconds <= 0:
            raise ValueError("recommendation timeout must be positive")
        if llm_generation_timeout_seconds is not None and llm_generation_timeout_seconds <= 0:
            raise ValueError("LLM generation timeout must be positive")
        if cache_ttl_seconds <= 0:
            raise ValueError("recommendation cache TTL must be positive")
        self.catalog = catalog or CuratedDestinationCatalog.from_package_data()
        self.provider = provider or CuratedPlacesProvider(
            catalog=self.catalog,
            clock=clock,
            cache_ttl_seconds=cache_ttl_seconds,
        )
        self.cache = cache or DestinationRecommendationCache()
        self.executor = executor
        self.llm = llm
        self.clock = clock or SystemClock()
        self.enabled = enabled
        self.llm_generation_enabled = llm_generation_enabled
        self.llm_enabled = llm_enabled
        self.maximum_places = maximum_places
        self.maximum_candidates = maximum_candidates
        self.llm_generation_timeout_seconds = llm_generation_timeout_seconds or timeout_seconds
        self.timeout_seconds = timeout_seconds
        self.cache_ttl = timedelta(seconds=cache_ttl_seconds)
        self.curated_fallback_enabled = curated_fallback_enabled
        self.metric_sink = metric_sink or LoggingDestinationRecommendationMetricSink()
        self.provider_name = str(getattr(self.provider, "name", "places"))
        self.provider_environment = _environment(
            getattr(self.provider, "environment", PlaceSourceEnvironment.MOCK)
        )

    def _record(
        self,
        *,
        metric: str,
        outcome: str | None = None,
        reason: str | None = None,
        latency_seconds: float | None = None,
    ) -> None:
        self.metric_sink.record(
            DestinationRecommendationMetric(
                metric=metric,
                provider=self.provider_name,
                environment=self.provider_environment,
                outcome=outcome,
                reason=reason,
                latency_seconds=latency_seconds,
            )
        )

    def _result(
        self,
        *,
        status: DestinationRecommendationStatus,
        airport: str,
        destination: CatalogDestination | None,
        locale: str,
        trace_id: str,
        places: tuple[RecommendedPlace, ...] = (),
        source_labels: tuple[str, ...] = (),
        retrieved_at: datetime | None = None,
        retryable: bool = False,
    ) -> DestinationRecommendationResult:
        city, country = _destination_labels(destination, locale=locale)
        source = source_labels[0] if source_labels else None
        return DestinationRecommendationResult(
            status=status,
            destination_airport=airport,
            city=city,
            country=country,
            places=places,
            source_labels=source_labels,
            retrieved_at=retrieved_at,
            advisory_notice=_notice(status, locale),
            trace_id=_safe_trace(trace_id),
            retryable=retryable,
            source=source,
        )

    def _cache_key(
        self,
        *,
        prefs: RecommendationPreferences,
        airport: str,
    ) -> DestinationRecommendationCacheKey:
        return DestinationRecommendationCacheKey(
            provider=self.provider_name,
            environment=self.provider_environment,
            destination_airport=airport,
            locale=prefs.locale,
            interests=tuple(sorted(prefs.interests)),
            catalog_version=self.catalog.catalog_version,
            travel_date_bucket=(prefs.travel_start_date, prefs.travel_end_date),
            budget_category=(
                prefs.budget_category.value if prefs.budget_category is not None else None
            ),
            pace=prefs.pace.value if prefs.pace is not None else None,
            maximum_places=prefs.maximum_places,
            maximum_candidates=self.maximum_candidates,
        )

    async def _call_provider(self, request) -> tuple[PlaceCandidate, ...]:
        async def call() -> tuple[PlaceCandidate, ...]:
            return await self.provider.search(request)

        if self.executor is None:
            return await call()
        return await self.executor.execute(
            provider=self.provider_name,
            operation="places_search",
            call=call,
            retry_safe=True,
        )

    async def _call_llm(
        self,
        *,
        destination: CatalogDestination,
        preferences: RecommendationPreferences,
        candidates: tuple[PlaceCandidate, ...],
        deadline_monotonic: float,
    ) -> PlaceRankingResult:
        ranker = getattr(self.llm, "rank_places", None)
        if ranker is None:
            raise LLMOutputError("place ranking is not implemented by the configured LLM")
        remaining = deadline_monotonic - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("place ranking deadline expired")
        request = PlaceRankingRequest(
            destination_label=destination.city_label(preferences.locale),
            travel_start_date=preferences.travel_start_date,
            travel_end_date=preferences.travel_end_date,
            locale=preferences.locale,
            interests=preferences.interests,
            budget_category=preferences.budget_category,
            pace=preferences.pace,
            candidates=tuple(
                PlaceRankingCandidate(
                    place_id=candidate.place_id,
                    name=candidate.name,
                    categories=candidate.categories,
                    short_facts=candidate.short_facts[:3],
                )
                for candidate in candidates
            ),
        )
        async with asyncio.timeout(remaining):
            return await ranker(request)

    def _join_llm_order(
        self,
        result: PlaceRankingResult,
        *,
        deterministic: tuple[PlaceCandidate, ...],
        preferences: RecommendationPreferences,
    ) -> tuple[RecommendedPlace, ...]:
        by_id = {candidate.place_id: candidate for candidate in deterministic}
        if len(result.selections) > len(deterministic):
            raise LLMOutputError("place ranking returned excessive selections")
        selected: list[RecommendedPlace] = []
        seen: set[str] = set()
        for selection in result.selections:
            if selection.place_id not in by_id or selection.place_id in seen:
                raise LLMOutputError("place ranking returned an unknown or duplicate ID")
            seen.add(selection.place_id)
            selected.append(
                RecommendedPlace(
                    candidate=by_id[selection.place_id],
                    rank=len(selected) + 1,
                    reason=selection.reason,
                )
            )
        for candidate in deterministic:
            if candidate.place_id in seen:
                continue
            selected.append(
                RecommendedPlace(
                    candidate=candidate,
                    rank=len(selected) + 1,
                    reason=_baseline_reason(candidate, preferences=preferences),
                )
            )
        return tuple(selected[: preferences.maximum_places])

    def _deterministic_result(
        self,
        candidates: tuple[PlaceCandidate, ...],
        *,
        preferences: RecommendationPreferences,
    ) -> tuple[RecommendedPlace, ...]:
        return tuple(
            RecommendedPlace(
                candidate=candidate,
                rank=index,
                reason=_baseline_reason(candidate, preferences=preferences),
            )
            for index, candidate in enumerate(candidates[: preferences.maximum_places], start=1)
        )

    async def _rank_candidates(
        self,
        candidates: tuple[PlaceCandidate, ...],
        *,
        destination: CatalogDestination,
        preferences: RecommendationPreferences,
        deadline_monotonic: float,
    ) -> tuple[RecommendedPlace, ...]:
        if self.llm_enabled and self.llm is not None:
            try:
                llm_result = await self._call_llm(
                    destination=destination,
                    preferences=preferences,
                    candidates=candidates,
                    deadline_monotonic=deadline_monotonic,
                )
                return self._join_llm_order(
                    llm_result,
                    deterministic=candidates,
                    preferences=preferences,
                )
            except (TimeoutError, LLMOutputError, LLMUnavailableError, ValidationError, ValueError):
                self._record(
                    metric="destination_recommendation_llm_fallbacks_total",
                    reason="invalid_or_unavailable",
                )
            except Exception:
                self._record(
                    metric="destination_recommendation_llm_fallbacks_total",
                    reason="error",
                )
        return self._deterministic_result(candidates, preferences=preferences)

    async def _generate_ai_result(
        self,
        *,
        airport: str,
        destination: CatalogDestination | None,
        preferences: RecommendationPreferences,
        trace_id: str,
    ) -> DestinationRecommendationResult | None:
        generator = getattr(self.llm, "suggest_places", None)
        if not self.llm_generation_enabled or generator is None:
            return None
        destination_label = (
            f"{destination.city_label(preferences.locale)}, "
            f"{destination.country_label(preferences.locale)} ({airport})"
            if destination is not None
            else airport
        )
        try:
            async with asyncio.timeout(self.llm_generation_timeout_seconds):
                generated = await generator(
                    PlaceSuggestionRequest(
                        destination_airport=airport,
                        destination_label=destination_label,
                        travel_start_date=preferences.travel_start_date,
                        travel_end_date=preferences.travel_end_date,
                        locale=preferences.locale,
                        interests=preferences.interests,
                        budget_category=preferences.budget_category,
                        pace=preferences.pace,
                        maximum_places=min(preferences.maximum_places, 10),
                    )
                )
            if not isinstance(generated, PlaceSuggestionResult):
                generated = PlaceSuggestionResult.model_validate(generated)
            if not generated.suggestions:
                self._record(
                    metric="destination_recommendation_llm_generation_total",
                    outcome="no_results",
                )
                return None
        except (
            TimeoutError,
            LLMOutputError,
            LLMUnavailableError,
            ValidationError,
            TypeError,
            ValueError,
        ):
            self._record(
                metric="destination_recommendation_llm_generation_total",
                outcome="invalid_or_unavailable",
            )
            return None
        except Exception:
            self._record(
                metric="destination_recommendation_llm_generation_total",
                outcome="error",
            )
            return None

        retrieved_at = _utc(self.clock.now())
        expires_at = retrieved_at + self.cache_ttl
        provider_label = str(getattr(self.llm, "name", "llm")).strip() or "llm"
        model_label = str(getattr(self.llm, "model", "model")).strip() or "model"
        source_label = f"AI-generated · {provider_label}/{model_label}"[:80]
        places = tuple(
            RecommendedPlace(
                candidate=PlaceCandidate(
                    place_id=(
                        f"ai-{airport.casefold()}-"
                        f"{hashlib.sha256(f'{airport}:{suggestion.name.casefold()}'.encode()).hexdigest()[:20]}"
                    ),
                    destination_airport=airport,
                    city_code=airport,
                    country_code=generated.country_code,
                    name=suggestion.name,
                    categories=suggestion.categories,
                    short_facts=(),
                    source_name=source_label,
                    source_url=None,
                    environment=PlaceSourceEnvironment.AI_GENERATED,
                    is_live=False,
                    retrieved_at=retrieved_at,
                    expires_at=expires_at,
                ),
                rank=index,
                reason=suggestion.reason,
            )
            for index, suggestion in enumerate(
                generated.suggestions[: preferences.maximum_places],
                start=1,
            )
        )
        advisory_notice = (
            "Các địa điểm này do AI tạo từ điểm đến và sở thích của bạn. "
            "Đây không phải dữ liệu trực tiếp hoặc đã xác minh; hãy kiểm tra tên, vị trí "
            "và thông tin hiện hành trước khi đi."
            if preferences.locale == "vi"
            else (
                "These places were generated by AI from your destination and preferences. "
                "They are not live or verified place data; check each name, location, and "
                "current details before visiting."
            )
        )
        self._record(
            metric="destination_recommendation_llm_generation_total",
            outcome="success",
        )
        return DestinationRecommendationResult(
            status=DestinationRecommendationStatus.COMPLETED,
            destination_airport=airport,
            city=generated.city,
            country=generated.country,
            places=places,
            source_labels=(source_label,),
            retrieved_at=retrieved_at,
            advisory_notice=advisory_notice,
            trace_id=trace_id,
            retryable=False,
            source=source_label,
        )

    async def recommend(
        self,
        destination_airport: str,
        *,
        locale: str | None = None,
        language: str | None = None,
        travel_start_date: date | None = None,
        travel_end_date: date | None = None,
        interests: Iterable[str] = (),
        budget_category: BudgetCategory | str | None = None,
        pace: Pace | str | None = None,
        maximum_places: int | None = None,
        trace_id: str | None = None,
    ) -> DestinationRecommendationResult:
        started = time.monotonic()
        normalized_airport = _safe_airport(destination_airport)
        selected_locale = _safe_locale(locale or language)
        trace = _safe_trace(trace_id)
        destination = self.catalog.resolve(normalized_airport)
        if not self.enabled:
            result = self._result(
                status=DestinationRecommendationStatus.DISABLED,
                airport=normalized_airport,
                destination=destination,
                locale=selected_locale,
                trace_id=trace,
            )
            self._record(
                metric="destination_recommendation_requests_total", outcome=result.status.value
            )
            self._record(
                metric="destination_recommendation_latency_seconds",
                latency_seconds=time.monotonic() - started,
            )
            return result

        if destination is None and not self.llm_generation_enabled:
            result = self._result(
                status=DestinationRecommendationStatus.UNSUPPORTED_DESTINATION,
                airport=normalized_airport,
                destination=None,
                locale=selected_locale,
                trace_id=trace,
            )
            self._record(
                metric="destination_recommendation_requests_total",
                outcome=result.status.value,
            )
            self._record(
                metric="destination_recommendation_latency_seconds",
                latency_seconds=time.monotonic() - started,
            )
            return result

        try:
            prefs = RecommendationPreferences(
                locale=selected_locale,
                travel_start_date=travel_start_date,
                travel_end_date=travel_end_date,
                interests=tuple(interests),
                budget_category=budget_category,
                pace=pace,
                maximum_places=maximum_places or self.maximum_places,
            )
        except (TypeError, ValueError, ValidationError):
            result = self._result(
                status=DestinationRecommendationStatus.NO_RESULTS,
                airport=normalized_airport,
                destination=destination,
                locale=selected_locale,
                trace_id=trace,
            )
            self._record(
                metric="destination_recommendation_requests_total", outcome=result.status.value
            )
            self._record(
                metric="destination_recommendation_latency_seconds",
                latency_seconds=time.monotonic() - started,
            )
            return result
        if destination is None:
            generated_result = await self._generate_ai_result(
                airport=normalized_airport,
                destination=None,
                preferences=prefs,
                trace_id=trace,
            )
            fallback_status = (
                DestinationRecommendationStatus.PROVIDER_UNAVAILABLE
                if self.llm_generation_enabled
                else DestinationRecommendationStatus.UNSUPPORTED_DESTINATION
            )
            result = generated_result or self._result(
                status=fallback_status,
                airport=normalized_airport,
                destination=None,
                locale=selected_locale,
                trace_id=trace,
                retryable=self.llm_generation_enabled,
            )
            self._record(
                metric="destination_recommendation_requests_total",
                outcome=result.status.value,
            )
            self._record(
                metric="destination_recommendation_latency_seconds",
                latency_seconds=time.monotonic() - started,
            )
            return result

        key = self._cache_key(prefs=prefs, airport=normalized_airport)
        now = _utc(self.clock.now())
        cached = self.cache.get(key, now=now)
        if cached is not None:
            self._record(metric="destination_recommendation_cache_total", outcome="hit")
            candidates = rank_place_candidates(
                cached,
                destination_airport=normalized_airport,
                preferences=prefs,
                locale=selected_locale,
                maximum_candidates=self.maximum_candidates,
                now=now,
            )
            generated_result = None
            if not candidates:
                places: tuple[RecommendedPlace, ...] = ()
            else:
                places = await self._rank_candidates(
                    candidates,
                    destination=destination,
                    preferences=prefs,
                    deadline_monotonic=started + self.timeout_seconds,
                )
            if not places:
                generated_result = await self._generate_ai_result(
                    airport=normalized_airport,
                    destination=destination,
                    preferences=prefs,
                    trace_id=trace,
                )
            result = generated_result or self._result(
                status=(
                    DestinationRecommendationStatus.COMPLETED
                    if places
                    else DestinationRecommendationStatus.NO_RESULTS
                ),
                airport=normalized_airport,
                destination=destination,
                locale=selected_locale,
                trace_id=trace,
                places=places,
                source_labels=tuple(dict.fromkeys(item.candidate.source_name for item in places)),
                retrieved_at=min((item.candidate.retrieved_at for item in places), default=None),
            )
            self._record(
                metric="destination_recommendation_requests_total", outcome=result.status.value
            )
            self._record(
                metric="destination_recommendation_latency_seconds",
                latency_seconds=time.monotonic() - started,
            )
            return result
        self._record(metric="destination_recommendation_cache_total", outcome="miss")

        deadline_monotonic = time.monotonic() + self.timeout_seconds
        deadline = now + timedelta(seconds=self.timeout_seconds)
        request = None
        try:
            request = PlaceSearchRequest(
                destination_airport=normalized_airport,
                city_code=destination.city_code,
                country_code=destination.country_code,
                travel_start_date=prefs.travel_start_date,
                travel_end_date=prefs.travel_end_date,
                locale=prefs.locale,
                interests=prefs.interests,
                limit=self.maximum_candidates,
                deadline=deadline,
            )
            async with asyncio.timeout(self.timeout_seconds):
                raw_candidates = await self._call_provider(request)
            if not isinstance(raw_candidates, (tuple, list)):
                raise ProviderMalformedResponseError(
                    provider=self.provider_name,
                    operation="search",
                    safe_message="places provider returned an invalid candidate collection",
                )
        except (TimeoutError, ProviderTimeoutError):
            raw_candidates = None
            failure = DestinationRecommendationStatus.TIMED_OUT
        except (
            CapabilityUnavailable,
            CircuitOpenError,
            ProviderMalformedResponseError,
            ProviderRateLimitError,
            ProviderUnavailableError,
            ProviderError,
        ):
            raw_candidates = None
            failure = DestinationRecommendationStatus.PROVIDER_UNAVAILABLE
        except Exception:
            logger.exception(
                "destination recommendation provider failure", extra={"trace_id": trace}
            )
            raw_candidates = None
            failure = DestinationRecommendationStatus.PROVIDER_UNAVAILABLE

        if raw_candidates is None:
            if self.curated_fallback_enabled:
                fallback_provider = CuratedPlacesProvider(
                    catalog=self.catalog,
                    clock=self.clock,
                    cache_ttl_seconds=self.cache_ttl.total_seconds(),
                )
                try:
                    raw_candidates = await fallback_provider.search(request)
                    self._record(
                        metric="destination_recommendation_cache_total",
                        outcome="curated_fallback",
                    )
                except Exception:
                    raw_candidates = None
            if raw_candidates is None:
                result = self._result(
                    status=failure,
                    airport=normalized_airport,
                    destination=destination,
                    locale=selected_locale,
                    trace_id=trace,
                    retryable=failure
                    in {
                        DestinationRecommendationStatus.TIMED_OUT,
                        DestinationRecommendationStatus.PROVIDER_UNAVAILABLE,
                    },
                )
                self._record(
                    metric="destination_recommendation_requests_total", outcome=result.status.value
                )
                self._record(
                    metric="destination_recommendation_latency_seconds",
                    latency_seconds=time.monotonic() - started,
                )
                return result

        candidates = rank_place_candidates(
            raw_candidates,
            destination_airport=normalized_airport,
            preferences=prefs,
            locale=selected_locale,
            maximum_candidates=self.maximum_candidates,
            now=_utc(self.clock.now()),
        )
        if not candidates:
            generated_result = await self._generate_ai_result(
                airport=normalized_airport,
                destination=destination,
                preferences=prefs,
                trace_id=trace,
            )
            result = generated_result or self._result(
                status=DestinationRecommendationStatus.NO_RESULTS,
                airport=normalized_airport,
                destination=destination,
                locale=selected_locale,
                trace_id=trace,
            )
            self.cache.put(key, (), now=now, ttl=self.cache_ttl)
            self._record(
                metric="destination_recommendation_requests_total", outcome=result.status.value
            )
            self._record(
                metric="destination_recommendation_latency_seconds",
                latency_seconds=time.monotonic() - started,
            )
            return result
        self.cache.put(key, candidates, now=now, ttl=self.cache_ttl)

        places = await self._rank_candidates(
            candidates,
            destination=destination,
            preferences=prefs,
            deadline_monotonic=deadline_monotonic,
        )

        result = self._result(
            status=(
                DestinationRecommendationStatus.COMPLETED
                if places
                else DestinationRecommendationStatus.NO_RESULTS
            ),
            airport=normalized_airport,
            destination=destination,
            locale=selected_locale,
            trace_id=trace,
            places=places,
            source_labels=tuple(dict.fromkeys(item.candidate.source_name for item in places)),
            retrieved_at=min((item.candidate.retrieved_at for item in places), default=None),
        )
        self._record(
            metric="destination_recommendation_requests_total", outcome=result.status.value
        )
        self._record(
            metric="destination_recommendation_latency_seconds",
            latency_seconds=time.monotonic() - started,
        )
        return result


__all__ = [
    "DestinationRecommendationMetric",
    "DestinationRecommendationMetricSink",
    "DestinationRecommendationService",
    "InMemoryDestinationRecommendationMetricSink",
    "LoggingDestinationRecommendationMetricSink",
    "rank_place_candidates",
]
