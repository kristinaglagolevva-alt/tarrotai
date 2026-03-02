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


def _relay_enabled() -> bool:
    return bool(_env("OPENAI_RELAY_URL"))


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
        "finance": "Фокус: деньги, стабильность, доходы и расходы.",
        "other": "Фокус: общий контекст ситуации и личные приоритеты.",
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


def _has_meaningful_question(question: str) -> bool:
    return bool(str(question or "").strip())


def _strip_question_context_section(text: str) -> str:
    src = str(text or "")
    if not src:
        return ""

    out = src
    out = re.sub(
        r"(^|\n)\s*##\s*Что это значит в контексте вопроса\s*\n[\s\S]*?(?=(\n\s*##\s)|\Z)",
        "\n",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(
        r"(^|\n)\s*Что это значит в контексте вопроса\s*\n[\s\S]*?(?=(\n\s*##\s)|(\n\s*\*\*Итог)|\Z)",
        "\n",
        out,
        flags=re.IGNORECASE,
    )
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out


async def _call_openai(system_prompt: str, user_prompt: str, *, model: str, temperature: float, max_tokens: int) -> str:
    relay_url = _env("OPENAI_RELAY_URL")
    relay_token = _env("OPENAI_RELAY_TOKEN")
    relay_err: Optional[Exception] = None
    if relay_url:
        try:
            import httpx  # type: ignore

            headers = {"Content-Type": "application/json"}
            if relay_token:
                headers["Authorization"] = f"Bearer {relay_token}"
                headers["X-Relay-Token"] = relay_token

            payload = {
                "mode": "chat",
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            }

            async with httpx.AsyncClient(timeout=90.0) as client:
                resp = await client.post(relay_url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            text = str((data or {}).get("text") or "").strip()
            if text:
                return text
            raise RuntimeError("Relay returned empty text")
        except Exception as e:
            relay_err = e
            log.warning("relay chat failed: %s", repr(e))
            if not _env("OPENAI_API_KEY"):
                raise RuntimeError(f"Relay failed: {relay_err!r}")

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
    relay_url = _env("OPENAI_RELAY_URL")
    relay_token = _env("OPENAI_RELAY_TOKEN")
    relay_err: Optional[Exception] = None
    if relay_url:
        try:
            import httpx  # type: ignore

            headers = {"Content-Type": "application/json"}
            if relay_token:
                headers["Authorization"] = f"Bearer {relay_token}"
                headers["X-Relay-Token"] = relay_token

            payload = {
                "mode": "vision",
                "model": model,
                "temperature": temperature,
                "max_tokens": max_tokens,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "image_mime": image_mime,
                "image_base64": base64.b64encode(image_bytes).decode("ascii"),
            }

            async with httpx.AsyncClient(timeout=120.0) as client:
                resp = await client.post(relay_url, headers=headers, json=payload)
                resp.raise_for_status()
                data = resp.json()
            text = str((data or {}).get("text") or "").strip()
            if text:
                return text
            raise RuntimeError("Relay returned empty text")
        except Exception as e:
            relay_err = e
            log.warning("relay vision failed: %s", repr(e))
            if not _env("OPENAI_API_KEY"):
                raise RuntimeError(f"Relay failed: {relay_err!r}")

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
    has_relay = _relay_enabled()
    model = _env("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key and not has_relay:
        if require_llm:
            raise RuntimeError("OPENAI_API_KEY is missing (and OPENAI_RELAY_URL is not set)")
        return fallback_text(topic, question, card, is_reversed)

    orient = "перевёрнутая" if is_reversed else "прямая"
    meaning = card["reversed_meaning"] if is_reversed else card["upright_meaning"]
    has_question = _has_meaningful_question(question)

    if has_question:
        system_prompt = (
            "Ты — человечный таролог-психолог. Пиши на русском, живо и понятно.\n"
            "Не делай точных предсказаний и не выдумывай факты о людях.\n"
            "Опирайся на вопрос пользователя и смысл карты.\n\n"
            "Формат строго в markdown:\n"
            "## Суть\n"
            "2–3 предложения, коротко и по вопросу.\n"
            "## Что это значит в контексте вопроса\n"
            "2–4 предложения.\n"
            "## Практика на сегодня\n"
            "- 4–6 конкретных шагов\n"
            "**Итог:** 1 короткая поддерживающая фраза.\n\n"
            "Ограничение: 140–220 слов, без воды."
        )
        user_prompt = (
            f"Тема: {topic}\n"
            f"Вопрос: {question}\n"
            f"Карта: {card['name']} ({orient})\n"
            f"Смысл карты (для тебя как справка): {meaning}\n"
            "Сгенерируй ответ."
        )
    else:
        system_prompt = (
            "Ты — человечный таролог-психолог. Пиши на русском, живо и понятно.\n"
            "Не делай точных предсказаний и не выдумывай факты о людях.\n"
            "Вопрос отсутствует: не упоминай «контекст вопроса» и не придумывай вопрос.\n"
            "Опирайся только на смысл карты и тему дня.\n\n"
            "Формат строго в markdown:\n"
            "## Суть\n"
            "2–3 предложения по значению карты на день.\n"
            "## Практика на сегодня\n"
            "- 4–6 конкретных шагов\n"
            "**Итог:** 1 короткая поддерживающая фраза.\n\n"
            "Ограничение: 120–200 слов, без воды."
        )
        user_prompt = (
            f"Тема: {topic}\n"
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
                temperature=0.72,
                max_tokens=560,
            )
            text = _sanitize_memory_meta_phrases((text or "").strip())
            if not has_question:
                text = _strip_question_context_section(text)
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
    has_relay = _relay_enabled()
    model = _env("OPENAI_MODEL", "gpt-4o-mini")

    if not api_key and not has_relay:
        if require_llm:
            raise RuntimeError("OPENAI_API_KEY is missing (and OPENAI_RELAY_URL is not set)")
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
        "Ты — человечный таролог-психолог. Пиши как живой эксперт, без шаблонов.\n"
        "Не делай точных предсказаний и не повторяй дословно входные значения карт.\n"
        "Собери карты в одну понятную картину по вопросу пользователя.\n\n"
        "Если во входе есть блок 'Контекст из истории', используй его аккуратно и только по фактам из входа.\n"
        "Важно: при наличии исторического контекста обязательно подмешай его уже в блок 'Общий вектор' "
        "(1 естественная фраза про динамику ситуации), а не только в отдельный итоговый блок.\n"
        "Если во входе есть блок 'Карточная динамика по вашей истории', обязательно используй минимум 1 факт "
        "про конкретную карту (повтор/разворот/пауза) в 'Общий вектор' или в 'Тогда vs сейчас'.\n"
        "Если во входе есть подпункт 'Сравнение (тогда vs сейчас)', добавь отдельный блок в конце ответа:\n"
        "## Тогда vs сейчас\n"
        "- 2–3 короткие строки: что совпало, что изменилось, практический вывод.\n"
        "- Практический вывод обязан быть конкретным действием (что сделать сегодня/в ближайшие 24 часа).\n"
        "- Запрещены шаблонные формулировки типа: "
        "'сравните текущее решение с прошлым', 'в этом точка развилки', 'смотрите динамику'.\n"
        "Опирайся только на факты из контекста: прошлый вопрос, прошлый краткий итог, прошлые карты.\n"
        "Нельзя писать служебные фразы вроде 'AI заметил', 'повторяющийся паттерн', 'триггер', "
        "'другое', 'open question' и т.п.\n\n"
        "Формат строго в markdown:\n"
        "## Общий вектор\n"
        "1 абзац (3–4 предложения) по вопросу; при наличии исторического контекста мягко упомяни динамику.\n"
        "## По картам\n"
        "Для каждой позиции в исходном порядке: **Название позиции: карта** + 2–3 предложения.\n"
        "## Рекомендации\n"
        "- 5–7 конкретных шагов\n"
        "**Итог:** 1 короткая практичная мысль.\n"
        "## Тогда vs сейчас\n"
        "Показывай только если есть исторический контекст. Этот блок должен быть последним.\n\n"
        "Ограничение: 220–340 слов."
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
                temperature=0.7,
                max_tokens=780,
            )
            text = _sanitize_memory_meta_phrases((text or "").strip())
            if text:
                then_vs_now_lines = _extract_then_vs_now_lines_from_context(extra_context)
                history_hint = _extract_history_hint_from_context(extra_context)
                if then_vs_now_lines:
                    text = _ensure_then_vs_now_block(text, context_lines=then_vs_now_lines)
                if history_hint:
                    text = _ensure_history_in_general_vector(text, history_hint=history_hint)
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


def _sanitize_memory_meta_phrases(text: str) -> str:
    src = str(text or "")
    if not src.strip():
        return ""

    patterns = [
        r"(?im)^\s*.*ai\s+заметил.*\n?",
        r"(?im)^\s*.*повторяющ[а-я]*\s+паттерн.*\n?",
        r"(?im)^\s*.*эта\s+тема\s+возвращается.*(?:другое|open).*\n?",
        r"(?im)^\s*.*(?:trigger|триггер).*\n?",
        r"(?im)^\s*факт\s+из\s+истории\s*:.*\n?",
        r"(?im)^\s*сравнение\s+карт\s*:.*\n?",
        r"(?im)^\s*контекст\s+из\s+истории.*\n?",
        r"(?im)^\s*карточная\s+динамика\s+по\s+вашей\s+истории\s*:.*\n?",
    ]
    cleaned = src
    for pat in patterns:
        cleaned = re.sub(pat, "", cleaned)

    cleaned = re.sub(
        r"(?im)\bобязательно\b[^.\n!?]*(?:[.\n!?]|$)",
        "",
        cleaned,
    )
    cleaned = cleaned.replace("open question", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def _normalize_history_hint(hint: str) -> str:
    raw = str(hint or "").strip()
    if not raw:
        return ""
    clean = re.sub(r"^сравнение\s+карт:\s*", "", raw, flags=re.IGNORECASE)
    clean = re.sub(r"^факт\s+из\s+истории:\s*", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"(?i)\bобязательно\b[^.\n!?]*(?:[.\n!?]|$)", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip(" .")
    if not clean:
        return ""

    low = clean.lower()
    if "ориентация изменилась" in low:
        return "По сравнению с прошлым раскладом у повторяющейся карты заметно изменился вектор."
    if "снова выходит та же карта" in low or "снова вышла та же карта" in low:
        cards_match = re.search(r"\((.*?)\)", clean)
        cards = cards_match.group(1).strip() if cards_match else ""
        if cards:
            return f"В раскладе снова проявились знакомые карты ({cards}), поэтому тема развивается по той же линии."
        return "В раскладе снова проявилась знакомая карта, поэтому тема развивается по той же линии."
    if "повторяется акцент на масти" in low:
        suit_match = re.search(r"масти\s+«([^»]+)»", clean)
        suit = suit_match.group(1).strip() if suit_match else ""
        if suit:
            return f"Как и раньше, сохраняется акцент масти «{suit}», но детали ситуации уже сдвигаются."
    if "повторяется смысловая линия" in low:
        motif_match = re.search(r"линия\s+«([^»]+)»", clean)
        motif = motif_match.group(1).strip() if motif_match else ""
        if motif:
            return f"Снова возвращается линия «{motif}», но текущие карты показывают её в новом ракурсе."
    return clean


def _extract_then_vs_now_lines_from_context(extra_context: str) -> List[str]:
    lines: List[str] = []
    src_lines = str(extra_context or "").splitlines()
    in_block = False
    for raw in src_lines:
        line = str(raw or "").strip()
        if not line:
            if in_block:
                break
            continue
        if "Сравнение (тогда vs сейчас)" in line:
            in_block = True
            continue
        if not in_block:
            continue
        if line.startswith("-"):
            clean = re.sub(r"^\-\s*", "", line).strip(" .")
            if clean:
                lines.append(clean)
            if len(lines) >= 3:
                break
        else:
            # закончился целевой блок
            break
    return lines[:3]


def _extract_history_hint_from_context(extra_context: str) -> str:
    src_lines = str(extra_context or "").splitlines()
    for raw in src_lines:
        line = str(raw or "").strip()
        if line.lower().startswith("сравнение карт:"):
            return _normalize_history_hint(line)
    for raw in src_lines:
        line = str(raw or "").strip()
        if line.lower().startswith("прошлый похожий вопрос:"):
            return line.strip(" .")
    return ""


def _ensure_then_vs_now_block(text: str, *, context_lines: List[str]) -> str:
    if not context_lines:
        return text
    block = "## Тогда vs сейчас\n" + "\n".join(f"- {line}" for line in context_lines) + "\n"
    pattern = re.compile(
        r"(?is)(^|\n)\s*##\s*Тогда\s*vs\s*сейчас\s*\n(?P<body>.*?)(?=\n\s*##\s|\Z)"
    )
    match = pattern.search(text)
    if not match:
        return (text.rstrip() + "\n\n" + block).strip()

    body = str(match.group("body") or "")
    low = body.lower()
    bad_markers = (
        "сравните текущее решение с прошлым",
        "в этом точка развилки",
        "смотрите динамику",
        "ai заметил",
        "повторяющийся паттерн",
    )
    if len(body.strip()) < 70 or any(marker in low for marker in bad_markers):
        repl = (match.group(1) or "\n") + block
        return pattern.sub(repl, text, count=1).strip()
    return text


def _ensure_history_in_general_vector(text: str, *, history_hint: str) -> str:
    hint = _normalize_history_hint(history_hint)
    if not hint:
        return text
    pattern = re.compile(
        r"(?is)(^|\n)\s*##\s*Общий\s+вектор\s*\n(?P<body>.*?)(?=\n\s*##\s|\Z)"
    )
    match = pattern.search(text)
    if not match:
        return text
    body = str(match.group("body") or "").strip()
    low = body.lower()
    markers = ("в прошл", "раньше", "как и тогда", "по сравнению", "снова")
    if any(m in low for m in markers):
        return text

    concise_hint = re.sub(r"\s+", " ", hint).strip(" .")
    if len(concise_hint) > 180:
        concise_hint = concise_hint[:180].rsplit(" ", 1)[0]
    if concise_hint:
        body = (body + "\n\n" + concise_hint + ".").strip()
        repl = (match.group(1) or "\n") + "## Общий вектор\n" + body + "\n"
        return pattern.sub(repl, text, count=1).strip()
    return text


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
    has_relay = _relay_enabled()
    model = _env("OPENAI_VISION_MODEL", _env("OPENAI_MODEL", "gpt-4o-mini"))

    if not api_key and not has_relay:
        if require_llm:
            raise RuntimeError("OPENAI_API_KEY is missing (and OPENAI_RELAY_URL is not set)")
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
                max_tokens=980,
            )
            text = (text or "").strip()
            if not text:
                raise RuntimeError("Empty vision response")

            obj = _extract_json_obj(text)
            if not obj:
                # if model didn't follow JSON, return as description only
                return (_sanitize_memory_meta_phrases(text), [])

            desc = _sanitize_memory_meta_phrases(str(obj.get("description") or "").strip())
            cards_raw = _normalize_cards(obj.get("cards"))
            cards_enriched = _enrich_with_deck(cards_raw)

            return (desc, cards_enriched)
        except Exception as e:
            last_err = e
            log.warning("photo LLM attempt %s failed: %s", attempt + 1, repr(e))

    if require_llm:
        raise RuntimeError(f"LLM failed: {last_err!r}")
    return ("", [])
