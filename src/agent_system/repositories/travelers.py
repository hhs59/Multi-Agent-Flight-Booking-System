from __future__ import annotations

from datetime import date
from uuid import UUID, uuid4

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from agent_system.auth.principal import AuthenticatedPrincipal
from agent_system.db.models import TravelerProfileRecord, UserRecord
from agent_system.domain.accounts import TravelerProfile, TravelerProfileInput
from agent_system.repositories.base import OwnedRepository
from agent_system.security.encryption import FieldEncryptor, profile_field_aad


class TravelerProfileRepository(OwnedRepository[TravelerProfileRecord]):
    model = TravelerProfileRecord

    def __init__(
        self,
        session: Session,
        principal: AuthenticatedPrincipal,
        encryptor: FieldEncryptor,
    ) -> None:
        super().__init__(session, principal)
        self.encryptor = encryptor

    def _encrypt_optional(
        self, profile_id: UUID, field_name: str, value: str | None
    ) -> bytes | None:
        if value is None:
            return None
        encrypted = self.encryptor.encrypt_text(
            value,
            associated_data=profile_field_aad(self.principal.user_id, profile_id, field_name),
        )
        return encrypted.ciphertext

    def _decrypt_optional(
        self,
        record: TravelerProfileRecord,
        field_name: str,
        value: bytes | None,
    ) -> str | None:
        if value is None:
            return None
        return self.encryptor.decrypt_text(
            value,
            key_version=record.encryption_key_version,
            associated_data=profile_field_aad(record.user_id, record.id, field_name),
        )

    def create(
        self, data: TravelerProfileInput, *, consent_version: str, consented_at
    ) -> TravelerProfile:
        profile_id = uuid4()
        if data.is_default:
            # lock
            self.session.execute(
                select(UserRecord.id)
                .where(UserRecord.id == self.principal.user_id)
                .with_for_update()
            )
            self.session.execute(
                update(TravelerProfileRecord)
                .where(
                    TravelerProfileRecord.user_id == self.principal.user_id,
                    TravelerProfileRecord.is_default.is_(True),
                )
                .values(is_default=False)
            )
        record = TravelerProfileRecord(
            id=profile_id,
            user_id=self.principal.user_id,
            label=data.label,
            is_default=data.is_default,
            legal_name=data.legal_name,
            title=data.title,
            given_name=data.given_name,
            family_name=data.family_name,
            email=data.email,
            phone=data.phone,
            gender_marker=data.gender_marker,
            birth_date_encrypted=self._encrypt_optional(
                profile_id, "birth_date", data.birth_date.isoformat()
            ),
            nationality_encrypted=self._encrypt_optional(
                profile_id, "nationality", data.nationality
            ),
            passport_number_encrypted=self._encrypt_optional(
                profile_id,
                "passport_number",
                data.passport_number.get_secret_value() if data.passport_number else None,
            ),
            passport_issuing_country_encrypted=self._encrypt_optional(
                profile_id, "passport_issuing_country", data.passport_issuing_country
            ),
            passport_expiry_date_encrypted=self._encrypt_optional(
                profile_id,
                "passport_expiry_date",
                data.passport_expiry_date.isoformat() if data.passport_expiry_date else None,
            ),
            encryption_key_version=self.encryptor.active_version,
            consent_version=consent_version,
            consented_at=consented_at,
        )
        self.add(record)
        return self.to_domain(record)

    def to_domain(self, record: TravelerProfileRecord) -> TravelerProfile:
        birth_date = self._decrypt_optional(record, "birth_date", record.birth_date_encrypted)
        nationality = self._decrypt_optional(record, "nationality", record.nationality_encrypted)
        passport_number = self._decrypt_optional(
            record, "passport_number", record.passport_number_encrypted
        )
        issuing_country = self._decrypt_optional(
            record,
            "passport_issuing_country",
            record.passport_issuing_country_encrypted,
        )
        passport_expiry = self._decrypt_optional(
            record,
            "passport_expiry_date",
            record.passport_expiry_date_encrypted,
        )
        return TravelerProfile(
            id=record.id,
            user_id=record.user_id,
            label=record.label,
            is_default=record.is_default,
            legal_name=record.legal_name,
            title=record.title,
            given_name=record.given_name,
            family_name=record.family_name,
            birth_date=date.fromisoformat(birth_date),
            gender_marker=record.gender_marker,
            email=record.email,
            phone=record.phone,
            nationality=nationality,
            passport_number=passport_number,
            passport_issuing_country=issuing_country,
            passport_expiry_date=date.fromisoformat(passport_expiry) if passport_expiry else None,
            consent_version=record.consent_version,
            consented_at=record.consented_at,
            created_at=record.created_at,
            updated_at=record.updated_at,
        )

    def rotate_encryption(self, profile_id: UUID) -> TravelerProfile:
        record = self.require(profile_id)
        if record.encryption_key_version == self.encryptor.active_version:
            return self.to_domain(record)
        decrypted = {
            field_name: self._decrypt_optional(record, field_name, getattr(record, column_name))
            for field_name, column_name in (
                ("birth_date", "birth_date_encrypted"),
                ("nationality", "nationality_encrypted"),
                ("passport_number", "passport_number_encrypted"),
                ("passport_issuing_country", "passport_issuing_country_encrypted"),
                ("passport_expiry_date", "passport_expiry_date_encrypted"),
            )
        }
        for field_name, column_name in (
            ("birth_date", "birth_date_encrypted"),
            ("nationality", "nationality_encrypted"),
            ("passport_number", "passport_number_encrypted"),
            ("passport_issuing_country", "passport_issuing_country_encrypted"),
            ("passport_expiry_date", "passport_expiry_date_encrypted"),
        ):
            setattr(
                record,
                column_name,
                self._encrypt_optional(profile_id, field_name, decrypted[field_name]),
            )
        record.encryption_key_version = self.encryptor.active_version
        record.version += 1
        self.session.flush()
        return self.to_domain(record)
