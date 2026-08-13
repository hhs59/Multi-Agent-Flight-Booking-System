from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from pydantic import SecretStr

from agent_system.domain.values import ExecutionMode

DUFFEL_BASE_URL = "https://api.duffel.com"
DUFFEL_MIN_ORDER_TIMEOUT_SECONDS = 130.0


@dataclass(frozen=True, repr=False)
class DuffelSettings:
    access_token: SecretStr
    environment: ExecutionMode
    order_enabled: bool = False
    order_timeout_seconds: float = DUFFEL_MIN_ORDER_TIMEOUT_SECONDS
    settlement_mode: str = "balance"
    offer_ttl: timedelta = timedelta(minutes=30)
    api_version: str = "v2"

    def __post_init__(self) -> None:
        if self.environment not in {ExecutionMode.SANDBOX, ExecutionMode.PRODUCTION}:
            raise ValueError("Duffel supports only sandbox or production mode")
        if not self.access_token.get_secret_value().strip():
            raise ValueError("Duffel access token cannot be blank")
        if self.offer_ttl <= timedelta(0):
            raise ValueError("Duffel offer TTL must be positive")
        if not self.api_version.strip():
            raise ValueError("Duffel API version cannot be blank")
        if self.settlement_mode != "balance":
            raise ValueError("Duffel settlement mode must be balance")
        if self.order_timeout_seconds <= 0:
            raise ValueError("Duffel order timeout must be greater than zero")
        if self.order_enabled and self.order_timeout_seconds < DUFFEL_MIN_ORDER_TIMEOUT_SECONDS:
            raise ValueError(
                "Duffel order timeout must be at least 130 seconds when orders are enabled"
            )

    @property
    def base_url(self) -> str:
        # Duffel test/live mode is selected by the token; both use the same host.
        return DUFFEL_BASE_URL
