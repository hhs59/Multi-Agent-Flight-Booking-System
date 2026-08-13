from __future__ import annotations

from datetime import date
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, SecretStr, model_validator

from agent_system.domain.flights import CabinClass, FlightOffer, PassengerMix
from agent_system.domain.provider_services import NotificationChannel
from agent_system.domain.values import (
    AirportCode,
    CarrierCode,
    CurrencyCode,
    DomainModel,
    ExecutionMode,
    Money,
    PurchaseMandateId,
    TravelerProfileId,
    UserId,
    UTCInstant,
    WatchId,
    WatchMatchId,
    new_id,
    utc_now,
)


class WatchActionMode(StrEnum):
    NOTIFY = "notify"
    CONFIRM = "confirm"
    AUTO_BUY = "auto_buy"


class NotificationBehavior(StrEnum):
    FIRST_MATCH = "first_match"
    EVERY_BETTER_MATCH = "every_better_match"
    DAILY_DIGEST = "daily_digest"


class WatchStatus(StrEnum):
    DRAFT = "draft"
    ACTIVE = "active"
    PAUSED = "paused"
    MATCHED = "matched"
    AWAITING_CONFIRMATION = "awaiting_confirmation"
    EXECUTING = "executing"
    BOOKED = "booked"
    NEEDS_USER_ACTION = "needs_user_action"
    EXPIRED = "expired"
    FAILED = "failed"
    CANCELLED = "cancelled"
    # Kept for compatibility with earlier persisted drafts.
    COMPLETED = "completed"


class WatchRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    ABANDONED = "abandoned"


class WatchMatchStatus(StrEnum):
    MATCHED = "matched"
    REJECTED = "rejected"
    NOTIFIED = "notified"
    ACTION_REQUIRED = "action_required"
    EXECUTING = "executing"
    BOOKED = "booked"
    FAILED = "failed"


class PurchaseMandateStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    REVOKED = "revoked"
    EXPIRED = "expired"


class HoldStatus(StrEnum):
    ACTIVE = "active"
    RELEASED = "released"
    EXPIRED = "expired"
    NEEDS_USER_ACTION = "needs_user_action"
    FAILED = "failed"


class FlightWatchCriteria(DomainModel):
    origin: AirportCode
    destination: AirportCode
    departure_date_from: date
    departure_date_to: date
    passengers: PassengerMix = Field(default_factory=PassengerMix)
    traveler_profile_ids: tuple[TravelerProfileId, ...] = Field(default_factory=tuple, max_length=9)
    cabin: CabinClass = CabinClass.ECONOMY
    max_stops: int | None = Field(default=None, ge=0, le=4)
    maximum_total: Money | None = None
    minimum_checked_pieces: int | None = Field(default=None, ge=0, le=20)
    require_refundable: bool = False
    purchase_deadline: UTCInstant | None = None
    timezone: str = Field(default="Asia/Ho_Chi_Minh", min_length=1, max_length=64)
    preferred_carriers: tuple[CarrierCode, ...] = ()
    excluded_carriers: tuple[CarrierCode, ...] = ()
    selected_provider: str | None = Field(default=None, max_length=80)
    notification_channels: tuple[NotificationChannel, ...] = (NotificationChannel.EMAIL,)
    notification_behavior: NotificationBehavior = NotificationBehavior.FIRST_MATCH
    action_mode: WatchActionMode = WatchActionMode.NOTIFY

    @model_validator(mode="after")
    def validate_criteria(self) -> Self:
        if self.origin == self.destination:
            raise ValueError("origin and destination must differ")
        if self.departure_date_to < self.departure_date_from:
            raise ValueError("departure date window is reversed")
        if self.maximum_total is not None and self.maximum_total.amount <= 0:
            raise ValueError("maximum_total must be positive")
        if set(self.preferred_carriers) & set(self.excluded_carriers):
            raise ValueError("a carrier cannot be both preferred and excluded")
        if self.action_mode is WatchActionMode.AUTO_BUY:
            if self.maximum_total is None:
                raise ValueError("auto-buy requires an explicit maximum_total")
            if self.purchase_deadline is None:
                raise ValueError("auto-buy requires an explicit purchase_deadline")
            if len(self.traveler_profile_ids) != self.passengers.total:
                raise ValueError("auto-buy requires one traveler profile per passenger")
        if self.action_mode is not WatchActionMode.NOTIFY and not self.traveler_profile_ids:
            raise ValueError("confirm and auto-buy watches require traveler profiles")
        return self


