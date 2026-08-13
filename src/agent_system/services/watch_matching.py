from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from agent_system.domain.flights import FlightOffer
from agent_system.domain.watches import FlightWatchCriteria


@dataclass(frozen=True)
class MatchDecision:
    matched: bool
    reasons: tuple[str, ...]
    rejection_reason: str | None = None


class WatchPolicyEvaluator:
    """Deterministic hard-constraint evaluator; it never asks an LLM to decide a match."""

    def __init__(self, *, action_buffer: timedelta = timedelta(minutes=2)) -> None:
        self.action_buffer = action_buffer

    def evaluate(
        self,
        criteria: FlightWatchCriteria,
        offer: FlightOffer,
        *,
        now: datetime,
    ) -> MatchDecision:
        now = now.astimezone(UTC)
        reasons: list[str] = []
        failures: list[str] = []
        try:
            timezone = ZoneInfo(criteria.timezone)
        except ZoneInfoNotFoundError:
            failures.append("invalid_timezone")
            timezone = UTC
        first_segment = offer.segments[0]
        departure_date = first_segment.departure_at.astimezone(timezone).date()
        if not criteria.departure_date_from <= departure_date <= criteria.departure_date_to:
            failures.append("departure_date_outside_window")
        else:
            reasons.append("departure_date_in_window")
        if (
            first_segment.origin != criteria.origin
            or offer.segments[-1].destination != criteria.destination
        ):
            failures.append("route_mismatch")
        else:
            reasons.append("route_matches")
        stops = len(offer.segments) - 1
        if criteria.max_stops is not None and stops > criteria.max_stops:
            failures.append("too_many_stops")
        else:
            reasons.append("stops_within_limit")
        if offer.cabin is not criteria.cabin:
            failures.append("cabin_mismatch")
        else:
            reasons.append("cabin_matches")
        carrier = offer.validating_carrier
        if criteria.preferred_carriers and carrier not in criteria.preferred_carriers:
            failures.append("carrier_not_preferred")
        elif carrier in criteria.excluded_carriers:
            failures.append("carrier_excluded")
        else:
            reasons.append("carrier_allowed")
        if criteria.selected_provider and offer.metadata.provider != criteria.selected_provider:
            failures.append("provider_mismatch")
        if criteria.maximum_total is not None:
            if offer.total.currency != criteria.maximum_total.currency:
                failures.append("currency_mismatch")
            elif offer.total.amount > criteria.maximum_total.amount:
                failures.append("price_above_maximum")
            else:
                reasons.append("price_within_limit")
        if criteria.minimum_checked_pieces is not None:
            pieces = offer.baggage.checked_pieces
            if pieces is None:
                failures.append("baggage_data_missing")
            elif pieces < criteria.minimum_checked_pieces:
                failures.append("baggage_below_minimum")
            else:
                reasons.append("baggage_requirement_met")
        if criteria.require_refundable:
            if offer.fare_conditions.refundable is not True:
                failures.append("refundable_fare_required")
            else:
                reasons.append("refundable_fare")
        expiry = offer.metadata.expires_at
        if expiry is None or expiry <= now + self.action_buffer:
            failures.append("offer_expires_before_action")
        else:
            reasons.append("offer_is_actionable")
        if criteria.purchase_deadline is not None:
            if now >= criteria.purchase_deadline:
                failures.append("purchase_deadline_expired")
            else:
                reasons.append("purchase_deadline_open")
        return MatchDecision(not failures, tuple(reasons), failures[0] if failures else None)
