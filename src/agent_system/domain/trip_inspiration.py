from __future__ import annotations

import re
from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID

from pydantic import Field, field_validator, model_validator

from agent_system.domain.accounts import CountryCode
from agent_system.domain.flights import CabinClass, PassengerMix
from agent_system.domain.intents import AgentIntent
from agent_system.domain.optimization import OptimizationPreference
from agent_system.domain.trip_discovery import LocationReference, TravelDateWindow
from agent_system.domain.values import AirportCode, DomainModel, Money, UTCInstant


class BudgetScope(StrEnum):
    AIRFARE_ONLY = "airfare_only"
    TOTAL_TRIP = "total_trip"
    UNKNOWN = "unknown"


BudgetAllocation = Literal["group_total", "per_person", "unknown"]


class TripInspirationStatus(StrEnum):
    CLARIFICATION_REQUIRED = "clarification_required"
    RESULTS = "results"
    NO_RESULTS = "no_results"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class TripInspirationNoResultReason(StrEnum):
    NO_VERIFIED_OFFER = "no_verified_offer"
    OVER_BUDGET = "over_budget"
    CURRENCY_MISMATCH = "currency_mismatch"
    CURRENCY_CONVERSION_UNAVAILABLE = "currency_conversion_unavailable"
    CANDIDATE_GENERATION_EMPTY = "candidate_generation_empty"
    CANDIDATE_VALIDATION_FAILED = "candidate_validation_failed"
    SEARCH_BUDGET_EXHAUSTED = "search_budget_exhausted"


class TripInspirationPendingClarification(StrEnum):
    ORIGIN = "origin"
    DATE_WINDOW = "date_window"
    BUDGET_CURRENCY = "budget_currency"
    BUDGET_SCOPE = "budget_scope"
    AIRFARE_ALLOCATION = "airfare_allocation"
    AIRFARE_ALLOCATION_CURRENCY = "airfare_allocation_currency"
    PASSENGERS = "passengers"


class TripInspirationCommand(DomainModel):
    intent: Literal[AgentIntent.TRIP_INSPIRATION] = AgentIntent.TRIP_INSPIRATION
    origin: LocationReference | None = None
    date_window: TravelDateWindow | None = None
    return_date: date | None = None
    airfare_budget: Money | None = None
    budget_scope: BudgetScope = BudgetScope.UNKNOWN
    budget_allocation: BudgetAllocation = "unknown"
    passengers: PassengerMix = Field(default_factory=PassengerMix)
    cabin: CabinClass = CabinClass.ECONOMY
    interests: tuple[str, ...] = Field(default_factory=tuple, max_length=5)

    @field_validator("interests")
    @classmethod
    def normalize_interests(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            item = " ".join(value.strip().split())
            if not item or len(item) > 80:
                raise ValueError("inspiration interests must be bounded non-empty strings")
            if item.casefold() not in {existing.casefold() for existing in normalized}:
                normalized.append(item)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_return_date(self) -> Self:
        if self.return_date is not None:
            if self.date_window is None:
                raise ValueError("return_date requires a date_window")
            if self.return_date < self.date_window.start_date:
                raise ValueError("return_date cannot precede departure window")
        return self


class TripInspirationCandidateRequest(DomainModel):
    """Safe, bounded facts supplied to the candidate-generation model."""

    origin_airport: AirportCode
    origin_label: str = Field(min_length=1, max_length=160)
    date_window: TravelDateWindow
    return_date: date | None = None
    airfare_budget: Money | None = None
    budget_scope: BudgetScope = BudgetScope.UNKNOWN
    budget_allocation: BudgetAllocation = "unknown"
    optimization: OptimizationPreference | None = None
    passengers: PassengerMix = Field(default_factory=PassengerMix)
    cabin: CabinClass = CabinClass.ECONOMY
    interests: tuple[str, ...] = Field(default_factory=tuple, max_length=5)
    destination_scope: str | None = Field(default=None, min_length=1, max_length=160)
    excluded_places: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    rejected_places: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    locale: Literal["vi", "en"]
    maximum_candidates: int = Field(default=5, ge=1, le=8)

    @model_validator(mode="after")
    def validate_budget_scope(self) -> Self:
        if self.budget_scope is BudgetScope.AIRFARE_ONLY and self.airfare_budget is None:
            raise ValueError("airfare_only requests require an airfare budget")
        if self.return_date is not None and self.return_date < self.date_window.start_date:
            raise ValueError("return_date cannot precede departure window")
        return self


def _reject_untrusted_candidate_text(value: str, field_name: str) -> str:
    normalized = " ".join(value.strip().split())
    if not normalized:
        raise ValueError(f"{field_name} cannot be blank")
    if re.search(
        r"(?:[$€£]\s*\d|\b\d+(?:\.\d{1,2})?\s*(?:usd|vnd|eur|gbp|jpy|aud|sgd|thb)\b)",
        normalized,
        re.I,
    ):
        raise ValueError(f"{field_name} cannot contain prices or currencies")
    if re.search(
        r"\b(?:iata|duffel|provider|offer(?:[_ ]?id)?|booking|book|availability|available|flight|fare|price|visa|weather|safety)\b",
        normalized,
        re.I,
    ):
        raise ValueError(f"{field_name} cannot contain provider or factual flight claims")
    if re.search(
        r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
        normalized,
        re.I,
    ):
        raise ValueError(f"{field_name} cannot contain identifiers")
    if field_name == "place_query" and re.fullmatch(r"[A-Z]{3}", normalized):
        raise ValueError("place_query must be a natural-language place, not an IATA code")
    return normalized


class DestinationIdea(DomainModel):
    place_query: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=300)

    @field_validator("place_query")
    @classmethod
    def validate_place_query(cls, value: str) -> str:
        return _reject_untrusted_candidate_text(value, "place_query")

    @field_validator("reason")
    @classmethod
    def validate_reason(cls, value: str) -> str:
        return _reject_untrusted_candidate_text(value, "reason")


