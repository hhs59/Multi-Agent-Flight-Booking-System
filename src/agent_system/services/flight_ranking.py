from __future__ import annotations

import os
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime, time
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent_system.domain.flights import FlightOffer, FlightSearchCriteria
from agent_system.domain.limits import (
    MAX_AGGREGATE_OFFERS,
    MAX_CLIENT_OFFERS,
)
from agent_system.domain.ranking import (
    RankedFlightOffer,
    RankingReason,
    RankingScoreComponent,
    SafeFlightOffer,
    SafeFlightSegment,
)
from agent_system.providers.localization import AirportCatalog

RANKING_VERSION = "flight-rank-v1"
_SCORE_QUANTUM = Decimal("0.000001")
_WEIGHT_PRICE = Decimal("0.45")
_WEIGHT_DURATION = Decimal("0.25")
_WEIGHT_STOPS = Decimal("0.20")
_WEIGHT_BAGGAGE = Decimal("0.05")
_WEIGHT_DEPARTURE = Decimal("0.05")
_WEIGHTS = (
    _WEIGHT_PRICE,
    _WEIGHT_DURATION,
    _WEIGHT_STOPS,
    _WEIGHT_BAGGAGE,
    _WEIGHT_DEPARTURE,
)


def _quantize(value: Decimal) -> Decimal:
    return value.quantize(_SCORE_QUANTUM)


def _ensure_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("ranking expiry checks require a timezone-aware now")
    return value.astimezone(UTC)


def _minmax_lower_is_better(value: Decimal, values: Sequence[Decimal]) -> Decimal:
    minimum = min(values)
    maximum = max(values)
    if minimum == maximum:
        return Decimal("1.000000")
    return _quantize((maximum - value) / (maximum - minimum))


def _is_included(offer: SafeFlightOffer) -> bool:
    baggage = offer.baggage
    return bool(
        (baggage.checked_pieces is not None and baggage.checked_pieces > 0)
        or (baggage.checked_weight_kg is not None and baggage.checked_weight_kg > 0)
    )


