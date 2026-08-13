from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Protocol, runtime_checkable

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
from agent_system.domain.location_resolution import LocationLookupRequest, LocationSuggestion
from agent_system.domain.locations import AirportLocation
from agent_system.domain.provider_services import (
    NotificationDestination,
    NotificationResult,
    PaymentMethodSetupRequest,
    PaymentMethodSetupResult,
    PaymentResult,
    WeatherForecast,
)
from agent_system.domain.recommendations import (
    PlaceCandidate,
    PlaceSearchRequest,
    PlaceSourceEnvironment,
)
from agent_system.domain.values import ExecutionMode, Money


@runtime_checkable
class FlightProvider(Protocol):
    name: str
    environment: ExecutionMode

    def capabilities(self) -> ProviderCapabilities: ...

    async def search(
        self,
        criteria: FlightSearchCriteria,
        *,
        correlation_id: str | None = None,
    ) -> SearchResultPage: ...

    async def reprice(
        self,
        provider_offer_id: str,
        expected: FlightOffer,
        *,
        correlation_id: str | None = None,
    ) -> RepriceResult: ...

    async def create_order(
        self,
        quote: BookingQuote,
        travelers: tuple[TravelerSnapshot, ...],
        idempotency_key: str,
    ) -> ProviderOrderReference: ...

    async def get_order(self, provider_order_id: str) -> ProviderOrderReference: ...

    async def cancel_order(
        self,
        provider_order_id: str,
        idempotency_key: str,
    ) -> ProviderOrderReference: ...

    async def create_hold(
        self,
        quote: BookingQuote,
        travelers: tuple[TravelerSnapshot, ...],
        idempotency_key: str,
    ) -> HoldReference: ...

    async def release_hold(
        self,
        provider_hold_id: str,
        idempotency_key: str,
    ) -> None: ...


@runtime_checkable
class WeatherProvider(Protocol):
    name: str
    environment: ExecutionMode

    async def forecast(
        self,
        location: AirportLocation,
        instant: datetime,
        *,
        correlation_id: str | None = None,
        language: str = "vi",
    ) -> WeatherForecast: ...


@runtime_checkable
class PlacesProvider(Protocol):
    name: str
    environment: PlaceSourceEnvironment

    async def search(self, request: PlaceSearchRequest) -> tuple[PlaceCandidate, ...]: ...


@runtime_checkable
class LocationProvider(Protocol):
    name: str
    environment: ExecutionMode

    async def suggest(
        self,
        request: LocationLookupRequest,
        *,
        correlation_id: str | None = None,
    ) -> tuple[LocationSuggestion, ...]: ...


@runtime_checkable
class PaymentProvider(Protocol):
    name: str
    environment: ExecutionMode

    async def setup_method(
        self,
        request: PaymentMethodSetupRequest,
        idempotency_key: str,
    ) -> PaymentMethodSetupResult: ...

    async def authorize(
        self,
        amount: Money,
        payment_method_reference: SecretStr,
        idempotency_key: str,
    ) -> PaymentResult: ...

    async def capture(
        self,
        authorization_reference: SecretStr,
        amount: Money,
        idempotency_key: str,
    ) -> PaymentResult: ...

    async def cancel(
        self,
        authorization_reference: SecretStr,
        idempotency_key: str,
    ) -> PaymentResult: ...

    async def refund(
        self,
        transaction_reference: SecretStr,
        amount: Money,
        idempotency_key: str,
    ) -> PaymentResult: ...


@runtime_checkable
class NotificationProvider(Protocol):
    name: str
    environment: ExecutionMode

    async def send(
        self,
        template: str,
        destination: NotificationDestination,
        idempotency_key: str,
        *,
        variables: Mapping[str, JsonValue] | None = None,
    ) -> NotificationResult: ...
