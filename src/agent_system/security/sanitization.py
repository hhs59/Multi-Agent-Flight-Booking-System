from __future__ import annotations

import logging
import re
import traceback
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any
from uuid import UUID

from pydantic import SecretStr

REDACTED = "[REDACTED]"
_SENSITIVE_KEY_PARTS = (
    "access_token",
    "api_key",
    "authorization",
    "birth_date",
    "card_number",
    "client_secret",
    "cvv",
    "email",
    "encryption_key",
    "legal_name",
    "nationality",
    "passenger_name",
    "passport",
    "phone",
    "refresh_token",
    "secret",
    "session_token",
)
_EMAIL_PATTERN = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_BEARER_PATTERN = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]+")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_API_KEY_PATTERN = re.compile(r"\b(?:sk|pk)_[A-Za-z0-9_-]{12,}\b")
_QUERY_SECRET_PATTERN = re.compile(
    r"(?i)([?&](?:access_token|api_key|appid|client_secret|secret|token)=)[^&\s]+"
)
_CARD_PATTERN = re.compile(r"(?<!\d)(?:\d[ -]?){13,19}(?!\d)")
_PASSPORT_LABEL_PATTERN = re.compile(
    r"(?i)(passport(?:\s+number)?|h[oộ]\s*chi[eế]u)\s*(?:(?:is|l[aà])\s+|[:#-]\s*)?(?=[A-Z0-9]*\d)[A-Z0-9]{5,20}"
)
_POSSIBLE_ID_PATTERN = re.compile(r"(?<![A-Z0-9])[A-Z]{1,3}\d{6,12}(?![A-Z0-9])", re.IGNORECASE)
_APPLICATION_UUID_PATTERN = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_PHONE_PATTERN = re.compile(r"(?<!\d)(?:\+?\d[ .-]?){9,15}(?!\d)")


def _redact_possible_ids(value: str) -> str:
    uuid_spans = [match.span() for match in _APPLICATION_UUID_PATTERN.finditer(value)]
    matches = [
        match
        for match in _POSSIBLE_ID_PATTERN.finditer(value)
        if not any(start <= match.start() < end for start, end in uuid_spans)
    ]
    if not matches:
        return value
    rendered: list[str] = []
    cursor = 0
    for match in matches:
        rendered.append(value[cursor : match.start()])
        rendered.append("[POSSIBLE_ID_REDACTED]")
        cursor = match.end()
    rendered.append(value[cursor:])
    return "".join(rendered)


def sanitize_text(value: str, *, redact_email: bool = True) -> str:
    application_uuids: list[str] = []

    def preserve_uuid(match: re.Match[str]) -> str:
        application_uuids.append(match.group(0))
        return f"[[APPLICATION_UUID_{len(application_uuids) - 1}]]"

    sanitized = _APPLICATION_UUID_PATTERN.sub(preserve_uuid, value)
    sanitized = _BEARER_PATTERN.sub("Bearer [REDACTED]", sanitized)
    sanitized = _QUERY_SECRET_PATTERN.sub(r"\1[REDACTED]", sanitized)
    sanitized = _JWT_PATTERN.sub("[TOKEN_REDACTED]", sanitized)
    sanitized = _API_KEY_PATTERN.sub("[API_KEY_REDACTED]", sanitized)
    sanitized = _PASSPORT_LABEL_PATTERN.sub(r"\1 [REDACTED]", sanitized)
    sanitized = _redact_possible_ids(sanitized)
    sanitized = _CARD_PATTERN.sub("[PAYMENT_DATA_REDACTED]", sanitized)
    sanitized = _PHONE_PATTERN.sub("[PHONE_REDACTED]", sanitized)
    if redact_email:
        sanitized = _EMAIL_PATTERN.sub("[EMAIL_REDACTED]", sanitized)
    for index, application_uuid in enumerate(application_uuids):
        sanitized = sanitized.replace(f"[[APPLICATION_UUID_{index}]]", application_uuid)
    return sanitized


def _key_is_sensitive(key: str) -> bool:
    normalized = key.lower()
    return any(part in normalized for part in _SENSITIVE_KEY_PARTS)


def sanitize_payload(value: Any, *, redact_email: bool = True) -> Any:
    if isinstance(value, dict):
        return {
            str(key): REDACTED
            if _key_is_sensitive(str(key))
            else sanitize_payload(nested, redact_email=redact_email)
            for key, nested in value.items()
        }
    if isinstance(value, (list, tuple, set)):
        return [sanitize_payload(item, redact_email=redact_email) for item in value]
    if isinstance(value, SecretStr):
        return REDACTED
    if isinstance(value, str):
        return sanitize_text(value, redact_email=redact_email)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, (UUID, Decimal, Enum)):
        return str(value.value if isinstance(value, Enum) else value)
    if isinstance(value, bytes):
        return REDACTED
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return sanitize_text(str(value), redact_email=redact_email)


def sanitize_for_llm(value: Any) -> Any:
    return sanitize_payload(value, redact_email=True)


def safe_traveler_context(*, profile_id: UUID, label: str, is_default: bool) -> dict[str, Any]:
    return {
        "traveler_profile_id": str(profile_id),
        "label": label,
        "is_default": is_default,
    }


class SanitizingFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        rendered = record.getMessage()
        record.msg = sanitize_text(rendered, redact_email=True)
        record.args = ()
        if record.exc_info:
            record.exc_text = sanitize_text(
                "".join(traceback.format_exception(*record.exc_info)),
                redact_email=True,
            )
            record.exc_info = None
        elif record.exc_text:
            record.exc_text = sanitize_text(record.exc_text, redact_email=True)
        return True
