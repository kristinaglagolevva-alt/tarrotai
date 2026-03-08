from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import re
import secrets
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from html import escape
from typing import Optional, List, Literal, Dict, Any
from urllib.parse import urlencode, urlsplit, parse_qsl
try:
    from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
except Exception:  # Python 3.8 fallback
    from backports.zoneinfo import ZoneInfo, ZoneInfoNotFoundError  # type: ignore

import httpx
from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func, and_, or_
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv

from db import engine, get_db, Base, SessionLocal
from models import (
    User,
    CardOfDay,
    Reading,
    PaymentTransaction,
    SbpOrder,
    ClickOrder,
    SbpAutopaySubscription,
    SupportTicket,
    SupportTicketMessage,
    UserMemoryEvent,
    UserMemoryProfile,
    RetentionNudgeLog,
    VisionEscalationCounter,
)
from telegram_auth import validate_init_data
from jwt import create_jwt, decode_jwt

from tarot_deck import get_card_by_index, DECK_SIZE
from llm_card_of_day import (
    generate_card_text_llm,
    generate_spread_text_llm,
    generate_photo_analysis_llm,
    generate_photo_followup_llm,
)
from features.common.flags import FEATURE_FLAGS
from features.common.timezone import resolve_tz_name
from features.analytics.schemas import KpiOut, GrowthOut
from features.analytics import service as analytics_service
from features.memory.schemas import MemorySummaryOut
from features.memory import service as memory_service
from features.support_tickets import service as support_ticket_service
from features.support_tickets import repository as support_ticket_repository
from features.support_tickets.schemas import SupportTicketListOut, SupportTicketOut
from features.retention.scheduler import run_retention_cycle

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")
# Do not log request URLs from low-level HTTP clients (can leak bot token in path).
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)

load_dotenv()

app = FastAPI(title="Telegram Mini App API")
_autopay_worker_task: Optional[asyncio.Task] = None
_autopay_lock = asyncio.Lock()
_autopay_last_run_ts = 0.0

FREE_READINGS_PER_MONTH = int(os.getenv("FREE_READINGS_PER_MONTH", "5"))


def _pricing_env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _pricing_env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


def _apply_markup_minor(
    amount: int,
    percent: float,
    *,
    step: int = 1,
    allow_zero: bool = False,
) -> int:
    base = max(0, int(amount))
    if base <= 0:
        return 0 if allow_zero else max(1, int(step))
    multiplier = 1.0 + (float(percent) / 100.0)
    marked = base * multiplier
    safe_step = max(1, int(step))
    rounded = int(round(marked / safe_step) * safe_step)
    if rounded <= 0:
        return 0 if allow_zero else safe_step
    return rounded


PRICING_MARKUP_PERCENT = _pricing_env_float("PRICING_MARKUP_PERCENT", 25.0)
YEARLY_DISCOUNT_PERCENT = _pricing_env_float("YEARLY_DISCOUNT_PERCENT", 35.0)
SBP_SUB_2WEEKS_AMOUNT_BASE = max(100, _pricing_env_int("RUB_SUB_2WEEKS_AMOUNT_BASE", 99 * 100))
SBP_SUB_MONTH_AMOUNT_BASE = max(100, _pricing_env_int("RUB_SUB_MONTH_AMOUNT_BASE", 179 * 100))
SBP_SUB_2WEEKS_AMOUNT = _apply_markup_minor(SBP_SUB_2WEEKS_AMOUNT_BASE, PRICING_MARKUP_PERCENT, step=100)
SBP_SUB_MONTH_AMOUNT = _apply_markup_minor(SBP_SUB_MONTH_AMOUNT_BASE, PRICING_MARKUP_PERCENT, step=100)
SBP_SUB_YEAR_AMOUNT = _apply_markup_minor(
    SBP_SUB_MONTH_AMOUNT * 12,
    -YEARLY_DISCOUNT_PERCENT,
    step=100,
)

SBP_PLANS: Dict[str, Dict[str, Any]] = {
    "sub_2weeks": {
        "code": "sub_2weeks",
        "title": "Безлимит на 2 недели",
        "description": "Подписка AI Tarot на 14 дней",
        "days": 14,
        "amount": SBP_SUB_2WEEKS_AMOUNT,
        "currency": "RUB",
    },
    "sub_month": {
        "code": "sub_month",
        "title": "Безлимит на месяц",
        "description": "Подписка AI Tarot на 30 дней",
        "days": 30,
        "amount": SBP_SUB_MONTH_AMOUNT,
        "currency": "RUB",
    },
    "sub_year": {
        "code": "sub_year",
        "title": "Безлимит на год",
        "description": "Подписка AI Tarot на 365 дней",
        "days": 365,
        "amount": SBP_SUB_YEAR_AMOUNT,
        "currency": "RUB",
    },
}

YOOKASSA_SHOP_ID = (os.getenv("YOOKASSA_SHOP_ID") or "").strip()
YOOKASSA_SECRET_KEY = (os.getenv("YOOKASSA_SECRET_KEY") or "").strip()
YOOKASSA_API_BASE = (os.getenv("YOOKASSA_API_BASE") or "https://api.yookassa.ru/v3").strip().rstrip("/")
YOOKASSA_SBP_RETURN_URL = (
    (os.getenv("YOOKASSA_SBP_RETURN_URL") or os.getenv("TELEGRAM_APP_URL") or "https://tarrotai.ru").strip()
)
YOOKASSA_WEBHOOK_TOKEN = (os.getenv("YOOKASSA_WEBHOOK_TOKEN") or "").strip()
SBP_BOT_LINK_SECRET = (
    os.getenv("SBP_BOT_LINK_SECRET")
    or YOOKASSA_WEBHOOK_TOKEN
    or ""
).strip()
YOOKASSA_HTTP_TIMEOUT = float(os.getenv("YOOKASSA_HTTP_TIMEOUT", "20"))
TELEGRAM_BOT_TOKEN = (os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
SUPPORT_INBOX_BOT_TOKEN = (os.getenv("SUPPORT_INBOX_BOT_TOKEN") or "").strip()
_SUPPORT_CHAT_RAW = (os.getenv("SUPPORT_INBOX_CHAT_ID") or "").strip()
SUPPORT_INBOX_CHAT_ID = int(_SUPPORT_CHAT_RAW) if _SUPPORT_CHAT_RAW.lstrip("-").isdigit() else None
SUPPORT_ADMIN_IDS = {
    int(x.strip())
    for x in str(os.getenv("SUPPORT_ADMIN_IDS") or "").split(",")
    if x.strip().lstrip("-").isdigit()
}
if SUPPORT_INBOX_CHAT_ID is None and SUPPORT_ADMIN_IDS:
    # Fallback: route support inbox to the first admin chat if explicit chat_id is not set.
    SUPPORT_INBOX_CHAT_ID = sorted(SUPPORT_ADMIN_IDS)[0]
SUPPORT_INBOX_WEBHOOK_SECRET = (os.getenv("SUPPORT_INBOX_WEBHOOK_SECRET") or "").strip()
SUPPORT_INBOX_WEBHOOK_URL = (
    os.getenv("SUPPORT_INBOX_WEBHOOK_URL")
    or "https://api.tarrotai.ru/support/inbox/webhook"
).strip()
SBP_AUTOPAY_ENABLED = str(os.getenv("SBP_AUTOPAY_ENABLED", "1")).strip().lower() not in {"0", "false", "no"}
SBP_AUTOPAY_PLAN_CODE = (os.getenv("SBP_AUTOPAY_PLAN_CODE") or "sub_month").strip().lower()
SBP_AUTOPAY_INTERVAL_DAYS = max(1, int(os.getenv("SBP_AUTOPAY_INTERVAL_DAYS", "30")))
SBP_AUTOPAY_MAX_FAILS = max(1, int(os.getenv("SBP_AUTOPAY_MAX_FAILS", "3")))
SBP_AUTOPAY_WORKER_INTERVAL_SEC = max(60, int(os.getenv("SBP_AUTOPAY_WORKER_INTERVAL_SEC", "300")))
CLICK_SERVICE_ID_RAW = (os.getenv("CLICK_SERVICE_ID") or "").strip()
CLICK_MERCHANT_ID_RAW = (os.getenv("CLICK_MERCHANT_ID") or "").strip()
CLICK_MERCHANT_USER_ID_RAW = (
    os.getenv("CLICK_MERCHANT_USER_ID")
    or os.getenv("CLICK_MERCHANT_USER")
    or ""
).strip()
CLICK_SECRET_KEY = (os.getenv("CLICK_SECRET_KEY") or "").strip()
CLICK_PAYMENT_BASE_URL = (os.getenv("CLICK_PAYMENT_BASE_URL") or "https://my.click.uz/services/pay").strip().rstrip("/")
CLICK_RETURN_URL = (
    os.getenv("CLICK_RETURN_URL")
    or os.getenv("TELEGRAM_APP_URL")
    or "https://tarrotai.ru"
).strip()
CLICK_CURRENCY = (os.getenv("CLICK_CURRENCY") or "UZS").strip().upper()
CLICK_BOT_LINK_SECRET = (
    os.getenv("CLICK_BOT_LINK_SECRET")
    or SBP_BOT_LINK_SECRET
    or ""
).strip()
CLICK_BOT_LINK_TTL_SEC = max(60, int(os.getenv("CLICK_BOT_LINK_TTL_SEC", "900")))
CLICK_INTRO_PLAN_CODE = (os.getenv("CLICK_INTRO_PLAN_CODE") or "sub_week").strip().lower()
CLICK_INTRO_FIRST_PURCHASE_ONLY = str(
    os.getenv("CLICK_INTRO_FIRST_PURCHASE_ONLY", "1")
).strip().lower() not in {"0", "false", "no"}
CLICK_PLAN_WEEK_AMOUNT_RAW = (
    os.getenv("CLICK_API_SUB_WEEK_AMOUNT")
    or os.getenv("CLICK_SUB_WEEK_AMOUNT")
    or "12000"
).strip()
CLICK_PLAN_2WEEKS_AMOUNT_RAW = (
    os.getenv("CLICK_API_SUB_2WEEKS_AMOUNT")
    or os.getenv("CLICK_SUB_2WEEKS_AMOUNT")
    or "0"
).strip()
CLICK_PLAN_MONTH_AMOUNT_RAW = (
    os.getenv("CLICK_API_SUB_MONTH_AMOUNT")
    or os.getenv("CLICK_SUB_MONTH_AMOUNT")
    or "0"
).strip()
CLICK_PLAN_YEAR_AMOUNT_RAW = (
    os.getenv("CLICK_API_SUB_YEAR_AMOUNT")
    or os.getenv("CLICK_SUB_YEAR_AMOUNT")
    or "219000"
).strip()
ANALYTICS_ADMIN_TOKEN = (os.getenv("ANALYTICS_ADMIN_TOKEN") or "").strip()
ANALYTICS_TRIAL_PRODUCT_CODE = (os.getenv("ANALYTICS_TRIAL_PRODUCT_CODE") or "sub_week_click").strip().lower()
ANALYTICS_MONTH_PRODUCT_CODES_RAW = (
    os.getenv("ANALYTICS_MONTH_PRODUCT_CODES")
    or "sub_month_click"
).strip()
ANALYTICS_EXCLUDE_TELEGRAM_IDS_RAW = (os.getenv("ANALYTICS_EXCLUDE_TELEGRAM_IDS") or "").strip()
VISION_ESCALATION_COUNTER_KEY = "photo_analysis"
VISION_ESCALATION_TZ_NAME = (os.getenv("OPENAI_VISION_ESCALATION_TZ") or "Asia/Tashkent").strip() or "Asia/Tashkent"


def _safe_env_float(name: str, default: float) -> float:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


def _safe_env_int(name: str, default: int) -> int:
    raw = str(os.getenv(name) or "").strip()
    if not raw:
        return int(default)
    try:
        return int(raw)
    except Exception:
        return int(default)


VISION_ESCALATION_MAX_RATIO = min(1.0, max(0.0, _safe_env_float("OPENAI_VISION_ESCALATION_MAX_RATIO", 0.25)))
VISION_ESCALATION_DAILY_MAX = max(0, _safe_env_int("OPENAI_VISION_ESCALATION_DAILY_MAX", 0))
VISION_ESCALATION_MIN_SAMPLES = max(0, _safe_env_int("OPENAI_VISION_ESCALATION_MIN_SAMPLES", 20))


def _parse_int_or_default(raw: str, default: int = 0) -> int:
    try:
        return int(str(raw or "").strip())
    except Exception:
        return int(default)


def _parse_csv_values(raw: str, *, lower: bool = True) -> List[str]:
    values: List[str] = []
    for part in str(raw or "").split(","):
        item = str(part or "").strip()
        if not item:
            continue
        values.append(item.lower() if lower else item)
    return values


def _parse_csv_ints(raw: str) -> List[int]:
    out: List[int] = []
    for item in _parse_csv_values(raw, lower=False):
        s = str(item or "").strip()
        if not s.lstrip("-").isdigit():
            continue
        out.append(int(s))
    return out


ANALYTICS_GROWTH_LOOKBACK_DAYS = max(7, _safe_env_int("ANALYTICS_GROWTH_LOOKBACK_DAYS", 30))
ANALYTICS_TRIAL_TO_MONTH_WINDOW_DAYS = max(1, _safe_env_int("ANALYTICS_TRIAL_TO_MONTH_WINDOW_DAYS", 30))
ANALYTICS_AD_SPEND_UZS = max(0, _safe_env_int("ANALYTICS_AD_SPEND_UZS", 0))
ANALYTICS_AD_SPEND_RUB = max(0, _safe_env_int("ANALYTICS_AD_SPEND_RUB", 0))
ANALYTICS_EXCLUDE_TELEGRAM_IDS = _parse_csv_ints(ANALYTICS_EXCLUDE_TELEGRAM_IDS_RAW)
ANALYTICS_MONTH_PRODUCT_CODES = _parse_csv_values(
    ANALYTICS_MONTH_PRODUCT_CODES_RAW,
    lower=True,
) or ["sub_month_click"]


CLICK_SERVICE_ID = max(0, _parse_int_or_default(CLICK_SERVICE_ID_RAW, 0))
CLICK_MERCHANT_ID = max(0, _parse_int_or_default(CLICK_MERCHANT_ID_RAW, 0))
CLICK_MERCHANT_USER_ID = max(0, _parse_int_or_default(CLICK_MERCHANT_USER_ID_RAW, 0))
CLICK_PLAN_WEEK_AMOUNT = max(0, _parse_int_or_default(CLICK_PLAN_WEEK_AMOUNT_RAW, 12000))
CLICK_PLAN_2WEEKS_AMOUNT = _apply_markup_minor(
    max(0, _parse_int_or_default(CLICK_PLAN_2WEEKS_AMOUNT_RAW, 0)),
    PRICING_MARKUP_PERCENT,
    step=100,
    allow_zero=True,
)
CLICK_PLAN_MONTH_AMOUNT = _apply_markup_minor(
    max(0, _parse_int_or_default(CLICK_PLAN_MONTH_AMOUNT_RAW, 0)),
    PRICING_MARKUP_PERCENT,
    step=100,
    allow_zero=True,
)
_click_year_raw = max(0, _parse_int_or_default(CLICK_PLAN_YEAR_AMOUNT_RAW, 0))
if _click_year_raw > 0:
    CLICK_PLAN_YEAR_AMOUNT = _click_year_raw
elif CLICK_PLAN_MONTH_AMOUNT > 0:
    CLICK_PLAN_YEAR_AMOUNT = _apply_markup_minor(
        CLICK_PLAN_MONTH_AMOUNT * 12,
        -YEARLY_DISCOUNT_PERCENT,
        step=100,
        allow_zero=True,
    )
else:
    CLICK_PLAN_YEAR_AMOUNT = 0

CLICK_PLANS: Dict[str, Dict[str, Any]] = {
    "sub_week": {
        "code": "sub_week",
        "title": "Пробная неделя (CLICK)",
        "description": "Пробный доступ AI Tarot на 7 дней через CLICK",
        "days": 7,
        "amount": CLICK_PLAN_WEEK_AMOUNT,
        "currency": CLICK_CURRENCY,
    },
    "sub_2weeks": {
        "code": "sub_2weeks",
        "title": "Безлимит на 2 недели (CLICK)",
        "description": "Подписка AI Tarot на 14 дней через CLICK",
        "days": 14,
        "amount": CLICK_PLAN_2WEEKS_AMOUNT,
        "currency": CLICK_CURRENCY,
    },
    "sub_month": {
        "code": "sub_month",
        "title": "Безлимит на месяц (CLICK)",
        "description": "Подписка AI Tarot на 30 дней через CLICK",
        "days": 30,
        "amount": CLICK_PLAN_MONTH_AMOUNT,
        "currency": CLICK_CURRENCY,
    },
    "sub_year": {
        "code": "sub_year",
        "title": "Безлимит на год (CLICK)",
        "description": "Подписка AI Tarot на 365 дней через CLICK",
        "days": 365,
        "amount": CLICK_PLAN_YEAR_AMOUNT,
        "currency": CLICK_CURRENCY,
    },
}

try:
    VISION_ESCALATION_TZ = ZoneInfo(VISION_ESCALATION_TZ_NAME)
except ZoneInfoNotFoundError:
    log.warning("OPENAI_VISION_ESCALATION_TZ=%s is invalid, fallback to UTC", VISION_ESCALATION_TZ_NAME)
    VISION_ESCALATION_TZ = timezone.utc

_WEAK_SECRET_VALUES = {
    "changeme",
    "change_me",
    "change-me",
    "default",
    "secret",
    "supersecret",
    "super_secret",
    "super-secret",
    "password",
    "test",
    "example",
    "admin",
    "root",
    "12345",
    "qwerty",
    "yookassa_webhook_token",
}
_WEAK_SECRET_HINT_RE = re.compile(
    r"(change.?me|replace.?me|default|example|test.?key|test.?secret|webhook.?token)",
    re.IGNORECASE,
)


def _assert_strong_secret(
    secret_name: str,
    secret_value: str,
    *,
    min_len: int = 24,
    required: bool = True,
) -> None:
    value = str(secret_value or "").strip()
    if not value:
        if required:
            raise RuntimeError(f"{secret_name} is required and must not be empty")
        return
    if len(value) < min_len:
        raise RuntimeError(
            f"{secret_name} is too short (len={len(value)}). "
            f"Use at least {min_len} random characters."
        )
    normalized = re.sub(r"[^a-z0-9]+", "", value.lower())
    if normalized in _WEAK_SECRET_VALUES or _WEAK_SECRET_HINT_RE.search(value):
        raise RuntimeError(
            f"{secret_name} looks like a default/weak placeholder. "
            "Set a strong random value and restart."
        )


def _validate_security_boot_config() -> None:
    # SBP/YooKassa webhook auth token is mandatory in production runtime.
    _assert_strong_secret("YOOKASSA_WEBHOOK_TOKEN", YOOKASSA_WEBHOOK_TOKEN, min_len=24, required=True)
    # Optional channel, but if configured then must be strong.
    _assert_strong_secret(
        "SUPPORT_INBOX_WEBHOOK_SECRET",
        SUPPORT_INBOX_WEBHOOK_SECRET,
        min_len=24,
        required=False,
    )
    # Click can work in parallel with other providers; validate a complete set only if any field is enabled.
    click_any = bool(CLICK_SERVICE_ID or CLICK_MERCHANT_ID or CLICK_SECRET_KEY)
    click_all = bool(CLICK_SERVICE_ID > 0 and CLICK_MERCHANT_ID > 0 and CLICK_SECRET_KEY)
    if click_any and not click_all:
        raise RuntimeError(
            "CLICK config is incomplete. Set CLICK_SERVICE_ID, CLICK_MERCHANT_ID and CLICK_SECRET_KEY together."
        )


# ============================ CORS ============================
def _parse_cors_origins(value: str) -> List[str]:
    items = [x.strip() for x in (value or "").split(",")]
    return [x for x in items if x]


DEFAULT_CORS_ORIGINS = "https://tarrotai.ru,https://www.tarrotai.ru"
CORS_ORIGINS = _parse_cors_origins(os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS))
if not CORS_ORIGINS:
    CORS_ORIGINS = _parse_cors_origins(DEFAULT_CORS_ORIGINS)
    log.warning("CORS_ORIGINS is empty, fallback to defaults: %s", CORS_ORIGINS)

app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["GET", "POST", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "Accept", "Origin", "X-Requested-With"],
)

# ============================ TELEGRAM BOT AUTOSTART ============================
import threading

_bot_thread: Optional[threading.Thread] = None


def _run_telegram_bot_thread() -> None:
    """
    Запускаем python-telegram-bot в отдельном потоке с отдельным asyncio event loop.
    Это нужно для Python 3.12 (в потоке по умолчанию loop отсутствует).
    """
    try:
        import telegram_bot  # type: ignore
    except Exception as e:
        log.exception("Failed to import telegram_bot.py: %s", repr(e))
        return

    bot_token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not bot_token:
        log.warning("TELEGRAM_BOT_TOKEN is not set -> Telegram bot will not start.")
        return

    async def bot_main_async():
        # В telegram_bot.py должен быть export create_application()
        if hasattr(telegram_bot, "create_application"):
            app_ = telegram_bot.create_application()
        else:
            # fallback: если не сделали create_application — попробуем собрать тут минимально
            from telegram.ext import Application, CommandHandler
            app_ = Application.builder().token(bot_token).build()
            if hasattr(telegram_bot, "start"):
                app_.add_handler(CommandHandler("start", telegram_bot.start))  # type: ignore

        await app_.initialize()
        await app_.start()
        if hasattr(telegram_bot, "configure_bot_ui_at_startup"):
            try:
                await telegram_bot.configure_bot_ui_at_startup(app_)  # type: ignore[attr-defined]
            except Exception as e:
                log.warning("Bot UI preconfigure skipped: %s", repr(e))
        await app_.updater.start_polling()
        log.info("Telegram bot polling started in background thread.")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.create_task(bot_main_async())
        loop.run_forever()
    except Exception as e:
        log.exception("Telegram bot thread crashed: %s", repr(e))
    finally:
        try:
            loop.stop()
        except Exception:
            pass
        loop.close()


async def _start_telegram_bot_background() -> None:
    global _bot_thread
    if _bot_thread and _bot_thread.is_alive():
        return
    _bot_thread = threading.Thread(target=_run_telegram_bot_thread, name="telegram-bot", daemon=True)
    _bot_thread.start()



# ============================ STARTUP / SHUTDOWN ============================
@app.on_event("startup")
async def on_startup() -> None:
    global _autopay_worker_task
    _validate_security_boot_config()
    # БД
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_runtime_schema(conn)

    # Бот
    await _start_telegram_bot_background()
    await _configure_support_inbox_bot()
    if _autopay_worker_task is None:
        _autopay_worker_task = asyncio.create_task(_autopay_worker_loop())


@app.on_event("shutdown")
async def on_shutdown() -> None:
    global _autopay_worker_task
    if _autopay_worker_task:
        _autopay_worker_task.cancel()
        try:
            await _autopay_worker_task
        except asyncio.CancelledError:
            pass
        except Exception:
            pass
        _autopay_worker_task = None
    # Бот работает в daemon-thread и завершится вместе с процессом.
    return None


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "time": datetime.utcnow().isoformat(),
        "sbp_configured": _yookassa_sbp_configured(),
        "sbp_autopay_enabled": SBP_AUTOPAY_ENABLED,
        "click_configured": _click_api_configured(),
    }


# ============================ HELPERS ============================
def _today_key() -> str:
    return date.today().isoformat()


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _month_bounds_utc(now: datetime) -> tuple[datetime, datetime]:
    first = datetime(now.year, now.month, 1, tzinfo=timezone.utc)
    if now.month == 12:
        nxt = datetime(now.year + 1, 1, 1, tzinfo=timezone.utc)
    else:
        nxt = datetime(now.year, now.month + 1, 1, tzinfo=timezone.utc)
    return first, nxt


def _vision_day_key_local(now: Optional[datetime] = None) -> str:
    base = now.astimezone(VISION_ESCALATION_TZ) if now else datetime.now(VISION_ESCALATION_TZ)
    return base.strftime("%Y-%m-%d")


def _vision_snapshot(day_key: str, total: int, escalated: int) -> Dict[str, Any]:
    return {
        "day": str(day_key),
        "total": int(total or 0),
        "escalated": int(escalated or 0),
    }


async def _vision_register_request_global() -> Dict[str, Any]:
    day_key = _vision_day_key_local()
    try:
        async with SessionLocal() as quota_db:
            stmt = (
                pg_insert(VisionEscalationCounter)
                .values(
                    counter_key=VISION_ESCALATION_COUNTER_KEY,
                    day_key=day_key,
                    total_requests=1,
                    escalated_requests=0,
                )
                .on_conflict_do_update(
                    index_elements=[VisionEscalationCounter.counter_key, VisionEscalationCounter.day_key],
                    set_={
                        "total_requests": VisionEscalationCounter.total_requests + 1,
                        "updated_at": func.now(),
                    },
                )
                .returning(
                    VisionEscalationCounter.day_key,
                    VisionEscalationCounter.total_requests,
                    VisionEscalationCounter.escalated_requests,
                )
            )
            row = (await quota_db.execute(stmt)).one()
            await quota_db.commit()
            return _vision_snapshot(row.day_key, row.total_requests, row.escalated_requests)
    except Exception as exc:
        log.warning("vision quota register failed (fallback fail-open): %s", repr(exc))
        return _vision_snapshot(day_key, 0, 0)


