from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel


class RetentionPoint(BaseModel):
    day: int
    eligible_users: int
    retained_users: int
    retention_rate: float


class ConversionAfterFreeOut(BaseModel):
    free_limit: int
    window_days: int
    users_hit_limit: int
    users_converted: int
    conversion_rate: float


class CardDayReturnsOut(BaseModel):
    window_days: int
    active_users: int
    returning_users: int
    return_share: float


class KpiOut(BaseModel):
    generated_at: datetime
    lookback_days: int
    retention: list[RetentionPoint]
    readings_total_last_7d: int
    readings_users_last_7d: int
    readings_avg_per_user_week: float
    conversion_after_free: ConversionAfterFreeOut
    card_day_returns: CardDayReturnsOut
    definitions: dict[str, str]
