from __future__ import annotations

import json
import re
from datetime import UTC, date, datetime
from uuid import UUID, uuid4

from pydantic import SecretStr
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import BookingIntentRecord, TravelerProfileRecord, UserRecord
from agent_system.domain.conversations import (
    ChatTravelerExtraction,
    ProfileCompleteness,
    ProfileSavePreference,
    TravelerDraft,
    TravelerProfileData,
    TravelerProfilePatch,
    TravelerProfileView,
    TravelerSnapshotData,
    TravelerValidation,
)
from agent_system.repositories.base import (
    ConcurrencyConflictError,
    ResourceNotFoundError,
)
from agent_system.repositories.travelers import TravelerProfileRepository
from agent_system.security.encryption import FieldEncryptor
from agent_system.security.messages import sanitize_message_text

_EMAIL = re.compile(r"(?<![\w.+-])[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}(?![\w.-])")
_PASSPORT = re.compile(
    r"(?i)\b(?:passport(?:\s+number)?|h[oộ]\s*chi[eế]u)\s*(?:(?:is|l[aà])\s+|[:#-]\s*)?((?=[A-Z0-9]*\d)[A-Z0-9]{5,20})\b"
)

_PASSPORT_VALUE = re.compile(r"(?i)^(?=[A-Z0-9]*\d)[A-Z0-9]{5,20}$")
_NAME = re.compile(r"(?i)\b(?:name|full name|h[oọ]\s*t[eê]n)\s*[:#-]\s*([^,;\n]{2,200})")
_BIRTH_DATE = re.compile(
    r"(?i)\b(?:birth date|date of birth|dob|ng[aà]y sinh)\s*[:#-]\s*(\d{4}-\d{2}-\d{2})"
)
_PASSPORT_EXPIRY = re.compile(
    r"(?i)\b(?:passport expiry|expiry|h[aạ]n h[oộ] chi[eế]u)\s*[:#-]\s*(\d{4}-\d{2}-\d{2})"
)


def _db_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


_ISSUING_COUNTRY = re.compile(
    r"(?i)\b(?:issuing country|passport country|n[uư][oớ]c c[aấ]p)\s*[:#-]\s*([A-Z]{2})\b"
)


def _passport_plain(value: SecretStr | None) -> str | None:
    if value is None:
        return None
    plaintext = value.get_secret_value()
    if not _PASSPORT_VALUE.fullmatch(plaintext):
        raise ValueError("passport number must be an unmasked alphanumeric identifier")
    return plaintext


