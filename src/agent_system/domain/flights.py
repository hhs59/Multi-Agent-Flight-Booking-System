from __future__ import annotations

from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Self

from pydantic import Field, model_validator

from agent_system.domain.values import (
    AirportCode,
    CarrierCode,
    CurrencyCode,
    DomainModel,
    ExecutionMode,
    Money,
    OfferId,
    ProviderMetadata,
    UTCInstant,
    new_id,
)


class CabinClass(StrEnum):
    ECONOMY = "economy"
    PREMIUM_ECONOMY = "premium_economy"
    BUSINESS = "business"
    FIRST = "first"


class PassengerType(StrEnum):
    ADULT = "adult"
    CHILD = "child"
    INFANT = "infant"


class PassengerMix(DomainModel):
    adults: int = Field(default=1, ge=1, le=9)
    children: int = Field(default=0, ge=0, le=8)
    infants: int = Field(default=0, ge=0, le=8)

    @model_validator(mode="after")
    def validate_party(self) -> Self:
        if self.infants > self.adults:
            raise ValueError("each infant must be accompanied by an adult")
        if self.total > 9:
            raise ValueError("a search supports at most 9 passengers")
        return self

    @property
    def total(self) -> int:
        return self.adults + self.children + self.infants


class FlightSearchCriteria(DomainModel):
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
    preferred_carriers: tuple[CarrierCode, ...] = ()

    @model_validator(mode="after")
    def validate_route_and_dates(self) -> Self:
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        if self.return_date is not None and self.return_date < self.departure_date:
            raise ValueError("return_date cannot precede departure_date")
        if (self.preferred_departure_start is None) != (self.preferred_departure_end is None):
            raise ValueError("preferred departure start and end must be provided together")
        return self


class FlightSegment(DomainModel):
    origin: AirportCode
    destination: AirportCode
    departure_at: UTCInstant
    arrival_at: UTCInstant
    marketing_carrier: CarrierCode
    operating_carrier: CarrierCode
    flight_number: str = Field(min_length=3, max_length=12, pattern=r"^[A-Z0-9]+$")
    aircraft_code: str | None = Field(default=None, max_length=12)

    @model_validator(mode="after")
    def validate_segment(self) -> Self:
        if self.origin == self.destination:
            raise ValueError("segment origin and destination must differ")
        if self.arrival_at <= self.departure_at:
            raise ValueError("arrival_at must be later than departure_at")
        return self


class FareConditions(DomainModel):
    fare_basis: str | None = Field(default=None, max_length=80)
    change_allowed: bool | None = None
    change_fee: Money | None = None
    cancellation_allowed: bool | None = None
    cancellation_fee: Money | None = None
    refundable: bool | None = None
    description: str | None = Field(default=None, max_length=2000)


class BaggageAllowance(DomainModel):
    checked_pieces: int | None = Field(default=None, ge=0, le=20)
    checked_weight_kg: Decimal | None = Field(default=None, ge=0)
    cabin_pieces: int | None = Field(default=None, ge=0, le=20)
    cabin_weight_kg: Decimal | None = Field(default=None, ge=0)
    personal_item_included: bool | None = None


class ProviderCapabilities(DomainModel):
    can_search: bool = True
    can_reprice: bool = False
    can_book: bool = False
    can_hold: bool = False
    requires_instant_payment: bool = False
    can_cancel: bool = False
    can_refund: bool = False
    supports_ancillaries: bool = False

    @model_validator(mode="after")
    def validate_capabilities(self) -> Self:
        if self.can_hold and self.requires_instant_payment:
            raise ValueError("a hold cannot require instant payment")
        if self.can_refund and not self.can_cancel:
            raise ValueError("refund capability requires cancellation capability")
        return self


class ProviderPassengerReference(DomainModel):
    """Provider-owned passenger identity kept out of browser-safe offer responses."""

    provider_passenger_id: str = Field(min_length=1, max_length=160)
    passenger_type: PassengerType


