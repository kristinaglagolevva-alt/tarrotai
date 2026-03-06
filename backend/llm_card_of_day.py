# llm_card_of_day.py
from __future__ import annotations

import asyncio
import os
import json
import re
import base64
import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple

log = logging.getLogger("llm")

_VISION_ESCALATION_LOCK = asyncio.Lock()
_VISION_ESCALATION_STATE: Dict[str, Any] = {
    "day": "",
    "total": 0,
    "escalated": 0,
}


def _env(name: str, default: str = "") -> str:
    v = os.getenv(name)
    return v.strip() if isinstance(v, str) else default


def _env_float(name: str, default: float) -> float:
    raw = _env(name, "")
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _env_int(name: str, default: int) -> int:
    raw = _env(name, "")
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _vision_utc_day() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _vision_reset_if_new_day() -> None:
    day = _vision_utc_day()
    if str(_VISION_ESCALATION_STATE.get("day") or "") != day:
        _VISION_ESCALATION_STATE["day"] = day
        _VISION_ESCALATION_STATE["total"] = 0
        _VISION_ESCALATION_STATE["escalated"] = 0


async def _vision_register_request() -> Dict[str, Any]:
    async with _VISION_ESCALATION_LOCK:
        _vision_reset_if_new_day()
        _VISION_ESCALATION_STATE["total"] = int(_VISION_ESCALATION_STATE.get("total") or 0) + 1
        return dict(_VISION_ESCALATION_STATE)


async def _vision_try_escalate() -> Tuple[bool, str, Dict[str, Any]]:
    """
    Predohranitel for expensive fallback model:
    - OPENAI_VISION_ESCALATION_MAX_RATIO (default 0.25)
    - OPENAI_VISION_ESCALATION_DAILY_MAX (default 0 = disabled)
    - OPENAI_VISION_ESCALATION_MIN_SAMPLES (default 20)
    """
    max_ratio = _env_float("OPENAI_VISION_ESCALATION_MAX_RATIO", 0.25)
    daily_max = _env_int("OPENAI_VISION_ESCALATION_DAILY_MAX", 0)
    min_samples = _env_int("OPENAI_VISION_ESCALATION_MIN_SAMPLES", 20)

    if max_ratio < 0:
        max_ratio = 0.0
    if max_ratio > 1:
        max_ratio = 1.0
    if daily_max < 0:
        daily_max = 0
    if min_samples < 0:
        min_samples = 0

    async with _VISION_ESCALATION_LOCK:
        _vision_reset_if_new_day()
        total = int(_VISION_ESCALATION_STATE.get("total") or 0)
        escalated = int(_VISION_ESCALATION_STATE.get("escalated") or 0)

        if daily_max > 0 and escalated >= daily_max:
            return False, "daily_max_reached", dict(_VISION_ESCALATION_STATE)

        # Apply ratio gate only after enough daily traffic to stabilize it.
        if total >= min_samples and total > 0:
            current_ratio = escalated / float(total)
            if current_ratio >= max_ratio:
                return False, "ratio_limit_reached", dict(_VISION_ESCALATION_STATE)

        _VISION_ESCALATION_STATE["escalated"] = escalated + 1
        return True, "ok", dict(_VISION_ESCALATION_STATE)


