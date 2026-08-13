from __future__ import annotations

from dataclasses import dataclass, field
from threading import Lock

import httpx

from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.contracts import (
    FlightProvider,
    LocationProvider,
    NotificationProvider,
    PaymentProvider,
    PlacesProvider,
    WeatherProvider,
)
from agent_system.providers.duffel.client import DuffelClient
from agent_system.providers.duffel.flights import DuffelFlightProvider
from agent_system.providers.duffel.locations import DuffelLocationProvider
from agent_system.providers.duffel.settings import DuffelSettings
from agent_system.providers.location_fixtures import (
    CatalogLocationProvider,
    FixtureLocationProvider,
    UnavailableLocationProvider,
)
from agent_system.providers.mock.flights import MockFlightProvider
from agent_system.providers.mock.notifications import MockNotificationProvider
from agent_system.providers.mock.payments import MockPaymentProvider
from agent_system.providers.mock.weather import MockWeatherProvider
from agent_system.providers.openweather.weather import OpenWeatherProvider
from agent_system.providers.places import (
    CuratedPlacesProvider,
    FixturePlacesProvider,
    UnavailablePlacesProvider,
)
from agent_system.providers.settings import ProviderSettings
from agent_system.providers.unavailable import (
    UnavailableFlightProvider,
    UnavailableNotificationProvider,
    UnavailablePaymentProvider,
    UnavailableWeatherProvider,
)


@dataclass(frozen=True)
class ProviderRegistry:
    flight: FlightProvider
    weather: WeatherProvider
    payment: PaymentProvider
    notifications: NotificationProvider
    locations: LocationProvider = field(default_factory=CatalogLocationProvider)
    places: PlacesProvider | None = None
    http_client: httpx.AsyncClient | None = field(default=None, repr=False)
    owns_http_client: bool = field(default=False, repr=False)

    async def aclose(self) -> None:
        if self.owns_http_client and self.http_client is not None:
            await self.http_client.aclose()


def _http_client(settings: ProviderSettings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        timeout=httpx.Timeout(
            connect=settings.connect_timeout_seconds,
            read=settings.read_timeout_seconds,
            write=settings.read_timeout_seconds,
            pool=settings.connect_timeout_seconds,
        ),
        follow_redirects=False,
    )


def build_provider_registry(
    settings: ProviderSettings,
    *,
    clock: Clock | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> ProviderRegistry:
    active_clock = clock or SystemClock()
    requires_http = (
        settings.flight_provider == "duffel"
        or settings.weather_provider == "openweather"
        or settings.location_provider == "duffel"
    )
    owns_client = requires_http and http_client is None
    client = http_client or (_http_client(settings) if requires_http else None)

    duffel_settings: DuffelSettings | None = None
    duffel_client: DuffelClient | None = None
    if settings.flight_provider == "duffel" or settings.location_provider == "duffel":
        assert client is not None
        assert settings.duffel_access_token is not None
        duffel_settings = DuffelSettings(
            access_token=settings.duffel_access_token,
            environment=settings.execution_mode,
            order_enabled=settings.duffel_order_enabled,
            order_timeout_seconds=settings.duffel_order_timeout_seconds,
            settlement_mode=settings.duffel_settlement_mode,
        )
        duffel_client = DuffelClient(duffel_settings, client)

    if settings.flight_provider == "mock":
        flight: FlightProvider = MockFlightProvider(clock=active_clock)
    elif settings.flight_provider == "duffel":
        assert duffel_settings is not None
        assert duffel_client is not None
        flight = DuffelFlightProvider(
            duffel_settings,
            duffel_client,
            clock=active_clock,
        )
    else:
        flight = UnavailableFlightProvider(
            settings.flight_provider,
            settings.execution_mode,
        )

    if settings.weather_provider == "mock":
        weather: WeatherProvider = MockWeatherProvider(clock=active_clock)
    elif settings.weather_provider == "openweather":
        assert client is not None
        assert settings.openweathermap_api_key is not None
        weather = OpenWeatherProvider(
            api_key=settings.openweathermap_api_key,
            environment=settings.execution_mode,
            http_client=client,
            clock=active_clock,
        )
    else:
        weather = UnavailableWeatherProvider(
            settings.weather_provider,
            settings.execution_mode,
        )

    payment: PaymentProvider
    if settings.payment_provider == "mock":
        payment = MockPaymentProvider(clock=active_clock)
    else:
        payment = UnavailablePaymentProvider(
            settings.payment_provider,
            settings.execution_mode,
        )

    notifications: NotificationProvider
    if settings.notification_provider == "mock":
        notifications = MockNotificationProvider(clock=active_clock)
    else:
        notifications = UnavailableNotificationProvider(
            settings.notification_provider,
            settings.execution_mode,
        )

    if settings.location_provider == "catalog":
        locations: LocationProvider = CatalogLocationProvider(
            environment=settings.execution_mode,
        )
    elif settings.location_provider == "fixture":
        locations = FixtureLocationProvider()
    elif settings.location_provider == "duffel":
        assert duffel_settings is not None
        assert duffel_client is not None
        locations = DuffelLocationProvider(duffel_settings, duffel_client)
    else:
        locations = UnavailableLocationProvider(
            settings.location_provider,
            settings.execution_mode,
        )

    if settings.places_provider == "curated":
        places: PlacesProvider = CuratedPlacesProvider(
            clock=active_clock,
            cache_ttl_seconds=settings.places_cache_ttl_seconds,
        )
    elif settings.places_provider == "fixture":
        places = FixturePlacesProvider(clock=active_clock)
    else:
        places = UnavailablePlacesProvider(
            settings.places_provider,
            settings.execution_mode,
        )

    return ProviderRegistry(
        flight=flight,
        weather=weather,
        payment=payment,
        notifications=notifications,
        locations=locations,
        places=places,
        http_client=client,
        owns_http_client=owns_client,
    )


_registry: ProviderRegistry | None = None
_registry_lock = Lock()


def get_provider_registry() -> ProviderRegistry:
    global _registry
    if _registry is not None:
        return _registry
    with _registry_lock:
        if _registry is None:
            _registry = build_provider_registry(ProviderSettings.from_environment())
        return _registry


def reset_provider_registry_for_tests() -> ProviderRegistry | None:
    global _registry
    with _registry_lock:
        previous = _registry
        _registry = None
    return previous