async def _vision_try_escalate_global() -> tuple[bool, str, Dict[str, Any]]:
    day_key = _vision_day_key_local()
    try:
        async with SessionLocal() as quota_db:
            await quota_db.execute(
                pg_insert(VisionEscalationCounter)
                .values(
                    counter_key=VISION_ESCALATION_COUNTER_KEY,
                    day_key=day_key,
                    total_requests=0,
                    escalated_requests=0,
                )
                .on_conflict_do_nothing(
                    index_elements=[VisionEscalationCounter.counter_key, VisionEscalationCounter.day_key]
                )
            )
            row = (
                await quota_db.execute(
                    select(VisionEscalationCounter)
                    .where(
                        VisionEscalationCounter.counter_key == VISION_ESCALATION_COUNTER_KEY,
                        VisionEscalationCounter.day_key == day_key,
                    )
                    .with_for_update()
                )
            ).scalar_one()

            total = int(row.total_requests or 0)
            escalated = int(row.escalated_requests or 0)
            snapshot = _vision_snapshot(day_key, total, escalated)

            if VISION_ESCALATION_DAILY_MAX > 0 and escalated >= VISION_ESCALATION_DAILY_MAX:
                await quota_db.rollback()
                return False, "daily_max_reached", snapshot

            if total >= VISION_ESCALATION_MIN_SAMPLES and total > 0:
                current_ratio = escalated / float(total)
                if current_ratio >= VISION_ESCALATION_MAX_RATIO:
                    await quota_db.rollback()
                    return False, "ratio_limit_reached", snapshot

            row.escalated_requests = escalated + 1
            await quota_db.commit()
            return True, "ok", _vision_snapshot(day_key, total, escalated + 1)
    except Exception as exc:
        log.warning("vision quota escalate check failed (fail-open): %s", repr(exc))
        return True, "db_error_fail_open", _vision_snapshot(day_key, 0, 0)


async def _vision_mark_forced_escalation_global() -> Dict[str, Any]:
    day_key = _vision_day_key_local()
    try:
        async with SessionLocal() as quota_db:
            await quota_db.execute(
                pg_insert(VisionEscalationCounter)
                .values(
                    counter_key=VISION_ESCALATION_COUNTER_KEY,
                    day_key=day_key,
                    total_requests=0,
                    escalated_requests=0,
                )
                .on_conflict_do_nothing(
                    index_elements=[VisionEscalationCounter.counter_key, VisionEscalationCounter.day_key]
                )
            )
            row = (
                await quota_db.execute(
                    select(VisionEscalationCounter)
                    .where(
                        VisionEscalationCounter.counter_key == VISION_ESCALATION_COUNTER_KEY,
                        VisionEscalationCounter.day_key == day_key,
                    )
                    .with_for_update()
                )
            ).scalar_one()
            total = int(row.total_requests or 0)
            escalated = int(row.escalated_requests or 0) + 1
            row.escalated_requests = escalated
            await quota_db.commit()
            return _vision_snapshot(day_key, total, escalated)
    except Exception as exc:
        log.warning("vision quota forced escalation failed (fail-open): %s", repr(exc))
        return _vision_snapshot(day_key, 0, 1)


def _get_sbp_plan(plan_code: str) -> Dict[str, Any]:
    code = str(plan_code or "").strip().lower()
    plan = SBP_PLANS.get(code)
    if not plan:
        raise HTTPException(status_code=400, detail={"code": "INVALID_PLAN", "message": "Неизвестный тариф подписки."})
    return plan


def _yookassa_sbp_configured() -> bool:
    return bool(YOOKASSA_SHOP_ID and YOOKASSA_SECRET_KEY)


def _is_sbp_autopay_plan(plan_code: str) -> bool:
    return str(plan_code or "").strip().lower() == SBP_AUTOPAY_PLAN_CODE


def _click_api_configured() -> bool:
    return bool(CLICK_SERVICE_ID > 0 and CLICK_MERCHANT_ID > 0 and CLICK_SECRET_KEY)


def _get_click_plan(plan_code: str) -> Dict[str, Any]:
    code = str(plan_code or "").strip().lower()
    plan = CLICK_PLANS.get(code)
    if not plan:
        raise HTTPException(status_code=400, detail={"code": "INVALID_PLAN", "message": "Неизвестный тариф Click."})
    if int(plan.get("amount") or 0) <= 0:
        raise HTTPException(
            status_code=503,
            detail={"code": "CLICK_PLAN_DISABLED", "message": f"Тариф Click '{code}' пока не настроен."},
        )
    return plan


async def _click_intro_plan_already_used(db: AsyncSession, *, user_id: int) -> bool:
    if not CLICK_INTRO_FIRST_PURCHASE_ONLY:
        return False
    if not CLICK_INTRO_PLAN_CODE:
        return False
    target = f"{CLICK_INTRO_PLAN_CODE}_click"
    q = await db.execute(
        select(PaymentTransaction.id)
        .where(
            PaymentTransaction.user_id == int(user_id),
            PaymentTransaction.kind == "subscription",
            PaymentTransaction.product_code == target,
        )
        .limit(1)
    )
    return q.scalar_one_or_none() is not None


def _click_sign_raw(
    *,
    click_trans_id: str,
    service_id: str,
    merchant_trans_id: str,
    amount: str,
    action: str,
    sign_time: str,
    merchant_prepare_id: str = "",
) -> str:
    payload = (
        f"{str(click_trans_id)}"
        f"{str(service_id)}"
        f"{CLICK_SECRET_KEY}"
        f"{str(merchant_trans_id)}"
        f"{str(merchant_prepare_id)}"
        f"{str(amount)}"
        f"{str(action)}"
        f"{str(sign_time)}"
    )
    return hashlib.md5(payload.encode("utf-8")).hexdigest()


def _click_build_payment_url(
    *,
    order_id: str,
    amount: int,
    return_url: Optional[str] = None,
    card_type: Optional[str] = None,
) -> str:
    params: Dict[str, str] = {
        "service_id": str(CLICK_SERVICE_ID),
        "merchant_id": str(CLICK_MERCHANT_ID),
        "amount": str(max(0, int(amount))),
        "transaction_param": str(order_id),
    }
    if CLICK_MERCHANT_USER_ID > 0:
        params["merchant_user_id"] = str(CLICK_MERCHANT_USER_ID)
    target_return = str(return_url or CLICK_RETURN_URL or "").strip()
    if target_return:
        params["return_url"] = _append_query_param(target_return, "click_order_id", str(order_id))
    safe_card_type = str(card_type or "").strip().lower()
    if safe_card_type in {"uzcard", "humo"}:
        params["card_type"] = safe_card_type
    return f"{CLICK_PAYMENT_BASE_URL}?{urlencode(params)}"


def _click_amount_decimal(amount: int) -> str:
    return f"{max(0, int(amount)):0.2f}"


def _click_normalize_card_type(raw: Optional[str]) -> str:
    value = str(raw or "").strip().lower()
    if value in {"uzcard", "humo"}:
        return value
    return ""


