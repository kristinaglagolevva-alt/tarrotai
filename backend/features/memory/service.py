from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
import re
from typing import Any, Dict, Sequence

from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CardOfDay, Reading
from . import repository
from .extractor import build_memory_event_payload


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


_GENERIC_TOPICS = {
    "",
    "other",
    "другое",
    "open",
    "open question",
    "открытый вопрос",
    "эта тема",
}

_GENERIC_QUESTION_TOKENS = {
    "тема",
    "другое",
    "вопрос",
    "карта",
    "карты",
    "расклад",
    "расклада",
    "будет",
    "буду",
    "хочу",
    "узнать",
    "можно",
    "надо",
    "если",
    "когда",
    "почему",
    "тогда",
    "опять",
    "снова",
    "ли",
    "про",
    "about",
    "question",
    "other",
}

_NON_RESOLVABLE_HINT_TOKENS = {
    "болез",
    "здоров",
    "диагноз",
    "симптом",
    "лечен",
    "лекар",
    "операц",
    "суиц",
    "самоуб",
    "не хочу жить",
    "умер",
    "смерт",
    "похорон",
    "онколог",
    "рак",
}

_CAPSULE_LABEL_MAP = {
    "экзамен": "экзамен и результат",
    "отношения": "отношения и динамика",
    "карьера": "карьера и рост",
    "финансы": "финансы и стабильность",
    "здоровье": "здоровье и восстановление",
}

_CAPSULE_TOPIC_MAP = {
    "relations": "отношения",
    "relationship": "отношения",
    "career": "карьера",
    "finance": "финансы",
    "other": "",
    "open": "",
    "open question": "",
}

_CAPSULE_STOP_WORDS = {
    "я",
    "ты",
    "вы",
    "он",
    "она",
    "они",
    "мы",
    "это",
    "или",
    "как",
    "что",
    "когда",
    "почему",
    "будет",
    "буду",
    "хочу",
    "узнать",
    "про",
    "ли",
    "мне",
    "меня",
    "мой",
    "моя",
    "моего",
    "моей",
    "other",
    "open",
    "question",
}

_CARD_SUIT_RULES = (
    ("жезл", "жезлы"),
    ("куб", "кубки"),
    ("меч", "мечи"),
    ("пентак", "пентакли"),
)

_MOTIF_KEYWORDS = {
    "внутреннее противоречие": (
        "внутрен",
        "противореч",
        "сомнен",
        "колебан",
        "двойствен",
        "неопредел",
        "конфликт",
    ),
    "контроль и давление": (
        "контрол",
        "домини",
        "давлен",
        "жестк",
        "манипул",
        "тиран",
        "власть",
    ),
    "эмоциональное напряжение": (
        "эмоцион",
        "тревог",
        "страх",
        "раним",
        "неувер",
        "обид",
        "беспокой",
    ),
    "пауза и переоценка": (
        "пауза",
        "задерж",
        "ожидан",
        "пересмотр",
        "переоцен",
        "замедл",
        "терпени",
    ),
    "рост и новые возможности": (
        "рост",
        "возможн",
        "шанс",
        "успех",
        "прорыв",
        "гармон",
        "ресурс",
        "обновлен",
    ),
}

_SEMANTIC_CONCEPT_RULES = {
    "переезд": (
        "переезд",
        "переез",
        "переех",
        "переед",
        "релок",
        "эмигр",
        "иммигр",
        "миграц",
        "стран",
        "город",
        "виза",
        "пмж",
        "внж",
    ),
    "экзамен": (
        "экзам",
        "егэ",
        "сесс",
        "зачет",
        "диплом",
        "поступить",
        "поступлен",
        "балл",
    ),
    "отношения": (
        "отнош",
        "люб",
        "партнер",
        "брак",
        "муж",
        "жен",
        "бывш",
        "расст",
        "ссора",
        "примир",
    ),
    "карьера": (
        "работ",
        "карьер",
        "должн",
        "проект",
        "офис",
        "началь",
        "уволь",
        "собесед",
        "бизнес",
    ),
    "финансы": (
        "ден",
        "финанс",
        "кредит",
        "долг",
        "доход",
        "бюджет",
        "зарплат",
        "инвест",
        "покупк",
        "ипотек",
    ),
    "здоровье": (
        "здоров",
        "болез",
        "симптом",
        "диагноз",
        "лекар",
        "врач",
        "терап",
        "операц",
    ),
}

_SEMANTIC_LABELS = {
    "переезд": "переезд и смена места",
    "экзамен": "экзамен и результат",
    "отношения": "отношения и динамика",
    "карьера": "карьера и рост",
    "финансы": "финансы и стабильность",
    "здоровье": "здоровье и восстановление",
}

_SEMANTIC_STOP_WORDS = _GENERIC_QUESTION_TOKENS | _GENERIC_TOPICS | {
    "стоит",
    "можно",
    "нужно",
    "лучше",
    "хочется",
    "будет",
    "буду",
    "хочу",
    "хотел",
    "хотела",
    "ли",
    "чтобы",
    "или",
    "какой",
    "какая",
    "какие",
    "какое",
    "эта",
    "этот",
    "эти",
    "очень",
    "тема",
}

_RUSSIAN_STEM_SUFFIXES = (
    "иями",
    "ями",
    "ами",
    "ого",
    "ему",
    "ими",
    "ыми",
    "ая",
    "яя",
    "ое",
    "ее",
    "ый",
    "ий",
    "ой",
    "ам",
    "ям",
    "ах",
    "ях",
    "ом",
    "ем",
    "ов",
    "ев",
    "ей",
    "а",
    "я",
    "ы",
    "и",
    "е",
    "у",
    "ю",
    "о",
)


def _is_generic_topic_label(topic: str) -> bool:
    t = str(topic or "").strip().lower()
    return t in _GENERIC_TOPICS


def _stem_ru_token(token: str) -> str:
    src = str(token or "").strip().lower().replace("ё", "е")
    if len(src) <= 3:
        return src
    for suffix in _RUSSIAN_STEM_SUFFIXES:
        if src.endswith(suffix) and len(src) - len(suffix) >= 4:
            return src[: -len(suffix)]
    return src


def _semantic_tokens_from_text(text: str) -> set[str]:
    raw_tokens = re.findall(r"[a-zа-я0-9]{3,}", str(text or "").lower(), flags=re.IGNORECASE)
    out: set[str] = set()
    for raw in raw_tokens:
        normalized = _normalize_question_signal(raw)
        if not normalized:
            continue
        if normalized in _SEMANTIC_STOP_WORDS:
            continue
        stem = _stem_ru_token(normalized)
        if not stem or len(stem) < 3 or stem in _SEMANTIC_STOP_WORDS:
            continue
        out.add(stem)
    return out


