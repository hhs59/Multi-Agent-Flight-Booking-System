from __future__ import annotations

from decimal import Decimal
from enum import StrEnum
from typing import Any, Self

from pydantic import Field, SecretStr, field_validator, model_validator

from agent_system.domain.locations import AirportLocation
from agent_system.domain.values import DomainModel, Money, ProviderMetadata, UTCInstant


class ForecastStatus(StrEnum):
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


class WeatherForecast(DomainModel):
    metadata: ProviderMetadata
    location: AirportLocation
    requested_at: UTCInstant
    forecast_at: UTCInstant | None = None
    status: ForecastStatus
    temperature_c: Decimal | None = None
    description: str | None = Field(default=None, max_length=500)
    precipitation_probability: Decimal | None = Field(
        default=None,
        ge=Decimal("0"),
        le=Decimal("1"),
    )
    reason: str | None = Field(default=None, max_length=500)

    @field_validator("temperature_c", "precipitation_probability", mode="before")
    @classmethod
    def reject_binary_float(cls, value: Any) -> Any:
        if isinstance(value, float):
            raise TypeError("weather decimals must be created from Decimal or decimal strings")
        return value

    @model_validator(mode="after")
    def validate_forecast(self) -> Self:
        if self.status is ForecastStatus.AVAILABLE:
            if self.forecast_at is None or self.temperature_c is None or not self.description:
                raise ValueError(
                    "available weather requires forecast time, temperature, and description"
                )
            if self.reason is not None:
                raise ValueError("available weather cannot contain an unavailable reason")
        elif any(
            value is not None
            for value in (
                self.forecast_at,
                self.temperature_c,
                self.description,
                self.precipitation_probability,
            )
        ):
            raise ValueError("unavailable weather cannot contain forecast values")
        return self


class PaymentStatus(StrEnum):
    READY = "ready"
    AUTHORIZED = "authorized"
    CAPTURED = "captured"
    CANCELLED = "cancelled"
    REFUNDED = "refunded"
    DECLINED = "declined"
    REQUIRES_ACTION = "requires_action"


class PaymentMethodSetupRequest(DomainModel):
    provider_token: SecretStr


class PaymentMethodSetupResult(DomainModel):
    metadata: ProviderMetadata
    status: PaymentStatus
    payment_method_reference: SecretStr | None = None
    action_reference: SecretStr | None = None
    reason_code: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_setup_result(self) -> Self:
        if self.status is PaymentStatus.READY and self.payment_method_reference is None:
            raise ValueError("ready payment setup requires a payment method reference")
        if self.status is PaymentStatus.REQUIRES_ACTION and self.action_reference is None:
            raise ValueError("requires-action setup requires an action reference")
        return self


class PaymentResult(DomainModel):
    metadata: ProviderMetadata
    status: PaymentStatus
    amount: Money
    transaction_reference: SecretStr | None = None
    action_reference: SecretStr | None = None
    reason_code: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_payment_result(self) -> Self:
        if (
            self.status
            in {PaymentStatus.AUTHORIZED, PaymentStatus.CAPTURED, PaymentStatus.REFUNDED}
            and self.transaction_reference is None
        ):
            raise ValueError("successful payment result requires a transaction reference")
        if self.status is PaymentStatus.REQUIRES_ACTION and self.action_reference is None:
            raise ValueError("requires-action payment requires an action reference")
        return self


class NotificationChannel(StrEnum):
    IN_APP = "in_app"
    EMAIL = "email"
    SMS = "sms"


class NotificationDestination(DomainModel):
    channel: NotificationChannel
    address: SecretStr | None = None


class NotificationResult(DomainModel):
    metadata: ProviderMetadata
    accepted: bool
    provider_message_reference: SecretStr | None = None
    reason_code: str | None = Field(default=None, max_length=120)

    @model_validator(mode="after")
    def validate_notification(self) -> Self:
        if self.accepted and self.provider_message_reference is None:
            raise ValueError("accepted notification requires a provider reference")
        return self
