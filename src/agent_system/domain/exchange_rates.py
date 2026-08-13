from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Protocol, runtime_checkable

from pydantic import Field, field_validator, model_validator

from agent_system.domain.values import (
    SUPPORTED_CURRENCIES,
    CurrencyCode,
    DomainModel,
    ExecutionMode,
    Money,
    UTCInstant,
)

_DEFAULT_RATES_JSON = '{"USD":"1","VND":"25000","EUR":"0.92","AUD":"1.52"}'
_ZERO_MINOR_UNIT_CURRENCIES = frozenset({"IDR", "JPY", "KRW", "LAK", "VND"})
_DEFAULT_RATE_TTL_SECONDS = 86_400


class Clock(Protocol):
    def now(self) -> datetime: ...


class _SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class ExchangeRateError(RuntimeError):
    """A safe exchange-rate provider failure."""


class ExchangeRateUnavailableError(ExchangeRateError):
    safe_code = "currency_conversion_unavailable"


class ExchangeRateConfigurationError(ValueError):
    """The server-owned FX configuration is invalid."""


class ExchangeRateQuote(DomainModel):
    source_currency: CurrencyCode
    target_currency: CurrencyCode
    rate: Decimal
    source: str = Field(min_length=1, max_length=80)
    as_of: UTCInstant
    expires_at: UTCInstant
    is_demo: bool

    @field_validator("rate", mode="before")
    @classmethod
    def reject_float_rate(cls, value: Any) -> Any:
        if isinstance(value, float) or not isinstance(value, (Decimal, str)):
            raise TypeError("exchange rates must be created from Decimal or a decimal string")
        return value

    @field_validator("rate")
    @classmethod
    def validate_rate(cls, value: Decimal) -> Decimal:
        if not value.is_finite() or value <= 0:
            raise ValueError("exchange rate must be positive and finite")
        return value

    @model_validator(mode="after")
    def validate_expiry(self) -> ExchangeRateQuote:
        if self.expires_at <= self.as_of:
            raise ValueError("exchange-rate expiry must follow as_of")
        return self


class MoneyConversion(DomainModel):
    original: Money
    converted: Money
    quote: ExchangeRateQuote


@runtime_checkable
class ExchangeRateProvider(Protocol):
    async def quote(
        self,
        source_currency: CurrencyCode,
        target_currency: CurrencyCode,
        *,
        correlation_id: str | None = None,
    ) -> ExchangeRateQuote: ...


def _currency(value: str, label: str) -> str:
    normalized = value.strip().upper()
    if normalized not in SUPPORTED_CURRENCIES:
        raise ExchangeRateConfigurationError(f"{label} contains unsupported currency: {normalized}")
    return normalized


def _decimal_rate(value: object, label: str) -> Decimal:
    if isinstance(value, float) or not isinstance(value, str):
        raise ExchangeRateConfigurationError(f"{label} must be a decimal string")
    try:
        parsed = Decimal(value)
    except (InvalidOperation, ValueError) as exc:
        raise ExchangeRateConfigurationError(f"{label} must be a decimal string") from exc
    if not parsed.is_finite() or parsed <= 0:
        raise ExchangeRateConfigurationError(f"{label} must be positive and finite")
    return parsed


def _quantum(currency: str) -> Decimal:
    return Decimal("1") if currency in _ZERO_MINOR_UNIT_CURRENCIES else Decimal("0.01")


def quantize_currency(amount: Decimal, currency: CurrencyCode) -> Decimal:
    return amount.quantize(_quantum(currency), rounding=ROUND_HALF_UP)


def conversion_from_quote(
    money: Money,
    target_currency: CurrencyCode,
    quote: ExchangeRateQuote,
) -> MoneyConversion:
    if quote.source_currency != money.currency or quote.target_currency != target_currency:
        raise ExchangeRateError("exchange-rate quote currencies do not match conversion")
    converted = Money(
        amount=quantize_currency(money.amount * quote.rate, target_currency),
        currency=target_currency,
    )
    return MoneyConversion(original=money, converted=converted, quote=quote)


def _same_currency_quote(
    currency: CurrencyCode,
    *,
    clock: Clock,
    ttl_seconds: int = _DEFAULT_RATE_TTL_SECONDS,
) -> ExchangeRateQuote:
    as_of = clock.now()
    return ExchangeRateQuote(
        source_currency=currency,
        target_currency=currency,
        rate=Decimal("1"),
        source="same_currency",
        as_of=as_of,
        expires_at=as_of + timedelta(seconds=ttl_seconds),
        is_demo=False,
    )


