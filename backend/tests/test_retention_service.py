from features.retention.service import _build_nudge_text, _build_trigger_hint


def test_unfinished_cycle_resolvable_hint_has_resolution_question():
    summary = {
        "recurring_question_signals": [{"label": "экзамен", "signal": "экзамен", "count": 3}],
        "recurring_topics": [],
        "last_changes": {},
    }
    out = _build_trigger_hint(trigger="unfinished_cycle", summary=summary, events=[])
    assert "экзамен" in out.lower()
    assert "изменилась" in out.lower() or "разреш" in out.lower()


def test_unfinished_cycle_sensitive_hint_has_no_resolution_question():
    summary = {
        "recurring_question_signals": [{"label": "здоровье", "signal": "здоровье", "count": 2}],
        "recurring_topics": [],
        "last_changes": {},
    }
    out = _build_trigger_hint(trigger="unfinished_cycle", summary=summary, events=[])
    assert "разрешилась ли ситуация" not in out.lower()


def test_nudge_text_contains_opt_out_line():
    out = _build_nudge_text(hint="Проверим динамику?")
    assert "Отключить такие напоминания" in out
