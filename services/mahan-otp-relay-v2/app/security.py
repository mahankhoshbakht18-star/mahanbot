from __future__ import annotations

import hashlib
import hmac
import secrets

from cryptography.fernet import Fernet


def secret_hash(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def matches(value: str, expected_hash: str, pepper: str) -> bool:
    return hmac.compare_digest(secret_hash(value, pepper), expected_hash)


def national_id_hash(value: str, pepper: str) -> str:
    return secret_hash("nid:" + value, pepper)


def issue_token() -> str:
    return secrets.token_urlsafe(32)


def encrypt_otp(otp: str, key: str) -> str:
    return Fernet(key.encode()).encrypt(otp.encode()).decode()


def decrypt_otp(ciphertext: str, key: str) -> str:
    return Fernet(key.encode()).decrypt(ciphertext.encode()).decode()
