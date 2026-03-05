from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterable, Optional, Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import UserMemoryEvent, UserMemoryProfile


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def insert_memory_event(
    db: AsyncSession,
    *,
    user_id: int,
    source_kind: str,
    source_id: Optional[int],
    topic: str,
    spread_type: str,
    question: str,
    cards: list,
    primary_card: str,
    primary_card_reversed: bool,
    sentiment_label: str,
    tags: list,
    summary: dict,
) -> UserMemoryEvent:
    row = UserMemoryEvent(
        user_id=int(user_id),
        source_kind=str(source_kind),
        source_id=int(source_id) if source_id is not None else None,
        topic=str(topic or "other"),
        spread_type=str(spread_type or ""),
        question=str(question or ""),
        cards=cards,
        primary_card=str(primary_card or ""),
        primary_card_reversed=bool(primary_card_reversed),
        sentiment_label=str(sentiment_label or "neutral"),
        tags=tags,
        summary=summary,
    )
    db.add(row)
    await db.flush()
    return row


async def list_recent_events(db: AsyncSession, *, user_id: int, days: int = 90, limit: int = 400) -> Sequence[UserMemoryEvent]:
    since = _now_utc() - timedelta(days=max(1, int(days)))
    q = (
        select(UserMemoryEvent)
        .where(UserMemoryEvent.user_id == int(user_id), UserMemoryEvent.event_at >= since)
        .order_by(desc(UserMemoryEvent.event_at))
        .limit(max(1, min(int(limit), 1000)))
    )
    res = await db.execute(q)
    return list(res.scalars().all())


async def get_memory_profile(db: AsyncSession, *, user_id: int) -> Optional[UserMemoryProfile]:
    q = select(UserMemoryProfile).where(UserMemoryProfile.user_id == int(user_id))
    res = await db.execute(q)
    return res.scalar_one_or_none()


async def upsert_memory_profile(
    db: AsyncSession,
    *,
    user_id: int,
    recurring_topics: list,
    repeated_cards: list,
    cycle_hints: list,
    last_changes: dict,
) -> UserMemoryProfile:
    row = await get_memory_profile(db, user_id=user_id)
    if not row:
        row = UserMemoryProfile(
            user_id=int(user_id),
            recurring_topics=recurring_topics,
            repeated_cards=repeated_cards,
            cycle_hints=cycle_hints,
            last_changes=last_changes,
        )
        db.add(row)
        await db.flush()
        return row

    row.recurring_topics = recurring_topics
    row.repeated_cards = repeated_cards
    row.cycle_hints = cycle_hints
    row.last_changes = last_changes
    return row


async def purge_old_events(db: AsyncSession, *, days: int = 90) -> int:
    since = _now_utc() - timedelta(days=max(1, int(days)))
    q = await db.execute(select(UserMemoryEvent).where(UserMemoryEvent.event_at < since))
    rows = list(q.scalars().all())
    for row in rows:
        await db.delete(row)
    return len(rows)


async def has_source_event(
    db: AsyncSession,
    *,
    user_id: int,
    source_kind: str,
    source_id: Optional[int],
) -> bool:
    if source_id is None:
        return False
    q = (
        select(UserMemoryEvent.id)
        .where(
            UserMemoryEvent.user_id == int(user_id),
            UserMemoryEvent.source_kind == str(source_kind),
            UserMemoryEvent.source_id == int(source_id),
        )
        .limit(1)
    )
    res = await db.execute(q)
    return res.scalar_one_or_none() is not None


async def list_existing_source_ids(
    db: AsyncSession,
    *,
    user_id: int,
    source_kind: str,
    source_ids: Iterable[int],
) -> set[int]:
    normalized = sorted({int(x) for x in source_ids if x is not None})
    if not normalized:
        return set()
    q = await db.execute(
        select(UserMemoryEvent.source_id).where(
            UserMemoryEvent.user_id == int(user_id),
            UserMemoryEvent.source_kind == str(source_kind),
            UserMemoryEvent.source_id.in_(normalized),
        )
    )
    return {int(x) for x in q.scalars().all() if x is not None}
