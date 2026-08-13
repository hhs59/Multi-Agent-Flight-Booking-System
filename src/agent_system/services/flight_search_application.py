from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import (
    FlightDiscoveryRecord,
    FlightOfferRecord,
    FlightSearchAttemptRecord,
    FlightSearchRecord,
)
from agent_system.domain.flights import (
    FlightOffer,
    FlightSearchCriteria,
    RepriceStatus,
    SearchResultPage,
)
from agent_system.domain.limits import (
    MAX_AGGREGATE_OFFERS,
    MAX_PROVIDER_OFFERS_PER_ATTEMPT,
)
from agent_system.domain.trip_discovery import (
    ExecutableFlightSearch,
    FlightSearchAttempt,
    SearchAttemptOutcome,
    TripDiscoveryRepriceResult,
    TripDiscoverySearchResult,
    TripDiscoveryStatus,
)
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.errors import (
    CircuitOpenError,
    ProviderError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from agent_system.repositories.base import ConcurrencyConflictError, ResourceNotFoundError
from agent_system.services.flight_ranking import safe_offer_from_flight
from agent_system.services.flight_search import FlightSearchService

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DiscoverySearchSettings:
    max_days: int = 7
    max_calls: int = 14
    concurrency: int = 3
    timeout_seconds: float = 20.0

    def __post_init__(self) -> None:
        if not 1 <= self.max_days <= 7:
            raise ValueError("DISCOVERY_MAX_DAYS must be between 1 and 7")
        if not 1 <= self.max_calls <= 20:
            raise ValueError("DISCOVERY_MAX_CALLS must be between 1 and 20")
        if not 1 <= self.concurrency <= 5:
            raise ValueError("DISCOVERY_CONCURRENCY must be between 1 and 5")
        if not math.isfinite(self.timeout_seconds) or not 0 < self.timeout_seconds <= 60:
            raise ValueError("DISCOVERY_TIMEOUT_SECONDS must be greater than zero and at most 60")

    @classmethod
    def from_environment(cls) -> DiscoverySearchSettings:
        return cls(
            max_days=int(os.getenv("DISCOVERY_MAX_DAYS", "7")),
            max_calls=int(os.getenv("DISCOVERY_MAX_CALLS", "14")),
            concurrency=int(os.getenv("DISCOVERY_CONCURRENCY", "3")),
            timeout_seconds=float(os.getenv("DISCOVERY_TIMEOUT_SECONDS", "20")),
        )


class DiscoveryBudgetExceeded(ValueError):  # noqa: N818
    code = "discovery_budget_exceeded"

    def __init__(
        self,
        requested_calls: int,
        max_calls: int,
        *,
        reason: str = "search_budget_exceeded",
        requested_days: int | None = None,
        max_days: int | None = None,
    ) -> None:
        self.requested_calls = requested_calls
        self.max_calls = max_calls
        self.reason = reason
        self.requested_days = requested_days
        self.max_days = max_days
        if reason == "date_window_too_wide" and requested_days is not None and max_days is not None:
            self.missing_fields = ("date_window",)
            message = (
                f"This flexible search spans {requested_days} days, above the server limit of "
                f"{max_days}. Narrow the date window."
            )
        else:
            self.missing_fields = ("destination_airports", "date_window")
            message = (
                f"This flexible search needs {requested_calls} exact searches, above the server "
                f"limit of {max_calls}. Narrow the airport choices or date window."
            )
        super().__init__(message)


@dataclass
class _AttemptExecution:
    criteria: FlightSearchCriteria
    page: SearchResultPage | None
    error: ProviderError | None
    attempt: FlightSearchAttempt
    safe_offers: tuple[Any, ...] = ()
    fingerprints: tuple[str, ...] = ()
    search_id: UUID | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _trace_id(value: str | None) -> str:
    normalized = str(value or "unknown").strip()
    return normalized[:160] or "unknown"


def _canonical_decimal(value: Decimal) -> str:
    if value == 0:
        return "0"
    return format(value.normalize(), "f")


def _safe_error_code(error: ProviderError) -> str:
    if isinstance(error, ProviderTimeoutError):
        return "provider_timeout"
    if isinstance(error, ProviderRateLimitError):
        return "provider_rate_limited"
    if isinstance(error, (ProviderUnavailableError, CircuitOpenError)):
        return "provider_unavailable"
    if isinstance(error, ProviderValidationError):
        return "provider_validation"
    return "provider_error"


def stable_offer_fingerprint(offer: FlightOffer) -> str:
    payload = {
        "segments": [
            {
                "origin": segment.origin,
                "destination": segment.destination,
                "departure_at": _utc(segment.departure_at).isoformat(),
                "arrival_at": _utc(segment.arrival_at).isoformat(),
                "marketing_carrier": segment.marketing_carrier,
                "operating_carrier": segment.operating_carrier,
                "flight_number": segment.flight_number,
            }
            for segment in offer.segments
        ],
        "cabin": offer.cabin.value,
        "fare_brand": offer.fare_brand,
        "currency": offer.total.currency,
        "amount": _canonical_decimal(offer.total.amount),
        "baggage": offer.baggage.model_dump(mode="json"),
        "fare_conditions": offer.fare_conditions.model_dump(mode="json"),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def parse_stored_search_criteria(
    raw: Mapping[str, Any],
) -> FlightSearchCriteria | ExecutableFlightSearch:
    """Parse exact and flexible criteria persisted on a search aggregate.

    Flexible rows created before the discriminator was introduced are accepted only when
    their bounded executable shape is recognizable.
    """

    if not isinstance(raw, Mapping):
        raise ValueError("stored search criteria must be an object")
    candidate = raw.get("resolved_request")
    normalized = dict(candidate) if isinstance(candidate, Mapping) else dict(raw)
    status = normalized.get("status")
    status_value = getattr(status, "value", status)
    if status_value == "executable":
        return ExecutableFlightSearch.model_validate(normalized)
    flexible_keys = {"resolved_origin", "destination_airports", "date_window"}
    if flexible_keys.issubset(normalized):
        normalized["status"] = "executable"
        return ExecutableFlightSearch.model_validate(normalized)
    return FlightSearchCriteria.model_validate(normalized)


def _normalize_stored_search_criteria(request: Mapping[str, Any]) -> dict[str, Any]:
    normalized = dict(request)
    if "status" not in normalized and {
        "resolved_origin",
        "destination_airports",
        "date_window",
    }.issubset(normalized):
        normalized["status"] = "executable"
    return normalized


class FlightSearchApplicationService:
    def __init__(
        self,
        session_factory: Callable[[], Session],
        flight_search: FlightSearchService,
        *,
        clock: Clock | None = None,
        discovery_settings: DiscoverySearchSettings | None = None,
    ) -> None:
        self.session_factory = session_factory
        self.flight_search = flight_search
        self.clock = clock or SystemClock()
        self.discovery_settings = discovery_settings or DiscoverySearchSettings.from_environment()

    @property
    def provider_name(self) -> str:
        return self.flight_search.provider.name.strip().lower()

    @property
    def provider_environment(self) -> str:
        return self.flight_search.provider.environment.value

    def _create_discovery(self, user_id: UUID, request: dict[str, Any]) -> tuple[UUID, UUID]:
        discovery_id = uuid4()
        search_id = uuid4()
        initial_expiry = _utc(self.clock.now()) + timedelta(minutes=5)
        stored_request = _normalize_stored_search_criteria(request)
        session = self.session_factory()
        try:
            with session.begin():
                session.add(
                    FlightDiscoveryRecord(
                        id=discovery_id,
                        user_id=user_id,
                        resolved_request=stored_request,
                        status="pending",
                    )
                )
                session.add(
                    FlightSearchRecord(
                        id=search_id,
                        user_id=user_id,
                        criteria=stored_request,
                        provider=self.provider_name,
                        environment=self.provider_environment,
                        expires_at=initial_expiry,
                    )
                )
                session.flush()
        finally:
            session.close()
        return discovery_id, search_id

    def _page_expiry(self, page: SearchResultPage, now: datetime) -> datetime:
        expiry = _utc(now) + timedelta(minutes=5)
        if page.metadata.expires_at is not None:
            expiry = min(expiry, _utc(page.metadata.expires_at))
        for offer in page.offers:
            if offer.metadata.expires_at is not None:
                expiry = min(expiry, _utc(offer.metadata.expires_at))
        return expiry if expiry > _utc(now) else _utc(now) + timedelta(seconds=1)

    def _persist_attempt(
        self,
        user_id: UUID,
        discovery_id: UUID,
        search_id: UUID,
        criteria: FlightSearchCriteria,
        started_at: datetime,
        completed_at: datetime,
        page: SearchResultPage | None,
        error: ProviderError | None,
    ) -> _AttemptExecution:
        provider = page.metadata.provider if page is not None else self.provider_name
        environment = (
            page.metadata.environment.value if page is not None else self.provider_environment
        )
        safe_offers: list[Any] = []
        fingerprints: list[str] = []
        session = self.session_factory()
        try:
            with session.begin():
                discovery = session.scalar(
                    select(FlightDiscoveryRecord).where(
                        FlightDiscoveryRecord.id == discovery_id,
                        FlightDiscoveryRecord.user_id == user_id,
                    )
                )
                if discovery is None:
                    raise ResourceNotFoundError(
                        "discovery does not belong to the authenticated user"
                    )
                search = session.scalar(
                    select(FlightSearchRecord)
                    .where(
                        FlightSearchRecord.id == search_id,
                        FlightSearchRecord.user_id == user_id,
                    )
                    .with_for_update()
                )
                if search is None:
                    raise ResourceNotFoundError("search does not belong to the authenticated user")
                if page is not None:
                    now = _utc(completed_at)
                    existing_records = session.scalars(
                        select(FlightOfferRecord)
                        .where(
                            FlightOfferRecord.user_id == user_id,
                            FlightOfferRecord.search_id == search_id,
                        )
                        .order_by(FlightOfferRecord.id)
                        .limit(MAX_AGGREGATE_OFFERS)
                    ).all()
                    seen_fingerprints: set[str] = set()
                    occupied_slots = 0
                    for existing in existing_records:
                        if _utc(existing.expires_at) <= now:
                            continue
                        try:
                            existing_offer = FlightOffer.model_validate(existing.offer_snapshot)
                        except ValueError:
                            occupied_slots += 1
                            continue
                        if existing_offer.is_expired(now):
                            continue
                        fingerprint = stable_offer_fingerprint(existing_offer)
                        if fingerprint not in seen_fingerprints:
                            seen_fingerprints.add(fingerprint)
                            occupied_slots += 1
                    for offer in page.offers[:MAX_PROVIDER_OFFERS_PER_ATTEMPT]:
                        if offer.is_expired(now):
                            continue
                        fingerprint = stable_offer_fingerprint(offer)
                        if fingerprint in seen_fingerprints:
                            continue
                        if occupied_slots >= MAX_AGGREGATE_OFFERS:
                            break
                        provider_offer_id = offer.metadata.provider_offer_id or str(offer.id)
                        provider = offer.metadata.provider
                        environment = offer.metadata.environment.value
                        record = session.scalar(
                            select(FlightOfferRecord)
                            .where(
                                FlightOfferRecord.user_id == user_id,
                                FlightOfferRecord.search_id == search_id,
                                FlightOfferRecord.provider == provider,
                                FlightOfferRecord.environment == environment,
                                FlightOfferRecord.provider_offer_id == provider_offer_id,
                            )
                            .with_for_update()
                        )
                        if record is None:
                            record = FlightOfferRecord(
                                id=uuid4(),
                                user_id=user_id,
                                search_id=search.id,
                                provider=provider,
                                environment=environment,
                                provider_offer_id=provider_offer_id,
                                offer_snapshot=offer.model_dump(mode="json"),
                                retrieved_at=offer.metadata.retrieved_at,
                                expires_at=offer.metadata.expires_at or completed_at,
                            )
                            session.add(record)
                            session.flush()
                        else:
                            record.offer_snapshot = offer.model_dump(mode="json")
                            record.retrieved_at = offer.metadata.retrieved_at
                            record.expires_at = offer.metadata.expires_at or completed_at
                            record.version += 1
                        seen_fingerprints.add(fingerprint)
                        occupied_slots += 1
                        safe_offers.append(safe_offer_from_flight(offer, record.id))
                        fingerprints.append(fingerprint)
                outcome = (
                    SearchAttemptOutcome.RESULTS
                    if page is not None and page.offers
                    else SearchAttemptOutcome.NO_RESULTS
                    if page is not None
                    else SearchAttemptOutcome.PROVIDER_ERROR
                )
                session.add(
                    FlightSearchAttemptRecord(
                        id=uuid4(),
                        user_id=user_id,
                        discovery_id=discovery_id,
                        search_id=search_id,
                        criteria=criteria.model_dump(mode="json"),
                        provider=provider,
                        environment=environment,
                        outcome=outcome.value,
                        result_count=len(page.offers) if page is not None else 0,
                        safe_error_code=_safe_error_code(error) if error is not None else None,
                        started_at=_utc(started_at),
                        completed_at=_utc(completed_at),
                    )
                )
                session.flush()
        finally:
            session.close()
        logger.info(
            "flight_search_attempt_metric",
            extra={
                "metric_name": "flight_search_attempts_total",
                "provider": provider,
                "environment": environment,
                "outcome": outcome.value,
            },
        )
        attempt = FlightSearchAttempt(
            criteria=criteria,
            provider=provider,
            environment=environment,
            outcome=outcome,
            result_count=len(page.offers) if page is not None else 0,
            safe_error_code=_safe_error_code(error) if error is not None else None,
            started_at=_utc(started_at),
            completed_at=_utc(completed_at),
        )
        return _AttemptExecution(
            criteria=criteria,
            page=page,
            error=error,
            attempt=attempt,
            safe_offers=tuple(safe_offers),
            fingerprints=tuple(fingerprints),
            search_id=search_id,
        )

    async def _run_attempt(
        self,
        user_id: UUID,
        discovery_id: UUID,
        search_id: UUID,
        criteria: FlightSearchCriteria,
        trace_id: str,
        semaphore: asyncio.Semaphore | None = None,
    ) -> _AttemptExecution:
        started_at = _utc(self.clock.now())
        page: SearchResultPage | None = None
        error: ProviderError | None = None
        try:
            if semaphore is None:
                page = await self.flight_search.search(criteria, correlation_id=trace_id)
            else:
                async with semaphore:
                    page = await self.flight_search.search(criteria, correlation_id=trace_id)
        except asyncio.CancelledError:
            error = ProviderTimeoutError(
                provider=self.provider_name,
                operation="search",
                safe_message="search exceeded the overall discovery deadline",
            )
        except ProviderError as exc:
            error = exc
        except Exception:
            error = ProviderError(
                provider=self.provider_name,
                operation="search",
                safe_message="provider search failed",
                retryable=True,
            )
        completed_at = _utc(self.clock.now())
        return self._persist_attempt(
            user_id,
            discovery_id,
            search_id,
            criteria,
            started_at,
            completed_at,
            page,
            error,
        )

    def _complete_discovery(
        self,
        user_id: UUID,
        discovery_id: UUID,
        search_id: UUID,
        status: TripDiscoveryStatus,
        expires_at: datetime,
    ) -> None:
        session = self.session_factory()
        try:
            with session.begin():
                record = session.scalar(
                    select(FlightDiscoveryRecord).where(
                        FlightDiscoveryRecord.id == discovery_id,
                        FlightDiscoveryRecord.user_id == user_id,
                    )
                )
                if record is None:
                    raise ResourceNotFoundError(
                        "discovery does not belong to the authenticated user"
                    )
                search = session.scalar(
                    select(FlightSearchRecord).where(
                        FlightSearchRecord.id == search_id,
                        FlightSearchRecord.user_id == user_id,
                    )
                )
                if search is None:
                    raise ResourceNotFoundError("search does not belong to the authenticated user")
                record.status = status.value
                record.completed_at = _utc(self.clock.now())
                search.expires_at = _utc(expires_at)
        finally:
            session.close()

    def _build_result(
        self,
        user_id: UUID,
        discovery_id: UUID,
        search_id: UUID,
        request: dict[str, Any],
        executions: tuple[_AttemptExecution, ...],
        trace_id: str,
    ) -> TripDiscoverySearchResult:
        offers: list[Any] = []
        fingerprints: set[str] = set()
        warnings: list[str] = []
        now = _utc(self.clock.now())
        for execution in executions:
            if len(offers) >= MAX_AGGREGATE_OFFERS:
                break
            if execution.page is not None:
                for warning in execution.page.warnings:
                    if warning not in warnings:
                        warnings.append(warning)
            for offer, fingerprint in zip(
                execution.safe_offers, execution.fingerprints, strict=True
            ):
                if offer.expires_at <= now or fingerprint in fingerprints:
                    continue
                fingerprints.add(fingerprint)
                offers.append(offer)
                if len(offers) >= MAX_AGGREGATE_OFFERS:
                    break
        failures = tuple(item for item in executions if item.error is not None)
        successful_pages = tuple(item for item in executions if item.page is not None)
        if failures and successful_pages:
            logger.info(
                "flight_search_partial_metric",
                extra={
                    "metric_name": "flight_search_partial_failures_total",
                    "provider": self.provider_name,
                    "environment": self.provider_environment,
                },
            )
            warnings.append("partial_provider_failure")
        if offers:
            status = TripDiscoveryStatus.RESULTS
        elif successful_pages:
            status = TripDiscoveryStatus.NO_RESULTS
        else:
            status = TripDiscoveryStatus.PROVIDER_UNAVAILABLE
        aggregate_expiry = min(
            (_utc(offer.expires_at) for offer in offers if _utc(offer.expires_at) > now),
            default=now + timedelta(minutes=5),
        )
        self._complete_discovery(
            user_id,
            discovery_id,
            search_id,
            status,
            aggregate_expiry,
        )
        return TripDiscoverySearchResult(
            status=status,
            discovery_id=discovery_id,
            search_id=search_id,
            resolved_request=request,
            attempts=tuple(item.attempt for item in executions),
            offers=tuple(offers),
            warnings=tuple(warnings),
            retryable=any(bool(getattr(item.error, "retryable", True)) for item in failures),
            trace_id=trace_id,
        )

    async def search_exact(
        self, user_id: UUID, criteria: FlightSearchCriteria, trace_id: str | None = None
    ) -> TripDiscoverySearchResult:
        trace = _trace_id(trace_id)
        request = criteria.model_dump(mode="json")
        discovery_id, search_id = self._create_discovery(user_id, request)
        execution = await self._run_attempt(
            user_id,
            discovery_id,
            search_id,
            criteria,
            trace,
        )
        return self._build_result(
            user_id,
            discovery_id,
            search_id,
            request,
            (execution,),
            trace,
        )

    @staticmethod
    def _criteria_for_discovery(
        request: ExecutableFlightSearch,
    ) -> tuple[FlightSearchCriteria, ...]:
        day_count = (request.date_window.end_date - request.date_window.start_date).days + 1
        dates = tuple(
            request.date_window.start_date + timedelta(days=index) for index in range(day_count)
        )
        return tuple(
            FlightSearchCriteria(
                origin=request.resolved_origin,
                destination=destination,
                departure_date=departure,
                passengers=request.passengers,
                cabin=request.cabin,
                currency=request.currency,
                max_stops=request.max_stops,
                baggage_required=request.baggage_required,
                preferred_departure_start=request.preferred_departure_start,
                preferred_departure_end=request.preferred_departure_end,
            )
            for departure in dates
            for destination in sorted(request.destination_airports)
        )

    async def search_discovery(
        self, user_id: UUID, request: ExecutableFlightSearch, trace_id: str | None = None
    ) -> TripDiscoverySearchResult:
        settings = self.discovery_settings
        criteria_list = self._criteria_for_discovery(request)
        day_count = (request.date_window.end_date - request.date_window.start_date).days + 1
        if day_count > settings.max_days:
            raise DiscoveryBudgetExceeded(
                len(criteria_list),
                settings.max_calls,
                reason="date_window_too_wide",
                requested_days=day_count,
                max_days=settings.max_days,
            )
        if len(criteria_list) > settings.max_calls:
            raise DiscoveryBudgetExceeded(len(criteria_list), settings.max_calls)
        trace = _trace_id(trace_id)
        resolved_request = request.model_dump(mode="json")
        discovery_id, search_id = self._create_discovery(user_id, resolved_request)
        semaphore = asyncio.Semaphore(settings.concurrency)
        tasks = [
            asyncio.create_task(
                self._run_attempt(
                    user_id,
                    discovery_id,
                    search_id,
                    criteria,
                    trace,
                    semaphore,
                )
            )
            for criteria in criteria_list
        ]
        _, pending = await asyncio.wait(tasks, timeout=settings.timeout_seconds)
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        executions: list[_AttemptExecution] = []
        for criteria, task in zip(criteria_list, tasks, strict=True):
            if task.cancelled():
                started_at = _utc(self.clock.now())
                timeout_error = ProviderTimeoutError(
                    provider=self.provider_name,
                    operation="search",
                    safe_message="search exceeded the overall discovery deadline",
                )
                executions.append(
                    self._persist_attempt(
                        user_id,
                        discovery_id,
                        search_id,
                        criteria,
                        started_at,
                        _utc(self.clock.now()),
                        None,
                        timeout_error,
                    )
                )
            else:
                executions.append(task.result())
        return self._build_result(
            user_id,
            discovery_id,
            search_id,
            resolved_request,
            tuple(executions),
            trace,
        )

    async def reprice_owned_offer(
        self,
        principal: AuthenticatedPrincipal,
        offer_id: UUID,
        trace_id: str | None = None,
    ) -> TripDiscoveryRepriceResult:
        trace = _trace_id(trace_id)
        session = self.session_factory()
        try:
            with session.begin():
                record = session.scalar(
                    select(FlightOfferRecord).where(
                        FlightOfferRecord.id == offer_id,
                        FlightOfferRecord.user_id == principal.user_id,
                    )
                )
                if record is None:
                    raise ResourceNotFoundError("offer does not belong to the authenticated user")
                provider_offer_id = record.provider_offer_id
                expected_version = record.version
                expected = FlightOffer.model_validate(record.offer_snapshot)
                expires_at = _utc(record.expires_at)
        finally:
            session.close()
        if expires_at <= _utc(self.clock.now()):
            return TripDiscoveryRepriceResult(
                status=RepriceStatus.EXPIRED,
                reason="offer expired before repricing",
                trace_id=trace,
            )
        try:
            repricing = await self.flight_search.reprice(
                provider_offer_id, expected, correlation_id=trace
            )
        except ProviderError:
            return TripDiscoveryRepriceResult(
                status=RepriceStatus.UNAVAILABLE,
                reason="provider unavailable",
                trace_id=trace,
            )
        safe_offer = None
        session = self.session_factory()
        try:
            with session.begin():
                current = session.scalar(
                    select(FlightOfferRecord).where(
                        FlightOfferRecord.id == offer_id,
                        FlightOfferRecord.user_id == principal.user_id,
                    )
                )
                if current is None:
                    raise ResourceNotFoundError("offer does not belong to the authenticated user")
                if current.version != expected_version:
                    raise ConcurrencyConflictError("offer changed while repricing")
                if _utc(current.expires_at) <= _utc(self.clock.now()):
                    return TripDiscoveryRepriceResult(
                        status=RepriceStatus.EXPIRED,
                        reason="offer expired while repricing",
                        trace_id=trace,
                    )
                if repricing.repriced_offer is not None:
                    metadata = repricing.repriced_offer.metadata
                    if metadata.provider_offer_id is not None:
                        current.provider_offer_id = metadata.provider_offer_id
                    current.provider = metadata.provider
                    current.environment = metadata.environment.value
                    current.offer_snapshot = repricing.repriced_offer.model_dump(mode="json")
                    current.retrieved_at = metadata.retrieved_at
                    current.expires_at = metadata.expires_at or current.expires_at
                    current.version += 1
                    safe_offer = safe_offer_from_flight(repricing.repriced_offer, current.id)
        finally:
            session.close()
        return TripDiscoveryRepriceResult(
            status=repricing.status,
            repriced_offer=safe_offer,
            reason=repricing.reason,
            trace_id=trace,
        )


__all__ = [
    "DiscoveryBudgetExceeded",
    "DiscoverySearchSettings",
    "FlightSearchApplicationService",
    "parse_stored_search_criteria",
    "stable_offer_fingerprint",
]