def _normalize_name_part(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = " ".join(value.split())
    return normalized or None


def derive_display_legal_name(given_name: str | None, family_name: str | None) -> str | None:
    """Derive a compatibility display name without splitting legacy names."""

    normalized_given = _normalize_name_part(given_name)
    normalized_family = _normalize_name_part(family_name)
    if not normalized_given or not normalized_family:
        return None
    return f"{normalized_given} {normalized_family}"


def _normalize_gender_marker(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    mapped = {"m": "m", "male": "m", "f": "f", "female": "f"}.get(normalized)
    if mapped is None:
        raise ValueError("gender marker must be one of m, f, male, or female")
    return mapped


class TravelerValidationError(ValueError):
    def __init__(self, validation: TravelerValidation) -> None:
        super().__init__("traveler profile is incomplete or invalid")
        self.validation = validation


def _completeness(
    record: TravelerProfileRecord, repository: TravelerProfileRepository
) -> ProfileCompleteness:
    domestic_ready = all(
        (
            record.given_name,
            record.family_name,
            record.birth_date_encrypted,
            record.email,
        )
    )
    if not domestic_ready:
        return ProfileCompleteness.INCOMPLETE
    international_ready = all(
        (
            record.nationality_encrypted,
            record.passport_number_encrypted,
            record.passport_issuing_country_encrypted,
            record.passport_expiry_date_encrypted,
        )
    )
    if international_ready:
        expiry = repository._decrypt_optional(
            record, "passport_expiry_date", record.passport_expiry_date_encrypted
        )
        if expiry and date.fromisoformat(expiry) > date.today():
            return ProfileCompleteness.READY_INTERNATIONAL
    return ProfileCompleteness.READY_DOMESTIC


def _to_view(
    record: TravelerProfileRecord, repository: TravelerProfileRepository
) -> TravelerProfileView:
    birth = repository._decrypt_optional(record, "birth_date", record.birth_date_encrypted)
    expiry = repository._decrypt_optional(
        record, "passport_expiry_date", record.passport_expiry_date_encrypted
    )
    passport = repository._decrypt_optional(
        record, "passport_number", record.passport_number_encrypted
    )
    return TravelerProfileView(
        id=record.id,
        user_id=record.user_id,
        label=record.label,
        is_default=record.is_default,
        legal_name=record.legal_name,
        title=record.title,
        given_name=record.given_name,
        family_name=record.family_name,
        birth_date=date.fromisoformat(birth) if birth else None,
        gender_marker=record.gender_marker,
        email=record.email,
        phone=record.phone,
        nationality=repository._decrypt_optional(
            record, "nationality", record.nationality_encrypted
        ),
        passport_number=SecretStr(passport) if passport else None,
        passport_issuing_country=repository._decrypt_optional(
            record,
            "passport_issuing_country",
            record.passport_issuing_country_encrypted,
        ),
        passport_expiry_date=date.fromisoformat(expiry) if expiry else None,
        save_preference=record.save_preference,
        consent_version=record.consent_version,
        consented_at=_db_utc(record.consented_at),
        completeness=record.completeness,
        version=record.version,
        created_at=_db_utc(record.created_at),
        updated_at=_db_utc(record.updated_at),
    )


class TravelerProfileService:
    def __init__(self, session: Session, encryptor: FieldEncryptor) -> None:
        self.session = session
        self.encryptor = encryptor

    def _repository(self, principal: AuthenticatedPrincipal) -> TravelerProfileRepository:
        return TravelerProfileRepository(self.session, principal, self.encryptor)

    def _lock_user(self, principal: AuthenticatedPrincipal) -> None:
        found = self.session.scalar(
            select(UserRecord.id).where(UserRecord.id == principal.user_id).with_for_update()
        )
        if found is None:
            raise ResourceNotFoundError("resource was not found")

    def create(
        self,
        principal: AuthenticatedPrincipal,
        data: TravelerProfileData,
        *,
        consent_version: str,
        consented_at: datetime | None = None,
    ) -> TravelerProfileView:
        consented_at = consented_at or datetime.now(UTC)
        repository = self._repository(principal)
        profile_id = uuid4()
        if data.is_default:
            self._lock_user(principal)
            self.session.execute(
                update(TravelerProfileRecord)
                .where(
                    TravelerProfileRecord.user_id == principal.user_id,
                    TravelerProfileRecord.is_default.is_(True),
                )
                .values(is_default=False)
            )
        given_name = _normalize_name_part(data.given_name)
        family_name = _normalize_name_part(data.family_name)
        record = TravelerProfileRecord(
            id=profile_id,
            user_id=principal.user_id,
            label=data.label,
            is_default=data.is_default,
            legal_name=derive_display_legal_name(given_name, family_name) or data.legal_name,
            title=data.title,
            given_name=given_name,
            family_name=family_name,
            email=data.email,
            phone=data.phone,
            gender_marker=_normalize_gender_marker(data.gender_marker),
            birth_date_encrypted=repository._encrypt_optional(
                profile_id,
                "birth_date",
                data.birth_date.isoformat() if data.birth_date else None,
            ),
            nationality_encrypted=repository._encrypt_optional(
                profile_id, "nationality", data.nationality
            ),
            passport_number_encrypted=repository._encrypt_optional(
                profile_id,
                "passport_number",
                _passport_plain(data.passport_number),
            ),
            passport_issuing_country_encrypted=repository._encrypt_optional(
                profile_id,
                "passport_issuing_country",
                data.passport_issuing_country,
            ),
            passport_expiry_date_encrypted=repository._encrypt_optional(
                profile_id,
                "passport_expiry_date",
                data.passport_expiry_date.isoformat() if data.passport_expiry_date else None,
            ),
            encryption_key_version=self.encryptor.active_version,
            consent_version=consent_version,
            consented_at=consented_at,
            completeness=ProfileCompleteness.INCOMPLETE.value,
            save_preference=data.save_preference.value,
        )
        repository.add(record)
        record.completeness = _completeness(record, repository).value
        self.session.flush()
        return _to_view(record, repository)

    def get(self, principal: AuthenticatedPrincipal, profile_id: UUID) -> TravelerProfileView:
        repository = self._repository(principal)
        return _to_view(repository.require(profile_id), repository)

    def list(
        self, principal: AuthenticatedPrincipal, *, limit: int = 100
    ) -> tuple[TravelerProfileView, ...]:
        repository = self._repository(principal)
        return tuple(_to_view(row, repository) for row in repository.list(limit=limit))

    def update(
        self,
        principal: AuthenticatedPrincipal,
        profile_id: UUID,
        patch: TravelerProfilePatch,
        *,
        expected_version: int,
    ) -> TravelerProfileView:
        repository = self._repository(principal)
        record = repository.require(profile_id)
        if record.version != expected_version:
            raise ConcurrencyConflictError("resource version changed")
        fields = patch.model_fields_set
        if not fields:
            return _to_view(record, repository)
        for name in (
            "label",
            "legal_name",
            "title",
            "given_name",
            "family_name",
            "gender_marker",
            "email",
            "phone",
        ):
            if name in fields:
                value = getattr(patch, name)
                if name in {"given_name", "family_name"}:
                    value = _normalize_name_part(value)
                elif name == "gender_marker":
                    value = _normalize_gender_marker(value)
                setattr(record, name, value)
        if "legal_name" not in fields and {"given_name", "family_name"}.issubset(fields):
            derived_legal_name = derive_display_legal_name(
                record.given_name,
                record.family_name,
            )
            if derived_legal_name is not None:
                record.legal_name = derived_legal_name
        if "save_preference" in fields:
            if patch.save_preference is None:
                raise ValueError("save preference cannot be null")
            record.save_preference = patch.save_preference.value
        encrypted_fields = (
            ("birth_date", "birth_date_encrypted"),
            ("nationality", "nationality_encrypted"),
            ("passport_number", "passport_number_encrypted"),
            ("passport_issuing_country", "passport_issuing_country_encrypted"),
            ("passport_expiry_date", "passport_expiry_date_encrypted"),
        )
        sensitive_values = {
            field_name: repository._decrypt_optional(
                record, field_name, getattr(record, column_name)
            )
            for field_name, column_name in encrypted_fields
        }
        for field_name, _ in encrypted_fields:
            if field_name not in fields:
                continue
            value = getattr(patch, field_name)
            if isinstance(value, SecretStr):
                serialized = _passport_plain(value)
            elif isinstance(value, date):
                serialized = value.isoformat()
            else:
                serialized = value
            sensitive_values[field_name] = serialized
        passport_values = tuple(
            sensitive_values[name]
            for name in (
                "passport_number",
                "passport_issuing_country",
                "passport_expiry_date",
            )
        )
        if any(value is not None for value in passport_values) and not all(
            value is not None for value in passport_values
        ):
            raise ValueError(
                "passport number, issuing country, and expiry date must be provided together"
            )
        for field_name, column_name in encrypted_fields:
            setattr(
                record,
                column_name,
                repository._encrypt_optional(profile_id, field_name, sensitive_values[field_name]),
            )
        record.encryption_key_version = self.encryptor.active_version
        record.completeness = _completeness(record, repository).value
        record.version += 1
        self.session.flush()
        return _to_view(record, repository)

    def set_default(
        self,
        principal: AuthenticatedPrincipal,
        profile_id: UUID,
        *,
        is_default: bool = True,
    ) -> TravelerProfileView:
        repository = self._repository(principal)
        self._lock_user(principal)
        record = repository.require(profile_id)
        if is_default:
            self.session.execute(
                update(TravelerProfileRecord)
                .where(
                    TravelerProfileRecord.user_id == principal.user_id,
                    TravelerProfileRecord.is_default.is_(True),
                )
                .values(is_default=False)
            )
        record.is_default = is_default
        record.version += 1
        self.session.flush()
        return _to_view(record, repository)

    def delete(self, principal: AuthenticatedPrincipal, profile_id: UUID) -> None:
        repository = self._repository(principal)
        repository.require(profile_id)
        if not repository.delete(profile_id):
            raise ResourceNotFoundError("resource was not found")

    def validate(
        self,
        principal: AuthenticatedPrincipal,
        profile_id: UUID,
        *,
        international: bool,
        provider_required_fields: tuple[str, ...] = (),
        today: date | None = None,
    ) -> TravelerValidation:
        profile = self.get(principal, profile_id)
        today = today or date.today()
        required = {"legal_name", "birth_date", "email"}
        if international:
            required.update(
                {
                    "nationality",
                    "passport_number",
                    "passport_issuing_country",
                    "passport_expiry_date",
                }
            )
        required.update(provider_required_fields)
        missing = tuple(sorted(name for name in required if getattr(profile, name, None) is None))
        errors: list[str] = []
        if profile.birth_date and profile.birth_date >= today:
            errors.append("birth_date_must_be_in_the_past")
        if international and profile.passport_expiry_date and profile.passport_expiry_date <= today:
            errors.append("passport_expired")
        completeness = (
            ProfileCompleteness.READY_INTERNATIONAL
            if international and not missing and not errors
            else (
                ProfileCompleteness.READY_DOMESTIC
                if not international and not missing and not errors
                else ProfileCompleteness.INCOMPLETE
            )
        )
        return TravelerValidation(
            complete=not missing and not errors,
            completeness=completeness,
            missing_fields=missing,
            errors=tuple(errors),
        )

    def select_for_booking(
        self,
        principal: AuthenticatedPrincipal,
        profile_ids: tuple[UUID, ...],
        *,
        international: bool,
        provider_required_fields: tuple[str, ...] = (),
    ) -> tuple[TravelerSnapshotData, ...]:
        if not profile_ids or len(profile_ids) > 9 or len(set(profile_ids)) != len(profile_ids):
            raise ValueError("select between one and nine distinct traveler profiles")
        snapshots: list[TravelerSnapshotData] = []
        for profile_id in profile_ids:
            validation = self.validate(
                principal,
                profile_id,
                international=international,
                provider_required_fields=provider_required_fields,
            )
            if not validation.complete:
                raise TravelerValidationError(validation)
            profile = self.get(principal, profile_id)
            snapshots.append(
                TravelerSnapshotData(
                    traveler_profile_id=profile.id,
                    legal_name=profile.legal_name,
                    title=profile.title,
                    given_name=profile.given_name,
                    family_name=profile.family_name,
                    birth_date=profile.birth_date,
                    gender_marker=profile.gender_marker,
                    email=profile.email,
                    phone=profile.phone,
                    nationality=profile.nationality,
                    passport_number=profile.passport_number,
                    passport_issuing_country=profile.passport_issuing_country,
                    passport_expiry_date=profile.passport_expiry_date,
                )
            )
        return tuple(snapshots)

    def snapshot_booking_intent(
        self,
        principal: AuthenticatedPrincipal,
        booking_intent_id: UUID,
        profile_ids: tuple[UUID, ...],
        *,
        international: bool,
        provider_required_fields: tuple[str, ...] = (),
        expected_version: int,
    ) -> tuple[TravelerSnapshotData, ...]:
        intent = self.session.scalar(
            select(BookingIntentRecord)
            .where(
                BookingIntentRecord.id == booking_intent_id,
                BookingIntentRecord.user_id == principal.user_id,
            )
            .with_for_update()
        )
        if intent is None:
            raise ResourceNotFoundError("resource was not found")
        if intent.version != expected_version:
            raise ConcurrencyConflictError("resource version changed")
        snapshots = self.select_for_booking(
            principal,
            profile_ids,
            international=international,
            provider_required_fields=provider_required_fields,
        )
        payload = [
            snapshot.model_dump(mode="json", context={"include_secrets": True})
            for snapshot in snapshots
        ]
        for item, snapshot in zip(payload, snapshots, strict=True):
            item["passport_number"] = (
                snapshot.passport_number.get_secret_value() if snapshot.passport_number else None
            )
        encrypted = self.encryptor.encrypt_text(
            json.dumps(payload, separators=(",", ":")),
            associated_data=self._snapshot_aad(principal.user_id, booking_intent_id),
        )
        intent.traveler_profile_ids = [str(profile_id) for profile_id in profile_ids]
        intent.traveler_snapshots_encrypted = encrypted.ciphertext
        intent.snapshot_encryption_key_version = encrypted.key_version
        intent.version += 1
        self.session.flush()
        return snapshots

    def load_booking_snapshots(
        self,
        principal: AuthenticatedPrincipal,
        booking_intent_id: UUID,
    ) -> tuple[TravelerSnapshotData, ...]:
        intent = self.session.scalar(
            select(BookingIntentRecord).where(
                BookingIntentRecord.id == booking_intent_id,
                BookingIntentRecord.user_id == principal.user_id,
            )
        )
        if (
            intent is None
            or intent.traveler_snapshots_encrypted is None
            or intent.snapshot_encryption_key_version is None
        ):
            raise ResourceNotFoundError("resource was not found")
        plaintext = self.encryptor.decrypt_text(
            intent.traveler_snapshots_encrypted,
            key_version=intent.snapshot_encryption_key_version,
            associated_data=self._snapshot_aad(principal.user_id, booking_intent_id),
        )
        return tuple(TravelerSnapshotData.model_validate(item) for item in json.loads(plaintext))

    @staticmethod
    def _snapshot_aad(user_id: UUID, booking_intent_id: UUID) -> bytes:
        return f"booking-intent:{user_id}:{booking_intent_id}:traveler-snapshots:v1".encode()

    def extract_chat_draft(
        self,
        principal: AuthenticatedPrincipal,
        text: str,
        *,
        target_profile_id: UUID | None = None,
        explicit_save_consent: bool = False,
    ) -> ChatTravelerExtraction:
        prior_permission = (
            target_profile_id is not None
            and self.get(principal, target_profile_id).save_preference
            is ProfileSavePreference.ALLOW_CHAT_SAVE
        )
        can_persist = explicit_save_consent or prior_permission
        sanitized = sanitize_message_text(text)
        values: dict = {}
        if match := _EMAIL.search(text):
            values["email"] = match.group(0)
        if match := _PASSPORT.search(text):
            values["passport_number"] = SecretStr(match.group(1))
        if match := _NAME.search(text):
            values["legal_name"] = match.group(1).strip()
        if match := _BIRTH_DATE.search(text):
            values["birth_date"] = date.fromisoformat(match.group(1))
        if match := _PASSPORT_EXPIRY.search(text):
            values["passport_expiry_date"] = date.fromisoformat(match.group(1))
        if match := _ISSUING_COUNTRY.search(text):
            values["passport_issuing_country"] = match.group(1).upper()
        draft = TravelerDraft.model_validate(values)
        return ChatTravelerExtraction(
            sanitized_text=sanitized.text,
            draft=draft,
            can_persist=can_persist,
            requires_explicit_consent=not can_persist,
            uncertain_sensitive_token=sanitized.uncertain_sensitive_token,
        )

    def save_chat_draft(
        self,
        principal: AuthenticatedPrincipal,
        extraction: ChatTravelerExtraction,
        *,
        label: str,
        explicit_save_consent: bool,
        consent_version: str,
        target_profile_id: UUID | None = None,
        confirm_overwrite: bool = False,
    ) -> TravelerProfileView:
        if not (explicit_save_consent or extraction.can_persist):
            raise ValueError("explicit consent is required to save chat traveler data")
        draft = extraction.draft
        if target_profile_id is not None:
            if not confirm_overwrite:
                raise ValueError("explicit overwrite confirmation is required")
            patch = TravelerProfilePatch.model_validate(draft.model_dump())
            current = self.get(principal, target_profile_id)
            return self.update(
                principal,
                target_profile_id,
                patch,
                expected_version=current.version,
            )
        return self.create(
            principal,
            TravelerProfileData(
                label=label,
                **draft.model_dump(),
            ),
            consent_version=consent_version,
        )
