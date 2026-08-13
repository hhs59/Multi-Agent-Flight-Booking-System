from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import AliasChoices, Field, field_validator, model_validator

from agent_system.domain.flights import BaggageAllowance, CabinClass, FareConditions
from agent_system.domain.limits import MAX_CLIENT_OFFERS
from agent_system.domain.values import (
    AirportCode,
    CarrierCode,
    CurrencyCode,
    DomainModel,
    ExecutionMode,
    UTCInstant,
)

_SCORE_QUANTUM = Decimal("0.000001")


class RankingReason(StrEnum):
    LOWEST_TOTAL = "lowest_total"
    SHORTER_DURATION = "shorter_duration"
    NONSTOP = "nonstop"
    FEWER_STOPS = "fewer_stops"
    BAGGAGE_INCLUDED = "baggage_included"
    DEPARTURE_TIME_MATCH = "departure_time_match"


class SafeFlightSegment(DomainModel):
    origin: AirportCode
    destination: AirportCode
    departure_at: UTCInstant
    arrival_at: UTCInstant
    flight_number: str = Field(min_length=3, max_length=12, pattern=r"^[A-Z0-9]+$")


class SafeFlightOffer(DomainModel):
    """Application-owned flight facts safe to return to a client or persist for replay."""

    offer_id: UUID = Field(
        validation_alias=AliasChoices("offer_id", "id"),
        serialization_alias="offer_id",
    )
    origin: AirportCode
    destination: AirportCode
    departure_at: UTCInstant
    arrival_at: UTCInstant
    duration_minutes: int = Field(ge=0, le=10_000)
    stops: int = Field(ge=0, le=20)
    flight_numbers: tuple[str, ...] = Field(default_factory=tuple, max_length=20)
    carrier: CarrierCode
    cabin: CabinClass
    total: Decimal
    currency: CurrencyCode
    baggage: BaggageAllowance
    fare_conditions: FareConditions
    provider: str = Field(min_length=1, max_length=80)
    environment: ExecutionMode
    is_live: bool
    retrieved_at: UTCInstant
    expires_at: UTCInstant
    segments: tuple[SafeFlightSegment, ...] = Field(default_factory=tuple, max_length=20)

    @field_validator("total", mode="before")
    @classmethod
    def reject_binary_float(cls, value: object) -> object:
        if isinstance(value, float):
            raise TypeError("SafeFlightOffer.total must use Decimal, int, or a decimal string")
        return value

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @field_validator("total")
    @classmethod
    def validate_total(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("safe offer total must be finite")
        return value

    @model_validator(mode="after")
    def validate_offer(self) -> SafeFlightOffer:
        if self.arrival_at <= self.departure_at:
            raise ValueError("safe offer arrival must be later than departure")
        if self.expires_at <= self.retrieved_at:
            raise ValueError("safe offer expiry must be later than retrieval")
        if self.is_live != (self.environment is ExecutionMode.PRODUCTION):
            raise ValueError("safe offer is_live must match its environment")
        if self.total < 0:
            raise ValueError("safe offer total cannot be negative")
        if self.segments and (
            self.segments[0].origin != self.origin
            or self.segments[-1].destination != self.destination
        ):
            raise ValueError("safe offer route must match its segments")
        if (
            self.flight_numbers
            and self.segments
            and tuple(segment.flight_number for segment in self.segments) != self.flight_numbers
        ):
            raise ValueError("safe offer flight numbers must match its segments")
        return self


class RankingScoreComponent(DomainModel):
    name: str = Field(min_length=1, max_length=80)
    raw_value: str | int
    normalized_score: Decimal
    weight: Decimal
    weighted_score: Decimal

    @field_validator("normalized_score", "weight", "weighted_score", mode="before")
    @classmethod
    def reject_binary_float(cls, value: object) -> object:
        if isinstance(value, float):
            raise TypeError("ranking scores must use Decimal, not binary floats")
        return value

    @field_validator("normalized_score", "weight", "weighted_score")
    @classmethod
    def validate_score(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0 or value > 1:
            raise ValueError("ranking score values must be between 0 and 1")
        return value.quantize(_SCORE_QUANTUM)

    @model_validator(mode="after")
    def validate_weighted_score(self) -> RankingScoreComponent:
        expected = (self.normalized_score * self.weight).quantize(_SCORE_QUANTUM)
        if self.weighted_score != expected:
            raise ValueError("weighted_score must equal normalized_score multiplied by weight")
        return self


class RankedFlightOffer(DomainModel):
    offer: SafeFlightOffer
    rank: int = Field(ge=1, le=MAX_CLIENT_OFFERS)
    total_score: Decimal
    ranking_version: Literal["flight-rank-v1"] = "flight-rank-v1"
    reasons: tuple[RankingReason, ...] = Field(default_factory=tuple, max_length=5)
    components: tuple[RankingScoreComponent, ...] = Field(default_factory=tuple, max_length=10)

    @field_validator("total_score", mode="before")
    @classmethod
    def reject_binary_float(cls, value: object) -> object:
        if isinstance(value, float):
            raise TypeError("ranking total scores must use Decimal, not binary floats")
        return value

    @field_validator("total_score")
    @classmethod
    def validate_total_score(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value < 0 or value > 1:
            raise ValueError("ranking total score must be between 0 and 1")
        return value.quantize(_SCORE_QUANTUM)

    @model_validator(mode="after")
    def validate_components(self) -> RankedFlightOffer:
        total = sum((component.weighted_score for component in self.components), Decimal("0"))
        if total.quantize(_SCORE_QUANTUM) != self.total_score:
            raise ValueError("total_score must equal the sum of weighted component scores")
        return self


__all__ = [
    "RankedFlightOffer",
    "RankingReason",
    "RankingScoreComponent",
    "SafeFlightOffer",
    "SafeFlightSegment",
]
