import time
import os
import re
from jose import jwt
from dotenv import load_dotenv

load_dotenv()

JWT_EXPIRE_SECONDS = int(os.getenv("JWT_EXPIRE_SECONDS", "2592000"))
JWT_ALG = "HS256"
JWT_SECRET_MIN_LENGTH = int(os.getenv("JWT_SECRET_MIN_LENGTH", "32"))

_WEAK_SECRET_VALUES = {
    "changeme",
    "change_me",
    "change-me",
    "default",
    "secret",
    "supersecret",
    "super_secret",
    "super-secret",
    "jwtsecret",
    "jwt_secret",
    "jwt-secret",
    "password",
    "test",
    "example",
    "admin",
    "root",
    "12345",
    "qwerty",
    "super_secret_change_me",
}
_WEAK_SECRET_HINT_RE = re.compile(
    r"(change.?me|replace.?me|default|example|test.?key|test.?secret|jwt.?secret)",
    re.IGNORECASE,
)


def _load_jwt_secret() -> str:
    secret = (os.getenv("JWT_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET is not set")

    if len(secret) < JWT_SECRET_MIN_LENGTH:
        raise RuntimeError(
            f"JWT_SECRET is too short (len={len(secret)}). "
            f"Use at least {JWT_SECRET_MIN_LENGTH} random characters."
        )

    normalized = re.sub(r"[^a-z0-9]+", "", secret.lower())
    if normalized in _WEAK_SECRET_VALUES or _WEAK_SECRET_HINT_RE.search(secret):
        raise RuntimeError(
            "JWT_SECRET looks like a default/weak placeholder. "
            "Set a strong random value and restart."
        )
    return secret


JWT_SECRET = _load_jwt_secret()


def create_jwt(user_id: int, telegram_id: int) -> str:
    now = int(time.time())
    payload = {
        "sub": str(user_id),
        "telegram_id": telegram_id,
        "iat": now,
        "exp": now + JWT_EXPIRE_SECONDS,
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALG)


def decode_jwt(token: str) -> dict:
    return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALG])
