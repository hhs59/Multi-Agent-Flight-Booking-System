from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Literal

from pydantic import Field, StringConstraints, field_validator, model_validator

from agent_system.domain.accounts import CountryCode
from agent_system.domain.values import AirportCode, DomainModel, UTCInstant

LocaleCode = Literal["vi", "en"]
PlaceCategory = Annotated[
    str,
    StringConstraints(min_length=1, max_length=40, pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$"),
]

ALLOWED_PLACE_INTERESTS = frozenset(
    {
        "adventure",
        "architecture",
        "art",
        "beach",
        "boat-trip",
        "culture",
        "family",
        "food",
        "history",
        "local-food",
        "market",
        "museum",
        "nature",
        "nightlife",
        "outdoors",
        "relaxation",
        "shopping",
        "viewpoint",
        "wellness",
    }
)


class PlaceSourceEnvironment(StrEnum):
    CURATED = "curated"
    AI_GENERATED = "ai_generated"
    MOCK = "mock"
    SANDBOX = "sandbox"
    PRODUCTION = "production"


class BudgetCategory(StrEnum):
    BUDGET = "budget"
    STANDARD = "standard"
    PREMIUM = "premium"


class Pace(StrEnum):
    RELAXED = "relaxed"
    BALANCED = "balanced"
    BUSY = "busy"


def _normalize_interests(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        values = (value,)
    else:
        try:
            values = tuple(value)
        except TypeError as exc:
            raise ValueError("interests must be a bounded sequence") from exc
    normalized: list[str] = []
    for item in values:
        if not isinstance(item, str):
            raise ValueError("interests must contain strings")
        interest = item.strip().casefold()
        if interest not in ALLOWED_PLACE_INTERESTS:
            raise ValueError(f"unsupported place interest: {interest}")
        if interest not in normalized:
            normalized.append(interest)
    if len(normalized) > 8:
        raise ValueError("at most eight place interests are allowed")
    return tuple(normalized)


class PlaceCandidate(DomainModel):
    """A bounded, source-owned place candidate safe to cross the application boundary."""

    place_id: str = Field(
        min_length=1,
        max_length=160,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]*$",
    )
    destination_airport: AirportCode
    city_code: str = Field(min_length=2, max_length=16, pattern=r"^[A-Z0-9_-]+$")
    country_code: CountryCode
    name: str = Field(min_length=1, max_length=160)
    categories: tuple[PlaceCategory, ...] = Field(min_length=1, max_length=8)
    latitude: Decimal | None = Field(default=None, ge=Decimal("-90"), le=Decimal("90"))
    longitude: Decimal | None = Field(default=None, ge=Decimal("-180"), le=Decimal("180"))
    short_facts: tuple[str, ...] = Field(default_factory=tuple, max_length=5)
    verified_rating: Decimal | None = Field(default=None, ge=Decimal("0"), le=Decimal("5"))
    opening_summary: str | None = Field(default=None, max_length=300)
    facts_as_of: UTCInstant | None = None
    source_name: str = Field(min_length=1, max_length=80)
    source_url: str | None = Field(default=None, max_length=2048)
    environment: PlaceSourceEnvironment
    is_live: bool = False
    retrieved_at: UTCInstant
    expires_at: UTCInstant | None = None

    @field_validator("short_facts")
    @classmethod
    def validate_facts(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        if any(not item.strip() or len(item) > 600 for item in value):
            raise ValueError("short facts must be bounded non-empty text")
        return tuple(item.strip() for item in value)

    @field_validator("opening_summary")
    @classmethod
    def normalize_opening_summary(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @model_validator(mode="after")
    def validate_provenance(self) -> PlaceCandidate:
        if self.environment is PlaceSourceEnvironment.CURATED and self.is_live:
            raise ValueError("curated candidates cannot be marked live")
        if self.is_live and self.environment is not PlaceSourceEnvironment.PRODUCTION:
            raise ValueError("only production candidates can be marked live")
        if self.expires_at is not None and self.expires_at <= self.retrieved_at:
            raise ValueError("candidate expiry must be later than retrieval")
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be provided together")
        return self


class RecommendationPreferences(DomainModel):
    locale: LocaleCode = "en"
    travel_start_date: date | None = None
    travel_end_date: date | None = None
    interests: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    budget_category: BudgetCategory | None = None
    pace: Pace | None = None
    maximum_places: int = Field(default=5, ge=1, le=20)

    @field_validator("interests", mode="before")
    @classmethod
    def validate_interests(cls, value: Any) -> tuple[str, ...]:
        return _normalize_interests(value)

    @model_validator(mode="after")
    def validate_dates(self) -> RecommendationPreferences:
        if (self.travel_start_date is None) != (self.travel_end_date is None):
            raise ValueError("travel start and end dates must be provided together")
        if (
            self.travel_start_date is not None
            and self.travel_end_date is not None
            and self.travel_end_date < self.travel_start_date
        ):
            raise ValueError("travel end date cannot precede travel start date")
        return self


class PlaceSearchRequest(DomainModel):
    """Normalized input shared by every places adapter."""

    destination_airport: AirportCode
    city_code: str = Field(min_length=2, max_length=16, pattern=r"^[A-Z0-9_-]+$")
    country_code: CountryCode
    travel_start_date: date | None = None
    travel_end_date: date | None = None
    locale: LocaleCode = "en"
    interests: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    limit: int = Field(default=5, ge=1, le=20)
    deadline: UTCInstant

    @field_validator("interests", mode="before")
    @classmethod
    def validate_interests(cls, value: Any) -> tuple[str, ...]:
        return _normalize_interests(value)

    @model_validator(mode="after")
    def validate_dates(self) -> PlaceSearchRequest:
        if (self.travel_start_date is None) != (self.travel_end_date is None):
            raise ValueError("travel start and end dates must be provided together")
        if (
            self.travel_start_date is not None
            and self.travel_end_date is not None
            and self.travel_end_date < self.travel_start_date
        ):
            raise ValueError("travel end date cannot precede travel start date")
        return self


PlacesProviderRequest = PlaceSearchRequest


class PlaceSuggestionRequest(DomainModel):
    destination_airport: AirportCode
    destination_label: str = Field(min_length=1, max_length=200)
    travel_start_date: date | None = None
    travel_end_date: date | None = None
    locale: LocaleCode = "en"
    interests: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    budget_category: BudgetCategory | None = None
    pace: Pace | None = None
    maximum_places: int = Field(default=5, ge=1, le=10)

    @field_validator("interests", mode="before")
    @classmethod
    def validate_interests(cls, value: Any) -> tuple[str, ...]:
        return _normalize_interests(value)

    @model_validator(mode="after")
    def validate_dates(self) -> PlaceSuggestionRequest:
        if (self.travel_start_date is None) != (self.travel_end_date is None):
            raise ValueError("travel start and end dates must be provided together")
        if (
            self.travel_start_date is not None
            and self.travel_end_date is not None
            and self.travel_end_date < self.travel_start_date
        ):
            raise ValueError("travel end date cannot precede travel start date")
        return self


class PlaceSuggestion(DomainModel):
    name: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=300)
    categories: tuple[PlaceCategory, ...] = Field(min_length=1, max_length=3)


class PlaceSuggestionResult(DomainModel):
    city: str = Field(min_length=1, max_length=160)
    country: str = Field(min_length=1, max_length=160)
    country_code: CountryCode
    suggestions: tuple[PlaceSuggestion, ...] = Field(default_factory=tuple, max_length=10)

    @model_validator(mode="after")
    def unique_names(self) -> PlaceSuggestionResult:
        normalized = [item.name.strip().casefold() for item in self.suggestions]
        if len(normalized) != len(set(normalized)):
            raise ValueError("generated place suggestion names must be unique")
        return self


class PlaceRankingCandidate(DomainModel):
    place_id: str = Field(min_length=1, max_length=160)
    name: str = Field(min_length=1, max_length=160)
    categories: tuple[PlaceCategory, ...] = Field(default_factory=tuple, max_length=8)
    short_facts: tuple[str, ...] = Field(default_factory=tuple, max_length=3)


class PlaceRankingRequest(DomainModel):
    destination_label: str = Field(min_length=1, max_length=160)
    travel_start_date: date | None = None
    travel_end_date: date | None = None
    locale: LocaleCode = "en"
    interests: tuple[str, ...] = Field(default_factory=tuple, max_length=8)
    budget_category: BudgetCategory | None = None
    pace: Pace | None = None
    candidates: tuple[PlaceRankingCandidate, ...] = Field(min_length=1, max_length=20)

    @field_validator("interests", mode="before")
    @classmethod
    def validate_interests(cls, value: Any) -> tuple[str, ...]:
        return _normalize_interests(value)

    @model_validator(mode="after")
    def validate_dates(self) -> PlaceRankingRequest:
        if (self.travel_start_date is None) != (self.travel_end_date is None):
            raise ValueError("travel start and end dates must be provided together")
        if (
            self.travel_start_date is not None
            and self.travel_end_date is not None
            and self.travel_end_date < self.travel_start_date
        ):
            raise ValueError("travel end date cannot precede travel start date")
        return self


class PlaceRankingSelection(DomainModel):
    place_id: str = Field(min_length=1, max_length=160)
    reason: str = Field(min_length=1, max_length=240)


class PlaceRankingResult(DomainModel):
    selections: tuple[PlaceRankingSelection, ...] = Field(default_factory=tuple, max_length=20)


class RecommendedPlace(DomainModel):
    candidate: PlaceCandidate
    rank: int = Field(ge=1, le=20)
    reason: str = Field(min_length=1, max_length=500)


class DestinationRecommendationStatus(StrEnum):
    COMPLETED = "completed"
    NO_RESULTS = "no_results"
    UNSUPPORTED_DESTINATION = "unsupported_destination"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TIMED_OUT = "timed_out"
    DISABLED = "disabled"


class DestinationRecommendationResult(DomainModel):
    status: DestinationRecommendationStatus
    destination_airport: AirportCode
    city: str = Field(min_length=1, max_length=160)
    country: str = Field(min_length=1, max_length=160)
    places: tuple[RecommendedPlace, ...] = Field(default_factory=tuple, max_length=20)
    source_labels: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    retrieved_at: UTCInstant | None = None
    advisory_notice: str = Field(min_length=1, max_length=500)
    trace_id: str = Field(min_length=1, max_length=160)
    retryable: bool = False
    # Kept as a small compatibility projection for clients from the pre-Phase-5 API.
    source: str | None = Field(default=None, min_length=1, max_length=80)


class PlaceRecommendation(DomainModel):
    """Legacy prose-only shape retained for import compatibility."""

    name: str = Field(min_length=1, max_length=160)
    description: str = Field(min_length=1, max_length=500)
    categories: tuple[str, ...] = Field(default_factory=tuple, max_length=5)


# Old imports continue to resolve while all new callers use the typed result above.
DestinationRecommendations = DestinationRecommendationResult


__all__ = [
    "ALLOWED_PLACE_INTERESTS",
    "BudgetCategory",
    "DestinationRecommendationResult",
    "DestinationRecommendationStatus",
    "DestinationRecommendations",
    "LocaleCode",
    "Pace",
    "PlaceCandidate",
    "PlaceRankingCandidate",
    "PlaceRankingRequest",
    "PlaceRankingResult",
    "PlaceRankingSelection",
    "PlaceRecommendation",
    "PlaceSearchRequest",
    "PlaceSuggestion",
    "PlaceSuggestionRequest",
    "PlaceSuggestionResult",
    "PlaceSourceEnvironment",
    "PlacesProviderRequest",
    "RecommendedPlace",
    "RecommendationPreferences",
]
