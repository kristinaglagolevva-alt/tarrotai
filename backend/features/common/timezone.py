from __future__ import annotations

from datetime import datetime, timezone

try:  # Python 3.9+
    from zoneinfo import ZoneInfo
except Exception:  # Python 3.8 fallback
    from backports.zoneinfo import ZoneInfo  # type: ignore


def resolve_tz_name(name: str | None) -> str:
    raw = str(name or "").strip()
    if not raw:
        return "UTC"
    try:
        ZoneInfo(raw)
        return raw
    except Exception:
        return "UTC"


def to_local_hour(now_utc: datetime, tz_name: str | None) -> int:
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)
    else:
        now_utc = now_utc.astimezone(timezone.utc)
    tz = ZoneInfo(resolve_tz_name(tz_name))
    return int(now_utc.astimezone(tz).hour)


def is_matching_local_hour(now_utc: datetime, tz_name: str | None, hour_local: int | None) -> bool:
    if hour_local is None:
        return False
    try:
        target = int(hour_local)
    except Exception:
        return False
    if target < 0 or target > 23:
        return False
    return to_local_hour(now_utc, tz_name) == target
