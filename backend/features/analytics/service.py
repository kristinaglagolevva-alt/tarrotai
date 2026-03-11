from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    BotOpenUser,
    CardOfDay,
    ClickOrder,
    PaymentTransaction,
    Reading,
    SbpOrder,
    User,
    VisionEscalationCounter,
)


@dataclass
class RetentionCalc:
    day: int
    eligible_users: int
    retained_users: int

    @property
    def retention_rate(self) -> float:
        if self.eligible_users <= 0:
            return 0.0
        return round(float(self.retained_users) / float(self.eligible_users), 4)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _safe_rate(numer: int, denom: int) -> float:
    if denom <= 0:
        return 0.0
    return round(float(numer) / float(denom), 4)


def _currency_minor_divisor(code: str) -> int:
    up = str(code or "").strip().upper()
    # In our billing DB RUB amounts are stored in kopeks.
    if up in {"RUB", "USD", "EUR"}:
        return 100
    # UZS is stored in whole sums in current integrations.
    return 1


def _to_major_amount(*, currency: str, raw_amount: int) -> int:
    divisor = _currency_minor_divisor(currency)
    if divisor <= 1:
        return int(raw_amount or 0)
    return int(round(float(raw_amount or 0) / float(divisor)))


def _payment_is_test_expr():
    provider_ref = func.coalesce(PaymentTransaction.provider_payment_charge_id, "")
    invoice_payload = func.coalesce(PaymentTransaction.invoice_payload, "")
    return or_(
        provider_ref.ilike("manual:%"),
        provider_ref.ilike("test:%"),
        provider_ref.ilike("sandbox:%"),
        invoice_payload.ilike("manual:%"),
        invoice_payload.ilike("test:%"),
        invoice_payload.ilike("sandbox:%"),
    )


async def _load_activity_days(
    db: AsyncSession,
    *,
    since: datetime,
    exclude_user_ids: Optional[Sequence[int]] = None,
) -> Dict[int, set]:
    by_user: Dict[int, set] = defaultdict(set)
    excluded = {int(x) for x in (exclude_user_ids or []) if int(x) > 0}

    readings_stmt = select(Reading.user_id, Reading.created_at).where(Reading.created_at >= since)
    if excluded:
        readings_stmt = readings_stmt.where(~Reading.user_id.in_(sorted(excluded)))
    readings_q = await db.execute(readings_stmt)
    for user_id, created_at in readings_q.all():
        if user_id is None or created_at is None:
            continue
        by_user[int(user_id)].add(created_at.date())

    card_day_stmt = select(CardOfDay.user_id, CardOfDay.created_at).where(CardOfDay.created_at >= since)
    if excluded:
        card_day_stmt = card_day_stmt.where(~CardOfDay.user_id.in_(sorted(excluded)))
    card_day_q = await db.execute(card_day_stmt)
    for user_id, created_at in card_day_q.all():
        if user_id is None or created_at is None:
            continue
        by_user[int(user_id)].add(created_at.date())

    return by_user


async def calculate_retention(
    db: AsyncSession,
    *,
    days: Sequence[int] = (1, 7, 30),
    lookback_days: int = 120,
    exclude_user_ids: Optional[Sequence[int]] = None,
    now: Optional[datetime] = None,
) -> List[RetentionCalc]:
    now_utc = now or _utc_now()
    today = now_utc.date()
    max_day = max(days) if days else 30
    since = now_utc - timedelta(days=max(lookback_days, max_day + 2))
    excluded = {int(x) for x in (exclude_user_ids or []) if int(x) > 0}

    users_stmt = select(User.id, User.created_at).where(User.created_at >= since)
    if excluded:
        users_stmt = users_stmt.where(~User.id.in_(sorted(excluded)))
    users_q = await db.execute(users_stmt)
    user_created: Dict[int, datetime] = {
        int(user_id): created_at
        for user_id, created_at in users_q.all()
        if user_id is not None and created_at is not None
    }

    activity_by_user = await _load_activity_days(
        db,
        since=since,
        exclude_user_ids=sorted(excluded),
    )

    out: List[RetentionCalc] = []
    for day in days:
        eligible = 0
        retained = 0
        for user_id, created_at in user_created.items():
            cohort_day = created_at.date()
            if cohort_day > (today - timedelta(days=day)):
                continue
            eligible += 1
            target_day = cohort_day + timedelta(days=day)
            if target_day in activity_by_user.get(user_id, set()):
                retained += 1
        out.append(RetentionCalc(day=int(day), eligible_users=eligible, retained_users=retained))

    return out


