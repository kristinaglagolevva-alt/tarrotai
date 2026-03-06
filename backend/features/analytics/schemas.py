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


class TrialToMonthOut(BaseModel):
    trial_product_code: str
    month_product_codes: list[str]
    lookback_days: int
    conversion_window_days: int
    trial_users: int
    trial_purchases: int
    converted_users: int
    conversion_rate: float


class UnitEconomicsOut(BaseModel):
    period_days: int
    revenue_uzs: int
    paid_users: int
    arppu_uzs: float
    new_paid_users: int
    ad_spend_uzs: int
    cac_uzs: float
    payback_months: float
    payback_days: float


class GrowthOut(BaseModel):
    generated_at: datetime
    trial_to_month: TrialToMonthOut
    unit_economics: UnitEconomicsOut
    notes: list[str]