def _validated_departure_timezone(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise ValueError("departure_timezone cannot be blank")
    try:
        ZoneInfo(normalized)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA departure timezone: {normalized}") from exc
    return normalized


def resolve_departure_timezone(
    origin_airport: str | None,
    *,
    fallback_timezone: str | None = None,
) -> str:
    if origin_airport:
        try:
            return _validated_departure_timezone(
                AirportCatalog.from_v2_package_data().get(origin_airport).timezone
            )
        except ValueError:
            pass
    return _validated_departure_timezone(
        fallback_timezone or os.getenv("TRAVEL_DEFAULT_TIMEZONE", "Asia/Ho_Chi_Minh")
    )


def _departure_matches(
    value: datetime,
    preference: tuple[time, time] | None,
    departure_timezone: ZoneInfo,
) -> bool:
    if preference is None:
        return True
    start, end = preference
    local_time = value.astimezone(departure_timezone).time()
    if start <= end:
        return start <= local_time <= end
    return local_time >= start or local_time <= end


def _coerce_offer(value: SafeFlightOffer | dict[str, Any]) -> SafeFlightOffer:
    if isinstance(value, SafeFlightOffer):
        return value
    return SafeFlightOffer.model_validate(value)


def _safe_offer_from_flight(offer: FlightOffer, application_offer_id) -> SafeFlightOffer:
    segments = tuple(
        SafeFlightSegment(
            origin=segment.origin,
            destination=segment.destination,
            departure_at=segment.departure_at,
            arrival_at=segment.arrival_at,
            flight_number=segment.flight_number,
        )
        for segment in offer.segments
    )
    first = offer.segments[0]
    last = offer.segments[-1]
    duration_minutes = max(0, int((last.arrival_at - first.departure_at).total_seconds() // 60))
    return SafeFlightOffer(
        offer_id=application_offer_id,
        origin=first.origin,
        destination=last.destination,
        departure_at=first.departure_at,
        arrival_at=last.arrival_at,
        duration_minutes=duration_minutes,
        stops=max(0, len(offer.segments) - 1),
        flight_numbers=tuple(segment.flight_number for segment in offer.segments),
        carrier=offer.validating_carrier,
        cabin=offer.cabin,
        total=offer.total.amount,
        currency=offer.total.currency,
        baggage=offer.baggage,
        fare_conditions=offer.fare_conditions,
        provider=offer.metadata.provider,
        environment=offer.metadata.environment,
        is_live=offer.metadata.is_live,
        retrieved_at=offer.metadata.retrieved_at,
        expires_at=offer.metadata.expires_at,
        segments=segments,
    )


def safe_offer_from_flight(offer: FlightOffer, application_offer_id) -> SafeFlightOffer:
    """Map provider-owned typed facts to an application-owned safe offer."""

    if offer.metadata.expires_at is None:
        raise ValueError("safe offers require an expiry instant")
    return _safe_offer_from_flight(offer, application_offer_id)


def safe_offer_response(
    offer: SafeFlightOffer,
    *,
    rank: int | None = None,
    ranking_reasons: Sequence[RankingReason | str] = (),
) -> dict[str, Any]:
    """Serialize only application-owned facts for HTTP/chat responses."""

    result = offer.model_dump(mode="json")
    result["offer_id"] = str(offer.offer_id)
    # The exact HTTP endpoint historically called this field id; retain the alias
    # while keeping offer_id authoritative for chat and selection.
    result["id"] = str(offer.offer_id)
    result["source"] = offer.provider
    if rank is not None:
        result["rank"] = rank
    result["ranking_reasons"] = [
        reason.value if isinstance(reason, RankingReason) else str(reason)
        for reason in ranking_reasons
    ]
    return result


class FlightRankingService:
    """Pure, versioned ranking over already-safe application-owned offer facts."""

    ranking_version = RANKING_VERSION
    weights = {
        "price": _WEIGHT_PRICE,
        "duration": _WEIGHT_DURATION,
        "stops": _WEIGHT_STOPS,
        "baggage": _WEIGHT_BAGGAGE,
        "departure_match": _WEIGHT_DEPARTURE,
    }

    def rank(
        self,
        offers: Iterable[SafeFlightOffer | dict[str, Any]],
        *,
        now: datetime,
        requested_currency: str | None = None,
        max_stops: int | None = None,
        baggage_required: bool | None = None,
        criteria: FlightSearchCriteria | None = None,
        departure_preference: tuple[time, time] | None = None,
        departure_time_window: tuple[time, time] | None = None,
        departure_timezone: str | None = None,
    ) -> tuple[RankedFlightOffer, ...]:
        checked_at = _ensure_utc(now)
        if criteria is not None:
            if requested_currency is None:
                requested_currency = criteria.currency
            if max_stops is None:
                max_stops = criteria.max_stops
            if baggage_required is None:
                baggage_required = criteria.baggage_required
            if (
                departure_preference is None
                and departure_time_window is None
                and criteria.preferred_departure_start is not None
                and criteria.preferred_departure_end is not None
            ):
                departure_time_window = (
                    criteria.preferred_departure_start,
                    criteria.preferred_departure_end,
                )
        if max_stops is not None and max_stops < 0:
            raise ValueError("max_stops cannot be negative")
        if departure_preference is not None and departure_time_window is not None:
            raise ValueError("provide only one departure preference")
        preference = departure_preference or departure_time_window
        departure_zone = ZoneInfo(
            _validated_departure_timezone(
                departure_timezone or os.getenv("TRAVEL_DEFAULT_TIMEZONE", "Asia/Ho_Chi_Minh")
            )
        )
        requested = requested_currency.upper() if requested_currency else None
        if requested is not None and len(requested) != 3:
            raise ValueError("requested_currency must be a three-letter currency code")

        eligible: list[SafeFlightOffer] = []
        seen_offer_ids: set[Any] = set()
        for raw_offer in offers:
            offer = _coerce_offer(raw_offer)
            if offer.offer_id in seen_offer_ids:
                continue
            if offer.expires_at <= checked_at:
                continue
            if max_stops is not None and offer.stops > max_stops:
                continue
            if baggage_required is True and not _is_included(offer):
                continue
            seen_offer_ids.add(offer.offer_id)
            eligible.append(offer)
            if len(eligible) >= MAX_AGGREGATE_OFFERS:
                break

        grouped: dict[str, list[SafeFlightOffer]] = defaultdict(list)
        for offer in eligible:
            grouped[offer.currency].append(offer)
        currency_order = sorted(grouped)
        if requested in grouped:
            currency_order.remove(requested)
            currency_order.insert(0, requested)

        ranked: list[RankedFlightOffer] = []
        for currency in currency_order:
            group = grouped[currency]
            price_values = [offer.total for offer in group]
            duration_values = [Decimal(offer.duration_minutes) for offer in group]
            stops_values = [Decimal(offer.stops) for offer in group]
            min_price = min(price_values)
            min_duration = min(duration_values)
            min_stops = min(stops_values)
            scored: list[
                tuple[
                    SafeFlightOffer,
                    Decimal,
                    tuple[RankingReason, ...],
                    tuple[RankingScoreComponent, ...],
                ]
            ] = []
            for offer in group:
                price_score = _minmax_lower_is_better(offer.total, price_values)
                duration_score = _minmax_lower_is_better(
                    Decimal(offer.duration_minutes), duration_values
                )
                stops_score = _minmax_lower_is_better(Decimal(offer.stops), stops_values)
                baggage_score = Decimal("1.000000") if _is_included(offer) else Decimal("0.000000")
                departure_score = (
                    Decimal("0.500000")
                    if preference is None
                    else (
                        Decimal("1.000000")
                        if _departure_matches(offer.departure_at, preference, departure_zone)
                        else Decimal("0.000000")
                    )
                )
                normalized = (
                    price_score,
                    duration_score,
                    stops_score,
                    baggage_score,
                    departure_score,
                )
                names = ("price", "duration", "stops", "baggage", "departure_match")
                raw_values: tuple[str | int, ...] = (
                    format(offer.total, "f"),
                    offer.duration_minutes,
                    offer.stops,
                    "included" if _is_included(offer) else "not_included",
                    "neutral"
                    if preference is None
                    else (
                        "match"
                        if _departure_matches(
                            offer.departure_at,
                            preference,
                            departure_zone,
                        )
                        else "no_match"
                    ),
                )
                components = tuple(
                    RankingScoreComponent(
                        name=name,
                        raw_value=raw_value,
                        normalized_score=score,
                        weight=weight,
                        weighted_score=_quantize(score * weight),
                    )
                    for name, raw_value, score, weight in zip(
                        names, raw_values, normalized, _WEIGHTS, strict=True
                    )
                )
                total_score = _quantize(
                    sum((component.weighted_score for component in components), Decimal("0"))
                )
                reasons: list[RankingReason] = []
                if offer.total == min_price:
                    reasons.append(RankingReason.LOWEST_TOTAL)
                if Decimal(offer.duration_minutes) == min_duration:
                    reasons.append(RankingReason.SHORTER_DURATION)
                if offer.stops == 0:
                    reasons.append(RankingReason.NONSTOP)
                elif Decimal(offer.stops) == min_stops:
                    reasons.append(RankingReason.FEWER_STOPS)
                if _is_included(offer):
                    reasons.append(RankingReason.BAGGAGE_INCLUDED)
                if preference is not None and _departure_matches(
                    offer.departure_at,
                    preference,
                    departure_zone,
                ):
                    reasons.append(RankingReason.DEPARTURE_TIME_MATCH)
                scored.append((offer, total_score, tuple(reasons[:5]), components))
            scored.sort(
                key=lambda item: (
                    -item[1],
                    item[0].total,
                    item[0].duration_minutes,
                    item[0].stops,
                    str(item[0].offer_id),
                )
            )
            for offer, total_score, reasons, components in scored:
                if len(ranked) >= MAX_CLIENT_OFFERS:
                    break
                ranked.append(
                    RankedFlightOffer(
                        offer=offer,
                        rank=len(ranked) + 1,
                        total_score=total_score,
                        ranking_version=RANKING_VERSION,
                        reasons=reasons,
                        components=components,
                    )
                )
            if len(ranked) >= MAX_CLIENT_OFFERS:
                break
        return tuple(ranked)

    def rank_offers(self, *args, **kwargs) -> tuple[RankedFlightOffer, ...]:
        return self.rank(*args, **kwargs)


def provider_order_offers(
    offers: Iterable[SafeFlightOffer | dict[str, Any]],
    *,
    now: datetime,
    max_stops: int | None = None,
) -> tuple[SafeFlightOffer, ...]:
    """Retain provider order for the disabled rollout while applying safe eligibility."""

    checked_at = _ensure_utc(now)
    result: list[SafeFlightOffer] = []
    seen_offer_ids: set[Any] = set()
    for raw_offer in offers:
        offer = _coerce_offer(raw_offer)
        if offer.offer_id in seen_offer_ids:
            continue
        if offer.expires_at <= checked_at:
            continue
        if max_stops is not None and offer.stops > max_stops:
            continue
        seen_offer_ids.add(offer.offer_id)
        result.append(offer)
        if len(result) >= MAX_CLIENT_OFFERS:
            break
    return tuple(result)


__all__ = [
    "FlightRankingService",
    "RANKING_VERSION",
    "provider_order_offers",
    "resolve_departure_timezone",
    "safe_offer_from_flight",
    "safe_offer_response",
]
