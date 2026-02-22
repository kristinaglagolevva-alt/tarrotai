from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import random
import secrets
import uuid
from datetime import date, datetime, timedelta, timezone
from typing import Optional, List, Literal, Dict, Any

import httpx
from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, Form, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from db import engine, get_db, Base
from models import User, CardOfDay, Reading, PaymentTransaction, SbpOrder
from telegram_auth import validate_init_data
from jwt import create_jwt, decode_jwt

from tarot_deck import get_card_by_index, DECK_SIZE
from llm_card_of_day import generate_card_text_llm, generate_spread_text_llm, generate_photo_analysis_llm

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")

app = FastAPI(title="Telegram Mini App API")

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
    # БД
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        await _ensure_runtime_schema(conn)

    # Бот
    await _start_telegram_bot_background()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    # Бот работает в daemon-thread и завершится вместе с процессом.
    return None


@app.get("/health")
async def health() -> dict:
    return {
        "status": "ok",
        "time": datetime.utcnow().isoformat(),
        "sbp_configured": _yookassa_sbp_configured(),
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


async def _ensure_runtime_schema(conn) -> None:
    """
    Lightweight runtime migration for existing DBs without Alembic.
    Safe for repeated startups.
    """
    statements = [
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS subscription_until TIMESTAMPTZ NULL;",
        "ALTER TABLE users ADD COLUMN IF NOT EXISTS paid_readings_balance INTEGER NOT NULL DEFAULT 0;",
        "CREATE INDEX IF NOT EXISTS ix_users_subscription_until ON users (subscription_until);",
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
    }


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

    try:
        context = (extra_context or "").strip()
        if not consider_reversed:
            context = f"{context}\nИгнорируй перевёрнутые позиции, считай карты прямыми.".strip()

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
