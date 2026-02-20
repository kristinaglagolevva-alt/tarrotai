# tarot_deck.py
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

BASE_DIR = Path(__file__).resolve().parent
DECK_JSON = BASE_DIR / "tarot_cards.json"

def load_deck() -> List[Dict[str, Any]]:
    if not DECK_JSON.exists():
        raise RuntimeError(f"Missing tarot deck file: {DECK_JSON}")

    data = json.loads(DECK_JSON.read_text(encoding="utf-8"))
    if not isinstance(data, list) or len(data) < 78:
        raise RuntimeError("tarot_cards.json must be a list of 78 cards")

    # нормализуем и гарантируем ключи
    deck: List[Dict[str, Any]] = []
    for c in data:
        deck.append({
            "id": int(c["id"]),
            "name": str(c["name"]),
            "upright_meaning": str(c.get("upright_meaning") or ""),
            "reversed_meaning": str(c.get("reversed_meaning") or ""),
        })

    # важно: порядок по id (0..77) = индекс колоды
    deck.sort(key=lambda x: x["id"])
    return deck

TAROT_DECK = load_deck()
DECK_SIZE = len(TAROT_DECK)

def get_card_by_index(idx: int) -> Dict[str, Any]:
    idx = int(idx)
    idx = max(0, min(DECK_SIZE - 1, idx))
    return TAROT_DECK[idx]