class TripInspirationCandidateResult(DomainModel):
    ideas: tuple[DestinationIdea, ...] = Field(default_factory=tuple, max_length=8)

    @model_validator(mode="after")
    def validate_unique_queries(self) -> Self:
        queries = [idea.place_query.casefold() for idea in self.ideas]
        if len(queries) != len(set(queries)):
            raise ValueError("candidate place queries must be unique")
        return self


class TripInspirationConstraints(DomainModel):
    origin: AirportCode | None = None
    date_window: TravelDateWindow | None = None
    return_date: date | None = None
    airfare_budget: Money | None = None
    budget_scope: BudgetScope = BudgetScope.UNKNOWN
    budget_allocation: BudgetAllocation = "unknown"
    optimization: OptimizationPreference | None = None
    passengers: PassengerMix = Field(default_factory=PassengerMix)
    cabin: CabinClass = CabinClass.ECONOMY
    interests: tuple[str, ...] = Field(default_factory=tuple, max_length=5)

    @model_validator(mode="after")
    def validate_return_date(self) -> Self:
        if self.return_date is not None and (
            self.date_window is None or self.return_date < self.date_window.start_date
        ):
            raise ValueError("return_date must follow the departure window")
        return self


class InspirationBudgetComparison(DomainModel):
    """Advisory budget comparison; it is never a booking quote."""

    user_budget: Money
    comparison_budget: Money
    approximate_fare: Money
    rate: Decimal
    rate_source: str = Field(min_length=1, max_length=80)
    rate_as_of: UTCInstant
    rate_expires_at: UTCInstant
    is_demo_rate: bool

    @field_validator("rate", mode="before")
    @classmethod
    def reject_binary_float_rate(cls, value):
        if isinstance(value, float) or not isinstance(value, (Decimal, str)):
            raise TypeError("exchange rates must be created from Decimal or a decimal string")
        return value

    @model_validator(mode="after")
    def validate_comparison(self) -> Self:
        if not self.rate.is_finite() or self.rate <= 0:
            raise ValueError("exchange rate must be positive and finite")
        if self.rate_expires_at <= self.rate_as_of:
            raise ValueError("exchange-rate expiry must follow as_of")
        if self.approximate_fare.currency != self.user_budget.currency:
            raise ValueError("approximate fare must use the user's budget currency")
        if self.comparison_budget.currency == self.user_budget.currency:
            raise ValueError("cross-currency comparison budget must use the offer currency")
        return self


class TripInspirationRecommendation(DomainModel):
    rank: int = Field(ge=1, le=5)
    city: str = Field(min_length=1, max_length=160)
    country_code: CountryCode
    airport_codes: tuple[AirportCode, ...] = Field(min_length=1, max_length=5)
    lowest_verified_fare: Money
    retrieved_at: UTCInstant
    expires_at: UTCInstant
    application_offer_id: UUID
    search_id: UUID
    reason: str = Field(min_length=1, max_length=300)
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    budget_comparison: InspirationBudgetComparison | None = None

    @model_validator(mode="after")
    def validate_expiry_and_airports(self) -> Self:
        if self.expires_at <= self.retrieved_at:
            raise ValueError("inspiration recommendation expiry must follow retrieval")
        if len(set(self.airport_codes)) != len(self.airport_codes):
            raise ValueError("inspiration airport codes must be unique")
        return self


