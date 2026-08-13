from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from agent_system.providers.duffel.settings import DuffelSettings
from agent_system.providers.errors import (
    OfferExpiredError,
    OfferUnavailableError,
    ProviderAuthenticationError,
    ProviderError,
    ProviderMalformedResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    ProviderValidationError,
)


def _first_error(payload: Mapping[str, Any] | None) -> tuple[str | None, str | None, str | None]:
    if payload is None:
        return None, None, None
    errors = payload.get("errors")
    if not isinstance(errors, list) or not errors or not isinstance(errors[0], Mapping):
        return None, None, None
    error = errors[0]
    values = []
    for key in ("code", "type", "title", "message"):
        value = error.get(key)
        values.append(str(value)[:160] if value is not None else None)
    return values[0], values[1], values[2] or values[3]


def _retry_after(headers: httpx.Headers | None) -> float | None:
    if headers is None:
        return None
    raw = headers.get("Retry-After")
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        return None
    return value if 0 <= value <= 300 else None


def map_duffel_error(
    *,
    status_code: int,
    payload: Mapping[str, Any] | None,
    operation: str,
    headers: httpx.Headers | None = None,
) -> ProviderError:
    code, error_type, title = _first_error(payload)
    base = {
        "provider": "duffel",
        "operation": operation,
        "provider_code": code or error_type,
        "http_status": status_code,
    }
    if code in {"offer_expired", "expired_offer"}:
        return OfferExpiredError(
            **base,
            safe_message="flight offer has expired",
        )
    if code in {"offer_no_longer_available", "offer_unavailable"} or status_code == 404:
        return OfferUnavailableError(
            **base,
            safe_message="flight offer is no longer available",
        )
    if status_code in {401, 403} or error_type == "authentication_error":
        return ProviderAuthenticationError(
            **base,
            safe_message="provider authentication failed",
        )
    if status_code == 429 or error_type == "rate_limit_error":
        return ProviderRateLimitError(
            **base,
            safe_message="provider rate limited the request",
            retry_after_seconds=_retry_after(headers),
        )
    if status_code in {400, 422} or error_type in {"validation_error", "invalid_request_error"}:
        return ProviderValidationError(
            **base,
            safe_message=title or "provider rejected the request",
        )
    if status_code >= 500 or error_type in {"api_error", "airline_error"}:
        return ProviderUnavailableError(
            **base,
            safe_message="provider service is unavailable",
        )
    return ProviderError(
        **base,
        safe_message="provider request failed",
    )


class DuffelClient:
    def __init__(self, settings: DuffelSettings, http_client: httpx.AsyncClient) -> None:
        self.settings = settings
        self.http_client = http_client

    async def request_envelope(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | bool] | None = None,
        json_body: Mapping[str, Any] | None = None,
        operation: str,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
        timeout_seconds: float | None = None,
    ) -> DuffelResponse:
        headers = {
            "Authorization": f"Bearer {self.settings.access_token.get_secret_value()}",
            "Accept": "application/json",
            "Accept-Encoding": "gzip",
            "Duffel-Version": self.settings.api_version,
        }
        if json_body is not None:
            headers["Content-Type"] = "application/json"
        if correlation_id:
            headers["x-client-correlation-id"] = correlation_id[:512]
        if idempotency_key:
            headers["Idempotency-Key"] = idempotency_key[:120]
        request_kwargs: dict[str, Any] = {
            "params": params,
            "json": json_body,
            "headers": headers,
        }
        if timeout_seconds is not None:
            request_kwargs["timeout"] = timeout_seconds
        try:
            response = await self.http_client.request(
                method,
                f"{self.settings.base_url}{path}",
                **request_kwargs,
            )
        except httpx.TimeoutException as exc:
            raise ProviderTimeoutError(
                provider="duffel",
                operation=operation,
                safe_message="provider request timed out",
            ) from exc
        except httpx.RequestError as exc:
            raise ProviderUnavailableError(
                provider="duffel",
                operation=operation,
                safe_message="provider could not be reached",
            ) from exc

        payload: Mapping[str, Any] | None
        try:
            decoded = response.json()
            payload = decoded if isinstance(decoded, Mapping) else None
        except ValueError:
            payload = None
        if response.status_code >= 400:
            raise map_duffel_error(
                status_code=response.status_code,
                payload=payload,
                operation=operation,
                headers=response.headers,
            )
        if payload is None:
            raise ProviderMalformedResponseError(
                provider="duffel",
                operation=operation,
                safe_message="provider returned malformed JSON",
                http_status=response.status_code,
            )
        safe_headers = {
            key: response.headers[key]
            for key in ("request-id", "duffel-request-id", "retry-after")
            if key in response.headers
        }
        return DuffelResponse(
            status_code=response.status_code,
            payload=dict(payload),
            headers=safe_headers,
        )

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, str | int | bool] | None = None,
        json_body: Mapping[str, Any] | None = None,
        operation: str,
        correlation_id: str | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        response = await self.request_envelope(
            method,
            path,
            params=params,
            json_body=json_body,
            operation=operation,
            correlation_id=correlation_id,
            idempotency_key=idempotency_key,
        )
        return response.payload


@dataclass(frozen=True)
class DuffelResponse:
    status_code: int
    payload: dict[str, Any]
    headers: dict[str, str]
