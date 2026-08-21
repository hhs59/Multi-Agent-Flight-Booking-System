from __future__ import annotations

import os
from dataclasses import dataclass

from pydantic import SecretStr

from agent_system.domain.values import ExecutionMode

_PROVIDER_CHOICES = {
    "flight": frozenset({"mock", "duffel", "unavailable"}),
    "weather": frozenset({"mock", "openweather", "unavailable"}),
    "payment": frozenset({"mock", "unavailable"}),
    "notification": frozenset({"mock", "unavailable"}),
    "places": frozenset({"curated", "fixture", "unavailable"}),
    "locations": frozenset({"catalog", "fixture", "duffel", "unavailable"}),
}


def _positive_float(value: str, name: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"{name} must be greater than zero")
    return parsed


def _bounded_int(value: str, name: str, *, minimum: int, maximum: int) -> int:
    parsed = int(value)
    if not minimum <= parsed <= maximum:
        raise ValueError(f"{name} must be between {minimum} and {maximum}")
    return parsed


def _optional_secret(value: str | None) -> SecretStr | None:
    if value is None or not value.strip():
        return None
    return SecretStr(value.strip())


def _environment_bool(value: str, name: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes"}:
        return True
    if normalized in {"0", "false", "no"}:
        return False
    raise ValueError(f"{name} must be one of: 1, 0, true, false, yes, no")


@dataclass(frozen=True, repr=False)
class ProviderSettings:
    execution_mode: ExecutionMode = ExecutionMode.MOCK
    flight_provider: str = "mock"
    weather_provider: str = "mock"
    payment_provider: str = "mock"
    notification_provider: str = "mock"
    connect_timeout_seconds: float = 3.0
    read_timeout_seconds: float = 10.0
    total_timeout_seconds: float = 15.0
    max_safe_retries: int = 2
    search_cache_max_entries: int = 256
    production_approved: bool = False
    duffel_access_token: SecretStr | None = None
    duffel_order_enabled: bool = False
    duffel_order_timeout_seconds: float = 130.0
    duffel_settlement_mode: str = "balance"
    openweathermap_api_key: SecretStr | None = None
    places_provider: str = "curated"
    places_recommendation_limit: int = 5
    places_recommendation_max_candidates: int = 20
    places_recommendation_timeout_seconds: float = 2.0
    places_cache_ttl_seconds: float = 300.0
    places_curated_fallback_enabled: bool = False
    location_provider: str = "catalog"
    location_lookup_limit: int = 8
    location_cache_ttl_seconds: float = 3600.0
    location_cache_max_entries: int = 512

    def __post_init__(self) -> None:
        selections = {
            "flight": self.flight_provider,
            "weather": self.weather_provider,
            "payment": self.payment_provider,
            "notification": self.notification_provider,
        }
        for kind, selected in selections.items():
            if selected not in _PROVIDER_CHOICES[kind]:
                raise ValueError(f"unsupported {kind} provider: {selected}")
        if self.places_provider not in _PROVIDER_CHOICES["places"]:
            raise ValueError(f"unsupported places provider: {self.places_provider}")
        if self.places_provider == "fixture" and self.execution_mode is not ExecutionMode.MOCK:
            raise ValueError("fixture places provider is available only in mock mode")
        if self.location_provider not in _PROVIDER_CHOICES["locations"]:
            raise ValueError(f"unsupported locations provider: {self.location_provider}")
        if self.location_provider == "fixture" and self.execution_mode is not ExecutionMode.MOCK:
            raise ValueError("fixture location provider is available only in mock mode")
        if self.location_provider == "duffel" and self.execution_mode is ExecutionMode.MOCK:
            raise ValueError("Duffel location provider is unavailable in mock mode")
        if self.execution_mode is ExecutionMode.MOCK:
            if any(selected != "mock" for selected in selections.values()):
                raise ValueError("mock execution mode can select only mock providers")
        elif self.execution_mode is ExecutionMode.PRODUCTION and any(
            selected == "mock" for selected in selections.values()
        ):
            raise ValueError("production mode cannot select mock providers")
        if self.execution_mode is ExecutionMode.PRODUCTION and not self.production_approved:
            raise ValueError("production providers require explicit server-side approval")
        if self.flight_provider == "duffel" and (
            self.duffel_access_token is None
            or not self.duffel_access_token.get_secret_value().strip()
        ):
            raise ValueError("Duffel access token is required when Duffel is selected")
        if self.location_provider == "duffel" and (
            self.duffel_access_token is None
            or not self.duffel_access_token.get_secret_value().strip()
        ):
            raise ValueError("Duffel access token is required when locations use Duffel")
        if self.duffel_settlement_mode != "balance":
            raise ValueError("Duffel settlement mode must be balance")
        if self.duffel_order_timeout_seconds <= 0:
            raise ValueError("DUFFEL_ORDER_TIMEOUT_SECONDS must be greater than zero")
        if self.duffel_order_enabled and self.duffel_order_timeout_seconds < 130:
            raise ValueError(
                "DUFFEL_ORDER_TIMEOUT_SECONDS must be at least 130 seconds when Duffel orders are enabled"
            )
        if self.weather_provider == "openweather" and self.openweathermap_api_key is None:
            raise ValueError("OpenWeather API key is required when OpenWeather is selected")
        if (
            min(
                self.connect_timeout_seconds,
                self.read_timeout_seconds,
                self.total_timeout_seconds,
            )
            <= 0
        ):
            raise ValueError("provider timeouts must be greater than zero")
        if self.total_timeout_seconds < self.connect_timeout_seconds:
            raise ValueError("total timeout cannot be shorter than connect timeout")
        if not 0 <= self.max_safe_retries <= 5:
            raise ValueError("max_safe_retries must be between zero and five")
        if not 1 <= self.search_cache_max_entries <= 10_000:
            raise ValueError("search_cache_max_entries must be between 1 and 10000")
        if not 1 <= self.places_recommendation_limit <= 20:
            raise ValueError("places_recommendation_limit must be between 1 and 20")
        if not self.places_recommendation_limit <= self.places_recommendation_max_candidates <= 20:
            raise ValueError(
                "places_recommendation_max_candidates must be between the recommendation limit and 20"
            )
        if not 0.05 <= self.places_recommendation_timeout_seconds <= 30:
            raise ValueError("places recommendation timeout must be between 0.05 and 30 seconds")
        if not 1 <= self.places_cache_ttl_seconds <= 86_400:
            raise ValueError("places cache TTL must be between 1 and 86400 seconds")
        if not 1 <= self.location_lookup_limit <= 10:
            raise ValueError("location lookup limit must be between 1 and 10")
        if not 1 <= self.location_cache_ttl_seconds <= 86_400:
            raise ValueError("location cache TTL must be between 1 and 86400 seconds")
        if not 1 <= self.location_cache_max_entries <= 10_000:
            raise ValueError("location cache max entries must be between 1 and 10000")

    @classmethod
    def from_environment(cls) -> ProviderSettings:
        mode = ExecutionMode(os.getenv("EXECUTION_MODE", "mock").strip().lower())
        defaults = {
            ExecutionMode.MOCK: ("mock", "mock", "mock", "mock", "catalog"),
            ExecutionMode.SANDBOX: (
                "duffel",
                "openweather",
                "unavailable",
                "unavailable",
                "duffel",
            ),
            ExecutionMode.PRODUCTION: (
                "duffel",
                "openweather",
                "unavailable",
                "unavailable",
                "duffel",
            ),
        }[mode]
        production_approved = os.getenv("PROVIDER_PRODUCTION_APPROVED", "").lower() in {
            "1",
            "true",
            "yes",
        }
        return cls(
            execution_mode=mode,
            flight_provider=os.getenv("FLIGHT_PROVIDER", defaults[0]).strip().lower(),
            weather_provider=os.getenv("WEATHER_PROVIDER", defaults[1]).strip().lower(),
            payment_provider=os.getenv("PAYMENT_PROVIDER", defaults[2]).strip().lower(),
            notification_provider=os.getenv("NOTIFICATION_PROVIDER", defaults[3]).strip().lower(),
            location_provider=os.getenv("LOCATION_PROVIDER", defaults[4]).strip().lower(),
            connect_timeout_seconds=_positive_float(
                os.getenv("PROVIDER_CONNECT_TIMEOUT_SECONDS", "3"),
                "PROVIDER_CONNECT_TIMEOUT_SECONDS",
            ),
            read_timeout_seconds=_positive_float(
                os.getenv("PROVIDER_READ_TIMEOUT_SECONDS", "10"),
                "PROVIDER_READ_TIMEOUT_SECONDS",
            ),
            total_timeout_seconds=_positive_float(
                os.getenv("PROVIDER_TOTAL_TIMEOUT_SECONDS", "15"),
                "PROVIDER_TOTAL_TIMEOUT_SECONDS",
            ),
            max_safe_retries=_bounded_int(
                os.getenv("PROVIDER_MAX_SAFE_RETRIES", "2"),
                "PROVIDER_MAX_SAFE_RETRIES",
                minimum=0,
                maximum=5,
            ),
            search_cache_max_entries=_bounded_int(
                os.getenv("PROVIDER_SEARCH_CACHE_MAX_ENTRIES", "256"),
                "PROVIDER_SEARCH_CACHE_MAX_ENTRIES",
                minimum=1,
                maximum=10_000,
            ),
            production_approved=production_approved,
            duffel_access_token=_optional_secret(os.getenv("DUFFEL_ACCESS_TOKEN")),
            duffel_order_enabled=_environment_bool(
                os.getenv("DUFFEL_ORDER_ENABLED", "false"), "DUFFEL_ORDER_ENABLED"
            ),
            duffel_order_timeout_seconds=_positive_float(
                os.getenv("DUFFEL_ORDER_TIMEOUT_SECONDS", "130"),
                "DUFFEL_ORDER_TIMEOUT_SECONDS",
            ),
            duffel_settlement_mode=os.getenv("DUFFEL_SETTLEMENT_MODE", "balance").strip().lower(),
            openweathermap_api_key=_optional_secret(os.getenv("OPENWEATHERMAP_API_KEY")),
            places_provider=os.getenv("PLACES_PROVIDER", "curated").strip().lower(),
            places_recommendation_limit=_bounded_int(
                os.getenv("PLACES_RECOMMENDATION_LIMIT", "5"),
                "PLACES_RECOMMENDATION_LIMIT",
                minimum=1,
                maximum=20,
            ),
            places_recommendation_max_candidates=_bounded_int(
                os.getenv("PLACES_RECOMMENDATION_MAX_CANDIDATES", "20"),
                "PLACES_RECOMMENDATION_MAX_CANDIDATES",
                minimum=1,
                maximum=20,
            ),
            places_recommendation_timeout_seconds=_positive_float(
                os.getenv("PLACES_RECOMMENDATION_TIMEOUT_SECONDS", "2"),
                "PLACES_RECOMMENDATION_TIMEOUT_SECONDS",
            ),
            places_cache_ttl_seconds=_positive_float(
                os.getenv("PLACES_CACHE_TTL_SECONDS", "300"),
                "PLACES_CACHE_TTL_SECONDS",
            ),
            places_curated_fallback_enabled=_environment_bool(
                os.getenv("PLACES_CURATED_FALLBACK_ENABLED", "false"),
                "PLACES_CURATED_FALLBACK_ENABLED",
            ),
            location_lookup_limit=_bounded_int(
                os.getenv("LOCATION_LOOKUP_LIMIT", "8"),
                "LOCATION_LOOKUP_LIMIT",
                minimum=1,
                maximum=10,
            ),
            location_cache_ttl_seconds=_positive_float(
                os.getenv("LOCATION_CACHE_TTL_SECONDS", "3600"),
                "LOCATION_CACHE_TTL_SECONDS",
            ),
            location_cache_max_entries=_bounded_int(
                os.getenv("LOCATION_CACHE_MAX_ENTRIES", "512"),
                "LOCATION_CACHE_MAX_ENTRIES",
                minimum=1,
                maximum=10_000,
            ),
        )
