"""Password and signed-session primitives for PharmaDrone AI."""
from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone
import hashlib
import hmac
import json
import os
import re
import secrets
from typing import Any


PASSWORD_ALGORITHM = "pbkdf2-sha256-v1"
PASSWORD_ITERATIONS = 310_000
TOKEN_LIFETIME_HOURS = 12
_EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


class AuthenticationError(ValueError):
    """Safe authentication failure suitable for an API response."""


def normalize_email(email: str) -> str:
    value = " ".join(str(email or "").split()).strip().casefold()
    if len(value) > 254 or not _EMAIL_RE.match(value):
        raise ValueError("A valid email address is required.")
    return value


def validate_password(password: str) -> None:
    if not isinstance(password, str) or len(password) < 10:
        raise ValueError("Password must contain at least 10 characters.")
    if len(password) > 256:
        raise ValueError("Password is too long.")


def hash_password(password: str) -> str:
    validate_password(password)
    salt = os.urandom(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt, PASSWORD_ITERATIONS, dklen=32,
    )
    return "$".join((
        PASSWORD_ALGORITHM,
        base64.urlsafe_b64encode(salt).decode("ascii").rstrip("="),
        base64.urlsafe_b64encode(digest).decode("ascii").rstrip("="),
    ))


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, salt_value, expected_value = encoded.split("$", 2)
        if algorithm != PASSWORD_ALGORITHM:
            return False
        actual = hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), _decode(salt_value),
            PASSWORD_ITERATIONS, dklen=32,
        )
        return hmac.compare_digest(actual, _decode(expected_value))
    except (TypeError, ValueError):
        return False


def _auth_secret() -> bytes:
    configured = os.getenv("PHARMADRONE_AI_AUTH_SECRET", "").strip()
    environment = os.getenv("APP_ENV", "development").strip().lower()
    if configured:
        if len(configured) < 32:
            raise RuntimeError("PHARMADRONE_AI_AUTH_SECRET must contain at least 32 characters.")
        return configured.encode("utf-8")
    if environment in {"production", "prod"}:
        raise RuntimeError("PHARMADRONE_AI_AUTH_SECRET is required in production.")
    return b"development-only-secret-change-before-deploy"


def _encode_json(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def create_session_token(user_id: str, workspace_id: str) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": user_id,
        "workspace_id": workspace_id,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(hours=TOKEN_LIFETIME_HOURS)).timestamp()),
        "nonce": secrets.token_hex(8),
    }
    encoded = _encode_json(payload)
    signature = hmac.new(_auth_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
    return encoded + "." + base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")


def decode_session_token(token: str) -> dict[str, Any]:
    try:
        encoded, supplied_signature = token.split(".", 1)
        expected = hmac.new(_auth_secret(), encoded.encode("ascii"), hashlib.sha256).digest()
        if not hmac.compare_digest(expected, _decode(supplied_signature)):
            raise AuthenticationError("Invalid session token.")
        payload = json.loads(_decode(encoded).decode("utf-8"))
        if int(payload.get("exp") or 0) <= int(datetime.now(timezone.utc).timestamp()):
            raise AuthenticationError("Session has expired.")
        if not payload.get("sub") or not payload.get("workspace_id"):
            raise AuthenticationError("Invalid session token.")
        return payload
    except AuthenticationError:
        raise
    except Exception as exc:
        raise AuthenticationError("Invalid session token.") from exc