def _semantic_concepts_from_text(*, question: str, topic: str = "") -> set[str]:
    src = str(question or "").lower().replace("ё", "е")
    concepts: set[str] = set()

    for concept, needles in _SEMANTIC_CONCEPT_RULES.items():
        if any(needle in src for needle in needles):
            concepts.add(concept)

    for token in _question_signals_from_text(question):
        label = _signal_label(token)
        if label and not _is_generic_topic_label(label):
            concepts.add(label)

    normalized_topic = _compact_topic_label(topic)
    if normalized_topic and not _is_generic_topic_label(normalized_topic):
        topic_label = _signal_label(normalized_topic)
        concepts.add(topic_label or normalized_topic)

    return concepts


def _semantic_signature(*, question: str, topic: str = "") -> dict[str, set[str]]:
    return {
        "tokens": _semantic_tokens_from_text(question),
        "concepts": _semantic_concepts_from_text(question=question, topic=topic),
    }


def _normalize_question_signal(token: str) -> str:
    t = str(token or "").strip().lower().replace("ё", "е")
    if not t or len(t) < 4:
        return ""
    if t.isdigit():
        return ""
    if t in _GENERIC_QUESTION_TOKENS:
        return ""
    if re.fullmatch(r"[0-9a-z]+", t):
        # keep latin product names if needed, but drop random ids
        if len(t) >= 8:
            return ""
    # Lightweight domain normalization for relocation-like questions.
    # This keeps runtime cheap while improving cross-phrase matching:
    # "переезжать", "переехать", "релокация" -> "переезд".
    if t.startswith(("переез", "переех", "переед", "релок", "эмигр", "иммигр", "миграц")):
        return "переезд"
    return t


def _signal_label(signal: str) -> str:
    s = str(signal or "").strip().lower()
    if not s:
        return ""
    mapping = (
        (("переезд", "переез", "переех", "переед", "релок", "эмигр", "иммигр", "миграц"), "переезд"),
        (("экзам", "егэ", "сесс", "сдам", "сдат"), "экзамен"),
        (("отнош", "люб", "партнер", "брак"), "отношения"),
        (("работ", "карьер", "должн", "проект"), "карьера"),
        (("ден", "финанс", "кредит", "долг", "доход"), "финансы"),
        (("здоров", "болез", "симптом", "диагноз"), "здоровье"),
    )
    for needles, label in mapping:
        if any(n in s for n in needles):
            return label
    return s


def _signal_labels_from_tokens(tokens: Sequence[str]) -> set[str]:
    labels: set[str] = set()
    for token in tokens:
        label = _signal_label(str(token or ""))
        if not label:
            continue
        if _is_generic_topic_label(label):
            continue
        labels.add(label)
    return labels


def _normalize_capsule_text(text: str, *, max_words: int = 4) -> str:
    raw_tokens = re.findall(r"[a-zа-я0-9]{2,}", str(text or "").lower(), flags=re.IGNORECASE)
    out: list[str] = []
    for token in raw_tokens:
        token = token.replace("ё", "е").strip()
        if not token or token in _CAPSULE_STOP_WORDS:
            continue
        if token in _GENERIC_QUESTION_TOKENS:
            continue
        if token in _GENERIC_TOPICS:
            continue
        if len(token) < 2:
            continue
        out.append(token)
        if len(out) >= max_words:
            break
    return " ".join(out).strip()


def _normalize_theme_capsule(text: str) -> str:
    """
    Нормализует подпись темы к человеко-понятному виду.
    Никогда не возвращает generic-лейблы вроде "другое/open".
    """
    raw = _normalize_capsule_text(str(text or ""), max_words=6)
    if not raw:
        return ""
    if _is_generic_topic_label(raw):
        return ""

    labels = _signal_labels_from_tokens(_question_signals_from_text(raw))
    for label in labels:
        mapped = _CAPSULE_LABEL_MAP.get(label)
        if mapped:
            return mapped
    for label in labels:
        cleaned = _normalize_capsule_text(label, max_words=4)
        if cleaned and not _is_generic_topic_label(cleaned):
            return cleaned

    cleaned_raw = _normalize_capsule_text(raw, max_words=4)
    if cleaned_raw and not _is_generic_topic_label(cleaned_raw):
        return cleaned_raw
    return ""


def _topic_label_for_capsule(topic: str) -> str:
    low = str(topic or "").strip().lower()
    mapped = _CAPSULE_TOPIC_MAP.get(low)
    if mapped is not None:
        return mapped
    if low in _GENERIC_TOPICS:
        return ""
    return low


def _compact_sentence(text: str, *, max_chars: int = 120) -> str:
    src = re.sub(r"\s+", " ", str(text or "").strip())
    if not src:
        return ""
    src = src.strip(" .,:;!?")
    if len(src) <= max_chars:
        return src
    cut = src[:max_chars].rsplit(" ", 1)[0].strip()
    return (cut or src[:max_chars]).strip()


def _build_micro_context(question: str, description: str) -> str:
    q = _compact_sentence(question, max_chars=96)
    d = _compact_sentence(description, max_chars=132)
    parts: list[str] = []
    if q:
        parts.append(f"Запрос: {q}.")
    if d:
        parts.append(f"Итог тогда: {d}.")
    return " ".join(parts).strip()[:260]


def _build_cards_brief(cards: list[Any], *, max_cards: int = 3) -> str:
    items: list[str] = []
    for row in list(cards or []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("card_name") or "").strip()
        if not name:
            continue
        orient = "перев." if bool(row.get("is_reversed")) else "прямая"
        items.append(f"{name} ({orient})")
        if len(items) >= max_cards:
            break
    return ", ".join(items)


def _capsule_from_labels(labels: Sequence[str]) -> str:
    # 1) Сначала пробуем выдать "смысловой" капсульный ярлык (экзамен/отношения/финансы...),
    # чтобы не застревать на глаголах вроде "сдам".
    for label in labels:
        clean = str(label or "").strip().lower()
        if not clean:
            continue
        mapped = _CAPSULE_LABEL_MAP.get(clean)
        if mapped:
            return mapped

    # 2) Если ярлык не найден — fallback на короткий нормализованный текст.
    for label in labels:
        clean = str(label or "").strip().lower()
        if not clean:
            continue
        normalized = _normalize_theme_capsule(clean)
        if normalized:
            return normalized
    return ""


