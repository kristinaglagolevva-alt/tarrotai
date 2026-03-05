from __future__ import annotations

import re
from typing import Any, Dict, List


_STOP_WORDS = {
    "и", "в", "на", "по", "не", "что", "как", "к", "с", "о", "а", "но", "или", "это", "у", "из", "за",
    "the", "and", "for", "with", "about", "from", "that", "this",
}


def _tokenize(text: str) -> List[str]:
    src = str(text or "").lower()
    tokens = re.findall(r"[a-zа-я0-9]{3,}", src, flags=re.IGNORECASE)
    return [t for t in tokens if t not in _STOP_WORDS]


def _pick_tags(question: str, topic: str, spread_type: str) -> List[str]:
    tags: List[str] = []
    q = str(question or "").lower()
    t = str(topic or "").strip().lower()
    s = str(spread_type or "").strip().lower()

    if t:
        tags.append(f"topic:{t}")
    if s:
        tags.append(f"spread:{s}")

    if any(x in q for x in ["отнош", "люб", "партнер", "брак"]):
        tags.append("theme:relationships")
    if any(x in q for x in ["работ", "карьер", "должн", "проект"]):
        tags.append("theme:career")
    if any(x in q for x in ["ден", "финанс", "кредит", "долг", "доход"]):
        tags.append("theme:finance")
    if any(x in q for x in ["здоров", "болит", "болез", "диагноз", "симптом"]):
        tags.append("risk:medical")
    if any(x in q for x in ["суиц", "умереть", "не хочу жить", "самоубий"]):
        tags.append("risk:crisis")

    return list(dict.fromkeys(tags))


def _compact_description(description: str, *, max_sentences: int = 2, max_chars: int = 260) -> str:
    src = re.sub(r"\s+", " ", str(description or "").strip())
    if not src:
        return ""

    parts = [p.strip(" .") for p in re.split(r"(?<=[.!?])\s+", src) if p.strip()]
    selected: List[str] = []
    for part in parts:
        selected.append(part)
        if len(selected) >= max_sentences:
            break
    if not selected:
        selected = [src]

    out = ". ".join(selected).strip()
    if out and not out.endswith((".", "!", "?")):
        out = f"{out}."
    if len(out) <= max_chars:
        return out
    cut = out[:max_chars].rsplit(" ", 1)[0].strip()
    return (cut or out[:max_chars]).strip() + "…"


def build_memory_event_payload(
    *,
    source_kind: str,
    source_id: int | None,
    topic: str,
    spread_type: str,
    question: str,
    cards: List[Dict[str, Any]],
    description: str,
) -> Dict[str, Any]:
    cards_list = list(cards or [])
    primary = cards_list[0] if cards_list else {}
    primary_name = str(primary.get("card_name") or "").strip()
    primary_rev = bool(primary.get("is_reversed"))

    tags = _pick_tags(question, topic, spread_type)
    tokens = _tokenize(question)

    sentiment = "neutral"
    ql = str(question or "").lower()
    if any(x in ql for x in ["страш", "бою", "тревог", "плохо", "кризис", "конфликт"]):
        sentiment = "anxious"
    elif any(x in ql for x in ["рад", "счаст", "успех", "получилось"]):
        sentiment = "positive"

    return {
        "source_kind": str(source_kind or "reading").strip().lower(),
        "source_id": int(source_id) if source_id is not None else None,
        "topic": str(topic or "other").strip().lower() or "other",
        "spread_type": str(spread_type or "").strip().lower(),
        "question": str(question or "").strip(),
        "cards": cards_list,
        "primary_card": primary_name,
        "primary_card_reversed": primary_rev,
        "sentiment_label": sentiment,
        "tags": tags,
        "question_tokens": tokens[:16],
        "description_excerpt": _compact_description(description, max_sentences=2, max_chars=260),
    }
