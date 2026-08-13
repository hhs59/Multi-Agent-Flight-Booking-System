from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from agent_system.domain.provider_services import ForecastStatus, WeatherForecast
from agent_system.domain.values import ProviderMetadata
from agent_system.providers.cache import WeatherCache, WeatherCacheKey
from agent_system.providers.clock import Clock
from agent_system.providers.contracts import WeatherProvider
from agent_system.providers.errors import ProviderError
from agent_system.providers.localization import AirportCatalog
from agent_system.providers.resilience import ProviderExecutor


def _three_hour_bucket(instant: datetime) -> datetime:
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError("weather request instant must be timezone-aware")
    normalized = instant.astimezone(UTC)
    return normalized.replace(
        hour=(normalized.hour // 3) * 3,
        minute=0,
        second=0,
        microsecond=0,
    )


class WeatherService:
    def __init__(
        self,
        provider: WeatherProvider,
        cache: WeatherCache,
        executor: ProviderExecutor,
        clock: Clock,
        *,
        airports: AirportCatalog | None = None,
    ) -> None:
        if executor.environment is not provider.environment:
            raise ValueError("provider and executor environments must match")
        self.provider = provider
        self.cache = cache
        self.executor = executor
        self.clock = clock
        self.airports = airports or AirportCatalog.from_package_data()

    async def forecast(
        self,
        airport_or_alias: str,
        instant: datetime,
        *,
        correlation_id: str | None = None,
        language: str = "vi",
    ) -> WeatherForecast:
        location = self.airports.resolve(airport_or_alias)
        normalized_language = "vi" if language == "vi" else "en"
        key = WeatherCacheKey(
            provider=self.provider.name,
            environment=self.provider.environment,
            airport_code=location.iata_code,
            forecast_bucket=_three_hour_bucket(instant),
            language=normalized_language,
        )
        now = self.clock.now()
        cached = self.cache.get(key, now=now)
        if cached is not None:
            return cached
        try:
            forecast = await self.executor.execute(
                provider=self.provider.name,
                operation="forecast",
                call=lambda: self.provider.forecast(
                    location,
                    instant,
                    correlation_id=correlation_id,
                    language=normalized_language,
                ),
                retry_safe=True,
            )
        except ProviderError:
            now = self.clock.now()
            forecast = WeatherForecast(
                metadata=ProviderMetadata(
                    provider=self.provider.name,
                    environment=self.provider.environment,
                    is_live=self.provider.environment.value == "production",
                    retrieved_at=now,
                    expires_at=now + timedelta(minutes=5),
                    correlation_id=correlation_id,
                ),
                location=location,
                requested_at=instant,
                status=ForecastStatus.UNAVAILABLE,
                reason="weather provider unavailable",
            )
        self.cache.put(key, forecast, now=self.clock.now())
        return forecast

    async def forecast_for_date(
        self,
        airport_or_alias: str,
        travel_date: date,
        *,
        correlation_id: str | None = None,
        language: str = "vi",
    ) -> WeatherForecast:
        """Fetch the daytime forecast for a travel date in the destination timezone."""
        location = self.airports.resolve(airport_or_alias)
        local_noon = datetime.combine(
            travel_date,
            time(hour=12),
            tzinfo=ZoneInfo(location.timezone),
        )
        return await self.forecast(
            location.iata_code,
            local_noon,
            correlation_id=correlation_id,
            language=language,
        )


def safe_weather_summary(forecast: WeatherForecast) -> dict[str, object]:
    """Return the bounded, non-sensitive forecast shape exposed to clients."""
    return {
        "status": forecast.status.value,
        "destination_airport": forecast.location.iata_code,
        "city": forecast.location.city_name_en,
        "requested_at": forecast.requested_at.isoformat(),
        "forecast_at": forecast.forecast_at.isoformat() if forecast.forecast_at else None,
        "temperature_c": (
            str(forecast.temperature_c) if forecast.temperature_c is not None else None
        ),
        "description": forecast.description,
        "precipitation_probability": (
            str(forecast.precipitation_probability)
            if forecast.precipitation_probability is not None
            else None
        ),
        "source": "OpenWeather" if forecast.metadata.provider == "openweather" else "Weather",
        "updated_at": forecast.metadata.retrieved_at.isoformat(),
        "reason": forecast.reason,
    }