def build_event_capsule(
    *,
    topic: str,
    spread_type: str,
    question: str,
    description: str,
    cards: list[dict] | list,
) -> str:
    """
    Короткая тема расклада (до 4 слов), без «другое».
    Используется в истории и в контексте следующих раскладов.
    """
    q = str(question or "").strip()
    signals = _question_signals_from_text(q)
    signal_labels = [_signal_label(s) for s in signals]
    capsule = _capsule_from_labels(signal_labels)
    if capsule:
        return _normalize_theme_capsule(capsule)

    q_capsule = _normalize_theme_capsule(q)
    if q_capsule:
        return q_capsule

    topic_label = _topic_label_for_capsule(topic)
    if topic_label:
        mapped = _CAPSULE_LABEL_MAP.get(topic_label)
        if mapped:
            return mapped
        topic_capsule = _normalize_theme_capsule(topic_label)
        if topic_capsule:
            return topic_capsule

    desc_capsule = _normalize_theme_capsule(description)
    if desc_capsule:
        return desc_capsule

    first = cards[0] if cards and isinstance(cards, list) else {}
    card_name = str((first or {}).get("card_name") or "").strip().lower()
    if card_name:
        card_capsule = _normalize_theme_capsule(card_name)
        if card_capsule:
            return card_capsule

    return ""


def is_resolvable_followup_context(text: str) -> bool:
    src = str(text or "").strip().lower().replace("ё", "е")
    if not src:
        return True
    return not any(token in src for token in _NON_RESOLVABLE_HINT_TOKENS)


def _question_signals_from_text(question: str) -> list[str]:
    raw_tokens = re.findall(r"[a-zа-я0-9]{3,}", str(question or ""), flags=re.IGNORECASE)
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in raw_tokens:
        token = _normalize_question_signal(raw)
        if not token or token in seen:
            continue
        seen.add(token)
        normalized.append(token)
    return normalized


def _build_home_hint(
    *,
    recurring_question_signals: list[dict],
    recurring_topics: list[dict],
    repeated_cards: list[dict],
) -> str:
    # На главной не показываем авто-подсказки "по профилю", чтобы исключить
    # нерелевантные формулировки (например, "отношения" при вопросе про экзамен).
    # Контекст подмешивается только в итоговую интерпретацию расклада.
    _ = (recurring_question_signals, recurring_topics, repeated_cards)
    return ""


def _build_cycle_hints(*, topic_counts: Counter, card_counts: Counter) -> list[str]:
    # Поле оставлено для обратной совместимости DTO, но текстовые подсказки
    # из profile-summary больше не используем.
    _ = (topic_counts, card_counts)
    return []


def _compact_topic_label(topic: str) -> str:
    t = str(topic or "").strip().lower()
    mapping = {
        "relations": "отношения",
        "career": "карьера",
        "finance": "финансы",
        "other": "",
    }
    if t in _GENERIC_TOPICS:
        return ""
    return mapping.get(t, t)


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
    capsule = build_event_capsule(
        topic=str(payload.get("topic") or topic or "other"),
        spread_type=str(payload.get("spread_type") or spread_type or ""),
        question=str(payload.get("question") or question or ""),
        description=str(payload.get("description_excerpt") or description or ""),
        cards=list(payload.get("cards") or cards or []),
    )
    capsule_tokens = _question_signals_from_text(capsule)
    description_excerpt = str(payload.get("description_excerpt") or "")
    micro_context = _build_micro_context(
        str(payload.get("question") or question or ""),
        description_excerpt,
    )
    semantic_sig = _semantic_signature(
        question=str(payload.get("question") or question or ""),
        topic=str(payload.get("topic") or topic or ""),
    )
    cards_brief = _build_cards_brief(list(payload.get("cards") or cards or []))

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
            "description_excerpt": description_excerpt,
            "micro_context": micro_context,
            "cards_brief": cards_brief,
            "capsule": capsule,
            "capsule_tokens": capsule_tokens[:8],
            "semantic_tokens": sorted(list(semantic_sig.get("tokens") or []))[:16],
            "semantic_concepts": sorted(list(semantic_sig.get("concepts") or []))[:8],
            "question_brief": _compact_sentence(str(payload.get("question") or question or ""), max_chars=110),
            "outcome_brief": _compact_sentence(description_excerpt, max_chars=140),
        },
    )


async def ingest_event_if_missing(
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
) -> bool:
    if source_id is not None:
        exists = await repository.has_source_event(
            db,
            user_id=int(user_id),
            source_kind=str(source_kind),
            source_id=int(source_id),
        )
        if exists:
            return False
    await ingest_event(
        db,
        user_id=int(user_id),
        source_kind=source_kind,
        source_id=source_id,
        topic=topic,
        spread_type=spread_type,
        question=question,
        cards=cards,
        description=description,
    )
    return True