def _render_click_card_checkout_html(
    *,
    order_id: str,
    amount: int,
    return_url: str,
    fallback_url: str,
    default_card_type: str = "",
) -> str:
    req: Dict[str, str] = {
        "service_id": str(CLICK_SERVICE_ID),
        "merchant_id": str(CLICK_MERCHANT_ID),
        "amount": _click_amount_decimal(int(amount)),
        "transaction_param": str(order_id),
    }
    if CLICK_MERCHANT_USER_ID > 0:
        req["merchant_user_id"] = str(CLICK_MERCHANT_USER_ID)
    safe_return = str(return_url or "").strip()
    if safe_return:
        req["return_url"] = safe_return
    safe_default_card_type = _click_normalize_card_type(default_card_type)
    if safe_default_card_type:
        req["card_type"] = safe_default_card_type

    req_json = json.dumps(req, ensure_ascii=False)
    fallback_json = json.dumps(str(fallback_url or "").strip(), ensure_ascii=False)
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>Оплата картой через CLICK</title>
  <style>
    body {{
      margin: 0;
      font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;
      background: linear-gradient(180deg, #081635 0%, #102556 100%);
      color: #e8eeff;
    }}
    .wrap {{
      max-width: 560px;
      margin: 0 auto;
      padding: max(16px, env(safe-area-inset-top)) 16px calc(18px + env(safe-area-inset-bottom));
    }}
    .card {{
      border: 1px solid rgba(255,255,255,0.18);
      border-radius: 16px;
      background: rgba(18, 40, 92, 0.62);
      padding: 16px;
      backdrop-filter: blur(12px);
    }}
    h1 {{ margin: 0 0 8px; font-size: 20px; }}
    p {{ margin: 0 0 10px; line-height: 1.45; color: #dbe4ff; }}
    .btn {{
      display: block;
      width: 100%;
      border: 0;
      border-radius: 12px;
      padding: 12px 14px;
      margin-top: 10px;
      font-weight: 700;
      font-size: 16px;
      cursor: pointer;
      color: #fff;
      background: linear-gradient(90deg, #325edb, #5b7bff);
    }}
    .btn.alt {{ background: rgba(255,255,255,0.14); }}
    .hint {{ font-size: 13px; opacity: 0.9; margin-top: 12px; }}
  </style>
  <script src="https://my.click.uz/pay/checkout.js"></script>
</head>
<body>
  <main class="wrap">
    <section class="card">
      <h1>Оплата картой через CLICK</h1>
      <p>Открываем форму оплаты картой без перехода в приложение.</p>
      <button class="btn" id="pay-any">Оплатить картой</button>
      <button class="btn alt" id="pay-uzcard">Оплатить Uzcard</button>
      <button class="btn alt" id="pay-humo">Оплатить Humo</button>
      <button class="btn alt" id="open-click">Открыть стандартную страницу CLICK</button>
      <p class="hint">Если форма не открылась автоматически, нажмите кнопку выше.</p>
    </section>
  </main>
  <script>
    (function() {{
      const requestBase = {req_json};
      const fallbackUrl = {fallback_json};
      function openCard(cardType) {{
        const payload = Object.assign({{}}, requestBase);
        if (cardType) {{
          payload.card_type = cardType;
        }} else if (payload.card_type) {{
          delete payload.card_type;
        }}
        try {{
          if (typeof window.createPaymentRequest === "function") {{
            window.createPaymentRequest(payload, function() {{}});
            return;
          }}
        }} catch (e) {{}}
        if (fallbackUrl) {{
          window.location.href = fallbackUrl;
        }}
      }}
      document.getElementById("pay-any")?.addEventListener("click", function() {{ openCard(""); }});
      document.getElementById("pay-uzcard")?.addEventListener("click", function() {{ openCard("uzcard"); }});
      document.getElementById("pay-humo")?.addEventListener("click", function() {{ openCard("humo"); }});
      document.getElementById("open-click")?.addEventListener("click", function() {{
        if (fallbackUrl) {{
          window.location.href = fallbackUrl;
        }}
      }});
      window.addEventListener("load", function() {{
        setTimeout(function() {{ openCard(""); }}, 120);
      }});
    }})();
  </script>
</body>
</html>"""


def _click_amount_matches(*, request_amount: str, expected_amount: int) -> bool:
    try:
        value = float(str(request_amount or "").strip())
    except Exception:
        return False
    return abs(value - float(int(expected_amount))) < 0.01


def _click_bot_link_signature(*, tg_user_id: int, plan_code: str, exp: int) -> str:
    payload = f"{int(tg_user_id)}:{str(plan_code).strip().lower()}:{int(exp)}"
    return hmac.new(
        CLICK_BOT_LINK_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _verify_click_bot_link_signature(*, tg_user_id: int, plan_code: str, exp: int, sig: str) -> bool:
    if not CLICK_BOT_LINK_SECRET:
        return False
    expected = _click_bot_link_signature(tg_user_id=tg_user_id, plan_code=plan_code, exp=exp)
    actual = str(sig or "").strip().lower()
    return bool(actual) and hmac.compare_digest(expected, actual)


CLICK_ERROR_NOTES: Dict[int, str] = {
    0: "Success",
    -1: "SIGN CHECK FAILED!",
    -2: "Incorrect parameter amount",
    -3: "Action not found",
    -4: "Already paid",
    -5: "User does not exist",
    -6: "Transaction does not exist",
    -7: "Failed to update user",
    -8: "Error in request from click",
    -9: "Transaction cancelled",
}


def _click_build_response(
    *,
    click_trans_id: str,
    merchant_trans_id: str,
    error: int,
    merchant_prepare_id: Optional[int] = None,
    merchant_confirm_id: Optional[int] = None,
) -> Dict[str, Any]:
    out: Dict[str, Any] = {
        "click_trans_id": str(click_trans_id or ""),
        "merchant_trans_id": str(merchant_trans_id or ""),
        "error": int(error),
        "error_note": CLICK_ERROR_NOTES.get(int(error), "Error in request from click"),
    }
    if merchant_prepare_id is not None:
        out["merchant_prepare_id"] = int(merchant_prepare_id)
    if merchant_confirm_id is not None:
        out["merchant_confirm_id"] = int(merchant_confirm_id)
    return out


def _rub_value_from_kopecks(kopecks: int) -> str:
    value = max(0, int(kopecks or 0)) / 100.0
    return f"{value:.2f}"


def _append_query_param(url: str, key: str, value: str) -> str:
    base = str(url or "").strip()
    if not base:
        return ""
    try:
        parsed = urlsplit(base)
        existing_keys = {str(k or "").strip() for k, _ in parse_qsl(parsed.query, keep_blank_values=True)}
        if str(key or "").strip() in existing_keys:
            return base
    except Exception:
        pass
    sep = "&" if "?" in base else "?"
    return f"{base}{sep}{key}={value}"


def _parse_provider_dt(value: Any) -> Optional[datetime]:
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except Exception:
        return None
    return _to_utc(parsed)


def _sbp_bot_link_signature(*, tg_user_id: int, plan_code: str, exp: int) -> str:
    payload = f"{int(tg_user_id)}:{str(plan_code).strip().lower()}:{int(exp)}"
    return hmac.new(
        SBP_BOT_LINK_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _verify_sbp_bot_link_signature(*, tg_user_id: int, plan_code: str, exp: int, sig: str) -> bool:
    if not SBP_BOT_LINK_SECRET:
        return False
    expected = _sbp_bot_link_signature(tg_user_id=tg_user_id, plan_code=plan_code, exp=exp)
    actual = str(sig or "").strip().lower()
    return bool(actual) and hmac.compare_digest(expected, actual)


def _render_sbp_unavailable_html(*, plan_title: str, message: str) -> str:
    safe_plan = escape(str(plan_title or "Подписка"))
    safe_msg = escape(str(message or "СБП сейчас недоступен"))
    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Оплата СБП</title>
  <style>
    body {{ margin:0; font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif; background:#0b153f; color:#e7ecff; }}
    .wrap {{ max-width:560px; margin:0 auto; padding:24px 18px 28px; }}
    .card {{ background:rgba(255,255,255,0.08); border:1px solid rgba(255,255,255,0.16); border-radius:16px; padding:16px; }}
    h1 {{ margin:0 0 12px; font-size:24px; line-height:1.2; }}
    p {{ margin:0 0 10px; font-size:16px; line-height:1.45; color:#d7ddf7; }}
    .hint {{ margin-top:14px; color:#bfc8f8; font-size:14px; }}
  </style>
</head>
<body>
  <div class="wrap">
    <h1>СБП временно недоступен</h1>
    <div class="card">
      <p><strong>{safe_plan}</strong></p>
      <p>{safe_msg}</p>
      <p class="hint">Вернитесь в бот и выберите оплату <strong>По карте или SberPay</strong> или <strong>CLICK</strong>.</p>
    </div>
  </div>
</body>
</html>"""


async def _yookassa_request(
    method: str,
    path: str,
    *,
    payload: Optional[Dict[str, Any]] = None,
    idempotence_key: Optional[str] = None,
) -> Dict[str, Any]:
    if not _yookassa_sbp_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SBP_NOT_CONFIGURED",
                "message": "СБП ещё не настроен: добавьте YOOKASSA_SHOP_ID и YOOKASSA_SECRET_KEY на сервере.",
            },
        )

    url = f"{YOOKASSA_API_BASE}{path}"
    headers = {"Content-Type": "application/json"}
    if idempotence_key:
        headers["Idempotence-Key"] = str(idempotence_key)

    try:
        async with httpx.AsyncClient(timeout=YOOKASSA_HTTP_TIMEOUT) as client:
            response = await client.request(
                method.upper(),
                url,
                auth=(YOOKASSA_SHOP_ID, YOOKASSA_SECRET_KEY),
                headers=headers,
                content=json.dumps(payload or {}, ensure_ascii=False).encode("utf-8") if payload is not None else None,
            )
    except httpx.HTTPError as exc:
        log.exception("YooKassa request failed: %s %s err=%s", method, path, exc)
        raise HTTPException(
            status_code=502,
            detail={"code": "SBP_PROVIDER_UNREACHABLE", "message": "Не удалось связаться с платёжным провайдером."},
        )

    body: Dict[str, Any] = {}
    try:
        body = response.json()
    except Exception:
        body = {"raw": response.text[:2000]}

    if response.status_code >= 400:
        log.warning("YooKassa error %s for %s %s: %s", response.status_code, method, path, body)
        err_msg = ""
        if isinstance(body, dict):
            err_msg = str(body.get("description") or body.get("message") or "").strip()
        raise HTTPException(
            status_code=502,
            detail={
                "code": "SBP_PROVIDER_ERROR",
                "message": err_msg or "Платёжный провайдер вернул ошибку.",
            },
        )

    if not isinstance(body, dict):
        raise HTTPException(
            status_code=502,
            detail={"code": "SBP_PROVIDER_BAD_RESPONSE", "message": "Некорректный ответ платёжного провайдера."},
        )
    return body


async def _apply_subscription_from_sbp(
    db: AsyncSession,
    *,
    user: User,
    plan: Dict[str, Any],
    provider_payment_id: str,
    order_id: str,
) -> tuple[bool, Optional[datetime]]:
    provider_payment_id = str(provider_payment_id or "").strip()
    if not provider_payment_id:
        raise HTTPException(
            status_code=502,
            detail={"code": "SBP_PROVIDER_BAD_RESPONSE", "message": "Провайдер не вернул идентификатор платежа."},
        )

    provider_charge_id = f"yookassa_sbp:{provider_payment_id}"
    exists_q = await db.execute(
        select(PaymentTransaction.id).where(PaymentTransaction.provider_payment_charge_id == provider_charge_id)
    )
    exists = exists_q.scalar_one_or_none()
    if exists:
        sub_until = _to_utc(user.subscription_until)
        return False, sub_until

    now = datetime.now(timezone.utc)
    sub_until = _to_utc(user.subscription_until)
    base = sub_until if sub_until and sub_until > now else now
    new_sub_until = base + timedelta(days=int(plan["days"]))
    user.subscription_until = new_sub_until

    tx = PaymentTransaction(
        user_id=int(user.id),
        invoice_payload=f"sbp:{order_id}",
        product_code=str(plan["code"]),
        kind="subscription",
        amount=int(plan["amount"]),
        currency=str(plan.get("currency") or "RUB"),
        credits_delta=0,
        subscription_days=int(plan["days"]),
        telegram_payment_charge_id=None,
        provider_payment_charge_id=provider_charge_id,
    )
    db.add(tx)
    return True, new_sub_until


async def _apply_subscription_from_click(
    db: AsyncSession,
    *,
    user: User,
    plan: Dict[str, Any],
    click_trans_id: str,
    order_id: str,
) -> tuple[bool, Optional[datetime]]:
    provider_ref = str(click_trans_id or "").strip() or str(order_id or "").strip()
    if not provider_ref:
        return False, _to_utc(user.subscription_until)

    provider_charge_id = f"click_api:{provider_ref}"
    exists_q = await db.execute(
        select(PaymentTransaction.id).where(PaymentTransaction.provider_payment_charge_id == provider_charge_id)
    )
    exists = exists_q.scalar_one_or_none()
    if exists:
        return False, _to_utc(user.subscription_until)

    now = datetime.now(timezone.utc)
    sub_until = _to_utc(user.subscription_until)
    base = sub_until if sub_until and sub_until > now else now
    new_sub_until = base + timedelta(days=int(plan["days"]))
    user.subscription_until = new_sub_until

    tx = PaymentTransaction(
        user_id=int(user.id),
        invoice_payload=f"click:{order_id}",
        product_code=f"{str(plan['code'])}_click",
        kind="subscription",
        amount=int(plan["amount"]),
        currency=str(plan.get("currency") or CLICK_CURRENCY),
        credits_delta=0,
        subscription_days=int(plan["days"]),
        telegram_payment_charge_id=None,
        provider_payment_charge_id=provider_charge_id,
    )
    db.add(tx)
    return True, new_sub_until


async def _recompute_user_subscription_until(db: AsyncSession, user: User) -> Optional[datetime]:
    """
    Rebuild subscription_until from non-refunded subscription transactions.
    Needed to handle refunds safely (including old payments).
    """
    q = await db.execute(
        select(PaymentTransaction)
        .where(
            PaymentTransaction.user_id == int(user.id),
            PaymentTransaction.kind == "subscription",
            PaymentTransaction.refunded_at.is_(None),
            PaymentTransaction.subscription_days > 0,
        )
        .order_by(PaymentTransaction.created_at.asc(), PaymentTransaction.id.asc())
    )
    txs = list(q.scalars().all())
    if not txs:
        user.subscription_until = None
        return None

    sub_until: Optional[datetime] = None
    for tx in txs:
        tx_time = _to_utc(tx.created_at) or datetime.now(timezone.utc)
        base = tx_time
        if sub_until and sub_until > tx_time:
            base = sub_until
        sub_until = base + timedelta(days=max(0, int(tx.subscription_days or 0)))

    user.subscription_until = sub_until
    return sub_until


async def _ensure_runtime_schema(conn) -> None:
    """
    Lightweight runtime migration for existing DBs without Alembic.
    Safe for repeated startups.
    """
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_until TIMESTAMPTZ NULL;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS paid_readings_balance INTEGER NOT NULL DEFAULT 0;",
        "ALTER TABLE card_of_day ADD COLUMN IF NOT EXISTS is_reversed BOOLEAN NOT NULL DEFAULT FALSE;",
        "UPDATE card_of_day SET is_reversed = TRUE WHERE is_reversed = FALSE AND (LOWER(COALESCE(description, '')) LIKE '%перевер%' OR LOWER(COALESCE(description, '')) LIKE '%перевёр%' OR LOWER(COALESCE(description, '')) LIKE '%обратн%' OR LOWER(COALESCE(description, '')) LIKE '%reversed%' OR LOWER(COALESCE(description, '')) LIKE '%reverse%');",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS memory_opt_in BOOLEAN NOT NULL DEFAULT TRUE;",
        "ALTER TABLE users ALTER COLUMN memory_opt_in SET DEFAULT TRUE;",
        "UPDATE users SET memory_opt_in = TRUE WHERE memory_opt_in IS NULL;",
        "ALTER TABLE users ALTER COLUMN memory_opt_in SET NOT NULL;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS retention_nudges_opt_in BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS retention_nudge_hour_local INTEGER NULL;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS retention_nudge_tz VARCHAR(64) NULL;",
        "ALTER TABLE sbp_orders ADD COLUMN IF NOT EXISTS success_notified BOOLEAN NOT NULL DEFAULT FALSE;",
        "ALTER TABLE payment_transactions ADD COLUMN IF NOT EXISTS refunded_at TIMESTAMPTZ NULL;",
        "ALTER TABLE support_tickets ADD COLUMN IF NOT EXISTS support_inbox_message_id BIGINT NULL;",
        "CREATE INDEX IF NOT EXISTS ix_users_subscription_until ON users (subscription_until);",
        "CREATE INDEX IF NOT EXISTS ix_users_memory_opt_in ON users (memory_opt_in);",
        "CREATE INDEX IF NOT EXISTS ix_users_retention_nudges_opt_in ON users (retention_nudges_opt_in);",
        "CREATE INDEX IF NOT EXISTS ix_payment_transactions_refunded_at ON payment_transactions (refunded_at);",
        """
        CREATE TABLE IF NOT EXISTS sbp_autopay_subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            plan_code VARCHAR(64) NOT NULL DEFAULT 'sub_month',
            amount INTEGER NOT NULL DEFAULT 0,
            currency VARCHAR(8) NOT NULL DEFAULT 'RUB',
            interval_days INTEGER NOT NULL DEFAULT 30,
            payment_method_id VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            next_charge_at TIMESTAMPTZ NOT NULL,
            last_charged_at TIMESTAMPTZ NULL,
            last_payment_id VARCHAR(64) NULL,
            fail_count INTEGER NOT NULL DEFAULT 0,
            last_error VARCHAR(512) NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sbp_autopay_user_id ON sbp_autopay_subscriptions (user_id);",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_sbp_autopay_payment_method ON sbp_autopay_subscriptions (payment_method_id);",
        "CREATE INDEX IF NOT EXISTS ix_sbp_autopay_status_next_charge ON sbp_autopay_subscriptions (status, next_charge_at);",
        """
        CREATE TABLE IF NOT EXISTS click_orders (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            order_id VARCHAR(80) NOT NULL,
            plan_code VARCHAR(64) NOT NULL,
            amount INTEGER NOT NULL DEFAULT 0,
            currency VARCHAR(8) NOT NULL DEFAULT 'UZS',
            status VARCHAR(32) NOT NULL DEFAULT 'pending',
            click_trans_id VARCHAR(64) NULL,
            click_paydoc_id VARCHAR(64) NULL,
            merchant_prepare_id INTEGER NULL,
            provider_error INTEGER NULL,
            provider_error_note VARCHAR(256) NULL,
            prepare_payload JSON NULL,
            complete_payload JSON NULL,
            activation_applied BOOLEAN NOT NULL DEFAULT FALSE,
            paid_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_click_orders_order_id ON click_orders (order_id);",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_click_orders_click_trans_id ON click_orders (click_trans_id);",
        "CREATE INDEX IF NOT EXISTS ix_click_orders_user_id ON click_orders (user_id);",
        "CREATE INDEX IF NOT EXISTS ix_click_orders_status ON click_orders (status);",
        "CREATE INDEX IF NOT EXISTS ix_click_orders_plan_code ON click_orders (plan_code);",
        """
        CREATE TABLE IF NOT EXISTS support_tickets (
            id SERIAL PRIMARY KEY,
            ticket_id VARCHAR(64) NOT NULL,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            telegram_user_id BIGINT NOT NULL,
            source_chat_id BIGINT NOT NULL,
            support_chat_id BIGINT NULL,
            support_inbox_message_id BIGINT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'open',
            last_user_message_at TIMESTAMPTZ NULL,
            last_support_reply_at TIMESTAMPTZ NULL,
            closed_at TIMESTAMPTZ NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_support_tickets_ticket_id ON support_tickets (ticket_id);",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_user_id ON support_tickets (user_id);",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_status ON support_tickets (status);",
        "CREATE INDEX IF NOT EXISTS ix_support_tickets_updated_at ON support_tickets (updated_at);",
        """
        CREATE TABLE IF NOT EXISTS support_ticket_messages (
            id SERIAL PRIMARY KEY,
            ticket_pk INTEGER NOT NULL REFERENCES support_tickets(id) ON DELETE CASCADE,
            ticket_id VARCHAR(64) NOT NULL,
            direction VARCHAR(32) NOT NULL DEFAULT 'user_to_support',
            message_text TEXT NOT NULL DEFAULT '',
            sender_telegram_id BIGINT NULL,
            telegram_chat_id BIGINT NULL,
            telegram_message_id BIGINT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        "CREATE INDEX IF NOT EXISTS ix_support_ticket_messages_ticket_pk ON support_ticket_messages (ticket_pk);",
        "CREATE INDEX IF NOT EXISTS ix_support_ticket_messages_ticket_id ON support_ticket_messages (ticket_id);",
        "CREATE INDEX IF NOT EXISTS ix_support_ticket_messages_created_at ON support_ticket_messages (created_at);",
        """
        CREATE TABLE IF NOT EXISTS user_memory_events (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            source_kind VARCHAR(32) NOT NULL DEFAULT 'reading',
            source_id INTEGER NULL,
            event_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            topic VARCHAR(32) NOT NULL DEFAULT 'other',
            spread_type VARCHAR(32) NOT NULL DEFAULT '',
            question VARCHAR(1024) NOT NULL DEFAULT '',
            cards JSON NOT NULL DEFAULT '[]'::json,
            primary_card VARCHAR(128) NOT NULL DEFAULT '',
            primary_card_reversed BOOLEAN NOT NULL DEFAULT FALSE,
            sentiment_label VARCHAR(32) NOT NULL DEFAULT 'neutral',
            tags JSON NOT NULL DEFAULT '[]'::json,
            summary JSON NOT NULL DEFAULT '{}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        "CREATE INDEX IF NOT EXISTS ix_user_memory_events_user_id ON user_memory_events (user_id);",
        "CREATE INDEX IF NOT EXISTS ix_user_memory_events_event_at ON user_memory_events (event_at);",
        "CREATE INDEX IF NOT EXISTS ix_user_memory_events_topic ON user_memory_events (topic);",
        "CREATE INDEX IF NOT EXISTS ix_user_memory_events_spread_type ON user_memory_events (spread_type);",
        "CREATE INDEX IF NOT EXISTS ix_user_memory_events_primary_card ON user_memory_events (primary_card);",
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_memory_events_source ON user_memory_events (user_id, source_kind, source_id) WHERE source_id IS NOT NULL;",
        """
        CREATE TABLE IF NOT EXISTS user_memory_profiles (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            recurring_topics JSON NOT NULL DEFAULT '[]'::json,
            repeated_cards JSON NOT NULL DEFAULT '[]'::json,
            cycle_hints JSON NOT NULL DEFAULT '[]'::json,
            last_changes JSON NOT NULL DEFAULT '{}'::json,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_user_memory_profiles_user_id ON user_memory_profiles (user_id);",
        """
        CREATE TABLE IF NOT EXISTS retention_nudge_log (
            id SERIAL PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            nudge_type VARCHAR(32) NOT NULL DEFAULT 'daily',
            scheduled_for TIMESTAMPTZ NULL,
            sent_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            status VARCHAR(32) NOT NULL DEFAULT 'sent',
            payload JSON NULL,
            error TEXT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        "CREATE INDEX IF NOT EXISTS ix_retention_nudge_log_user_id ON retention_nudge_log (user_id);",
        "CREATE INDEX IF NOT EXISTS ix_retention_nudge_log_sent_at ON retention_nudge_log (sent_at);",
        "CREATE INDEX IF NOT EXISTS ix_retention_nudge_log_status ON retention_nudge_log (status);",
        """
        CREATE TABLE IF NOT EXISTS vision_escalation_counters (
            id SERIAL PRIMARY KEY,
            counter_key VARCHAR(64) NOT NULL DEFAULT 'photo_analysis',
            day_key VARCHAR(10) NOT NULL,
            total_requests INTEGER NOT NULL DEFAULT 0,
            escalated_requests INTEGER NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        "CREATE UNIQUE INDEX IF NOT EXISTS uq_vision_escalation_counter_day ON vision_escalation_counters (counter_key, day_key);",
        "CREATE INDEX IF NOT EXISTS ix_vision_escalation_counters_counter_key ON vision_escalation_counters (counter_key);",
        "CREATE INDEX IF NOT EXISTS ix_vision_escalation_counters_day_key ON vision_escalation_counters (day_key);",
    ]
    for stmt in statements:
        try:
            await conn.exec_driver_sql(stmt)
        except Exception as e:
            log.warning("schema check skipped for statement '%s': %s", stmt, repr(e))


async def _consume_reading_quota_or_raise(
    db: AsyncSession,
    user_id: int,
) -> dict:
    """
    Billing rules:
      1) Daily card is separate and always available once per day (/card-of-day).
      2) Other spreads: first FREE_READINGS_PER_MONTH per month are free.
      3) After free quota: consume paid_readings_balance OR active subscription.
    """
    user_res = await db.execute(select(User).where(User.id == int(user_id)))
    user = user_res.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    now = datetime.now(timezone.utc)
    sub_until = _to_utc(user.subscription_until)
    if sub_until and sub_until > now:
        return {"mode": "subscription", "subscription_until": sub_until.isoformat()}

    month_start, month_next = _month_bounds_utc(now)
    used_q = await db.execute(
        select(func.count(Reading.id)).where(
            Reading.user_id == int(user_id),
            Reading.created_at >= month_start,
            Reading.created_at < month_next,
        )
    )
    month_used = int(used_q.scalar() or 0)

    if month_used < FREE_READINGS_PER_MONTH:
        return {
            "mode": "free",
            "month_used": month_used,
            "free_left_after": max(0, FREE_READINGS_PER_MONTH - (month_used + 1)),
        }

    balance = int(user.paid_readings_balance or 0)
    if balance > 0:
        user.paid_readings_balance = balance - 1
        return {"mode": "credits", "balance_after": int(user.paid_readings_balance)}

    raise HTTPException(
        status_code=402,
        detail={
            "code": "READING_LIMIT_EXCEEDED",
            "message": (
                f"Бесплатный лимит ({FREE_READINGS_PER_MONTH} раскладов в месяц) исчерпан. "
                "Оплатите пакет/подписку в боте и попробуйте снова."
            ),
            "free_limit": FREE_READINGS_PER_MONTH,
            "month_used": month_used,
            "paid_balance": balance,
        },
    )


async def _memory_context_for_user(
    db: AsyncSession,
    user: User,
    *,
    question: str = "",
    current_cards: Optional[list] = None,
    include_summary: bool = False,
) -> tuple[dict, str, str]:
    """
    Returns: (summary, prompt_context, inline_hint)
    """
    if not FEATURE_FLAGS.memory_v1:
        return {}, "", ""
    if not bool(getattr(user, "memory_opt_in", False)):
        return {}, "", ""
    summary: dict = {}
    if include_summary:
        summary = await memory_service.get_summary(db, user_id=int(user.id))
        last_changes = dict(summary.get("last_changes") or {})
        needs_profile_refresh = (
            not last_changes.get("updated_at")
            or "home_hint" not in last_changes
            or "recurring_question_signals" not in last_changes
        )
        if needs_profile_refresh:
            try:
                await memory_service.backfill_user_history(
                    db,
                    user_id=int(user.id),
                    max_readings=1200,
                    max_card_days=365,
                )
                summary = await memory_service.rebuild_profile(db, user_id=int(user.id), days=90)
                await db.commit()
            except Exception as exc:
                await db.rollback()
                log.warning("Lazy memory init failed for user_id=%s: %s", user.id, repr(exc))

    prompt_context = ""
    if str(question or "").strip() or bool(list(current_cards or [])):
        try:
            prompt_context = await memory_service.build_runtime_prompt_context(
                db,
                user_id=int(user.id),
                question=question,
                current_cards=list(current_cards or []),
                days=90,
            )
        except Exception as exc:
            log.warning("Runtime memory context lookup failed for user_id=%s: %s", user.id, repr(exc))

    hint = ""
    return summary, prompt_context, hint


async def _ensure_card_day_memory_event(
    db: AsyncSession,
    *,
    user: User,
    row: CardOfDay,
    is_reversed_hint: Optional[bool] = None,
) -> None:
    if not FEATURE_FLAGS.memory_v1 or not bool(user.memory_opt_in):
        return
    reversed_flag = (
        bool(is_reversed_hint)
        if is_reversed_hint is not None
        else bool(getattr(row, "is_reversed", False))
    )
    try:
        cards_payload = memory_service.build_card_of_day_cards_payload(
            card_index=int(row.card_index),
            card_name=str(row.card_name or ""),
            is_reversed=reversed_flag,
        )
        inserted = await memory_service.ingest_event_if_missing(
            db,
            user_id=int(user.id),
            source_kind="card_of_day",
            source_id=int(row.id),
            topic=str(row.topic or "other"),
            spread_type="card_day",
            question=str(row.question or ""),
            cards=cards_payload,
            description=str(row.description or ""),
        )
        if inserted:
            await db.commit()
    except Exception as exc:
        await db.rollback()
        log.warning("Memory ingest failed for card_of_day id=%s: %s", row.id, repr(exc))


def _get_bearer_token(authorization: Optional[str]) -> str:
    if not authorization:
        raise HTTPException(status_code=401, detail="Missing Authorization header")
    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer" or not parts[1].strip():
        raise HTTPException(status_code=401, detail="Invalid Authorization header")
    return parts[1].strip()


async def get_current_user(
    db: AsyncSession = Depends(get_db),
    authorization: Optional[str] = Header(default=None),
) -> User:
    token = _get_bearer_token(authorization)
    try:
        payload = decode_jwt(token)
    except Exception:
        # Never leak JWT parsing details to clients.
        raise HTTPException(status_code=401, detail="Invalid or expired token")

    user_id = payload.get("user_id") or payload.get("sub")
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid token payload")

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=401, detail="User not found")
    return user


# ================================== SCHEMAS ==================================
class AuthOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class MeOut(BaseModel):
    id: int
    telegram_id: int
    username: Optional[str] = None
    first_name: Optional[str] = None
    last_name: Optional[str] = None
    photo_url: Optional[str] = None
    paid_readings_balance: int = 0
    subscription_until: Optional[datetime] = None
    has_active_subscription: bool = False
    memory_opt_in: bool = True
    retention_nudges_opt_in: bool = False
    retention_nudge_hour_local: Optional[int] = None
    retention_nudge_tz: Optional[str] = None


class MePreferencesIn(BaseModel):
    memory_opt_in: bool = True
    retention_nudges_opt_in: bool = False
    retention_nudge_hour_local: Optional[int] = Field(default=None, ge=0, le=23)
    retention_nudge_tz: Optional[str] = None


class MePreferencesOut(BaseModel):
    memory_opt_in: bool = True
    retention_nudges_opt_in: bool = False
    retention_nudge_hour_local: Optional[int] = None
    retention_nudge_tz: Optional[str] = None


class BillingStatusOut(BaseModel):
    free_limit: int
    month_used: int
    free_left: int
    paid_readings_balance: int
    subscription_until: Optional[datetime] = None
    has_active_subscription: bool = False
    can_create_reading: bool = False


class SbpCreateIn(BaseModel):
    plan_code: Literal["sub_2weeks", "sub_month", "sub_year"] = "sub_2weeks"


class SbpCreateOut(BaseModel):
    order_id: str
    plan_code: str
    amount: int
    currency: str
    payment_id: str
    status: str
    confirmation_url: str


class SbpStatusOut(BaseModel):
    order_id: str
    plan_code: str
    status: str
    amount: int
    currency: str
    paid_at: Optional[datetime] = None
    has_active_subscription: bool = False
    subscription_until: Optional[datetime] = None
    message: str = ""


class ClickCreateIn(BaseModel):
    plan_code: Literal["sub_week", "sub_2weeks", "sub_month", "sub_year"] = "sub_week"


class ClickCreateOut(BaseModel):
    order_id: str
    plan_code: str
    amount: int
    currency: str
    status: str
    payment_url: str


class ClickStatusOut(BaseModel):
    order_id: str
    plan_code: str
    status: str
    amount: int
    currency: str
    paid_at: Optional[datetime] = None
    has_active_subscription: bool = False
    subscription_until: Optional[datetime] = None
    message: str = ""


class CardOfDayCreateIn(BaseModel):
    topic: str = "other"
    question: str = ""
    consider_reversed: bool = True
    deck_size: int = 78
    force_llm: bool = False


class CardOfDayOut(BaseModel):
    day_key: str
    topic: str
    question: str
    card_index: int
    card_name: str
    is_reversed: bool = False
    description: str


class CardOfDayHistoryItem(CardOfDayOut):
    created_at: datetime


class ReadingCard(BaseModel):
    position: str
    title: str
    card_index: Optional[int] = None
    card_name: str
    is_reversed: bool = False
    meaning: str = ""


class ForcedReadingCardIn(BaseModel):
    card_index: int = Field(ge=0)
    is_reversed: bool = False


class ReadingCreateIn(BaseModel):
    spread_type: Literal["ppf", "three_cards", "decision", "custom"] = "three_cards"
    topic: str = "other"
    question: str = ""
    consider_reversed: bool = True
    deck_size: int = 78

    # decision
    option_a: str = ""
    option_b: str = ""

    # custom
    positions: List[str] = Field(default_factory=list)
    position_titles: List[str] = Field(default_factory=list)
    forced_cards: List[ForcedReadingCardIn] = Field(default_factory=list)

    extra_context: str = ""
    force_llm: bool = False


class ReadingOut(BaseModel):
    id: int
    spread_type: str
    topic: str
    question: str
    cards: List[ReadingCard]
    description: str
    created_at: datetime


class ReadingHistoryItem(ReadingOut):
    pass


class UnifiedHistoryItem(BaseModel):
    kind: Literal["card_of_day", "reading"]
    created_at: datetime
    payload: dict


class PhotoAnalysisOut(BaseModel):
    """
    Ответ по AI анализу фото расклада.
    cards — список найденных карт (если LLM смогла их распознать).
    """
    description: str
    cards: List[ReadingCard] = Field(default_factory=list)
    topic: str = "other"
    question: str = ""
    spread_type: str = "photo_analysis"


class PhotoFollowupIn(BaseModel):
    topic: str = "other"
    main_question: str = ""
    followup_question: str = ""
    cards: List[ReadingCard] = Field(default_factory=list)
    base_interpretation: str = ""
    extra_context: str = ""


class PhotoFollowupOut(BaseModel):
    description: str
    topic: str = "other"
    question: str = ""
    spread_type: str = "photo_analysis_followup"


# ================================== AUTH ==================================
@app.post("/auth/telegram", response_model=AuthOut)
async def auth_telegram(payload: dict, db: AsyncSession = Depends(get_db)):
    if "init_data" not in payload:
        raise HTTPException(status_code=400, detail="init_data required")

    try:
        tg_user = validate_init_data(payload["init_data"])
    except Exception:
        raise HTTPException(status_code=401, detail="Invalid Telegram initData")

    telegram_id = tg_user.get("id")
    if not telegram_id:
        raise HTTPException(status_code=400, detail="Invalid telegram user")

    result = await db.execute(select(User).where(User.telegram_id == telegram_id))
    user = result.scalar_one_or_none()

    if not user:
        user = User(
            telegram_id=telegram_id,
            username=tg_user.get("username"),
            first_name=tg_user.get("first_name"),
            last_name=tg_user.get("last_name"),
            photo_url=tg_user.get("photo_url"),
            memory_opt_in=True,
        )
        db.add(user)
        await db.commit()
        await db.refresh(user)

    token = create_jwt(user.id, user.telegram_id)
    return {"access_token": token}


@app.get("/me", response_model=MeOut)
async def me(current_user: User = Depends(get_current_user)):
    now = datetime.now(timezone.utc)
    sub_until = _to_utc(current_user.subscription_until)
    has_sub = bool(sub_until and sub_until > now)
    return {
        "id": current_user.id,
        "telegram_id": current_user.telegram_id,
        "username": current_user.username,
        "first_name": current_user.first_name,
        "last_name": current_user.last_name,
        "photo_url": current_user.photo_url,
        "paid_readings_balance": int(current_user.paid_readings_balance or 0),
        "subscription_until": sub_until,
        "has_active_subscription": has_sub,
        "memory_opt_in": (
            bool(current_user.memory_opt_in)
            if current_user.memory_opt_in is not None
            else True
        ),
        "retention_nudges_opt_in": bool(current_user.retention_nudges_opt_in),
        "retention_nudge_hour_local": (
            int(current_user.retention_nudge_hour_local)
            if current_user.retention_nudge_hour_local is not None
            else None
        ),
        "retention_nudge_tz": (str(current_user.retention_nudge_tz or "").strip() or None),
    }


@app.post("/me/preferences", response_model=MePreferencesOut)
async def update_me_preferences(
    payload: MePreferencesIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    was_memory_opt_in = bool(current_user.memory_opt_in)

    tz_name: Optional[str] = None
    if payload.retention_nudge_tz is not None:
        raw_tz = str(payload.retention_nudge_tz or "").strip()
        tz_name = resolve_tz_name(raw_tz) if raw_tz else None
    else:
        tz_name = str(current_user.retention_nudge_tz or "").strip() or None

    current_user.memory_opt_in = bool(payload.memory_opt_in)
    current_user.retention_nudges_opt_in = bool(payload.retention_nudges_opt_in)
    current_user.retention_nudge_hour_local = (
        int(payload.retention_nudge_hour_local)
        if payload.retention_nudge_hour_local is not None
        else None
    )
    current_user.retention_nudge_tz = tz_name

    if not current_user.memory_opt_in:
        current_user.retention_nudges_opt_in = False

    if not current_user.retention_nudges_opt_in:
        current_user.retention_nudge_hour_local = None

    await db.commit()
    await db.refresh(current_user)

    if FEATURE_FLAGS.memory_v1 and (not was_memory_opt_in) and bool(current_user.memory_opt_in):
        try:
            backfill_stats = await memory_service.backfill_user_history(
                db,
                user_id=int(current_user.id),
                max_readings=1200,
                max_card_days=365,
            )
            await memory_service.rebuild_profile(db, user_id=int(current_user.id), days=90)
            await db.commit()
            if any(int(backfill_stats.get(k) or 0) for k in ("readings_inserted", "card_days_inserted")):
                log.info(
                    "Memory backfill done: user_id=%s readings=%s card_days=%s",
                    current_user.id,
                    int(backfill_stats.get("readings_inserted") or 0),
                    int(backfill_stats.get("card_days_inserted") or 0),
                )
        except Exception as exc:
            await db.rollback()
            log.exception("Memory backfill failed for user_id=%s: %s", current_user.id, repr(exc))

    return {
        "memory_opt_in": bool(current_user.memory_opt_in),
        "retention_nudges_opt_in": bool(current_user.retention_nudges_opt_in),
        "retention_nudge_hour_local": (
            int(current_user.retention_nudge_hour_local)
            if current_user.retention_nudge_hour_local is not None
            else None
        ),
        "retention_nudge_tz": (str(current_user.retention_nudge_tz or "").strip() or None),
    }


@app.get("/memory/summary", response_model=MemorySummaryOut)
async def get_memory_summary(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not FEATURE_FLAGS.memory_v1:
        raise HTTPException(status_code=404, detail={"code": "FEATURE_DISABLED", "message": "Функция памяти выключена."})
    if not bool(current_user.memory_opt_in):
        raise HTTPException(
            status_code=403,
            detail={"code": "MEMORY_NOT_ENABLED", "message": "Включите «Память раскладов» в профиле."},
        )
    summary, _, _ = await _memory_context_for_user(db, current_user, include_summary=True)
    return MemorySummaryOut(**summary)


@app.get("/support/tickets/me", response_model=SupportTicketListOut)
async def support_tickets_me(
    limit: int = 30,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not FEATURE_FLAGS.tickets_v2:
        return SupportTicketListOut(items=[])
    rows = await support_ticket_service.list_user_tickets_with_counts(
        db,
        user_id=int(current_user.id),
        limit=max(1, min(int(limit), 200)),
    )
    items = [SupportTicketOut(**row) for row in rows]
    return SupportTicketListOut(items=items)


def _assert_analytics_access(current_user: User, x_analytics_token: Optional[str]) -> None:
    """
    Access policy:
      1) If ANALYTICS_ADMIN_TOKEN is configured -> require X-Analytics-Token header.
      2) Else if SUPPORT_ADMIN_IDS is configured -> require current user to be support admin.
      3) Else allow any authenticated user (single-tenant fallback).
    """
    if ANALYTICS_ADMIN_TOKEN:
        got = str(x_analytics_token or "").strip()
        if not got or not hmac.compare_digest(got, ANALYTICS_ADMIN_TOKEN):
            raise HTTPException(status_code=403, detail="Forbidden: invalid analytics token")
        return

    if SUPPORT_ADMIN_IDS and int(current_user.telegram_id) not in SUPPORT_ADMIN_IDS:
        raise HTTPException(status_code=403, detail="Forbidden: admin access required")


async def _resolve_analytics_excluded_user_ids(db: AsyncSession) -> List[int]:
    tg_ids = {
        int(x)
        for x in [*ANALYTICS_EXCLUDE_TELEGRAM_IDS, *SUPPORT_ADMIN_IDS]
        if int(x) > 0
    }
    if not tg_ids:
        return []
    q = await db.execute(
        select(User.id).where(User.telegram_id.in_(sorted(tg_ids)))
    )
    return sorted(
        {
            int(uid)
            for (uid,) in q.all()
            if uid is not None and int(uid) > 0
        }
    )


def _assert_analytics_token_only(
    *,
    x_analytics_token: Optional[str],
    token_query: Optional[str],
) -> None:
    if not ANALYTICS_ADMIN_TOKEN:
        raise HTTPException(
            status_code=503,
            detail="ANALYTICS_ADMIN_TOKEN is required for /analytics/dashboard",
        )
    got = str(token_query or x_analytics_token or "").strip()
    if not got or not hmac.compare_digest(got, ANALYTICS_ADMIN_TOKEN):
        raise HTTPException(status_code=403, detail="Forbidden: invalid analytics token")


def _analytics_fmt_num(value: Any) -> str:
    try:
        ivalue = int(round(float(value)))
    except Exception:
        ivalue = 0
    return f"{ivalue:,}".replace(",", " ")


def _analytics_fmt_pct(value: Any) -> str:
    try:
        ratio = float(value)
    except Exception:
        ratio = 0.0
    return f"{ratio * 100.0:.1f}%"


def _analytics_currency_hint(code: str) -> str:
    up = str(code or "").upper()
    if up == "UZS":
        return "сум"
    if up == "RUB":
        return "₽"
    return up or "валюта"


def _analytics_funnel_row_html(label: str, value: int, baseline: int) -> str:
    safe_value = max(0, int(value or 0))
    safe_base = max(1, int(baseline or 0))
    width = max(0.0, min(100.0, (float(safe_value) / float(safe_base)) * 100.0))
    return (
        "<div class='funnel-row'>"
        f"<div class='funnel-meta'><span>{escape(label)}</span><b>{_analytics_fmt_num(safe_value)}</b></div>"
        f"<div class='funnel-bar'><i style='width:{width:.2f}%'></i></div>"
        "</div>"
    )


def _render_analytics_dashboard_html(
    *,
    snapshot: Dict[str, Any],
    lookback_days: int,
    conversion_window_days: int,
    ad_spend_uzs: int,
    ad_spend_rub: int,
    token: Optional[str],
) -> str:
    trial = snapshot.get("trial_to_month") or {}
    unit = snapshot.get("unit_economics") or {}
    by_currency = snapshot.get("unit_economics_by_currency") or []
    activation = snapshot.get("activation_funnel") or {}
    photo = snapshot.get("photo_funnel") or {}
    paywall = snapshot.get("paywall_funnel") or {}
    retention = snapshot.get("retention_summary") or {}
    quality = snapshot.get("payment_quality") or {}
    ai_ops = snapshot.get("ai_operations") or {}
    generated_at = str(snapshot.get("generated_at") or datetime.now(timezone.utc).isoformat())

    refresh_q: Dict[str, Any] = {
        "lookback_days": int(lookback_days),
        "conversion_window_days": int(conversion_window_days),
        "ad_spend_uzs": int(ad_spend_uzs),
        "ad_spend_rub": int(ad_spend_rub),
    }
    if token:
        refresh_q["token"] = token
    refresh_url = f"/analytics/dashboard?{urlencode(refresh_q)}"

    currency_cards = ""
    for row in by_currency:
        code = str(row.get("currency") or "").upper() or "UNK"
        revenue = _analytics_fmt_num(row.get("revenue"))
        arppu = _analytics_fmt_num(row.get("arppu"))
        cac = _analytics_fmt_num(row.get("cac"))
        payback = float(row.get("payback_months") or 0.0)
        currency_cards += (
            "<article class='card'>"
            f"<h3>{escape(code)} • {_analytics_currency_hint(code)}</h3>"
            f"<p>Revenue: <b>{revenue}</b></p>"
            f"<p>ARPPU: <b>{arppu}</b></p>"
            f"<p>CAC: <b>{cac}</b></p>"
            f"<p>Payback: <b>{payback:.2f} мес</b></p>"
            "</article>"
        )
    if not currency_cards:
        currency_cards = "<article class='card'><p>Нет платежей за выбранный период.</p></article>"

    activation_base = int(activation.get("new_users") or 0)
    activation_rows = "".join(
        [
            _analytics_funnel_row_html("Новые", activation_base, activation_base),
            _analytics_funnel_row_html("Активировались", int(activation.get("activated_users") or 0), activation_base),
            _analytics_funnel_row_html("С фото", int(activation.get("photo_users") or 0), activation_base),
            _analytics_funnel_row_html("Дошли до лимита", int(activation.get("reached_free_limit_users") or 0), activation_base),
            _analytics_funnel_row_html("Взяли trial", int(activation.get("trial_users") or 0), activation_base),
            _analytics_funnel_row_html("Оплатили", int(activation.get("paid_users") or 0), activation_base),
        ]
    )

    photo_base = int(photo.get("photo_users") or 0)
    photo_rows = "".join(
        [
            _analytics_funnel_row_html("Пользователи фото", photo_base, photo_base),
            _analytics_funnel_row_html("Trial после фото", int(photo.get("converted_to_trial_users") or 0), photo_base),
            _analytics_funnel_row_html("Оплата после фото", int(photo.get("converted_to_paid_users") or 0), photo_base),
        ]
    )

    paywall_base = int(paywall.get("users_hit_limit") or 0)
    paywall_rows = "".join(
        [
            _analytics_funnel_row_html("Дошли до лимита", paywall_base, paywall_base),
            _analytics_funnel_row_html("Конвертировались", int(paywall.get("users_converted") or 0), paywall_base),
        ]
    )

    return f"""<!doctype html>
<html lang="ru">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover" />
  <title>AI Taro • Dashboard</title>
  <style>
    :root {{
      --bg: #061331;
      --bg2: #0b1f4f;
      --glass: rgba(20, 38, 88, 0.62);
      --text: #eef3ff;
      --muted: #aebeea;
      --line: rgba(255,255,255,0.14);
      --accent: #66a6ff;
      --accent2: #7a5cff;
      --good: #3ed598;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      min-height: 100vh;
      color: var(--text);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Inter, Roboto, sans-serif;
      background:
        radial-gradient(900px 520px at -10% -5%, rgba(122,92,255,0.26), transparent 62%),
        radial-gradient(820px 440px at 115% 0%, rgba(102,166,255,0.22), transparent 58%),
        linear-gradient(165deg, var(--bg), var(--bg2));
    }}
    .wrap {{
      width: min(760px, 100%);
      margin: 0 auto;
      padding: max(14px, env(safe-area-inset-top)) 14px calc(16px + env(safe-area-inset-bottom));
    }}
    .head {{
      position: sticky;
      top: 0;
      z-index: 5;
      margin: -2px -2px 10px;
      padding: 12px;
      border: 1px solid var(--line);
      border-radius: 16px;
      background: rgba(6, 18, 48, 0.82);
      backdrop-filter: blur(16px);
      display: flex;
      justify-content: space-between;
      gap: 10px;
      align-items: center;
    }}
    .head h1 {{ margin: 0 0 2px; font-size: 20px; }}
    .muted {{ margin: 0; color: var(--muted); font-size: 12px; }}
    .btn {{
      border: 0;
      border-radius: 12px;
      padding: 10px 12px;
      background: linear-gradient(90deg, var(--accent2), var(--accent));
      color: #fff;
      text-decoration: none;
      font-weight: 700;
      font-size: 13px;
      white-space: nowrap;
    }}
    .section {{ margin-top: 12px; }}
    .title {{ margin: 0 0 8px; font-size: 15px; color: #d4e0ff; }}
    .grid {{
      display: grid;
      grid-template-columns: repeat(2, minmax(0, 1fr));
      gap: 8px;
    }}
    .card {{
      border: 1px solid var(--line);
      border-radius: 14px;
      padding: 10px 11px;
      background: var(--glass);
      backdrop-filter: blur(14px);
    }}
    .card h3 {{ margin: 0 0 7px; font-size: 13px; color: #cfe0ff; }}
    .kpi-label {{ font-size: 12px; color: var(--muted); margin-bottom: 5px; }}
    .kpi-val {{ font-size: 22px; font-weight: 800; letter-spacing: 0.2px; }}
    .funnel-row {{ margin-bottom: 8px; }}
    .funnel-meta {{ display:flex; justify-content:space-between; font-size: 12px; margin-bottom: 4px; color:#d6e3ff; }}
    .funnel-bar {{
      width: 100%;
      height: 7px;
      border-radius: 999px;
      background: rgba(255,255,255,0.12);
      overflow: hidden;
    }}
    .funnel-bar i {{
      display: block;
      height: 100%;
      background: linear-gradient(90deg, var(--accent), var(--accent2));
      border-radius: inherit;
    }}
    .list p {{ margin: 6px 0; font-size: 13px; color: #dbe6ff; }}
    @media (max-width: 560px) {{
      .grid {{ grid-template-columns: 1fr; }}
      .kpi-val {{ font-size: 20px; }}
      .head h1 {{ font-size: 18px; }}
    }}
  </style>
</head>
<body>
  <main class="wrap">
    <header class="head">
      <div>
        <h1>AI Taro • Growth Dashboard</h1>
        <p class="muted">Обновлено: {escape(generated_at)}</p>
      </div>
      <a class="btn" href="{escape(refresh_url)}">Обновить</a>
    </header>

    <section class="section">
      <h2 class="title">Ключевые KPI</h2>
      <div class="grid">
        <article class="card"><div class="kpi-label">Trial → Month</div><div class="kpi-val">{_analytics_fmt_pct(trial.get("conversion_rate"))}</div></article>
        <article class="card"><div class="kpi-label">CAC (UZS)</div><div class="kpi-val">{_analytics_fmt_num(unit.get("cac_uzs"))}</div></article>
        <article class="card"><div class="kpi-label">Payback (UZS)</div><div class="kpi-val">{float(unit.get("payback_months") or 0.0):.2f} мес</div></article>
        <article class="card"><div class="kpi-label">Paid rate (new users)</div><div class="kpi-val">{_analytics_fmt_pct(activation.get("paid_rate"))}</div></article>
      </div>
    </section>

    <section class="section">
      <h2 class="title">Монетизация по валютам</h2>
      <div class="grid">{currency_cards}</div>
    </section>

    <section class="section">
      <h2 class="title">Воронка активации ({int(activation.get("lookback_days") or lookback_days)} дн)</h2>
      <article class="card">{activation_rows}</article>
    </section>

    <section class="section">
      <h2 class="title">Воронка фото-анализа ({int(photo.get("lookback_days") or lookback_days)} дн)</h2>
      <article class="card">{photo_rows}</article>
    </section>

    <section class="section">
      <h2 class="title">Воронка paywall ({int(paywall.get("window_days") or conversion_window_days)} дн)</h2>
      <article class="card">{paywall_rows}</article>
    </section>

    <section class="section">
      <h2 class="title">Retention и качество</h2>
      <div class="grid">
        <article class="card list">
          <h3>Retention</h3>
          <p>D1: <b>{_analytics_fmt_pct(retention.get("d1"))}</b></p>
          <p>D7: <b>{_analytics_fmt_pct(retention.get("d7"))}</b></p>
          <p>D30: <b>{_analytics_fmt_pct(retention.get("d30"))}</b></p>
          <p>Paid repeat 30d: <b>{_analytics_fmt_pct(retention.get("paid_repeat_30d_rate"))}</b></p>
        </article>
        <article class="card list">
          <h3>Платежи</h3>
          <p>Refund rate: <b>{_analytics_fmt_pct(quality.get("refund_rate"))}</b></p>
          <p>Click fail rate: <b>{_analytics_fmt_pct(quality.get("click_failure_rate"))}</b></p>
          <p>SBP fail rate: <b>{_analytics_fmt_pct(quality.get("sbp_failure_rate"))}</b></p>
          <p>Всего подписочных платежей: <b>{_analytics_fmt_num(quality.get("total_subscription_payments"))}</b></p>
        </article>
        <article class="card list">
          <h3>AI фото-операции</h3>
          <p>Фото-анализов: <b>{_analytics_fmt_num(ai_ops.get("photo_analyses_completed"))}</b></p>
          <p>Vision tracked: <b>{_analytics_fmt_num(ai_ops.get("vision_requests_tracked"))}</b></p>
          <p>Vision escalation: <b>{_analytics_fmt_pct(ai_ops.get("vision_escalation_rate"))}</b></p>
        </article>
        <article class="card list">
          <h3>Параметры</h3>
          <p>Lookback: <b>{int(lookback_days)} дн</b></p>
          <p>Conversion window: <b>{int(conversion_window_days)} дн</b></p>
          <p>Ad spend UZS: <b>{_analytics_fmt_num(ad_spend_uzs)}</b></p>
          <p>Ad spend RUB: <b>{_analytics_fmt_num(ad_spend_rub)}</b></p>
        </article>
      </div>
    </section>
  </main>
</body>
</html>"""


@app.get("/analytics/kpi", response_model=KpiOut)
async def analytics_kpi(
    x_analytics_token: Optional[str] = Header(default=None, alias="X-Analytics-Token"),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _assert_analytics_access(current_user, x_analytics_token)
    snapshot = await analytics_service.build_kpi_snapshot(
        db,
        free_limit=FREE_READINGS_PER_MONTH,
    )
    return KpiOut(**snapshot)


@app.get("/analytics/growth", response_model=GrowthOut)
async def analytics_growth(
    x_analytics_token: Optional[str] = Header(default=None, alias="X-Analytics-Token"),
    lookback_days: int = Query(default=ANALYTICS_GROWTH_LOOKBACK_DAYS, ge=7, le=180),
    conversion_window_days: int = Query(default=ANALYTICS_TRIAL_TO_MONTH_WINDOW_DAYS, ge=1, le=90),
    ad_spend_uzs: Optional[int] = Query(default=None, ge=0),
    ad_spend_rub: Optional[int] = Query(default=None, ge=0),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    _assert_analytics_access(current_user, x_analytics_token)
    safe_ad_spend_uzs = int(ANALYTICS_AD_SPEND_UZS if ad_spend_uzs is None else ad_spend_uzs)
    safe_ad_spend_rub = int(ANALYTICS_AD_SPEND_RUB if ad_spend_rub is None else ad_spend_rub)
    excluded_user_ids = await _resolve_analytics_excluded_user_ids(db)
    snapshot = await analytics_service.build_growth_snapshot(
        db,
        lookback_days=max(7, int(lookback_days)),
        conversion_window_days=max(1, int(conversion_window_days)),
        trial_product_code=ANALYTICS_TRIAL_PRODUCT_CODE,
        month_product_codes=ANALYTICS_MONTH_PRODUCT_CODES,
        ad_spend_uzs=safe_ad_spend_uzs,
        ad_spend_rub=safe_ad_spend_rub,
        ad_spend_by_currency={"UZS": safe_ad_spend_uzs, "RUB": safe_ad_spend_rub},
        free_limit=FREE_READINGS_PER_MONTH,
        exclude_user_ids=excluded_user_ids,
    )
    return GrowthOut(**snapshot)


@app.get("/analytics/dashboard", response_class=HTMLResponse)
async def analytics_dashboard(
    token: Optional[str] = Query(default=None),
    x_analytics_token: Optional[str] = Header(default=None, alias="X-Analytics-Token"),
    lookback_days: int = Query(default=ANALYTICS_GROWTH_LOOKBACK_DAYS, ge=7, le=180),
    conversion_window_days: int = Query(default=ANALYTICS_TRIAL_TO_MONTH_WINDOW_DAYS, ge=1, le=90),
    ad_spend_uzs: Optional[int] = Query(default=None, ge=0),
    ad_spend_rub: Optional[int] = Query(default=None, ge=0),
    db: AsyncSession = Depends(get_db),
):
    _assert_analytics_token_only(
        x_analytics_token=x_analytics_token,
        token_query=token,
    )
    safe_ad_spend_uzs = int(ANALYTICS_AD_SPEND_UZS if ad_spend_uzs is None else ad_spend_uzs)
    safe_ad_spend_rub = int(ANALYTICS_AD_SPEND_RUB if ad_spend_rub is None else ad_spend_rub)
    excluded_user_ids = await _resolve_analytics_excluded_user_ids(db)
    snapshot = await analytics_service.build_growth_snapshot(
        db,
        lookback_days=max(7, int(lookback_days)),
        conversion_window_days=max(1, int(conversion_window_days)),
        trial_product_code=ANALYTICS_TRIAL_PRODUCT_CODE,
        month_product_codes=ANALYTICS_MONTH_PRODUCT_CODES,
        ad_spend_uzs=safe_ad_spend_uzs,
        ad_spend_rub=safe_ad_spend_rub,
        ad_spend_by_currency={"UZS": safe_ad_spend_uzs, "RUB": safe_ad_spend_rub},
        free_limit=FREE_READINGS_PER_MONTH,
        exclude_user_ids=excluded_user_ids,
    )
    html = _render_analytics_dashboard_html(
        snapshot=snapshot,
        lookback_days=max(7, int(lookback_days)),
        conversion_window_days=max(1, int(conversion_window_days)),
        ad_spend_uzs=safe_ad_spend_uzs,
        ad_spend_rub=safe_ad_spend_rub,
        token=token,
    )
    return HTMLResponse(content=html, status_code=200)


@app.get("/billing/status", response_model=BillingStatusOut)
async def billing_status(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    now = datetime.now(timezone.utc)
    month_start, month_next = _month_bounds_utc(now)

    used_q = await db.execute(
        select(func.count(Reading.id)).where(
            Reading.user_id == int(current_user.id),
            Reading.created_at >= month_start,
            Reading.created_at < month_next,
        )
    )
    month_used = int(used_q.scalar() or 0)

    sub_until = _to_utc(current_user.subscription_until)
    has_sub = bool(sub_until and sub_until > now)
    free_left = max(0, FREE_READINGS_PER_MONTH - month_used)
    balance = int(current_user.paid_readings_balance or 0)

    return {
        "free_limit": FREE_READINGS_PER_MONTH,
        "month_used": month_used,
        "free_left": free_left,
        "paid_readings_balance": balance,
        "subscription_until": sub_until,
        "has_active_subscription": has_sub,
        "can_create_reading": bool(has_sub or free_left > 0 or balance > 0),
    }


async def _refresh_sbp_order_from_provider(order: SbpOrder) -> str:
    if not order.yookassa_payment_id or not _yookassa_sbp_configured():
        return str(order.status or "pending")

    provider = await _yookassa_request("GET", f"/payments/{order.yookassa_payment_id}")
    status = str(provider.get("status") or order.status or "pending").strip().lower()
    order.status = status
    order.provider_payload = provider
    if status == "succeeded" and not order.paid_at:
        order.paid_at = _parse_provider_dt(provider.get("paid_at")) or datetime.now(timezone.utc)
    return status


def _sbp_status_message(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "succeeded":
        return "Оплата подтверждена, подписка активирована."
    if normalized in {"canceled", "cancelled"}:
        return "Платёж отменён."
    if normalized == "pending":
        return "Ожидаем завершения оплаты в банке."
    if normalized == "waiting_for_capture":
        return "Платёж получен и подтверждается."
    return "Статус платежа обновляется."


def _click_status_message(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized == "succeeded":
        return "Оплата через CLICK подтверждена, подписка активирована."
    if normalized in {"cancelled", "canceled"}:
        return "Платёж CLICK отменён."
    if normalized == "prepared":
        return "Счёт в CLICK подготовлен, ожидаем завершения оплаты."
    if normalized == "pending":
        return "Счёт создан, ожидаем подтверждения от CLICK."
    return "Статус платежа обновляется."


def _format_sbp_success_bot_text(*, plan: Dict[str, Any], active_until: Optional[datetime], autopay_setup: bool) -> str:
    title = str(plan.get("title") or plan.get("code") or "Подписка").strip()
    until = _to_utc(active_until)
    until_text = until.strftime("%d.%m.%Y") if until else "активна"
    if autopay_setup:
        return (
            f"Оплата по СБП прошла успешно.\n"
            f"Подписка «{title}» активирована до {until_text}.\n"
            f"СБП автоплатёж подключён: продление будет выполняться автоматически."
        )
    return (
        f"Оплата по СБП прошла успешно.\n"
        f"Подписка «{title}» активирована до {until_text}."
    )


def _support_reply_markup_payload(*, ticket_id: Optional[str] = None) -> Dict[str, Any]:
    safe_ticket = str(ticket_id or "").strip()
    support_cb = f"support:{safe_ticket}" if safe_ticket else "support"
    close_cb = f"support_close:{safe_ticket}" if safe_ticket else "support_close"
    return {
        "inline_keyboard": [
            [
                {"text": "💬 Ответить", "callback_data": support_cb},
                {"text": "✅ Закрыть диалог", "callback_data": close_cb},
            ]
        ]
    }


def _support_status_human(status: str) -> str:
    s = str(status or "").strip().lower()
    mapping = {
        "open": "Открыт",
        "pending_support": "Ожидает поддержки",
        "pending_user": "Ожидает пользователя",
        "closed": "Закрыт",
    }
    return mapping.get(s, s or "Открыт")


def _support_inbox_reply_markup_payload(*, ticket_id: str, user_id: int, status: str) -> Dict[str, Any]:
    safe_ticket = str(ticket_id or "").strip().upper()
    safe_uid = int(user_id)
    rows: list[list[Dict[str, str]]] = [
        [
            {"text": "💬 Ответить", "callback_data": f"support_reply:{safe_ticket}:{safe_uid}"},
            {"text": "⏸ Pending", "callback_data": f"support_pending:{safe_ticket}:{safe_uid}"},
        ]
    ]
    if str(status or "").strip().lower() == "closed":
        rows.append([{"text": "♻️ Переоткрыть", "callback_data": f"support_reopen:{safe_ticket}:{safe_uid}"}])
    else:
        rows.append([{"text": "✅ Закрыть", "callback_data": f"support_close:{safe_ticket}:{safe_uid}"}])
    return {"inline_keyboard": rows}


def _support_ticket_card_text(row: Dict[str, Any]) -> str:
    ticket_id = str(row.get("ticket_id") or "").strip().upper()
    user_id = int(row.get("telegram_user_id") or 0)
    status = _support_status_human(str(row.get("status") or "open"))
    msg_count = int(row.get("messages_count") or 0)
    updated_at = _to_utc(row.get("updated_at"))
    updated_label = updated_at.strftime("%d.%m %H:%M UTC") if updated_at else "—"
    return (
        "🎫 <b>Тикет</b>\n"
        f"ID: <code>{escape(ticket_id)}</code>\n"
        f"Пользователь: <code>{user_id}</code>\n"
        f"Статус: <b>{escape(status)}</b>\n"
        f"Сообщений: <b>{msg_count}</b>\n"
        f"Обновлён: {escape(updated_label)}"
    )


async def _send_bot_message(
    chat_id: int,
    text: str,
    *,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> bool:
    token = (TELEGRAM_BOT_TOKEN or os.getenv("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        log.warning("SBP notify skipped: TELEGRAM_BOT_TOKEN is empty in API context")
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {
        "chat_id": int(chat_id),
        "text": str(text or "").strip(),
        "disable_web_page_preview": True,
    }
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 400:
                log.warning("sendMessage failed: code=%s body=%s", resp.status_code, resp.text[:400])
                return False
            data = resp.json()
            return bool(data.get("ok"))
    except Exception as exc:
        log.warning("sendMessage exception: %s", repr(exc))
        return False


async def _send_bot_message_with_token(
    *,
    token: str,
    chat_id: int,
    text: str,
    parse_mode: Optional[str] = None,
    disable_web_page_preview: bool = True,
    reply_to_message_id: Optional[int] = None,
    reply_markup: Optional[Dict[str, Any]] = None,
) -> bool:
    bot_token = str(token or "").strip()
    if not bot_token:
        return False
    payload: Dict[str, Any] = {
        "chat_id": int(chat_id),
        "text": str(text or "").strip(),
        "disable_web_page_preview": bool(disable_web_page_preview),
    }
    if parse_mode:
        payload["parse_mode"] = str(parse_mode)
    if reply_to_message_id:
        payload["reply_to_message_id"] = int(reply_to_message_id)
    if reply_markup:
        payload["reply_markup"] = reply_markup
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(url, json=payload)
            if resp.status_code >= 400:
                log.warning("sendMessage(token) failed: code=%s body=%s", resp.status_code, resp.text[:400])
                return False
            data = resp.json()
            return bool(data.get("ok"))
    except Exception as exc:
        log.warning("sendMessage(token) exception: %s", repr(exc))
        return False


def _support_inbox_enabled() -> bool:
    return bool(SUPPORT_INBOX_BOT_TOKEN and SUPPORT_INBOX_CHAT_ID)


_SUPPORT_REPLY_PENDING: Dict[tuple[int, int], Dict[str, Any]] = {}


def _support_reply_pending_key(chat_id: int, operator_id: int) -> tuple[int, int]:
    return (int(chat_id), int(operator_id))


async def _answer_support_callback_query(
    *,
    callback_query_id: str,
    text: Optional[str] = None,
    show_alert: bool = False,
) -> None:
    token = str(SUPPORT_INBOX_BOT_TOKEN or "").strip()
    if not token:
        return
    cb_id = str(callback_query_id or "").strip()
    if not cb_id:
        return

    url = f"https://api.telegram.org/bot{token}/answerCallbackQuery"
    payload: Dict[str, Any] = {"callback_query_id": cb_id}
    if text:
        payload["text"] = str(text)[:200]
        payload["show_alert"] = bool(show_alert)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=payload)
    except Exception as exc:
        log.warning("answerCallbackQuery failed: %s", repr(exc))


def _extract_support_target_user_id(*, text: str, caption: str) -> Optional[int]:
    src = f"{str(text or '')}\n{str(caption or '')}"
    m = re.search(r"\bID:\s*(-?\d+)\b", src, flags=re.IGNORECASE)
    if not m:
        return None
    raw = str(m.group(1) or "").strip()
    if not raw.lstrip("-").isdigit():
        return None
    target = int(raw)
    if target == 0:
        return None
    return target


def _extract_support_ticket_id(*, text: str, caption: str) -> str:
    src = f"{str(text or '')}\n{str(caption or '')}"
    plain = re.sub(r"<[^>]+>", " ", src)
    m = re.search(r"\bТикет:\s*([A-Z0-9\-]+)\b", plain, flags=re.IGNORECASE)
    if not m:
        return ""
    return str(m.group(1) or "").strip().upper()


async def _configure_support_inbox_bot() -> None:
    if not _support_inbox_enabled():
        return
    if not SUPPORT_INBOX_WEBHOOK_SECRET:
        log.warning("Support inbox webhook is disabled: SUPPORT_INBOX_WEBHOOK_SECRET is empty")
        return
    webhook_url = str(SUPPORT_INBOX_WEBHOOK_URL or "").strip()
    if not webhook_url:
        log.warning("Support inbox webhook is disabled: SUPPORT_INBOX_WEBHOOK_URL is empty")
        return

    token = SUPPORT_INBOX_BOT_TOKEN
    set_webhook_url = f"https://api.telegram.org/bot{token}/setWebhook"
    get_webhook_url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
    set_commands_url = f"https://api.telegram.org/bot{token}/setMyCommands"

    commands = [
        {"command": "start", "description": "Справка по обработке тикетов"},
        {"command": "reply", "description": "Ответить пользователю: /reply <id> <текст>"},
        {"command": "metrics", "description": "Рост: trial→month, CAC, payback"},
        {"command": "dashboard", "description": "Открыть мобильный growth dashboard"},
    ]
    help_text = (
        "✅ Support inbox подключен.\n"
        "Ответы можно давать двумя способами:\n"
        "1) /reply <telegram_id> <текст>\n"
        "2) Ответить реплаем на сообщение тикета."
    )

    try:
        async with httpx.AsyncClient(timeout=20.0) as client:
            await client.post(
                set_webhook_url,
                data={
                    "url": webhook_url,
                    "secret_token": SUPPORT_INBOX_WEBHOOK_SECRET,
                    "drop_pending_updates": False,
                    "allowed_updates": json.dumps(["message", "callback_query"]),
                },
            )
            await client.post(set_commands_url, data={"commands": json.dumps(commands, ensure_ascii=False)})
            info = await client.get(get_webhook_url)
            if info.status_code < 400:
                info_obj = info.json().get("result") or {}
                current_url = str(info_obj.get("url") or "")
                pending = int(info_obj.get("pending_update_count") or 0)
                if current_url != webhook_url:
                    log.warning("Support inbox webhook url mismatch: got=%s expected=%s", current_url, webhook_url)
                else:
                    log.info("Support inbox webhook configured: pending_updates=%s", pending)
            # Optional one-time ping to support inbox chat.
            await _send_bot_message_with_token(
                token=token,
                chat_id=int(SUPPORT_INBOX_CHAT_ID),
                text=help_text,
            )
    except Exception as exc:
        log.warning("Failed to configure support inbox bot: %s", repr(exc))


def _extract_saved_payment_method_id(provider_payload: Dict[str, Any]) -> str:
    try:
        payment_method = provider_payload.get("payment_method") or {}
        if not isinstance(payment_method, dict):
            return ""
        if str(payment_method.get("type") or "").strip().lower() != "sbp":
            return ""
        method_id = str(payment_method.get("id") or "").strip()
        if not method_id:
            return ""
        saved = payment_method.get("saved")
        if saved is False:
            return ""
        return method_id
    except Exception:
        return ""


async def _activate_sbp_autopay_if_possible(
    db: AsyncSession,
    *,
    user: User,
    plan_code: str,
    provider_payload: Dict[str, Any],
    provider_payment_id: str,
    paid_at: Optional[datetime] = None,
) -> bool:
    if not SBP_AUTOPAY_ENABLED:
        return False
    if not _is_sbp_autopay_plan(plan_code):
        return False

    payment_method_id = _extract_saved_payment_method_id(provider_payload)
    if not payment_method_id:
        return False

    plan = _get_sbp_plan(plan_code)
    now = _to_utc(paid_at) or datetime.now(timezone.utc)
    next_charge_at = now + timedelta(days=SBP_AUTOPAY_INTERVAL_DAYS)

    q = await db.execute(select(SbpAutopaySubscription).where(SbpAutopaySubscription.user_id == int(user.id)))
    sub = q.scalar_one_or_none()
    if not sub:
        sub = SbpAutopaySubscription(
            user_id=int(user.id),
            plan_code=str(plan["code"]),
            amount=int(plan["amount"]),
            currency=str(plan.get("currency") or "RUB"),
            interval_days=int(SBP_AUTOPAY_INTERVAL_DAYS),
            payment_method_id=payment_method_id,
            status="active",
            next_charge_at=next_charge_at,
            last_charged_at=now,
            last_payment_id=str(provider_payment_id or "") or None,
            fail_count=0,
            last_error=None,
        )
        db.add(sub)
    else:
        sub.plan_code = str(plan["code"])
        sub.amount = int(plan["amount"])
        sub.currency = str(plan.get("currency") or "RUB")
        sub.interval_days = int(SBP_AUTOPAY_INTERVAL_DAYS)
        sub.payment_method_id = payment_method_id
        sub.status = "active"
        sub.next_charge_at = next_charge_at
        sub.last_charged_at = now
        sub.last_payment_id = str(provider_payment_id or "") or sub.last_payment_id
        sub.fail_count = 0
        sub.last_error = None
    return True


async def _process_due_autopay_subscriptions(db: AsyncSession, *, limit: int = 10) -> int:
    if not SBP_AUTOPAY_ENABLED or not _yookassa_sbp_configured():
        return 0

    now = datetime.now(timezone.utc)
    q = await db.execute(
        select(SbpAutopaySubscription)
        .where(
            SbpAutopaySubscription.status.in_(["active", "pending", "past_due"]),
            SbpAutopaySubscription.next_charge_at <= now,
        )
        .order_by(SbpAutopaySubscription.next_charge_at.asc())
        .limit(max(1, int(limit)))
    )
    due_items = list(q.scalars().all())
    if not due_items:
        return 0

    processed = 0
    for sub in due_items:
        plan_code = str(sub.plan_code or SBP_AUTOPAY_PLAN_CODE).strip().lower()
        if not _is_sbp_autopay_plan(plan_code):
            sub.status = "disabled"
            sub.last_error = "autopay_disabled_for_plan"
            continue

        try:
            plan = _get_sbp_plan(plan_code)
        except Exception:
            sub.status = "disabled"
            sub.last_error = "unknown_plan"
            continue

        due_at = _to_utc(sub.next_charge_at) or now
        cycle_key = due_at.strftime("%Y%m%d")
        idempotence_key = f"sbp_auto_{int(sub.id)}_{cycle_key}"
        payload = {
            "amount": {
                "value": _rub_value_from_kopecks(int(sub.amount or plan["amount"])),
                "currency": str(sub.currency or plan.get("currency") or "RUB"),
            },
            "capture": True,
            "payment_method_id": str(sub.payment_method_id or "").strip(),
            "description": f"{str(plan.get('description') or plan.get('title') or 'Подписка')} (СБП автоплатёж)",
            "metadata": {
                "autopay_mode": "renewal",
                "autopay_subscription_id": str(sub.id),
                "plan_code": str(plan["code"]),
                "user_id": str(sub.user_id),
                "cycle_key": cycle_key,
            },
        }

        try:
            provider = await _yookassa_request(
                "POST",
                "/payments",
                payload=payload,
                idempotence_key=idempotence_key,
            )
            processed += 1
        except HTTPException as exc:
            detail = exc.detail if isinstance(exc.detail, dict) else {}
            code = str(detail.get("code") or "SBP_PROVIDER_ERROR")
            msg = str(detail.get("message") or "").strip() or code
            sub.fail_count = int(sub.fail_count or 0) + 1
            sub.status = "disabled" if int(sub.fail_count) >= SBP_AUTOPAY_MAX_FAILS else "past_due"
            sub.last_error = f"{code}: {msg}"[:500]
            sub.next_charge_at = now + timedelta(hours=12)
            continue
        except Exception as exc:
            sub.fail_count = int(sub.fail_count or 0) + 1
            sub.status = "disabled" if int(sub.fail_count) >= SBP_AUTOPAY_MAX_FAILS else "past_due"
            sub.last_error = f"runtime_error: {repr(exc)}"[:500]
            sub.next_charge_at = now + timedelta(hours=12)
            continue

        payment_id = str(provider.get("id") or "").strip()
        status = str(provider.get("status") or "").strip().lower()
        sub.last_payment_id = payment_id or sub.last_payment_id

        if status == "succeeded" and payment_id:
            user_q = await db.execute(select(User).where(User.id == int(sub.user_id)))
            user = user_q.scalar_one_or_none()
            if user:
                await _apply_subscription_from_sbp(
                    db,
                    user=user,
                    plan=plan,
                    provider_payment_id=payment_id,
                    order_id=f"sbp_auto_{sub.id}_{cycle_key}",
                )
            sub.status = "active"
            sub.fail_count = 0
            sub.last_error = None
            sub.last_charged_at = _parse_provider_dt(provider.get("paid_at")) or now
            base_next = due_at if due_at > now else now
            sub.next_charge_at = base_next + timedelta(days=max(1, int(sub.interval_days or SBP_AUTOPAY_INTERVAL_DAYS)))
        elif status in {"pending", "waiting_for_capture"}:
            sub.status = "pending"
            sub.last_error = None
            sub.next_charge_at = now + timedelta(hours=2)
        else:
            sub.fail_count = int(sub.fail_count or 0) + 1
            sub.status = "disabled" if int(sub.fail_count) >= SBP_AUTOPAY_MAX_FAILS else "past_due"
            sub.last_error = f"provider_status:{status or 'unknown'}"[:500]
            sub.next_charge_at = now + timedelta(hours=12)

    return processed


async def _retry_sbp_success_notifications(db: AsyncSession, *, limit: int = 20) -> int:
    """
    Retry notification/activation for succeeded SBP orders.
    Covers cases when webhook/status path succeeded but Telegram send failed transiently.
    """
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=7)

    q = await db.execute(
        select(SbpOrder)
        .where(
            SbpOrder.status == "succeeded",
            SbpOrder.success_notified.is_(False),
            SbpOrder.created_at >= cutoff,
        )
        .order_by(desc(SbpOrder.created_at))
        .limit(max(1, int(limit))),
    )
    rows = q.scalars().all()
    if not rows:
        return 0

    sent_count = 0
    for order in rows:
        user_q = await db.execute(select(User).where(User.id == int(order.user_id)))
        user = user_q.scalar_one_or_none()
        if not user:
            continue

        # Safety net: if order is succeeded but activation flag wasn't set,
        # apply subscription idempotently by provider payment id.
        if not bool(order.activation_applied):
            try:
                plan = _get_sbp_plan(order.plan_code)
                await _apply_subscription_from_sbp(
                    db,
                    user=user,
                    plan=plan,
                    provider_payment_id=str(order.yookassa_payment_id or ""),
                    order_id=str(order.order_id),
                )
                order.activation_applied = True
                if not order.paid_at:
                    order.paid_at = now
            except Exception as exc:
                log.warning(
                    "SBP retry activation failed: order_id=%s user_id=%s err=%s",
                    order.order_id,
                    user.id,
                    repr(exc),
                )
                continue

        try:
            plan_for_msg = _get_sbp_plan(order.plan_code)
        except Exception:
            plan_for_msg = {"title": str(order.plan_code or "Подписка")}

        payload_obj = order.provider_payload if isinstance(order.provider_payload, dict) else {}
        md = payload_obj.get("metadata") if isinstance(payload_obj, dict) else {}
        autopay_setup = bool(
            isinstance(md, dict) and str(md.get("autopay_mode") or "").strip().lower() == "setup"
        )
        text = _format_sbp_success_bot_text(
            plan=plan_for_msg,
            active_until=_to_utc(user.subscription_until),
            autopay_setup=autopay_setup,
        )
        sent = await _send_bot_message(int(user.telegram_id), text)
        if sent:
            order.success_notified = True
            sent_count += 1

    return sent_count


async def _run_autopay_maintenance(*, force: bool = False) -> None:
    global _autopay_last_run_ts

    now_ts = time.time()
    if not force and (now_ts - _autopay_last_run_ts) < SBP_AUTOPAY_WORKER_INTERVAL_SEC:
        return
    if _autopay_lock.locked():
        return

    async with _autopay_lock:
        now_ts = time.time()
        if not force and (now_ts - _autopay_last_run_ts) < SBP_AUTOPAY_WORKER_INTERVAL_SEC:
            return
        _autopay_last_run_ts = now_ts

        async with SessionLocal() as db:
            try:
                processed = 0
                if SBP_AUTOPAY_ENABLED:
                    processed = await _process_due_autopay_subscriptions(db, limit=10)
                retried = await _retry_sbp_success_notifications(db, limit=20)
                retention_stats: Dict[str, int] = {"purged_events": 0, "nudges_checked": 0, "nudges_sent": 0, "nudges_failed": 0}
                if FEATURE_FLAGS.nudges_v1:
                    retention_stats = await run_retention_cycle(
                        db,
                        send_message=_send_bot_message,
                        memory_retention_days=90,
                    )
                await db.commit()
                if processed or retried or int(retention_stats.get("purged_events") or 0) or int(retention_stats.get("nudges_sent") or 0):
                    log.info(
                        "Maintenance: autopay_processed=%s success_retried=%s memory_purged=%s nudges_sent=%s/%s",
                        processed,
                        retried,
                        int(retention_stats.get("purged_events") or 0),
                        int(retention_stats.get("nudges_sent") or 0),
                        int(retention_stats.get("nudges_checked") or 0),
                    )
            except Exception as exc:
                await db.rollback()
                log.exception("SBP autopay maintenance failed: %s", repr(exc))


async def _autopay_worker_loop() -> None:
    while True:
        try:
            await _run_autopay_maintenance(force=True)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("SBP autopay worker loop error: %s", repr(exc))
        await asyncio.sleep(SBP_AUTOPAY_WORKER_INTERVAL_SEC)


@app.post("/support/inbox/webhook")
async def support_inbox_webhook(
    payload: Dict[str, Any],
    x_telegram_bot_api_secret_token: Optional[str] = Header(default=None, alias="X-Telegram-Bot-Api-Secret-Token"),
    db: AsyncSession = Depends(get_db),
):
    if not _support_inbox_enabled():
        return {"ok": True, "ignored": "support_inbox_not_enabled"}
    if not SUPPORT_INBOX_WEBHOOK_SECRET:
        raise HTTPException(status_code=503, detail="Support webhook secret is not configured")
    if str(x_telegram_bot_api_secret_token or "").strip() != SUPPORT_INBOX_WEBHOOK_SECRET:
        raise HTTPException(status_code=401, detail="Invalid support webhook secret")

    def _usage_text() -> str:
        return (
            "💬 Ответы поддержки\n\n"
            "• Ответьте в тикет через кнопку «Ответить»\n"
            "• /reply <telegram_id> <текст>\n"
            "• /metrics [days] [ad_spend_uzs] [window] [ad_spend_rub]\n"
            "• /dashboard [days] [window] — открыть мобильный dashboard\n"
            "• /open, /pending, /closed — фильтры тикетов\n"
            "• /ticket <ID> — карточка тикета\n\n"
            "Ответ будет доставлен пользователю в @Ttaarrroobot."
        )

    def _fmt_num(value: Any) -> str:
        try:
            ivalue = int(round(float(value)))
        except Exception:
            ivalue = 0
        return f"{ivalue:,}".replace(",", " ")

    def _fmt_pct(value: Any) -> str:
        try:
            ratio = float(value)
        except Exception:
            ratio = 0.0
        return f"{ratio * 100.0:.1f}%"

    def _parse_metrics_args(raw: str) -> tuple[int, int, int, int]:
        lookback_days = int(ANALYTICS_GROWTH_LOOKBACK_DAYS)
        conversion_window_days = int(ANALYTICS_TRIAL_TO_MONTH_WINDOW_DAYS)
        ad_spend_uzs = int(ANALYTICS_AD_SPEND_UZS)
        ad_spend_rub = int(ANALYTICS_AD_SPEND_RUB)
        numeric_args: List[int] = []
        for token in str(raw or "").split()[1:]:
            item = str(token or "").strip()
            if not item:
                continue
            if "=" in item:
                key, value = item.split("=", 1)
                k = str(key or "").strip().lower()
                v = str(value or "").strip()
                if not v.lstrip("-").isdigit():
                    continue
                n = int(v)
                if k in {"days", "day", "d", "lookback", "lb"}:
                    lookback_days = max(7, min(180, n))
                elif k in {"window", "win", "w"}:
                    conversion_window_days = max(1, min(90, n))
                elif k in {"ad", "spend", "ads", "budget", "ad_spend", "ad_uzs", "spend_uzs"}:
                    ad_spend_uzs = max(0, n)
                elif k in {"ad_rub", "spend_rub", "rub"}:
                    ad_spend_rub = max(0, n)
                continue
            if item.lstrip("-").isdigit():
                numeric_args.append(int(item))
        if numeric_args:
            lookback_days = max(7, min(180, numeric_args[0]))
        if len(numeric_args) >= 2:
            ad_spend_uzs = max(0, numeric_args[1])
        if len(numeric_args) >= 3:
            conversion_window_days = max(1, min(90, numeric_args[2]))
        if len(numeric_args) >= 4:
            ad_spend_rub = max(0, numeric_args[3])
        return lookback_days, conversion_window_days, ad_spend_uzs, ad_spend_rub

    def _parse_cb_ticket_target(raw_data: str) -> tuple[str, Optional[int]]:
        raw_part = str(raw_data.split(":", 1)[1] if ":" in raw_data else "").strip()
        raw_ticket = ""
        raw_target = ""
        if ":" in raw_part:
            raw_ticket, raw_target = raw_part.rsplit(":", 1)
            raw_ticket = str(raw_ticket or "").strip().upper()
            raw_target = str(raw_target or "").strip()
        elif raw_part.lstrip("-").isdigit():
            raw_target = raw_part
        target_user_id: Optional[int] = None
        if raw_target and raw_target.lstrip("-").isdigit():
            target_user_id = int(raw_target)
        return raw_ticket, target_user_id

    callback_query = payload.get("callback_query") or {}
    if isinstance(callback_query, dict) and callback_query:
        cb_id = str(callback_query.get("id") or "").strip()
        cb_data = str(callback_query.get("data") or "").strip()
        cb_from = callback_query.get("from") or {}
        cb_msg = callback_query.get("message") or {}
        cb_chat = cb_msg.get("chat") or {} if isinstance(cb_msg, dict) else {}

        if bool(cb_from.get("is_bot")):
            await _answer_support_callback_query(callback_query_id=cb_id)
            return {"ok": True, "ignored": "bot_callback"}

        chat_id = int(cb_chat.get("id") or 0)
        from_id = int(cb_from.get("id") or 0)
        if SUPPORT_INBOX_CHAT_ID and chat_id != int(SUPPORT_INBOX_CHAT_ID):
            await _answer_support_callback_query(callback_query_id=cb_id)
            return {"ok": True, "ignored": "foreign_chat"}
        if SUPPORT_ADMIN_IDS and from_id not in SUPPORT_ADMIN_IDS:
            await _answer_support_callback_query(callback_query_id=cb_id, text="Нет доступа", show_alert=True)
            return {"ok": True, "ignored": "not_admin"}

        if cb_data.startswith("support_reply:"):
            raw_ticket, target_user_id = _parse_cb_ticket_target(cb_data)
            if not target_user_id:
                await _answer_support_callback_query(
                    callback_query_id=cb_id,
                    text="Не удалось определить пользователя",
                    show_alert=True,
                )
                return {"ok": True, "handled": "invalid_target"}

            if raw_ticket and FEATURE_FLAGS.tickets_v2:
                ticket = await support_ticket_repository.get_ticket_by_public_id(db, raw_ticket)
                if ticket and str(ticket.status or "").strip().lower() == "closed":
                    await support_ticket_service.set_ticket_status(
                        db,
                        ticket=ticket,
                        status="open",
                        system_text="Тикет переоткрыт оператором.",
                        actor_telegram_id=from_id,
                        actor_chat_id=chat_id,
                    )
                    await db.commit()

            pending_key = _support_reply_pending_key(chat_id, from_id)
            _SUPPORT_REPLY_PENDING[pending_key] = {
                "target_user_id": target_user_id,
                "ticket_id": raw_ticket,
            }

            await _answer_support_callback_query(
                callback_query_id=cb_id,
                text="Теперь отправьте текст ответа одним сообщением",
                show_alert=False,
            )
            reply_to_message_id = None
            if isinstance(cb_msg, dict):
                reply_to_message_id = int(cb_msg.get("message_id") or 0) or None
            await _send_bot_message_with_token(
                token=SUPPORT_INBOX_BOT_TOKEN,
                chat_id=chat_id,
                text=(
                    f"✍️ Режим ответа включён для пользователя <code>{target_user_id}</code>"
                    + (f"\nТикет: <code>{escape(raw_ticket)}</code>" if raw_ticket else "")
                    + "\n"
                    "Отправьте текст одним сообщением.\n"
                    "Отмена: /cancel"
                ),
                parse_mode="HTML",
                reply_to_message_id=reply_to_message_id,
            )
            return {
                "ok": True,
                "handled": "pending_reply_set",
                "target_user_id": target_user_id,
                "ticket_id": raw_ticket or None,
            }

        if cb_data.startswith("support_pending:"):
            raw_ticket, target_user_id = _parse_cb_ticket_target(cb_data)
            if raw_ticket and FEATURE_FLAGS.tickets_v2:
                ticket = await support_ticket_repository.get_ticket_by_public_id(db, raw_ticket)
                if ticket:
                    await support_ticket_service.set_ticket_status(
                        db,
                        ticket=ticket,
                        status="pending_user",
                        system_text="Оператор перевёл тикет в pending.",
                        actor_telegram_id=from_id,
                        actor_chat_id=chat_id,
                    )
                    await db.commit()
            await _answer_support_callback_query(
                callback_query_id=cb_id,
                text="Статус: pending",
                show_alert=False,
            )
            return {"ok": True, "handled": "support_pending", "ticket_id": raw_ticket or None, "target_user_id": target_user_id}

        if cb_data.startswith("support_reopen:"):
            raw_ticket, target_user_id = _parse_cb_ticket_target(cb_data)
            if raw_ticket and FEATURE_FLAGS.tickets_v2:
                ticket = await support_ticket_repository.get_ticket_by_public_id(db, raw_ticket)
                if ticket:
                    await support_ticket_service.set_ticket_status(
                        db,
                        ticket=ticket,
                        status="open",
                        system_text="Тикет переоткрыт оператором.",
                        actor_telegram_id=from_id,
                        actor_chat_id=chat_id,
                    )
                    await db.commit()
            await _answer_support_callback_query(
                callback_query_id=cb_id,
                text="Тикет переоткрыт",
                show_alert=False,
            )
            if target_user_id:
                await _send_bot_message(
                    int(target_user_id),
                    f"♻️ Тикет {raw_ticket or ''} снова открыт.\nНапишите сообщение, если нужна помощь.",
                    reply_markup=_support_reply_markup_payload(ticket_id=raw_ticket or None),
                )
            return {"ok": True, "handled": "support_reopened", "ticket_id": raw_ticket or None}

        if cb_data.startswith("support_close:"):
            raw_ticket, target_user_id = _parse_cb_ticket_target(cb_data)
            if raw_ticket and FEATURE_FLAGS.tickets_v2:
                ticket = await support_ticket_repository.get_ticket_by_public_id(db, raw_ticket)
                if ticket:
                    await support_ticket_service.set_ticket_status(
                        db,
                        ticket=ticket,
                        status="closed",
                        system_text="Тикет закрыт оператором.",
                        actor_telegram_id=from_id,
                        actor_chat_id=chat_id,
                    )
                    await db.commit()

            await _answer_support_callback_query(
                callback_query_id=cb_id,
                text="Тикет закрыт",
                show_alert=False,
            )
            reply_to_message_id = None
            if isinstance(cb_msg, dict):
                reply_to_message_id = int(cb_msg.get("message_id") or 0) or None

            if target_user_id:
                close_text = (
                    "✅ Диалог с поддержкой закрыт.\n"
                    "Если понадобится, нажмите «Ответить», чтобы открыть новое обращение."
                )
                if raw_ticket:
                    close_text = (
                        f"✅ Диалог по тикету {raw_ticket} закрыт.\n"
                        "Если понадобится, нажмите «Ответить», чтобы открыть новое обращение."
                    )
                await _send_bot_message(
                    int(target_user_id),
                    close_text + "\nНажмите «Ответить», чтобы переоткрыть диалог.",
                    reply_markup=_support_reply_markup_payload(ticket_id=raw_ticket or None),
                )

            await _send_bot_message_with_token(
                token=SUPPORT_INBOX_BOT_TOKEN,
                chat_id=chat_id,
                text=(
                    "✅ Тикет помечен закрытым."
                    + (f"\nТикет: <code>{escape(raw_ticket)}</code>" if raw_ticket else "")
                ),
                parse_mode="HTML",
                reply_to_message_id=reply_to_message_id,
            )
            return {"ok": True, "handled": "support_closed", "ticket_id": raw_ticket or None}

        await _answer_support_callback_query(callback_query_id=cb_id)
        return {"ok": True, "ignored": "unknown_callback"}

    msg = payload.get("message") or {}
    if not isinstance(msg, dict):
        return {"ok": True, "ignored": "no_message"}

    chat = msg.get("chat") or {}
    from_user = msg.get("from") or {}
    text = str(msg.get("text") or "").strip()
    if not text:
        return {"ok": True, "ignored": "empty_text"}
    if bool(from_user.get("is_bot")):
        return {"ok": True, "ignored": "bot_message"}

    chat_id = int(chat.get("id") or 0)
    from_id = int(from_user.get("id") or 0)
    if SUPPORT_INBOX_CHAT_ID and chat_id != int(SUPPORT_INBOX_CHAT_ID):
        return {"ok": True, "ignored": "foreign_chat"}
    if SUPPORT_ADMIN_IDS and from_id not in SUPPORT_ADMIN_IDS:
        return {"ok": True, "ignored": "not_admin"}

    lower = text.lower()
    pending_key = _support_reply_pending_key(chat_id, from_id)
    pending_ctx = _SUPPORT_REPLY_PENDING.get(pending_key) or {}
    pending_target_user_id = int(pending_ctx.get("target_user_id") or 0) or None
    pending_ticket_id = str(pending_ctx.get("ticket_id") or "").strip().upper()

    if lower in {"/start", "/help"}:
        await _send_bot_message_with_token(
            token=SUPPORT_INBOX_BOT_TOKEN,
            chat_id=chat_id,
            text=_usage_text(),
            reply_to_message_id=int(msg.get("message_id") or 0) or None,
        )
        return {"ok": True, "handled": "help"}

    if lower.startswith("/dashboard"):
        lookback_days = int(ANALYTICS_GROWTH_LOOKBACK_DAYS)
        conversion_window_days = int(ANALYTICS_TRIAL_TO_MONTH_WINDOW_DAYS)
        numeric_args: List[int] = []
        for token in str(text or "").split()[1:]:
            item = str(token or "").strip()
            if not item:
                continue
            if "=" in item:
                key, value = item.split("=", 1)
                k = str(key or "").strip().lower()
                v = str(value or "").strip()
                if not v.lstrip("-").isdigit():
                    continue
                n = int(v)
                if k in {"days", "day", "d", "lookback", "lb"}:
                    lookback_days = max(7, min(180, n))
                elif k in {"window", "win", "w"}:
                    conversion_window_days = max(1, min(90, n))
                continue
            if item.lstrip("-").isdigit():
                numeric_args.append(int(item))
        if numeric_args:
            lookback_days = max(7, min(180, numeric_args[0]))
        if len(numeric_args) >= 2:
            conversion_window_days = max(1, min(90, numeric_args[1]))

        if not ANALYTICS_ADMIN_TOKEN:
            await _send_bot_message_with_token(
                token=SUPPORT_INBOX_BOT_TOKEN,
                chat_id=chat_id,
                text="Для dashboard нужен ANALYTICS_ADMIN_TOKEN в .env.",
                reply_to_message_id=int(msg.get("message_id") or 0) or None,
            )
            return {"ok": True, "handled": "dashboard_token_missing"}

        url = (
            "https://api.tarrotai.ru/analytics/dashboard?"
            + urlencode(
                {
                    "token": ANALYTICS_ADMIN_TOKEN,
                    "lookback_days": lookback_days,
                    "conversion_window_days": conversion_window_days,
                    "ad_spend_uzs": int(ANALYTICS_AD_SPEND_UZS),
                    "ad_spend_rub": int(ANALYTICS_AD_SPEND_RUB),
                }
            )
        )
        await _send_bot_message_with_token(
            token=SUPPORT_INBOX_BOT_TOKEN,
            chat_id=chat_id,
            text=f"📱 Mobile dashboard:\n{url}",
            reply_to_message_id=int(msg.get("message_id") or 0) or None,
        )
        return {
            "ok": True,
            "handled": "dashboard_link",
            "lookback_days": lookback_days,
            "conversion_window_days": conversion_window_days,
        }

    if lower.startswith("/metrics") or lower.startswith("/kpi"):
        lookback_days, conversion_window_days, ad_spend_uzs, ad_spend_rub = _parse_metrics_args(text)
        excluded_user_ids = await _resolve_analytics_excluded_user_ids(db)
        snapshot = await analytics_service.build_growth_snapshot(
            db,
            lookback_days=lookback_days,
            conversion_window_days=conversion_window_days,
            trial_product_code=ANALYTICS_TRIAL_PRODUCT_CODE,
            month_product_codes=ANALYTICS_MONTH_PRODUCT_CODES,
            ad_spend_uzs=ad_spend_uzs,
            ad_spend_rub=ad_spend_rub,
            ad_spend_by_currency={"UZS": ad_spend_uzs, "RUB": ad_spend_rub},
            free_limit=FREE_READINGS_PER_MONTH,
            exclude_user_ids=excluded_user_ids,
        )
        trial_to_month = snapshot.get("trial_to_month") or {}
        unit = snapshot.get("unit_economics") or {}
        activation = snapshot.get("activation_funnel") or {}
        photo = snapshot.get("photo_funnel") or {}
        paywall = snapshot.get("paywall_funnel") or {}
        retention = snapshot.get("retention_summary") or {}
        quality = snapshot.get("payment_quality") or {}
        ai_ops = snapshot.get("ai_operations") or {}
        notes = snapshot.get("notes") or []
        excluded_users_count = 0
        for note in notes:
            s = str(note or "").strip()
            if not s.startswith("excluded_user_ids_count:"):
                continue
            try:
                excluded_users_count = int(s.split(":", 1)[1].strip())
            except Exception:
                excluded_users_count = 0
        by_currency = snapshot.get("unit_economics_by_currency") or []
        currency_lines = []
        for row in by_currency[:4]:
            code = str(row.get("currency") or "UNK").upper()
            currency_lines.append(
                f"• {code}: rev {_fmt_num(row.get('revenue'))}, ARPPU {_fmt_num(row.get('arppu'))}, "
                f"CAC {_fmt_num(row.get('cac'))}, payback {float(row.get('payback_months') or 0.0):.2f} мес"
            )
        by_currency_text = "\n".join(currency_lines) if currency_lines else "• Нет данных"
        metric_text = (
            "📊 Метрики роста\n\n"
            f"Период: {int(trial_to_month.get('lookback_days') or lookback_days)} дн\n"
            f"Окно конверсии: {int(trial_to_month.get('conversion_window_days') or conversion_window_days)} дн\n\n"
            "Trial → Month:\n"
            f"• Конверсия: {_fmt_pct(trial_to_month.get('conversion_rate'))}\n"
            f"• Пользователи trial: {_fmt_num(trial_to_month.get('trial_users'))}\n"
            f"• Перешли в месяц: {_fmt_num(trial_to_month.get('converted_users'))}\n\n"
            "Unit-экономика (UZS):\n"
            f"• CAC: {_fmt_num(unit.get('cac_uzs'))} сум\n"
            f"• Payback: {float(unit.get('payback_months') or 0.0):.2f} мес (~{float(unit.get('payback_days') or 0.0):.1f} дн)\n"
            f"• Revenue: {_fmt_num(unit.get('revenue_uzs'))} сум\n"
            f"• ARPPU: {_fmt_num(unit.get('arppu_uzs'))} сум\n"
            f"• New paid: {_fmt_num(unit.get('new_paid_users'))}\n"
            f"• Paid users: {_fmt_num(unit.get('paid_users'))}\n"
            f"• Ad spend: {_fmt_num(unit.get('ad_spend_uzs'))} сум\n\n"
            "По валютам:\n"
            f"{by_currency_text}\n\n"
            "Активация:\n"
            f"• New users: {_fmt_num(activation.get('new_users'))}\n"
            f"• Activation rate: {_fmt_pct(activation.get('activation_rate'))}\n"
            f"• Paid rate: {_fmt_pct(activation.get('paid_rate'))}\n\n"
            "Фото-воронка:\n"
            f"• Photo users: {_fmt_num(photo.get('photo_users'))}\n"
            f"• Trial conv: {_fmt_pct(photo.get('trial_conversion_rate'))}\n"
            f"• Paid conv: {_fmt_pct(photo.get('paid_conversion_rate'))}\n\n"
            "Paywall / Retention / Quality:\n"
            f"• Paywall conv: {_fmt_pct(paywall.get('conversion_rate'))}\n"
            f"• D1/D7/D30: {_fmt_pct(retention.get('d1'))} / {_fmt_pct(retention.get('d7'))} / {_fmt_pct(retention.get('d30'))}\n"
            f"• Paid repeat 30d: {_fmt_pct(retention.get('paid_repeat_30d_rate'))}\n"
            f"• Refund rate: {_fmt_pct(quality.get('refund_rate'))}\n"
            f"• Click/SBP fail: {_fmt_pct(quality.get('click_failure_rate'))} / {_fmt_pct(quality.get('sbp_failure_rate'))}\n"
            f"• Vision escalation: {_fmt_pct(ai_ops.get('vision_escalation_rate'))}\n\n"
            f"Исключено тестовых пользователей: {_fmt_num(excluded_users_count)}\n\n"
            "Команда: /metrics [days] [ad_spend_uzs] [window] [ad_spend_rub]\n"
            "Пример: /metrics 30 3000000 30 120000"
        )
        await _send_bot_message_with_token(
            token=SUPPORT_INBOX_BOT_TOKEN,
            chat_id=chat_id,
            text=metric_text,
            reply_to_message_id=int(msg.get("message_id") or 0) or None,
        )
        return {
            "ok": True,
            "handled": "metrics",
            "lookback_days": lookback_days,
            "conversion_window_days": conversion_window_days,
            "ad_spend_uzs": ad_spend_uzs,
            "ad_spend_rub": ad_spend_rub,
        }

    if lower in {"/open", "/pending", "/closed"}:
        status_map: Dict[str, List[str]] = {
            "/open": ["open", "pending_support", "pending_user"],
            "/pending": ["pending_support", "pending_user"],
            "/closed": ["closed"],
        }
        statuses = status_map.get(lower, [])
        rows = await support_ticket_service.list_support_tickets_with_counts(db, statuses=statuses, limit=40)
        if not rows:
            await _send_bot_message_with_token(
                token=SUPPORT_INBOX_BOT_TOKEN,
                chat_id=chat_id,
                text="Тикеты не найдены.",
                reply_to_message_id=int(msg.get("message_id") or 0) or None,
            )
            return {"ok": True, "handled": "ticket_list_empty"}
        for row in rows[:20]:
            ticket_id = str(row.get("ticket_id") or "").strip().upper()
            target_uid = int(row.get("telegram_user_id") or 0)
            await _send_bot_message_with_token(
                token=SUPPORT_INBOX_BOT_TOKEN,
                chat_id=chat_id,
                text=_support_ticket_card_text(row),
                parse_mode="HTML",
                reply_markup=(
                    _support_inbox_reply_markup_payload(
                        ticket_id=ticket_id,
                        user_id=target_uid,
                        status=str(row.get("status") or "open"),
                    )
                    if ticket_id and target_uid
                    else None
                ),
            )
        return {"ok": True, "handled": "ticket_list", "count": len(rows)}

    if lower.startswith("/ticket"):
        parts = text.split(maxsplit=1)
        if len(parts) < 2:
            await _send_bot_message_with_token(
                token=SUPPORT_INBOX_BOT_TOKEN,
                chat_id=chat_id,
                text="Формат: /ticket <ID>",
                reply_to_message_id=int(msg.get("message_id") or 0) or None,
            )
            return {"ok": True, "handled": "ticket_format_error"}
        ticket_id = str(parts[1] or "").strip().upper()
        ticket = await support_ticket_repository.get_ticket_by_public_id(db, ticket_id)
        if not ticket:
            await _send_bot_message_with_token(
                token=SUPPORT_INBOX_BOT_TOKEN,
                chat_id=chat_id,
                text=f"Тикет {ticket_id} не найден.",
                reply_to_message_id=int(msg.get("message_id") or 0) or None,
            )
            return {"ok": True, "handled": "ticket_not_found"}
        messages = await support_ticket_repository.list_ticket_messages(db, ticket_pk=int(ticket.id), limit=20)
        history_lines = []
        for m in list(messages)[-8:]:
            role = "👤" if str(m.direction) == "user_to_support" else ("🛟" if str(m.direction) == "support_to_user" else "ℹ️")
            snippet = str(m.message_text or "").strip().replace("\n", " ")
            if len(snippet) > 120:
                snippet = snippet[:117] + "..."
            history_lines.append(f"{role} {escape(snippet)}")
        summary_text = (
            "🎫 <b>Карточка тикета</b>\n"
            f"ID: <code>{escape(ticket_id)}</code>\n"
            f"Пользователь: <code>{int(ticket.telegram_user_id)}</code>\n"
            f"Статус: <b>{escape(_support_status_human(ticket.status))}</b>\n"
            f"Сообщений: <b>{len(messages)}</b>\n\n"
            + ("<b>Последние сообщения:</b>\n" + "\n".join(history_lines) if history_lines else "Сообщений пока нет.")
        )
        await _send_bot_message_with_token(
            token=SUPPORT_INBOX_BOT_TOKEN,
            chat_id=chat_id,
            text=summary_text,
            parse_mode="HTML",
            reply_to_message_id=int(msg.get("message_id") or 0) or None,
            reply_markup=_support_inbox_reply_markup_payload(
                ticket_id=ticket_id,
                user_id=int(ticket.telegram_user_id),
                status=str(ticket.status or "open"),
            ),
        )
        return {"ok": True, "handled": "ticket_show", "ticket_id": ticket_id}

    target_user_id: Optional[int] = None
    ticket_id = ""
    answer_text = ""
    used_pending_target = False

    if lower in {"/cancel", "отмена", "cancel"} and pending_target_user_id:
        _SUPPORT_REPLY_PENDING.pop(pending_key, None)
        await _send_bot_message_with_token(
            token=SUPPORT_INBOX_BOT_TOKEN,
            chat_id=chat_id,
            text="Ок, режим ответа отменён.",
            reply_to_message_id=int(msg.get("message_id") or 0) or None,
        )
        return {"ok": True, "handled": "pending_reply_cancelled"}

    if lower.startswith("/reply"):
        parts = text.split(maxsplit=2)
        if len(parts) < 3 or not str(parts[1]).lstrip("-").isdigit():
            await _send_bot_message_with_token(
                token=SUPPORT_INBOX_BOT_TOKEN,
                chat_id=chat_id,
                text="Формат: /reply <telegram_id> <сообщение>",
                reply_to_message_id=int(msg.get("message_id") or 0) or None,
            )
            return {"ok": True, "handled": "invalid_reply_format"}
        target_user_id = int(parts[1])
        answer_text = str(parts[2] or "").strip()
    elif pending_target_user_id:
        target_user_id = int(pending_target_user_id)
        ticket_id = pending_ticket_id
        answer_text = text
        used_pending_target = True
    else:
        reply_to = msg.get("reply_to_message") or {}
        if not isinstance(reply_to, dict):
            await _send_bot_message_with_token(
                token=SUPPORT_INBOX_BOT_TOKEN,
                chat_id=chat_id,
                text="Ответьте реплаем на тикет или используйте /reply <telegram_id> <текст>.",
                reply_to_message_id=int(msg.get("message_id") or 0) or None,
            )
            return {"ok": True, "handled": "needs_reply_context"}
        target_user_id = _extract_support_target_user_id(
            text=str(reply_to.get("text") or ""),
            caption=str(reply_to.get("caption") or ""),
        )
        ticket_id = _extract_support_ticket_id(
            text=str(reply_to.get("text") or ""),
            caption=str(reply_to.get("caption") or ""),
        )
        answer_text = text

    if not target_user_id:
        await _send_bot_message_with_token(
            token=SUPPORT_INBOX_BOT_TOKEN,
            chat_id=chat_id,
            text="Не удалось определить telegram_id пользователя. Используйте /reply <telegram_id> <текст>.",
            reply_to_message_id=int(msg.get("message_id") or 0) or None,
        )
        return {"ok": True, "handled": "target_not_found"}

    if not answer_text:
        await _send_bot_message_with_token(
            token=SUPPORT_INBOX_BOT_TOKEN,
            chat_id=chat_id,
            text="Введите текст ответа.",
            reply_to_message_id=int(msg.get("message_id") or 0) or None,
        )
        return {"ok": True, "handled": "empty_answer"}

    if FEATURE_FLAGS.tickets_v2 and not ticket_id and target_user_id:
        q_recent = await db.execute(
            select(SupportTicket)
            .where(
                SupportTicket.telegram_user_id == int(target_user_id),
                SupportTicket.status != "closed",
            )
            .order_by(desc(SupportTicket.updated_at))
            .limit(1)
        )
        recent_ticket = q_recent.scalar_one_or_none()
        if recent_ticket:
            ticket_id = str(recent_ticket.ticket_id or "").strip().upper()

    delivered = await _send_bot_message(
        int(target_user_id),
        (
            "💬 Ответ поддержки AI Taro\n"
            + (f"Тикет: {ticket_id}\n" if ticket_id else "")
            + f"\n{answer_text}"
        ),
        reply_markup=_support_reply_markup_payload(ticket_id=ticket_id or None),
    )
    if delivered:
        if FEATURE_FLAGS.tickets_v2 and ticket_id:
            try:
                ticket = await support_ticket_repository.get_ticket_by_public_id(db, ticket_id)
                if ticket:
                    await support_ticket_service.register_support_reply(
                        db,
                        ticket=ticket,
                        text=answer_text,
                        sender_telegram_id=from_id,
                        telegram_chat_id=chat_id,
                        telegram_message_id=int(msg.get("message_id") or 0) or None,
                    )
                    await db.commit()
            except Exception as exc:
                await db.rollback()
                log.warning("Support reply persistence failed ticket_id=%s: %s", ticket_id, repr(exc))
        if used_pending_target:
            _SUPPORT_REPLY_PENDING.pop(pending_key, None)
        await _send_bot_message_with_token(
            token=SUPPORT_INBOX_BOT_TOKEN,
            chat_id=chat_id,
            text=(
                f"✅ Ответ отправлен пользователю {target_user_id}."
                + (f"\nТикет: <code>{escape(ticket_id)}</code>" if ticket_id else "")
            ),
            parse_mode="HTML",
            reply_to_message_id=int(msg.get("message_id") or 0) or None,
        )
        return {
            "ok": True,
            "handled": "delivered",
            "target_user_id": int(target_user_id),
            "ticket_id": ticket_id or None,
        }

    await _send_bot_message_with_token(
        token=SUPPORT_INBOX_BOT_TOKEN,
        chat_id=chat_id,
        text=f"⚠️ Не удалось доставить сообщение пользователю {target_user_id}.",
        reply_to_message_id=int(msg.get("message_id") or 0) or None,
    )
    return {"ok": True, "handled": "delivery_failed", "target_user_id": int(target_user_id)}


@app.post("/billing/sbp/create", response_model=SbpCreateOut)
async def billing_sbp_create(
    payload: SbpCreateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    plan = _get_sbp_plan(payload.plan_code)

    order_id = f"sbp_{current_user.id}_{secrets.token_hex(8)}"
    idempotence_key = uuid.uuid4().hex
    return_url = _append_query_param(YOOKASSA_SBP_RETURN_URL, "sbp_order_id", order_id)

    provider_request = {
        "amount": {
            "value": _rub_value_from_kopecks(int(plan["amount"])),
            "currency": str(plan.get("currency") or "RUB"),
        },
        "capture": True,
        "description": str(plan.get("description") or plan["title"]),
        "payment_method_data": {"type": "sbp"},
        "confirmation": {
            "type": "redirect",
            "return_url": return_url,
        },
        "metadata": {
            "order_id": order_id,
            "plan_code": str(plan["code"]),
            "user_id": str(current_user.id),
            "telegram_id": str(current_user.telegram_id),
            "source": "miniapp_sbp",
        },
    }

    provider = await _yookassa_request(
        "POST",
        "/payments",
        payload=provider_request,
        idempotence_key=idempotence_key,
    )

    payment_id = str(provider.get("id") or "").strip()
    status = str(provider.get("status") or "pending").strip().lower()
    confirmation_url = str(((provider.get("confirmation") or {}).get("confirmation_url") or "")).strip()

    if not payment_id or not confirmation_url:
        raise HTTPException(
            status_code=502,
            detail={"code": "SBP_PROVIDER_BAD_RESPONSE", "message": "Провайдер не вернул ссылку для оплаты."},
        )

    row = SbpOrder(
        user_id=int(current_user.id),
        order_id=order_id,
        plan_code=str(plan["code"]),
        amount=int(plan["amount"]),
        currency=str(plan.get("currency") or "RUB"),
        status=status or "pending",
        yookassa_payment_id=payment_id,
        confirmation_url=confirmation_url,
        idempotence_key=idempotence_key,
        provider_payload=provider,
        paid_at=_parse_provider_dt(provider.get("paid_at")),
    )
    db.add(row)
    await db.commit()

    return {
        "order_id": order_id,
        "plan_code": str(plan["code"]),
        "amount": int(plan["amount"]),
        "currency": str(plan.get("currency") or "RUB"),
        "payment_id": payment_id,
        "status": status or "pending",
        "confirmation_url": confirmation_url,
    }


@app.get("/billing/sbp/bot-link")
async def billing_sbp_bot_link(
    tg_user_id: int = Query(..., ge=1),
    plan_code: Literal["sub_2weeks", "sub_month", "sub_year"] = Query(...),
    exp: int = Query(..., ge=1),
    sig: str = Query(..., min_length=16, max_length=128),
    db: AsyncSession = Depends(get_db),
):
    if not _yookassa_sbp_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SBP_NOT_CONFIGURED",
                "message": "СБП ещё не настроен на сервере.",
            },
        )
    if not SBP_BOT_LINK_SECRET:
        raise HTTPException(
            status_code=503,
            detail={
                "code": "SBP_LINK_SECRET_MISSING",
                "message": "SBP_BOT_LINK_SECRET не задан.",
            },
        )

    now_ts = int(datetime.now(timezone.utc).timestamp())
    if exp < now_ts - 30:
        raise HTTPException(status_code=401, detail={"code": "SBP_LINK_EXPIRED", "message": "Ссылка устарела."})
    if exp > now_ts + 3600:
        raise HTTPException(status_code=401, detail={"code": "SBP_LINK_INVALID", "message": "Некорректная ссылка."})
    if not _verify_sbp_bot_link_signature(tg_user_id=tg_user_id, plan_code=plan_code, exp=exp, sig=sig):
        raise HTTPException(status_code=401, detail={"code": "SBP_LINK_INVALID", "message": "Некорректная подпись."})

    plan = _get_sbp_plan(plan_code)

    q_user = await db.execute(select(User).where(User.telegram_id == int(tg_user_id)))
    user = q_user.scalar_one_or_none()
    if not user:
        user = User(telegram_id=int(tg_user_id), paid_readings_balance=0)
        db.add(user)
        await db.flush()

    order_id = f"sbp_tg_{user.id}_{secrets.token_hex(8)}"
    idempotence_key = uuid.uuid4().hex
    return_url = _append_query_param(YOOKASSA_SBP_RETURN_URL, "sbp_order_id", order_id)

    provider_request = {
        "amount": {
            "value": _rub_value_from_kopecks(int(plan["amount"])),
            "currency": str(plan.get("currency") or "RUB"),
        },
        "capture": True,
        "description": str(plan.get("description") or plan["title"]),
        "payment_method_data": {"type": "sbp"},
        "confirmation": {
            "type": "redirect",
            "return_url": return_url,
        },
        "metadata": {
            "order_id": order_id,
            "plan_code": str(plan["code"]),
            "user_id": str(user.id),
            "telegram_id": str(user.telegram_id),
            "source": "bot_sbp_link",
        },
    }

    try:
        provider = await _yookassa_request(
            "POST",
            "/payments",
            payload=provider_request,
            idempotence_key=idempotence_key,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = str(detail.get("code") or "")
        msg = str(detail.get("message") or "").strip()
        if code in {"SBP_PROVIDER_ERROR", "SBP_NOT_CONFIGURED"}:
            text = msg or "СБП недоступен для вашего магазина ЮKassa."
            html = _render_sbp_unavailable_html(plan_title=str(plan.get("title") or plan["code"]), message=text)
            return HTMLResponse(content=html, status_code=200)
        raise

    payment_id = str(provider.get("id") or "").strip()
    status = str(provider.get("status") or "pending").strip().lower()
    confirmation_url = str(((provider.get("confirmation") or {}).get("confirmation_url") or "")).strip()

    if not payment_id or not confirmation_url:
        raise HTTPException(
            status_code=502,
            detail={"code": "SBP_PROVIDER_BAD_RESPONSE", "message": "Провайдер не вернул ссылку для оплаты."},
        )

    row = SbpOrder(
        user_id=int(user.id),
        order_id=order_id,
        plan_code=str(plan["code"]),
        amount=int(plan["amount"]),
        currency=str(plan.get("currency") or "RUB"),
        status=status or "pending",
        yookassa_payment_id=payment_id,
        confirmation_url=confirmation_url,
        idempotence_key=idempotence_key,
        provider_payload=provider,
        paid_at=_parse_provider_dt(provider.get("paid_at")),
    )
    db.add(row)
    await db.commit()

    return RedirectResponse(url=confirmation_url, status_code=302)


@app.get("/billing/sbp/autopay/bot-link")
async def billing_sbp_autopay_bot_link(
    tg_user_id: int = Query(..., ge=1),
    plan_code: Literal["sub_month"] = Query(...),
    exp: int = Query(..., ge=1),
    sig: str = Query(..., min_length=16, max_length=128),
    db: AsyncSession = Depends(get_db),
):
    if not SBP_AUTOPAY_ENABLED:
        raise HTTPException(
            status_code=503,
            detail={"code": "SBP_AUTOPAY_DISABLED", "message": "СБП автоплатёж пока отключён."},
        )
    if not _yookassa_sbp_configured():
        raise HTTPException(
            status_code=503,
            detail={"code": "SBP_NOT_CONFIGURED", "message": "СБП ещё не настроен на сервере."},
        )
    if not SBP_BOT_LINK_SECRET:
        raise HTTPException(
            status_code=503,
            detail={"code": "SBP_LINK_SECRET_MISSING", "message": "SBP_BOT_LINK_SECRET не задан."},
        )

    now_ts = int(datetime.now(timezone.utc).timestamp())
    if exp < now_ts - 30:
        raise HTTPException(status_code=401, detail={"code": "SBP_LINK_EXPIRED", "message": "Ссылка устарела."})
    if exp > now_ts + 3600:
        raise HTTPException(status_code=401, detail={"code": "SBP_LINK_INVALID", "message": "Некорректная ссылка."})

    payload_to_sign = f"{int(tg_user_id)}:{str(plan_code).strip().lower()}:autopay:{exp}"
    expected_sig = hmac.new(
        SBP_BOT_LINK_SECRET.encode("utf-8"),
        payload_to_sign.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, str(sig or "").strip()):
        raise HTTPException(status_code=401, detail={"code": "SBP_LINK_INVALID", "message": "Некорректная подпись."})

    plan = _get_sbp_plan(plan_code)
    if not _is_sbp_autopay_plan(plan["code"]):
        raise HTTPException(status_code=400, detail={"code": "INVALID_PLAN", "message": "Этот тариф недоступен для автоплатежа."})

    q_user = await db.execute(select(User).where(User.telegram_id == int(tg_user_id)))
    user = q_user.scalar_one_or_none()
    if not user:
        user = User(telegram_id=int(tg_user_id), paid_readings_balance=0)
        db.add(user)
        await db.flush()

    order_id = f"sbp_auto_tg_{user.id}_{secrets.token_hex(8)}"
    idempotence_key = uuid.uuid4().hex
    return_url = _append_query_param(YOOKASSA_SBP_RETURN_URL, "sbp_order_id", order_id)

    provider_request = {
        "amount": {
            "value": _rub_value_from_kopecks(int(plan["amount"])),
            "currency": str(plan.get("currency") or "RUB"),
        },
        "capture": True,
        "description": f"{str(plan.get('description') or plan['title'])} (СБП автоплатёж)",
        "payment_method_data": {"type": "sbp"},
        "save_payment_method": True,
        "confirmation": {
            "type": "redirect",
            "return_url": return_url,
        },
        "metadata": {
            "order_id": order_id,
            "plan_code": str(plan["code"]),
            "user_id": str(user.id),
            "telegram_id": str(user.telegram_id),
            "source": "bot_sbp_autopay_link",
            "autopay_mode": "setup",
            "autopay_interval_days": str(SBP_AUTOPAY_INTERVAL_DAYS),
        },
    }

    try:
        provider = await _yookassa_request(
            "POST",
            "/payments",
            payload=provider_request,
            idempotence_key=idempotence_key,
        )
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, dict) else {}
        code = str(detail.get("code") or "")
        msg = str(detail.get("message") or "").strip()
        if code in {"SBP_PROVIDER_ERROR", "SBP_NOT_CONFIGURED"}:
            text = msg or "СБП автоплатёж недоступен для вашего магазина ЮKassa."
            html = _render_sbp_unavailable_html(plan_title=f"{str(plan.get('title') or plan['code'])} (автоплатёж)", message=text)
            return HTMLResponse(content=html, status_code=200)
        raise

    payment_id = str(provider.get("id") or "").strip()
    status = str(provider.get("status") or "pending").strip().lower()
    confirmation_url = str(((provider.get("confirmation") or {}).get("confirmation_url") or "")).strip()
    if not payment_id or not confirmation_url:
        raise HTTPException(
            status_code=502,
            detail={"code": "SBP_PROVIDER_BAD_RESPONSE", "message": "Провайдер не вернул ссылку для оплаты."},
        )

    row = SbpOrder(
        user_id=int(user.id),
        order_id=order_id,
        plan_code=str(plan["code"]),
        amount=int(plan["amount"]),
        currency=str(plan.get("currency") or "RUB"),
        status=status or "pending",
        yookassa_payment_id=payment_id,
        confirmation_url=confirmation_url,
        idempotence_key=idempotence_key,
        provider_payload=provider,
        paid_at=_parse_provider_dt(provider.get("paid_at")),
    )
    db.add(row)
    await db.commit()
    return RedirectResponse(url=confirmation_url, status_code=302)


@app.get("/billing/sbp/status", response_model=SbpStatusOut)
async def billing_sbp_status(
    order_id: str = Query(..., min_length=8, max_length=80),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(
        select(SbpOrder).where(
            SbpOrder.order_id == str(order_id),
            SbpOrder.user_id == int(current_user.id),
        )
    )
    order = q.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail={"code": "SBP_ORDER_NOT_FOUND", "message": "Счёт не найден."})

    status = str(order.status or "pending").strip().lower()
    if status not in {"succeeded", "canceled", "cancelled"}:
        status = await _refresh_sbp_order_from_provider(order)

    if status == "succeeded" and not bool(order.activation_applied):
        plan = _get_sbp_plan(order.plan_code)
        applied, new_sub_until = await _apply_subscription_from_sbp(
            db,
            user=current_user,
            plan=plan,
            provider_payment_id=str(order.yookassa_payment_id or ""),
            order_id=str(order.order_id),
        )
        order.activation_applied = True
        if not order.paid_at:
            order.paid_at = datetime.now(timezone.utc)
        if applied:
            log.info(
                "SBP activated: user_id=%s order_id=%s plan=%s until=%s",
                current_user.id,
                order.order_id,
                plan["code"],
                new_sub_until.isoformat() if new_sub_until else None,
            )

        metadata = {}
        try:
            payload_obj = order.provider_payload or {}
            metadata = payload_obj.get("metadata") if isinstance(payload_obj, dict) else {}
            if not isinstance(metadata, dict):
                metadata = {}
        except Exception:
            metadata = {}
        if str(metadata.get("autopay_mode") or "").strip().lower() == "setup":
            activated = await _activate_sbp_autopay_if_possible(
                db,
                user=current_user,
                plan_code=str(order.plan_code or ""),
                provider_payload=order.provider_payload or {},
                provider_payment_id=str(order.yookassa_payment_id or ""),
                paid_at=_to_utc(order.paid_at),
            )
            if activated:
                log.info("SBP autopay setup activated: user_id=%s order_id=%s", current_user.id, order.order_id)

    if status == "succeeded" and not bool(order.success_notified):
        try:
            plan_for_msg = _get_sbp_plan(order.plan_code)
        except Exception:
            plan_for_msg = {"title": str(order.plan_code or "Подписка")}
        payload_obj = order.provider_payload if isinstance(order.provider_payload, dict) else {}
        md = payload_obj.get("metadata") if isinstance(payload_obj, dict) else {}
        autopay_setup = bool(isinstance(md, dict) and str(md.get("autopay_mode") or "").strip().lower() == "setup")
        text = _format_sbp_success_bot_text(
            plan=plan_for_msg,
            active_until=_to_utc(current_user.subscription_until),
            autopay_setup=autopay_setup,
        )
        sent = await _send_bot_message(int(current_user.telegram_id), text)
        if sent:
            order.success_notified = True

    await db.commit()
    await db.refresh(current_user)
    await db.refresh(order)

    sub_until = _to_utc(current_user.subscription_until)
    has_sub = bool(sub_until and sub_until > datetime.now(timezone.utc))
    return {
        "order_id": str(order.order_id),
        "plan_code": str(order.plan_code),
        "status": str(order.status or "pending"),
        "amount": int(order.amount or 0),
        "currency": str(order.currency or "RUB"),
        "paid_at": _to_utc(order.paid_at),
        "has_active_subscription": has_sub,
        "subscription_until": sub_until,
        "message": _sbp_status_message(str(order.status or "pending")),
    }


@app.post("/billing/sbp/webhook")
async def billing_sbp_webhook(
    payload: Dict[str, Any],
    token: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    if str(token or "").strip() != YOOKASSA_WEBHOOK_TOKEN:
        raise HTTPException(status_code=401, detail="Invalid webhook token")

    event = str(payload.get("event") or "").strip()
    obj = payload.get("object") or {}
    if not isinstance(obj, dict):
        return {"ok": True}

    # Refund webhook: revoke/refit subscription state and disable SBP autopay for this user.
    if event == "refund.succeeded":
        refunded_payment_id = str(obj.get("payment_id") or "").strip()
        if not refunded_payment_id:
            log.warning("SBP refund webhook ignored: payment_id missing")
            return {"ok": True}

        provider_charge_id = f"yookassa_sbp:{refunded_payment_id}"
        tx_q = await db.execute(
            select(PaymentTransaction).where(PaymentTransaction.provider_payment_charge_id == provider_charge_id)
        )
        tx = tx_q.scalar_one_or_none()
        if not tx:
            log.warning("SBP refund webhook: tx not found for payment_id=%s", refunded_payment_id)
            return {"ok": True}

        refund_at = _parse_provider_dt(obj.get("created_at")) or datetime.now(timezone.utc)
        if not tx.refunded_at:
            tx.refunded_at = refund_at

        user_q = await db.execute(select(User).where(User.id == int(tx.user_id)))
        user = user_q.scalar_one_or_none()
        if user:
            await _recompute_user_subscription_until(db, user)
            autopay_q = await db.execute(
                select(SbpAutopaySubscription).where(
                    SbpAutopaySubscription.user_id == int(user.id),
                    SbpAutopaySubscription.status.in_(["active", "pending", "past_due"]),
                )
            )
            for sub in list(autopay_q.scalars().all()):
                sub.status = "disabled"
                sub.last_error = f"refund:{refunded_payment_id}"[:500]
                sub.next_charge_at = datetime.now(timezone.utc) + timedelta(days=3650)

            # Mark related SBP order for observability.
            order_q = await db.execute(
                select(SbpOrder).where(SbpOrder.yookassa_payment_id == refunded_payment_id)
            )
            order = order_q.scalar_one_or_none()
            if order:
                order.status = "refunded"
                order.provider_notification = payload

            refund_text = (
                "Получен возврат по оплате. "
                "Подписка обновлена, автопродление отключено."
            )
            await _send_bot_message(int(user.telegram_id), refund_text)

        await db.commit()
        return {"ok": True, "refund_applied": True}

    payment_id = str(obj.get("id") or "").strip()
    status = str(obj.get("status") or "").strip().lower()
    metadata = obj.get("metadata") or {}
    order_id = str((metadata.get("order_id") if isinstance(metadata, dict) else "") or "").strip()

    order: Optional[SbpOrder] = None
    if payment_id:
        q = await db.execute(select(SbpOrder).where(SbpOrder.yookassa_payment_id == payment_id))
        order = q.scalar_one_or_none()
    if not order and order_id:
        q = await db.execute(select(SbpOrder).where(SbpOrder.order_id == order_id))
        order = q.scalar_one_or_none()

    autopay_sub_id_raw = ""
    if isinstance(metadata, dict):
        autopay_sub_id_raw = str(metadata.get("autopay_subscription_id") or "").strip()

    # Renewal webhook may arrive without sbp_orders row.
    if not order and autopay_sub_id_raw:
        try:
            autopay_sub_id = int(autopay_sub_id_raw)
        except Exception:
            autopay_sub_id = 0
        if autopay_sub_id > 0:
            sub_q = await db.execute(
                select(SbpAutopaySubscription).where(SbpAutopaySubscription.id == autopay_sub_id)
            )
            sub = sub_q.scalar_one_or_none()
            if sub:
                sub.last_payment_id = payment_id or sub.last_payment_id
                if status == "succeeded" and payment_id:
                    user_q = await db.execute(select(User).where(User.id == int(sub.user_id)))
                    user = user_q.scalar_one_or_none()
                    if user:
                        plan = _get_sbp_plan(sub.plan_code)
                        await _apply_subscription_from_sbp(
                            db,
                            user=user,
                            plan=plan,
                            provider_payment_id=payment_id,
                            order_id=f"sbp_auto_wh_{sub.id}_{datetime.now(timezone.utc).strftime('%Y%m%d')}",
                        )
                    sub.status = "active"
                    sub.fail_count = 0
                    sub.last_error = None
                    sub.last_charged_at = _parse_provider_dt(obj.get("paid_at")) or datetime.now(timezone.utc)
                    base_next = _to_utc(sub.next_charge_at) or datetime.now(timezone.utc)
                    if base_next < datetime.now(timezone.utc):
                        base_next = datetime.now(timezone.utc)
                    sub.next_charge_at = base_next + timedelta(days=max(1, int(sub.interval_days or SBP_AUTOPAY_INTERVAL_DAYS)))
                    log.info("SBP autopay renewal activated by webhook: sub_id=%s user_id=%s", sub.id, sub.user_id)
                elif status in {"pending", "waiting_for_capture"}:
                    sub.status = "pending"
                    sub.next_charge_at = datetime.now(timezone.utc) + timedelta(hours=2)
                elif status in {"canceled", "cancelled"}:
                    sub.fail_count = int(sub.fail_count or 0) + 1
                    sub.status = "disabled" if int(sub.fail_count) >= SBP_AUTOPAY_MAX_FAILS else "past_due"
                    sub.last_error = f"provider_status:{status}"
                    sub.next_charge_at = datetime.now(timezone.utc) + timedelta(hours=12)
                await db.commit()
                return {"ok": True}

    if not order:
        log.warning("SBP webhook ignored: order not found payment_id=%s order_id=%s event=%s", payment_id, order_id, event)
        return {"ok": True}

    if payment_id and not order.yookassa_payment_id:
        order.yookassa_payment_id = payment_id
    if status:
        order.status = status
    order.provider_notification = payload
    order.provider_payload = obj
    if status == "succeeded" and not order.paid_at:
        order.paid_at = _parse_provider_dt(obj.get("paid_at")) or datetime.now(timezone.utc)

    if status == "succeeded" and not bool(order.activation_applied):
        user_q = await db.execute(select(User).where(User.id == int(order.user_id)))
        user = user_q.scalar_one_or_none()
        if user:
            plan = _get_sbp_plan(order.plan_code)
            await _apply_subscription_from_sbp(
                db,
                user=user,
                plan=plan,
                provider_payment_id=str(order.yookassa_payment_id or payment_id or ""),
                order_id=str(order.order_id),
            )
            order.activation_applied = True
            log.info("SBP webhook activation applied: user_id=%s order_id=%s", user.id, order.order_id)

            if str((metadata.get("autopay_mode") if isinstance(metadata, dict) else "") or "").strip().lower() == "setup":
                activated = await _activate_sbp_autopay_if_possible(
                    db,
                    user=user,
                    plan_code=str(order.plan_code or ""),
                    provider_payload=obj,
                    provider_payment_id=str(order.yookassa_payment_id or payment_id or ""),
                    paid_at=_parse_provider_dt(obj.get("paid_at")) or datetime.now(timezone.utc),
                )
                if activated:
                    log.info("SBP autopay setup activated by webhook: user_id=%s order_id=%s", user.id, order.order_id)

    if status == "succeeded" and not bool(order.success_notified):
        user_q = await db.execute(select(User).where(User.id == int(order.user_id)))
        user = user_q.scalar_one_or_none()
        if user:
            try:
                plan = _get_sbp_plan(order.plan_code)
            except Exception:
                plan = {"title": str(order.plan_code or "Подписка")}
            autopay_setup = bool(isinstance(metadata, dict) and str(metadata.get("autopay_mode") or "").strip().lower() == "setup")
            text = _format_sbp_success_bot_text(
                plan=plan,
                active_until=_to_utc(user.subscription_until),
                autopay_setup=autopay_setup,
            )
            sent = await _send_bot_message(int(user.telegram_id), text)
            if sent:
                order.success_notified = True

    await db.commit()
    return {"ok": True}


def _click_collect_payload(**kwargs: Optional[str]) -> Dict[str, str]:
    return {k: str(v or "").strip() for k, v in kwargs.items()}


def _click_missing_fields(payload: Dict[str, str], required: List[str]) -> List[str]:
    missing: List[str] = []
    for key in required:
        if not str(payload.get(key) or "").strip():
            missing.append(key)
    return missing


@app.post("/billing/click/create", response_model=ClickCreateOut)
async def billing_click_create(
    payload: ClickCreateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if not _click_api_configured():
        raise HTTPException(
            status_code=503,
            detail={
                "code": "CLICK_NOT_CONFIGURED",
                "message": "Click пока не настроен: задайте CLICK_SERVICE_ID, CLICK_MERCHANT_ID и CLICK_SECRET_KEY.",
            },
        )
    plan = _get_click_plan(payload.plan_code)
    if str(plan.get("code") or "").strip().lower() == CLICK_INTRO_PLAN_CODE:
        if await _click_intro_plan_already_used(db, user_id=int(current_user.id)):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CLICK_INTRO_ALREADY_USED",
                    "message": "Пробная неделя доступна только для первой покупки.",
                },
            )
    order_id = f"click_{current_user.id}_{secrets.token_hex(8)}"
    payment_url = _click_build_payment_url(order_id=order_id, amount=int(plan["amount"]))

    row = ClickOrder(
        user_id=int(current_user.id),
        order_id=order_id,
        plan_code=str(plan["code"]),
        amount=int(plan["amount"]),
        currency=str(plan.get("currency") or CLICK_CURRENCY),
        status="pending",
    )
    db.add(row)
    await db.commit()

    return {
        "order_id": order_id,
        "plan_code": str(plan["code"]),
        "amount": int(plan["amount"]),
        "currency": str(plan.get("currency") or CLICK_CURRENCY),
        "status": "pending",
        "payment_url": payment_url,
    }


@app.get("/billing/click/bot-link")
async def billing_click_bot_link(
    tg_user_id: int = Query(..., ge=1),
    plan_code: Literal["sub_week", "sub_2weeks", "sub_month", "sub_year"] = Query(...),
    exp: int = Query(..., ge=1),
    sig: str = Query(..., min_length=16, max_length=128),
    checkout_mode: Literal["app", "card"] = Query(default="app"),
    card_type: Optional[str] = Query(default=None),
    db: AsyncSession = Depends(get_db),
):
    if not _click_api_configured():
        raise HTTPException(status_code=503, detail={"code": "CLICK_NOT_CONFIGURED", "message": "Click ещё не настроен."})
    if not CLICK_BOT_LINK_SECRET:
        raise HTTPException(status_code=503, detail={"code": "CLICK_LINK_SECRET_MISSING", "message": "CLICK_BOT_LINK_SECRET не задан."})

    now_ts = int(datetime.now(timezone.utc).timestamp())
    if exp < now_ts - 30:
        raise HTTPException(status_code=401, detail={"code": "CLICK_LINK_EXPIRED", "message": "Ссылка устарела."})
    if exp > now_ts + max(3600, CLICK_BOT_LINK_TTL_SEC):
        raise HTTPException(status_code=401, detail={"code": "CLICK_LINK_INVALID", "message": "Некорректная ссылка."})
    if not _verify_click_bot_link_signature(tg_user_id=tg_user_id, plan_code=plan_code, exp=exp, sig=sig):
        raise HTTPException(status_code=401, detail={"code": "CLICK_LINK_INVALID", "message": "Некорректная подпись."})

    plan = _get_click_plan(plan_code)

    q_user = await db.execute(select(User).where(User.telegram_id == int(tg_user_id)))
    user = q_user.scalar_one_or_none()
    if not user:
        user = User(telegram_id=int(tg_user_id), paid_readings_balance=0)
        db.add(user)
        await db.flush()
    if str(plan.get("code") or "").strip().lower() == CLICK_INTRO_PLAN_CODE:
        if await _click_intro_plan_already_used(db, user_id=int(user.id)):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "CLICK_INTRO_ALREADY_USED",
                    "message": "Пробная неделя доступна только для первой покупки.",
                },
            )

    order_id = f"click_tg_{user.id}_{secrets.token_hex(8)}"
    safe_card_type = _click_normalize_card_type(card_type)
    target_return_url = _append_query_param(
        str(CLICK_RETURN_URL or "").strip(),
        "click_order_id",
        str(order_id),
    )
    payment_url = _click_build_payment_url(
        order_id=order_id,
        amount=int(plan["amount"]),
        return_url=target_return_url,
        card_type=(safe_card_type if checkout_mode == "card" else None),
    )
    row = ClickOrder(
        user_id=int(user.id),
        order_id=order_id,
        plan_code=str(plan["code"]),
        amount=int(plan["amount"]),
        currency=str(plan.get("currency") or CLICK_CURRENCY),
        status="pending",
    )
    db.add(row)
    await db.commit()

    if str(checkout_mode or "").strip().lower() == "card":
        html = _render_click_card_checkout_html(
            order_id=order_id,
            amount=int(plan["amount"]),
            return_url=target_return_url,
            fallback_url=payment_url,
            default_card_type=safe_card_type,
        )
        return HTMLResponse(content=html, status_code=200)

    return RedirectResponse(url=payment_url, status_code=302)


@app.get("/billing/click/status", response_model=ClickStatusOut)
async def billing_click_status(
    order_id: str = Query(..., min_length=8, max_length=96),
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    q = await db.execute(
        select(ClickOrder).where(
            ClickOrder.order_id == str(order_id),
            ClickOrder.user_id == int(current_user.id),
        )
    )
    order = q.scalar_one_or_none()
    if not order:
        raise HTTPException(status_code=404, detail={"code": "CLICK_ORDER_NOT_FOUND", "message": "Счёт не найден."})

    if str(order.status or "").strip().lower() == "succeeded" and not bool(order.activation_applied):
        plan = _get_click_plan(order.plan_code)
        await _apply_subscription_from_click(
            db,
            user=current_user,
            plan=plan,
            click_trans_id=str(order.click_trans_id or ""),
            order_id=str(order.order_id),
        )
        order.activation_applied = True
        if not order.paid_at:
            order.paid_at = datetime.now(timezone.utc)
        await db.commit()

    await db.refresh(current_user)
    await db.refresh(order)
    sub_until = _to_utc(current_user.subscription_until)
    has_sub = bool(sub_until and sub_until > datetime.now(timezone.utc))
    return {
        "order_id": str(order.order_id),
        "plan_code": str(order.plan_code),
        "status": str(order.status or "pending"),
        "amount": int(order.amount or 0),
        "currency": str(order.currency or CLICK_CURRENCY),
        "paid_at": _to_utc(order.paid_at),
        "has_active_subscription": has_sub,
        "subscription_until": sub_until,
        "message": _click_status_message(str(order.status or "pending")),
    }


@app.post("/billing/click/prepare")
async def billing_click_prepare(
    click_trans_id: Optional[str] = Form(default=None),
    service_id: Optional[str] = Form(default=None),
    click_paydoc_id: Optional[str] = Form(default=None),
    merchant_trans_id: Optional[str] = Form(default=None),
    amount: Optional[str] = Form(default=None),
    action: Optional[str] = Form(default=None),
    error: Optional[str] = Form(default=None),
    error_note: Optional[str] = Form(default=None),
    sign_time: Optional[str] = Form(default=None),
    sign_string: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    payload = _click_collect_payload(
        click_trans_id=click_trans_id,
        service_id=service_id,
        click_paydoc_id=click_paydoc_id,
        merchant_trans_id=merchant_trans_id,
        amount=amount,
        action=action,
        error=error,
        error_note=error_note,
        sign_time=sign_time,
        sign_string=sign_string,
    )
    missing = _click_missing_fields(
        payload,
        [
            "click_trans_id",
            "service_id",
            "click_paydoc_id",
            "merchant_trans_id",
            "amount",
            "action",
            "error",
            "error_note",
            "sign_time",
            "sign_string",
        ],
    )
    if missing:
        return _click_build_response(
            click_trans_id=payload.get("click_trans_id", ""),
            merchant_trans_id=payload.get("merchant_trans_id", ""),
            error=-8,
            merchant_prepare_id=0,
        )

    if not _click_api_configured():
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-8,
            merchant_prepare_id=0,
        )
    if payload["action"] != "0":
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-3,
            merchant_prepare_id=0,
        )
    if payload["service_id"] != str(CLICK_SERVICE_ID):
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-8,
            merchant_prepare_id=0,
        )

    sign_expected = _click_sign_raw(
        click_trans_id=payload["click_trans_id"],
        service_id=payload["service_id"],
        merchant_trans_id=payload["merchant_trans_id"],
        amount=payload["amount"],
        action=payload["action"],
        sign_time=payload["sign_time"],
        merchant_prepare_id="",
    )
    if sign_expected != payload["sign_string"].lower():
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-1,
            merchant_prepare_id=0,
        )

    q = await db.execute(select(ClickOrder).where(ClickOrder.order_id == payload["merchant_trans_id"]))
    order = q.scalar_one_or_none()
    if not order:
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-5,
            merchant_prepare_id=0,
        )
    if not _click_amount_matches(request_amount=payload["amount"], expected_amount=int(order.amount or 0)):
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-2,
            merchant_prepare_id=int(order.merchant_prepare_id or order.id),
        )

    status = str(order.status or "").strip().lower()
    if status == "succeeded":
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-4,
            merchant_prepare_id=int(order.merchant_prepare_id or order.id),
        )
    if status in {"cancelled", "canceled"}:
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-9,
            merchant_prepare_id=int(order.merchant_prepare_id or order.id),
        )
    if order.click_trans_id and str(order.click_trans_id) != payload["click_trans_id"]:
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-8,
            merchant_prepare_id=int(order.merchant_prepare_id or order.id),
        )

    if not order.merchant_prepare_id:
        order.merchant_prepare_id = int(order.id)
    order.status = "prepared"
    order.click_trans_id = payload["click_trans_id"]
    order.click_paydoc_id = payload["click_paydoc_id"]
    try:
        order.provider_error = int(payload["error"])
    except Exception:
        order.provider_error = None
    order.provider_error_note = payload["error_note"][:255] if payload["error_note"] else None
    order.prepare_payload = payload
    await db.commit()

    return _click_build_response(
        click_trans_id=payload["click_trans_id"],
        merchant_trans_id=payload["merchant_trans_id"],
        error=0,
        merchant_prepare_id=int(order.merchant_prepare_id or order.id),
    )


@app.post("/billing/click/complete")
async def billing_click_complete(
    click_trans_id: Optional[str] = Form(default=None),
    service_id: Optional[str] = Form(default=None),
    click_paydoc_id: Optional[str] = Form(default=None),
    merchant_trans_id: Optional[str] = Form(default=None),
    merchant_prepare_id: Optional[str] = Form(default=None),
    amount: Optional[str] = Form(default=None),
    action: Optional[str] = Form(default=None),
    error: Optional[str] = Form(default=None),
    error_note: Optional[str] = Form(default=None),
    sign_time: Optional[str] = Form(default=None),
    sign_string: Optional[str] = Form(default=None),
    db: AsyncSession = Depends(get_db),
):
    payload = _click_collect_payload(
        click_trans_id=click_trans_id,
        service_id=service_id,
        click_paydoc_id=click_paydoc_id,
        merchant_trans_id=merchant_trans_id,
        merchant_prepare_id=merchant_prepare_id,
        amount=amount,
        action=action,
        error=error,
        error_note=error_note,
        sign_time=sign_time,
        sign_string=sign_string,
    )
    missing = _click_missing_fields(
        payload,
        [
            "click_trans_id",
            "service_id",
            "click_paydoc_id",
            "merchant_trans_id",
            "merchant_prepare_id",
            "amount",
            "action",
            "error",
            "error_note",
            "sign_time",
            "sign_string",
        ],
    )
    if missing:
        return _click_build_response(
            click_trans_id=payload.get("click_trans_id", ""),
            merchant_trans_id=payload.get("merchant_trans_id", ""),
            error=-8,
            merchant_confirm_id=0,
        )

    if not _click_api_configured():
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-8,
            merchant_confirm_id=0,
        )
    if payload["action"] != "1":
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-3,
            merchant_confirm_id=0,
        )
    if payload["service_id"] != str(CLICK_SERVICE_ID):
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-8,
            merchant_confirm_id=0,
        )

    sign_expected = _click_sign_raw(
        click_trans_id=payload["click_trans_id"],
        service_id=payload["service_id"],
        merchant_trans_id=payload["merchant_trans_id"],
        merchant_prepare_id=payload["merchant_prepare_id"],
        amount=payload["amount"],
        action=payload["action"],
        sign_time=payload["sign_time"],
    )
    if sign_expected != payload["sign_string"].lower():
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-1,
            merchant_confirm_id=0,
        )

    q = await db.execute(select(ClickOrder).where(ClickOrder.order_id == payload["merchant_trans_id"]))
    order = q.scalar_one_or_none()
    if not order:
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-5,
            merchant_confirm_id=0,
        )
    if not _click_amount_matches(request_amount=payload["amount"], expected_amount=int(order.amount or 0)):
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-2,
            merchant_confirm_id=int(order.merchant_prepare_id or order.id),
        )
    if order.click_trans_id and str(order.click_trans_id) != payload["click_trans_id"]:
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-6,
            merchant_confirm_id=int(order.merchant_prepare_id or order.id),
        )

    try:
        merchant_prepare_id_int = int(payload["merchant_prepare_id"])
    except Exception:
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-6,
            merchant_confirm_id=int(order.merchant_prepare_id or order.id),
        )
    if order.merchant_prepare_id and int(order.merchant_prepare_id) != merchant_prepare_id_int:
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-6,
            merchant_confirm_id=int(order.merchant_prepare_id),
        )
    if not order.merchant_prepare_id:
        order.merchant_prepare_id = merchant_prepare_id_int

    status = str(order.status or "").strip().lower()
    if status == "succeeded":
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-4,
            merchant_confirm_id=int(order.merchant_prepare_id or order.id),
        )
    if status in {"cancelled", "canceled"}:
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-9,
            merchant_confirm_id=int(order.merchant_prepare_id or order.id),
        )

    incoming_error = 0
    try:
        incoming_error = int(payload["error"])
    except Exception:
        incoming_error = -8

    order.click_trans_id = payload["click_trans_id"]
    order.click_paydoc_id = payload["click_paydoc_id"]
    order.complete_payload = payload
    order.provider_error = incoming_error
    order.provider_error_note = payload["error_note"][:255] if payload["error_note"] else None

    if incoming_error < 0:
        order.status = "cancelled"
        await db.commit()
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-9,
            merchant_confirm_id=int(order.merchant_prepare_id or order.id),
        )
    if incoming_error != 0:
        order.status = "cancelled"
        await db.commit()
        return _click_build_response(
            click_trans_id=payload["click_trans_id"],
            merchant_trans_id=payload["merchant_trans_id"],
            error=-8,
            merchant_confirm_id=int(order.merchant_prepare_id or order.id),
        )

    if not bool(order.activation_applied):
        user_q = await db.execute(select(User).where(User.id == int(order.user_id)))
        user = user_q.scalar_one_or_none()
        if not user:
            return _click_build_response(
                click_trans_id=payload["click_trans_id"],
                merchant_trans_id=payload["merchant_trans_id"],
                error=-5,
                merchant_confirm_id=int(order.merchant_prepare_id or order.id),
            )
        try:
            plan = _get_click_plan(order.plan_code)
            await _apply_subscription_from_click(
                db,
                user=user,
                plan=plan,
                click_trans_id=payload["click_trans_id"],
                order_id=str(order.order_id),
            )
            order.activation_applied = True
        except Exception as exc:
            log.exception("CLICK complete failed to apply subscription order_id=%s: %s", order.order_id, repr(exc))
            order.provider_error = -7
            order.provider_error_note = CLICK_ERROR_NOTES.get(-7)
            await db.commit()
            return _click_build_response(
                click_trans_id=payload["click_trans_id"],
                merchant_trans_id=payload["merchant_trans_id"],
                error=-7,
                merchant_confirm_id=int(order.merchant_prepare_id or order.id),
            )

    order.status = "succeeded"
    if not order.paid_at:
        order.paid_at = datetime.now(timezone.utc)
    await db.commit()
    return _click_build_response(
        click_trans_id=payload["click_trans_id"],
        merchant_trans_id=payload["merchant_trans_id"],
        error=0,
        merchant_confirm_id=int(order.merchant_prepare_id or order.id),
    )