async def calculate_readings_avg_per_user_week(
    db: AsyncSession,
    *,
    now: Optional[datetime] = None,
) -> Tuple[int, int, float]:
    now_utc = now or _utc_now()
    since = now_utc - timedelta(days=7)

    q = await db.execute(
        select(
            func.count(Reading.id),
            func.count(func.distinct(Reading.user_id)),
        ).where(Reading.created_at >= since)
    )
    total_readings, total_users = q.one()
    readings_total = int(total_readings or 0)
    users_total = int(total_users or 0)
    avg = _safe_rate(readings_total, users_total)
    return readings_total, users_total, avg


async def calculate_conversion_after_free(
    db: AsyncSession,
    *,
    free_limit: int,
    window_days: int = 30,
    lookback_days: int = 120,
    exclude_user_ids: Optional[Sequence[int]] = None,
    exclude_test_payments: bool = False,
    now: Optional[datetime] = None,
) -> Dict[str, float | int]:
    now_utc = now or _utc_now()
    since = now_utc - timedelta(days=max(lookback_days, window_days + 7))
    excluded = {int(x) for x in (exclude_user_ids or []) if int(x) > 0}

    readings_stmt = (
        select(Reading.user_id, Reading.created_at)
        .where(Reading.created_at >= since)
        .order_by(Reading.user_id.asc(), Reading.created_at.asc())
    )
    if excluded:
        readings_stmt = readings_stmt.where(~Reading.user_id.in_(sorted(excluded)))
    readings_q = await db.execute(readings_stmt)
    counters: Dict[Tuple[int, int, int], int] = {}
    first_hit_by_user: Dict[int, datetime] = {}

    for user_id, created_at in readings_q.all():
        if user_id is None or created_at is None:
            continue
        uid = int(user_id)
        ym_key = (uid, int(created_at.year), int(created_at.month))
        next_count = int(counters.get(ym_key, 0)) + 1
        counters[ym_key] = next_count
        if next_count == int(free_limit):
            prev = first_hit_by_user.get(uid)
            if prev is None or created_at < prev:
                first_hit_by_user[uid] = created_at

    users_hit_limit = len(first_hit_by_user)
    if users_hit_limit <= 0:
        return {
            "free_limit": int(free_limit),
            "window_days": int(window_days),
            "users_hit_limit": 0,
            "users_converted": 0,
            "conversion_rate": 0.0,
        }

    user_ids = sorted(first_hit_by_user.keys())
    payments_stmt = (
        select(PaymentTransaction.user_id, PaymentTransaction.created_at)
        .where(
            PaymentTransaction.user_id.in_(user_ids),
            PaymentTransaction.kind == "subscription",
            PaymentTransaction.refunded_at.is_(None),
            PaymentTransaction.created_at >= since,
        )
        .order_by(PaymentTransaction.user_id.asc(), PaymentTransaction.created_at.asc())
    )
    if exclude_test_payments:
        payments_stmt = payments_stmt.where(~_payment_is_test_expr())
    if excluded:
        payments_stmt = payments_stmt.where(~PaymentTransaction.user_id.in_(sorted(excluded)))
    payments_q = await db.execute(payments_stmt)

    payments_by_user: Dict[int, List[datetime]] = defaultdict(list)
    for user_id, created_at in payments_q.all():
        if user_id is None or created_at is None:
            continue
        payments_by_user[int(user_id)].append(created_at)

    converted = 0
    window = timedelta(days=window_days)
    for uid, hit_at in first_hit_by_user.items():
        for pay_at in payments_by_user.get(uid, []):
            if pay_at >= hit_at and pay_at <= hit_at + window:
                converted += 1
                break

    return {
        "free_limit": int(free_limit),
        "window_days": int(window_days),
        "users_hit_limit": int(users_hit_limit),
        "users_converted": int(converted),
        "conversion_rate": _safe_rate(converted, users_hit_limit),
    }


async def calculate_card_day_return_share(
    db: AsyncSession,
    *,
    window_days: int = 30,
    now: Optional[datetime] = None,
) -> Dict[str, float | int]:
    now_utc = now or _utc_now()
    since = now_utc - timedelta(days=max(1, window_days))

    q = await db.execute(
        select(CardOfDay.user_id, CardOfDay.day_key).where(CardOfDay.created_at >= since)
    )

    days_by_user: Dict[int, set] = defaultdict(set)
    for user_id, day_key in q.all():
        if user_id is None:
            continue
        uid = int(user_id)
        if day_key:
            days_by_user[uid].add(str(day_key))

    active_users = len(days_by_user)
    returning_users = sum(1 for days in days_by_user.values() if len(days) >= 2)

    return {
        "window_days": int(window_days),
        "active_users": int(active_users),
        "returning_users": int(returning_users),
        "return_share": _safe_rate(returning_users, active_users),
    }


