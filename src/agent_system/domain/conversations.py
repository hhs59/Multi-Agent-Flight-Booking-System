from __future__ import annotations

import json
from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import Field, JsonValue, SecretStr, model_validator

from agent_system.domain.accounts import ChatRole, CountryCode, EmailAddress, Locale
from agent_system.domain.travel_preferences import TravelPreferencesPlanningProjection
from agent_system.domain.trip_discovery import (
    DynamicDestinationChoices,
    DynamicOriginChoices,
    LocationKind,
    LocationReference,
    PendingDestinationConfirmation,
    TravelDateWindow,
)
from agent_system.domain.trip_inspiration import TripInspirationCheckpoint
from agent_system.domain.values import DomainModel, UTCInstant


class ProfileCompleteness(StrEnum):
    INCOMPLETE = "incomplete"
    READY_DOMESTIC = "ready_domestic"
    READY_INTERNATIONAL = "ready_international"


class ProfileSavePreference(StrEnum):
    ASK = "ask"
    ALLOW_CHAT_SAVE = "allow_chat_save"


class ThreadView(DomainModel):
    id: UUID
    user_id: UUID
    title: str | None = None
    locale: Locale
    archived: bool
    summary: str | None = None
    summary_version: int = Field(ge=0)
    summary_prompt_version: str | None = None
    summarized_through_sequence: int = Field(ge=0)
    created_at: UTCInstant
    updated_at: UTCInstant


class ThreadPage(DomainModel):
    items: tuple[ThreadView, ...]
    next_cursor: str | None = None


class MessageView(DomainModel):
    id: UUID
    user_id: UUID
    thread_id: UUID
    role: ChatRole
    content: str
    sequence: int = Field(ge=1)
    client_message_id: str | None = None
    result: dict[str, JsonValue] | None = None
    created_at: UTCInstant


class MessagePage(DomainModel):
    items: tuple[MessageView, ...]
    next_cursor: str | None = None


_FORBIDDEN_CHECKPOINT_KEYS = frozenset(
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
        "passport_number",
        "payment_token",
        "provider_offer_id",
        "provider_payload",
        "raw_payload",
        "refresh_token",
        "secret",
        "session_token",
        "user_id",
    }
)


def _find_forbidden_key(value: JsonValue, path: str = "state") -> str | None:
    if isinstance(value, dict):
        for key, nested in value.items():
            normalized = str(key).casefold().replace("-", "_")
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
                "payment_data",
                "paymentdata",
                "email",
                "legal_name",
                "passport",
                "phone",
            )
            if normalized in _FORBIDDEN_CHECKPOINT_KEYS or any(
                fragment in normalized or fragment in compact for fragment in forbidden_fragments
            ):
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


def _validate_presented_offers_projection(value: JsonValue) -> None:
    if not isinstance(value, dict):
        raise ValueError("presented_offers_v1 must be an object")
    if set(value) != {"search_id", "expires_at", "offers"}:
        raise ValueError("presented_offers_v1 contains unsupported fields")
    search_id = value.get("search_id")
    expires_at = value.get("expires_at")
    offers = value.get("offers")
    try:
        UUID(str(search_id))
        expiry = datetime.fromisoformat(str(expires_at))
    except (TypeError, ValueError) as exc:
        raise ValueError("presented_offers_v1 must contain a valid search and expiry") from exc
    if expiry.tzinfo is None or expiry.utcoffset() is None:
        raise ValueError("presented_offers_v1 expiry must be timezone-aware")
    if not isinstance(offers, list) or len(offers) > 20:
        raise ValueError("presented_offers_v1 supports at most 20 offers")
    seen_ranks: set[int] = set()
    seen_offer_ids: set[UUID] = set()
    for mapping in offers:
        if not isinstance(mapping, dict):
            raise ValueError("presented offer mappings must be objects")
        if set(mapping) != {"rank", "offer_id"}:
            raise ValueError("presented offer mappings contain unsupported fields")
        rank = mapping.get("rank")
        try:
            rank = int(rank)
            offer_id = UUID(str(mapping.get("offer_id")))
        except (TypeError, ValueError) as exc:
            raise ValueError("presented offer mappings must contain valid IDs") from exc
        if rank < 1 or rank > 20 or rank in seen_ranks:
            raise ValueError("presented offer ranks must be unique values from 1 through 20")
        if offer_id in seen_offer_ids:
            raise ValueError("presented offer IDs must be unique")
        seen_ranks.add(rank)
        seen_offer_ids.add(offer_id)


