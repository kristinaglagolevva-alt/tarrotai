from __future__ import annotations

import asyncio
import logging
import os
import random
from datetime import date, datetime, timezone
from typing import Optional, List, Literal

from fastapi import FastAPI, Depends, HTTPException, Header, UploadFile, File, Form
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from db import engine, get_db, Base
from models import User, CardOfDay, Reading
from telegram_auth import validate_init_data
from jwt import create_jwt, decode_jwt

from tarot_deck import get_card_by_index, DECK_SIZE
from llm_card_of_day import generate_card_text_llm, generate_spread_text_llm, generate_photo_analysis_llm

logging.basicConfig(level=logging.INFO)
log = logging.getLogger("api")

app = FastAPI(title="Telegram Mini App API")

FREE_READINGS_PER_MONTH = int(os.getenv("FREE_READINGS_PER_MONTH", "5"))


# ============================ CORS ============================
def _parse_cors_origins(value: str) -> List[str]:
    items = [x.strip() for x in (value or "").split(",")]
    return [x for x in items if x]


DEFAULT_CORS_ORIGINS = "https://tarrotai.ru,https://www.tarrotai.ru"
CORS_ORIGINS = _parse_cors_origins(os.getenv("CORS_ORIGINS", DEFAULT_CORS_ORIGINS))

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
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
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


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


class CardOfDayCreateIn(BaseModel):
    topic: str = "relations"
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


class ReadingCreateIn(BaseModel):
    spread_type: Literal["ppf", "three_cards", "decision", "custom"] = "three_cards"
    topic: str = "relations"
    question: str = ""
    consider_reversed: bool = True
    deck_size: int = 78

    # decision
    option_a: str = ""
    option_b: str = ""

    # custom
    positions: List[str] = Field(default_factory=list)
    position_titles: List[str] = Field(default_factory=list)

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
    topic: str = "relations"
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

    indices = random.sample(range(effective_deck), k)

    cards_for_store: List[dict] = []
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
    topic: str = Form(default="relations"),
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
