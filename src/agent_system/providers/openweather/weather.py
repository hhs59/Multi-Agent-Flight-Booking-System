from __future__ import annotations

from datetime import datetime
from typing import Any

import httpx
from pydantic import SecretStr

from agent_system.domain.locations import AirportLocation
from agent_system.domain.provider_services import WeatherForecast
from agent_system.domain.values import ExecutionMode
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.errors import (
    ProviderAuthenticationError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)
from agent_system.providers.openweather.mapper import map_forecast

OPENWEATHER_FORECAST_URL = "https://api.openweathermap.org/data/2.5/forecast"


class OpenWeatherProvider:
    name = "openweather"

    def __init__(
        self,
        *,
        api_key: SecretStr,
        environment: ExecutionMode,
        http_client: httpx.AsyncClient,
        clock: Clock | None = None,
    ) -> None:
        if environment is ExecutionMode.MOCK:
            raise ValueError("OpenWeather cannot run in mock mode")
        if not api_key.get_secret_value().strip():
            raise ValueError("OpenWeather API key cannot be blank")
        self.api_key = api_key
        self.environment = environment
        self.http_client = http_client
        self.clock = clock or SystemClock()

    async def forecast(
        self,
        location: AirportLocation,
        instant: datetime,
        *,
        correlation_id: str | None = None,
        language: str = "vi",
    ) -> WeatherForecast:
        operation = "forecast"
        try:
            response = await self.http_client.get(
                OPENWEATHER_FORECAST_URL,
                params={
                    "lat": str(location.coordinates.latitude),
                    "lon": str(location.coordinates.longitude),
                    "appid": self.api_key.get_secret_value(),
                    "units": "metric",
                    "lang": "vi" if language == "vi" else "en",
                },
                headers={"Accept": "application/json"},
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                provider=self.name,
                operation=operation,
                safe_message="weather request timed out",
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                provider=self.name,
                operation=operation,
                safe_message="weather provider could not be reached",
            ) from exc
        if response.status_code in {401, 403}:
            raise ProviderAuthenticationError(
                provider=self.name,
                operation=operation,
                safe_message="weather provider authentication failed",
                http_status=response.status_code,
            )
        if response.status_code == 429:
            retry_after = response.headers.get("Retry-After")
            try:
                bounded_retry = float(retry_after) if retry_after is not None else None
                if bounded_retry is not None and not 0 <= bounded_retry <= 300:
                    bounded_retry = None
            except ValueError:
                bounded_retry = None
            raise ProviderRateLimitError(
                provider=self.name,
                operation=operation,
                safe_message="weather provider rate limited the request",
                retry_after_seconds=bounded_retry,
                http_status=429,
            )
        if response.status_code >= 500:
            raise ProviderUnavailableError(
                provider=self.name,
                operation=operation,
                safe_message="weather provider is unavailable",
                http_status=response.status_code,
            )
        if response.status_code >= 400:
            raise ProviderValidationError(
                provider=self.name,
                operation=operation,
                safe_message="weather provider rejected the request",
                http_status=response.status_code,
            )
        try:
            payload: Any = response.json()
        except ValueError as exc:
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation=operation,
                safe_message="weather provider returned malformed JSON",
            ) from exc
        if not isinstance(payload, dict):
            raise ProviderMalformedResponseError(
                provider=self.name,
                operation=operation,
                safe_message="weather provider returned malformed JSON",
            )
        return map_forecast(
            payload,
            location=location,
            requested_at=instant,
            retrieved_at=self.clock.now(),
            environment=self.environment,
            correlation_id=correlation_id,
        )
