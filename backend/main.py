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

import httpx
from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession
from dotenv import load_dotenv

from db import engine, get_db, Base, SessionLocal
from models import (
    User,
    CardOfDay,
    Reading,
    PaymentTransaction,
    SbpOrder,
    SbpAutopaySubscription,
    SupportTicket,
    SupportTicketMessage,
    UserMemoryEvent,
    UserMemoryProfile,
    RetentionNudgeLog,
)
from telegram_auth import validate_init_data
from jwt import create_jwt, decode_jwt

from tarot_deck import get_card_by_index, DECK_SIZE
from llm_card_of_day import generate_card_text_llm, generate_spread_text_llm, generate_photo_analysis_llm
from features.common.flags import FEATURE_FLAGS
from features.common.timezone import resolve_tz_name
from features.memory.schemas import MemorySummaryOut
from features.memory import service as memory_service
from features.support_tickets import service as support_ticket_service
from features.support_tickets import repository as support_ticket_repository
from features.support_tickets.schemas import SupportTicketListOut, SupportTicketOut
from features.retention.scheduler import run_retention_cycle

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")

load_dotenv()

app = FastAPI(title="Telegram Mini App API")
_autopay_worker_task: Optional[asyncio.Task] = None
_autopay_lock = asyncio.Lock()
_autopay_last_run_ts = 0.0

FREE_READINGS_PER_MONTH = int(os.getenv("FREE_READINGS_PER_MONTH", "5"))

SBP_PLANS: Dict[str, Dict[str, Any]] = {
    "sub_2weeks": {
        "code": "sub_2weeks",
        "title": "Безлимит на 2 недели",
        "description": "Подписка AI Tarot на 14 дней",
        "days": 14,
        "amount": 99 * 100,
        "currency": "RUB",
    },
    "sub_month": {
        "code": "sub_month",
        "title": "Безлимит на месяц",
        "description": "Подписка AI Tarot на 30 дней",
        "days": 30,
        "amount": 179 * 100,
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


def _rub_value_from_kopecks(kopecks: int) -> str:
    value = max(0, int(kopecks or 0)) / 100.0
    return f"{value:.2f}"


def _append_query_param(url: str, key: str, value: str) -> str:
    base = str(url or "").strip()
    if not base:
        return ""
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
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS memory_opt_in BOOLEAN NOT NULL DEFAULT FALSE;",
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


async def _memory_context_for_user(db: AsyncSession, user: User) -> tuple[dict, str, str]:
    """
    Returns: (summary, prompt_context, inline_hint)
    """
    if not FEATURE_FLAGS.memory_v1:
        return {}, "", ""
    if not bool(getattr(user, "memory_opt_in", False)):
        return {}, "", ""
    summary = await memory_service.get_summary(db, user_id=int(user.id))
    prompt_context = memory_service.build_prompt_context(summary)
    hint = memory_service.build_inline_hint(summary)
    return summary, prompt_context, hint


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
    payload = decode_jwt(token)

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
    memory_opt_in: bool = False
    retention_nudges_opt_in: bool = False
    retention_nudge_hour_local: Optional[int] = None
    retention_nudge_tz: Optional[str] = None


class MePreferencesIn(BaseModel):
    memory_opt_in: bool = False
    retention_nudges_opt_in: bool = False
    retention_nudge_hour_local: Optional[int] = Field(default=None, ge=0, le=23)
    retention_nudge_tz: Optional[str] = None


class MePreferencesOut(BaseModel):
    memory_opt_in: bool = False
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
    plan_code: Literal["sub_2weeks", "sub_month"] = "sub_2weeks"


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
    memory_hint: Optional[str] = None


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
    memory_hint: Optional[str] = None


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
        "memory_opt_in": bool(current_user.memory_opt_in),
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
    summary = await memory_service.get_summary(db, user_id=int(current_user.id))
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
    if not _yookassa_sbp_configured():
        return

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
            "• /open, /pending, /closed — фильтры тикетов\n"
            "• /ticket <ID> — карточка тикета\n\n"
            "Ответ будет доставлен пользователю в @Ttaarrroobot."
        )

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
    plan_code: Literal["sub_2weeks", "sub_month"] = Query(...),
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
    if YOOKASSA_WEBHOOK_TOKEN and str(token or "").strip() != YOOKASSA_WEBHOOK_TOKEN:
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
        return {
            "day_key": existing.day_key,
            "topic": existing.topic,
            "question": existing.question,
            "card_index": existing.card_index,
            "card_name": existing.card_name,
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
        description=description,
    )
    db.add(row)
    await db.commit()
    await db.refresh(row)

    return {
        "day_key": row.day_key,
        "topic": row.topic,
        "question": row.question,
        "card_index": row.card_index,
        "card_name": row.card_name,
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

    return {
        "day_key": existing.day_key,
        "topic": existing.topic,
        "question": existing.question,
        "card_index": existing.card_index,
        "card_name": existing.card_name,
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
            ("advice", "Совет"),
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

    memory_hint = ""
    _, memory_prompt_context, _ = await _memory_context_for_user(db, current_user)
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
            summary = await memory_service.rebuild_profile(db, user_id=int(current_user.id), days=90)
            memory_hint = memory_service.build_inline_hint(summary)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            log.warning("Memory ingest/rebuild failed for reading_id=%s: %s", row.id, repr(exc))

    return {
        "id": row.id,
        "spread_type": row.spread_type,
        "topic": row.topic,
        "question": row.question,
        "cards": row.cards,
        "description": row.description,
        "created_at": row.created_at,
        "memory_hint": memory_hint or None,
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

    await _consume_reading_quota_or_raise(db, current_user.id)
    memory_hint = ""
    _, memory_prompt_context, _ = await _memory_context_for_user(db, current_user)

    try:
        context = (extra_context or "").strip()
        if not consider_reversed:
            context = f"{context}\nИгнорируй перевёрнутые позиции, считай карты прямыми.".strip()
        if memory_prompt_context:
            context = f"{context}\n\n{memory_prompt_context}".strip() if context else memory_prompt_context

        description, cards = await generate_photo_analysis_llm(
            topic=topic,
            question=question,
            image_bytes=data,
            image_mime=content_type,
            extra_context=context,
            require_llm=force_llm,
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
    if not description:
        description = "Не удалось распознать карты на фото. Попробуй сделать фото ближе, без бликов и с хорошим светом."

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
            summary = await memory_service.rebuild_profile(db, user_id=int(current_user.id), days=90)
            memory_hint = memory_service.build_inline_hint(summary)
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
        "memory_hint": memory_hint or None,
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

    items: List[dict] = []

    for r in cod_rows:
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
                    "description": r.description,
                },
            }
        )

    for r in rd_rows:
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
                },
            }
        )

    items.sort(key=lambda x: x["created_at"], reverse=True)
    return items[:limit]
