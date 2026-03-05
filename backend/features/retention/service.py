from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import Reading, RetentionNudgeLog, User
from features.common.timezone import is_matching_local_hour, resolve_tz_name, to_local_hour
from features.memory import service as memory_service
from features.memory import repository as memory_repository

try:
    from zoneinfo import ZoneInfo
except Exception:  # pragma: no cover
    from backports.zoneinfo import ZoneInfo  # type: ignore


@dataclass
class DueNudge:
    user: User
    summary: Dict[str, Any]
    hint: str
    nudge_type: str


QUIET_HOURS_START = 22
QUIET_HOURS_END = 9


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _to_local_day(dt: datetime, tz_name: str | None) -> date:
    src = dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)
    return src.astimezone(ZoneInfo(resolve_tz_name(tz_name))).date()


def _is_quiet_hour(now_utc: datetime, tz_name: str | None) -> bool:
    hour = to_local_hour(now_utc, tz_name)
    return hour >= QUIET_HOURS_START or hour < QUIET_HOURS_END


def _extract_signal_label(summary: Dict[str, Any]) -> str:
    last_changes = dict(summary.get("last_changes") or {})
    signals = list(summary.get("recurring_question_signals") or last_changes.get("recurring_question_signals") or [])
    for item in signals:
        label = str(item.get("label") or item.get("signal") or "").strip()
        if label:
            return label
    return ""


def _extract_topic_label(summary: Dict[str, Any]) -> str:
    topics = list(summary.get("recurring_topics") or [])
    for item in topics:
        topic = str(item.get("topic") or "").strip()
        if topic and topic.lower() not in {"other", "другое", "open", "открытый вопрос"}:
            return topic
    return ""


def _label_from_question_text(question: str) -> str:
    q = str(question or "").strip().lower().replace("ё", "е")
    if not q:
        return ""
    mapping = (
        (("экзам", "егэ", "сесс", "зачет", "зачёт"), "экзамен"),
        (("отнош", "люб", "партнер", "брак"), "отношения"),
        (("работ", "карьер", "должн", "проект"), "карьера"),
        (("ден", "финанс", "кредит", "долг", "доход"), "финансы"),
    )
    for needles, label in mapping:
        if any(x in q for x in needles):
            return label
    return ""


def _pick_resolvable_label(summary: Dict[str, Any], events: list[Any]) -> str:
    signal = _extract_signal_label(summary)
    if signal and memory_service.is_resolvable_followup_context(signal):
        return signal
    topic = _extract_topic_label(summary)
    if topic and memory_service.is_resolvable_followup_context(topic):
        return topic

    for ev in events:
        question = str(getattr(ev, "question", "") or "").strip()
        if not question:
            continue
        if memory_service.classify_safety_risk(question) != "none":
            continue
        label = _label_from_question_text(question)
        if label and memory_service.is_resolvable_followup_context(label):
            return label
    return ""


def _build_trigger_hint(*, trigger: str, summary: Dict[str, Any], events: list[Any]) -> str:
    resolvable_label = _pick_resolvable_label(summary, events)
    if trigger == "unfinished_cycle":
        if resolvable_label:
            if resolvable_label == "экзамен":
                return "Как прошёл экзамен? Если хотите, мягко сверим, как изменилась динамика."
            return f"Похоже, тема «{resolvable_label}» ещё не закрылась. Разрешилась ли ситуация?"
        return "Если тема всё ещё волнует, можно сделать поддерживающий расклад в удобный момент."

    if trigger == "yesterday_continuation":
        if resolvable_label:
            if resolvable_label == "экзамен":
                return "Вчера мы смотрели тему экзамена. Хотите проверить, что изменилось сегодня?"
            return f"Вчера вы касались темы «{resolvable_label}». Хотите мягко проверить продолжение?"
        return "Вчера был расклад по важной теме. Если хотите, можно аккуратно посмотреть продолжение."

    if trigger == "card_day_topic":
        if resolvable_label:
            return f"На сегодня можно вытянуть карту дня по теме «{resolvable_label}» и сверить динамику."
        return "На сегодня доступна новая карта дня — можно мягко свериться с текущей ситуацией."

    return memory_service.build_inline_hint(summary)


def _build_nudge_text(*, hint: str) -> str:
    hint_clean = str(hint or "").strip()
    if hint_clean:
        return (
            "✨ Небольшое напоминание от AI Taro\n\n"
            f"{hint_clean}\n\n"
            "Отключить такие напоминания можно в Профиле → Персонализация AI."
        )
    return (
        "✨ Небольшое напоминание от AI Taro\n\n"
        "Можно мягко свериться с текущей ситуацией через новый расклад.\n\n"
        "Отключить такие напоминания можно в Профиле → Персонализация AI."
    )


async def _has_recent_nudge(db: AsyncSession, *, user_id: int, min_hours: int = 24) -> bool:
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
        if _is_quiet_hour(now, user.retention_nudge_tz):
            continue
        if await _has_recent_nudge(db, user_id=int(user.id), min_hours=24):
            continue
        if await _has_readings_recently(db, user_id=int(user.id), max_hours=12):
            continue

        summary = await memory_service.get_summary(db, user_id=int(user.id))
        events = list(await memory_repository.list_recent_events(db, user_id=int(user.id), days=7, limit=80))
        local_today = _to_local_day(now, user.retention_nudge_tz)
        local_yesterday = local_today - timedelta(days=1)

        non_card_events = [ev for ev in events if str(getattr(ev, "source_kind", "")) != "card_of_day"]
        non_card_today = [
            ev
            for ev in non_card_events
            if _to_local_day(getattr(ev, "event_at", now), user.retention_nudge_tz) == local_today
        ]
        non_card_yesterday = [
            ev
            for ev in non_card_events
            if _to_local_day(getattr(ev, "event_at", now), user.retention_nudge_tz) == local_yesterday
        ]
        card_today = [
            ev
            for ev in events
            if str(getattr(ev, "source_kind", "")) == "card_of_day"
            and _to_local_day(getattr(ev, "event_at", now), user.retention_nudge_tz) == local_today
        ]

        trigger = "default"
        if non_card_events:
            last_non_card = non_card_events[0]
            raw_event_at = getattr(last_non_card, "event_at", None) or now
            event_at = raw_event_at if raw_event_at.tzinfo else raw_event_at.replace(tzinfo=timezone.utc)
            age = now - event_at
            if timedelta(hours=18) <= age <= timedelta(days=4):
                trigger = "unfinished_cycle"
            elif non_card_yesterday and not non_card_today:
                trigger = "yesterday_continuation"
            elif not card_today:
                trigger = "card_day_topic"
        elif not card_today:
            trigger = "card_day_topic"

        hint = _build_trigger_hint(trigger=trigger, summary=summary, events=events)
        out.append(DueNudge(user=user, summary=summary, hint=hint, nudge_type=trigger))
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
            nudge_type=item.nudge_type or "daily",
            scheduled_for=None,
            sent_at=_now_utc(),
            status="sent" if ok else "failed",
            payload={
                "hint": item.hint,
                "trigger": item.nudge_type or "daily",
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
