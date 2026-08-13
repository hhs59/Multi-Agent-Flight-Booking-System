from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from agent_system.domain.locations import AirportLocation
from agent_system.domain.provider_services import ForecastStatus, WeatherForecast
from agent_system.domain.values import ExecutionMode, ProviderMetadata
from agent_system.providers.errors import ProviderMalformedResponseError


def _unavailable(
    *,
    location: AirportLocation,
    requested_at: datetime,
    retrieved_at: datetime,
    environment: ExecutionMode,
    correlation_id: str | None,
    reason: str,
) -> WeatherForecast:
    return WeatherForecast(
        metadata=ProviderMetadata(
            provider="openweather",
            environment=environment,
            is_live=environment is ExecutionMode.PRODUCTION,
            retrieved_at=retrieved_at,
            expires_at=retrieved_at + timedelta(minutes=30),
            correlation_id=correlation_id,
        ),
        location=location,
        requested_at=requested_at,
        status=ForecastStatus.UNAVAILABLE,
        reason=reason,
    )


def map_forecast(
    payload: Mapping[str, Any],
    *,
    location: AirportLocation,
    requested_at: datetime,
    retrieved_at: datetime,
    environment: ExecutionMode,
    correlation_id: str | None,
    maximum_distance: timedelta = timedelta(hours=3),
) -> WeatherForecast:
    if requested_at.tzinfo is None or requested_at.utcoffset() is None:
        raise ValueError("weather request instant must be timezone-aware")
    raw_entries = payload.get("list")
    if not isinstance(raw_entries, list):
        raise ProviderMalformedResponseError(
            provider="openweather",
            operation="map_forecast",
            safe_message="forecast response has no forecast list",
        )
    requested_utc = requested_at.astimezone(UTC)
    candidates: list[tuple[timedelta, datetime, Mapping[str, Any]]] = []
    for item in raw_entries:
        if not isinstance(item, Mapping):
            continue
        raw_timestamp = item.get("dt")
        if not isinstance(raw_timestamp, (int, float)):
            continue
        forecast_at = datetime.fromtimestamp(raw_timestamp, tz=UTC)
        candidates.append((abs(forecast_at - requested_utc), forecast_at, item))
    if not candidates:
        return _unavailable(
            location=location,
            requested_at=requested_utc,
            retrieved_at=retrieved_at,
            environment=environment,
            correlation_id=correlation_id,
            reason="weather forecast is unavailable for the requested time",
        )
    distance, forecast_at, closest = min(candidates, key=lambda value: value[0])
    if distance > maximum_distance:
        return _unavailable(
            location=location,
            requested_at=requested_utc,
            retrieved_at=retrieved_at,
            environment=environment,
            correlation_id=correlation_id,
            reason="weather forecast is outside the supported time window",
        )
    main = closest.get("main")
    weather = closest.get("weather")
    if (
        not isinstance(main, Mapping)
        or not isinstance(weather, list)
        or not weather
        or not isinstance(weather[0], Mapping)
    ):
        raise ProviderMalformedResponseError(
            provider="openweather",
            operation="map_forecast",
            safe_message="forecast response entry is malformed",
        )
    try:
        temperature = Decimal(str(main["temp"]))
        precipitation = Decimal(str(closest.get("pop", 0)))
        raw_description = weather[0]["description"]
        if not isinstance(raw_description, str):
            raise KeyError("description")
        description = raw_description.strip()
    except (KeyError, InvalidOperation) as exc:
        raise ProviderMalformedResponseError(
            provider="openweather",
            operation="map_forecast",
            safe_message="forecast response values are malformed",
        ) from exc
    if (
        not temperature.is_finite()
        or not precipitation.is_finite()
        or not Decimal("0") <= precipitation <= Decimal("1")
        or not description
    ):
        raise ProviderMalformedResponseError(
            provider="openweather",
            operation="map_forecast",
            safe_message="forecast response values are malformed",
        )
    return WeatherForecast(
        metadata=ProviderMetadata(
            provider="openweather",
            environment=environment,
            is_live=environment is ExecutionMode.PRODUCTION,
            retrieved_at=retrieved_at,
            expires_at=retrieved_at + timedelta(minutes=30),
            correlation_id=correlation_id,
        ),
        location=location,
        requested_at=requested_utc,
        forecast_at=forecast_at,
        status=ForecastStatus.AVAILABLE,
        temperature_c=temperature,
        description=description,
        precipitation_probability=precipitation,
    )
