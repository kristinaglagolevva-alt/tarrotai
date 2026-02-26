from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict

from sqlalchemy.ext.asyncio import AsyncSession

from . import repository
from .extractor import build_memory_event_payload


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _build_cycle_hints(*, topic_counts: Counter, card_counts: Counter) -> list[str]:
    hints: list[str] = []
    for topic, count in topic_counts.most_common(3):
        if count >= 3:
            hints.append(f"Эта тема возвращается уже {count}-й раз: {topic}.")
    for card, count in card_counts.most_common(2):
        if card and count >= 2:
            hints.append(f"Карта «{card}» повторяется ({count} раз), это важный паттерн.")
    return hints[:4]


def _compact_topic_label(topic: str) -> str:
    t = str(topic or "").strip().lower()
    mapping = {
        "relations": "отношения",
        "career": "карьера",
        "finance": "финансы",
        "other": "другое",
    }
    return mapping.get(t, t or "другое")


async def ingest_event(
    db: AsyncSession,
    *,
    user_id: int,
    source_kind: str,
    source_id: int | None,
    topic: str,
    spread_type: str,
    question: str,
    cards: list,
    description: str,
) -> None:
    payload = build_memory_event_payload(
        source_kind=source_kind,
        source_id=source_id,
        topic=topic,
        spread_type=spread_type,
        question=question,
        cards=cards,
        description=description,
    )
    await repository.insert_memory_event(
        db,
        user_id=int(user_id),
        source_kind=str(payload["source_kind"]),
        source_id=payload.get("source_id"),
        topic=str(payload.get("topic") or "other"),
        spread_type=str(payload.get("spread_type") or ""),
        question=str(payload.get("question") or ""),
        cards=list(payload.get("cards") or []),
        primary_card=str(payload.get("primary_card") or ""),
        primary_card_reversed=bool(payload.get("primary_card_reversed")),
        sentiment_label=str(payload.get("sentiment_label") or "neutral"),
        tags=list(payload.get("tags") or []),
        summary={
            "question_tokens": list(payload.get("question_tokens") or []),
            "description_excerpt": str(payload.get("description_excerpt") or ""),
        },
    )


async def rebuild_profile(db: AsyncSession, *, user_id: int, days: int = 90) -> Dict[str, Any]:
    events = await repository.list_recent_events(db, user_id=int(user_id), days=days, limit=500)

    topic_counts: Counter = Counter()
    topic_last_seen: dict[str, datetime] = {}
    card_counts: Counter = Counter()
    card_last_seen: dict[str, datetime] = {}

    for ev in events:
        topic = _compact_topic_label(ev.topic)
        if topic:
            topic_counts[topic] += 1
            topic_last_seen[topic] = max(topic_last_seen.get(topic, ev.event_at), ev.event_at)

        card = str(ev.primary_card or "").strip()
        if card:
            card_counts[card] += 1
            card_last_seen[card] = max(card_last_seen.get(card, ev.event_at), ev.event_at)

    recurring_topics = [
        {
            "topic": topic,
            "count": int(count),
            "last_seen": topic_last_seen.get(topic).isoformat() if topic_last_seen.get(topic) else None,
        }
        for topic, count in topic_counts.most_common(5)
        if count >= 2
    ]

    repeated_cards = [
        {
            "card": card,
            "count": int(count),
            "last_seen": card_last_seen.get(card).isoformat() if card_last_seen.get(card) else None,
        }
        for card, count in card_counts.most_common(5)
        if count >= 2
    ]

    cycle_hints = _build_cycle_hints(topic_counts=topic_counts, card_counts=card_counts)

    last_changes = {
        "updated_at": _now_utc().isoformat(),
        "events_considered": len(events),
        "top_topic": recurring_topics[0]["topic"] if recurring_topics else None,
        "top_card": repeated_cards[0]["card"] if repeated_cards else None,
    }

    await repository.upsert_memory_profile(
        db,
        user_id=int(user_id),
        recurring_topics=recurring_topics,
        repeated_cards=repeated_cards,
        cycle_hints=cycle_hints,
        last_changes=last_changes,
    )

    return {
        "recurring_topics": recurring_topics,
        "repeated_cards": repeated_cards,
        "cycle_hints": cycle_hints,
        "last_changes": last_changes,
    }


async def get_summary(db: AsyncSession, *, user_id: int) -> Dict[str, Any]:
    profile = await repository.get_memory_profile(db, user_id=int(user_id))
    if not profile:
        return {
            "recurring_topics": [],
            "repeated_cards": [],
            "cycle_hints": [],
            "last_changes": {"updated_at": None, "events_considered": 0},
        }
    return {
        "recurring_topics": list(profile.recurring_topics or []),
        "repeated_cards": list(profile.repeated_cards or []),
        "cycle_hints": list(profile.cycle_hints or []),
        "last_changes": dict(profile.last_changes or {}),
    }


def build_prompt_context(summary: Dict[str, Any]) -> str:
    cycle_hints = list(summary.get("cycle_hints") or [])
    repeated_cards = list(summary.get("repeated_cards") or [])
    recurring_topics = list(summary.get("recurring_topics") or [])

    parts: list[str] = []
    if recurring_topics:
        topics = ", ".join([str(x.get("topic") or "").strip() for x in recurring_topics[:3] if str(x.get("topic") or "").strip()])
        if topics:
            parts.append(f"Повторяющиеся темы пользователя: {topics}.")
    if repeated_cards:
        cards = ", ".join([str(x.get("card") or "").strip() for x in repeated_cards[:2] if str(x.get("card") or "").strip()])
        if cards:
            parts.append(f"Ранее часто выпадали карты: {cards}.")
    if cycle_hints:
        parts.append(f"Паттерны: {' '.join(cycle_hints[:2])}")

    if not parts:
        return ""

    return (
        "Контекст из истории (используй аккуратно, без категоричных выводов, без медицинских/юридических диагнозов):\n"
        + "\n".join(parts)
    )


def build_inline_hint(summary: Dict[str, Any]) -> str:
    cycle_hints = list(summary.get("cycle_hints") or [])
    if cycle_hints:
        return str(cycle_hints[0])

    recurring_topics = list(summary.get("recurring_topics") or [])
    if recurring_topics:
        top = recurring_topics[0]
        topic = str(top.get("topic") or "эта тема")
        count = int(top.get("count") or 0)
        if count >= 2:
            return f"Вы уже возвращались к теме «{topic}» {count} раз."

    repeated_cards = list(summary.get("repeated_cards") or [])
    if repeated_cards:
        top_card = str(repeated_cards[0].get("card") or "").strip()
        count = int(repeated_cards[0].get("count") or 0)
        if top_card and count >= 2:
            return f"Карта «{top_card}» повторяется в ваших раскладах ({count} раз)."

    return ""


def classify_safety_risk(question: str) -> str:
    q = str(question or "").lower()
    if any(x in q for x in ["суиц", "самоубий", "не хочу жить", "умереть"]):
        return "crisis"
    if any(x in q for x in ["болит", "болез", "диагноз", "симптом", "лекар"]):
        return "medical"
    return "none"