def _validate_trip_discovery_projection(value: JsonValue) -> None:
    if not isinstance(value, dict):
        raise ValueError("trip_discovery_v1 must be an object")
    allowed = {
        "origin",
        "destination",
        "date_window",
        "pending_destination_confirmation",
        "destination_resolution_source",
        "origin_resolution_source",
        "dynamic_destination_choices",
        "dynamic_origin_choices",
    }
    if not set(value).issubset(allowed):
        raise ValueError("trip_discovery_v1 contains unsupported fields")
    for key in ("origin", "destination"):
        raw_location = value.get(key)
        if raw_location is None:
            continue
        try:
            LocationReference.model_validate(raw_location)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} is not a safe location projection") from exc
    raw_date_window = value.get("date_window")
    if raw_date_window is not None:
        try:
            TravelDateWindow.model_validate(raw_date_window)
        except (TypeError, ValueError) as exc:
            raise ValueError("date_window is not a safe travel-date projection") from exc
    raw_pending = value.get("pending_destination_confirmation")
    if raw_pending is not None:
        try:
            pending = PendingDestinationConfirmation.model_validate(raw_pending)
        except (TypeError, ValueError) as exc:
            raise ValueError("pending destination confirmation is malformed") from exc
        if pending.reference.kind is LocationKind.UNKNOWN:
            raise ValueError("pending destination confirmation must resolve to a known location")
    source = value.get("destination_resolution_source")
    if source is not None and source not in {"catalog", "duffel", "fixture"}:
        raise ValueError("destination resolution source is not supported")
    origin_source = value.get("origin_resolution_source")
    if origin_source is not None and origin_source not in {"catalog", "duffel", "fixture"}:
        raise ValueError("origin resolution source is not supported")
    raw_choices = value.get("dynamic_destination_choices")
    if raw_choices is not None:
        try:
            choices = DynamicDestinationChoices.model_validate(raw_choices)
        except (TypeError, ValueError) as exc:
            raise ValueError("dynamic destination choices are malformed") from exc
        if source is not None and choices.source != source:
            raise ValueError("dynamic destination choice source does not match projection")
        for choice in choices.choices:
            if choice.reference.kind in {LocationKind.UNKNOWN, LocationKind.COUNTRY}:
                raise ValueError("dynamic destination choices must be city or airport references")
            if choice.reference.country_code is None or not choice.reference.airport_candidates:
                raise ValueError("dynamic destination choices require provider location data")
    raw_origin_choices = value.get("dynamic_origin_choices")
    if raw_origin_choices is not None:
        try:
            origin_choices = DynamicOriginChoices.model_validate(raw_origin_choices)
        except (TypeError, ValueError) as exc:
            raise ValueError("dynamic origin choices are malformed") from exc
        if origin_source is not None and origin_choices.source != origin_source:
            raise ValueError("dynamic origin choice source does not match projection")
        for choice in origin_choices.choices:
            if choice.reference.kind is not LocationKind.AIRPORT:
                raise ValueError("dynamic origin choices must resolve to airports")
            if len(choice.reference.airport_candidates) != 1:
                raise ValueError("dynamic origin choices require one airport")
            if choice.reference.country_code is None:
                raise ValueError("dynamic origin choices require provider location data")


def _validate_trip_inspiration_projection(value: JsonValue) -> None:
    try:
        checkpoint = TripInspirationCheckpoint.model_validate(value)
    except (TypeError, ValueError) as exc:
        raise ValueError("trip_inspiration_v1 is malformed") from exc
    option_ids = [str(option.application_offer_id) for option in checkpoint.options]
    if len(option_ids) != len(set(option_ids)):
        raise ValueError("trip inspiration options must be unique")
    if checkpoint.options and checkpoint.expires_at is None:
        raise ValueError("trip inspiration options require an expiry")


def _validate_last_action_projection(value: JsonValue) -> None:
    if not isinstance(value, dict):
        raise ValueError("last_action_v1 must be an object")
    allowed = {
        "action",
        "status",
        "search_id",
        "offer_id",
        "selected_offer_id",
        "booking_intent_id",
        "watch_draft_id",
    }
    if not set(value).issubset(allowed):
        raise ValueError("last_action_v1 contains unsupported fields")
    for key in (
        "search_id",
        "offer_id",
        "selected_offer_id",
        "booking_intent_id",
        "watch_draft_id",
    ):
        if key in value and value[key] is not None:
            try:
                UUID(str(value[key]))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{key} must be an application UUID") from exc


