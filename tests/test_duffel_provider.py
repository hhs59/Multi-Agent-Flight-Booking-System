import pytest
from pydantic import SecretStr

from agent_system.domain.values import ExecutionMode
from agent_system.providers.duffel.client import map_duffel_error
from agent_system.providers.duffel.settings import DuffelSettings
from agent_system.providers.errors import (
    OfferExpiredError,
    OfferUnavailableError,
    ProviderAuthenticationError,
    ProviderRateLimitError,
)


def test_duffel_settings_validation():
    valid = DuffelSettings(
        access_token=SecretStr("duffel_test_sample_token_12345"),
        environment=ExecutionMode.SANDBOX,
        order_enabled=True,
    )
    assert valid.environment == ExecutionMode.SANDBOX
    assert valid.base_url == "https://api.duffel.com"

    with pytest.raises(ValueError, match="Duffel supports only sandbox or production mode"):
        DuffelSettings(
            access_token=SecretStr("test"),
            environment=ExecutionMode.MOCK,
        )


def test_map_duffel_error_codes():
    expired_err = map_duffel_error(
        status_code=400,
        payload={"errors": [{"code": "offer_expired", "title": "Offer has expired"}]},
        operation="reprice",
    )
    assert isinstance(expired_err, OfferExpiredError)
    assert expired_err.safe_message == "flight offer has expired"

    unavailable_err = map_duffel_error(
        status_code=404,
        payload={"errors": [{"code": "not_found", "title": "Offer not found"}]},
        operation="reprice",
    )
    assert isinstance(unavailable_err, OfferUnavailableError)

    auth_err = map_duffel_error(
        status_code=401,
        payload={"errors": [{"type": "authentication_error", "message": "Invalid token"}]},
        operation="search",
    )
    assert isinstance(auth_err, ProviderAuthenticationError)

    rate_limit_err = map_duffel_error(
        status_code=429,
        payload={"errors": [{"type": "rate_limit_error", "message": "Too many requests"}]},
        operation="search",
    )
    assert isinstance(rate_limit_err, ProviderRateLimitError)
