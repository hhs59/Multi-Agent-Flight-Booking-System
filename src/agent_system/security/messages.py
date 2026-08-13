from __future__ import annotations

import re
from dataclasses import dataclass

from agent_system.security.sanitization import sanitize_text

_PASSPORT_CAPTURE = re.compile(
    r"(?i)\b(passport(?:\s+number)?|h[oộ]\s*chi[eế]u)\s*(?:(?:is|l[aà])\s+|[:#-]\s*)?((?=[A-Z0-9]*\d)[A-Z0-9]{5,20})\b"
)
_UNCERTAIN_TOKEN = re.compile(r"(?<![A-Z0-9])[A-Z]{1,3}\d{6,12}(?![A-Z0-9])", re.IGNORECASE)
_APPLICATION_UUID = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)
_PLACEHOLDER = "[[SAFE_PASSPORT_LAST4_{index}]]"


@dataclass(frozen=True)
class MessageSanitizationResult:
    text: str
    uncertain_sensitive_token: bool = False


def _redact_uncertain_tokens(value: str) -> tuple[str, bool]:
    uuid_spans = [match.span() for match in _APPLICATION_UUID.finditer(value)]
    matches = [
        match
        for match in _UNCERTAIN_TOKEN.finditer(value)
        if not any(start <= match.start() < end for start, end in uuid_spans)
    ]
    if not matches:
        return value, False
    rendered: list[str] = []
    cursor = 0
    for match in matches:
        rendered.append(value[cursor : match.start()])
        rendered.append("[POSSIBLE_ID_REDACTED]")
        cursor = match.end()
    rendered.append(value[cursor:])
    return "".join(rendered), True


def sanitize_message_text(value: str) -> MessageSanitizationResult:
    masked: list[str] = []

    def replace_passport(match: re.Match[str]) -> str:
        token = match.group(2)
        masked.append(f"passport ending {token[-4:]}")
        return _PLACEHOLDER.format(index=len(masked) - 1)

    without_passports = _PASSPORT_CAPTURE.sub(replace_passport, value)
    without_uncertain_tokens, uncertain_token = _redact_uncertain_tokens(without_passports)
    sanitized = sanitize_text(without_uncertain_tokens, redact_email=True)
    for index, replacement in enumerate(masked):
        sanitized = sanitized.replace(_PLACEHOLDER.format(index=index), replacement)
    sanitized, post_sanitization_uncertain = _redact_uncertain_tokens(sanitized)
    return MessageSanitizationResult(
        sanitized,
        uncertain_token or post_sanitization_uncertain,
    )
