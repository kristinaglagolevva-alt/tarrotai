import time
import os
import re
import warnings
from jose import jwt
from dotenv import load_dotenv

load_dotenv()

JWT_EXPIRE_SECONDS = int(os.getenv("JWT_EXPIRE_SECONDS", "2592000"))
JWT_ALG = "HS256"
JWT_SECRET_MIN_LENGTH = int(os.getenv("JWT_SECRET_MIN_LENGTH", "32"))
JWT_ALLOW_WEAK_SECRET = (os.getenv("JWT_ALLOW_WEAK_SECRET") or "").strip().lower() in {"1", "true", "yes"}


def _load_jwt_secret() -> str:
    secret = (os.getenv("JWT_SECRET") or "").strip()
    if not secret:
        raise RuntimeError("JWT_SECRET is not set")

    weak_by_len = len(secret) < JWT_SECRET_MIN_LENGTH
    weak_by_pattern = bool(re.search(r"(change|secret|default|test|12345|qwerty)", secret, re.IGNORECASE))
    if weak_by_len or weak_by_pattern:
        msg = (
            f"JWT_SECRET is too weak (len={len(secret)}). "
            f"Use at least {JWT_SECRET_MIN_LENGTH} random characters."
        )
        if JWT_ALLOW_WEAK_SECRET:
            warnings.warn(msg)
        else:
            raise RuntimeError(msg)
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
