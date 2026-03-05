from types import SimpleNamespace

import asyncio

from features.memory.extractor import build_memory_event_payload
from features.memory.service import (
    _build_then_vs_now_lines,
    _build_cards_delta_hint,
    _semantic_signature,
    build_runtime_prompt_context,
    build_card_of_day_cards_payload,
    build_inline_hint,
    build_inline_hint_for_question,
    classify_safety_risk,
    infer_reversed_from_text,
    is_resolvable_followup_context,
)


def test_build_inline_hint_prefers_cycle_hints():
    summary = {
        "home_hint": "Как прошёл экзамен? Если хотите, мягко посмотрим, как изменилась ситуация.",
        "cycle_hints": ["Тема «отношения» снова возвращается. Полезно сравнить динамику с прошлым раскладом."],
        "recurring_topics": [{"topic": "отношения", "count": 4}],
        "repeated_cards": [{"card": "Башня", "count": 2}],
    }
    assert build_inline_hint(summary) == ""


def test_build_inline_hint_skips_generic_other_hint():
    summary = {
        "cycle_hints": ["Эта тема возвращается уже 55-й раз: другое."],
        "recurring_topics": [{"topic": "другое", "count": 55}],
        "repeated_cards": [{"card": "Башня", "count": 2}],
    }
    assert build_inline_hint(summary) == ""


def test_classify_safety_risk():
    assert classify_safety_risk("я не хочу жить и думаю о суициде") == "crisis"
    assert classify_safety_risk("у меня болит живот, это болезнь?") == "medical"
    assert classify_safety_risk("после смерти родственника не могу прийти в себя") == "crisis"
    assert classify_safety_risk("что ждет меня в карьере") == "none"


def test_build_inline_hint_for_question_avoids_irrelevant_topic_fallback():
    summary = {
        "recurring_topics": [{"topic": "отношения", "count": 8}],
        "recurring_question_signals": [{"signal": "отношения", "label": "отношения", "count": 8}],
        "last_changes": {},
    }
    out = build_inline_hint_for_question(summary, "Сдам ли я экзамен?")
    assert out == ""


def test_build_inline_hint_for_question_sensitive_returns_empty():
    summary = {"recurring_topics": [{"topic": "здоровье", "count": 4}]}
    out = build_inline_hint_for_question(summary, "Почему у меня болит живот?")
    assert out == ""


def test_is_resolvable_followup_context_blocks_sensitive_topics():
    assert is_resolvable_followup_context("экзамен") is True
    assert is_resolvable_followup_context("отношения") is True
    assert is_resolvable_followup_context("здоровье") is False
    assert is_resolvable_followup_context("смерть") is False


def test_card_of_day_payload_and_reversed_inference():
    cards = build_card_of_day_cards_payload(card_index=10, card_name="Колесо Фортуны", is_reversed=True)
    assert len(cards) == 1
    assert cards[0]["position"] == "card_day"
    assert cards[0]["is_reversed"] is True
    assert infer_reversed_from_text("Перевернутый Паж Мечей указывает на...") is True
    assert infer_reversed_from_text("Прямая карта: сила воли") is False


def test_extractor_sets_risk_tags_and_primary_card():
    payload = build_memory_event_payload(
        source_kind="reading",
        source_id=101,
        topic="other",
        spread_type="three_cards",
        question="У мужа болит живот, это серьезно?",
        cards=[
            {
                "position": "past",
                "title": "Прошлое",
                "card_index": 12,
                "card_name": "Повешенный",
                "is_reversed": False,
                "meaning": "",
            }
        ],
        description="Тест",
    )
    assert payload["primary_card"] == "Повешенный"
    assert "risk:medical" in payload["tags"]


