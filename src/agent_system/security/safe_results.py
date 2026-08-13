from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any

from agent_system.security.messages import sanitize_message_text

SAFE_RESULT_SCHEMA_VERSION = 1
MAX_SAFE_RESULT_BYTES = 64 * 1024
MAX_SAFE_ERRORS = 20
MAX_SAFE_ERROR_LENGTH = 160

_FORBIDDEN_SAFE_KEY_TOKENS = frozenset(
    {
        "access_token",
        "api_key",
        "authorization",
        "card_number",
        "client_secret",
        "correlation_id",
        "cvv",
        "email",
        "legal_name",
        "passport",
        "passenger_identity",
        "payment",
        "payment_token",
        "passenger_name",
        "phone",
        "phone_number",
        "provider_offer_id",
        "provider_payload",
        "raw_payload",
        "raw_provider",
        "refresh_token",
        "secret",
        "session_token",
        "user_id",
    }
)


class SafeResultError(ValueError):
    """A structured result crossed the safe persistence boundary."""


def _key_is_forbidden(key: str) -> bool:
    normalized = key.casefold().replace("-", "_")
    if normalized in _FORBIDDEN_SAFE_KEY_TOKENS:
        return True
    compact = "".join(character for character in normalized if character.isalnum())
    forbidden_fragments = (
        "provider_offer_id",
        "providerofferid",
        "provider_payload",
        "providerpayload",
        "raw_payload",
        "rawpayload",
        "raw_provider_payload",
        "rawproviderpayload",
        "passenger_identity",
        "passengeridentity",
        "passenger_name",
        "passengername",
        "passport_number",
        "passportnumber",
        "payment_data",
        "paymentdata",
        "payment_authorization",
        "paymentauthorization",
        "phone_number",
        "phonenumber",
        "user_identity",
        "useridentity",
        "email",
        "phone",
        "passport",
        "legal_name",
    )
    return any(fragment in normalized or fragment in compact for fragment in forbidden_fragments)


def _walk(value: Any, path: str = "safe_result") -> None:
    if isinstance(value, Mapping):
        for key, nested in value.items():
            if not isinstance(key, str):
                raise SafeResultError(f"safe result keys must be strings: {path}")
            if _key_is_forbidden(key):
                raise SafeResultError(f"forbidden safe result key: {path}.{key}")
            _walk(nested, f"{path}.{key}")
    elif isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        for index, nested in enumerate(value):
            _walk(nested, f"{path}[{index}]")
    elif isinstance(value, (str, int, bool)) or value is None:
        return
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise SafeResultError(f"safe result numbers must be finite: {path}")
    else:
        raise SafeResultError(f"unsupported safe result value at {path}")


def sanitize_safe_result(value: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise SafeResultError("safe result must be an object")
    copied = deepcopy(dict(value))

    def sanitize_strings(item: Any) -> Any:
        if isinstance(item, Mapping):
            return {key: sanitize_strings(nested) for key, nested in item.items()}
        if isinstance(item, list):
            return [sanitize_strings(nested) for nested in item]
        if isinstance(item, tuple):
            return [sanitize_strings(nested) for nested in item]
        if isinstance(item, str):
            return sanitize_message_text(item).text
        return item

    sanitized = sanitize_strings(copied)
    _walk(sanitized)
    try:
        encoded = json.dumps(
            sanitized,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise SafeResultError("safe result must contain JSON values") from exc
    if len(encoded) > MAX_SAFE_RESULT_BYTES:
        raise SafeResultError("safe result exceeds 64 KiB")
    return sanitized


def validate_safe_errors(errors: Sequence[str]) -> list[str]:
    if len(errors) > MAX_SAFE_ERRORS:
        raise SafeResultError("too many safe errors")
    result: list[str] = []
    for error in errors:
        if not isinstance(error, str) or not error or len(error) > MAX_SAFE_ERROR_LENGTH:
            raise SafeResultError("safe errors must be bounded strings")
        result.append(error)
    return result


__all__ = [
    "MAX_SAFE_RESULT_BYTES",
    "SAFE_RESULT_SCHEMA_VERSION",
    "SafeResultError",
    "sanitize_safe_result",
    "validate_safe_errors",
]
