"""Secret encryption at rest and redaction helpers."""
from __future__ import annotations

import re
from typing import Any

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

from app.config import get_settings

_SENSITIVE = re.compile(r"(pass(word)?|secret|community|token|auth_key|priv_key|private|credential|api_key|_enc$)", re.I)
REDACTED = "***redacted***"


def _fernet() -> Fernet:
    return Fernet(get_settings().encryption_key.encode())


def encrypt_secret(value: str | None) -> str | None:
    if value is None or value == "":
        return None
    return _fernet().encrypt(value.encode()).decode()


def decrypt_secret(token: str | None) -> str | None:
    if not token:
        return None
    try:
        return _fernet().decrypt(token.encode()).decode()
    except InvalidToken as exc:  # pragma: no cover - key rotation problem
        raise RuntimeError("Unable to decrypt stored credential (encryption key changed?)") from exc


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt(rounds=12)).decode()


def verify_password(password: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), hashed.encode())
    except ValueError:
        return False


def is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE.search(key))


def redact(value: Any) -> Any:
    """Recursively replace values whose key looks sensitive. Used for audit logs, job payloads and logs."""
    if isinstance(value, dict):
        return {k: (REDACTED if is_sensitive_key(str(k)) and v not in (None, "") else redact(v)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def validate_password_strength(password: str) -> list[str]:
    problems = []
    if len(password) < 10:
        problems.append("must be at least 10 characters")
    if not re.search(r"[A-Za-z]", password) or not re.search(r"\d", password):
        problems.append("must contain letters and digits")
    return problems
