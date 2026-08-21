from __future__ import annotations

from datetime import date, time
from typing import Annotated, Literal, Self
from uuid import UUID

from pydantic import Field, JsonValue, TypeAdapter, field_validator, model_validator

from agent_system.domain.flights import CabinClass, PassengerMix
from agent_system.domain.intents import AgentIntent
from agent_system.domain.location_resolution import normalize_location_query
from agent_system.domain.optimization import (
    OptimizationBudgetRelation,
    OptimizationDirection,
    OptimizationMetric,
)
from agent_system.domain.trip_discovery import TripDiscoveryCommand
from agent_system.domain.trip_inspiration import TripInspirationCommand
from agent_system.domain.values import AirportCode, CurrencyCode, DomainModel


class SearchFlightsCommand(DomainModel):
    intent: Literal[AgentIntent.SEARCH_FLIGHTS] = AgentIntent.SEARCH_FLIGHTS
    origin: AirportCode
    destination: AirportCode
    departure_date: date
    return_date: date | None = None
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


class InspirationOptionReference(DomainModel):
    rank: int = Field(ge=1, le=5)


class SearchInspirationOptionCommand(DomainModel):
    intent: Literal[AgentIntent.SEARCH_INSPIRATION_OPTION] = AgentIntent.SEARCH_INSPIRATION_OPTION
    option: InspirationOptionReference


class AdviseCommand(DomainModel):
    intent: Literal[AgentIntent.ADVISE] = AgentIntent.ADVISE
    question: str = Field(min_length=1, max_length=4000)
    offer_id: UUID | None = None


class PresentedOfferReference(DomainModel):
    search_id: UUID
    rank: int = Field(ge=1, le=20)


class StartBookingCommand(DomainModel):
    intent: Literal[AgentIntent.START_BOOKING] = AgentIntent.START_BOOKING
    offer_id: UUID | None = None
    presented_offer: PresentedOfferReference | None = None
    inspiration_option: InspirationOptionReference | None = None
    traveler_profile_ids: tuple[UUID, ...] = Field(default_factory=tuple, max_length=9)

    @model_validator(mode="after")
    def validate_one_offer_reference(self) -> Self:
        references = sum(
            reference is not None
            for reference in (self.offer_id, self.presented_offer, self.inspiration_option)
        )
        if references > 1:
            raise ValueError("booking offer references are mutually exclusive")
        return self


class ConfirmBookingCommand(DomainModel):
    intent: Literal[AgentIntent.CONFIRM_BOOKING] = AgentIntent.CONFIRM_BOOKING
    booking_intent_id: UUID | None = None


class ManageBookingCommand(DomainModel):
    intent: Literal[AgentIntent.MANAGE_BOOKING] = AgentIntent.MANAGE_BOOKING
    booking_id: UUID | None = None
    action: Literal["view", "cancel", "refund"] = "view"


class CreateWatchCommand(DomainModel):
    intent: Literal[AgentIntent.CREATE_WATCH] = AgentIntent.CREATE_WATCH
    origin: AirportCode
    destination: AirportCode
    departure_date_from: date
    departure_date_to: date
    maximum_total: str | None = Field(default=None, pattern=r"^[0-9]+(?:\.[0-9]{1,2})?$")
    currency: CurrencyCode = "VND"
    auto_buy_requested: bool = False


class ManageWatchCommand(DomainModel):
    intent: Literal[AgentIntent.MANAGE_WATCH] = AgentIntent.MANAGE_WATCH
    watch_id: UUID | None = None
    action: Literal["view", "pause", "resume", "cancel"] = "view"


class UpdateProfileCommand(DomainModel):
    intent: Literal[AgentIntent.UPDATE_PROFILE] = AgentIntent.UPDATE_PROFILE
    traveler_profile_id: UUID | None = None
    requested_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    explicit_save_consent: bool = False


class UnclearCommand(DomainModel):
    intent: Literal[AgentIntent.UNCLEAR] = AgentIntent.UNCLEAR
    reason: str = Field(min_length=1, max_length=1000)
    missing_fields: tuple[str, ...] = Field(default_factory=tuple, max_length=20)


AgentCommand = Annotated[
    SearchFlightsCommand
    | TripDiscoveryCommand
    | TripInspirationCommand
    | SearchInspirationOptionCommand
    | AdviseCommand
    | StartBookingCommand
    | ConfirmBookingCommand
    | ManageBookingCommand
    | CreateWatchCommand
    | ManageWatchCommand
    | UpdateProfileCommand
    | UnclearCommand,
    Field(discriminator="intent"),
]

COMMAND_ADAPTER = TypeAdapter(AgentCommand)


