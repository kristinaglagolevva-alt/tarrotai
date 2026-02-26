from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List
from pydantic import BaseModel, Field


class MemorySummaryOut(BaseModel):
    recurring_topics: List[Dict[str, Any]] = Field(default_factory=list)
    repeated_cards: List[Dict[str, Any]] = Field(default_factory=list)
    cycle_hints: List[str] = Field(default_factory=list)
    last_changes: Dict[str, Any] = Field(default_factory=dict)


class MemoryEventIn(BaseModel):
    source_kind: str
    source_id: int | None = None
    topic: str = "other"
    spread_type: str = ""
    question: str = ""
    cards: List[Dict[str, Any]] = Field(default_factory=list)
    description: str = ""


class MemoryContextHint(BaseModel):
    text: str = ""
    generated_at: datetime