async def _vision_mark_forced_escalation() -> Dict[str, Any]:
    """
    Used when primary model failed hard and we must try fallback for service continuity.
    """
    async with _VISION_ESCALATION_LOCK:
        _vision_reset_if_new_day()
        _VISION_ESCALATION_STATE["escalated"] = int(_VISION_ESCALATION_STATE.get("escalated") or 0) + 1
        return dict(_VISION_ESCALATION_STATE)


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

    has_question = bool(str(question or "").strip())

    system_prompt = (
        "Ты — человечный таролог-психолог. Пиши как живой эксперт, без шаблонов.\n"
        "Не делай точных предсказаний и не повторяй дословно входные значения карт.\n"
        "Собери карты в одну понятную картину по вопросу пользователя.\n\n"
        "Если вопрос не задан, сделай универсальную персонализированную интерпретацию:\n"
        "- опирайся только на карты и их сочетание,\n"
        "- аккуратно предположи 1–2 вероятные жизненные темы,\n"
        "- дай практичные шаги без категоричных утверждений.\n\n"
        "Если во входе есть блок 'Контекст из истории', используй его аккуратно и только по фактам из входа.\n"
        "Если во входе есть строки 'Тон ответа:' и 'Стиль:', обязательно соблюдай их во всём тексте.\n"
        "Если во входе есть 'Поведенческий паттерн' и 'Как вести ответ', используй это только для качества формулировок,\n"
        "но НЕ выводи эти служебные названия в финальный ответ.\n"
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
        f"Вопрос: {question if has_question else '(не задан)'}\n"
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
        r"(?im)^\s*поведенческ[а-я]*\s+паттерн\s*:.*\n?",
        r"(?im)^\s*как\s+вести\s+ответ\s*:.*\n?",
        r"(?im)^\s*тон\s+ответа\s*:.*\n?",
        r"(?im)^\s*стиль\s*:.*\n?",
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
        raw_name = str(c.get("card_name") or "").strip()
        raw_title = str(c.get("title") or "").strip()
        if not raw_name:
            raw_name = "Unknown"
        out.append(
            {
                "position": str(c.get("position") or "").strip(),
                "title": raw_title,
                "card_name": raw_name,
                "is_reversed": bool(c.get("is_reversed")) if c.get("is_reversed") is not None else False,
                "notes": str(c.get("notes") or "").strip(),
                "confidence": c.get("confidence"),
            }
        )
    return out


def _to_conf01(value: Any) -> Optional[float]:
    try:
        if value is None:
            return None
        num = float(value)
        if num < 0:
            return 0.0
        # Some models may return 0..100.
        if num > 1 and num <= 100:
            num = num / 100.0
        if num > 1:
            return 1.0
        return num
    except Exception:
        return None


def _photo_quality_metrics(cards: List[dict], description: str, question: str) -> Dict[str, Any]:
    total = len(cards or [])
    known = sum(1 for c in (cards or []) if c.get("card_index") is not None)
    unknown = max(0, total - known)
    unknown_ratio = (unknown / total) if total else 1.0
    names = [str(c.get("card_name") or "").strip().lower() for c in (cards or []) if str(c.get("card_name") or "").strip()]
    unique_names = len(set(names))
    duplicate_ratio = ((total - unique_names) / total) if total else 1.0
    conf_values = [_to_conf01(c.get("confidence")) for c in (cards or [])]
    conf_values = [v for v in conf_values if v is not None]
    avg_conf = (sum(conf_values) / len(conf_values)) if conf_values else None
    echo = _is_question_echo_description(question, description)
    short_desc = len(str(description or "").strip()) < 80

    # Heuristic score for model-selection only.
    score = 0.0
    score += known * 18.0
    score += total * 8.0
    if avg_conf is not None:
        score += avg_conf * 35.0
    else:
        score += 8.0 if total >= 2 else 3.0
    score -= unknown_ratio * 24.0
    score -= duplicate_ratio * 12.0
    if echo:
        score -= 16.0
    if short_desc:
        score -= 8.0

    return {
        "total": total,
        "known": known,
        "unknown_ratio": unknown_ratio,
        "duplicate_ratio": duplicate_ratio,
        "avg_conf": avg_conf,
        "echo": echo,
        "short_desc": short_desc,
        "score": score,
    }


def _is_low_confidence_photo_result(metrics: Dict[str, Any]) -> Tuple[bool, str]:
    total = int(metrics.get("total") or 0)
    known = int(metrics.get("known") or 0)
    unknown_ratio = float(metrics.get("unknown_ratio") or 0.0)
    avg_conf = metrics.get("avg_conf")
    echo = bool(metrics.get("echo"))
    short_desc = bool(metrics.get("short_desc"))

    if total == 0:
        return True, "no_cards"
    if known == 0:
        return True, "no_known_cards"

    # Single-card photo can still be valid, but only with strong confidence.
    if total == 1:
        if avg_conf is None:
            if echo or short_desc:
                return True, "single_no_conf_echo"
            return False, "single_no_conf_ok"
        if float(avg_conf) < 0.84:
            return True, "single_low_conf"
        return False, "single_ok"

    # 2+ cards.
    if unknown_ratio > 0.55:
        return True, "too_many_unknown"
    if avg_conf is not None and float(avg_conf) < 0.62:
        return True, "avg_conf_low"
    if echo and known < 2:
        return True, "echo_with_few_cards"
    return False, "ok"


def _extract_cards_from_free_text(text: str) -> List[dict]:
    src = str(text or "")
    if not src.strip():
        return []
    low = src.lower()
    names = sorted(_deck_names(), key=len, reverse=True)
    out: List[dict] = []
    seen: set[str] = set()
    for name in names:
        nlow = name.lower()
        if nlow in seen:
            continue
        pat = rf"(?<!\w){re.escape(nlow)}(?!\w)"
        m = re.search(pat, low)
        if not m:
            continue
        around = low[max(0, m.start() - 80) : min(len(low), m.end() + 80)]
        is_reversed = bool(re.search(r"перев[её]рнут|обратн|reverse|reversed", around))
        out.append(
            {
                "position": "",
                "title": "",
                "card_name": name,
                "is_reversed": is_reversed,
                "notes": "Выделено из свободного текста ответа модели.",
            }
        )
        seen.add(nlow)
        if len(out) >= 12:
            break
    return out


_RU_STOPWORDS = {
    "и",
    "в",
    "во",
    "на",
    "с",
    "со",
    "к",
    "по",
    "про",
    "о",
    "об",
    "что",
    "как",
    "это",
    "эта",
    "этот",
    "этом",
    "для",
    "или",
    "ли",
    "у",
    "а",
    "но",
    "да",
    "не",
    "из",
    "за",
}


def _norm_words(src: str) -> List[str]:
    clean = re.sub(r"[^a-zA-Zа-яА-ЯёЁ0-9]+", " ", str(src or "").lower())
    words = [w for w in clean.split() if len(w) >= 3 and w not in _RU_STOPWORDS]
    return words


def _is_question_echo_description(question: str, description: str) -> bool:
    q = str(question or "").strip()
    d = str(description or "").strip()
    if not q or not d:
        return False

    q_norm = " ".join(_norm_words(q))
    d_norm = " ".join(_norm_words(d))
    if not q_norm or not d_norm:
        return False

    if q_norm in d_norm and len(d_norm) <= max(120, int(len(q_norm) * 5)):
        return True

    q_set = set(_norm_words(q))
    d_set = set(_norm_words(d))
    if not q_set:
        return False
    overlap = len(q_set & d_set) / max(1, len(q_set))
    if overlap >= 0.7 and len(d_set) <= max(24, int(len(q_set) * 3)):
        return True

    if re.search(r"\b(в данном раскладе|этот расклад|в этом раскладе)\b", d.lower()) and overlap >= 0.55:
        return True

    return False


def _short_meaning_line(src: str, limit: int = 120) -> str:
    text = str(src or "").strip()
    if not text:
        return ""
    first = re.split(r"[.!?;]", text)[0].strip()
    first = re.sub(r"\s+", " ", first)
    if len(first) <= limit:
        return first
    cut = first[:limit].rsplit(" ", 1)[0].strip()
    return cut or first[:limit]


def _build_photo_fallback_description(topic: str, question: str, cards: List[dict]) -> str:
    topic_hint = {
        "relations": "Фокус: динамика отношений, чувства и границы.",
        "career": "Фокус: работа, роли, решения и следующий шаг.",
        "finance": "Фокус: деньги, устойчивость и управление риском.",
        "other": "Фокус: общий контекст ситуации и ближайший ориентир.",
    }.get(str(topic or "other"), "Фокус: общий контекст ситуации.")

    practice = {
        "relations": "Обсудите одну конкретную тему спокойно и по фактам.",
        "career": "Зафиксируйте один практический шаг на 24 часа и проверьте результат.",
        "finance": "Примите одно аккуратное финансовое решение и оцените эффект через день.",
        "other": "Выберите одно действие на сегодня, которое снизит неопределенность.",
    }.get(str(topic or "other"), "Выберите одно действие на сегодня, которое снизит неопределенность.")

    lines: List[str] = ["## Общая интерпретация"]
    if str(question or "").strip():
        lines.append(f"Вопрос принят. {topic_hint}")
    else:
        lines.append(topic_hint)

    if not cards:
        lines.append(
            "На фото карты распознаны неуверенно. Сделайте снимок ближе, без бликов и с ровным светом,"
            " чтобы увидеть точную расшифровку."
        )
        return "\n\n".join(lines).strip()

    lines.append(
        f"Уверенно распознано карт: {len(cards)}. Ниже — ключевые акценты по самым заметным позициям."
    )
    lines.append("## Ключевые акценты")
    for idx, c in enumerate(cards[:4]):
        name = str(c.get("card_name") or "").strip() or f"Карта {idx + 1}"
        orient = "перевёрнутая" if bool(c.get("is_reversed")) else "прямая"
        meaning = _short_meaning_line(str(c.get("meaning") or ""))
        if meaning:
            lines.append(f"- {name} ({orient}): {meaning}.")
        else:
            lines.append(f"- {name} ({orient}).")

    lines.append("## Практический шаг")
    lines.append(practice)
    return "\n\n".join(lines).strip()


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


def _dedupe_photo_cards(cards: List[dict]) -> List[dict]:
    """Keep stable, unique recognized cards for UI/LLM prompt."""
    out: List[dict] = []
    seen: set[Tuple[str, str, bool, str]] = set()
    for c in cards or []:
        name = str(c.get("card_name") or "").strip() or "Unknown"
        pos = str(c.get("position") or "").strip().lower() or "unknown"
        is_rev = bool(c.get("is_reversed"))
        note = str(c.get("notes") or "").strip().lower()[:80]
        key = (name.lower(), pos, is_rev, note)
        if key in seen:
            continue
        seen.add(key)
        out.append(c)
    return out


def _cards_for_prompt(cards: List[dict], limit: int = 10) -> str:
    rows: List[str] = []
    for idx, c in enumerate((cards or [])[: max(1, int(limit))], start=1):
        raw_name = str(c.get("card_name") or "").strip()
        name = raw_name if raw_name and raw_name.lower() != "unknown" else f"Неуверенно распознанная карта {idx}"
        pos = str(c.get("position") or "").strip() or "unknown"
        orient = "перевёрнутая" if bool(c.get("is_reversed")) else "прямая"
        meaning = str(c.get("meaning") or "").strip()
        if meaning:
            rows.append(f"{idx}. {name} | позиция: {pos} | {orient} | смысл: {meaning}")
        else:
            rows.append(f"{idx}. {name} | позиция: {pos} | {orient}")
    return "\n".join(rows)


def _photo_detection_metrics(cards: List[dict]) -> Dict[str, Any]:
    total = len(cards or [])
    known = sum(1 for c in (cards or []) if c.get("card_index") is not None)
    unknown = max(0, total - known)
    unknown_ratio = (unknown / total) if total else 1.0
    names = [
        str(c.get("card_name") or "").strip().lower()
        for c in (cards or [])
        if str(c.get("card_name") or "").strip()
    ]
    unique_names = len(set(names))
    duplicate_ratio = ((total - unique_names) / total) if total else 1.0
    conf_values = [_to_conf01(c.get("confidence")) for c in (cards or [])]
    conf_values = [v for v in conf_values if v is not None]
    avg_conf = (sum(conf_values) / len(conf_values)) if conf_values else None

    score = 0.0
    score += known * 20.0
    score += total * 9.0
    if avg_conf is not None:
        score += avg_conf * 30.0
    else:
        score += 9.0 if total >= 2 else 4.0
    score -= unknown_ratio * 30.0
    score -= duplicate_ratio * 9.0

    return {
        "total": total,
        "known": known,
        "unknown_ratio": unknown_ratio,
        "duplicate_ratio": duplicate_ratio,
        "avg_conf": avg_conf,
        "score": score,
    }


def _is_low_confidence_photo_detection(metrics: Dict[str, Any]) -> Tuple[bool, str]:
    total = int(metrics.get("total") or 0)
    known = int(metrics.get("known") or 0)
    unknown_ratio = float(metrics.get("unknown_ratio") or 0.0)
    avg_conf = metrics.get("avg_conf")
    if total == 0:
        return True, "no_cards"
    if known == 0:
        return True, "no_known_cards"
    if total == 1:
        if avg_conf is not None and float(avg_conf) < 0.8:
            return True, "single_low_conf"
        return False, "single_ok"
    if unknown_ratio > 0.6:
        return True, "too_many_unknown"
    if avg_conf is not None and float(avg_conf) < 0.56:
        return True, "avg_conf_low"
    if total >= 5 and known < 3:
        return True, "many_cards_too_few_known"
    return False, "ok"


async def generate_photo_analysis_llm(
    *,
    topic: str,
    question: str,
    image_bytes: bytes,
    image_mime: str,
    extra_context: str = "",
    require_llm: bool = True,
    quota_register: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None,
    quota_try_escalate: Optional[Callable[[], Awaitable[Tuple[bool, str, Dict[str, Any]]]]] = None,
    quota_mark_forced_escalation: Optional[Callable[[], Awaitable[Dict[str, Any]]]] = None,
) -> Tuple[str, List[dict]]:
    """
    Returns (description_md, cards[])
    cards elements:
      { position, title, card_name, is_reversed, notes, card_index, meaning }
    """
    api_key = _env("OPENAI_API_KEY")
    has_relay = _relay_enabled()
    # Cascaded vision: cheaper primary model first, expensive fallback only when low confidence.
    primary_model = _env(
        "OPENAI_VISION_MODEL_PRIMARY",
        _env("OPENAI_VISION_MODEL", "gpt-4.1-mini"),
    )
    fallback_model = _env("OPENAI_VISION_MODEL_FALLBACK", "gpt-4o")

    if not api_key and not has_relay:
        if require_llm:
            raise RuntimeError("OPENAI_API_KEY is missing (and OPENAI_RELAY_URL is not set)")
        return ("", [])

    register_request = quota_register or _vision_register_request
    try_escalate = quota_try_escalate or _vision_try_escalate
    mark_forced_escalation = quota_mark_forced_escalation or _vision_mark_forced_escalation

    quota_snapshot = await register_request()
    log.debug(
        "photo vision quota start: day=%s total=%s escalated=%s",
        quota_snapshot.get("day"),
        quota_snapshot.get("total"),
        quota_snapshot.get("escalated"),
    )

    deck = _deck_names()
    deck_list = "\n".join([f"- {n}" for n in deck])
    text_model = _env("OPENAI_PHOTO_TEXT_MODEL", _env("OPENAI_MODEL", "gpt-4.1-mini"))

    detect_system_prompt = (
        "Ты OCR+Vision модуль для Таро-фото.\n"
        "Задача: только распознать карты на изображении. Никаких советов и интерпретаций.\n"
        "Правила:\n"
        "- Используй только то, что реально видно в кадре.\n"
        "- Если уверенность низкая — не выдумывай карту.\n"
        "- Если карт много, верни все уверенно распознанные (до 12).\n"
        "- card_name либо из списка ниже, либо 'Unknown' при неуверенности.\n"
        "- Верни visible_count: сколько карт вообще видно на фото (даже частично).\n"
        "- Верни ТОЛЬКО JSON.\n\n"
        "{\n"
        '  "visible_count": 0,\n'
        '  "cards":[\n'
        '    {"position":"left|center|right|top|bottom|unknown","title":"ярлык","card_name":"...",'
        ' "is_reversed":false,"notes":"коротко почему","confidence":0.0}\n'
        "  ]\n"
        "}\n\n"
        "Список допустимых card_name:\n"
        f"{deck_list}\n"
    )

    detect_user_prompt = (
        f"Тема: {topic}\n"
        f"Вопрос: {question}\n"
        f"Контекст: {extra_context}\n\n"
        "Сфокусируйся только на распознавании карт и их ориентации."
    )

    async def _run_detection_model(model_name: str) -> Tuple[List[dict], Dict[str, Any]]:
        last_err: Optional[Exception] = None
        for attempt in range(2):
            try:
                text = await _call_openai_with_image(
                    detect_system_prompt,
                    detect_user_prompt,
                    image_bytes=image_bytes,
                    image_mime=image_mime,
                    model=model_name,
                    temperature=0.08,
                    max_tokens=760,
                )
                text = (text or "").strip()
                if not text:
                    raise RuntimeError("Empty vision response")

                obj = _extract_json_obj(text)
                if not obj:
                    cards_guess = _extract_cards_from_free_text(text)
                    cards_guess = _dedupe_photo_cards(_enrich_with_deck(cards_guess))
                    return (cards_guess, {"parse_mode": "text"})

                cards_raw = _normalize_cards(obj.get("cards"))
                cards_enriched = _dedupe_photo_cards(_enrich_with_deck(cards_raw))
                visible_count = 0
                try:
                    visible_count = max(0, int(obj.get("visible_count") or 0))
                except Exception:
                    visible_count = 0
                return (cards_enriched, {"parse_mode": "json", "visible_count": visible_count})
            except Exception as e:
                last_err = e
                log.warning("photo LLM %s attempt %s failed: %s", model_name, attempt + 1, repr(e))
        raise RuntimeError(f"LLM failed for model={model_name}: {last_err!r}")

    primary_err: Optional[Exception] = None
    primary_result: Optional[Tuple[List[dict], Dict[str, Any]]] = None
    try:
        primary_result = await _run_detection_model(primary_model)
    except Exception as e:
        primary_err = e

    # If primary failed, try fallback model directly.
    if primary_result is None:
        if fallback_model and fallback_model != primary_model:
            try:
                forced_snapshot = await mark_forced_escalation()
                cards_fb, _meta_fb = await _run_detection_model(fallback_model)
                log.info(
                    "photo vision: primary failed; forced fallback %s succeeded (day=%s total=%s escalated=%s)",
                    fallback_model,
                    forced_snapshot.get("day"),
                    forced_snapshot.get("total"),
                    forced_snapshot.get("escalated"),
                )
                description_fb = _build_photo_fallback_description(topic, question, cards_fb)
                return (description_fb, cards_fb)
            except Exception as e:
                if require_llm:
                    raise RuntimeError(f"Photo LLM failed: primary={primary_err!r}; fallback={e!r}")
                return ("", [])
        if require_llm:
            raise RuntimeError(f"Photo LLM failed: {primary_err!r}")
        return ("", [])

    cards_primary, _meta_primary = primary_result
    m_primary = _photo_detection_metrics(cards_primary)
    primary_low, primary_reason = _is_low_confidence_photo_detection(m_primary)
    expected_primary = int((_meta_primary or {}).get("visible_count") or 0)
    if expected_primary >= 3 and len(cards_primary) < max(1, expected_primary - 1):
        primary_low = True
        primary_reason = "visible_count_mismatch"
    selected_cards = cards_primary

    if primary_low and fallback_model and fallback_model != primary_model:
        allow_escalation, quota_reason, quota_after = await try_escalate()
        if not allow_escalation:
            log.info(
                "photo vision: escalation blocked by quota (%s). keep primary=%s reason=%s score=%.2f (day=%s total=%s escalated=%s)",
                quota_reason,
                primary_model,
                primary_reason,
                float(m_primary.get("score") or 0.0),
                quota_after.get("day"),
                quota_after.get("total"),
                quota_after.get("escalated"),
            )
        else:
            try:
                cards_fb, _meta_fb = await _run_detection_model(fallback_model)
                m_fb = _photo_detection_metrics(cards_fb)
                fb_low, fb_reason = _is_low_confidence_photo_detection(m_fb)
                expected_fb = int((_meta_fb or {}).get("visible_count") or 0)
                if expected_fb >= 3 and len(cards_fb) < max(1, expected_fb - 1):
                    fb_low = True
                    fb_reason = "visible_count_mismatch"
                if (not fb_low and primary_low) or (
                    float(m_fb.get("score") or 0.0) >= float(m_primary.get("score") or 0.0) + 5.0
                ):
                    log.info(
                        "photo vision escalated to fallback: primary=%s (%s, score=%.2f) -> fallback=%s (%s, score=%.2f)",
                        primary_model,
                        primary_reason,
                        float(m_primary.get("score") or 0.0),
                        fallback_model,
                        fb_reason,
                        float(m_fb.get("score") or 0.0),
                    )
                    selected_cards = cards_fb
                else:
                    log.info(
                        "photo vision kept primary after fallback check: primary score=%.2f, fallback score=%.2f",
                        float(m_primary.get("score") or 0.0),
                        float(m_fb.get("score") or 0.0),
                    )
            except Exception as e:
                log.warning("photo vision fallback %s failed after low primary confidence: %s", fallback_model, repr(e))

    description = ""
    try:
        cards_block = _cards_for_prompt(selected_cards, limit=10)
        interpret_system_prompt = (
            "Ты опытный таролог-аналитик. Пиши по-русски, аккуратно и по делу.\n"
            "Не пересказывай вопрос пользователя буквально и не дублируй его формулировки.\n"
            "Не выдумывай карты, работай только с переданным списком.\n"
            "Если есть Unknown-карты, пометь это аккуратно и не делай категоричных выводов по ним.\n"
            "Без категоричных предсказаний; фокус на смыслах и действиях.\n"
            "Ответ в markdown, 140-260 слов.\n"
            "Структура:\n"
            "## Общая интерпретация\n"
            "## Карты в раскладе\n"
            "## Практический шаг\n"
        )
        interpret_user_prompt = (
            f"Тема: {topic}\n"
            f"Вопрос: {question}\n"
            f"Доп. контекст: {extra_context}\n"
            f"Распознанные карты:\n{cards_block if cards_block else '(карты распознаны неуверенно)'}\n\n"
            "Сделай интерпретацию по картам."
        )
        description = (await _call_openai(
            interpret_system_prompt,
            interpret_user_prompt,
            model=text_model,
            temperature=0.35,
            max_tokens=760,
        )).strip()
    except Exception as e:
        log.warning("photo text interpretation failed: %s", repr(e))

    description = _sanitize_memory_meta_phrases(description or "")
    if not description or _is_question_echo_description(question, description):
        description = _build_photo_fallback_description(topic, question, selected_cards)

    return (description, selected_cards)