class CheckpointState(DomainModel):
    current_intent: str | None = Field(default=None, max_length=80)
    plan: tuple[str, ...] = Field(default_factory=tuple, max_length=30)
    selected_offer_id: UUID | None = None
    booking_intent_id: UUID | None = None
    watch_draft_id: UUID | None = None
    safe_context: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_safe_projection(self) -> Self:
        dumped = self.model_dump(mode="json")
        forbidden_path = _find_forbidden_key(dumped)
        if forbidden_path:
            raise ValueError(f"sensitive field is not allowed in checkpoint: {forbidden_path}")
        if "last_result" in self.safe_context:
            raise ValueError("complete safe results are not allowed in checkpoint state")
        if "trip_discovery_v1" in self.safe_context:
            _validate_trip_discovery_projection(self.safe_context["trip_discovery_v1"])
        if "presented_offers_v1" in self.safe_context:
            _validate_presented_offers_projection(self.safe_context["presented_offers_v1"])
        if "trip_inspiration_v1" in self.safe_context:
            _validate_trip_inspiration_projection(self.safe_context["trip_inspiration_v1"])
        if "last_action_v1" in self.safe_context:
            _validate_last_action_projection(self.safe_context["last_action_v1"])
        if "travel_preferences_v1" in self.safe_context:
            try:
                TravelPreferencesPlanningProjection.model_validate(
                    self.safe_context["travel_preferences_v1"]
                )
            except (TypeError, ValueError) as exc:
                raise ValueError(
                    "travel_preferences_v1 is not a safe preference projection"
                ) from exc
        try:
            serialized = json.dumps(
                dumped,
                ensure_ascii=False,
                separators=(",", ":"),
                allow_nan=False,
            ).encode("utf-8")
        except (TypeError, ValueError) as exc:
            raise ValueError("checkpoint state must contain JSON values") from exc
        if len(serialized) > 32 * 1024:
            raise ValueError("checkpoint state exceeds 32 KiB")
        return self


class CheckpointView(DomainModel):
    id: UUID
    user_id: UUID
    thread_id: UUID
    version: int = Field(ge=1)
    state_schema_version: int = Field(ge=1)
    state: CheckpointState
    last_message_id: UUID | None = None
    created_at: UTCInstant


class ConversationContext(DomainModel):
    thread: ThreadView
    summary: str | None
    messages: tuple[MessageView, ...]
    checkpoint: CheckpointView | None


class TravelerProfileData(DomainModel):
    label: str = Field(min_length=1, max_length=80)
    is_default: bool = False
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=20)
    given_name: str | None = Field(default=None, max_length=120)
    family_name: str | None = Field(default=None, max_length=120)
    birth_date: date | None = None
    gender_marker: str | None = Field(default=None, max_length=30)
    email: EmailAddress | None = None
    phone: str | None = Field(default=None, max_length=40)
    nationality: CountryCode | None = None
    passport_number: SecretStr | None = None
    passport_issuing_country: CountryCode | None = None
    passport_expiry_date: date | None = None
    save_preference: ProfileSavePreference = ProfileSavePreference.ASK


class TravelerProfilePatch(DomainModel):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=20)
    given_name: str | None = Field(default=None, max_length=120)
    family_name: str | None = Field(default=None, max_length=120)
    birth_date: date | None = None
    gender_marker: str | None = Field(default=None, max_length=30)
    email: EmailAddress | None = None
    phone: str | None = Field(default=None, max_length=40)
    nationality: CountryCode | None = None
    passport_number: SecretStr | None = None
    passport_issuing_country: CountryCode | None = None
    passport_expiry_date: date | None = None
    save_preference: ProfileSavePreference | None = None


class TravelerDraft(DomainModel):
    legal_name: str | None = Field(default=None, min_length=1, max_length=200)
    title: str | None = Field(default=None, max_length=20)
    given_name: str | None = Field(default=None, max_length=120)
    family_name: str | None = Field(default=None, max_length=120)
    birth_date: date | None = None
    gender_marker: str | None = Field(default=None, max_length=30)
    email: EmailAddress | None = None
    phone: str | None = Field(default=None, max_length=40)
    nationality: CountryCode | None = None
    passport_number: SecretStr | None = None
    passport_issuing_country: CountryCode | None = None
    passport_expiry_date: date | None = None


class TravelerProfileView(TravelerProfileData):
    id: UUID
    user_id: UUID
    consent_version: str
    consented_at: UTCInstant
    completeness: ProfileCompleteness
    version: int = Field(ge=1)
    created_at: UTCInstant
    updated_at: UTCInstant


class TravelerValidation(DomainModel):
    complete: bool
    completeness: ProfileCompleteness
    missing_fields: tuple[str, ...]
    errors: tuple[str, ...]


class TravelerSnapshotData(DomainModel):
    traveler_profile_id: UUID
    legal_name: str
    title: str | None = None
    given_name: str | None = None
    family_name: str | None = None
    birth_date: date
    gender_marker: str | None = None
    email: EmailAddress
    phone: str | None = None
    nationality: CountryCode | None = None
    passport_number: SecretStr | None = None
    passport_issuing_country: CountryCode | None = None
    passport_expiry_date: date | None = None


class ChatTravelerExtraction(DomainModel):
    sanitized_text: str
    draft: TravelerDraft
    can_persist: bool
    requires_explicit_consent: bool
    uncertain_sensitive_token: bool


class AppendResult(DomainModel):
    created: bool
    message: MessageView
    checkpoint: CheckpointView | None = None
