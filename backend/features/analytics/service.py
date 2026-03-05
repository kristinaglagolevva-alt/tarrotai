from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import CardOfDay, PaymentTransaction, Reading, User


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


async def _load_activity_days(
    db: AsyncSession,
    *,
    since: datetime,
) -> Dict[int, set]:
    by_user: Dict[int, set] = defaultdict(set)

    readings_q = await db.execute(
        select(Reading.user_id, Reading.created_at).where(Reading.created_at >= since)
    )
    for user_id, created_at in readings_q.all():
        if user_id is None or created_at is None:
            continue
        by_user[int(user_id)].add(created_at.date())

    card_day_q = await db.execute(
        select(CardOfDay.user_id, CardOfDay.created_at).where(CardOfDay.created_at >= since)
    )
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
    now: Optional[datetime] = None,
) -> List[RetentionCalc]:
    now_utc = now or _utc_now()
    today = now_utc.date()
    max_day = max(days) if days else 30
    since = now_utc - timedelta(days=max(lookback_days, max_day + 2))

    users_q = await db.execute(
        select(User.id, User.created_at).where(User.created_at >= since)
    )
    user_created: Dict[int, datetime] = {
        int(user_id): created_at
        for user_id, created_at in users_q.all()
        if user_id is not None and created_at is not None
    }

    activity_by_user = await _load_activity_days(db, since=since)

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
    now: Optional[datetime] = None,
) -> Dict[str, float | int]:
    now_utc = now or _utc_now()
    since = now_utc - timedelta(days=max(lookback_days, window_days + 7))

    readings_q = await db.execute(
        select(Reading.user_id, Reading.created_at)
        .where(Reading.created_at >= since)
        .order_by(Reading.user_id.asc(), Reading.created_at.asc())
    )
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
    payments_q = await db.execute(
        select(PaymentTransaction.user_id, PaymentTransaction.created_at)
        .where(
            PaymentTransaction.user_id.in_(user_ids),
            PaymentTransaction.kind == "subscription",
            PaymentTransaction.refunded_at.is_(None),
            PaymentTransaction.created_at >= since,
        )
        .order_by(PaymentTransaction.user_id.asc(), PaymentTransaction.created_at.asc())
    )

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