async def build_kpi_snapshot(
    db: AsyncSession,
    *,
    free_limit: int,
    now: Optional[datetime] = None,
) -> dict:
    now_utc = now or _utc_now()

    retention = await calculate_retention(db, days=(1, 7, 30), lookback_days=120, now=now_utc)
    readings_total, readings_users, readings_avg = await calculate_readings_avg_per_user_week(db, now=now_utc)
    conversion = await calculate_conversion_after_free(
        db,
        free_limit=max(1, int(free_limit)),
        window_days=30,
        lookback_days=120,
        now=now_utc,
    )
    card_day_returns = await calculate_card_day_return_share(db, window_days=30, now=now_utc)

    return {
        "generated_at": now_utc,
        "lookback_days": 120,
        "retention": [
            {
                "day": int(p.day),
                "eligible_users": int(p.eligible_users),
                "retained_users": int(p.retained_users),
                "retention_rate": float(p.retention_rate),
            }
            for p in retention
        ],
        "readings_total_last_7d": int(readings_total),
        "readings_users_last_7d": int(readings_users),
        "readings_avg_per_user_week": float(readings_avg),
        "conversion_after_free": conversion,
        "card_day_returns": card_day_returns,
        "definitions": {
            "retention": "D1/D7/D30: доля пользователей с активностью (расклад или карта дня) ровно в N-й день после даты регистрации.",
            "avg_readings_week": "Среднее число раскладов (без карты дня) на активного пользователя за последние 7 дней.",
            "conversion_after_free": "Доля пользователей, достигших 5 бесплатных раскладов в месяце и оплативших подписку в течение 30 дней после этого.",
            "card_day_returns": "Доля пользователей, вернувшихся к 'Карте дня' хотя бы на 2 разных дня за последние 30 дней.",
        },
    }