class PlanningMessage(DomainModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=50_000)


DialogueAct = Literal["request", "answer", "affirm", "reject", "question", "other"]
ConstraintUpdateOperation = Literal["none", "set", "replace", "clear"]
ConversationAction = Literal[
    "none",
    "answer_pending",
    "continue_pending",
    "accept_clarification",
    "reject_clarification",
    "update_constraints",
    "refine_search",
    "reference_presented_result",
    "accept_any_destination",
    "request_alternatives",
]

TemporalKind = Literal[
    "today",
    "tomorrow",
    "this_week",
    "next_week",
    "this_weekend",
    "next_weekend",
    "weekday",
    "relative_days",
    "explicit_date_text",
    "explicit_range_text",
    "unknown",
]
TemporalFlexibility = Literal["exact", "any_day", "range", "around", "unknown"]
WeekdayName = Literal[
    "monday",
    "tuesday",
    "wednesday",
    "thursday",
    "friday",
    "saturday",
    "sunday",
]


class TemporalSemantic(DomainModel):
    operation: ConstraintUpdateOperation = "none"
    kind: TemporalKind = "unknown"
    flexibility: TemporalFlexibility = "unknown"
    weekday: WeekdayName | None = None
    week_offset: int | None = Field(default=None, ge=0, le=1)
    relative_days: int | None = Field(default=None, ge=1, le=365)
    source_text: str | None = Field(default=None, max_length=160)
    confidence: float = Field(default=0, ge=0, le=1)

    @field_validator("source_text")
    @classmethod
    def normalize_source_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.kind == "weekday" and self.weekday is None:
            raise ValueError("weekday temporal semantics require weekday")
        if self.kind != "weekday" and self.weekday is not None:
            raise ValueError("weekday is only valid for weekday temporal semantics")
        if self.kind == "relative_days" and self.relative_days is None:
            raise ValueError("relative_days semantics require relative_days")
        if self.kind != "relative_days" and self.relative_days is not None:
            raise ValueError("relative_days is only valid for relative_days semantics")
        if self.kind != "weekday" and self.week_offset is not None:
            raise ValueError("week_offset is only valid for weekday semantics")
        if self.operation == "clear" and (
            self.kind != "unknown"
            or self.flexibility != "unknown"
            or self.weekday is not None
            or self.week_offset is not None
            or self.relative_days is not None
        ):
            raise ValueError("clearing temporal semantics cannot contain a temporal value")
        return self


BudgetMode = Literal["exact", "approximately", "maximum", "increase_by", "unknown"]
BudgetAllocation = Literal["group_total", "per_person", "unknown"]


class BudgetSemantic(DomainModel):
    operation: ConstraintUpdateOperation = "none"
    amount_text: str | None = Field(default=None, max_length=80)
    currency_hint: str | None = Field(default=None, max_length=20)
    mode: BudgetMode = "unknown"
    allocation: BudgetAllocation = "unknown"
    scope: Literal["airfare_only", "total_trip", "unknown"] = "unknown"
    source_text: str | None = Field(default=None, max_length=160)
    confidence: float = Field(default=0, ge=0, le=1)

    @field_validator("amount_text", "currency_hint", "source_text")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.operation == "clear" and (
            self.amount_text is not None
            or self.currency_hint is not None
            or self.mode != "unknown"
            or self.allocation != "unknown"
            or self.scope != "unknown"
        ):
            raise ValueError("clearing a budget cannot contain a budget value")
        return self


class PassengerSemantic(DomainModel):
    operation: ConstraintUpdateOperation = "none"
    adults: int | None = Field(default=None, ge=0, le=9)
    children: int | None = Field(default=None, ge=0, le=9)
    infants: int | None = Field(default=None, ge=0, le=9)
    total_only: int | None = Field(default=None, ge=1, le=9)
    source_text: str | None = Field(default=None, max_length=160)
    confidence: float = Field(default=0, ge=0, le=1)

    @field_validator("source_text")
    @classmethod
    def normalize_source_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        composition = (self.adults, self.children, self.infants)
        if self.total_only is not None and any(value is not None for value in composition):
            raise ValueError("total_only cannot be combined with passenger composition")
        if self.operation == "clear" and (
            self.total_only is not None or any(value is not None for value in composition)
        ):
            raise ValueError("clearing passengers cannot contain passenger values")
        return self


DestinationMode = Literal[
    "specific",
    "anywhere",
    "anywhere_within_scope",
    "international_only",
    "domestic_only",
    "exclude_previous",
    "unknown",
]
DestinationInterest = Literal["beach", "food", "culture", "nature", "shopping", "history"]