# ================================== CARD OF DAY ==================================
@app.post("/card-of-day", response_model=CardOfDayOut)
async def create_or_get_card_of_day(
    payload: CardOfDayCreateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    day_key = _today_key()

    result = await db.execute(
        select(CardOfDay).where(
            CardOfDay.user_id == current_user.id,
            CardOfDay.day_key == day_key,
        )
    )
    existing = result.scalar_one_or_none()
    if existing:
        await _ensure_card_day_memory_event(
            db,
            user=current_user,
            row=existing,
            is_reversed_hint=bool(getattr(existing, "is_reversed", False)),
        )
        return {
            "day_key": existing.day_key,
            "topic": existing.topic,
            "question": existing.question,
            "card_index": existing.card_index,
            "card_name": existing.card_name,
            "is_reversed": bool(getattr(existing, "is_reversed", False)),
            "description": existing.description,
        }

    effective_deck = min(max(int(payload.deck_size), 1), DECK_SIZE)
    card_index = random.randint(0, effective_deck - 1)
    card = get_card_by_index(card_index)

    seed = f"{day_key}:{current_user.id}:{card_index}:{payload.topic}:{(payload.question or '').strip()}"
    rnd = random.Random(seed)
    is_reversed = bool(payload.consider_reversed and rnd.choice([True, False]))

    try:
        description = await generate_card_text_llm(
            topic=payload.topic,
            question=payload.question,
            card=card,
            is_reversed=is_reversed,
            require_llm=payload.force_llm,
        )
    except Exception as e:
        log.exception("LLM card-of-day failed: %s", repr(e))
        raise HTTPException(
            status_code=503,
            detail="LLM недоступна. Проверь OPENAI_API_KEY/OPENAI_MODEL и доступ к OpenAI. "
                   "Если хочешь fallback — отправь force_llm=false.",
        )

    row = CardOfDay(
        user_id=current_user.id,
        day_key=day_key,
        topic=payload.topic,
        question=payload.question,
        card_index=card_index,
        card_name=card["name"],
        is_reversed=bool(is_reversed),
        description=description,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)
    await _ensure_card_day_memory_event(
        db,
        user=current_user,
        row=row,
        is_reversed_hint=bool(is_reversed),
    )

    return {
        "day_key": row.day_key,
        "topic": row.topic,
        "question": row.question,
        "card_index": row.card_index,
        "card_name": row.card_name,
        "is_reversed": bool(getattr(row, "is_reversed", False)),
        "description": row.description,
    }


@app.get("/card-of-day/today", response_model=CardOfDayOut)
async def get_card_of_day_today(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    day_key = _today_key()
    result = await db.execute(
        select(CardOfDay).where(
            CardOfDay.user_id == current_user.id,
            CardOfDay.day_key == day_key,
        )
    )
    existing = result.scalar_one_or_none()
    if not existing:
        raise HTTPException(status_code=404, detail="No card-of-day for today yet")

    await _ensure_card_day_memory_event(
        db,
        user=current_user,
        row=existing,
        is_reversed_hint=bool(getattr(existing, "is_reversed", False)),
    )

    return {
        "day_key": existing.day_key,
        "topic": existing.topic,
        "question": existing.question,
        "card_index": existing.card_index,
        "card_name": existing.card_name,
        "is_reversed": bool(getattr(existing, "is_reversed", False)),
        "description": existing.description,
    }


@app.get("/card-of-day/history", response_model=List[CardOfDayHistoryItem])
async def get_card_of_day_history(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(CardOfDay)
        .where(CardOfDay.user_id == current_user.id)
        .order_by(CardOfDay.created_at.desc())
        .limit(60)
    )
    rows = result.scalars().all()

    return [
        {
            "day_key": r.day_key,
            "topic": r.topic,
            "question": r.question,
            "card_index": r.card_index,
            "card_name": r.card_name,
            "is_reversed": bool(getattr(r, "is_reversed", False)),
            "description": r.description,
            "created_at": r.created_at,
        }
        for r in rows
    ]


# ================================== READINGS ==================================
def _spread_layout(payload: ReadingCreateIn) -> List[tuple[str, str]]:
    st = (payload.spread_type or "").strip()

    if st == "ppf":
        return [("past", "Прошлое"), ("present", "Настоящее"), ("future", "Будущее")]

    if st == "decision":
        a = payload.option_a.strip() or "Вариант A"
        b = payload.option_b.strip() or "Вариант B"
        return [
            ("option_a", f"Вариант A: {a}" if payload.option_a.strip() else "Вариант A"),
            ("option_b", f"Вариант B: {b}" if payload.option_b.strip() else "Вариант B"),
        ]

    if st == "custom":
        positions = payload.positions or []
        titles = payload.position_titles or []
        if not positions:
            positions = ["1", "2", "3"]
        layout = []
        for i, p in enumerate(positions):
            t = titles[i] if i < len(titles) and titles[i].strip() else f"Карта {i+1}"
            layout.append((str(p), str(t)))
        return layout

    return [("situation", "Ситуация"), ("advice", "Совет"), ("outcome", "Итог")]


@app.post("/reading", response_model=ReadingOut)
async def create_reading(
    payload: ReadingCreateIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await _consume_reading_quota_or_raise(db, current_user.id)

    effective_deck = min(max(int(payload.deck_size), 1), DECK_SIZE)
    layout = _spread_layout(payload)
    k = max(1, len(layout))

    if k > effective_deck:
        raise HTTPException(status_code=400, detail="deck_size too small for this spread")

    cards_for_store: List[dict] = []
    forced_cards = payload.forced_cards or []

    use_forced = len(forced_cards) >= k
    if use_forced:
        used_indices: set[int] = set()
        for fc in forced_cards[:k]:
            idx = int(fc.card_index)
            if idx < 0 or idx >= effective_deck:
                raise HTTPException(status_code=400, detail="forced_cards contains invalid card_index")
            if idx in used_indices:
                raise HTTPException(status_code=400, detail="forced_cards contains duplicate card_index")
            used_indices.add(idx)

        for (pos, title), fc in zip(layout, forced_cards[:k]):
            idx = int(fc.card_index)
            card = get_card_by_index(idx)
            is_reversed = bool(payload.consider_reversed and bool(fc.is_reversed))
            meaning = card["reversed_meaning"] if is_reversed else card["upright_meaning"]
            cards_for_store.append(
                {
                    "position": pos,
                    "title": title,
                    "card_index": int(idx),
                    "card_name": str(card["name"]),
                    "is_reversed": bool(is_reversed),
                    "meaning": str(meaning or ""),
                }
            )
    else:
        indices = random.sample(range(effective_deck), k)
        for (pos, title), idx in zip(layout, indices):
            card = get_card_by_index(idx)
            is_reversed = bool(payload.consider_reversed and random.choice([True, False]))
            meaning = card["reversed_meaning"] if is_reversed else card["upright_meaning"]

            cards_for_store.append(
                {
                    "position": pos,
                    "title": title,
                    "card_index": int(idx),
                    "card_name": str(card["name"]),
                    "is_reversed": bool(is_reversed),
                    "meaning": str(meaning or ""),
                }
            )

    extra_context = (payload.extra_context or "").strip()
    if payload.spread_type == "decision":
        a = (payload.option_a or "").strip()
        b = (payload.option_b or "").strip()
        dec_ctx = []
        if a:
            dec_ctx.append(f"Вариант A: {a}")
        if b:
            dec_ctx.append(f"Вариант B: {b}")
        if dec_ctx:
            extra_context = ("\n".join(dec_ctx) + ("\n\n" + extra_context if extra_context else "")).strip()

    _, memory_prompt_context, _ = await _memory_context_for_user(
        db,
        current_user,
        question=payload.question,
        current_cards=cards_for_store,
    )
    if memory_prompt_context:
        extra_context = (
            f"{extra_context}\n\n{memory_prompt_context}".strip()
            if extra_context
            else memory_prompt_context
        )
    try:
        description = await generate_spread_text_llm(
            topic=payload.topic,
            question=payload.question,
            spread_type=payload.spread_type,
            cards=cards_for_store,
            extra_context=extra_context,
            require_llm=payload.force_llm,
        )
    except Exception as e:
        log.exception("LLM reading failed: %s", repr(e))
        raise HTTPException(
            status_code=503,
            detail="LLM недоступна. Проверь OPENAI_API_KEY/OPENAI_MODEL и доступ к OpenAI. "
                   "Если хочешь fallback — отправь force_llm=false.",
        )

    row = Reading(
        user_id=current_user.id,
        spread_type=payload.spread_type,
        topic=payload.topic,
        question=payload.question,
        cards=cards_for_store,
        description=description,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    if FEATURE_FLAGS.memory_v1 and bool(current_user.memory_opt_in):
        try:
            await memory_service.ingest_event(
                db,
                user_id=int(current_user.id),
                source_kind="reading",
                source_id=int(row.id),
                topic=str(row.topic or "other"),
                spread_type=str(row.spread_type or ""),
                question=str(row.question or ""),
                cards=list(row.cards or []),
                description=str(row.description or ""),
            )
            await db.commit()
        except Exception as exc:
            await db.rollback()
            log.warning("Memory ingest failed for reading_id=%s: %s", row.id, repr(exc))

    return {
        "id": row.id,
        "spread_type": row.spread_type,
        "topic": row.topic,
        "question": row.question,
        "cards": row.cards,
        "description": row.description,
        "created_at": row.created_at,
    }


@app.get("/reading/history", response_model=List[ReadingHistoryItem])
async def get_reading_history(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(int(limit), 200))
    result = await db.execute(
        select(Reading)
        .where(Reading.user_id == current_user.id)
        .order_by(desc(Reading.created_at))
        .limit(limit)
    )
    rows = result.scalars().all()

    return [
        {
            "id": r.id,
            "spread_type": r.spread_type,
            "topic": r.topic,
            "question": r.question,
            "cards": r.cards,
            "description": r.description,
            "created_at": r.created_at,
        }
        for r in rows
    ]


# ================================== PHOTO ANALYSIS ==================================
_MAX_UPLOAD_BYTES = 8 * 1024 * 1024  # 8MB


@app.post("/photo-analysis", response_model=PhotoAnalysisOut)
async def photo_analysis(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    image: Optional[UploadFile] = File(default=None),
    file: Optional[UploadFile] = File(default=None),
    topic: str = Form(default="other"),
    question: str = Form(default=""),
    extra_context: str = Form(default=""),
    consider_reversed: bool = Form(default=True),
    force_llm: bool = Form(default=False),
    detect_only: bool = Form(default=False),
):
    up = image or file
    if not up:
        raise HTTPException(status_code=400, detail="image/file is required")

    content_type = (up.content_type or "").lower()
    if (not content_type or not content_type.startswith("image/")) and up.filename:
        fn = str(up.filename).lower()
        if fn.endswith(".png"):
            content_type = "image/png"
        elif fn.endswith(".webp"):
            content_type = "image/webp"
        elif fn.endswith(".heic") or fn.endswith(".heif"):
            content_type = "image/heic"
        else:
            content_type = "image/jpeg"
    if not (content_type.startswith("image/")):
        raise HTTPException(status_code=400, detail=f"Unsupported content-type: {up.content_type}")

    data = await up.read()
    if not data:
        raise HTTPException(status_code=400, detail="Empty file")
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="File too large (max 8MB)")

    if not detect_only:
        await _consume_reading_quota_or_raise(db, current_user.id)

    try:
        context = (extra_context or "").strip()
        if not consider_reversed:
            context = f"{context}\nИгнорируй перевёрнутые позиции, считай карты прямыми.".strip()
        _, memory_prompt_context, _ = await _memory_context_for_user(
            db,
            current_user,
            question=question,
            current_cards=[],
        )
        if memory_prompt_context:
            context = (
                f"{context}\n\n{memory_prompt_context}".strip()
                if context
                else memory_prompt_context
            )
        description, cards = await generate_photo_analysis_llm(
            topic=topic,
            question=question,
            image_bytes=data,
            image_mime=content_type,
            extra_context=context,
            require_llm=force_llm,
            skip_interpretation=detect_only,
            quota_register=_vision_register_request_global,
            quota_try_escalate=_vision_try_escalate_global,
            quota_mark_forced_escalation=_vision_mark_forced_escalation_global,
        )
    except Exception as e:
        log.exception("LLM photo-analysis failed: %s", repr(e))
        if force_llm:
            raise HTTPException(
                status_code=503,
                detail="LLM недоступна для анализа фото. Проверь OPENAI_API_KEY/OPENAI_MODEL (и что модель поддерживает vision). "
                       "Если хочешь fallback — отправь force_llm=false.",
            )
        description, cards = "", []

    description = (description or "").strip()
    if detect_only:
        if cards:
            description = "Карты распознаны."
        elif not description:
            description = "Не удалось распознать карты на фото. Попробуй сделать фото ближе, без бликов и с хорошим светом."
    elif not description:
        description = "Не удалось распознать карты на фото. Попробуй сделать фото ближе, без бликов и с хорошим светом."

    if not detect_only:
        try:
            row = Reading(
                user_id=current_user.id,
                spread_type="photo_analysis",
                topic=topic,
                question=question,
                cards=cards,
                description=description,
            )
            db.add(row)
            await db.commit()
            if FEATURE_FLAGS.memory_v1 and bool(current_user.memory_opt_in):
                await memory_service.ingest_event(
                    db,
                    user_id=int(current_user.id),
                    source_kind="photo_analysis",
                    source_id=int(row.id) if getattr(row, "id", None) else None,
                    topic=str(topic or "other"),
                    spread_type="photo_analysis",
                    question=str(question or ""),
                    cards=list(cards or []),
                    description=str(description or ""),
                )
                await db.commit()
        except Exception as e:
            await db.rollback()
            log.exception("Failed to persist photo-analysis reading: %s", repr(e))

    return {
        "description": description,
        "cards": cards,
        "topic": topic,
        "question": question,
        "spread_type": "photo_analysis",
    }


@app.post("/photo-analysis/followup", response_model=PhotoFollowupOut)
async def photo_analysis_followup(
    payload: PhotoFollowupIn,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    followup_question = str(payload.followup_question or "").strip()
    if not followup_question:
        raise HTTPException(status_code=400, detail="followup_question is required")

    cards_for_prompt: List[dict] = []
    for c in list(payload.cards or []):
        try:
            if hasattr(c, "model_dump"):
                cards_for_prompt.append(dict(c.model_dump()))
            else:
                cards_for_prompt.append(dict(c.dict()))
        except Exception:
            continue

    context_parts: List[str] = []
    extra_context = str(payload.extra_context or "").strip()
    if extra_context:
        context_parts.append(extra_context)

    main_q = str(payload.main_question or "").strip()
    if main_q:
        context_parts.append(f"Первичный вопрос: {main_q}")

    base_interp = str(payload.base_interpretation or "").strip()
    if base_interp:
        base_excerpt = base_interp[:1400].rstrip()
        if len(base_interp) > 1400:
            base_excerpt += "..."
        context_parts.append(f"Первичный ответ: {base_excerpt}")

    _, memory_prompt_context, _ = await _memory_context_for_user(
        db,
        current_user,
        question=followup_question,
        current_cards=cards_for_prompt,
    )
    if memory_prompt_context:
        context_parts.append(memory_prompt_context)

    description = ""
    try:
        description = await generate_photo_followup_llm(
            topic=str(payload.topic or "other"),
            main_question=main_q,
            followup_question=followup_question,
            cards=cards_for_prompt,
            base_interpretation=base_interp,
            extra_context="\n\n".join([p for p in context_parts if str(p).strip()]),
            require_llm=False,
        )
    except Exception as exc:
        log.warning("photo followup llm failed: %s", repr(exc))

    description = str(description or "").strip()
    if not description:
        card_hints: List[str] = []
        for idx, c in enumerate(cards_for_prompt[:3], start=1):
            name = str(c.get("card_name") or c.get("title") or f"Карта {idx}").strip()
            orient = "перевёрнутая" if bool(c.get("is_reversed")) else "прямая"
            if name:
                card_hints.append(f"- {name} ({orient})")
        cards_block = "\n".join(card_hints) if card_hints else "- Карты в этом уточнении не удалось извлечь"
        description = (
            "## Ответ на ваш вопрос\n"
            "По этому раскладу ответ на уточнение лучше читать через текущий контекст карт, а не как новый расклад.\n\n"
            "## Почему так по картам\n"
            f"{cards_block}\n\n"
            "## Что сделать сейчас\n"
            "Сделайте один конкретный шаг по вашему вопросу в ближайшие 24 часа и сверяйтесь с этим раскладом как с ориентиром."
        )

    return {
        "description": description,
        "topic": str(payload.topic or "other"),
        "question": followup_question,
        "spread_type": "photo_analysis_followup",
    }


# ================================== UNIFIED HISTORY ==================================
@app.get("/history", response_model=List[UnifiedHistoryItem])
async def get_unified_history(
    limit: int = 50,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    limit = max(1, min(int(limit), 200))

    cod_res = await db.execute(
        select(CardOfDay)
        .where(CardOfDay.user_id == current_user.id)
        .order_by(desc(CardOfDay.created_at))
        .limit(limit)
    )
    cod_rows = cod_res.scalars().all()

    rd_res = await db.execute(
        select(Reading)
        .where(Reading.user_id == current_user.id)
        .order_by(desc(Reading.created_at))
        .limit(limit)
    )
    rd_rows = rd_res.scalars().all()

    reading_ids = [int(r.id) for r in rd_rows if getattr(r, "id", None) is not None]
    cod_ids = [int(r.id) for r in cod_rows if getattr(r, "id", None) is not None]
    reading_capsule_by_id: Dict[int, str] = {}
    cod_capsule_by_id: Dict[int, str] = {}
    if reading_ids or cod_ids:
        mem_filters = []
        if reading_ids:
            mem_filters.append(
                and_(
                    UserMemoryEvent.source_kind.in_(["reading", "photo_analysis"]),
                    UserMemoryEvent.source_id.in_(reading_ids),
                )
            )
        if cod_ids:
            mem_filters.append(
                and_(
                    UserMemoryEvent.source_kind == "card_of_day",
                    UserMemoryEvent.source_id.in_(cod_ids),
                )
            )
        if mem_filters:
            mem_res = await db.execute(
                select(UserMemoryEvent).where(
                    UserMemoryEvent.user_id == current_user.id,
                    or_(*mem_filters),
                )
            )
            for ev in mem_res.scalars().all():
                if ev.source_id is None:
                    continue
                ev_summary = ev.summary if isinstance(ev.summary, dict) else {}
                capsule = str(ev_summary.get("capsule") or "").strip()
                low_capsule = capsule.lower()
                if low_capsule in {"other", "другое", "open", "open question", "открытый вопрос", "эта тема"}:
                    capsule = ""
                if not capsule:
                    capsule = memory_service.build_event_capsule(
                        topic=str(ev.topic or ""),
                        spread_type=str(ev.spread_type or ""),
                        question=str(ev.question or ""),
                        description=str(ev_summary.get("description_excerpt") or ""),
                        cards=list(ev.cards or []),
                    )
                if not capsule:
                    continue
                source_id = int(ev.source_id)
                if str(ev.source_kind or "") == "card_of_day":
                    cod_capsule_by_id[source_id] = capsule
                else:
                    reading_capsule_by_id[source_id] = capsule

    items: List[dict] = []

    for r in cod_rows:
        cod_capsule = cod_capsule_by_id.get(int(r.id))
        if not cod_capsule:
            cod_capsule = memory_service.build_event_capsule(
                topic=str(r.topic or ""),
                spread_type="card_of_day",
                question=str(r.question or ""),
                description=str(r.description or ""),
                cards=memory_service.build_card_of_day_cards_payload(
                    card_index=int(r.card_index or 0),
                    card_name=str(r.card_name or ""),
                    is_reversed=bool(getattr(r, "is_reversed", False)),
                ),
            )
        items.append(
            {
                "kind": "card_of_day",
                "created_at": r.created_at,
                "payload": {
                    "day_key": r.day_key,
                    "topic": r.topic,
                    "question": r.question,
                    "card_index": r.card_index,
                    "card_name": r.card_name,
                    "is_reversed": bool(getattr(r, "is_reversed", False)),
                    "description": r.description,
                    "theme_capsule": cod_capsule or None,
                },
            }
        )

    for r in rd_rows:
        reading_capsule = reading_capsule_by_id.get(int(r.id))
        if not reading_capsule:
            reading_capsule = memory_service.build_event_capsule(
                topic=str(r.topic or ""),
                spread_type=str(r.spread_type or ""),
                question=str(r.question or ""),
                description=str(r.description or ""),
                cards=list(r.cards or []),
            )
        items.append(
            {
                "kind": "reading",
                "created_at": r.created_at,
                "payload": {
                    "id": r.id,
                    "spread_type": r.spread_type,
                    "topic": r.topic,
                    "question": r.question,
                    "cards": r.cards,
                    "description": r.description,
                    "theme_capsule": reading_capsule or None,
                },
            }
        )

    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items[:limit]
