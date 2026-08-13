from __future__ import annotations

from datetime import date, time
from enum import StrEnum
from typing import Any, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import Field, field_validator, model_validator

from agent_system.domain.accounts import CountryCode
from agent_system.domain.flights import (
    CabinClass,
    FlightSearchCriteria,
    PassengerMix,
    RepriceStatus,
)
from agent_system.domain.intents import AgentIntent
from agent_system.domain.limits import MAX_AGGREGATE_OFFERS
from agent_system.domain.ranking import SafeFlightOffer
from agent_system.domain.values import (
    AirportCode,
    CurrencyCode,
    DomainModel,
    ExecutionMode,
    UTCInstant,
)


class LocationKind(StrEnum):
    AIRPORT = "airport"
    CITY = "city"
    COUNTRY = "country"
    UNKNOWN = "unknown"


class DatePrecision(StrEnum):
    EXACT = "exact"
    RANGE = "range"
    WEEK = "week"
    FLEXIBLE = "flexible"


class ClarificationReason(StrEnum):
    MISSING_ORIGIN = "missing_origin"
    MISSING_DESTINATION = "missing_destination"
    MISSING_DATES = "missing_dates"
    AMBIGUOUS_DESTINATION = "ambiguous_destination"
    POSSIBLE_DESTINATION_TYPO = "possible_destination_typo"
    UNSUPPORTED_LOCATION = "unsupported_location"
    DATE_WINDOW_TOO_WIDE = "date_window_too_wide"
    PAST_DATE = "past_date"
    DYNAMIC_DESTINATION_CHOICES = "dynamic_destination_choices"
    DYNAMIC_DESTINATION_NOT_FOUND = "dynamic_destination_not_found"
    DYNAMIC_ORIGIN_NOT_FOUND = "dynamic_origin_not_found"
    LOCATION_PROVIDER_UNAVAILABLE = "location_provider_unavailable"


class DiscoveryStatus(StrEnum):
    CLARIFICATION_REQUIRED = "clarification_required"
    EXECUTABLE = "executable"


