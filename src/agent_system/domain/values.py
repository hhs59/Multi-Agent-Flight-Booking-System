from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Any, Self
from uuid import UUID, uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)


class DomainModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        validate_assignment=True,
        validate_default=True,
    )


class ExecutionMode(StrEnum):
    MOCK = "mock"
    SANDBOX = "sandbox"
    PRODUCTION = "production"


SUPPORTED_CURRENCIES = frozenset(
    {
        "AUD",
        "CAD",
        "CNY",
        "EUR",
        "GBP",
        "HKD",
        "IDR",
        "INR",
        "JPY",
        "KRW",
        "LAK",
        "MYR",
        "NZD",
        "PHP",
        "SGD",
        "THB",
        "TWD",
        "USD",
        "VND",
    }
)


def _normalize_code(value: Any, *, length: int, label: str, alphanumeric: bool) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    normalized = value.strip().upper()
    is_valid = normalized.isalnum() if alphanumeric else normalized.isalpha()
    if len(normalized) != length or not is_valid:
        kind = "alphanumeric" if alphanumeric else "alphabetic"
        raise ValueError(f"{label} must be a {length}-character {kind} IATA code")
    return normalized


def _normalize_airport_code(value: Any) -> str:
    return _normalize_code(value, length=3, label="airport code", alphanumeric=False)


def _normalize_carrier_code(value: Any) -> str:
    return _normalize_code(value, length=2, label="carrier code", alphanumeric=True)


def _normalize_currency(value: Any) -> str:
    code = _normalize_code(value, length=3, label="currency", alphanumeric=False)
    if code not in SUPPORTED_CURRENCIES:
        raise ValueError(f"unsupported transactional currency: {code}")
    return code


AirportCode = Annotated[
    str,
    BeforeValidator(_normalize_airport_code),
    StringConstraints(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
]
CarrierCode = Annotated[
    str,
    BeforeValidator(_normalize_carrier_code),
    StringConstraints(min_length=2, max_length=2, pattern=r"^[A-Z0-9]{2}$"),
]
CurrencyCode = Annotated[
    str,
    BeforeValidator(_normalize_currency),
    StringConstraints(min_length=3, max_length=3, pattern=r"^[A-Z]{3}$"),
]


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("instant must include a timezone offset")
    return value.astimezone(UTC)


UTCInstant = Annotated[datetime, AfterValidator(_as_utc)]


def utc_now() -> datetime:
    return datetime.now(UTC)


def new_id() -> UUID:
    return uuid4()


def to_local(instant: datetime, timezone_name: str) -> datetime:
    normalized = _as_utc(instant)
    try:
        local_zone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as exc:
        raise ValueError(f"unknown IANA timezone: {timezone_name}") from exc
    return normalized.astimezone(local_zone)


class Money(DomainModel):
    model_config = ConfigDict(extra="forbid", validate_assignment=True, frozen=True)

    amount: Decimal
    currency: CurrencyCode

    @field_validator("amount", mode="before")
    @classmethod
    def reject_binary_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise TypeError("Money.amount must be created from Decimal, int, or a decimal string")
        return value

    @field_validator("amount")
    @classmethod
    def finite_amount(cls, value: Decimal) -> Decimal:
        if not value.is_finite():
            raise ValueError("Money.amount must be finite")
        return value

    def _same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise ValueError(f"currency mismatch: {self.currency} and {other.currency}")

    def __add__(self, other: Money) -> Self:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return type(self)(amount=self.amount + other.amount, currency=self.currency)

    def __sub__(self, other: Money) -> Self:
        if not isinstance(other, Money):
            return NotImplemented
        self._same_currency(other)
        return type(self)(amount=self.amount - other.amount, currency=self.currency)

    def __mul__(self, multiplier: Decimal | int) -> Self:
        if isinstance(multiplier, float):
            raise TypeError("Money multiplication does not accept binary floats")
        return type(self)(amount=self.amount * Decimal(multiplier), currency=self.currency)


class ProviderMetadata(DomainModel):
    provider: str = Field(min_length=1, max_length=80)
    environment: ExecutionMode
    is_live: bool
    retrieved_at: UTCInstant
    expires_at: UTCInstant | None = None
    provider_offer_id: str | None = Field(default=None, max_length=512)
    correlation_id: str | None = Field(default=None, max_length=512)

    @field_validator("provider")
    @classmethod
    def normalize_provider(cls, value: str) -> str:
        return value.strip().lower()

    @model_validator(mode="after")
    def validate_environment(self) -> Self:
        expected_live = self.environment is ExecutionMode.PRODUCTION
        if self.is_live != expected_live:
            raise ValueError("is_live must be true only for the production environment")
        if self.expires_at is not None and self.expires_at <= self.retrieved_at:
            raise ValueError("expires_at must be later than retrieved_at")
        return self


UserId = UUID
ThreadId = UUID
MessageId = UUID
CheckpointId = UUID
TravelerProfileId = UUID
OfferId = UUID
BookingIntentId = UUID
BookingQuoteId = UUID
BookingId = UUID
BookingEventId = UUID
WatchId = UUID
WatchMatchId = UUID
PurchaseMandateId = UUID
