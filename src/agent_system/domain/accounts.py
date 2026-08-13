from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Any

from pydantic import (
    BeforeValidator,
    Field,
    JsonValue,
    SecretStr,
    StringConstraints,
    field_validator,
)

from agent_system.domain.values import (
    BookingIntentId,
    CheckpointId,
    DomainModel,
    MessageId,
    OfferId,
    ThreadId,
    TravelerProfileId,
    UserId,
    UTCInstant,
    new_id,
    utc_now,
)


def _normalize_email(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("email address must be a string")
    normalized = value.strip().lower()
    local, separator, domain = normalized.rpartition("@")
    if not separator or not local or "." not in domain or domain.startswith("."):
        raise ValueError("invalid email address")
    return normalized


def _normalize_country_code(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("country code must be a string")
    normalized = value.strip().upper()
    if len(normalized) != 2 or not normalized.isalpha():
        raise ValueError("country code must be a two-letter code")
    return normalized


EmailAddress = Annotated[
    str,
    BeforeValidator(_normalize_email),
    StringConstraints(max_length=320),
]
CountryCode = Annotated[
    str,
    BeforeValidator(_normalize_country_code),
    StringConstraints(min_length=2, max_length=2, pattern=r"^[A-Z]{2}$"),
]


class Locale(StrEnum):
    VI = "vi"
    EN = "en"


class AccountStatus(StrEnum):
    ACTIVE = "active"
    SUSPENDED = "suspended"
    DELETED = "deleted"


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"
    SYSTEM = "system"
    TOOL = "tool"


class UserAccount(DomainModel):
    id: UserId = Field(default_factory=new_id)
    oidc_issuer: str = Field(min_length=1, max_length=2048)
    oidc_subject: str = Field(min_length=1, max_length=255)
    email: EmailAddress
    display_name: str = Field(min_length=1, max_length=200)
    locale: Locale = Locale.VI
    timezone: str = "Asia/Ho_Chi_Minh"
    status: AccountStatus = AccountStatus.ACTIVE
    created_at: UTCInstant = Field(default_factory=utc_now)
    updated_at: UTCInstant = Field(default_factory=utc_now)

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, value: str) -> str:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

        try:
            ZoneInfo(value)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(f"unknown IANA timezone: {value}") from exc
        return value


class ChatThreadCreate(DomainModel):
    title: str | None = Field(default=None, max_length=200)
    locale: Locale = Locale.VI


class ChatThread(DomainModel):
    id: ThreadId = Field(default_factory=new_id)
    user_id: UserId
    title: str | None = Field(default=None, max_length=200)
    locale: Locale = Locale.VI
    archived: bool = False
    created_at: UTCInstant = Field(default_factory=utc_now)
    updated_at: UTCInstant = Field(default_factory=utc_now)


class ChatMessageCreate(DomainModel):
    thread_id: ThreadId
    content: str = Field(min_length=1, max_length=50_000)


class ChatMessage(DomainModel):
    id: MessageId = Field(default_factory=new_id)
    user_id: UserId
    thread_id: ThreadId
    role: ChatRole
    content: str = Field(min_length=1, max_length=50_000)
    sequence: int = Field(ge=0)
    created_at: UTCInstant = Field(default_factory=utc_now)


_CHECKPOINT_FORBIDDEN_KEYS = frozenset(
    {
        "access_token",
        "api_key",
        "card_number",
        "cvv",
        "passport_number",
        "refresh_token",
    }
)


def _find_forbidden_key(value: JsonValue, path: str = "state") -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.lower() in _CHECKPOINT_FORBIDDEN_KEYS:
                return f"{path}.{key}"
            found = _find_forbidden_key(nested, f"{path}.{key}")
            if found:
                return found
    elif isinstance(value, list):
        for index, nested in enumerate(value):
            found = _find_forbidden_key(nested, f"{path}[{index}]")
            if found:
                return found
    return None


class AgentCheckpoint(DomainModel):
    id: CheckpointId = Field(default_factory=new_id)
    user_id: UserId
    thread_id: ThreadId
    state_version: int = Field(default=1, ge=1)
    last_message_id: MessageId | None = None
    selected_offer_id: OfferId | None = None
    booking_intent_id: BookingIntentId | None = None
    safe_state: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: UTCInstant = Field(default_factory=utc_now)

    @field_validator("safe_state")
    @classmethod
    def reject_sensitive_state(cls, value: dict[str, JsonValue]) -> dict[str, JsonValue]:
        forbidden_path = _find_forbidden_key(value)
        if forbidden_path:
            raise ValueError(f"sensitive field is not allowed in a checkpoint: {forbidden_path}")
        return value


class TravelerProfileInput(DomainModel):
    label: str = Field(min_length=1, max_length=80)
    is_default: bool = False
    legal_name: str = Field(min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=20)
    given_name: str | None = Field(default=None, max_length=120)
    family_name: str | None = Field(default=None, max_length=120)
    birth_date: date
    gender_marker: str | None = Field(default=None, max_length=30)
    email: EmailAddress
    phone: str | None = Field(default=None, max_length=40)
    nationality: CountryCode | None = None
    passport_number: SecretStr | None = None
    passport_issuing_country: CountryCode | None = None
    passport_expiry_date: date | None = None


class TravelerProfile(TravelerProfileInput):
    id: TravelerProfileId = Field(default_factory=new_id)
    user_id: UserId
    consent_version: str = Field(min_length=1, max_length=40)
    consented_at: UTCInstant
    created_at: UTCInstant = Field(default_factory=utc_now)
    updated_at: UTCInstant = Field(default_factory=utc_now)
