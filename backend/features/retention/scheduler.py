from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Callable, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from features.memory import repository as memory_repository
from . import service as retention_service


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def run_retention_cycle(
    db: AsyncSession,
    *,
    send_message: Callable[[int, str], Any],
    memory_retention_days: int = 90,
) -> Dict[str, int]:
    purge_count = await memory_repository.purge_old_events(db, days=memory_retention_days)
    nudge_stats = await retention_service.deliver_due_nudges(db, send_message=send_message, limit=120)
    return {
        "purged_events": int(purge_count),
        "nudges_checked": int(nudge_stats.get("checked") or 0),
        "nudges_sent": int(nudge_stats.get("sent") or 0),
        "nudges_failed": int(nudge_stats.get("failed") or 0),
    }