def test_cards_delta_hint_highlights_same_card_same_orientation():
    out = _build_cards_delta_hint(
        current_cards=[
            {"card_name": "Башня", "is_reversed": False},
            {"card_name": "Солнце", "is_reversed": False},
        ],
        previous_cards=[
            {"card_name": "Башня", "is_reversed": False},
            {"card_name": "Луна", "is_reversed": True},
        ],
    )
    assert "снова выходит та же карта" in out.lower()
    assert "башня" in out.lower()


def test_cards_delta_hint_highlights_orientation_change():
    out = _build_cards_delta_hint(
        current_cards=[
            {"card_name": "Луна", "is_reversed": True},
        ],
        previous_cards=[
            {"card_name": "Луна", "is_reversed": False},
        ],
    )
    low = out.lower()
    assert "ориентация изменилась" in low
    assert "было прямая" in low
    assert "сейчас перевёрнутая" in low


def test_semantic_signature_detects_relocation_across_wording():
    sig = _semantic_signature(question="Хочу переехать в другую страну, как лучше сделать?", topic="other")
    assert "переезд" in set(sig.get("concepts") or set())
    assert "переезд" in set(sig.get("tokens") or set())


def test_semantic_signature_detects_relocation_for_future_tense_wording():
    sig = _semantic_signature(question="Если мы переедем в Португалию, что будет?", topic="other")
    assert "переезд" in set(sig.get("concepts") or set())
    assert "переезд" in set(sig.get("tokens") or set())


def test_then_vs_now_practical_line_avoids_generic_template_phrase():
    previous_event = SimpleNamespace(
        cards=[{"card_name": "Сила", "is_reversed": False}],
    )
    lines = _build_then_vs_now_lines(
        current_question="Хочу переехать в другую страну",
        current_cards=[{"card_name": "Сила", "is_reversed": False}],
        previous_event=previous_event,
        matched_concepts=["переезд"],
        matched_tokens=["переезд"],
    )
    low = " ".join(lines).lower()
    assert "сравните текущее решение с прошлым" not in low
    assert "точка развилки" not in low
    assert "карта «сила»" in low


def test_runtime_prompt_context_uses_semantic_match_and_then_vs_now(monkeypatch):
    import features.memory.service as memory_service_module

    unrelated = SimpleNamespace(
        question="Вернётся ли бывший в отношения?",
        topic="relations",
        cards=[{"card_name": "Влюбленные", "is_reversed": False}],
        summary={
            "capsule": "отношения и динамика",
            "semantic_tokens": ["отнош"],
            "semantic_concepts": ["отношения"],
            "description_excerpt": "Тема отношений.",
        },
        source_kind="reading",
    )
    relevant = SimpleNamespace(
        question="Стоит ли мне переезжать в Португалию?",
        topic="other",
        cards=[{"card_name": "Колесница", "is_reversed": False}],
        summary={
            "capsule": "переезд и смена места",
            "semantic_tokens": ["переезд", "португали"],
            "semantic_concepts": ["переезд"],
            "description_excerpt": "Тогда вектор был про быстрый переезд, но с организационными рисками.",
            "micro_context": "Запрос: стоит ли переезжать в Португалию. Итог тогда: был шанс на переезд.",
            "cards_brief": "Колесница (прямая)",
        },
        source_kind="reading",
    )

    async def fake_list_recent_events(db, *, user_id, days=90, limit=240):
        _ = (db, user_id, days, limit)
        return [unrelated, relevant]

    monkeypatch.setattr(memory_service_module.repository, "list_recent_events", fake_list_recent_events)

    out = asyncio.run(
        build_runtime_prompt_context(
            None,
            user_id=101,
            question="Хочу переехать в другую страну, рассматриваю Португалию.",
            current_cards=[{"card_name": "Колесница", "is_reversed": True}],
            days=90,
        )
    )

    low = out.lower()
    assert "прошлый похожий вопрос" in low
    assert "переезжать в португалию" in low
    assert "сравнение (тогда vs сейчас)" in low
    assert "отношения" not in low
    assert "другое" not in low