async def rebuild_profile(db: AsyncSession, *, user_id: int, days: int = 90) -> Dict[str, Any]:
    events = await repository.list_recent_events(db, user_id=int(user_id), days=days, limit=500)

    topic_counts: Counter = Counter()
    topic_last_seen: dict[str, datetime] = {}
    card_counts: Counter = Counter()
    card_last_seen: dict[str, datetime] = {}
    capsule_counts: Counter = Counter()
    capsule_last_seen: dict[str, datetime] = {}
    signal_counts: Counter = Counter()
    signal_last_seen: dict[str, datetime] = {}

    for ev in events:
        topic = _compact_topic_label(ev.topic)
        if topic:
            topic_counts[topic] += 1
            topic_last_seen[topic] = max(topic_last_seen.get(topic, ev.event_at), ev.event_at)

        card = str(ev.primary_card or "").strip()
        if card:
            card_counts[card] += 1
            card_last_seen[card] = max(card_last_seen.get(card, ev.event_at), ev.event_at)

        ev_summary = ev.summary if isinstance(ev.summary, dict) else {}
        capsule = str(ev_summary.get("capsule") or "").strip().lower()
        if not capsule:
            capsule = build_event_capsule(
                topic=str(ev.topic or ""),
                spread_type=str(ev.spread_type or ""),
                question=str(ev.question or ""),
                description=str(ev_summary.get("description_excerpt") or ""),
                cards=list(ev.cards or []),
            ).lower()
        capsule = _normalize_theme_capsule(capsule)
        if capsule and not _is_generic_topic_label(capsule):
            capsule_counts[capsule] += 1
            capsule_last_seen[capsule] = max(capsule_last_seen.get(capsule, ev.event_at), ev.event_at)

        raw_tokens = list(ev_summary.get("question_tokens") or [])
        if not raw_tokens:
            raw_tokens = re.findall(r"[a-zа-я0-9]{3,}", str(ev.question or ""), flags=re.IGNORECASE)
        seen: set[str] = set()
        for raw in raw_tokens:
            token = _normalize_question_signal(raw)
            if not token or token in seen:
                continue
            seen.add(token)
            signal_counts[token] += 1
            signal_last_seen[token] = max(signal_last_seen.get(token, ev.event_at), ev.event_at)

    recurring_topics = [
        {
            "topic": topic,
            "count": int(count),
            "last_seen": topic_last_seen.get(topic).isoformat() if topic_last_seen.get(topic) else None,
        }
        for topic, count in topic_counts.most_common(5)
        if count >= 2 and not _is_generic_topic_label(topic)
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

    recurring_question_signals = [
        {
            "signal": signal,
            "label": _signal_label(signal),
            "count": int(count),
            "last_seen": signal_last_seen.get(signal).isoformat() if signal_last_seen.get(signal) else None,
        }
        for signal, count in signal_counts.most_common(6)
        if count >= 2
    ]

    recurring_capsules = [
        {
            "capsule": capsule,
            "count": int(count),
            "last_seen": capsule_last_seen.get(capsule).isoformat() if capsule_last_seen.get(capsule) else None,
        }
        for capsule, count in capsule_counts.most_common(8)
        if count >= 2 and capsule and not _is_generic_topic_label(capsule)
    ]

    cycle_hints = _build_cycle_hints(topic_counts=topic_counts, card_counts=card_counts)
    home_hint = _build_home_hint(
        recurring_question_signals=recurring_question_signals,
        recurring_topics=recurring_topics,
        repeated_cards=repeated_cards,
    )

    last_changes = {
        "updated_at": _now_utc().isoformat(),
        "events_considered": len(events),
        "top_topic": recurring_topics[0]["topic"] if recurring_topics else None,
        "top_card": repeated_cards[0]["card"] if repeated_cards else None,
        "top_capsule": recurring_capsules[0]["capsule"] if recurring_capsules else None,
        "home_hint": home_hint,
        "recurring_question_signals": recurring_question_signals[:5],
        "recurring_capsules": recurring_capsules[:6],
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
        "recurring_question_signals": recurring_question_signals,
        "recurring_capsules": recurring_capsules,
        "home_hint": home_hint,
        "last_changes": last_changes,
    }


async def get_summary(db: AsyncSession, *, user_id: int) -> Dict[str, Any]:
    profile = await repository.get_memory_profile(db, user_id=int(user_id))
    if not profile:
        return {
            "recurring_topics": [],
            "repeated_cards": [],
            "cycle_hints": [],
            "recurring_question_signals": [],
            "recurring_capsules": [],
            "home_hint": "",
            "last_changes": {
                "updated_at": None,
                "events_considered": 0,
                "home_hint": "",
                "recurring_question_signals": [],
                "recurring_capsules": [],
            },
        }
    last_changes = dict(profile.last_changes or {})
    return {
        "recurring_topics": list(profile.recurring_topics or []),
        "repeated_cards": list(profile.repeated_cards or []),
        "cycle_hints": list(profile.cycle_hints or []),
        "recurring_question_signals": list(last_changes.get("recurring_question_signals") or []),
        "recurring_capsules": list(last_changes.get("recurring_capsules") or []),
        "home_hint": str(last_changes.get("home_hint") or "").strip(),
        "last_changes": last_changes,
    }


def _event_capsule_from_row(ev: Any) -> str:
    ev_summary = ev.summary if isinstance(ev.summary, dict) else {}
    capsule = _normalize_theme_capsule(str(ev_summary.get("capsule") or ""))
    if capsule:
        return capsule
    return _normalize_theme_capsule(
        build_event_capsule(
            topic=str(ev.topic or ""),
            spread_type=str(ev.spread_type or ""),
            question=str(ev.question or ""),
            description=str(ev_summary.get("description_excerpt") or ""),
            cards=list(ev.cards or []),
        )
    )


def _event_excerpt_from_row(ev: Any) -> str:
    ev_summary = ev.summary if isinstance(ev.summary, dict) else {}
    excerpt = str(ev_summary.get("micro_context") or "").strip()
    cards_brief = str(ev_summary.get("cards_brief") or "").strip()
    if excerpt:
        out = re.sub(r"\s+", " ", excerpt).strip()
        if cards_brief:
            out = f"{out} Карты тогда: {cards_brief}."
        return out[:320]

    excerpt = str(ev_summary.get("description_excerpt") or "").strip()
    if not excerpt:
        excerpt = str(ev.question or "").strip()
    out = re.sub(r"\s+", " ", excerpt).strip()
    if cards_brief:
        out = f"{out} Карты тогда: {cards_brief}."
    return out[:320]


def _event_semantic_signature(ev: Any) -> dict[str, set[str]]:
    ev_summary = ev.summary if isinstance(ev.summary, dict) else {}

    summary_tokens = {
        _stem_ru_token(str(t or ""))
        for t in list(ev_summary.get("semantic_tokens") or [])
        if str(t or "").strip()
    }
    summary_tokens = {t for t in summary_tokens if t and len(t) >= 3 and t not in _SEMANTIC_STOP_WORDS}

    summary_concepts = {
        str(c or "").strip().lower()
        for c in list(ev_summary.get("semantic_concepts") or [])
        if str(c or "").strip()
    }
    summary_concepts = {c for c in summary_concepts if c and not _is_generic_topic_label(c)}

    if summary_tokens and summary_concepts:
        return {"tokens": summary_tokens, "concepts": summary_concepts}

    fallback_question = str(getattr(ev, "question", "") or "")
    fallback_topic = str(getattr(ev, "topic", "") or "")
    capsule = str(ev_summary.get("capsule") or "").strip()
    combined_text = f"{fallback_question} {capsule}".strip()
    sig = _semantic_signature(question=combined_text, topic=fallback_topic)

    if summary_tokens:
        sig["tokens"] |= summary_tokens
    if summary_concepts:
        sig["concepts"] |= summary_concepts
    return sig


def _semantic_label(concepts: Sequence[str]) -> str:
    ordered = [str(c or "").strip().lower() for c in concepts if str(c or "").strip()]
    for c in ordered:
        mapped = _SEMANTIC_LABELS.get(c)
        if mapped:
            return mapped
    for c in ordered:
        if c and not _is_generic_topic_label(c):
            return c
    return ""


def _semantic_overlap_score(*, current_sig: dict[str, set[str]], event_sig: dict[str, set[str]]) -> tuple[int, set[str], set[str]]:
    current_concepts = set(current_sig.get("concepts") or set())
    event_concepts = set(event_sig.get("concepts") or set())
    current_tokens = set(current_sig.get("tokens") or set())
    event_tokens = set(event_sig.get("tokens") or set())

    matched_concepts = current_concepts & event_concepts
    matched_tokens = current_tokens & event_tokens

    score = 0
    if matched_concepts:
        score += len(matched_concepts) * 220
    if matched_tokens:
        score += len(matched_tokens) * 55

    # Bonus for dense token overlap (helps "переезд в другую страну" ~ "переезд в Португалию")
    if current_tokens and event_tokens:
        overlap_ratio = len(matched_tokens) / max(1, min(len(current_tokens), len(event_tokens)))
        if overlap_ratio >= 0.5:
            score += 40
        elif overlap_ratio >= 0.34:
            score += 20

    return score, matched_concepts, matched_tokens


def _extract_card_state(cards: list[Any]) -> Dict[str, bool]:
    state: Dict[str, bool] = {}
    for row in list(cards or []):
        if not isinstance(row, dict):
            continue
        name = str(row.get("card_name") or "").strip().lower()
        if not name:
            continue
        state[name] = bool(row.get("is_reversed"))
    return state


def _orientation_label(is_reversed: bool) -> str:
    return "перевёрнутая" if bool(is_reversed) else "прямая"


def _display_card_name(name: str) -> str:
    src = str(name or "").strip()
    if not src:
        return ""
    return src[0].upper() + src[1:] if src else ""


def _card_suit_from_name(name: str) -> str:
    low = str(name or "").strip().lower()
    for needle, suit in _CARD_SUIT_RULES:
        if needle in low:
            return suit
    return ""


def _suit_counter(cards: list[Any]) -> Counter:
    out: Counter = Counter()
    for row in list(cards or []):
        if not isinstance(row, dict):
            continue
        suit = _card_suit_from_name(str(row.get("card_name") or ""))
        if suit:
            out[suit] += 1
    return out


def _dominant_suit(counter: Counter) -> str:
    if not counter:
        return ""
    top = counter.most_common(2)
    if not top:
        return ""
    if len(top) == 1:
        return str(top[0][0] or "")
    if int(top[0][1]) == int(top[1][1]):
        # при равенстве не притягиваем "доминирующую" масть
        return ""
    return str(top[0][0] or "")


def _motif_scores(cards: list[Any]) -> Counter:
    scores: Counter = Counter()
    for row in list(cards or []):
        if not isinstance(row, dict):
            continue
        text = " ".join(
            [
                str(row.get("title") or ""),
                str(row.get("card_name") or ""),
                str(row.get("meaning") or ""),
            ]
        ).lower()
        if not text:
            continue
        for motif, needles in _MOTIF_KEYWORDS.items():
            if any(n in text for n in needles):
                scores[motif] += 1
    return scores


def _dominant_motif(scores: Counter) -> str:
    if not scores:
        return ""
    top = scores.most_common(2)
    if not top:
        return ""
    if len(top) == 1:
        return str(top[0][0] or "")
    if int(top[0][1]) == int(top[1][1]):
        return ""
    return str(top[0][0] or "")


def _history_card_stats(events: Sequence[Any]) -> tuple[Counter, dict[str, datetime], dict[str, str]]:
    counts: Counter = Counter()
    last_seen: dict[str, datetime] = {}
    display_names: dict[str, str] = {}
    for ev in events:
        event_at = getattr(ev, "event_at", None)
        for row in list(getattr(ev, "cards", []) or []):
            if not isinstance(row, dict):
                continue
            raw = str(row.get("card_name") or "").strip()
            key = raw.lower()
            if not key:
                continue
            counts[key] += 1
            display_names[key] = display_names.get(key) or _display_card_name(raw)
            if isinstance(event_at, datetime):
                prev = last_seen.get(key)
                if not prev or event_at > prev:
                    last_seen[key] = event_at
    return counts, last_seen, display_names


def _build_card_relationship_lines(
    *,
    current_cards: list[Any],
    history_counts: Counter,
    history_last_seen: dict[str, datetime],
    history_display_names: dict[str, str],
) -> list[str]:
    lines: list[str] = []
    now = _now_utc()
    current_keys: list[str] = []
    seen: set[str] = set()
    for row in list(current_cards or []):
        if not isinstance(row, dict):
            continue
        raw = str(row.get("card_name") or "").strip()
        key = raw.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        current_keys.append(key)
        count = int(history_counts.get(key, 0))
        if count >= 2:
            display = history_display_names.get(key) or _display_card_name(raw)
            lines.append(f"Карта «{display}» уже появлялась у вас {count} раз раньше.")

    top = history_counts.most_common(3)
    for key, count in top:
        if key in seen or int(count) < 3:
            continue
        last_at = history_last_seen.get(key)
        if not isinstance(last_at, datetime):
            continue
        days = max(1, int((now - last_at).days))
        if days >= 14:
            display = history_display_names.get(key) or _display_card_name(key)
            lines.append(f"Карта «{display}» не появлялась около {days} дней и сейчас это заметный сдвиг.")
            break

    return lines[:3]


def _cards_similarity_snapshot(*, current_cards: list[Any], previous_cards: list[Any]) -> dict[str, Any]:
    curr = _extract_card_state(current_cards)
    prev = _extract_card_state(previous_cards)
    out: dict[str, Any] = {
        "same_cards": [],
        "orientation_changed": [],
        "common_suit": "",
        "common_motif": "",
        "previous_dominant_suit": "",
        "current_dominant_suit": "",
        "previous_dominant_motif": "",
        "current_dominant_motif": "",
    }
    if not curr or not prev:
        return out

    overlap = sorted(set(curr.keys()) & set(prev.keys()))
    if overlap:
        for name in overlap:
            if curr[name] != prev[name]:
                out["orientation_changed"].append(name)
            else:
                out["same_cards"].append(name)

    curr_suits = _suit_counter(current_cards)
    prev_suits = _suit_counter(previous_cards)
    out["current_dominant_suit"] = _dominant_suit(curr_suits)
    out["previous_dominant_suit"] = _dominant_suit(prev_suits)
    common_suits = sorted(
        (set(curr_suits.keys()) & set(prev_suits.keys())),
        key=lambda s: curr_suits[s] + prev_suits[s],
        reverse=True,
    )
    if common_suits and (curr_suits[common_suits[0]] + prev_suits[common_suits[0]] >= 3):
        out["common_suit"] = common_suits[0]

    curr_motifs = _motif_scores(current_cards)
    prev_motifs = _motif_scores(previous_cards)
    out["current_dominant_motif"] = _dominant_motif(curr_motifs)
    out["previous_dominant_motif"] = _dominant_motif(prev_motifs)
    common_motifs = sorted(
        (set(curr_motifs.keys()) & set(prev_motifs.keys())),
        key=lambda m: curr_motifs[m] + prev_motifs[m],
        reverse=True,
    )
    if common_motifs:
        out["common_motif"] = common_motifs[0]

    return out


def _build_cards_delta_hint(*, current_cards: list[Any], previous_cards: list[Any]) -> str:
    curr = _extract_card_state(current_cards)
    prev = _extract_card_state(previous_cards)
    if not curr or not prev:
        return ""

    snapshot = _cards_similarity_snapshot(current_cards=current_cards, previous_cards=previous_cards)
    overlap = sorted(set(curr.keys()) & set(prev.keys()))
    if overlap:
        changed_orientation: list[str] = list(snapshot.get("orientation_changed") or [])
        stable_overlap: list[str] = list(snapshot.get("same_cards") or [])

        if changed_orientation:
            picked = changed_orientation[:2]
            chunks: list[str] = []
            for name in picked:
                prev_orient = _orientation_label(prev[name])
                curr_orient = _orientation_label(curr[name])
                chunks.append(f"«{_display_card_name(name)}»: было {prev_orient}, сейчас {curr_orient}")
            cards_str = "; ".join(chunks)
            return (
                "Сравнение по картам: уже встречалась та же карта, но ориентация изменилась "
                f"({cards_str})."
            )
        if stable_overlap:
            cards_str = ", ".join(f"«{_display_card_name(n)}»" for n in stable_overlap[:2])
            return (
                f"Сравнение по картам: снова выходит та же карта ({cards_str})."
            )

    suit = str(snapshot.get("common_suit") or "")
    if suit:
        return (
            f"Сравнение по картам: в обоих раскладах повторяется акцент на масти «{suit}»."
        )

    motif = str(snapshot.get("common_motif") or "")
    if motif:
        return f"Сравнение по картам: повторяется смысловая линия «{motif}»."

    # Если схожесть не найдена, не подталкиваем модель к искусственным выводам.
    return ""


def _build_then_vs_now_lines(
    *,
    current_question: str,
    current_cards: list[Any],
    previous_event: Any,
    matched_concepts: Sequence[str],
    matched_tokens: Sequence[str],
) -> list[str]:
    previous_cards = list(getattr(previous_event, "cards", []) or [])
    snapshot = _cards_similarity_snapshot(current_cards=current_cards, previous_cards=previous_cards)
    same_cards = list(snapshot.get("same_cards") or [])
    changed_orientation = list(snapshot.get("orientation_changed") or [])
    common_suit = str(snapshot.get("common_suit") or "")
    common_motif = str(snapshot.get("common_motif") or "")
    previous_dominant_suit = str(snapshot.get("previous_dominant_suit") or "")
    current_dominant_suit = str(snapshot.get("current_dominant_suit") or "")
    previous_dominant_motif = str(snapshot.get("previous_dominant_motif") or "")
    current_dominant_motif = str(snapshot.get("current_dominant_motif") or "")

    previous_state = _extract_card_state(previous_cards)
    current_state = _extract_card_state(current_cards)

    same_parts: list[str] = []
    current_concepts = _semantic_concepts_from_text(question=current_question, topic="")
    concept_candidates = sorted(set(matched_concepts) | set(current_concepts))
    concept_label = _semantic_label(concept_candidates)
    concept_key = next((c for c in concept_candidates if c in _SEMANTIC_LABELS), "")
    if not concept_key and concept_label:
        for k, v in _SEMANTIC_LABELS.items():
            if v == concept_label:
                concept_key = k
                break

    same_cards_display = [_display_card_name(name) for name in same_cards[:2] if str(name or "").strip()]
    if concept_label:
        same_parts.append(f"тема «{concept_label}» снова в фокусе")
    if same_cards_display:
        if len(same_cards_display) == 1:
            same_parts.append(f"повторилась карта «{same_cards_display[0]}»")
        else:
            same_parts.append(
                "снова вышли карты " + ", ".join(f"«{name}»" for name in same_cards_display[:2])
            )
    elif common_suit:
        same_parts.append(f"сохраняется акцент масти «{common_suit}»")
    elif common_motif:
        same_parts.append(f"повторяется мотив «{common_motif}»")
    elif matched_tokens:
        same_parts.append("ядро вопроса осталось прежним")

    changed_parts: list[str] = []
    if changed_orientation:
        changed_name = str(changed_orientation[0] or "").strip().lower()
        changed_display = _display_card_name(changed_name)
        prev_orient = _orientation_label(previous_state.get(changed_name, False))
        curr_orient = _orientation_label(current_state.get(changed_name, False))
        changed_parts.append(f"«{changed_display}» сменила вектор: было {prev_orient}, сейчас {curr_orient}")
    if (
        previous_dominant_suit
        and current_dominant_suit
        and previous_dominant_suit != current_dominant_suit
    ):
        changed_parts.append(f"тон мастей сместился: было «{previous_dominant_suit}», сейчас «{current_dominant_suit}»")
    elif not same_cards and common_suit:
        changed_parts.append(f"масть «{common_suit}» осталась, но в других позициях")
    elif not same_cards and not common_suit:
        changed_parts.append("набор карт и акценты стали другими")

    if (
        previous_dominant_motif
        and current_dominant_motif
        and previous_dominant_motif != current_dominant_motif
    ):
        changed_parts.append(
            f"эмоциональный тон сдвинулся: было «{previous_dominant_motif}», сейчас «{current_dominant_motif}»"
        )
    elif common_motif and not changed_orientation:
        changed_parts.append(f"мотив «{common_motif}» проявлен иначе")

    same_line = _compact_sentence(
        "Совпало: " + (", ".join(same_parts) if same_parts else "похожий эмоциональный запрос."),
        max_chars=190,
    )
    changed_line = _compact_sentence(
        "Изменилось: " + (", ".join(changed_parts) if changed_parts else "динамика мягко сдвинулась."),
        max_chars=190,
    )

    question_has_resolution = bool(str(current_question or "").strip()) and is_resolvable_followup_context(
        current_question
    )
    concept_actions = {
        "переезд": "соберите сегодня один проверяемый шаг: документы, бюджет или дедлайн переезда.",
        "экзамен": "выберите один блок подготовки и закройте его до конца дня с проверкой результата.",
        "отношения": "сделайте один честный разговор по фактам, без чтения мыслей за другого.",
        "карьера": "зафиксируйте конкретный шаг по работе и метрику, по которой поймёте прогресс.",
        "финансы": "примите одно финансовое решение по цифрам, а не по тревоге.",
        "здоровье": "мягко держите режим и по медицинским вопросам опирайтесь на консультацию специалиста.",
    }
    suit_actions = {
        "мечи": "ясности формулировок и проверки фактов.",
        "жезлы": "фокуса на одном действии и доведения его до результата.",
        "кубки": "экологичного разговора про чувства и ожидания.",
        "пентакли": "плана, структуры и шага с измеримым результатом.",
    }

    repeated_card = same_cards_display[0] if same_cards_display else ""
    if repeated_card and not changed_orientation:
        practical = (
            f"Практический вывод: карта «{repeated_card}» снова с вами — без нового шага сценарий повторится. "
            "Сделайте сегодня одно действие, которое в прошлый раз откладывали."
        )
    elif changed_orientation:
        changed_display = _display_card_name(str(changed_orientation[0] or ""))
        practical = (
            f"Практический вывод: по карте «{changed_display}» уже виден разворот. "
            "Закрепите его одним конкретным шагом в ближайшие 24 часа."
        )
    elif (
        previous_dominant_suit
        and current_dominant_suit
        and previous_dominant_suit != current_dominant_suit
        and current_dominant_suit in suit_actions
    ):
        practical = (
            f"Практический вывод: сейчас ведущая масть «{current_dominant_suit}» — "
            f"время {suit_actions[current_dominant_suit]}"
        )
    elif common_motif == "контроль и давление":
        practical = (
            "Практический вывод: вместо усиления контроля выберите один проверяемый шаг и срок; "
            "так напряжение снизится, а ясности станет больше."
        )
    elif common_motif == "внутреннее противоречие":
        practical = (
            "Практический вывод: сузьте выбор до двух вариантов и заранее задайте критерий решения, "
            "чтобы не застревать в сомнениях."
        )
    elif common_suit == "мечи":
        practical = (
            "Практический вывод: опирайтесь на факты и формулировки. Один честный разговор или чёткий вопрос даст разворот."
        )
    elif common_suit == "жезлы":
        practical = (
            "Практический вывод: энергия есть, но нужен фокус. Выберите один приоритет и доведите его до результата."
        )
    elif common_suit == "кубки":
        practical = (
            "Практический вывод: сначала проясните эмоции и ожидания, затем принимайте решение — это уберёт лишний шум."
        )
    elif common_suit == "пентакли":
        practical = (
            "Практический вывод: переведите идею в план шагов и ресурсов; практичность сейчас важнее импульса."
        )
    elif concept_key in concept_actions:
        practical = f"Практический вывод: {concept_actions[concept_key]}"
    elif concept_label:
        practical = f"Практический вывод: в теме «{concept_label}» зафиксируйте один шаг на сегодня и факт результата."
    elif question_has_resolution:
        practical = (
            "Практический вывод: зафиксируйте один критерий решения и проверьте его на реальном действии, "
            "а не только в размышлениях."
        )
    else:
        practical = (
            "Практический вывод: выберите один измеримый шаг на сегодня и вечером сверите фактический результат."
        )

    return [
        _compact_sentence(same_line, max_chars=200),
        _compact_sentence(changed_line, max_chars=200),
        _compact_sentence(practical, max_chars=210),
    ]


async def build_runtime_prompt_context(
    db: AsyncSession,
    *,
    user_id: int,
    question: str,
    current_cards: list[Any] | None = None,
    days: int = 90,
    limit: int = 240,
) -> str:
    """
    Runtime-контекст "память v2":
    - выбирает наиболее релевантный прошлый расклад по смыслу (semantic concepts + stems),
      а не по буквальному совпадению слов;
    - добавляет короткое сравнение "тогда vs сейчас" (2-3 строки);
    - не возвращает шум, если совпадение слабое.
    """
    q_text = str(question or "").strip()
    current_sig = _semantic_signature(question=q_text, topic="")
    current_state = _extract_card_state(list(current_cards or []))
    current_card_names = set(current_state.keys())
    if not q_text and not current_card_names:
        return ""

    events = await repository.list_recent_events(
        db,
        user_id=int(user_id),
        days=max(1, int(days)),
        limit=max(20, min(int(limit), 500)),
    )
    if not events:
        return ""

    best_event = None
    best_score = 0
    best_matched_concepts: set[str] = set()
    best_matched_tokens: set[str] = set()

    for idx, ev in enumerate(events):
        event_sig = _event_semantic_signature(ev)
        semantic_score, matched_concepts, matched_tokens = _semantic_overlap_score(
            current_sig=current_sig,
            event_sig=event_sig,
        )

        # Для осмысленного вопроса не притягиваем нерелевантные ивенты только по карте.
        if q_text and semantic_score <= 0:
            continue

        freshness_boost = max(0, 24 - idx // 5)
        score = semantic_score + freshness_boost
        ev_cards_state = _extract_card_state(list(getattr(ev, "cards", []) or []))
        if current_card_names and ev_cards_state:
            overlap_cards = current_card_names & set(ev_cards_state.keys())
            if overlap_cards:
                score += len(overlap_cards) * 140
                orientation_changes = sum(
                    1 for name in overlap_cards if current_state.get(name) != ev_cards_state.get(name)
                )
                score += orientation_changes * 30

        if str(ev.source_kind or "") in {"reading", "photo_analysis"}:
            score += 10

        # Против "слабых" ложных срабатываний: нужен либо semantic матч,
        # либо сильный матч по картам (>=2 карты).
        if q_text:
            if semantic_score < 55 and len(current_card_names & set(ev_cards_state.keys())) < 2:
                continue

        if score > best_score:
            best_score = score
            best_event = ev
            best_matched_concepts = set(matched_concepts)
            best_matched_tokens = set(matched_tokens)

    if not best_event:
        return ""

    history_counts, history_last_seen, history_display_names = _history_card_stats(events)

    parts: list[str] = []

    prev_question = _compact_sentence(str(getattr(best_event, "question", "") or ""), max_chars=110)
    if prev_question:
        parts.append(f"Прошлый похожий вопрос: {prev_question}.")

    excerpt = _event_excerpt_from_row(best_event)
    if excerpt:
        parts.append(f"Похожий прошлый расклад (кратко): {excerpt}")

    cards_delta = _build_cards_delta_hint(
        current_cards=list(current_cards or []),
        previous_cards=list(getattr(best_event, "cards", []) or []),
    )
    if cards_delta:
        parts.append(f"Сравнение карт: {cards_delta}")

    card_relationship_lines = _build_card_relationship_lines(
        current_cards=list(current_cards or []),
        history_counts=history_counts,
        history_last_seen=history_last_seen,
        history_display_names=history_display_names,
    )
    if card_relationship_lines:
        parts.append("Карточная динамика по вашей истории:")
        parts.extend([f"- {line}" for line in card_relationship_lines if str(line or "").strip()])

    then_vs_now = _build_then_vs_now_lines(
        current_question=q_text,
        current_cards=list(current_cards or []),
        previous_event=best_event,
        matched_concepts=sorted(list(best_matched_concepts)),
        matched_tokens=sorted(list(best_matched_tokens)),
    )
    if then_vs_now:
        parts.append("Сравнение (тогда vs сейчас):")
        parts.extend([f"- {line}" for line in then_vs_now if str(line or "").strip()])

    if not parts:
        return ""

    return (
        "Контекст из истории (используй только как мягкий фон, без категоричности и без домыслов):\n"
        + "\n".join(parts)
    )


def build_inline_hint(summary: Dict[str, Any]) -> str:
    # На главной намеренно не показываем авто-подсказки.
    # Контекст подмешивается только в интерпретацию конкретного расклада.
    _ = summary
    return ""


def build_inline_hint_for_question(summary: Dict[str, Any], question: str) -> str:
    # На главной вопросные подсказки не показываем.
    _ = (summary, question)
    return ""


def classify_safety_risk(question: str) -> str:
    q = str(question or "").lower()
    if any(x in q for x in ["суиц", "самоубий", "не хочу жить", "умереть", "смерт", "умер", "похорон"]):
        return "crisis"
    if any(x in q for x in ["болит", "болез", "диагноз", "симптом", "лекар"]):
        return "medical"
    return "none"


def infer_reversed_from_text(text: str) -> bool:
    src = str(text or "").lower()
    return "перевер" in src or "reversed" in src


def build_card_of_day_cards_payload(
    *,
    card_index: int,
    card_name: str,
    is_reversed: bool,
) -> list[dict]:
    return [
        {
            "position": "card_day",
            "title": "Карта дня",
            "card_index": int(card_index),
            "card_name": str(card_name or "").strip(),
            "is_reversed": bool(is_reversed),
            "meaning": "",
        }
    ]


async def backfill_user_history(
    db: AsyncSession,
    *,
    user_id: int,
    max_readings: int = 1000,
    max_card_days: int = 180,
) -> Dict[str, int]:
    """
    Backfill memory from existing readings/card-of-day rows.
    Idempotent by (user_id, source_kind, source_id).
    """
    stats = {
        "readings_scanned": 0,
        "readings_inserted": 0,
        "card_days_scanned": 0,
        "card_days_inserted": 0,
    }

    readings_q = await db.execute(
        select(Reading)
        .where(Reading.user_id == int(user_id))
        .order_by(desc(Reading.created_at), desc(Reading.id))
        .limit(max(1, min(int(max_readings), 5000)))
    )
    readings = list(readings_q.scalars().all())
    stats["readings_scanned"] = len(readings)
    plain_ids = [int(r.id) for r in readings if getattr(r, "id", None) is not None and str(r.spread_type or "") != "photo_analysis"]
    photo_ids = [int(r.id) for r in readings if getattr(r, "id", None) is not None and str(r.spread_type or "") == "photo_analysis"]
    existing_readings = await repository.list_existing_source_ids(
        db,
        user_id=int(user_id),
        source_kind="reading",
        source_ids=plain_ids,
    )
    existing_photo = await repository.list_existing_source_ids(
        db,
        user_id=int(user_id),
        source_kind="photo_analysis",
        source_ids=photo_ids,
    )

    for row in readings:
        row_id = int(row.id)
        source_kind = "photo_analysis" if str(row.spread_type or "") == "photo_analysis" else "reading"
        if (source_kind == "reading" and row_id in existing_readings) or (
            source_kind == "photo_analysis" and row_id in existing_photo
        ):
            continue
        await ingest_event(
            db,
            user_id=int(user_id),
            source_kind=source_kind,
            source_id=row_id,
            topic=str(row.topic or "other"),
            spread_type=str(row.spread_type or ""),
            question=str(row.question or ""),
            cards=list(row.cards or []),
            description=str(row.description or ""),
        )
        stats["readings_inserted"] += 1

    card_q = await db.execute(
        select(CardOfDay)
        .where(CardOfDay.user_id == int(user_id))
        .order_by(desc(CardOfDay.created_at), desc(CardOfDay.id))
        .limit(max(1, min(int(max_card_days), 1000)))
    )
    card_days = list(card_q.scalars().all())
    stats["card_days_scanned"] = len(card_days)
    card_ids = [int(r.id) for r in card_days if getattr(r, "id", None) is not None]
    existing_cards = await repository.list_existing_source_ids(
        db,
        user_id=int(user_id),
        source_kind="card_of_day",
        source_ids=card_ids,
    )

    for row in card_days:
        row_id = int(row.id)
        if row_id in existing_cards:
            continue
        is_reversed = infer_reversed_from_text(str(row.description or ""))
        cards_payload = build_card_of_day_cards_payload(
            card_index=int(row.card_index),
            card_name=str(row.card_name or ""),
            is_reversed=is_reversed,
        )
        await ingest_event(
            db,
            user_id=int(user_id),
            source_kind="card_of_day",
            source_id=row_id,
            topic=str(row.topic or "other"),
            spread_type="card_day",
            question=str(row.question or ""),
            cards=cards_payload,
            description=str(row.description or ""),
        )
        stats["card_days_inserted"] += 1

    return stats
