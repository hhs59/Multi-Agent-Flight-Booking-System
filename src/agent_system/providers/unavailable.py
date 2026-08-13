# ruff: noqa: ARG002

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime

from pydantic import JsonValue, SecretStr

from agent_system.domain.bookings import (
    BookingQuote,
    HoldReference,
    ProviderOrderReference,
    TravelerSnapshot,
)
from agent_system.domain.flights import (
    FlightOffer,
    FlightSearchCriteria,
    ProviderCapabilities,
    RepriceResult,
    SearchResultPage,
)
from agent_system.domain.locations import AirportLocation
from agent_system.domain.provider_services import (
    NotificationDestination,
    NotificationResult,
    PaymentMethodSetupRequest,
    PaymentMethodSetupResult,
    PaymentResult,
    WeatherForecast,
)
from agent_system.domain.values import ExecutionMode, Money
from agent_system.providers.errors import CapabilityUnavailable


class _Unavailable:
    def __init__(self, name: str, environment: ExecutionMode) -> None:
        self.name = name
        self.environment = environment

    def _raise(self, operation: str) -> None:
        raise CapabilityUnavailable(
            provider=self.name,
            operation=operation,
            safe_message="provider capability is not configured",
        )


class UnavailableFlightProvider(_Unavailable):
    def capabilities(self) -> ProviderCapabilities:
        return ProviderCapabilities(
            can_search=False,
            can_reprice=False,
            can_book=False,
            can_hold=False,
            can_cancel=False,
            can_refund=False,
        )

    async def search(
        self,
        criteria: FlightSearchCriteria,
        *,
        correlation_id: str | None = None,
    ) -> SearchResultPage:
        self._raise("search")

    async def reprice(
        self,
        provider_offer_id: str,
        expected: FlightOffer,
        *,
        correlation_id: str | None = None,
    ) -> RepriceResult:
        self._raise("reprice")

    async def create_order(
        self,
        quote: BookingQuote,
        travelers: tuple[TravelerSnapshot, ...],
        idempotency_key: str,
    ) -> ProviderOrderReference:
        self._raise("create_order")

    async def get_order(self, provider_order_id: str) -> ProviderOrderReference:
        self._raise("get_order")

    async def cancel_order(
        self,
        provider_order_id: str,
        idempotency_key: str,
    ) -> ProviderOrderReference:
        self._raise("cancel_order")

    async def create_hold(
        self,
        quote: BookingQuote,
        travelers: tuple[TravelerSnapshot, ...],
        idempotency_key: str,
    ) -> HoldReference:
        self._raise("create_hold")

    async def release_hold(
        self,
        provider_hold_id: str,
        idempotency_key: str,
    ) -> None:
        self._raise("release_hold")


class UnavailableWeatherProvider(_Unavailable):
    async def forecast(
        self,
        location: AirportLocation,
        instant: datetime,
        *,
        correlation_id: str | None = None,
        language: str = "vi",
    ) -> WeatherForecast:
        self._raise("forecast")


class UnavailablePaymentProvider(_Unavailable):
    async def setup_method(
        self,
        request: PaymentMethodSetupRequest,
        idempotency_key: str,
    ) -> PaymentMethodSetupResult:
        self._raise("setup_method")

    async def authorize(
        self,
        amount: Money,
        payment_method_reference: SecretStr,
        idempotency_key: str,
    ) -> PaymentResult:
        self._raise("authorize")

    async def capture(
        self,
        authorization_reference: SecretStr,
        amount: Money,
        idempotency_key: str,
    ) -> PaymentResult:
        self._raise("capture")

    async def cancel(
        self,
        authorization_reference: SecretStr,
        idempotency_key: str,
    ) -> PaymentResult:
        self._raise("cancel")

    async def refund(
        self,
        transaction_reference: SecretStr,
        amount: Money,
        idempotency_key: str,
    ) -> PaymentResult:
        self._raise("refund")


class UnavailableNotificationProvider(_Unavailable):
    async def send(
        self,
        template: str,
        destination: NotificationDestination,
        idempotency_key: str,
        *,
        variables: Mapping[str, JsonValue] | None = None,
    ) -> NotificationResult:
        self._raise("send")
