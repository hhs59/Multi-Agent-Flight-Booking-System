from __future__ import annotations

# Public safety boundaries shared by domain contracts and application services.
MAX_PROVIDER_OFFERS_PER_ATTEMPT = 50
MAX_AGGREGATE_OFFERS = 100
MAX_CLIENT_OFFERS = 20
MAX_PRESENTED_OFFERS = 20

__all__ = [
    "MAX_AGGREGATE_OFFERS",
    "MAX_CLIENT_OFFERS",
    "MAX_PRESENTED_OFFERS",
    "MAX_PROVIDER_OFFERS_PER_ATTEMPT",
]
