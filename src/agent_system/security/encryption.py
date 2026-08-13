from __future__ import annotations

import base64
import binascii
import json
import os
from dataclasses import dataclass
from typing import Self
from uuid import UUID

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

NONCE_SIZE = 12
KEY_SIZE = 32


class EncryptionConfigurationError(ValueError):
    pass


class DecryptionError(ValueError):
    pass


@dataclass(frozen=True)
class EncryptedField:
    ciphertext: bytes
    key_version: int

    def __repr__(self) -> str:
        return f"EncryptedField(ciphertext=<redacted>, key_version={self.key_version})"


class FieldEncryptor:
    def __init__(self, keys: dict[int, bytes], active_version: int) -> None:
        if not keys:
            raise EncryptionConfigurationError("at least one PII encryption key is required")
        if active_version not in keys:
            raise EncryptionConfigurationError("active PII key version is not present")
        invalid_versions = [version for version, key in keys.items() if len(key) != KEY_SIZE]
        if invalid_versions:
            raise EncryptionConfigurationError(
                f"PII keys must be {KEY_SIZE} bytes: versions {invalid_versions}"
            )
        self._keys = dict(keys)
        self.active_version = active_version

    @classmethod
    def from_environment(cls) -> Self:
        raw_keys = os.environ.get("PII_ENCRYPTION_KEYS")
        raw_active = os.environ.get("PII_ACTIVE_KEY_VERSION")
        if not raw_keys or not raw_active:
            raise EncryptionConfigurationError(
                "PII_ENCRYPTION_KEYS and PII_ACTIVE_KEY_VERSION are required"
            )
        try:
            parsed = json.loads(raw_keys)
            keys = {
                int(version): base64.urlsafe_b64decode(encoded)
                for version, encoded in parsed.items()
            }
            active_version = int(raw_active)
        except (ValueError, TypeError, binascii.Error, json.JSONDecodeError) as exc:
            raise EncryptionConfigurationError("invalid PII encryption key configuration") from exc
        return cls(keys, active_version)

    def encrypt_text(self, plaintext: str, *, associated_data: bytes) -> EncryptedField:
        nonce = os.urandom(NONCE_SIZE)
        key = self._keys[self.active_version]
        ciphertext = AESGCM(key).encrypt(nonce, plaintext.encode("utf-8"), associated_data)
        return EncryptedField(nonce + ciphertext, self.active_version)

    def decrypt_text(
        self,
        ciphertext: bytes,
        *,
        key_version: int,
        associated_data: bytes,
    ) -> str:
        key = self._keys.get(key_version)
        if key is None:
            raise DecryptionError(f"unknown PII encryption key version: {key_version}")
        if len(ciphertext) <= NONCE_SIZE:
            raise DecryptionError("encrypted PII payload is malformed")
        nonce, encrypted = ciphertext[:NONCE_SIZE], ciphertext[NONCE_SIZE:]
        try:
            plaintext = AESGCM(key).decrypt(nonce, encrypted, associated_data)
            return plaintext.decode("utf-8")
        except (InvalidTag, ValueError, UnicodeDecodeError) as exc:
            raise DecryptionError("encrypted PII authentication failed") from exc

    def rotate(
        self,
        encrypted: EncryptedField,
        *,
        associated_data: bytes,
    ) -> EncryptedField:
        plaintext = self.decrypt_text(
            encrypted.ciphertext,
            key_version=encrypted.key_version,
            associated_data=associated_data,
        )
        return self.encrypt_text(plaintext, associated_data=associated_data)


def profile_field_aad(user_id: UUID, profile_id: UUID, field_name: str) -> bytes:
    return f"traveler-profile:{user_id}:{profile_id}:{field_name}:v1".encode()
