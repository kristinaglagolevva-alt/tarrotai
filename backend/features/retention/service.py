from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Reading, RetentionNudgeLog, User
from features.common.timezone import is_matching_local_hour, resolve_tz_name
from features.memory import service as memory_service


@dataclass
class DueNudge:
    user: User
    summary: Dict[str, Any]
    hint: str


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _build_nudge_text(*, hint: str) -> str:
    hint_clean = str(hint or "").strip()
    if hint_clean:
        return (
            "✨ Небольшое напоминание от AI Taro\n\n"
            f"{hint_clean}\n\n"
            "Если чувствуете, что тема снова актуальна — откройте мини‑приложение и сделайте расклад."
        )
    return (
        "✨ Небольшое напоминание от AI Taro\n\n"
        "Можно мягко свериться с текущей ситуацией через новый расклад."
    )


async def _has_recent_nudge(db: AsyncSession, *, user_id: int, min_hours: int = 20) -> bool:
    cutoff = _now_utc() - timedelta(hours=max(1, int(min_hours)))
    q = await db.execute(
        select(RetentionNudgeLog.id)
        .where(
            RetentionNudgeLog.user_id == int(user_id),
            RetentionNudgeLog.status == "sent",
            RetentionNudgeLog.sent_at >= cutoff,
        )
        .limit(1)
    )
    return q.scalar_one_or_none() is not None


async def _has_readings_recently(db: AsyncSession, *, user_id: int, max_hours: int = 12) -> bool:
    cutoff = _now_utc() - timedelta(hours=max(1, int(max_hours)))
    q = await db.execute(
        select(Reading.id)
        .where(
            Reading.user_id == int(user_id),
            Reading.created_at >= cutoff,
        )
        .limit(1)
    )
    return q.scalar_one_or_none() is not None


async def list_due_nudges(db: AsyncSession, *, limit: int = 100) -> List[DueNudge]:
    now = _now_utc()
    q = await db.execute(
        select(User)
        .where(
            User.memory_opt_in.is_(True),
            User.retention_nudges_opt_in.is_(True),
            User.retention_nudge_hour_local.is_not(None),
        )
        .order_by(desc(User.created_at))
        .limit(max(1, min(int(limit), 500)))
    )
    users = list(q.scalars().all())
    if not users:
        return []

    out: List[DueNudge] = []
    for user in users:
        if not is_matching_local_hour(now, user.retention_nudge_tz, user.retention_nudge_hour_local):
            continue
        if await _has_recent_nudge(db, user_id=int(user.id), min_hours=20):
            continue
        if await _has_readings_recently(db, user_id=int(user.id), max_hours=12):
            continue

        summary = await memory_service.get_summary(db, user_id=int(user.id))
        hint = memory_service.build_inline_hint(summary)
        out.append(DueNudge(user=user, summary=summary, hint=hint))
    return out


async def deliver_due_nudges(
    db: AsyncSession,
    *,
    send_message: Callable[[int, str], Any],
    limit: int = 100,
) -> Dict[str, int]:
    due = await list_due_nudges(db, limit=limit)
    if not due:
        return {"checked": 0, "sent": 0, "failed": 0}

    sent = 0
    failed = 0
    for item in due:
        user = item.user
        text = _build_nudge_text(hint=item.hint)
        ok = False
        err_text: Optional[str] = None
        try:
            ok = bool(await send_message(int(user.telegram_id), text))
        except Exception as exc:
            ok = False
            err_text = repr(exc)

        log_row = RetentionNudgeLog(
            user_id=int(user.id),
            nudge_type="daily",
            scheduled_for=None,
            sent_at=_now_utc(),
            status="sent" if ok else "failed",
            payload={
                "hint": item.hint,
                "tz": resolve_tz_name(user.retention_nudge_tz),
                "hour_local": user.retention_nudge_hour_local,
            },
            error=err_text,
        )
        db.add(log_row)
        if ok:
            sent += 1
        else:
            failed += 1

    return {"checked": len(due), "sent": sent, "failed": failed}

