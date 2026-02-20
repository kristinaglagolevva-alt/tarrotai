# llm_card_of_day.py
from __future__ import annotations

import os
import json
import re
import base64
import logging
from typing import Any, Dict, List, Optional, Tuple

log = logging.getLogger("llm")


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v.strip() if isinstance(v, str) else default


def _extract_text(resp: Any) -> str:
    # Responses API (new)
    try:
        out = ""
        output = getattr(resp, "output", None)
        if output:
            for item in output:
                content = getattr(item, "content", None)
                if content:
                    for c in content:
                        t = getattr(c, "text", None)
                        if isinstance(t, str) and t.strip():
                            out += t
        if out.strip():
            return out.strip()
    except Exception:
        pass

    # Chat Completions (legacy)
    try:
        choices = getattr(resp, "choices", None)
        if choices:
            msg = getattr(choices[0], "message", None)
            if msg:
                t = getattr(msg, "content", None)
                if isinstance(t, str) and t.strip():
                    return t.strip()
    except Exception:
        pass

    return ""


def fallback_text(topic: str, question: str, card: Dict[str, Any], is_reversed: bool) -> str:
    orient = "перевёрнутая" if is_reversed else "прямая"
    meaning = card["reversed_meaning"] if is_reversed else card["upright_meaning"]

    topic_hint = {
        "relations": "Фокус: отношения, чувства, диалог, границы.",
        "career": "Фокус: работа, деньги, цели, дисциплина.",
        "health": "Фокус: самочувствие, ресурс, отдых, забота о себе.",
        "spiritual": "Фокус: смысл, внутренний рост, интуиция.",
        "daily": "Фокус: день в целом, настроение, микрорешения.",
    }.get(topic, "Фокус: ситуация в целом.")

    q_line = f"**Вопрос:** {question}\n" if (question or "").strip() else ""

    return (
        f"**{card['name']}** ({orient}).\n"
        f"{q_line}"
        f"{topic_hint}\n\n"
        f"Смысл карты: {meaning}.\n"
        f"Практический шаг: выбери одно действие на сегодня, которое поддержит тебя в этой теме."
    ).strip()


def fallback_spread_text(topic: str, question: str, spread_type: str, cards: List[Dict[str, Any]]) -> str:
    stitle = {
        "ppf": "Прошлое — Настоящее — Будущее",
        "three_cards": "Расклад по трём картам",
        "decision": "Принятие решения",
        "custom": "Расклад",
        "photo_analysis": "AI анализ фото расклада",
    }.get(spread_type, "Расклад")

    lines: List[str] = [f"## {stitle}"]
    if (question or "").strip():
        lines.append(f"**Вопрос:** {question}\n")

    for c in cards:
        title = str(c.get("title") or c.get("position") or "").strip()
        name = str(c.get("card_name") or "").strip()
        meaning = str(c.get("meaning") or "").strip()
        orient = "перевёрнутая" if c.get("is_reversed") else "прямая"
        if title:
            lines.append(f"**{title} — {name} ({orient})**\n{meaning}")
        else:
            lines.append(f"**{name} ({orient})**\n{meaning}")

    lines.append(
        "\n**Итог:** выбери одну самую важную мысль из расклада и преврати её в маленькое действие на 24 часа."
    )
    if topic:
        lines.append(f"\n_Тема: {topic}_")
    return "\n\n".join(lines).strip()


async def _call_openai(system_prompt: str, user_prompt: str, *, model: str, temperature: float, max_tokens: int) -> str:
    from openai import AsyncOpenAI  # type: ignore

    api_key = _env("OPENAI_API_KEY")
    client = AsyncOpenAI(api_key=api_key)

    # 1) Responses API
    try:
        resp = await client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {"role": "user", "content": [{"type": "input_text", "text": user_prompt}]},
            ],
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        text = _extract_text(resp)
        if text:
            return text
    except Exception:
        pass

    # 2) chat.completions
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return _extract_text(resp)


