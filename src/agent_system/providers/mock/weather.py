from __future__ import annotations

from datetime import datetime, timedelta
from decimal import Decimal

from agent_system.domain.locations import AirportLocation
from agent_system.domain.provider_services import ForecastStatus, WeatherForecast
from agent_system.domain.values import ExecutionMode, ProviderMetadata
from agent_system.providers.clock import Clock, SystemClock
from agent_system.providers.errors import ProviderRateLimitError, ProviderTimeoutError
from agent_system.providers.mock.scenarios import MockScenario

_WEATHER_EN = {
    "HAN": "overcast clouds",
    "SGN": "scattered showers",
    "DAD": "clear sky",
    "PQC": "partly cloudy",
    "CXR": "sunny",
}


_WEATHER = {
    "HAN": (Decimal("28"), "nhiều mây", Decimal("0.25")),
    "SGN": (Decimal("32"), "mưa rào rải rác", Decimal("0.60")),
    "DAD": (Decimal("30"), "trời quang", Decimal("0.10")),
    "PQC": (Decimal("29"), "có mây", Decimal("0.35")),
    "CXR": (Decimal("31"), "nắng", Decimal("0.05")),
}


class MockWeatherProvider:
    name = "mock"

    def __init__(
        self,
        *,
        environment: ExecutionMode = ExecutionMode.MOCK,
        scenario: MockScenario = MockScenario.SUCCESS,
        clock: Clock | None = None,
    ) -> None:
        self.environment = environment
        self.scenario = scenario
        self.clock = clock or SystemClock()

    async def forecast(
        self,
        location: AirportLocation,
        instant: datetime,
        *,
        correlation_id: str | None = None,
        language: str = "vi",
    ) -> WeatherForecast:
        now = self.clock.now()
        if self.scenario is MockScenario.TIMEOUT:
            raise ProviderTimeoutError(
                provider=self.name,
                operation="forecast",
                safe_message="synthetic provider timeout",
            )
        if self.scenario is MockScenario.RATE_LIMIT:
            raise ProviderRateLimitError(
                provider=self.name,
                operation="forecast",
                safe_message="synthetic provider rate limit",
                retry_after_seconds=1,
            )
        metadata = ProviderMetadata(
            provider=self.name,
            environment=self.environment,
            is_live=False,
            retrieved_at=now,
            expires_at=now + timedelta(hours=1),
            correlation_id=correlation_id,
        )
        values = _WEATHER.get(location.iata_code)
        if values is None or self.scenario is MockScenario.NO_RESULTS:
            return WeatherForecast(
                metadata=metadata,
                location=location,
                requested_at=instant,
                status=ForecastStatus.UNAVAILABLE,
                reason="synthetic forecast unavailable",
            )
        temperature, description, precipitation = values
        if language != "vi":
            description = _WEATHER_EN.get(location.iata_code, description)
        return WeatherForecast(
            metadata=metadata,
            location=location,
            requested_at=instant,
            forecast_at=instant,
            status=ForecastStatus.AVAILABLE,
            temperature_c=temperature,
            description=description,
            precipitation_probability=precipitation,
        )
