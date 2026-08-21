import base64
import json
import secrets
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from agent_system.security.encryption import FieldEncryptor


def test_aes_gcm_256_encryption_and_decryption():
    key_bytes = secrets.token_bytes(32)
    encryptor = FieldEncryptor(keys={1: key_bytes}, active_version=1)

    plaintext = "Passport: N12345678, DOB: 1995-05-15, Name: NGUYEN VAN A"
    aad = b"test-aad-data"
    encrypted_field = encryptor.encrypt_text(plaintext, associated_data=aad)

    assert encrypted_field.ciphertext != plaintext.encode("utf-8")
    assert len(encrypted_field.ciphertext) > 20
    assert encrypted_field.key_version == 1

    decrypted = encryptor.decrypt_text(
        encrypted_field.ciphertext,
        key_version=encrypted_field.key_version,
        associated_data=aad,
    )
    assert decrypted == plaintext