class TripInspirationPresentedOption(DomainModel):
    rank: int = Field(ge=1, le=5)
    application_offer_id: UUID
    search_id: UUID
    city: str = Field(min_length=1, max_length=160)
    airport_codes: tuple[AirportCode, ...] = Field(min_length=1, max_length=5)
    expires_at: UTCInstant


class TripInspirationCheckpoint(DomainModel):
    origin: AirportCode | None = None
    date_window: TravelDateWindow | None = None
    return_date: date | None = None
    airfare_budget: Money | None = None
    total_trip_budget: Money | None = None
    budget_scope: BudgetScope = BudgetScope.UNKNOWN
    budget_allocation: BudgetAllocation = "unknown"
    optimization: OptimizationPreference | None = None
    passengers: PassengerMix = Field(default_factory=PassengerMix)
    cabin: CabinClass = CabinClass.ECONOMY
    interests: tuple[str, ...] = Field(default_factory=tuple, max_length=5)
    destination_scope: str | None = Field(default=None, min_length=1, max_length=160)
    excluded_destinations: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    options: tuple[TripInspirationPresentedOption, ...] = Field(default_factory=tuple, max_length=5)
    expires_at: UTCInstant | None = None
    pending_clarification: TripInspirationPendingClarification | None = None
    pending_budget_amount: Decimal | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def validate_pending_budget(self) -> Self:
        currency_pending = {
            TripInspirationPendingClarification.BUDGET_CURRENCY,
            TripInspirationPendingClarification.AIRFARE_ALLOCATION_CURRENCY,
        }
        if (
            self.pending_budget_amount is not None
            and self.pending_clarification not in currency_pending
        ):
            raise ValueError("pending_budget_amount requires a pending currency clarification")
        if self.pending_clarification in currency_pending and self.pending_budget_amount is None:
            raise ValueError("currency clarification requires a pending budget amount")
        return self


class TripInspirationResult(DomainModel):
    action: Literal["trip_inspiration"] = "trip_inspiration"
    status: TripInspirationStatus
    constraints: TripInspirationConstraints | None = None
    recommendations: tuple[TripInspirationRecommendation, ...] = Field(
        default_factory=tuple,
        max_length=5,
    )
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    missing_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=5)
    question_vi: str | None = Field(default=None, max_length=1000)
    question_en: str | None = Field(default=None, max_length=1000)
    message_vi: str | None = Field(default=None, max_length=2000)
    message_en: str | None = Field(default=None, max_length=2000)
    no_result_reason: TripInspirationNoResultReason | None = None
    safe_error_code: str | None = Field(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9_]+$",
    )
    retryable: bool = False
    trace_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_status_payload(self) -> Self:
        if self.status is TripInspirationStatus.RESULTS and not self.recommendations:
            raise ValueError("results status requires recommendations")
        if self.status is not TripInspirationStatus.RESULTS and self.recommendations:
            raise ValueError("non-results inspiration statuses cannot contain recommendations")
        if self.status is TripInspirationStatus.CLARIFICATION_REQUIRED and not (
            self.question_vi and self.question_en
        ):
            raise ValueError("clarification results require localized questions")
        if self.status is TripInspirationStatus.NO_RESULTS:
            if self.no_result_reason is None:
                raise ValueError("no-results responses require a typed reason")
            if not (self.message_vi and self.message_en):
                raise ValueError("no-results responses require localized messages")
        elif self.no_result_reason is not None:
            raise ValueError("only no-results responses may contain a no-result reason")
        if self.status is TripInspirationStatus.PROVIDER_UNAVAILABLE:
            if self.safe_error_code is None:
                raise ValueError("provider-unavailable responses require a safe error code")
        elif (
            not (
                self.status is TripInspirationStatus.NO_RESULTS
                and self.no_result_reason
                is TripInspirationNoResultReason.CURRENCY_CONVERSION_UNAVAILABLE
                and self.safe_error_code == "currency_conversion_unavailable"
            )
            and self.safe_error_code is not None
        ):
            raise ValueError("safe error codes are restricted to provider or conversion failures")
        return self


__all__ = [
    "BudgetAllocation",
    "BudgetScope",
    "InspirationBudgetComparison",
    "DestinationIdea",
    "OptimizationPreference",
    "TripInspirationCandidateRequest",
    "TripInspirationCandidateResult",
    "TripInspirationCheckpoint",
    "TripInspirationCommand",
    "TripInspirationConstraints",
    "TripInspirationNoResultReason",
    "TripInspirationPresentedOption",
    "TripInspirationPendingClarification",
    "TripInspirationRecommendation",
    "TripInspirationResult",
    "TripInspirationStatus",
]
