from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Self

from pydantic import Field, JsonValue, SecretStr, model_validator

from agent_system.domain.accounts import CountryCode, EmailAddress
from agent_system.domain.flights import FlightOffer
from agent_system.domain.values import (
    BookingEventId,
    BookingId,
    BookingIntentId,
    BookingQuoteId,
    DomainModel,
    OfferId,
    ProviderMetadata,
    ThreadId,
    TravelerProfileId,
    UserId,
    UTCInstant,
    new_id,
    utc_now,
)


class BookingIntentStatus(StrEnum):
    DRAFT = "draft"
    QUOTE_READY = "quote_ready"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    CONFIRMED = "confirmed"
    EXPIRED = "expired"
    CANCELLED = "cancelled"
    FAILED = "failed"


class BookingStatus(StrEnum):
    PENDING = "pending"
    ORDER_CREATED = "order_created"
    TICKETED = "ticketed"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    FAILED = "failed"
    NEEDS_RECONCILIATION = "needs_reconciliation"


class BookingEventType(StrEnum):
    INTENT_CREATED = "intent_created"
    REPRICE_COMPLETED = "reprice_completed"
    CONFIRMATION_REQUESTED = "confirmation_requested"
    USER_CONFIRMED = "user_confirmed"
    ORDER_CREATED = "order_created"
    HOLD_CREATED = "hold_created"
    TICKET_ISSUED = "ticket_issued"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    FAILED = "failed"
    RECONCILIATION_REQUIRED = "reconciliation_required"


class BookingIntentCreate(DomainModel):
    thread_id: ThreadId | None = None
    source_offer_id: OfferId
    traveler_profile_ids: tuple[TravelerProfileId, ...] = Field(min_length=1, max_length=9)


class BookingIntent(BookingIntentCreate):
    id: BookingIntentId = Field(default_factory=new_id)
    user_id: UserId
    status: BookingIntentStatus = BookingIntentStatus.DRAFT
    created_at: UTCInstant = Field(default_factory=utc_now)
    updated_at: UTCInstant = Field(default_factory=utc_now)


class TravelerSnapshot(DomainModel):
    traveler_profile_id: TravelerProfileId | None = None
    legal_name: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=20)
    given_name: str | None = Field(default=None, max_length=120)
    family_name: str | None = Field(default=None, max_length=120)
    birth_date: date
    gender_marker: str | None = Field(default=None, max_length=30)
    email: EmailAddress
    phone: str | None = Field(default=None, max_length=40)
    nationality: CountryCode | None = None
    passport_number: SecretStr | None = None
    passport_issuing_country: CountryCode | None = None
    passport_expiry_date: date | None = None


class BookingQuote(DomainModel):
    id: BookingQuoteId = Field(default_factory=new_id)
    user_id: UserId
    booking_intent_id: BookingIntentId
    source_offer_id: OfferId
    offer: FlightOffer
    travelers: tuple[TravelerSnapshot, ...] = Field(min_length=1, max_length=9)
    created_at: UTCInstant = Field(default_factory=utc_now)
    expires_at: UTCInstant

    @model_validator(mode="after")
    def validate_quote(self) -> Self:
        if self.expires_at <= self.created_at:
            raise ValueError("booking quote must expire after it is created")
        offer_expiry = self.offer.metadata.expires_at
        if offer_expiry is None or self.expires_at > offer_expiry:
            raise ValueError("booking quote cannot outlive its provider offer")
        return self


class ProviderOrderReference(DomainModel):
    metadata: ProviderMetadata
    provider_order_id: SecretStr
    booking_reference: str | None = Field(default=None, max_length=80)
    provider_status: str | None = Field(default=None, max_length=80)
    live_mode: bool = False


class HoldReference(DomainModel):
    metadata: ProviderMetadata
    provider_hold_id: SecretStr
    expires_at: UTCInstant


class TicketReference(DomainModel):
    metadata: ProviderMetadata
    ticket_number: SecretStr
    issued_at: UTCInstant


class BookingConfirmation(DomainModel):
    id: BookingId = Field(default_factory=new_id)
    user_id: UserId
    booking_intent_id: BookingIntentId
    quote_id: BookingQuoteId
    user_confirmation_code: str = Field(min_length=1, max_length=40)
    status: BookingStatus
    provider_order: ProviderOrderReference | None = None
    hold: HoldReference | None = None
    tickets: tuple[TicketReference, ...] = ()
    confirmed_at: UTCInstant | None = None
    created_at: UTCInstant = Field(default_factory=utc_now)
    updated_at: UTCInstant = Field(default_factory=utc_now)

    @model_validator(mode="after")
    def validate_confirmation(self) -> Self:
        if (
            self.status in {BookingStatus.ORDER_CREATED, BookingStatus.TICKETED}
            and self.provider_order is None
        ):
            raise ValueError("an order-created booking requires a provider order reference")
        if self.status is BookingStatus.TICKETED and not self.tickets:
            raise ValueError("a ticketed booking requires at least one ticket reference")
        return self


class BookingEvent(DomainModel):
    id: BookingEventId = Field(default_factory=new_id)
    user_id: UserId
    booking_id: BookingId
    event_type: BookingEventType
    from_status: BookingStatus | None = None
    to_status: BookingStatus | None = None
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    occurred_at: UTCInstant = Field(default_factory=utc_now)