@dataclass(frozen=True)
class DemoStaticExchangeRateProvider:
    rates: Mapping[str, object]
    base_currency: str = "USD"
    ttl_seconds: int = _DEFAULT_RATE_TTL_SECONDS
    clock: Clock | None = None

    def __post_init__(self) -> None:
        base = _currency(self.base_currency, "FX_BASE_CURRENCY")
        if self.ttl_seconds <= 0:
            raise ExchangeRateConfigurationError("FX_RATE_TTL_SECONDS must be greater than zero")
        if not isinstance(self.rates, Mapping):
            raise ExchangeRateConfigurationError("FX_RATES_JSON must be an object")
        normalized: dict[str, Decimal] = {}
        for key, value in self.rates.items():
            if not isinstance(key, str):
                raise ExchangeRateConfigurationError("FX_RATES_JSON keys must be currency strings")
            currency = _currency(key, "FX_RATES_JSON")
            if currency in normalized:
                raise ExchangeRateConfigurationError(f"duplicate FX currency: {currency}")
            normalized[currency] = _decimal_rate(value, f"FX_RATES_JSON[{currency}]")
        if base not in normalized:
            raise ExchangeRateConfigurationError("FX_RATES_JSON must include the base currency")
        if normalized[base] != Decimal("1"):
            raise ExchangeRateConfigurationError("the FX base currency rate must equal exactly 1")
        object.__setattr__(self, "base_currency", base)
        object.__setattr__(self, "rates", normalized)
        object.__setattr__(self, "clock", self.clock or _SystemClock())

    @classmethod
    def from_environment(cls, *, clock: Clock | None = None) -> DemoStaticExchangeRateProvider:
        try:
            payload = json.loads(os.getenv("FX_RATES_JSON", _DEFAULT_RATES_JSON))
        except json.JSONDecodeError as exc:
            raise ExchangeRateConfigurationError("FX_RATES_JSON must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise ExchangeRateConfigurationError("FX_RATES_JSON must be an object")
        try:
            ttl_seconds = int(os.getenv("FX_RATE_TTL_SECONDS", str(_DEFAULT_RATE_TTL_SECONDS)))
        except ValueError as exc:
            raise ExchangeRateConfigurationError("FX_RATE_TTL_SECONDS must be an integer") from exc
        return cls(
            rates=payload,
            base_currency=os.getenv("FX_BASE_CURRENCY", "USD"),
            ttl_seconds=ttl_seconds,
            clock=clock,
        )

    async def quote(
        self,
        source_currency: CurrencyCode,
        target_currency: CurrencyCode,
        *,
        correlation_id: str | None = None,
    ) -> ExchangeRateQuote:
        del correlation_id
        source = _currency(source_currency, "source currency")
        target = _currency(target_currency, "target currency")
        if source == target:
            return _same_currency_quote(source, clock=self.clock, ttl_seconds=self.ttl_seconds)
        if source not in self.rates or target not in self.rates:
            raise ExchangeRateUnavailableError("no configured exchange rate for this currency pair")
        as_of = self.clock.now()
        return ExchangeRateQuote(
            source_currency=source,
            target_currency=target,
            rate=self.rates[target] / self.rates[source],
            source="demo_static",
            as_of=as_of,
            expires_at=as_of + timedelta(seconds=self.ttl_seconds),
            is_demo=True,
        )


def build_exchange_rate_provider(
    *,
    clock: Clock | None = None,
    environment: ExecutionMode | None = None,
) -> ExchangeRateProvider | None:
    selected = os.getenv("FX_PROVIDER", "disabled").strip().lower()
    if selected == "disabled":
        return None
    if selected != "demo_static":
        raise ExchangeRateConfigurationError("FX_PROVIDER must be disabled or demo_static")
    if environment is ExecutionMode.PRODUCTION:
        raise ExchangeRateConfigurationError(
            "demo_static FX rates are not allowed in production mode"
        )
    return DemoStaticExchangeRateProvider.from_environment(clock=clock)


async def convert_money(
    money: Money,
    target_currency: CurrencyCode,
    *,
    provider: ExchangeRateProvider | None,
    clock: Clock,
    correlation_id: str | None = None,
) -> MoneyConversion:
    if money.currency == target_currency:
        return MoneyConversion(
            original=money,
            converted=money,
            quote=_same_currency_quote(money.currency, clock=clock),
        )
    if provider is None:
        raise ExchangeRateUnavailableError("exchange-rate provider is disabled")
    quote = await provider.quote(
        money.currency,
        target_currency,
        correlation_id=correlation_id,
    )
    if not isinstance(quote, ExchangeRateQuote):
        raise ExchangeRateError("exchange-rate provider returned an invalid quote")
    now = clock.now()
    if quote.expires_at <= now:
        raise ExchangeRateUnavailableError("exchange-rate quote is expired")
    return conversion_from_quote(money, target_currency, quote)


__all__ = [
    "DemoStaticExchangeRateProvider",
    "ExchangeRateConfigurationError",
    "ExchangeRateError",
    "ExchangeRateProvider",
    "ExchangeRateQuote",
    "ExchangeRateUnavailableError",
    "MoneyConversion",
    "build_exchange_rate_provider",
    "conversion_from_quote",
    "convert_money",
    "quantize_currency",
]