def ensure_watch_action_allowed(
    criteria: FlightWatchCriteria,
    *,
    execution_mode: ExecutionMode,
    auto_buy_enabled: bool,
) -> None:
    if criteria.action_mode is not WatchActionMode.AUTO_BUY:
        return
    if execution_mode is not ExecutionMode.PRODUCTION or not auto_buy_enabled:
        raise ValueError("auto-buy is disabled outside an explicitly enabled production gate")


class FlightWatchCreate(DomainModel):
    criteria: FlightWatchCriteria


class FlightWatch(DomainModel):
    id: WatchId = Field(default_factory=new_id)
    user_id: UserId
    criteria: FlightWatchCriteria
    status: WatchStatus = WatchStatus.DRAFT
    created_at: UTCInstant = Field(default_factory=utc_now)
    updated_at: UTCInstant = Field(default_factory=utc_now)


class WatchMatchSummary(DomainModel):
    match_id: UUID
    offer_id: UUID | None = None
    status: str = Field(min_length=1, max_length=40)
    price: Decimal
    currency: CurrencyCode
    origin: AirportCode
    destination: AirportCode
    departure_at: UTCInstant
    provider: str | None = Field(default=None, max_length=80)
    environment: str | None = Field(default=None, max_length=16)
    expires_at: UTCInstant | None = None
    matched_at: UTCInstant


class WatchNotificationSummary(DomainModel):
    channel: NotificationChannel
    status: str = Field(min_length=1, max_length=32)
    sent_at: UTCInstant | None = None
    error_code: str | None = Field(default=None, max_length=120)


class WatchResponse(DomainModel):
    watch_id: UUID
    status: str = Field(min_length=1, max_length=40)
    criteria: FlightWatchCriteria
    next_run_at: UTCInstant | None = None
    last_checked_at: UTCInstant | None = None
    run_count: int = Field(ge=0)
    consecutive_failures: int = Field(ge=0)
    last_error_code: str | None = Field(default=None, max_length=120)
    latest_match: WatchMatchSummary | None = None
    latest_notifications: tuple[WatchNotificationSummary, ...] = Field(default_factory=tuple)


class WatchMatch(DomainModel):
    id: WatchMatchId = Field(default_factory=new_id)
    user_id: UserId
    watch_id: WatchId
    offer: FlightOffer
    matched_at: UTCInstant = Field(default_factory=utc_now)
    notification_sent_at: UTCInstant | None = None


class PurchaseMandate(DomainModel):
    id: PurchaseMandateId = Field(default_factory=new_id)
    user_id: UserId
    watch_id: WatchId
    traveler_profile_ids: tuple[TravelerProfileId, ...] = Field(min_length=1, max_length=9)
    criteria_snapshot: FlightWatchCriteria
    maximum_total: Money
    purchase_deadline: UTCInstant
    payment_method_reference: SecretStr
    off_session_permission: bool = False
    terms_version: str = Field(min_length=1, max_length=80)
    consent_version: str = Field(min_length=1, max_length=40)
    consented_at: UTCInstant
    revoked_at: UTCInstant | None = None

    @model_validator(mode="after")
    def validate_mandate(self) -> Self:
        if self.maximum_total.amount <= 0:
            raise ValueError("purchase mandate maximum_total must be positive")
        if self.purchase_deadline <= self.consented_at:
            raise ValueError("purchase mandate deadline must follow consent")
        if self.revoked_at is not None and self.revoked_at < self.consented_at:
            raise ValueError("revocation cannot precede consent")
        if not self.off_session_permission:
            raise ValueError("auto-buy mandates require explicit off-session permission")
        return self


class PurchaseMandateCreate(DomainModel):
    payment_method_reference: SecretStr
    off_session_permission: bool = False
    terms_version: str = Field(min_length=1, max_length=80)
    consent_version: str = Field(min_length=1, max_length=40)
    acknowledged_terms: bool = False