class PassengerPrice(DomainModel):
    passenger_type: PassengerType
    quantity: int = Field(ge=1, le=9)
    base: Money
    taxes_and_fees: Money
    total: Money

    @model_validator(mode="after")
    def validate_price(self) -> Self:
        currencies = {self.base.currency, self.taxes_and_fees.currency, self.total.currency}
        if len(currencies) != 1:
            raise ValueError("passenger price components must use one currency")
        if self.base.amount < 0 or self.taxes_and_fees.amount < 0 or self.total.amount < 0:
            raise ValueError("passenger price components cannot be negative")
        if self.base + self.taxes_and_fees != self.total:
            raise ValueError("passenger total must equal base plus taxes and fees")
        return self


class FlightOffer(DomainModel):
    id: OfferId = Field(default_factory=new_id)
    metadata: ProviderMetadata
    segments: tuple[FlightSegment, ...] = Field(min_length=1)
    validating_carrier: CarrierCode
    cabin: CabinClass
    fare_brand: str | None = Field(default=None, max_length=120)
    total: Money
    passenger_pricing: tuple[PassengerPrice, ...] = Field(min_length=1)
    baggage: BaggageAllowance
    fare_conditions: FareConditions
    seats_available: int | None = Field(default=None, ge=0, le=99)
    capabilities: ProviderCapabilities
    provider_passengers: tuple[ProviderPassengerReference, ...] = Field(
        default_factory=tuple,
        max_length=9,
    )

    @model_validator(mode="after")
    def validate_offer(self) -> Self:
        if self.total.amount < 0:
            raise ValueError("offer total cannot be negative")
        if self.metadata.expires_at is None:
            raise ValueError("flight offers require an expiry instant")
        if (
            self.metadata.environment is not ExecutionMode.MOCK
            and not self.metadata.provider_offer_id
        ):
            raise ValueError("non-mock flight offers require provider_offer_id")
        if any(price.total.currency != self.total.currency for price in self.passenger_pricing):
            raise ValueError("passenger pricing currency must match offer total")
        passenger_total = sum(
            (price.total.amount for price in self.passenger_pricing),
            start=Decimal("0"),
        )
        if passenger_total != self.total.amount:
            raise ValueError("offer total must equal the passenger pricing totals")
        passenger_ids = [item.provider_passenger_id for item in self.provider_passengers]
        if len(passenger_ids) != len(set(passenger_ids)):
            raise ValueError("provider passenger IDs must be unique")
        return self

    def is_expired(self, at: datetime | None = None) -> bool:
        check_at = at or datetime.now(UTC)
        if check_at.tzinfo is None or check_at.utcoffset() is None:
            raise ValueError("expiry checks require a timezone-aware instant")
        expires_at = self.metadata.expires_at
        return expires_at is not None and check_at.astimezone(UTC) >= expires_at


class SearchResultPage(DomainModel):
    metadata: ProviderMetadata
    criteria: FlightSearchCriteria
    offers: tuple[FlightOffer, ...] = Field(
        default_factory=tuple,
        max_length=50,
    )
    total_results: int = Field(ge=0)
    next_page_token: str | None = Field(default=None, max_length=1024)
    warnings: tuple[str, ...] = Field(default_factory=tuple, max_length=100)

    @model_validator(mode="after")
    def validate_page(self) -> Self:
        if self.total_results < len(self.offers):
            raise ValueError("total_results cannot be smaller than the returned offer count")
        if any(offer.metadata.provider != self.metadata.provider for offer in self.offers):
            raise ValueError("all offers in a page must come from the page provider")
        return self


class RepriceStatus(StrEnum):
    UNCHANGED = "unchanged"
    CHANGED = "changed"
    UNAVAILABLE = "unavailable"
    EXPIRED = "expired"


class RepriceResult(DomainModel):
    metadata: ProviderMetadata
    original_offer_id: OfferId
    status: RepriceStatus
    repriced_offer: FlightOffer | None = None
    reason: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def validate_result(self) -> Self:
        has_offer = self.repriced_offer is not None
        if self.status in {RepriceStatus.UNCHANGED, RepriceStatus.CHANGED} and not has_offer:
            raise ValueError("successful repricing requires a repriced offer")
        if self.status in {RepriceStatus.UNAVAILABLE, RepriceStatus.EXPIRED} and has_offer:
            raise ValueError("unavailable or expired repricing cannot include an offer")
        if has_offer and self.repriced_offer.metadata.provider != self.metadata.provider:
            raise ValueError("repriced offer must come from the repricing provider")
        return self