class DestinationSemantic(DomainModel):
    operation: ConstraintUpdateOperation = "none"
    mode: DestinationMode = "unknown"
    scope_query: str | None = Field(default=None, max_length=160)
    place_query: str | None = Field(default=None, max_length=160)
    excluded_place_queries: tuple[str, ...] = Field(default_factory=tuple, max_length=10)
    interests: tuple[DestinationInterest, ...] = Field(default_factory=tuple, max_length=5)
    source_text: str | None = Field(default=None, max_length=160)
    confidence: float = Field(default=0, ge=0, le=1)

    @field_validator("scope_query", "place_query", "source_text")
    @classmethod
    def normalize_query(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @field_validator("excluded_place_queries")
    @classmethod
    def normalize_exclusions(cls, values: tuple[str, ...]) -> tuple[str, ...]:
        normalized: list[str] = []
        for value in values:
            item = " ".join(value.strip().split())
            if item and item.casefold() not in {existing.casefold() for existing in normalized}:
                normalized.append(item)
        return tuple(normalized)

    @model_validator(mode="after")
    def validate_shape(self) -> Self:
        if self.mode == "specific" and not self.place_query:
            raise ValueError("specific destination semantics require place_query")
        if self.mode == "anywhere_within_scope" and not self.scope_query:
            raise ValueError("scoped destination semantics require scope_query")
        if self.operation == "clear" and (
            self.mode != "unknown"
            or self.scope_query is not None
            or self.place_query is not None
            or self.excluded_place_queries
            or self.interests
        ):
            raise ValueError("clearing destination cannot contain a destination value")
        return self


class OptimizationSemantic(DomainModel):
    """Natural-language optimization normalized into safe ranking dimensions."""

    metric: OptimizationMetric
    direction: OptimizationDirection
    budget_relation: OptimizationBudgetRelation = "ignore"
    source_text: str | None = Field(default=None, max_length=160)
    confidence: float = Field(default=0, ge=0, le=1)

    @field_validator("source_text")
    @classmethod
    def normalize_source_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None


class SearchRefinementSemantic(DomainModel):
    operation: ConstraintUpdateOperation = "none"
    direct_only: bool | None = None
    cabin: Literal["economy", "premium_economy", "business", "first"] | None = None
    time_of_day: Literal["morning", "afternoon", "evening", "night"] | None = None
    checked_baggage_required: bool | None = None
    optimization: OptimizationSemantic | None = None
    # Kept for planner/checkpoint compatibility with earlier semantic versions.
    sort_preference: (
        Literal["cheapest", "shortest", "fewest_stops", "earliest", "latest"] | None
    ) = None
    source_text: str | None = Field(default=None, max_length=160)
    confidence: float = Field(default=0, ge=0, le=1)

    @field_validator("source_text")
    @classmethod
    def normalize_source_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None

    @model_validator(mode="after")
    def validate_clear(self) -> Self:
        if self.operation == "clear" and any(
            value is not None
            for value in (
                self.direct_only,
                self.cabin,
                self.time_of_day,
                self.checked_baggage_required,
                self.optimization,
                self.sort_preference,
            )
        ):
            raise ValueError("clearing search refinements cannot contain a refinement value")
        return self


class PresentedResultReferenceSemantic(DomainModel):
    rank: int | None = Field(default=None, ge=1, le=20)
    descriptor: Literal[
        "cheapest", "shortest", "fewest_stops", "morning", "previous", "unknown"
    ] = "unknown"
    destination_query: str | None = Field(default=None, max_length=160)
    source_text: str | None = Field(default=None, max_length=160)
    confidence: float = Field(default=0, ge=0, le=1)

    @field_validator("destination_query", "source_text")
    @classmethod
    def normalize_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None


class SemanticConstraintUpdates(DomainModel):
    temporal: TemporalSemantic | None = None
    budget: BudgetSemantic | None = None
    passengers: PassengerSemantic | None = None
    origin: DestinationSemantic | None = None
    destination: DestinationSemantic | None = None
    search: SearchRefinementSemantic | None = None
    result_reference: PresentedResultReferenceSemantic | None = None


class PlanningLocationCandidate(DomainModel):
    candidate_id: str = Field(min_length=1, max_length=160)
    kind: Literal["airport", "city", "country"]
    canonical_name: str = Field(min_length=1, max_length=160)
    airport_codes: tuple[AirportCode, ...] = Field(default_factory=tuple, max_length=10)


class PlanningPendingClarification(DomainModel):
    clarification_type: Literal["destination"]
    candidate_id: str = Field(min_length=1, max_length=160)
    canonical_name: str = Field(min_length=1, max_length=160)


class InterpretedLocation(DomainModel):
    candidate_id: str | None = Field(default=None, max_length=160)
    source_text: str | None = Field(default=None, max_length=160)
    canonical_query: str | None = Field(default=None, min_length=1, max_length=160)
    kind_guess: Literal["airport", "city", "country", "region", "unknown"] = "unknown"
    interpretation: Literal["exact", "probable", "uncertain", "unknown"] = "unknown"
    confidence: float = Field(default=0, ge=0, le=1)

    @field_validator("canonical_query")
    @classmethod
    def normalize_query(cls, value: str | None) -> str | None:
        return normalize_location_query(value) if value is not None else None

    @field_validator("source_text")
    @classmethod
    def normalize_source_text(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        if not normalized or len(normalized) > 160:
            raise ValueError("source text cannot be blank or exceed 160 characters")
        return normalized

    @model_validator(mode="after")
    def validate_candidate(self) -> Self:
        if self.candidate_id is not None and self.canonical_query is not None:
            raise ValueError("candidate ID and canonical query are mutually exclusive")
        if self.candidate_id is not None:
            if self.interpretation == "unknown":
                raise ValueError("a local candidate requires an interpretation")
            return self
        if self.canonical_query is not None:
            if self.interpretation == "unknown":
                raise ValueError("dynamic location queries require an interpretation")
            return self
        if self.interpretation != "unknown" or self.kind_guess != "unknown":
            raise ValueError("unknown locations cannot contain semantic interpretation fields")
        return self


class PlanningRequest(DomainModel):
    current_message: str = Field(min_length=1, max_length=50_000)
    locale: Literal["vi", "en"]
    recent_messages: tuple[PlanningMessage, ...] = Field(default_factory=tuple, max_length=24)
    safe_summary: str | None = Field(default=None, max_length=12_000)
    safe_preferences: dict[str, JsonValue] = Field(default_factory=dict)
    allowed_locations: tuple[PlanningLocationCandidate, ...] = Field(
        default_factory=tuple,
        max_length=100,
    )
    pending_clarification: PlanningPendingClarification | None = None
    pending_field: Literal["origin", "destination", "travel_dates"] | None = None
    selected_offer_id: UUID | None = None
    presented_search_id: UUID | None = None

    booking_intent_id: UUID | None = None
    watch_draft_id: UUID | None = None

    @model_validator(mode="after")
    def validate_allowed_location_ids(self) -> Self:
        candidate_ids = [candidate.candidate_id for candidate in self.allowed_locations]
        if len(candidate_ids) != len(set(candidate_ids)):
            raise ValueError("allowed location candidate IDs must be unique")
        return self


class PlanResult(DomainModel):
    command: AgentCommand
    language: Literal["vi", "en"]
    plan: tuple[str, ...] = Field(default_factory=tuple, max_length=12)

    dialogue_act: DialogueAct = "other"
    interpreted_destination: InterpretedLocation | None = None
    conversation_action: ConversationAction = "none"
    destination_scope: str | None = Field(default=None, min_length=1, max_length=160)
    semantic_updates: SemanticConstraintUpdates = Field(default_factory=SemanticConstraintUpdates)

    @field_validator("destination_scope")
    @classmethod
    def normalize_destination_scope(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = " ".join(value.strip().split())
        return normalized or None


class AdviceRequest(DomainModel):
    question: str = Field(min_length=1, max_length=4000)
    language: Literal["vi", "en"]
    recent_messages: tuple[PlanningMessage, ...] = Field(default_factory=tuple, max_length=24)
    safe_summary: str | None = Field(default=None, max_length=12_000)
    safe_context: dict = Field(default_factory=dict)


class AdviceResult(DomainModel):
    text: str = Field(min_length=1, max_length=12_000)
    limitations: tuple[str, ...] = Field(default_factory=tuple, max_length=12)

    @field_validator("limitations", mode="before")
    @classmethod
    def _normalize_limitations(cls, value: Any) -> tuple[str, ...]:
        if value is None or value == "":
            return ()
        if isinstance(value, str):
            return (value,)
        if isinstance(value, (list, tuple, set)):
            return tuple(str(x) for x in value if x)
        return ()


class LLMCallMetadata(DomainModel):
    provider: str = Field(min_length=1, max_length=80)
    model: str = Field(min_length=1, max_length=200)
    prompt_version: str = Field(min_length=1, max_length=80)
    latency_ms: float = Field(ge=0)
    input_tokens: int | None = Field(default=None, ge=0)
    output_tokens: int | None = Field(default=None, ge=0)
    outcome: Literal["success", "invalid_output", "unavailable"]