async def _call_openai_with_image(
    system_prompt: str,
    user_prompt: str,
    *,
    image_bytes: bytes,
    image_mime: str,
    model: str,
    temperature: float,
    max_tokens: int,
) -> str:
    """
    Vision call: send image as data: URL to Responses API (preferred), fallback to chat.completions.
    """
    from openai import AsyncOpenAI  # type: ignore

    api_key = _env("OPENAI_API_KEY")
    client = AsyncOpenAI(api_key=api_key)

    b64 = base64.b64encode(image_bytes).decode("ascii")
    data_url = f"data:{image_mime};base64,{b64}"

    # 1) Responses API with input_image
    try:
        resp = await client.responses.create(
            model=model,
            input=[
                {"role": "system", "content": [{"type": "input_text", "text": system_prompt}]},
                {
                    "role": "user",
                    "content": [
                        {"type": "input_text", "text": user_prompt},
                        {"type": "input_image", "image_url": data_url},
                    ],
                },
            ],
            temperature=temperature,
            max_output_tokens=max_tokens,
        )
        text = _extract_text(resp)
        if text:
            return text
    except Exception:
        pass

    # 2) chat.completions with image_url
    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return _extract_text(resp)


async def generate_card_text_llm(
    topic: str,
    question: str,
    card: Dict[str, Any],
    is_reversed: bool,
    *,
    require_llm: bool = True,
) -> str:
    api_key = _env("OPENAI_API_KEY")
    model = _env("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        if require_llm:
            raise RuntimeError("OPENAI_API_KEY is missing")
        return fallback_text(topic, question, card, is_reversed)

    orient = "перевёрнутая" if is_reversed else "прямая"
    meaning = card["reversed_meaning"] if is_reversed else card["upright_meaning"]

    system_prompt = (
        "Ты — человечный таролог-психолог. Пиши естественно, без канцелярита.\n"
        "Не делай «точных предсказаний», не утверждай факты о людях без оснований.\n"
        "Если вопрос провокационный/токсичный — переформулируй его мягко и дай пользу.\n\n"
        "Стиль:\n"
        "- Русский язык.\n"
        "- Без перечисления голых значений карты.\n"
        "- Без повторов текста из входных данных.\n"
        "- Тон: поддерживающий, спокойный.\n\n"
        "Формат:\n"
        "1) 2–3 предложения — суть в контексте вопроса\n"
        "2) 4–6 буллетов — конкретные шаги\n"
        "3) 1 строка — мягкая подсказка\n"
    )

    user_prompt = (
        f"Тема: {topic}\n"
        f"Вопрос: {question}\n"
        f"Карта: {card['name']} ({orient})\n"
        f"Смысл карты (для тебя как справка): {meaning}\n"
        "Сгенерируй ответ."
    )

    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            text = await _call_openai(
                system_prompt,
                user_prompt,
                model=model,
                temperature=0.85,
                max_tokens=900,
            )
            text = (text or "").strip()
            if text:
                return text
            raise RuntimeError("Empty LLM response")
        except Exception as e:
            last_err = e
            log.warning("card LLM attempt %s failed: %s", attempt + 1, repr(e))

    if require_llm:
        raise RuntimeError(f"LLM failed: {last_err!r}")
    return fallback_text(topic, question, card, is_reversed)


async def generate_spread_text_llm(
    topic: str,
    question: str,
    spread_type: str,
    cards: List[Dict[str, Any]],
    extra_context: str = "",
    *,
    require_llm: bool = True,
) -> str:
    api_key = _env("OPENAI_API_KEY")
    model = _env("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key:
        if require_llm:
            raise RuntimeError("OPENAI_API_KEY is missing")
        return fallback_spread_text(topic, question, spread_type, cards)

    stitle = {
        "ppf": "Прошлое — Настоящее — Будущее",
        "three_cards": "Расклад по трём картам",
        "decision": "Принятие решения",
        "custom": "Расклад",
        "photo_analysis": "AI анализ фото расклада",
    }.get(spread_type, "Расклад")

    card_lines: List[str] = []
    for c in cards:
        title = str(c.get("title") or c.get("position") or "").strip()
        name = str(c.get("card_name") or "").strip()
        meaning = str(c.get("meaning") or "").strip()
        orient = "перевёрнутая" if c.get("is_reversed") else "прямая"
        if title:
            card_lines.append(f"- {title}: {name} ({orient}) — {meaning}")
        else:
            card_lines.append(f"- {name} ({orient}) — {meaning}")

    system_prompt = (
        "Ты — человечный таролог-психолог. Пиши как живой человек, без шаблонов.\n"
        "Не делай «точных предсказаний», не утверждай факты о людях без оснований.\n"
        "НЕЛЬЗЯ: просто переписать значения карт или повторять входной текст.\n"
        "НУЖНО: связать карты в цельный сюжет и дать практические шаги.\n\n"
        "Формат (строго):\n"
        "1) 1 абзац — общий смысл расклада\n"
        "2) По позициям (в том же порядке): подзаголовок + 2–4 предложения на позицию\n"
        "3) 6–8 буллетов — конкретные рекомендации\n"
        "4) 1 строка — мягкий итог/подсказка\n"
    )

    user_prompt = (
        f"Тип расклада: {stitle}\n"
        f"Тема: {topic}\n"
        f"Вопрос: {question}\n"
        f"Карты (справка):\n{chr(10).join(card_lines)}\n"
    )
    if extra_context.strip():
        user_prompt += f"\nКонтекст: {extra_context.strip()}\n"

    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            text = await _call_openai(
                system_prompt,
                user_prompt,
                model=model,
                temperature=0.9,
                max_tokens=1200,
            )
            text = (text or "").strip()
            if text:
                return text
            raise RuntimeError("Empty LLM response")
        except Exception as e:
            last_err = e
            log.warning("spread LLM attempt %s failed: %s", attempt + 1, repr(e))

    if require_llm:
        raise RuntimeError(f"LLM failed: {last_err!r}")
    return fallback_spread_text(topic, question, spread_type, cards)


# ============================== PHOTO ANALYSIS (VISION) ==============================

def _deck_names() -> List[str]:
    # Lazy import to avoid circulars
    from tarot_deck import TAROT_DECK  # type: ignore
    return [str(c.get("name") or "").strip() for c in TAROT_DECK if str(c.get("name") or "").strip()]


def _extract_json_obj(text: str) -> Optional[dict]:
    """
    Try to extract JSON object from LLM output.
    """
    if not text:
        return None
    t = text.strip()

    # First try direct json
    try:
        obj = json.loads(t)
        if isinstance(obj, dict):
            return obj
    except Exception:
        pass

    # Try to find first {...} block
    m = re.search(r"\{.*\}", t, re.DOTALL)
    if not m:
        return None
    chunk = m.group(0).strip()
    try:
        obj = json.loads(chunk)
        if isinstance(obj, dict):
            return obj
    except Exception:
        return None
    return None


def _normalize_cards(cards: Any) -> List[dict]:
    if not isinstance(cards, list):
        return []
    out: List[dict] = []
    for c in cards:
        if not isinstance(c, dict):
            continue
        out.append(
            {
                "position": str(c.get("position") or "").strip(),
                "title": str(c.get("title") or "").strip(),
                "card_name": str(c.get("card_name") or "").strip(),
                "is_reversed": bool(c.get("is_reversed")) if c.get("is_reversed") is not None else False,
                "notes": str(c.get("notes") or "").strip(),
            }
        )
    return out


def _enrich_with_deck(cards: List[dict]) -> List[dict]:
    """
    Add card_index + meaning from tarot_deck where possible.
    """
    from tarot_deck import TAROT_DECK  # type: ignore

    by_name = {str(c["name"]).strip().lower(): (int(c["id"]), c) for c in TAROT_DECK}
    enriched: List[dict] = []
    for c in cards:
        name = (c.get("card_name") or "").strip()
        key = name.lower()
        if key in by_name:
            idx, card = by_name[key]
            is_rev = bool(c.get("is_reversed"))
            meaning = card["reversed_meaning"] if is_rev else card["upright_meaning"]
            enriched.append(
                {
                    **c,
                    "card_index": idx,
                    "meaning": meaning,
                }
            )
        else:
            enriched.append({**c, "card_index": None, "meaning": ""})
    return enriched


async def generate_photo_analysis_llm(
    *,
    topic: str,
    question: str,
    image_bytes: bytes,
    image_mime: str,
    extra_context: str = "",
    require_llm: bool = True,
) -> Tuple[str, List[dict]]:
    """
    Returns (description_md, cards[])
    cards elements:
      { position, title, card_name, is_reversed, notes, card_index, meaning }
    """
    api_key = _env("OPENAI_API_KEY")
    model = _env("OPENAI_VISION_MODEL", _env("OPENAI_MODEL", "gpt-4o-mini"))

    if not api_key:
        if require_llm:
            raise RuntimeError("OPENAI_API_KEY is missing")
        return ("", [])

    deck = _deck_names()
    deck_list = "\n".join([f"- {n}" for n in deck])

    system_prompt = (
        "Ты — внимательный таролог-аналитик и помощник. Твоя задача — проанализировать ФОТО расклада Таро.\n"
        "Важно: будь честным. Если карта плохо читается — так и скажи.\n"
        "Не выдумывай «точные предсказания», фокус — смысл, динамика, практические рекомендации.\n\n"
        "Нужно определить:\n"
        "1) Какие карты на фото (используй ТОЧНОЕ имя из списка колоды ниже)\n"
        "2) По возможности — порядок/позиции (слева-направо, сверху-вниз) и перевёрнутость\n"
        "3) Сгенерировать человеческий разбор на русском, без шаблонов.\n\n"
        "Формат ответа: ВЫВОДИ ТОЛЬКО JSON объект (без текста вокруг) вида:\n"
        "{\n"
        '  "description": "markdown-текст (с заголовками/буллетами)",\n'
        '  "cards": [\n'
        '    {"position":"left","title":"Слева","card_name":"...", "is_reversed":false, "notes":"кратко почему так решил"},\n'
        '    ...\n'
        "  ]\n"
        "}\n\n"
        "Список допустимых card_name (строго из этого списка):\n"
        f"{deck_list}\n"
    )

    user_prompt = (
        f"Тема: {topic}\n"
        f"Вопрос: {question}\n"
        f"Контекст (если есть): {extra_context}\n\n"
        "Проанализируй фото расклада."
    )

    last_err: Optional[Exception] = None
    for attempt in range(2):
        try:
            text = await _call_openai_with_image(
                system_prompt,
                user_prompt,
                image_bytes=image_bytes,
                image_mime=image_mime,
                model=model,
                temperature=0.6,
                max_tokens=1400,
            )
            text = (text or "").strip()
            if not text:
                raise RuntimeError("Empty vision response")

            obj = _extract_json_obj(text)
            if not obj:
                # if model didn't follow JSON, return as description only
                return (text, [])

            desc = str(obj.get("description") or "").strip()
            cards_raw = _normalize_cards(obj.get("cards"))
            cards_enriched = _enrich_with_deck(cards_raw)

            return (desc, cards_enriched)
        except Exception as e:
            last_err = e
            log.warning("photo LLM attempt %s failed: %s", attempt + 1, repr(e))

    if require_llm:
        raise RuntimeError(f"LLM failed: {last_err!r}")
    return ("", [])
