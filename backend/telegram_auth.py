import hashlib
import hmac
import json
import time
from urllib.parse import parse_qsl
import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
INITDATA_TTL_SECONDS = int(os.getenv("TELEGRAM_INITDATA_TTL_SECONDS", "86400"))
INITDATA_MAX_FUTURE_SKEW_SECONDS = int(os.getenv("TELEGRAM_INITDATA_MAX_FUTURE_SKEW_SECONDS", "300"))


def validate_init_data(init_data: str) -> dict:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    data = dict(parse_qsl(init_data))
    received_hash = data.pop("hash", None)

    if not received_hash:
        raise ValueError("Missing hash")

    data_check_string = "\n".join(f"{k}={v}" for k, v in sorted(data.items()))

    secret_key = hmac.new(
        b"WebAppData",
        BOT_TOKEN.encode(),
        hashlib.sha256,
    ).digest()

    calculated_hash = hmac.new(
        secret_key,
        data_check_string.encode(),
        hashlib.sha256,
    ).hexdigest()

    if not hmac.compare_digest(calculated_hash, received_hash):
        raise ValueError("Invalid initData signature")

    auth_date_raw = str(data.get("auth_date") or "").strip()
    if not auth_date_raw:
        raise ValueError("Missing auth_date")
    try:
        auth_date = int(auth_date_raw)
    except Exception:
        raise ValueError("Invalid auth_date")

    now = int(time.time())
    if auth_date > now + INITDATA_MAX_FUTURE_SKEW_SECONDS:
        raise ValueError("Invalid initData timestamp (future)")
    if (now - auth_date) > INITDATA_TTL_SECONDS:
        raise ValueError("initData expired")

    return json.loads(data["user"])
