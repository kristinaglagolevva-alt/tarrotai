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


class CurrencyUnitEconomicsOut(BaseModel):
    currency: str
    period_days: int
    revenue: int
    paid_users: int
    arppu: float
    new_paid_users: int
    ad_spend: int
    cac: float
    payback_months: float
    payback_days: float


class ActivationFunnelOut(BaseModel):
    lookback_days: int
    bot_opened_users: int
    bot_opened_users_total: int
    bot_open_events_total: int
    new_users: int
    activated_users: int
    photo_users: int
    reached_free_limit_users: int
    trial_users: int
    paid_users: int
    signup_from_bot_open_rate: float
    activation_rate: float
    paid_rate: float


class PhotoFunnelOut(BaseModel):
    lookback_days: int
    conversion_window_days: int
    photo_users: int
    photo_analyses_completed: int
    converted_to_trial_users: int
    converted_to_paid_users: int
    trial_conversion_rate: float
    paid_conversion_rate: float


class PaywallFunnelOut(BaseModel):
    free_limit: int
    window_days: int
    users_hit_limit: int
    users_converted: int
    conversion_rate: float


class RetentionSummaryOut(BaseModel):
    d1: float
    d7: float
    d30: float
    prev_paid_users_30d: int
    current_paid_users_30d: int
    repeat_paid_users_30d: int
    paid_repeat_30d_rate: float


class PaymentQualityOut(BaseModel):
    period_days: int
    total_subscription_payments: int
    refunded_payments: int
    refund_rate: float
    click_orders_total: int
    click_orders_failed: int
    click_failure_rate: float
    sbp_orders_total: int
    sbp_orders_failed: int
    sbp_failure_rate: float


class AIOperationsOut(BaseModel):
    period_days: int
    photo_analyses_completed: int
    vision_requests_tracked: int
    vision_escalated_requests: int
    vision_escalation_rate: float


class GrowthOut(BaseModel):
    generated_at: datetime
    trial_to_month: TrialToMonthOut
    unit_economics: UnitEconomicsOut
    unit_economics_by_currency: list[CurrencyUnitEconomicsOut]
    activation_funnel: ActivationFunnelOut
    photo_funnel: PhotoFunnelOut
    paywall_funnel: PaywallFunnelOut
    retention_summary: RetentionSummaryOut
    payment_quality: PaymentQualityOut
    ai_operations: AIOperationsOut
    notes: list[str]