class LocationReference(DomainModel):
    kind: LocationKind
    normalized_name: str = Field(min_length=1, max_length=160)
    airport_candidates: tuple[AirportCode, ...] = Field(default_factory=tuple, max_length=10)
    country_code: CountryCode | None = None
    city_id: str | None = Field(default=None, min_length=1, max_length=120)

    @field_validator("normalized_name")
    @classmethod
    def normalize_name(cls, value: str) -> str:
        normalized = " ".join(value.strip().split())
        if not normalized:
            raise ValueError("location name cannot be blank")
        return normalized

    @field_validator("city_id")
    @classmethod
    def normalize_city_id(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        return normalized or None

    @model_validator(mode="after")
    def validate_candidates(self) -> Self:
        if len(set(self.airport_candidates)) != len(self.airport_candidates):
            raise ValueError("location airport candidates must be unique")
        if self.kind is LocationKind.AIRPORT and len(self.airport_candidates) != 1:
            raise ValueError("airport references require exactly one airport candidate")
        if self.kind is LocationKind.UNKNOWN and self.airport_candidates:
            raise ValueError("unknown locations cannot contain airport candidates")
        if self.kind is LocationKind.COUNTRY and len(self.airport_candidates) == 1:
            raise ValueError("country references cannot select one default airport")
        return self


class PendingDestinationConfirmation(DomainModel):
    original_text: str = Field(min_length=1, max_length=160)
    reference: LocationReference


class TravelDateWindow(DomainModel):
    start_date: date
    end_date: date
    precision: DatePrecision
    timezone: str = Field(min_length=1, max_length=64)
    parser_confidence: float = Field(default=1.0, ge=0, le=1)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("timezone cannot be blank")
        try:
            ZoneInfo(normalized)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {normalized}") from exc
        return normalized

    @model_validator(mode="after")
    def validate_window(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date cannot precede start_date")
        if (self.end_date - self.start_date).days + 1 > 7:
            raise ValueError("travel date windows cannot exceed seven inclusive days")
        return self


class TripDiscoveryCommand(DomainModel):
    intent: Literal[AgentIntent.TRIP_DISCOVERY] = AgentIntent.TRIP_DISCOVERY
    origin: LocationReference | None = None
    destination: LocationReference | None = None
    date_window: TravelDateWindow | None = None
    passengers: PassengerMix = Field(default_factory=PassengerMix)
    cabin: CabinClass = CabinClass.ECONOMY
    currency: CurrencyCode = "VND"
    max_stops: int | None = Field(default=None, ge=0, le=4)
    baggage_required: bool | None = None
    preferred_departure_start: time | None = None
    preferred_departure_end: time | None = None

    @model_validator(mode="after")
    def validate_preferences(self) -> Self:
        if (self.preferred_departure_start is None) != (self.preferred_departure_end is None):
            raise ValueError("preferred departure start and end must be provided together")
        return self


class ClarificationChoice(DomainModel):
    value: str = Field(min_length=1, max_length=80)
    label_vi: str = Field(min_length=1, max_length=160)
    label_en: str = Field(min_length=1, max_length=160)


class DynamicDestinationChoice(DomainModel):
    """A server-owned typed choice retained while a dynamic destination is pending."""

    value: str = Field(min_length=1, max_length=80)
    label_vi: str = Field(min_length=1, max_length=160)
    label_en: str = Field(min_length=1, max_length=160)
    reference: LocationReference


class DynamicDestinationChoices(DomainModel):
    source: Literal["catalog", "duffel", "fixture"]
    query_label: str | None = Field(default=None, min_length=1, max_length=160)
    expires_at: UTCInstant
    choices: tuple[DynamicDestinationChoice, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_choices(self) -> Self:
        if not self.choices:
            raise ValueError("dynamic destination choices cannot be empty")
        values = [choice.value for choice in self.choices]
        if len(values) != len(set(values)):
            raise ValueError("dynamic destination choice values must be unique")
        return self


class DynamicOriginChoice(DomainModel):
    """A server-owned typed choice retained while a dynamic origin is pending."""

    value: str = Field(min_length=1, max_length=80)
    label_vi: str = Field(min_length=1, max_length=160)
    label_en: str = Field(min_length=1, max_length=160)
    reference: LocationReference


class DynamicOriginChoices(DomainModel):
    source: Literal["catalog", "duffel", "fixture"]
    expires_at: UTCInstant
    choices: tuple[DynamicOriginChoice, ...] = Field(max_length=8)

    @model_validator(mode="after")
    def validate_choices(self) -> Self:
        if not self.choices:
            raise ValueError("dynamic origin choices cannot be empty")
        values = [choice.value for choice in self.choices]
        if len(values) != len(set(values)):
            raise ValueError("dynamic origin choice values must be unique")
        return self


class ClarificationRequired(DomainModel):
    status: Literal[DiscoveryStatus.CLARIFICATION_REQUIRED] = DiscoveryStatus.CLARIFICATION_REQUIRED
    reason: ClarificationReason
    missing_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=5)
    question_vi: str = Field(min_length=1, max_length=1000)
    question_en: str = Field(min_length=1, max_length=1000)
    choices: tuple[ClarificationChoice, ...] = Field(default_factory=tuple, max_length=10)


class ExecutableFlightSearch(DomainModel):
    status: Literal[DiscoveryStatus.EXECUTABLE] = DiscoveryStatus.EXECUTABLE
    resolved_origin: AirportCode
    destination_airports: tuple[AirportCode, ...] = Field(min_length=1, max_length=5)
    date_window: TravelDateWindow
    passengers: PassengerMix = Field(default_factory=PassengerMix)
    cabin: CabinClass = CabinClass.ECONOMY
    currency: CurrencyCode = "VND"
    max_stops: int | None = Field(default=None, ge=0, le=4)
    baggage_required: bool | None = None
    preferred_departure_start: time | None = None
    preferred_departure_end: time | None = None

    @model_validator(mode="after")
    def validate_destination_airports(self) -> Self:
        if len(set(self.destination_airports)) != len(self.destination_airports):
            raise ValueError("destination_airports must contain unique airport codes")
        if (self.preferred_departure_start is None) != (self.preferred_departure_end is None):
            raise ValueError("preferred departure start and end must be provided together")
        return self


DiscoveryResult = ClarificationRequired | ExecutableFlightSearch


class SearchAttemptOutcome(StrEnum):
    RESULTS = "results"
    NO_RESULTS = "no_results"
    PROVIDER_ERROR = "provider_error"


class TripDiscoveryStatus(StrEnum):
    RESULTS = "results"
    NO_RESULTS = "no_results"
    PROVIDER_UNAVAILABLE = "provider_unavailable"


class FlightSearchAttempt(DomainModel):
    """Safe, durable facts about one exact provider search."""

    criteria: FlightSearchCriteria
    provider: str = Field(min_length=1, max_length=80)
    environment: ExecutionMode
    outcome: SearchAttemptOutcome
    result_count: int = Field(ge=0)
    safe_error_code: str | None = Field(default=None, min_length=1, max_length=80)
    started_at: UTCInstant
    completed_at: UTCInstant

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_attempt(self) -> Self:
        if self.completed_at < self.started_at:
            raise ValueError("search attempt completed_at cannot precede started_at")
        if self.outcome is SearchAttemptOutcome.PROVIDER_ERROR and not self.safe_error_code:
            raise ValueError("provider errors require a bounded safe error code")
        if self.outcome is not SearchAttemptOutcome.PROVIDER_ERROR and self.safe_error_code:
            raise ValueError("successful search attempts cannot contain safe provider errors")
        return self


class TripDiscoverySearchResult(DomainModel):
    """The single safe result contract shared by HTTP and chat search paths."""

    action: Literal["trip_discovery"] = "trip_discovery"
    status: TripDiscoveryStatus
    discovery_id: UUID
    search_id: UUID | None = None
    resolved_request: dict[str, Any]
    attempts: tuple[FlightSearchAttempt, ...] = Field(default_factory=tuple, max_length=20)
    offers: tuple[SafeFlightOffer, ...] = Field(
        default_factory=tuple,
        max_length=MAX_AGGREGATE_OFFERS,
    )
    warnings: tuple[str, ...] = Field(default_factory=tuple, max_length=100)
    retryable: bool = False
    trace_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_status(self) -> Self:
        if self.status is TripDiscoveryStatus.RESULTS and not self.offers:
            raise ValueError("results status requires at least one safe offer")
        if self.status is TripDiscoveryStatus.NO_RESULTS and self.offers:
            raise ValueError("no_results status cannot contain offers")
        if self.status is TripDiscoveryStatus.PROVIDER_UNAVAILABLE and self.offers:
            raise ValueError("provider_unavailable status cannot contain offers")
        return self


class TripDiscoveryRepriceResult(DomainModel):
    status: RepriceStatus
    repriced_offer: SafeFlightOffer | None = None
    reason: str | None = Field(default=None, max_length=1000)
    trace_id: str = Field(min_length=1, max_length=160)

    @model_validator(mode="after")
    def validate_reprice(self) -> Self:
        has_offer = self.repriced_offer is not None
        if self.status in {RepriceStatus.UNCHANGED, RepriceStatus.CHANGED} and not has_offer:
            raise ValueError("successful repricing requires a safe offer")
        if self.status in {RepriceStatus.UNAVAILABLE, RepriceStatus.EXPIRED} and has_offer:
            raise ValueError("unavailable or expired repricing cannot contain an offer")
        return self