async def build_growth_snapshot(
    db: AsyncSession,
    *,
    lookback_days: int = 30,
    conversion_window_days: int = 30,
    trial_product_code: str = "sub_week_click",
    month_product_codes: Optional[Sequence[str]] = None,
    ad_spend_uzs: int = 0,
    ad_spend_rub: int = 0,
    ad_spend_by_currency: Optional[Dict[str, int]] = None,
    free_limit: int = 5,
    exclude_user_ids: Optional[Sequence[int]] = None,
    exclude_test_payments: bool = False,
    now: Optional[datetime] = None,
) -> dict:
    now_utc = now or _utc_now()
    period_days = max(1, int(lookback_days))
    conversion_days = max(1, int(conversion_window_days))
    since = now_utc - timedelta(days=period_days)
    safe_free_limit = max(1, int(free_limit))
    excluded_user_ids = sorted({int(x) for x in (exclude_user_ids or []) if int(x) > 0})
    safe_trial_code = str(trial_product_code or "sub_week_click").strip().lower()
    safe_month_codes = [
        str(code or "").strip().lower()
        for code in (month_product_codes or ("sub_month_click",))
        if str(code or "").strip()
    ]
    if not safe_month_codes:
        safe_month_codes = ["sub_month_click"]

    excluded_bot_tg_ids: List[int] = []
    if excluded_user_ids:
        excluded_tg_q = await db.execute(
            select(User.telegram_id).where(User.id.in_(excluded_user_ids))
        )
        excluded_bot_tg_ids = sorted(
            {
                int(tg_id)
                for (tg_id,) in excluded_tg_q.all()
                if tg_id is not None and int(tg_id) > 0
            }
        )

    payment_non_refunded_filters = [
        PaymentTransaction.kind == "subscription",
        PaymentTransaction.refunded_at.is_(None),
    ]
    payment_any_subscription_filters = [
        PaymentTransaction.kind == "subscription",
    ]
    if exclude_test_payments:
        non_test_expr = ~_payment_is_test_expr()
        payment_non_refunded_filters.append(non_test_expr)
        payment_any_subscription_filters.append(non_test_expr)

    safe_ad_spend_by_currency: Dict[str, int] = {
        "UZS": max(0, int(ad_spend_uzs or 0)),
        "RUB": max(0, int(ad_spend_rub or 0)),
    }
    for raw_currency, raw_value in (ad_spend_by_currency or {}).items():
        currency = str(raw_currency or "").strip().upper()
        if not currency:
            continue
        safe_ad_spend_by_currency[currency] = max(0, int(raw_value or 0))

    trial_q = await db.execute(
        select(PaymentTransaction.user_id, PaymentTransaction.created_at).where(
            *payment_non_refunded_filters,
            PaymentTransaction.product_code == safe_trial_code,
            PaymentTransaction.created_at >= since,
            *(
                [~PaymentTransaction.user_id.in_(excluded_user_ids)]
                if excluded_user_ids
                else []
            ),
        )
        .order_by(PaymentTransaction.user_id.asc(), PaymentTransaction.created_at.asc())
    )
    first_trial_by_user: Dict[int, datetime] = {}
    trial_purchases = 0
    for user_id, created_at in trial_q.all():
        if user_id is None or created_at is None:
            continue
        uid = int(user_id)
        trial_purchases += 1
        prev = first_trial_by_user.get(uid)
        if prev is None or created_at < prev:
            first_trial_by_user[uid] = created_at

    trial_users = len(first_trial_by_user)
    converted_users = 0
    if trial_users > 0:
        trial_user_ids = sorted(first_trial_by_user.keys())
        month_q = await db.execute(
            select(PaymentTransaction.user_id, PaymentTransaction.created_at)
            .where(
                PaymentTransaction.user_id.in_(trial_user_ids),
                *payment_non_refunded_filters,
                PaymentTransaction.product_code.in_(safe_month_codes),
                *(
                    [~PaymentTransaction.user_id.in_(excluded_user_ids)]
                    if excluded_user_ids
                    else []
                ),
            )
            .order_by(PaymentTransaction.user_id.asc(), PaymentTransaction.created_at.asc())
        )
        month_by_user: Dict[int, List[datetime]] = defaultdict(list)
        for user_id, created_at in month_q.all():
            if user_id is None or created_at is None:
                continue
            month_by_user[int(user_id)].append(created_at)

        window = timedelta(days=conversion_days)
        for uid, trial_at in first_trial_by_user.items():
            for month_at in month_by_user.get(uid, []):
                if month_at >= trial_at and month_at <= trial_at + window:
                    converted_users += 1
                    break

    conversion_rate = _safe_rate(converted_users, trial_users)

    revenue_q = await db.execute(
        select(
            PaymentTransaction.currency,
            func.coalesce(func.sum(PaymentTransaction.amount), 0),
            func.count(func.distinct(PaymentTransaction.user_id)),
        ).where(
            *payment_non_refunded_filters,
            PaymentTransaction.created_at >= since,
            *(
                [~PaymentTransaction.user_id.in_(excluded_user_ids)]
                if excluded_user_ids
                else []
            ),
        ).group_by(PaymentTransaction.currency)
    )
    revenue_by_currency: Dict[str, int] = {}
    paid_users_by_currency: Dict[str, int] = {}
    for currency, revenue_raw, paid_users_raw in revenue_q.all():
        code = str(currency or "").strip().upper() or "UNK"
        revenue_by_currency[code] = _to_major_amount(
            currency=code,
            raw_amount=int(revenue_raw or 0),
        )
        paid_users_by_currency[code] = int(paid_users_raw or 0)

    first_paid_sq = (
        select(
            PaymentTransaction.user_id.label("user_id"),
            PaymentTransaction.currency.label("currency"),
            func.min(PaymentTransaction.created_at).label("first_paid_at"),
        )
        .where(
            *payment_non_refunded_filters,
            *(
                [~PaymentTransaction.user_id.in_(excluded_user_ids)]
                if excluded_user_ids
                else []
            ),
        )
        .group_by(PaymentTransaction.user_id, PaymentTransaction.currency)
        .subquery()
    )
    new_paid_q = await db.execute(
        select(
            first_paid_sq.c.currency,
            func.count(first_paid_sq.c.user_id),
        )
        .where(first_paid_sq.c.first_paid_at >= since)
        .group_by(first_paid_sq.c.currency)
    )
    new_paid_by_currency: Dict[str, int] = {}
    for currency, cnt in new_paid_q.all():
        code = str(currency or "").strip().upper() or "UNK"
        new_paid_by_currency[code] = int(cnt or 0)

    ordered_currencies: List[str] = []
    for code in ("UZS", "RUB"):
        if (
            code in revenue_by_currency
            or code in new_paid_by_currency
            or int(safe_ad_spend_by_currency.get(code) or 0) > 0
        ):
            ordered_currencies.append(code)
    extra_codes = sorted(
        {
            *revenue_by_currency.keys(),
            *new_paid_by_currency.keys(),
            *safe_ad_spend_by_currency.keys(),
        }
        - set(ordered_currencies)
    )
    ordered_currencies.extend(extra_codes)
    if not ordered_currencies:
        ordered_currencies = ["UZS", "RUB"]

    unit_economics_by_currency: List[Dict[str, float | int | str]] = []
    for code in ordered_currencies:
        revenue = int(revenue_by_currency.get(code, 0) or 0)
        paid_users = int(paid_users_by_currency.get(code, 0) or 0)
        arppu = round(float(revenue) / float(paid_users), 2) if paid_users > 0 else 0.0
        new_paid_users = int(new_paid_by_currency.get(code, 0) or 0)
        ad_spend = int(safe_ad_spend_by_currency.get(code, 0) or 0)
        cac = round(float(ad_spend) / float(new_paid_users), 2) if new_paid_users > 0 else 0.0
        payback_months = round(float(cac) / float(arppu), 2) if arppu > 0 else 0.0
        payback_days = round(float(payback_months) * 30.0, 1) if payback_months > 0 else 0.0
        unit_economics_by_currency.append(
            {
                "currency": code,
                "period_days": period_days,
                "revenue": revenue,
                "paid_users": paid_users,
                "arppu": arppu,
                "new_paid_users": new_paid_users,
                "ad_spend": ad_spend,
                "cac": cac,
                "payback_months": payback_months,
                "payback_days": payback_days,
            }
        )

    uzs_metrics = next((row for row in unit_economics_by_currency if row.get("currency") == "UZS"), None)
    if not uzs_metrics:
        uzs_metrics = {
            "period_days": period_days,
            "revenue": 0,
            "paid_users": 0,
            "arppu": 0.0,
            "new_paid_users": 0,
            "ad_spend": int(safe_ad_spend_by_currency.get("UZS", 0) or 0),
            "cac": 0.0,
            "payback_months": 0.0,
            "payback_days": 0.0,
        }

    bot_opened_users = 0
    bot_opened_users_total = 0
    bot_open_events_total = 0
    try:
        bot_open_new_q = await db.execute(
            select(func.count(BotOpenUser.id)).where(
                BotOpenUser.first_opened_at >= since,
                *(
                    [~BotOpenUser.telegram_id.in_(excluded_bot_tg_ids)]
                    if excluded_bot_tg_ids
                    else []
                ),
            )
        )
        bot_open_total_q = await db.execute(
            select(func.count(BotOpenUser.id)).where(
                *(
                    [~BotOpenUser.telegram_id.in_(excluded_bot_tg_ids)]
                    if excluded_bot_tg_ids
                    else []
                ),
            )
        )
        bot_open_events_q = await db.execute(
            select(func.coalesce(func.sum(BotOpenUser.opens_count), 0)).where(
                *(
                    [~BotOpenUser.telegram_id.in_(excluded_bot_tg_ids)]
                    if excluded_bot_tg_ids
                    else []
                ),
            )
        )
        bot_opened_users = int(bot_open_new_q.scalar() or 0)
        bot_opened_users_total = int(bot_open_total_q.scalar() or 0)
        bot_open_events_total = int(bot_open_events_q.scalar() or 0)
    except Exception:
        # Fail-open for analytics: dashboard still works even if bot_open_users table is not ready yet.
        bot_opened_users = 0
        bot_opened_users_total = 0
        bot_open_events_total = 0

    new_users_q = await db.execute(
        select(User.id).where(
            User.created_at >= since,
            *([~User.id.in_(excluded_user_ids)] if excluded_user_ids else []),
        )
    )
    new_user_ids = [int(uid) for (uid,) in new_users_q.all() if uid is not None]
    new_users = len(new_user_ids)
    activated_users = 0
    photo_users_in_new = 0
    reached_free_limit_users = 0
    trial_users_in_new = 0
    paid_users_in_new = 0
    if new_user_ids:
        readings_count_q = await db.execute(
            select(
                Reading.user_id,
                func.count(Reading.id),
            )
            .where(
                Reading.user_id.in_(new_user_ids),
                Reading.created_at >= since,
                *([~Reading.user_id.in_(excluded_user_ids)] if excluded_user_ids else []),
            )
            .group_by(Reading.user_id)
        )
        readings_by_new_user: Dict[int, int] = {}
        for uid, cnt in readings_count_q.all():
            if uid is None:
                continue
            readings_by_new_user[int(uid)] = int(cnt or 0)
        activated_users = len(readings_by_new_user)
        reached_free_limit_users = sum(
            1 for cnt in readings_by_new_user.values() if int(cnt) >= safe_free_limit
        )

        photo_users_q = await db.execute(
            select(func.count(func.distinct(Reading.user_id))).where(
                Reading.user_id.in_(new_user_ids),
                Reading.created_at >= since,
                Reading.spread_type == "photo_analysis",
                *([~Reading.user_id.in_(excluded_user_ids)] if excluded_user_ids else []),
            )
        )
        photo_users_in_new = int(photo_users_q.scalar() or 0)

        trial_in_new_q = await db.execute(
            select(func.count(func.distinct(PaymentTransaction.user_id))).where(
                PaymentTransaction.user_id.in_(new_user_ids),
                *payment_non_refunded_filters,
                PaymentTransaction.product_code == safe_trial_code,
                PaymentTransaction.created_at >= since,
                *(
                    [~PaymentTransaction.user_id.in_(excluded_user_ids)]
                    if excluded_user_ids
                    else []
                ),
            )
        )
        trial_users_in_new = int(trial_in_new_q.scalar() or 0)

        paid_in_new_q = await db.execute(
            select(func.count(func.distinct(PaymentTransaction.user_id))).where(
                PaymentTransaction.user_id.in_(new_user_ids),
                *payment_non_refunded_filters,
                PaymentTransaction.created_at >= since,
                *(
                    [~PaymentTransaction.user_id.in_(excluded_user_ids)]
                    if excluded_user_ids
                    else []
                ),
            )
        )
        paid_users_in_new = int(paid_in_new_q.scalar() or 0)

    photo_q = await db.execute(
        select(Reading.user_id, Reading.created_at)
        .where(
            Reading.spread_type == "photo_analysis",
            Reading.created_at >= since,
            *([~Reading.user_id.in_(excluded_user_ids)] if excluded_user_ids else []),
        )
        .order_by(Reading.user_id.asc(), Reading.created_at.asc())
    )
    first_photo_by_user: Dict[int, datetime] = {}
    photo_analyses_completed = 0
    for user_id, created_at in photo_q.all():
        if user_id is None or created_at is None:
            continue
        uid = int(user_id)
        photo_analyses_completed += 1
        prev = first_photo_by_user.get(uid)
        if prev is None or created_at < prev:
            first_photo_by_user[uid] = created_at

    photo_users = len(first_photo_by_user)
    photo_trial_converted = 0
    photo_paid_converted = 0
    if photo_users > 0:
        photo_user_ids = sorted(first_photo_by_user.keys())
        payments_q = await db.execute(
            select(PaymentTransaction.user_id, PaymentTransaction.created_at, PaymentTransaction.product_code)
            .where(
                PaymentTransaction.user_id.in_(photo_user_ids),
                *payment_non_refunded_filters,
                *(
                    [~PaymentTransaction.user_id.in_(excluded_user_ids)]
                    if excluded_user_ids
                    else []
                ),
            )
            .order_by(PaymentTransaction.user_id.asc(), PaymentTransaction.created_at.asc())
        )
        payments_by_user: Dict[int, List[Tuple[datetime, str]]] = defaultdict(list)
        for user_id, created_at, product_code in payments_q.all():
            if user_id is None or created_at is None:
                continue
            payments_by_user[int(user_id)].append(
                (created_at, str(product_code or "").strip().lower())
            )

        window = timedelta(days=conversion_days)
        for uid, photo_at in first_photo_by_user.items():
            has_trial = False
            has_paid = False
            for pay_at, code in payments_by_user.get(uid, []):
                if pay_at < photo_at:
                    continue
                if pay_at > (photo_at + window):
                    break
                has_paid = True
                if code == safe_trial_code:
                    has_trial = True
            if has_trial:
                photo_trial_converted += 1
            if has_paid:
                photo_paid_converted += 1

    paywall = await calculate_conversion_after_free(
        db,
        free_limit=safe_free_limit,
        window_days=conversion_days,
        lookback_days=max(period_days, conversion_days + 7),
        exclude_user_ids=excluded_user_ids,
        exclude_test_payments=exclude_test_payments,
        now=now_utc,
    )

    retention_points = await calculate_retention(
        db,
        days=(1, 7, 30),
        lookback_days=max(120, period_days + 31),
        exclude_user_ids=excluded_user_ids,
        now=now_utc,
    )
    retention_map = {int(p.day): float(p.retention_rate) for p in retention_points}

    current_30_since = now_utc - timedelta(days=30)
    prev_30_since = now_utc - timedelta(days=60)
    current_paid_q = await db.execute(
        select(PaymentTransaction.user_id).where(
            *payment_non_refunded_filters,
            PaymentTransaction.created_at >= current_30_since,
            *(
                [~PaymentTransaction.user_id.in_(excluded_user_ids)]
                if excluded_user_ids
                else []
            ),
        )
    )
    prev_paid_q = await db.execute(
        select(PaymentTransaction.user_id).where(
            *payment_non_refunded_filters,
            PaymentTransaction.created_at >= prev_30_since,
            PaymentTransaction.created_at < current_30_since,
            *(
                [~PaymentTransaction.user_id.in_(excluded_user_ids)]
                if excluded_user_ids
                else []
            ),
        )
    )
    current_paid_users_set = {
        int(uid)
        for (uid,) in current_paid_q.all()
        if uid is not None
    }
    prev_paid_users_set = {
        int(uid)
        for (uid,) in prev_paid_q.all()
        if uid is not None
    }
    repeat_paid_users = len(current_paid_users_set.intersection(prev_paid_users_set))

    total_sub_payments_q = await db.execute(
        select(func.count(PaymentTransaction.id)).where(
            *payment_any_subscription_filters,
            PaymentTransaction.created_at >= since,
            *(
                [~PaymentTransaction.user_id.in_(excluded_user_ids)]
                if excluded_user_ids
                else []
            ),
        )
    )
    refunded_sub_payments_q = await db.execute(
        select(func.count(PaymentTransaction.id)).where(
            *payment_any_subscription_filters,
            PaymentTransaction.created_at >= since,
            PaymentTransaction.refunded_at.is_not(None),
            *(
                [~PaymentTransaction.user_id.in_(excluded_user_ids)]
                if excluded_user_ids
                else []
            ),
        )
    )
    total_sub_payments = int(total_sub_payments_q.scalar() or 0)
    refunded_sub_payments = int(refunded_sub_payments_q.scalar() or 0)

    click_total_q = await db.execute(
        select(func.count(ClickOrder.id)).where(
            ClickOrder.created_at >= since,
            *([~ClickOrder.user_id.in_(excluded_user_ids)] if excluded_user_ids else []),
        )
    )
    click_failed_q = await db.execute(
        select(func.count(ClickOrder.id)).where(
            ClickOrder.created_at >= since,
            ClickOrder.status.in_(["cancelled", "canceled"]),
            *([~ClickOrder.user_id.in_(excluded_user_ids)] if excluded_user_ids else []),
        )
    )
    click_total = int(click_total_q.scalar() or 0)
    click_failed = int(click_failed_q.scalar() or 0)

    sbp_total_q = await db.execute(
        select(func.count(SbpOrder.id)).where(
            SbpOrder.created_at >= since,
            *([~SbpOrder.user_id.in_(excluded_user_ids)] if excluded_user_ids else []),
        )
    )
    sbp_failed_q = await db.execute(
        select(func.count(SbpOrder.id)).where(
            SbpOrder.created_at >= since,
            SbpOrder.status.in_(["canceled", "cancelled"]),
            *([~SbpOrder.user_id.in_(excluded_user_ids)] if excluded_user_ids else []),
        )
    )
    sbp_total = int(sbp_total_q.scalar() or 0)
    sbp_failed = int(sbp_failed_q.scalar() or 0)

    vision_since_key = since.date().isoformat()
    vision_until_key = now_utc.date().isoformat()
    vision_q = await db.execute(
        select(
            func.coalesce(func.sum(VisionEscalationCounter.total_requests), 0),
            func.coalesce(func.sum(VisionEscalationCounter.escalated_requests), 0),
        ).where(
            VisionEscalationCounter.counter_key == "photo_analysis",
            VisionEscalationCounter.day_key >= vision_since_key,
            VisionEscalationCounter.day_key <= vision_until_key,
        )
    )
    vision_total_raw, vision_escalated_raw = vision_q.one()
    vision_total = int(vision_total_raw or 0)
    vision_escalated = int(vision_escalated_raw or 0)

    return {
        "generated_at": now_utc,
        "trial_to_month": {
            "trial_product_code": safe_trial_code,
            "month_product_codes": safe_month_codes,
            "lookback_days": period_days,
            "conversion_window_days": conversion_days,
            "trial_users": trial_users,
            "trial_purchases": int(trial_purchases),
            "converted_users": int(converted_users),
            "conversion_rate": conversion_rate,
        },
        "unit_economics": {
            "period_days": period_days,
            "revenue_uzs": int(uzs_metrics["revenue"]),
            "paid_users": int(uzs_metrics["paid_users"]),
            "arppu_uzs": float(uzs_metrics["arppu"]),
            "new_paid_users": int(uzs_metrics["new_paid_users"]),
            "ad_spend_uzs": int(uzs_metrics["ad_spend"]),
            "cac_uzs": float(uzs_metrics["cac"]),
            "payback_months": float(uzs_metrics["payback_months"]),
            "payback_days": float(uzs_metrics["payback_days"]),
        },
        "unit_economics_by_currency": unit_economics_by_currency,
        "activation_funnel": {
            "lookback_days": period_days,
            "bot_opened_users": int(bot_opened_users),
            "bot_opened_users_total": int(bot_opened_users_total),
            "bot_open_events_total": int(bot_open_events_total),
            "new_users": int(new_users),
            "activated_users": int(activated_users),
            "photo_users": int(photo_users_in_new),
            "reached_free_limit_users": int(reached_free_limit_users),
            "trial_users": int(trial_users_in_new),
            "paid_users": int(paid_users_in_new),
            "signup_from_bot_open_rate": _safe_rate(new_users, bot_opened_users),
            "activation_rate": _safe_rate(activated_users, new_users),
            "paid_rate": _safe_rate(paid_users_in_new, new_users),
        },
        "photo_funnel": {
            "lookback_days": period_days,
            "conversion_window_days": conversion_days,
            "photo_users": int(photo_users),
            "photo_analyses_completed": int(photo_analyses_completed),
            "converted_to_trial_users": int(photo_trial_converted),
            "converted_to_paid_users": int(photo_paid_converted),
            "trial_conversion_rate": _safe_rate(photo_trial_converted, photo_users),
            "paid_conversion_rate": _safe_rate(photo_paid_converted, photo_users),
        },
        "paywall_funnel": {
            "free_limit": int(paywall.get("free_limit") or safe_free_limit),
            "window_days": int(paywall.get("window_days") or conversion_days),
            "users_hit_limit": int(paywall.get("users_hit_limit") or 0),
            "users_converted": int(paywall.get("users_converted") or 0),
            "conversion_rate": float(paywall.get("conversion_rate") or 0.0),
        },
        "retention_summary": {
            "d1": float(retention_map.get(1, 0.0)),
            "d7": float(retention_map.get(7, 0.0)),
            "d30": float(retention_map.get(30, 0.0)),
            "prev_paid_users_30d": int(len(prev_paid_users_set)),
            "current_paid_users_30d": int(len(current_paid_users_set)),
            "repeat_paid_users_30d": int(repeat_paid_users),
            "paid_repeat_30d_rate": _safe_rate(repeat_paid_users, len(prev_paid_users_set)),
        },
        "payment_quality": {
            "period_days": period_days,
            "total_subscription_payments": int(total_sub_payments),
            "refunded_payments": int(refunded_sub_payments),
            "refund_rate": _safe_rate(refunded_sub_payments, total_sub_payments),
            "click_orders_total": int(click_total),
            "click_orders_failed": int(click_failed),
            "click_failure_rate": _safe_rate(click_failed, click_total),
            "sbp_orders_total": int(sbp_total),
            "sbp_orders_failed": int(sbp_failed),
            "sbp_failure_rate": _safe_rate(sbp_failed, sbp_total),
        },
        "ai_operations": {
            "period_days": period_days,
            "photo_analyses_completed": int(photo_analyses_completed),
            "vision_requests_tracked": int(vision_total),
            "vision_escalated_requests": int(vision_escalated),
            "vision_escalation_rate": _safe_rate(vision_escalated, vision_total),
        },
        "notes": [
            "trial_to_month: доля пользователей, купивших trial и затем месячный тариф в окне conversion_window_days.",
            "unit_economics_by_currency: ARPPU/CAC/Payback считаются отдельно по каждой валюте.",
            "Суммы в unit_economics_by_currency нормализуются в основные единицы валюты (например, RUB: копейки -> рубли).",
            f"payments_scope: {'production_only' if exclude_test_payments else 'all'}",
            "bot_opened_users: пользователи, впервые открывшие бота через /start в период lookback_days.",
            "activation_funnel: поведение новых пользователей за период lookback_days.",
            "photo_funnel: конверсия пользователей фото-анализа в оплату в окне conversion_window_days.",
            "retention_summary: D1/D7/D30 + repeat paid 30d.",
            f"excluded_user_ids_count: {len(excluded_user_ids)}",
        ],
    }
