# telegram_bot.py
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from datetime import datetime, timedelta, timezone
from html import escape
from io import BytesIO
from types import SimpleNamespace
from typing import Dict, Any, List, Optional, Set
from urllib.parse import urlencode

import asyncpg
from sqlalchemy import select
from telegram import (
    Bot,
    BotCommand,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InputFile,
    KeyboardButton,
    LabeledPrice,
    MenuButtonWebApp,
    ReplyKeyboardMarkup,
    Update,
    WebAppInfo,
)
from telegram.constants import ParseMode
from telegram.error import BadRequest
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    PreCheckoutQueryHandler,
    filters,
)
from dotenv import load_dotenv
from db import SessionLocal
from features.common.flags import FEATURE_FLAGS
from features.support_tickets import repository as support_ticket_repository
from features.support_tickets import service as support_ticket_service
from models import SupportTicket, User

load_dotenv()

logger = logging.getLogger("telegram_bot")


def _env_int(name: str, default: int = 0) -> int:
    raw = str(os.environ.get(name) or "").strip()
    if raw.lstrip("-").isdigit():
        return int(raw)
    return int(default)


def _env_float(name: str, default: float = 0.0) -> float:
    raw = str(os.environ.get(name) or "").strip()
    if not raw:
        return float(default)
    try:
        return float(raw)
    except Exception:
        return float(default)


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


BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
PROVIDER_TOKEN = (
    os.environ.get("TELEGRAM_PROVIDER_TOKEN")
    or os.environ.get("YOOKASSA_PROVIDER_TOKEN")
    or os.environ.get("YOO_PROVIDER_TOKEN")
    or ""
).strip()
CLICK_PROVIDER_TOKEN = (
    os.environ.get("TELEGRAM_CLICK_PROVIDER_TOKEN")
    or os.environ.get("CLICK_PROVIDER_TOKEN")
    or ""
).strip()
API_PUBLIC_BASE = (
    os.environ.get("API_PUBLIC_BASE_URL")
    or os.environ.get("API_BASE_URL")
    or "https://api.tarrotai.ru"
).strip().rstrip("/")
SBP_BOT_LINK_SECRET = (
    os.environ.get("SBP_BOT_LINK_SECRET")
    or os.environ.get("YOOKASSA_WEBHOOK_TOKEN")
    or ""
).strip()
CLICK_BOT_LINK_SECRET = (
    os.environ.get("CLICK_BOT_LINK_SECRET")
    or SBP_BOT_LINK_SECRET
    or ""
).strip()
CLICK_BOT_LINK_TTL_SEC = max(60, int(os.environ.get("CLICK_BOT_LINK_TTL_SEC", "900")))
SBP_AUTOPAY_ENABLED = str(os.environ.get("SBP_AUTOPAY_ENABLED", "0")).strip().lower() not in {"0", "false", "no"}
SBP_AUTOPAY_PLAN_KEY = (os.environ.get("SBP_AUTOPAY_PLAN_CODE") or "sub_month").strip().lower()

APP_URL_RAW = (os.environ.get("TELEGRAM_APP_URL") or "").strip()  # например https://tarrotai.ru
APP_BUTTON_TEXT = os.environ.get("TELEGRAM_APP_BUTTON_TEXT") or "🚀 Начать"
APP_MENU_BUTTON_TEXT = os.environ.get("TELEGRAM_MENU_BUTTON_TEXT") or "Начать"
BOT_VERSION = os.environ.get("TELEGRAM_BOT_VERSION") or os.environ.get("APP_VERSION") or "unknown"
YOOKASSA_REQUIRE_RECEIPT = str(os.environ.get("YOOKASSA_REQUIRE_RECEIPT", "1")).strip().lower() not in {"0", "false", "no"}
YOOKASSA_VAT_CODE = int(os.environ.get("YOOKASSA_VAT_CODE", "1"))
YOOKASSA_CONTACT_MODE = (os.environ.get("YOOKASSA_CONTACT_MODE") or "email").strip().lower()
ENABLE_YOOKASSA_PAYMENTS = str(os.environ.get("ENABLE_YOOKASSA_PAYMENTS", "1")).strip().lower() not in {"0", "false", "no"}
ENABLE_CLICK_PAYMENTS = str(os.environ.get("ENABLE_CLICK_PAYMENTS", "0")).strip().lower() not in {"0", "false", "no"}
CLICK_SERVICE_ID = _env_int("CLICK_SERVICE_ID", 0)
CLICK_MERCHANT_ID = _env_int("CLICK_MERCHANT_ID", 0)
PRICING_MARKUP_PERCENT = _env_float("PRICING_MARKUP_PERCENT", 25.0)
YEARLY_DISCOUNT_PERCENT = _env_float("YEARLY_DISCOUNT_PERCENT", 35.0)
CLICK_INTRO_PLAN_KEY = (os.environ.get("CLICK_INTRO_PLAN_CODE") or "sub_week").strip().lower()
CLICK_INTRO_FIRST_PURCHASE_ONLY = str(
    os.environ.get("CLICK_INTRO_FIRST_PURCHASE_ONLY", "1")
).strip().lower() not in {"0", "false", "no"}
RUB_SUB_2WEEKS_AMOUNT_BASE = max(100, _env_int("RUB_SUB_2WEEKS_AMOUNT_BASE", 99 * 100))
RUB_SUB_MONTH_AMOUNT_BASE = max(100, _env_int("RUB_SUB_MONTH_AMOUNT_BASE", 179 * 100))
USD_SUB_2WEEKS_CENTS_BASE = max(1, _env_int("USD_SUB_2WEEKS_CENTS_BASE", 99))
USD_SUB_MONTH_CENTS_BASE = max(1, _env_int("USD_SUB_MONTH_CENTS_BASE", 179))
CLICK_CURRENCY = (os.environ.get("CLICK_CURRENCY") or "UZS").strip().upper()
CLICK_SUB_WEEK_AMOUNT = max(
    0,
    _env_int(
        "CLICK_API_SUB_WEEK_AMOUNT",
        _env_int("CLICK_SUB_WEEK_AMOUNT", 12000),
    ),
)
CLICK_SUB_2WEEKS_AMOUNT_BASE = max(0, _env_int("CLICK_SUB_2WEEKS_AMOUNT", 0))
CLICK_SUB_MONTH_AMOUNT_BASE = max(0, _env_int("CLICK_SUB_MONTH_AMOUNT", 0))
CLICK_SUB_YEAR_AMOUNT_OVERRIDE = max(
    0,
    _env_int(
        "CLICK_API_SUB_YEAR_AMOUNT",
        _env_int("CLICK_SUB_YEAR_AMOUNT", 219000),
    ),
)
RUB_SUB_2WEEKS_AMOUNT = _apply_markup_minor(
    RUB_SUB_2WEEKS_AMOUNT_BASE,
    PRICING_MARKUP_PERCENT,
    step=100,
)
RUB_SUB_MONTH_AMOUNT = _apply_markup_minor(
    RUB_SUB_MONTH_AMOUNT_BASE,
    PRICING_MARKUP_PERCENT,
    step=100,
)
USD_SUB_2WEEKS_CENTS = _apply_markup_minor(
    USD_SUB_2WEEKS_CENTS_BASE,
    PRICING_MARKUP_PERCENT,
    step=1,
)
USD_SUB_MONTH_CENTS = _apply_markup_minor(
    USD_SUB_MONTH_CENTS_BASE,
    PRICING_MARKUP_PERCENT,
    step=1,
)
RUB_SUB_YEAR_AMOUNT = _apply_markup_minor(
    RUB_SUB_MONTH_AMOUNT * 12,
    -YEARLY_DISCOUNT_PERCENT,
    step=100,
)
USD_SUB_YEAR_CENTS = _apply_markup_minor(
    USD_SUB_MONTH_CENTS * 12,
    -YEARLY_DISCOUNT_PERCENT,
    step=1,
)
CLICK_SUB_2WEEKS_AMOUNT = _apply_markup_minor(
    CLICK_SUB_2WEEKS_AMOUNT_BASE,
    PRICING_MARKUP_PERCENT,
    step=100,
    allow_zero=True,
)
CLICK_SUB_MONTH_AMOUNT = _apply_markup_minor(
    CLICK_SUB_MONTH_AMOUNT_BASE,
    PRICING_MARKUP_PERCENT,
    step=100,
    allow_zero=True,
)
if CLICK_SUB_YEAR_AMOUNT_OVERRIDE > 0:
    CLICK_SUB_YEAR_AMOUNT = CLICK_SUB_YEAR_AMOUNT_OVERRIDE
elif CLICK_SUB_MONTH_AMOUNT > 0:
    CLICK_SUB_YEAR_AMOUNT = _apply_markup_minor(
        CLICK_SUB_MONTH_AMOUNT * 12,
        -YEARLY_DISCOUNT_PERCENT,
        step=100,
        allow_zero=True,
    )
else:
    CLICK_SUB_YEAR_AMOUNT = 0
CLICK_SUB_2WEEKS_LABEL = (os.environ.get("CLICK_SUB_2WEEKS_LABEL") or "🇺🇿 CLICK — 2 недели").strip()
CLICK_SUB_MONTH_LABEL = (os.environ.get("CLICK_SUB_MONTH_LABEL") or "🇺🇿 CLICK — месяц").strip()
CLICK_SUB_YEAR_LABEL = (os.environ.get("CLICK_SUB_YEAR_LABEL") or "🇺🇿 CLICK — год").strip()
CLICK_SUB_WEEK_LABEL = (os.environ.get("CLICK_SUB_WEEK_LABEL") or "🔥 CLICK — пробная неделя").strip()
BOT_PUBLIC_USERNAME = (os.environ.get("TELEGRAM_BOT_USERNAME") or "Ttaarrroobot").strip().lstrip("@")
_SUPPORT_CHAT_RAW = (os.environ.get("SUPPORT_INBOX_CHAT_ID") or "").strip()
SUPPORT_INBOX_CHAT_ID = int(_SUPPORT_CHAT_RAW) if _SUPPORT_CHAT_RAW.lstrip("-").isdigit() else None
SUPPORT_INBOX_BOT_TOKEN = (os.environ.get("SUPPORT_INBOX_BOT_TOKEN") or "").strip()
SUPPORT_ADMIN_IDS: Set[int] = {
    int(x.strip())
    for x in str(os.environ.get("SUPPORT_ADMIN_IDS") or "").split(",")
    if x.strip().lstrip("-").isdigit()
}
if SUPPORT_INBOX_CHAT_ID is None and SUPPORT_ADMIN_IDS:
    SUPPORT_INBOX_CHAT_ID = sorted(SUPPORT_ADMIN_IDS)[0]
START_ONBOARDING_TEXT = (
    os.environ.get("TELEGRAM_START_ONBOARDING_TEXT")
    or (
        "👋 <b>Добро пожаловать в AI Taro</b>\n\n"
        "<b>Бесплатно доступно:</b>\n"
        "• карта дня каждый день\n"
        "• <b>5 раскладов в месяц</b>\n\n"
        "Если нужен безлимит — нажмите <b>«💎 Подключить подписку»</b>.\n"
        "Чтобы начать, нажмите <b>«🚀 Начать»</b>.\n\n"
        "Открывая приложение, вы принимаете Пользовательское соглашение и Политику конфиденциальности."
    )
).strip()
BOT_SHORT_DESCRIPTION = (
    os.environ.get("TELEGRAM_BOT_SHORT_DESCRIPTION")
    or "Нажмите Start, затем «Начать»."
).strip()
BOT_DESCRIPTION = (
    os.environ.get("TELEGRAM_BOT_DESCRIPTION")
    or (
        "AI Taro — мини‑приложение с раскладами и AI‑интерпретацией.\n"
        "Чтобы начать, нажмите Start, затем кнопку «Начать»."
    )
).strip()

# ----- Тарифы -----
# amount — в минорных единицах (копейки/центы/сумы)
PRODUCTS: List[Dict[str, Any]] = [
    {
        "code": "sub_2weeks_ru",
        "menu_label": f"💳 По карте или SberPay — 2 недели — {RUB_SUB_2WEEKS_AMOUNT // 100} ₽",
        "menu_hint": "14 дней без ограничений через стандартную оплату Telegram (карта/SberPay).",
        "title": "Безлимит на 2 недели",
        "description": "Подписка AI Tarot на 14 дней",
        "amount": RUB_SUB_2WEEKS_AMOUNT,
        "kind": "subscription",
        "days": 14,
        "priority": 10,
        "currency": "RUB",
        "provider_mode": "yookassa_ru",
    },
    {
        "code": "sub_month_ru",
        "menu_label": f"💳 По карте или SberPay — месяц — {RUB_SUB_MONTH_AMOUNT // 100} ₽",
        "menu_hint": "30 дней полного доступа через стандартную оплату Telegram (карта/SberPay).",
        "title": "Безлимит на месяц",
        "description": "Подписка AI Tarot на 30 дней",
        "amount": RUB_SUB_MONTH_AMOUNT,
        "kind": "subscription",
        "days": 30,
        "priority": 20,
        "currency": "RUB",
        "provider_mode": "yookassa_ru",
    },
    {
        "code": "sub_year_ru",
        "menu_label": f"💳 По карте или SberPay — год — {RUB_SUB_YEAR_AMOUNT // 100} ₽",
        "menu_hint": "365 дней полного доступа со скидкой к ежемесячной цене.",
        "title": "Безлимит на год",
        "description": "Подписка AI Tarot на 365 дней",
        "amount": RUB_SUB_YEAR_AMOUNT,
        "kind": "subscription",
        "days": 365,
        "priority": 25,
        "currency": "RUB",
        "provider_mode": "yookassa_ru",
    },
    {
        "code": "sub_2weeks_usd",
        "menu_label": f"💳 Международная карта — 2 недели — ${USD_SUB_2WEEKS_CENTS / 100:.2f}",
        "menu_hint": "14 дней без ограничений для всех стран, кроме России.",
        "title": "Безлимит на 2 недели",
        "description": "Подписка AI Tarot на 14 дней",
        "amount": USD_SUB_2WEEKS_CENTS,
        "kind": "subscription",
        "days": 14,
        "priority": 30,
        "currency": "USD",
        "provider_mode": "yookassa_usd",
    },
    {
        "code": "sub_year_usd",
        "menu_label": f"💳 Международная карта — год — ${USD_SUB_YEAR_CENTS / 100:.2f}",
        "menu_hint": "365 дней полного доступа со скидкой к ежемесячной цене.",
        "title": "Безлимит на год",
        "description": "Подписка AI Tarot на 365 дней",
        "amount": USD_SUB_YEAR_CENTS,
        "kind": "subscription",
        "days": 365,
        "priority": 45,
        "currency": "USD",
        "provider_mode": "yookassa_usd",
    },
    {
        "code": "sub_month_usd",
        "menu_label": f"💳 Международная карта — месяц — ${USD_SUB_MONTH_CENTS / 100:.2f}",
        "menu_hint": "30 дней полного доступа для всех стран, кроме России.",
        "title": "Безлимит на месяц",
        "description": "Подписка AI Tarot на 30 дней",
        "amount": USD_SUB_MONTH_CENTS,
        "kind": "subscription",
        "days": 30,
        "priority": 40,
        "currency": "USD",
        "provider_mode": "yookassa_usd",
    },
    {
        "code": "sub_week_click",
        "menu_label": CLICK_SUB_WEEK_LABEL,
        "menu_hint": "Промо-тариф: 7 дней за 12 000 UZS (только для первой покупки).",
        "title": "Пробная неделя (CLICK)",
        "description": "Пробный доступ AI Tarot на 7 дней через CLICK",
        "amount": CLICK_SUB_WEEK_AMOUNT,
        "kind": "subscription",
        "days": 7,
        "priority": 205,
        "currency": CLICK_CURRENCY,
        "provider_mode": "click",
    },
    {
        "code": "sub_2weeks_click",
        "menu_label": CLICK_SUB_2WEEKS_LABEL,
        "menu_hint": "Оплата через CLICK (приложение Click или банковская карта).",
        "title": "Безлимит на 2 недели (CLICK)",
        "description": "Подписка AI Tarot на 14 дней через CLICK",
        "amount": CLICK_SUB_2WEEKS_AMOUNT,
        "kind": "subscription",
        "days": 14,
        "priority": 210,
        "currency": CLICK_CURRENCY,
        "provider_mode": "click",
    },
    {
        "code": "sub_month_click",
        "menu_label": CLICK_SUB_MONTH_LABEL,
        "menu_hint": "Оплата через CLICK (приложение Click или банковская карта).",
        "title": "Безлимит на месяц (CLICK)",
        "description": "Подписка AI Tarot на 30 дней через CLICK",
        "amount": CLICK_SUB_MONTH_AMOUNT,
        "kind": "subscription",
        "days": 30,
        "priority": 220,
        "currency": CLICK_CURRENCY,
        "provider_mode": "click",
    },
    {
        "code": "sub_year_click",
        "menu_label": CLICK_SUB_YEAR_LABEL,
        "menu_hint": "Оплата через CLICK (приложение Click или банковская карта).",
        "title": "Безлимит на год (CLICK)",
        "description": "Подписка AI Tarot на 365 дней через CLICK",
        "amount": CLICK_SUB_YEAR_AMOUNT,
        "kind": "subscription",
        "days": 365,
        "priority": 230,
        "currency": CLICK_CURRENCY,
        "provider_mode": "click",
    },
]

PLAN_ORDER = ["sub_week", "sub_2weeks", "sub_month", "sub_year"]
PLAN_TITLES = {
    "sub_week": "Неделя",
    "sub_2weeks": "2 недели",
    "sub_month": "Месяц",
    "sub_year": "Год",
}
COUNTRY_CHOICES: List[Dict[str, str]] = [
    {"code": "ru", "label": "🇷🇺 Россия"},
    {"code": "uz", "label": "🇺🇿 Узбекистан"},
    {"code": "kz", "label": "🇰🇿 Казахстан"},
    {"code": "by", "label": "🇧🇾 Беларусь"},
    {"code": "kg", "label": "🇰🇬 Кыргызстан"},
    {"code": "other", "label": "🌍 Другая страна"},
]
DEFAULT_COUNTRY_CODE = "ru"
TERMS_PLACEHOLDER_TEXT = (
    "Пользовательское соглашение AI Taro (черновик)\n\n"
    "1) AI Taro предоставляет информационные интерпретации и не заменяет медицинскую, юридическую или финансовую консультацию.\n"
    "2) При первом входе пользователь подтверждает согласие с документами и отдельное согласие на персональную память раскладов.\n"
    "3) Персональная память используется только для персонализации ответов и хранится до 90 дней.\n"
    "4) Память можно отключить в разделе «Персонализация».\n"
    "5) Для полного удаления персональных данных используйте команду /forgetme.\n"
    "6) Для поддержки используйте /support."
)
PRIVACY_PLACEHOLDER_TEXT = (
    "Политика конфиденциальности AI Taro (черновик)\n\n"
    "1) Сервис обрабатывает данные Telegram-профиля и пользовательские запросы для работы приложения.\n"
    "2) При включенной персональной памяти сохраняются структурированные данные раскладов (тема, тип расклада, карты, итоговый вывод).\n"
    "3) Срок хранения персональной памяти — до 90 дней.\n"
    "4) Данные не продаются третьим лицам.\n"
    "5) Полное удаление данных доступно командой /forgetme.\n"
    "6) По медико-кризисным темам сервис выдает мягкое предупреждение и контакты помощи по региону без постановки диагнозов."
)

# Простое хранилище заказов в памяти (для валидации precheckout и подтверждения оплаты)
# В проде лучше хранить в БД, но ты просил “без server.py”.
ORDERS: Dict[str, Dict[str, Any]] = {}
_BOT_UI_CONFIGURED = False
SUPPORT_PENDING_USERS: Set[int] = set()
SUPPORT_PENDING_TICKET_BY_USER: Dict[int, str] = {}
SUPPORT_ACTIVE_TICKET_BY_USER: Dict[int, str] = {}
SUPPORT_TICKETS: Dict[str, Dict[str, Any]] = {}


def _get_product(code: str) -> Optional[Dict[str, Any]]:
    for p in PRODUCTS:
        if p["code"] == code:
            return p
    return None


def _minor_units_to_value(amount_minor: int, currency: str) -> str:
    safe_currency = str(currency or "RUB").upper()
    safe_amount = max(0, int(amount_minor or 0))
    if safe_currency == "UZS":
        return str(safe_amount)
    return f"{safe_amount / 100:.2f}"


def _build_provider_data_for_receipt(product: Dict[str, Any]) -> str:
    amount_minor = int(product.get("amount") or 0)
    currency = str(product.get("currency") or "RUB").upper()
    title = str(product.get("title") or product.get("code") or "AI Tarot").strip() or "AI Tarot"
    title = title[:128]

    receipt_item: Dict[str, Any] = {
        "description": title,
        "quantity": "1.00",
        "amount": {
            "value": _minor_units_to_value(amount_minor, currency),
            "currency": currency,
        },
        "vat_code": int(YOOKASSA_VAT_CODE),
    }

    payload = {
        "receipt": {
            "items": [receipt_item],
        }
    }
    return json.dumps(payload, ensure_ascii=False)


def _build_sbp_payment_link(*, tg_user_id: int, plan_key: str) -> Optional[str]:
    if not API_PUBLIC_BASE or not SBP_BOT_LINK_SECRET:
        return None
    exp = int(time.time()) + (15 * 60)
    payload = f"{int(tg_user_id)}:{str(plan_key).strip().lower()}:{exp}"
    sig = hmac.new(
        SBP_BOT_LINK_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    query = urlencode(
        {
            "tg_user_id": int(tg_user_id),
            "plan_code": str(plan_key).strip().lower(),
            "exp": exp,
            "sig": sig,
        }
    )
    return f"{API_PUBLIC_BASE}/billing/sbp/bot-link?{query}"


def _build_sbp_autopay_link(*, tg_user_id: int, plan_key: str) -> Optional[str]:
    if not API_PUBLIC_BASE or not SBP_BOT_LINK_SECRET:
        return None
    if not SBP_AUTOPAY_ENABLED:
        return None
    exp = int(time.time()) + (15 * 60)
    payload = f"{int(tg_user_id)}:{str(plan_key).strip().lower()}:autopay:{exp}"
    sig = hmac.new(
        SBP_BOT_LINK_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    query = urlencode(
        {
            "tg_user_id": int(tg_user_id),
            "plan_code": str(plan_key).strip().lower(),
            "exp": exp,
            "sig": sig,
        }
    )
    return f"{API_PUBLIC_BASE}/billing/sbp/autopay/bot-link?{query}"


def _build_click_payment_link(
    *,
    tg_user_id: int,
    plan_key: str,
    checkout_mode: str = "app",
    card_type: Optional[str] = None,
) -> Optional[str]:
    if not API_PUBLIC_BASE or not CLICK_BOT_LINK_SECRET:
        return None
    exp = int(time.time()) + int(CLICK_BOT_LINK_TTL_SEC)
    payload = f"{int(tg_user_id)}:{str(plan_key).strip().lower()}:{exp}"
    sig = hmac.new(
        CLICK_BOT_LINK_SECRET.encode("utf-8"),
        payload.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    query = urlencode(
        {
            "tg_user_id": int(tg_user_id),
            "plan_code": str(plan_key).strip().lower(),
            "exp": exp,
            "sig": sig,
            "checkout_mode": ("card" if str(checkout_mode or "").strip().lower() == "card" else "app"),
            **(
                {"card_type": str(card_type).strip().lower()}
                if str(card_type or "").strip().lower() in {"uzcard", "humo"}
                else {}
            ),
        }
    )
    return f"{API_PUBLIC_BASE}/billing/click/bot-link?{query}"


def _products_for_mode(mode: str = "menu") -> List[Dict[str, Any]]:
    mode_l = str(mode or "menu").strip().lower()
    items = PRODUCTS
    click_link_available = bool(
        API_PUBLIC_BASE and CLICK_BOT_LINK_SECRET and CLICK_SERVICE_ID > 0 and CLICK_MERCHANT_ID > 0
    )
    click_available = bool(ENABLE_CLICK_PAYMENTS and (CLICK_PROVIDER_TOKEN or click_link_available))
    if not ENABLE_YOOKASSA_PAYMENTS:
        items = [p for p in items if not _is_card_provider_mode(str(p.get("provider_mode") or "").lower())]
    if not click_available:
        items = [p for p in items if str(p.get("provider_mode") or "").lower() != "click"]
    items = [p for p in items if int(p.get("amount") or 0) > 0]

    if mode_l in {"card", "cards", "sberpay", "card_sberpay"}:
        items = [p for p in items if _is_card_provider_mode(str(p.get("provider_mode") or "").lower())]
    elif mode_l in {"click", "uz", "uzbekistan", "click_card", "card_click", "clickcard"}:
        items = [p for p in items if str(p.get("provider_mode") or "").lower() == "click"]

    return sorted(items, key=lambda x: x.get("priority", 0))


def _plan_key_from_code(code: str) -> str:
    raw = str(code or "").strip().lower()
    if raw.endswith("_click"):
        return raw[:-6]
    if raw.endswith("_usd"):
        return raw[:-4]
    if raw.endswith("_ru"):
        return raw[:-3]
    return raw


def _format_amount_label(product: Dict[str, Any]) -> str:
    amount = int(product.get("amount") or 0)
    currency = str(product.get("currency") or "RUB").upper()
    if currency == "RUB":
        rub = amount / 100
        if float(rub).is_integer():
            return f"{int(rub)} ₽"
        return f"{rub:.2f} ₽".replace(".", ",")
    if currency == "UZS":
        return f"{amount:,}".replace(",", " ") + " UZS"
    if currency == "USD":
        usd = amount / 100
        return f"${usd:.2f}"
    return f"{amount} {currency}"


_CARD_PROVIDER_MODES = {"yookassa", "yookassa_ru", "yookassa_usd"}


def _is_card_provider_mode(provider_mode: str) -> bool:
    return str(provider_mode or "").strip().lower() in _CARD_PROVIDER_MODES


def _card_provider_mode_for_country(country_code: str) -> str:
    safe_country = _normalize_country_code(country_code)
    return "yookassa_ru" if safe_country == "ru" else "yookassa_usd"


def _card_product_for_country(plan_key: str, country_code: str) -> Optional[Dict[str, Any]]:
    safe_country = _normalize_country_code(country_code)
    primary_mode = _card_provider_mode_for_country(safe_country)
    candidate_modes = [primary_mode, "yookassa"]
    if safe_country == "ru":
        candidate_modes.append("yookassa_usd")
    else:
        candidate_modes.append("yookassa_ru")
    for mode in candidate_modes:
        product = _product_for_plan_and_provider(plan_key, mode)
        if product:
            return product
    return None


def _product_for_plan_and_provider(plan_key: str, provider_mode: str) -> Optional[Dict[str, Any]]:
    for product in _products_for_mode("menu"):
        if _plan_key_from_code(product.get("code") or "") != plan_key:
            continue
        if str(product.get("provider_mode") or "").strip().lower() == str(provider_mode or "").strip().lower():
            return product
    return None


def _available_plan_keys() -> List[str]:
    visible = _products_for_mode("menu")
    keys = {_plan_key_from_code(p.get("code") or "") for p in visible}
    return [k for k in PLAN_ORDER if k in keys]


def _normalize_country_code(country_code: str) -> str:
    code = str(country_code or "").strip().lower()
    available = {item["code"] for item in COUNTRY_CHOICES}
    return code if code in available else DEFAULT_COUNTRY_CODE


def _country_label(country_code: str) -> str:
    safe = _normalize_country_code(country_code)
    for item in COUNTRY_CHOICES:
        if item["code"] == safe:
            return item["label"]
    return safe.upper()


def _available_plan_keys_for_country(country_code: str) -> List[str]:
    safe_country = _normalize_country_code(country_code)
    visible = _products_for_mode("menu")
    click_keys = {
        _plan_key_from_code(p.get("code") or "")
        for p in visible
        if str(p.get("provider_mode") or "").lower() == "click"
    }
    visible_keys = {_plan_key_from_code(p.get("code") or "") for p in visible}

    result: List[str] = []
    for key in PLAN_ORDER:
        if key not in visible_keys:
            continue
        has_card = _card_product_for_country(key, safe_country) is not None
        if safe_country == "uz":
            if key in click_keys or has_card:
                result.append(key)
        elif safe_country == "ru":
            if has_card:
                result.append(key)
        else:
            if has_card:
                result.append(key)
    return result


def _plan_summary_label(plan_key: str, country_code: str) -> str:
    title = PLAN_TITLES.get(plan_key, plan_key)
    safe_country = _normalize_country_code(country_code)

    card_product = _card_product_for_country(plan_key, safe_country)
    click_product = _product_for_plan_and_provider(plan_key, "click")

    if safe_country == "uz" and click_product:
        return f"{title} — {_format_amount_label(click_product)}"
    if card_product:
        return f"{title} — {_format_amount_label(card_product)}"
    if click_product:
        return f"{title} — {_format_amount_label(click_product)}"
    return title


def _country_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for country in COUNTRY_CHOICES:
        rows.append([InlineKeyboardButton(country["label"], callback_data=f"country:{country['code']}")])
    return InlineKeyboardMarkup(rows)


def _format_country_list() -> str:
    return (
        "<b>Тарифы AI Tarot</b>\n"
        "Шаг 1 из 3: выберите страну.\n\n"
        "Мы покажем подходящие способы оплаты и корректную валюту."
    )


def _plans_keyboard(country_code: str) -> InlineKeyboardMarkup:
    safe_country = _normalize_country_code(country_code)
    rows: List[List[InlineKeyboardButton]] = []
    for plan_key in _available_plan_keys_for_country(safe_country):
        rows.append(
            [
                InlineKeyboardButton(
                    _plan_summary_label(plan_key, safe_country),
                    callback_data=f"plan:{safe_country}:{plan_key}",
                )
            ]
        )
    if not rows:
        rows.append([InlineKeyboardButton("Обновить", callback_data=f"country:{safe_country}")])
    rows.append([InlineKeyboardButton("⬅️ Назад к выбору страны", callback_data="country_menu")])
    return InlineKeyboardMarkup(rows)


def _format_plan_list(country_code: str) -> str:
    safe_country = _normalize_country_code(country_code)
    parts: List[str] = [
        "<b>Тарифы AI Tarot</b>",
        f"Страна: <b>{_country_label(safe_country)}</b>",
        "Шаг 2 из 3: выберите период подписки.",
        "",
    ]
    for plan_key in _available_plan_keys_for_country(safe_country):
        parts.append(f"• <b>{_plan_summary_label(plan_key, safe_country)}</b>")
    parts.append("")
    parts.append("Нажмите на подходящий план.")
    return "\n".join(parts).strip()


def _method_keyboard(plan_key: str, country_code: str) -> InlineKeyboardMarkup:
    safe_country = _normalize_country_code(country_code)
    rows: List[List[InlineKeyboardButton]] = []

    card_product = _card_product_for_country(plan_key, safe_country)
    click_product = _product_for_plan_and_provider(plan_key, "click")

    if safe_country == "ru":
        if card_product:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"🟢 СБП — {_format_amount_label(card_product)}",
                        callback_data=f"sbp:{safe_country}:{plan_key}",
                    )
                ]
            )
            if SBP_AUTOPAY_ENABLED and str(plan_key) == SBP_AUTOPAY_PLAN_KEY:
                rows.append(
                    [
                        InlineKeyboardButton(
                            f"🔁 СБП автоплатёж — {_format_amount_label(card_product)}",
                            callback_data=f"sbp_auto:{safe_country}:{plan_key}",
                        )
                    ]
                )
            rows.append(
                [
                    InlineKeyboardButton(
                        f"💳 По карте или SberPay — {_format_amount_label(card_product)}",
                        callback_data=f"buy:{card_product['code']}",
                    )
                ]
            )
    elif safe_country == "uz":
        if click_product:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"🇺🇿 CLICK / карта через CLICK — {_format_amount_label(click_product)}",
                        callback_data=f"buy:{click_product['code']}",
                    )
                ]
            )
        if card_product:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"💳 Международная карта — {_format_amount_label(card_product)}",
                        callback_data=f"buy:{card_product['code']}",
                    )
                ]
            )
    else:
        if card_product:
            rows.append(
                [
                    InlineKeyboardButton(
                        f"💳 Международная карта — {_format_amount_label(card_product)}",
                        callback_data=f"buy:{card_product['code']}",
                    )
                ]
            )

    rows.append([InlineKeyboardButton("⬅️ Назад к планам", callback_data=f"country:{safe_country}")])
    return InlineKeyboardMarkup(rows)


def _format_methods_text(plan_key: str, country_code: str) -> str:
    safe_country = _normalize_country_code(country_code)
    title = PLAN_TITLES.get(plan_key, plan_key)
    methods: List[str] = []

    card_product = _card_product_for_country(plan_key, safe_country)
    click_product = _product_for_plan_and_provider(plan_key, "click")

    if safe_country == "ru":
        if card_product:
            methods.append(f"• СБП — {_format_amount_label(card_product)}")
            if SBP_AUTOPAY_ENABLED and str(plan_key) == SBP_AUTOPAY_PLAN_KEY:
                methods.append(f"• СБП автоплатёж — {_format_amount_label(card_product)} / месяц")
            methods.append(f"• По карте / SberPay — {_format_amount_label(card_product)}")
    elif safe_country == "uz":
        if click_product:
            methods.append(f"• CLICK (приложение) или карта через CLICK — {_format_amount_label(click_product)}")
            if plan_key == CLICK_INTRO_PLAN_KEY and CLICK_INTRO_FIRST_PURCHASE_ONLY:
                methods.append("• Пробная неделя доступна только для первой покупки")
        if card_product:
            methods.append(f"• Международная карта — {_format_amount_label(card_product)}")
    else:
        if card_product:
            methods.append(f"• Международная карта — {_format_amount_label(card_product)}")

    if not methods:
        methods.append("• Способы оплаты временно недоступны")

    return (
        f"<b>{title}</b>\n"
        f"Страна: <b>{_country_label(safe_country)}</b>\n"
        "Шаг 3 из 3: выберите способ оплаты.\n\n"
        + "\n".join(methods)
    )


def _parse_country_plan_callback(data: str, prefix: str) -> Tuple[str, str]:
    raw = str(data or "").strip()
    if not raw.startswith(f"{prefix}:"):
        return DEFAULT_COUNTRY_CODE, ""

    parts = raw.split(":")
    if len(parts) >= 3:
        country_code = _normalize_country_code(parts[1])
        plan_key = str(parts[2] or "").strip().lower()
        return country_code, plan_key
    if len(parts) == 2:
        # Backward compatibility: old callbacks had no country, only plan key.
        return DEFAULT_COUNTRY_CODE, str(parts[1] or "").strip().lower()
    return DEFAULT_COUNTRY_CODE, ""


def _format_price_list(mode: str = "menu") -> str:
    products = _products_for_mode(mode)
    mode_l = str(mode or "menu").strip().lower()
    parts: List[str] = [
        "<b>Тарифы AI Tarot</b>",
        "Плати внутри Telegram и получи доступ сразу после оплаты.",
        "",
    ]
    if str(mode).lower() in {"buy_credits", "credits", "buy"}:
        parts = [
            "<b>Подключение безлимита</b>",
            "Выберите период подписки:",
            "",
        ]
    elif mode_l in {"card", "cards", "sberpay", "card_sberpay"}:
        parts = [
            "<b>По карте или SberPay</b>",
            "Выберите период подписки:",
            "",
        ]
    elif mode_l in {"click", "uz", "uzbekistan", "click_card", "card_click", "clickcard"}:
        parts = [
            "<b>Оплата через CLICK / картой через CLICK</b>",
            "Выберите период подписки:",
            "",
        ]

    for product in products:
        parts.append(f"<b>{product['menu_label']}</b>")
        hint = (product.get("menu_hint") or "").strip()
        if hint:
            parts.append(hint)
        parts.append("")
    parts.append("Нажми на подходящий план, чтобы получить счёт.")
    return "\n".join(parts).strip()


def _price_keyboard(mode: str = "menu") -> InlineKeyboardMarkup:
    products = _products_for_mode(mode)
    rows: List[List[InlineKeyboardButton]] = []
    for product in products:
        rows.append([InlineKeyboardButton(product["menu_label"], callback_data=f"buy:{product['code']}")])
    return InlineKeyboardMarkup(rows)


def _app_webapp_url() -> Optional[str]:
    raw = str(APP_URL_RAW or "").strip()
    if raw.startswith("https://") and not raw.startswith("https://t.me/"):
        return raw
    return None


def _bot_deeplink(mode: str) -> Optional[str]:
    username = str(BOT_PUBLIC_USERNAME or "").strip().lstrip("@")
    payload = str(mode or "").strip()
    if not username:
        return None
    if payload:
        return f"https://t.me/{username}?start={payload}"
    return f"https://t.me/{username}"


def _build_app_button(custom_text: Optional[str] = None) -> Optional[InlineKeyboardButton]:
    """
    Кнопка открытия Mini App.
    Если https-домен (добавлен в BotFather Web App) → web_app кнопка.
    Если t.me/startapp → обычная url кнопка.
    """
    if not APP_URL_RAW:
        return None

    url = APP_URL_RAW

    text = str(custom_text or APP_BUTTON_TEXT).strip() or APP_BUTTON_TEXT
    if url.startswith("https://t.me/"):
        return InlineKeyboardButton(text, url=url)

    if url.startswith("https://"):
        return InlineKeyboardButton(text, web_app=WebAppInfo(url=url))

    logger.warning("TELEGRAM_APP_URL is invalid (%s), skipping WebApp button", url)
    return None


def _welcome_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    app_btn = _build_app_button(APP_BUTTON_TEXT)
    if app_btn:
        rows.append([app_btn])
    rows.append(
        [
            InlineKeyboardButton("💎 Подключить подписку", callback_data="menu"),
            InlineKeyboardButton("💬 Поддержка", callback_data="support"),
        ]
    )
    rows.append(
        [
            InlineKeyboardButton("📄 Соглашение", callback_data="doc:terms"),
            InlineKeyboardButton("🔐 Политика", callback_data="doc:privacy"),
        ]
    )
    rows.append([InlineKeyboardButton("ℹ️ Как начать", callback_data="howto")])
    return InlineKeyboardMarkup(rows)


def _quick_reply_keyboard() -> Optional[ReplyKeyboardMarkup]:
    rows: List[List[KeyboardButton]] = []
    webapp_url = _app_webapp_url()

    if webapp_url:
        rows.append([KeyboardButton(APP_BUTTON_TEXT, web_app=WebAppInfo(url=webapp_url))])
    elif APP_URL_RAW:
        rows.append([KeyboardButton(APP_BUTTON_TEXT)])

    rows.append([KeyboardButton("💎 Подключить подписку"), KeyboardButton("💬 Поддержка")])
    rows.append([KeyboardButton("📄 Соглашение"), KeyboardButton("🔐 Политика")])
    rows.append([KeyboardButton("ℹ️ Как начать")])
    return ReplyKeyboardMarkup(
        rows,
        resize_keyboard=True,
        one_time_keyboard=False,
        is_persistent=True,
        input_field_placeholder="Выберите действие",
    )


def _howto_text() -> str:
    return (
        "<b>Как быстро начать</b>\n\n"
        "1) Нажмите <b>🚀 Начать</b>\n"
        "2) Разрешите открытие мини‑приложения в Telegram\n"
        "3) Задайте вопрос и выберите расклад\n\n"
        "💡 Оплату можно открыть кнопкой <b>«💎 Подключить подписку»</b>.\n"
        "💬 Поддержка: <b>/support</b>\n"
        "📄 Соглашение: <b>/terms</b>\n"
        "🔐 Политика: <b>/privacy</b>\n"
        "🗑 Удаление данных: <b>/forgetme</b>"
    )


def _subscription_manage_text() -> str:
    if SBP_AUTOPAY_ENABLED:
        return (
            "<b>Управление подпиской</b>\n\n"
            "Выберите действие:\n"
            "• подключить/настроить СБП автоплатёж;\n"
            "• запросить отмену подписки через поддержку;\n"
            "• перейти к тарифам и оплате."
        )
    return (
        "<b>Управление подпиской</b>\n\n"
        "Выберите действие:\n"
        "• запросить отмену подписки через поддержку;\n"
        "• перейти к тарифам и оплате."
    )


def _subscription_manage_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    if SBP_AUTOPAY_ENABLED:
        rows.append(
            [
                InlineKeyboardButton(
                    "🔁 СБП автоплатёж",
                    callback_data=f"sbp_auto:{SBP_AUTOPAY_PLAN_KEY}",
                )
            ]
        )
    rows.append([InlineKeyboardButton("🛑 Как отменить подписку", callback_data="sub_cancel")])
    rows.append([InlineKeyboardButton("💳 Тарифы и оплата", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def _subscription_cancel_text() -> str:
    return (
        "🛑 <b>Отмена подписки</b>\n\n"
        "Чтобы отменить подписку, напишите в поддержку одним сообщением:\n"
        "например, «Прошу отменить подписку».\n\n"
        "Ответ придёт сюда же, в этот чат."
    )


def _subscription_cancel_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("💬 Написать в поддержку", callback_data="support")],
            [InlineKeyboardButton("⬅️ Назад к управлению подпиской", callback_data="sub_manage")],
        ]
    )


async def _send_legal_document(
    *,
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    kind: str,
) -> None:
    kind_l = str(kind or "").strip().lower()
    if kind_l == "privacy":
        filename = "ai_taro_privacy_policy_draft.txt"
        caption = "🔐 Политика конфиденциальности (черновик)"
        text = PRIVACY_PLACEHOLDER_TEXT
    else:
        filename = "ai_taro_user_agreement_draft.txt"
        caption = "📄 Пользовательское соглашение (черновик)"
        text = TERMS_PLACEHOLDER_TEXT

    content = BytesIO(text.encode("utf-8"))
    content.name = filename
    await context.bot.send_document(
        chat_id=chat_id,
        document=InputFile(content, filename=filename),
        caption=f"{caption}\nФинальную версию заменим позже без изменения кнопок.",
    )


def _forgetme_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("🗑 Да, удалить мои данные", callback_data="forgetme_confirm")],
            [InlineKeyboardButton("Отмена", callback_data="forgetme_cancel")],
        ]
    )


def _cleanup_support_runtime_for_user(telegram_user_id: int) -> None:
    if telegram_user_id <= 0:
        return
    SUPPORT_PENDING_USERS.discard(telegram_user_id)
    SUPPORT_PENDING_TICKET_BY_USER.pop(telegram_user_id, None)
    active_ticket = SUPPORT_ACTIVE_TICKET_BY_USER.pop(telegram_user_id, None)
    if active_ticket:
        SUPPORT_TICKETS.pop(str(active_ticket), None)


async def _forgetme_delete_user_data(*, telegram_user_id: int) -> Dict[str, Any]:
    if telegram_user_id <= 0:
        return {"ok": False, "reason": "invalid_user"}
    async with SessionLocal() as db:
        try:
            q = await db.execute(select(User).where(User.telegram_id == int(telegram_user_id)))
            row = q.scalar_one_or_none()
            if not row:
                return {"ok": True, "deleted": False}
            await db.delete(row)
            await db.commit()
            _cleanup_support_runtime_for_user(int(telegram_user_id))
            return {"ok": True, "deleted": True}
        except Exception as exc:
            await db.rollback()
            logger.exception("forgetme failed for tg_user=%s: %s", telegram_user_id, exc)
            return {"ok": False, "reason": "db_error", "error": str(exc)}


def _support_target_enabled() -> bool:
    return bool(SUPPORT_INBOX_CHAT_ID)


def _is_support_operator(update: Update) -> bool:
    user_id = int(getattr(update.effective_user, "id", 0) or 0)
    if user_id <= 0:
        return False
    if SUPPORT_ADMIN_IDS and user_id in SUPPORT_ADMIN_IDS:
        return True
    return False


def _build_support_ticket_id(user_id: int) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    rnd = secrets.token_hex(2).upper()
    return f"SUP-{stamp}-{int(user_id)}-{rnd}"


def _support_user_reply_markup(ticket_id: Optional[str] = None) -> InlineKeyboardMarkup:
    safe_ticket = str(ticket_id or "").strip()
    support_cb = f"support:{safe_ticket}" if safe_ticket else "support"
    close_cb = f"support_close:{safe_ticket}" if safe_ticket else "support_close"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton("💬 Ответить", callback_data=support_cb),
                InlineKeyboardButton("✅ Закрыть диалог", callback_data=close_cb),
            ]
        ]
    )


def _support_inbox_reply_markup(*, ticket_id: str, user_id: int) -> InlineKeyboardMarkup:
    safe_ticket = str(ticket_id or "").strip()
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    "💬 Ответить пользователю",
                    callback_data=f"support_reply:{safe_ticket}:{int(user_id)}",
                ),
                InlineKeyboardButton(
                    "✅ Закрыть тикет",
                    callback_data=f"support_close:{safe_ticket}:{int(user_id)}",
                ),
            ]
        ]
    )


def _sync_ticket_runtime_state(
    *,
    ticket_id: str,
    user_id: int,
    source_chat_id: int,
    status: str,
    messages_count: int,
    inbox_message_id: Optional[int],
) -> Dict[str, Any]:
    now_ts = int(time.time())
    state = SUPPORT_TICKETS.get(ticket_id) or {
        "ticket_id": ticket_id,
        "user_id": int(user_id),
        "source_chat_id": int(source_chat_id),
        "status": str(status or "open"),
        "created_at": now_ts,
        "last_message_at": now_ts,
        "messages": int(messages_count),
        "inbox_message_id": inbox_message_id,
    }
    state["status"] = str(status or state.get("status") or "open")
    state["last_message_at"] = now_ts
    state["messages"] = int(max(0, int(messages_count)))
    if inbox_message_id and not state.get("inbox_message_id"):
        state["inbox_message_id"] = int(inbox_message_id)
    SUPPORT_TICKETS[ticket_id] = state
    if user_id > 0:
        SUPPORT_ACTIVE_TICKET_BY_USER[int(user_id)] = ticket_id
    return state


async def _ensure_support_user(db, from_user) -> User:
    tg_user_id = int(getattr(from_user, "id", 0) or 0)
    q = await db.execute(select(User).where(User.telegram_id == tg_user_id))
    row = q.scalar_one_or_none()
    if row:
        return row
    row = User(
        telegram_id=tg_user_id,
        username=(str(getattr(from_user, "username", "") or "").strip() or None),
        first_name=(str(getattr(from_user, "first_name", "") or "").strip() or None),
        last_name=(str(getattr(from_user, "last_name", "") or "").strip() or None),
    )
    db.add(row)
    await db.flush()
    return row


async def _persist_support_user_message(
    *,
    from_user,
    source_chat_id: int,
    message_text: str,
    ticket_id: Optional[str],
    telegram_message_id: Optional[int],
) -> Optional[Dict[str, Any]]:
    if not FEATURE_FLAGS.tickets_v2:
        return None

    tg_user_id = int(getattr(from_user, "id", 0) or 0)
    if tg_user_id <= 0:
        return None

    preferred_ticket = str(ticket_id or "").strip().upper() or None

    async with SessionLocal() as db:
        try:
            user_row = await _ensure_support_user(db, from_user)
            ticket_row, _ = await support_ticket_service.get_or_create_open_ticket(
                db,
                user_id=int(user_row.id),
                telegram_user_id=tg_user_id,
                source_chat_id=int(source_chat_id),
                preferred_ticket_id=preferred_ticket,
                support_chat_id=SUPPORT_INBOX_CHAT_ID,
            )

            await support_ticket_service.register_user_message(
                db,
                ticket=ticket_row,
                text=str(message_text or ""),
                sender_telegram_id=tg_user_id,
                telegram_chat_id=int(source_chat_id),
                telegram_message_id=(int(telegram_message_id) if telegram_message_id else None),
            )
            if SUPPORT_INBOX_CHAT_ID and not ticket_row.support_chat_id:
                ticket_row.support_chat_id = int(SUPPORT_INBOX_CHAT_ID)

            msg_count = await support_ticket_repository.count_messages(db, ticket_pk=int(ticket_row.id))
            await db.commit()

            return {
                "ticket_id": str(ticket_row.ticket_id or "").strip().upper(),
                "status": str(ticket_row.status or "open"),
                "messages_count": int(msg_count),
                "inbox_message_id": int(getattr(ticket_row, "support_inbox_message_id", 0) or 0) or None,
            }
        except Exception as exc:
            await db.rollback()
            logger.warning("Support message DB persistence failed for user %s: %s", tg_user_id, exc)
            return None


async def _store_support_inbox_anchor(ticket_id: str, inbox_message_id: int) -> None:
    if not FEATURE_FLAGS.tickets_v2:
        return
    safe_ticket = str(ticket_id or "").strip().upper()
    if not safe_ticket or int(inbox_message_id or 0) <= 0:
        return
    async with SessionLocal() as db:
        try:
            row = await support_ticket_repository.get_ticket_by_public_id(db, safe_ticket)
            if not row:
                return
            if hasattr(row, "support_inbox_message_id") and not int(getattr(row, "support_inbox_message_id", 0) or 0):
                setattr(row, "support_inbox_message_id", int(inbox_message_id))
            if SUPPORT_INBOX_CHAT_ID and not row.support_chat_id:
                row.support_chat_id = int(SUPPORT_INBOX_CHAT_ID)
            await db.commit()
        except Exception as exc:
            await db.rollback()
            logger.warning("Failed to store support inbox anchor ticket=%s: %s", safe_ticket, exc)


async def _mark_support_ticket_closed(
    *,
    ticket_id: str,
    actor_telegram_id: int,
    actor_chat_id: int,
) -> bool:
    if not FEATURE_FLAGS.tickets_v2:
        return False
    safe_ticket = str(ticket_id or "").strip().upper()
    if not safe_ticket:
        return False
    async with SessionLocal() as db:
        try:
            row = await support_ticket_repository.get_ticket_by_public_id(db, safe_ticket)
            if not row:
                return False
            row.status = "closed"
            row.closed_at = datetime.now(timezone.utc)
            row.updated_at = datetime.now(timezone.utc)
            await support_ticket_repository.add_message(
                db,
                ticket=row,
                direction="system",
                text="Тикет закрыт пользователем.",
                sender_telegram_id=int(actor_telegram_id) if actor_telegram_id else None,
                telegram_chat_id=int(actor_chat_id) if actor_chat_id else None,
            )
            await db.commit()
            return True
        except Exception as exc:
            await db.rollback()
            logger.warning("Failed to close support ticket ticket=%s: %s", safe_ticket, exc)
            return False


async def _forward_support_to_inbox(
    *,
    context: ContextTypes.DEFAULT_TYPE,
    from_user,
    source_chat_id: int,
    message_text: str,
    ticket_id: Optional[str] = None,
    source_message_id: Optional[int] = None,
) -> Dict[str, Any]:
    target_chat_id = SUPPORT_INBOX_CHAT_ID
    if not target_chat_id:
        return {"ok": False, "ticket_id": "", "is_new": False}

    user_id = int(getattr(from_user, "id", 0) or 0)
    username = str(getattr(from_user, "username", "") or "").strip()
    first_name = str(getattr(from_user, "first_name", "") or "").strip()
    last_name = str(getattr(from_user, "last_name", "") or "").strip()
    user_name_line = f"{first_name} {last_name}".strip() or "без имени"
    profile_link = f'<a href="tg://user?id={user_id}">профиль</a>' if user_id > 0 else "профиль недоступен"
    safe_text = escape(str(message_text or "").strip())
    safe_username = escape(f"@{username}" if username else "—")
    safe_name = escape(user_name_line)

    preferred_ticket = str(ticket_id or SUPPORT_ACTIVE_TICKET_BY_USER.get(user_id) or "").strip().upper()
    ticket_state = SUPPORT_TICKETS.get(preferred_ticket) if preferred_ticket else None

    persisted = await _persist_support_user_message(
        from_user=from_user,
        source_chat_id=int(source_chat_id),
        message_text=message_text,
        ticket_id=preferred_ticket or None,
        telegram_message_id=source_message_id,
    )
    used_persisted = persisted is not None
    if persisted:
        ticket_id = str(persisted.get("ticket_id") or "").strip().upper()
        ticket_state = _sync_ticket_runtime_state(
            ticket_id=ticket_id,
            user_id=user_id,
            source_chat_id=int(source_chat_id),
            status=str(persisted.get("status") or "open"),
            messages_count=int(persisted.get("messages_count") or 0),
            inbox_message_id=(int(persisted.get("inbox_message_id") or 0) or None),
        )
    else:
        ticket_id = preferred_ticket
        if not ticket_state:
            ticket_id = _build_support_ticket_id(user_id)
            ticket_state = _sync_ticket_runtime_state(
                ticket_id=ticket_id,
                user_id=user_id,
                source_chat_id=int(source_chat_id),
                status="open",
                messages_count=0,
                inbox_message_id=None,
            )
        else:
            ticket_state = _sync_ticket_runtime_state(
                ticket_id=ticket_id,
                user_id=user_id,
                source_chat_id=int(source_chat_id),
                status="open",
                messages_count=int(ticket_state.get("messages") or 0),
                inbox_message_id=(int(ticket_state.get("inbox_message_id") or 0) or None),
            )

    safe_ticket = escape(str(ticket_id or ""))
    is_new_ticket = int(ticket_state.get("messages") or 0) <= 1
    if is_new_ticket:
        support_payload = (
            "📩 <b>Новый запрос в поддержку</b>\n"
            f"Тикет: <code>{safe_ticket}</code>\n"
            f"ID: <code>{user_id}</code>\n"
            f"Пользователь: {safe_name}\n"
            f"Username: {safe_username}\n"
            f"Чат: <code>{source_chat_id}</code>\n"
            f"Ссылка: {profile_link}\n\n"
            f"<b>Сообщение:</b>\n{safe_text}\n\n"
            "Нажмите кнопку ниже, чтобы ответить в один шаг.\n"
            f"Резерв: <code>/reply {user_id} ваш_текст</code>"
        )
    else:
        support_payload = (
            "📨 <b>Новое сообщение в тикете</b>\n"
            f"Тикет: <code>{safe_ticket}</code>\n"
            f"ID: <code>{user_id}</code>\n"
            f"Пользователь: {safe_name}\n\n"
            f"<b>Сообщение:</b>\n{safe_text}"
        )

    reply_markup = _support_inbox_reply_markup(ticket_id=ticket_id, user_id=user_id)
    reply_to_message_id = int(ticket_state.get("inbox_message_id") or 0) or None
    sent_message = None

    if SUPPORT_INBOX_BOT_TOKEN:
        try:
            inbox_bot = Bot(token=SUPPORT_INBOX_BOT_TOKEN)
            sent_message = await inbox_bot.send_message(
                chat_id=target_chat_id,
                text=support_payload,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
                allow_sending_without_reply=True,
            )
        except Exception as exc:
            logger.warning("Support forward via SUPPORT_INBOX_BOT_TOKEN failed, fallback to main bot: %s", exc)

    if not sent_message:
        try:
            sent_message = await context.bot.send_message(
                chat_id=target_chat_id,
                text=support_payload,
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
                reply_markup=reply_markup,
                reply_to_message_id=reply_to_message_id,
                allow_sending_without_reply=True,
            )
        except Exception as exc:
            logger.warning("Support forward failed: %s", exc)
            return {"ok": False, "ticket_id": ticket_id, "is_new": is_new_ticket}

    just_set_anchor = False
    if not ticket_state.get("inbox_message_id"):
        ticket_state["inbox_message_id"] = int(getattr(sent_message, "message_id", 0) or 0) or None
        just_set_anchor = bool(ticket_state.get("inbox_message_id"))
    if not used_persisted:
        ticket_state["messages"] = int(ticket_state.get("messages") or 0) + 1
    SUPPORT_TICKETS[ticket_id] = ticket_state
    if user_id > 0:
        SUPPORT_ACTIVE_TICKET_BY_USER[user_id] = ticket_id
    if just_set_anchor:
        await _store_support_inbox_anchor(ticket_id, int(ticket_state.get("inbox_message_id") or 0))
    return {"ok": True, "ticket_id": ticket_id, "is_new": is_new_ticket}


async def _ensure_bot_ui(context: ContextTypes.DEFAULT_TYPE) -> None:
    global _BOT_UI_CONFIGURED
    if _BOT_UI_CONFIGURED:
        return
    try:
        await context.bot.set_my_commands(
            [
                BotCommand("start", "Начать"),
                BotCommand("menu", "Тарифы и оплата"),
                BotCommand("help", "Как начать"),
                BotCommand("support", "Написать в поддержку"),
                BotCommand("terms", "Пользовательское соглашение"),
                BotCommand("privacy", "Политика конфиденциальности"),
                BotCommand("forgetme", "Удалить персональные данные"),
            ]
        )
        await context.bot.set_my_short_description(BOT_SHORT_DESCRIPTION)
        await context.bot.set_my_description(BOT_DESCRIPTION)
        webapp_url = _app_webapp_url()
        if webapp_url:
            await context.bot.set_chat_menu_button(
                menu_button=MenuButtonWebApp(text=APP_MENU_BUTTON_TEXT, web_app=WebAppInfo(url=webapp_url))
            )
        _BOT_UI_CONFIGURED = True
    except Exception as exc:
        logger.warning("Failed to configure bot commands/menu button: %s", exc)


async def configure_bot_ui_at_startup(application: Application) -> None:
    # Configure menu/commands once at process boot so first-time users see hints immediately.
    await _ensure_bot_ui(SimpleNamespace(bot=application.bot))


async def _send_start_panel(chat_id: int, context: ContextTypes.DEFAULT_TYPE) -> None:
    quick_kb = _quick_reply_keyboard()
    if quick_kb:
        await context.bot.send_message(
            chat_id=chat_id,
            text="👇 Нажмите большую кнопку <b>«🚀 Начать»</b> внизу — приложение откроется сразу.",
            parse_mode=ParseMode.HTML,
            reply_markup=quick_kb,
        )
    await context.bot.send_message(
        chat_id=chat_id,
        text=START_ONBOARDING_TEXT,
        parse_mode=ParseMode.HTML,
        reply_markup=_welcome_keyboard(),
    )


def _to_utc(dt: Optional[datetime]) -> Optional[datetime]:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _database_dsn_for_asyncpg() -> str:
    raw = (os.environ.get("DATABASE_URL") or "").strip()
    if not raw:
        raise RuntimeError("DATABASE_URL is not set")
    if raw.startswith("postgresql+asyncpg://"):
        return "postgresql://" + raw[len("postgresql+asyncpg://"):]
    return raw


async def _click_intro_plan_already_used_by_tg_user(tg_user_id: int) -> bool:
    if not CLICK_INTRO_FIRST_PURCHASE_ONLY:
        return False
    if not CLICK_INTRO_PLAN_KEY or int(tg_user_id or 0) <= 0:
        return False
    conn: Optional[asyncpg.Connection] = None
    try:
        dsn = _database_dsn_for_asyncpg()
        conn = await asyncpg.connect(dsn=dsn)
        target_product_code = f"{CLICK_INTRO_PLAN_KEY}_click"
        row = await conn.fetchval(
            """
            SELECT pt.id
            FROM payment_transactions pt
            JOIN users u ON u.id = pt.user_id
            WHERE u.telegram_id = $1
              AND pt.kind = 'subscription'
              AND pt.product_code = $2
            LIMIT 1
            """,
            int(tg_user_id),
            target_product_code,
        )
        return row is not None
    except Exception as exc:
        logger.warning("Failed to check CLICK intro usage for tg_user=%s: %s", tg_user_id, repr(exc))
        return False
    finally:
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass


async def _activate_purchase_for_user(
    *,
    tg_user_id: int,
    payload: str,
    product: Dict[str, Any],
    total_amount: int,
    currency: str,
    tg_charge_id: str,
    provider_charge_id: str,
) -> Dict[str, Any]:
    """
    Persist payment effect in DB so API limits can rely on real balances/subscription.
    Idempotent by Telegram/provider charge IDs.
    """
    conn: Optional[asyncpg.Connection] = None
    try:
        dsn = _database_dsn_for_asyncpg()
        conn = await asyncpg.connect(dsn=dsn)

        async with conn.transaction():
            if tg_charge_id:
                exists_tg = await conn.fetchval(
                    "SELECT 1 FROM payment_transactions WHERE telegram_payment_charge_id = $1 LIMIT 1",
                    str(tg_charge_id),
                )
                if exists_tg:
                    return {"applied": False, "duplicate": True}

            if provider_charge_id:
                exists_provider = await conn.fetchval(
                    "SELECT 1 FROM payment_transactions WHERE provider_payment_charge_id = $1 LIMIT 1",
                    str(provider_charge_id),
                )
                if exists_provider:
                    return {"applied": False, "duplicate": True}

            user_row = await conn.fetchrow(
                """
                SELECT id, paid_readings_balance, subscription_until
                FROM users
                WHERE telegram_id = $1
                FOR UPDATE
                """,
                int(tg_user_id),
            )
            if not user_row:
                user_row = await conn.fetchrow(
                    """
                    INSERT INTO users (telegram_id, paid_readings_balance)
                    VALUES ($1, 0)
                    RETURNING id, paid_readings_balance, subscription_until
                    """,
                    int(tg_user_id),
                )

            user_id = int(user_row["id"])
            now = datetime.now(timezone.utc)
            kind = str(product.get("kind") or "credits")

            credits_delta = 0
            sub_days = 0
            balance_after = int(user_row["paid_readings_balance"] or 0)
            subscription_until = _to_utc(user_row["subscription_until"])

            if kind == "subscription":
                sub_days = max(0, int(product.get("days") or 0))
                base = now
                if subscription_until and subscription_until > base:
                    base = subscription_until
                subscription_until = base + timedelta(days=sub_days)
                await conn.execute(
                    "UPDATE users SET subscription_until = $1 WHERE id = $2",
                    subscription_until,
                    user_id,
                )
            else:
                credits_delta = max(0, int(product.get("credits") or 0))
                balance_after = balance_after + credits_delta
                await conn.execute(
                    "UPDATE users SET paid_readings_balance = $1 WHERE id = $2",
                    int(balance_after),
                    user_id,
                )

            await conn.execute(
                """
                INSERT INTO payment_transactions (
                    user_id,
                    invoice_payload,
                    product_code,
                    kind,
                    amount,
                    currency,
                    credits_delta,
                    subscription_days,
                    telegram_payment_charge_id,
                    provider_payment_charge_id
                ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
                """,
                int(user_id),
                str(payload or ""),
                str(product.get("code") or ""),
                kind,
                int(total_amount or 0),
                str(currency or "RUB"),
                int(credits_delta),
                int(sub_days),
                str(tg_charge_id or "") or None,
                str(provider_charge_id or "") or None,
            )

            return {
                "applied": True,
                "duplicate": False,
                "kind": kind,
                "credits_added": int(credits_delta),
                "balance": int(balance_after),
                "subscription_until": subscription_until,
                "subscription_days": int(sub_days),
            }
    except asyncpg.UniqueViolationError:
        return {"applied": False, "duplicate": True}
    except Exception as exc:
        logger.exception("Failed to persist payment effect for tg_user=%s: %s", tg_user_id, exc)
        return {"applied": False, "duplicate": False, "error": str(exc)}
    finally:
        if conn is not None:
            try:
                await conn.close()
            except Exception:
                pass


async def _safe_answer_query(query) -> None:
    try:
        await query.answer()
    except BadRequest as exc:
        if "query is too old" in str(exc).lower() or "query id is invalid" in str(exc).lower():
            logger.warning("Skipping stale callback answer: %s", exc)
        else:
            raise


async def _send_menu(
    chat_id: int,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    message=None,
    include_app_link: bool = False,
    mode: str = "menu",
) -> None:
    mode_l = str(mode or "menu").strip().lower()
    if mode_l in {"menu", "buy_credits", "credits", "buy", "country_menu"}:
        text = _format_country_list()
        markup = _country_keyboard()
    elif mode_l in {"click", "uz", "uzbekistan", "click_card", "card_click", "clickcard"}:
        text = _format_plan_list("uz")
        markup = _plans_keyboard("uz")
    elif mode_l in {"card", "cards", "sberpay", "card_sberpay", "ru", "russia"}:
        text = _format_plan_list("ru")
        markup = _plans_keyboard("ru")
    elif mode_l in {"kz", "by", "kg", "other"}:
        text = _format_plan_list(mode_l)
        markup = _plans_keyboard(mode_l)
    else:
        text = _format_country_list()
        markup = _country_keyboard()

    if message:
        await message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)
    else:
        await context.bot.send_message(chat_id=chat_id, text=text, reply_markup=markup, parse_mode=ParseMode.HTML)

        if include_app_link:
            app_btn = _build_app_button()
            if app_btn:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="Открой приложение AI Tarot в Telegram, чтобы привязать профиль и видеть свои покупки.",
                    reply_markup=InlineKeyboardMarkup([[app_btn]]),
                )
            else:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text="⚠️ Не задан TELEGRAM_APP_URL — кнопка открытия мини-приложения не показана.",
                )


# =============================== HANDLERS ===============================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return

    logger.info("start: tg_user=%s bot_version=%s", getattr(update.effective_user, "id", None), BOT_VERSION)
    mode = str((context.args or [""])[0] or "").strip().lower()
    await _ensure_bot_ui(context)

    if mode in {"menu", "buy", "buy_credits", "credits"}:
        await _send_menu(update.effective_chat.id, context, include_app_link=False, mode="menu")
        return
    if mode in {"card", "cards", "sberpay", "card_sberpay", "click", "uz", "uzbekistan", "click_card", "card_click", "clickcard"}:
        await _send_menu(update.effective_chat.id, context, include_app_link=False, mode=mode)
        return
    if mode in {"support", "help_support"}:
        support_uid = int(getattr(update.effective_user, "id", 0) or 0)
        SUPPORT_PENDING_USERS.add(support_uid)
        SUPPORT_PENDING_TICKET_BY_USER.pop(support_uid, None)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "💬 Поддержка AI Taro\n\n"
                "Напишите одним сообщением, с чем нужна помощь. "
                "Мы отправим это в поддержку.\n\n"
                "Для отмены напишите: отмена"
            ),
        )
        return
    if mode in {"sub_manage", "manage_sub", "subscription_manage"}:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=_subscription_manage_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=_subscription_manage_keyboard(),
        )
        return
    if mode in {"sub_cancel", "cancel_sub", "unsubscribe"}:
        support_uid = int(getattr(update.effective_user, "id", 0) or 0)
        SUPPORT_PENDING_USERS.add(support_uid)
        SUPPORT_PENDING_TICKET_BY_USER.pop(support_uid, None)
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=_subscription_cancel_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=_subscription_cancel_keyboard(),
        )
        return
    if mode in {"terms", "agreement", "user_agreement"}:
        await _send_legal_document(chat_id=update.effective_chat.id, context=context, kind="terms")
        return
    if mode in {"privacy", "policy"}:
        await _send_legal_document(chat_id=update.effective_chat.id, context=context, kind="privacy")
        return
    if mode in {"forgetme", "delete_me", "delete_data"}:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=(
                "🗑 <b>Удаление персональных данных</b>\n\n"
                "Это действие удалит ваш профиль, историю раскладов, персональную память,\n"
                "историю обращений в поддержку и связанные платежные записи в AI Taro.\n\n"
                "Действие необратимо. Продолжить?"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_forgetme_confirm_keyboard(),
        )
        return

    await _send_start_panel(update.effective_chat.id, context)


async def menu_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await _ensure_bot_ui(context)
    await _send_menu(update.effective_chat.id, context, include_app_link=False, mode="menu")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await _ensure_bot_ui(context)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=_howto_text(),
        parse_mode=ParseMode.HTML,
        reply_markup=_welcome_keyboard(),
    )


async def support_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await _ensure_bot_ui(context)
    user_id = int(getattr(update.effective_user, "id", 0) or 0)
    if user_id > 0:
        SUPPORT_PENDING_USERS.add(user_id)
        SUPPORT_PENDING_TICKET_BY_USER.pop(user_id, None)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "💬 Поддержка AI Taro\n\n"
            "Напишите одним сообщением, с чем нужна помощь.\n"
            "Мы передадим запрос в поддержку.\n\n"
            "Для отмены напишите: отмена"
        ),
    )


async def terms_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await _ensure_bot_ui(context)
    await _send_legal_document(chat_id=update.effective_chat.id, context=context, kind="terms")


async def privacy_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await _ensure_bot_ui(context)
    await _send_legal_document(chat_id=update.effective_chat.id, context=context, kind="privacy")


async def forgetme_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    await _ensure_bot_ui(context)
    await context.bot.send_message(
        chat_id=update.effective_chat.id,
        text=(
            "🗑 <b>Удаление персональных данных</b>\n\n"
            "Это действие удалит ваш профиль, историю раскладов, персональную память,\n"
            "историю обращений в поддержку и связанные платежные записи в AI Taro.\n\n"
            "Действие необратимо. Продолжить?"
        ),
        parse_mode=ParseMode.HTML,
        reply_markup=_forgetme_confirm_keyboard(),
    )


async def reply_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat:
        return
    if not _is_support_operator(update):
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Нет доступа к /reply. Добавьте ваш telegram_id в SUPPORT_ADMIN_IDS на сервере.",
        )
        return

    args = list(context.args or [])
    if len(args) < 2 or not str(args[0]).lstrip("-").isdigit():
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="Формат: /reply <telegram_id> <сообщение>",
        )
        return

    target_tg_id = int(args[0])
    text = " ".join(args[1:]).strip()
    if not text:
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Введите текст ответа после telegram_id.")
        return
    try:
        await context.bot.send_message(
            chat_id=target_tg_id,
            text=f"💬 Ответ поддержки AI Taro:\n\n{text}",
            reply_markup=_support_user_reply_markup(),
        )
        await context.bot.send_message(chat_id=update.effective_chat.id, text="Ответ отправлен пользователю.")
    except Exception as exc:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"Не удалось отправить ответ: {exc}",
        )


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await _safe_answer_query(query)
    if query.message:
        await _send_menu(query.message.chat.id, context, message=query.message, include_app_link=False, mode="menu")


async def country_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await _safe_answer_query(query)
    if not query.message:
        return

    data = str(query.data or "").strip().lower()
    if data == "country_menu":
        await query.message.edit_text(
            _format_country_list(),
            reply_markup=_country_keyboard(),
            parse_mode=ParseMode.HTML,
        )
        return

    _, _, code = data.partition(":")
    safe_country = _normalize_country_code(code)
    await query.message.edit_text(
        _format_plan_list(safe_country),
        reply_markup=_plans_keyboard(safe_country),
        parse_mode=ParseMode.HTML,
    )


async def howto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await _safe_answer_query(query)
    if query.message:
        await query.message.reply_text(
            _howto_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=_welcome_keyboard(),
        )


async def support_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await _safe_answer_query(query)
    user_id = int(getattr(query.from_user, "id", 0) or 0)
    data = str(query.data or "").strip()
    ticket_id = ""
    if data.startswith("support:"):
        ticket_id = str(data.split(":", 1)[1] or "").strip().upper()
    if user_id > 0:
        SUPPORT_PENDING_USERS.add(user_id)
        SUPPORT_PENDING_TICKET_BY_USER.pop(user_id, None)
        if ticket_id:
            SUPPORT_PENDING_TICKET_BY_USER[user_id] = ticket_id
    if query.message:
        if ticket_id:
            await query.message.reply_text(
                f"💬 Продолжим диалог по тикету {ticket_id}.\nНапишите ответ одним сообщением.\nДля отмены напишите: отмена"
            )
        else:
            await query.message.reply_text(
                "💬 Напишите одним сообщением, с чем нужна помощь.\nДля отмены напишите: отмена"
            )


async def sub_manage_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await _safe_answer_query(query)
    if query.message:
        await query.message.reply_text(
            _subscription_manage_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=_subscription_manage_keyboard(),
        )


async def sub_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await _safe_answer_query(query)
    user_id = int(getattr(query.from_user, "id", 0) or 0)
    if user_id > 0:
        SUPPORT_PENDING_USERS.add(user_id)
        SUPPORT_PENDING_TICKET_BY_USER.pop(user_id, None)
    if query.message:
        await query.message.reply_text(
            _subscription_cancel_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=_subscription_cancel_keyboard(),
        )


async def support_close_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await _safe_answer_query(query)
    user_id = int(getattr(query.from_user, "id", 0) or 0)
    data = str(query.data or "").strip()
    ticket_id = ""
    if data.startswith("support_close:"):
        ticket_id = str(data.split(":", 1)[1] or "").strip().upper()
    if user_id > 0:
        SUPPORT_PENDING_USERS.discard(user_id)
        SUPPORT_PENDING_TICKET_BY_USER.pop(user_id, None)
        if ticket_id and SUPPORT_ACTIVE_TICKET_BY_USER.get(user_id) == ticket_id:
            SUPPORT_ACTIVE_TICKET_BY_USER.pop(user_id, None)
            ticket_state = SUPPORT_TICKETS.get(ticket_id)
            if ticket_state:
                ticket_state["status"] = "closed"
                ticket_state["last_message_at"] = int(time.time())
                SUPPORT_TICKETS[ticket_id] = ticket_state
        if ticket_id:
            await _mark_support_ticket_closed(
                ticket_id=ticket_id,
                actor_telegram_id=user_id,
                actor_chat_id=int(getattr(query.message.chat, "id", 0) or 0) if query.message else 0,
            )
    if query.message:
        try:
            await query.message.edit_reply_markup(reply_markup=None)
        except Exception:
            pass
        if ticket_id:
            await query.message.reply_text(
                f"Диалог по тикету {ticket_id} закрыт.\nЕсли понадобится, нажмите «💬 Поддержка» или введите /support."
            )
        else:
            await query.message.reply_text(
                "Диалог с поддержкой закрыт.\nЕсли понадобится, нажмите «💬 Поддержка» или введите /support."
            )


async def doc_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await _safe_answer_query(query)
    kind = "privacy" if str(query.data).endswith("privacy") else "terms"
    if query.message:
        await _send_legal_document(chat_id=query.message.chat.id, context=context, kind=kind)


async def forgetme_confirm_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await _safe_answer_query(query)
    tg_user_id = int(getattr(query.from_user, "id", 0) or 0)
    result = await _forgetme_delete_user_data(telegram_user_id=tg_user_id)

    text = ""
    if result.get("ok") and result.get("deleted"):
        text = (
            "✅ Данные удалены.\n\n"
            "Если захотите вернуться, нажмите /start и заново пройдите вход."
        )
    elif result.get("ok"):
        text = "Данные не найдены. Возможно, профиль уже был удалён ранее."
    else:
        text = "Не удалось удалить данные сейчас. Попробуйте ещё раз позже или напишите в /support."

    if query.message:
        try:
            await query.message.edit_text(text)
        except Exception:
            await query.message.reply_text(text)


async def forgetme_cancel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await _safe_answer_query(query)
    if query.message:
        try:
            await query.message.edit_text("Удаление данных отменено.")
        except Exception:
            await query.message.reply_text("Удаление данных отменено.")


async def text_fallback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.text:
        return

    text = str(message.text or "").strip().lower()
    if not text:
        return

    await _ensure_bot_ui(context)

    user_id = int(getattr(message.from_user, "id", 0) or 0)
    if user_id > 0 and user_id in SUPPORT_PENDING_USERS:
        if text in {"отмена", "cancel", "/cancel"}:
            SUPPORT_PENDING_USERS.discard(user_id)
            SUPPORT_PENDING_TICKET_BY_USER.pop(user_id, None)
            await context.bot.send_message(chat_id=message.chat.id, text="Запрос в поддержку отменён.")
            return

        if text in {
            "💳 тарифы и оплата",
            "💎 подключить подписку",
            "🚀 начать",
            "🚀 начать ai taro",
            "🚀 открыть ai taro",
            "ℹ️ как начать",
            "📄 соглашение",
            "🔐 политика",
        }:
            SUPPORT_PENDING_USERS.discard(user_id)
            SUPPORT_PENDING_TICKET_BY_USER.pop(user_id, None)
        else:
            preferred_ticket_id = str(
                SUPPORT_PENDING_TICKET_BY_USER.get(user_id)
                or SUPPORT_ACTIVE_TICKET_BY_USER.get(user_id)
                or ""
            ).strip()
            if preferred_ticket_id:
                ticket_state = SUPPORT_TICKETS.get(preferred_ticket_id)
                if ticket_state and str(ticket_state.get("status") or "").lower() == "closed":
                    preferred_ticket_id = ""

            if preferred_ticket_id:
                SUPPORT_ACTIVE_TICKET_BY_USER[user_id] = preferred_ticket_id
            result = await _forward_support_to_inbox(
                context=context,
                from_user=message.from_user,
                source_chat_id=int(message.chat.id),
                message_text=message.text,
                ticket_id=preferred_ticket_id or None,
                source_message_id=int(getattr(message, "message_id", 0) or 0) or None,
            )
            SUPPORT_PENDING_USERS.discard(user_id)
            SUPPORT_PENDING_TICKET_BY_USER.pop(user_id, None)
            if bool(result.get("ok")):
                ticket_id = str(result.get("ticket_id") or "").strip()
                is_new_ticket = bool(result.get("is_new"))
                await context.bot.send_message(
                    chat_id=message.chat.id,
                    text=(
                        f"✅ Запрос отправлен в поддержку.\n"
                        f"Тикет: <code>{escape(ticket_id)}</code>\n"
                        + ("Ответ придёт сюда же в этот чат." if is_new_ticket else "Сообщение добавлено в существующий диалог.")
                    ),
                    parse_mode=ParseMode.HTML,
                )
            else:
                await context.bot.send_message(
                    chat_id=message.chat.id,
                    text=(
                        "Не удалось отправить запрос в поддержку.\n"
                        "Попробуйте ещё раз позже или напишите /support."
                    ),
                )
            return

    if text == "💎 подключить подписку" or "тариф" in text or "оплат" in text or "price" in text:
        await _send_menu(message.chat.id, context, include_app_link=False, mode="menu")
        return

    if "поддерж" in text or text in {"support", "/support", "💬 поддержка"}:
        SUPPORT_PENDING_USERS.add(user_id)
        SUPPORT_PENDING_TICKET_BY_USER.pop(user_id, None)
        await context.bot.send_message(
            chat_id=message.chat.id,
            text="💬 Напишите одним сообщением, с чем нужна помощь.\nДля отмены напишите: отмена",
        )
        return

    if "соглаш" in text or text in {"📄 соглашение", "/terms", "terms"}:
        await _send_legal_document(chat_id=message.chat.id, context=context, kind="terms")
        return

    if "политик" in text or text in {"🔐 политика", "/privacy", "privacy"}:
        await _send_legal_document(chat_id=message.chat.id, context=context, kind="privacy")
        return

    if (
        "удалить данные" in text
        or text in {"/forgetme", "forgetme", "delete me", "delete data", "🗑 удалить данные"}
    ):
        await context.bot.send_message(
            chat_id=message.chat.id,
            text=(
                "🗑 <b>Удаление персональных данных</b>\n\n"
                "Это действие удалит ваш профиль, историю раскладов, персональную память,\n"
                "историю обращений в поддержку и связанные платежные записи в AI Taro.\n\n"
                "Действие необратимо. Продолжить?"
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=_forgetme_confirm_keyboard(),
        )
        return

    if "как начать" in text or "помощ" in text or text in {"help", "start", "старт", "начать", "ℹ️ как начать"}:
        await context.bot.send_message(
            chat_id=message.chat.id,
            text=_howto_text(),
            parse_mode=ParseMode.HTML,
            reply_markup=_welcome_keyboard(),
        )
        return

    if (
        "открыть" in text
        or "прилож" in text
        or text in {"🚀 открыть ai taro", "🚀 начать", "🚀 начать ai taro"}
    ):
        await context.bot.send_message(
            chat_id=message.chat.id,
            text=START_ONBOARDING_TEXT,
            parse_mode=ParseMode.HTML,
            reply_markup=_welcome_keyboard(),
        )
        return

    await context.bot.send_message(
        chat_id=message.chat.id,
        text="Нажмите «🚀 Начать», чтобы сразу перейти в мини‑приложение.",
        reply_markup=_welcome_keyboard(),
    )


async def plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await _safe_answer_query(query)

    country_code, plan_key = _parse_country_plan_callback(query.data, "plan")
    if plan_key not in _available_plan_keys_for_country(country_code):
        # Backward compatibility fallback for older buttons without country.
        if plan_key in _available_plan_keys():
            country_code = DEFAULT_COUNTRY_CODE
        else:
            if query.message:
                await query.message.reply_text("Этот план временно недоступен. Выберите другой.")
            return

    if plan_key not in _available_plan_keys():
        if query.message:
            await query.message.reply_text("Этот план временно недоступен. Выберите другой.")
        return

    if query.message:
        await query.message.edit_text(
            _format_methods_text(plan_key, country_code),
            reply_markup=_method_keyboard(plan_key, country_code),
            parse_mode=ParseMode.HTML,
        )


async def sbp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await _safe_answer_query(query)

    country_code, plan_key = _parse_country_plan_callback(query.data, "sbp")
    country_code = _normalize_country_code(country_code)
    card_product = _card_product_for_country(plan_key, country_code)
    if not card_product:
        if query.message:
            await query.message.reply_text("СБП для этого плана временно недоступен.")
        return

    tg_user_id = int(getattr(query.from_user, "id", 0) or 0)
    if tg_user_id <= 0:
        if query.message:
            await query.message.reply_text("Не удалось определить профиль Telegram. Попробуйте ещё раз.")
        return

    payment_link = _build_sbp_payment_link(tg_user_id=tg_user_id, plan_key=plan_key)
    if not payment_link:
        if query.message:
            await query.message.reply_text("СБП временно недоступен. Попробуйте позже.")
        return

    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🟢 Открыть оплату СБП", url=payment_link)],
        [InlineKeyboardButton("⬅️ Назад к способам оплаты", callback_data=f"plan:{country_code}:{plan_key}")],
    ]
    text = (
        f"<b>СБП — {PLAN_TITLES.get(plan_key, plan_key)}</b>\n"
        f"Сумма: {_format_amount_label(card_product)}\n\n"
        "Нажмите кнопку ниже — откроется ссылка на оплату через СБП."
    )

    if query.message:
        await query.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def sbp_auto_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await _safe_answer_query(query)

    country_code, plan_key = _parse_country_plan_callback(query.data, "sbp_auto")
    country_code = _normalize_country_code(country_code)
    card_product = _card_product_for_country(plan_key, country_code)
    if not card_product:
        if query.message:
            await query.message.reply_text("СБП автоплатёж для этого плана временно недоступен.")
        return
    if not SBP_AUTOPAY_ENABLED or plan_key != SBP_AUTOPAY_PLAN_KEY:
        if query.message:
            await query.message.reply_text("СБП автоплатёж сейчас доступен только для ежемесячного плана.")
        return

    tg_user_id = int(getattr(query.from_user, "id", 0) or 0)
    if tg_user_id <= 0:
        if query.message:
            await query.message.reply_text("Не удалось определить профиль Telegram. Попробуйте ещё раз.")
        return

    payment_link = _build_sbp_autopay_link(tg_user_id=tg_user_id, plan_key=plan_key)
    if not payment_link:
        if query.message:
            await query.message.reply_text("СБП автоплатёж временно недоступен. Попробуйте позже.")
        return

    rows: List[List[InlineKeyboardButton]] = [
        [InlineKeyboardButton("🔁 Подключить СБП автоплатёж", url=payment_link)],
        [InlineKeyboardButton("⬅️ Назад к способам оплаты", callback_data=f"plan:{country_code}:{plan_key}")],
    ]
    text = (
        f"<b>СБП автоплатёж — {PLAN_TITLES.get(plan_key, plan_key)}</b>\n"
        f"Сумма: {_format_amount_label(card_product)} / месяц\n\n"
        "Первый платёж пройдёт сейчас. После подтверждения ЮKassa сохранит метод оплаты, "
        "и продление будет списываться автоматически раз в 30 дней."
    )

    if query.message:
        await query.message.reply_text(
            text,
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.HTML,
            disable_web_page_preview=True,
        )


async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await _safe_answer_query(query)

    parts = query.data.split(":", 1)
    if len(parts) != 2:
        return

    product_code = parts[1]
    product = _get_product(product_code)
    if not product:
        if query.message:
            await query.message.reply_text("Этот план временно недоступен. Попробуйте другой вариант.")
        return

    chat_id = query.message.chat.id if query.message else None
    if chat_id is None:
        return

    currency = str(product.get("currency") or "RUB").upper()
    provider_mode = str(product.get("provider_mode") or ("yookassa_ru" if currency == "RUB" else "yookassa_usd")).lower()
    plan_key = _plan_key_from_code(product_code)
    tg_user_id = int(getattr(query.from_user, "id", 0) or 0)

    if provider_mode == "click" and plan_key == CLICK_INTRO_PLAN_KEY and CLICK_INTRO_FIRST_PURCHASE_ONLY:
        already_used_intro = await _click_intro_plan_already_used_by_tg_user(tg_user_id)
        if already_used_intro:
            if query.message:
                await query.message.reply_text(
                    "Пробная неделя уже была использована на этом аккаунте. "
                    "Выберите 2 недели, месяц или год."
                )
            return

    if _is_card_provider_mode(provider_mode) and not PROVIDER_TOKEN:
        if query.message:
            await query.message.reply_text(
                "Оплата картой временно недоступна. Выберите другой способ оплаты."
            )
        logger.error("TELEGRAM_PROVIDER_TOKEN is not set for card provider product=%s mode=%s", product_code, provider_mode)
        return
    if provider_mode == "click" and not CLICK_PROVIDER_TOKEN:
        payment_link_app = (
            _build_click_payment_link(
                tg_user_id=tg_user_id,
                plan_key=plan_key,
                checkout_mode="app",
            )
            if tg_user_id > 0
            else None
        )
        payment_link_card = (
            _build_click_payment_link(
                tg_user_id=tg_user_id,
                plan_key=plan_key,
                checkout_mode="card",
            )
            if tg_user_id > 0
            else None
        )
        if not payment_link_app:
            if query.message:
                await query.message.reply_text(
                    "Оплата через CLICK временно недоступна. Выберите другой способ оплаты."
                )
            logger.error("CLICK payment link is not available for product=%s", product_code)
            return
        if query.message:
            rows = [
                [InlineKeyboardButton("🇺🇿 Открыть CLICK", url=payment_link_app)],
                [
                    InlineKeyboardButton(
                        "💳 Оплатить картой через CLICK",
                        url=(payment_link_card or payment_link_app),
                    )
                ],
                [InlineKeyboardButton("⬅️ Назад к способам оплаты", callback_data=f"plan:uz:{plan_key}")],
            ]
            await query.message.reply_text(
                (
                    f"<b>Оплата через CLICK</b>\n"
                    f"Тариф: {escape(str(product.get('title') or product_code))}\n"
                    f"Сумма: {_format_amount_label(product)}\n\n"
                    "Нажмите кнопку ниже и завершите оплату в приложении CLICK "
                    "или сразу откройте форму оплаты картой."
                ),
                reply_markup=InlineKeyboardMarkup(rows),
                parse_mode=ParseMode.HTML,
                disable_web_page_preview=True,
            )
        return

    # payload должен быть уникальным и <= 128 байт
    payload = f"order:{product_code}:{secrets.token_hex(8)}"

    # сохраняем в памяти "pending"
    ORDERS[payload] = {
        "status": "pending",
        "product_code": product_code,
        "amount": int(product["amount"]),
        "currency": currency,
        "tg_user_id": query.from_user.id if query.from_user else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    label = product.get("title") or product_code
    if len(label) > 32:
        label = label[:32]

    prices = [LabeledPrice(label=label, amount=int(product["amount"]))]

    invoice_kwargs: Dict[str, Any] = {
        "chat_id": chat_id,
        "title": product.get("title") or "AI Tarot",
        "description": product.get("description") or "AI Tarot",
        "payload": payload,
        "currency": currency,
        "prices": prices,
    }

    if _is_card_provider_mode(provider_mode):
        invoice_kwargs["provider_token"] = PROVIDER_TOKEN
        if YOOKASSA_REQUIRE_RECEIPT:
            invoice_kwargs["provider_data"] = _build_provider_data_for_receipt(product)
            # Для ЮKassa в LIVE-режиме обычно надежнее запрашивать email и передавать его провайдеру.
            if YOOKASSA_CONTACT_MODE == "phone":
                invoice_kwargs["need_phone_number"] = True
                invoice_kwargs["send_phone_number_to_provider"] = True
            elif YOOKASSA_CONTACT_MODE == "none":
                pass
            else:
                invoice_kwargs["need_email"] = True
                invoice_kwargs["send_email_to_provider"] = True
    elif provider_mode == "click":
        invoice_kwargs["provider_token"] = CLICK_PROVIDER_TOKEN
    else:
        logger.error("Unsupported provider_mode=%s for product=%s", provider_mode, product_code)
        if query.message:
            await query.message.reply_text("Этот способ оплаты сейчас недоступен. Выберите другой вариант.")
        ORDERS[payload]["status"] = "failed"
        ORDERS[payload]["error"] = f"unsupported_provider_mode:{provider_mode}"
        return

    try:
        await context.bot.send_invoice(**invoice_kwargs)
        logger.info(
            "Invoice sent: tg_user=%s product=%s amount=%s currency=%s provider_mode=%s receipt=%s",
            getattr(query.from_user, "id", None),
            product_code,
            int(product["amount"]),
            currency,
            provider_mode,
            "on" if YOOKASSA_REQUIRE_RECEIPT else "off",
        )
    except Exception as exc:
        logger.exception("Failed to send invoice for %s: %s", product_code, exc)
        ORDERS[payload]["status"] = "failed"
        ORDERS[payload]["error"] = str(exc)
        if query.message:
            await query.message.reply_text("Не удалось сформировать счёт. Попробуйте ещё раз позже.")


async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.pre_checkout_query
    if not query:
        return

    payload = str(query.invoice_payload or "")
    product = _product_from_payload(payload)
    if not product:
        await query.answer(ok=False, error_message="Заказ не найден. Пожалуйста, сформируйте счёт заново.")
        return

    expected_amount = int(product.get("amount") or -1)
    expected_currency = str(product.get("currency") or "RUB").upper()

    # Валидация без зависимости от in-memory ORDERS (устойчиво к рестартам/нескольким воркерам).
    if int(query.total_amount or -1) != expected_amount or str(query.currency or "").upper() != expected_currency:
        logger.warning(
            "Precheckout mismatch: tg_user=%s payload=%s amount=%s currency=%s expected_amount=%s expected_currency=%s",
            getattr(query.from_user, "id", None),
            payload,
            int(query.total_amount or -1),
            str(query.currency or ""),
            expected_amount,
            expected_currency,
        )
        await query.answer(ok=False, error_message="Данные заказа не совпадают. Сформируйте счёт заново.")
        return

    logger.info(
        "Precheckout OK: tg_user=%s payload=%s amount=%s currency=%s",
        getattr(query.from_user, "id", None),
        payload,
        int(query.total_amount or -1),
        str(query.currency or ""),
    )
    await query.answer(ok=True)


def _product_code_from_payload(payload: str) -> str:
    raw = str(payload or "").strip()
    if not raw.startswith("order:"):
        return ""
    parts = raw.split(":")
    if len(parts) < 2:
        return ""
    return str(parts[1] or "").strip()


def _product_from_payload(payload: str) -> Optional[Dict[str, Any]]:
    code = _product_code_from_payload(payload)
    if not code:
        return None
    return _get_product(code)


def _format_subscription_confirmation(
    product: Dict[str, Any],
    *,
    active_until: Optional[datetime] = None,
    days_added: int = 0,
) -> str:
    until = _to_utc(active_until)
    if until:
        expires_text = until.strftime("%d.%m.%Y")
        return (
            f"Готово! Подписка «{product.get('menu_label') or product.get('title') or product['code']}» активна. "
            f"Доступ действует до {expires_text}."
        )
    return (
        f"Готово! Подписка «{product.get('menu_label') or product.get('title') or product['code']}» активна. "
        f"Добавлено {max(0, int(days_added or 0))} дней."
    )


def _format_credits_confirmation(*, credits_added: int, balance: int) -> str:
    added = max(0, int(credits_added or 0))
    bal = max(0, int(balance or 0))
    return (
        f"Оплата прошла успешно! Начислено {added} платных раскладов, текущий баланс: {bal}. "
        f"Открой приложение или мини-апп AI Tarot и начни расклады."
    )


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.successful_payment:
        return

    payment = message.successful_payment
    payload = payment.invoice_payload
    order = ORDERS.get(payload)

    logger.info(
        "Successful payment update: tg_user=%s payload=%s amount=%s currency=%s tg_charge=%s provider_charge=%s",
        getattr(message.from_user, "id", None),
        payload,
        int(payment.total_amount or 0),
        str(payment.currency or ""),
        str(payment.telegram_payment_charge_id or ""),
        str(payment.provider_payment_charge_id or ""),
    )

    if order and order.get("status") != "paid":
        order["status"] = "paid"
        order["paid_at"] = datetime.now(timezone.utc).isoformat()
        order["telegram_payment_charge_id"] = payment.telegram_payment_charge_id
        order["provider_payment_charge_id"] = payment.provider_payment_charge_id

    product_code = ""
    if order:
        product_code = str(order.get("product_code") or "").strip()
    if not product_code:
        product_code = _product_code_from_payload(payload)

    product = _get_product(product_code)
    tg_user_id = getattr(message.from_user, "id", None) or (order or {}).get("tg_user_id")

    if not product or not tg_user_id:
        await message.reply_text("Платёж подтверждён! Открой приложение AI Tarot и начни расклады.")
        return

    applied = await _activate_purchase_for_user(
        tg_user_id=int(tg_user_id),
        payload=payload,
        product=product,
        total_amount=int(payment.total_amount or 0),
        currency=str(payment.currency or "RUB"),
        tg_charge_id=str(payment.telegram_payment_charge_id or ""),
        provider_charge_id=str(payment.provider_payment_charge_id or ""),
    )

    if applied.get("duplicate"):
        await message.reply_text("Платёж уже был обработан ранее. Доступ в приложении уже активен.")
        return

    if not applied.get("applied"):
        logger.error("Payment confirmed but DB apply failed: payload=%s result=%s", payload, applied)
        await message.reply_text(
            "Платёж подтверждён, но активация задержалась. Напишите в поддержку и укажите время платежа."
        )
        return

    if str(applied.get("kind") or product.get("kind") or "") == "subscription":
        text = _format_subscription_confirmation(
            product,
            active_until=applied.get("subscription_until"),
            days_added=int(applied.get("subscription_days") or 0),
        )
    else:
        text = _format_credits_confirmation(
            credits_added=int(applied.get("credits_added") or 0),
            balance=int(applied.get("balance") or 0),
        )

    await message.reply_text(text)


# =============================== APP FACTORY ===============================
def create_application() -> Application:
    """
    Важно: это используется, когда бот стартует из FastAPI (в фоне).
    """
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("menu", menu_command))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("support", support_command))
    application.add_handler(CommandHandler("terms", terms_command))
    application.add_handler(CommandHandler("privacy", privacy_command))
    application.add_handler(CommandHandler("forgetme", forgetme_command))
    application.add_handler(CommandHandler("reply", reply_command))
    application.add_handler(CallbackQueryHandler(buy_handler, pattern=r"^buy:"))
    application.add_handler(CallbackQueryHandler(country_callback, pattern=r"^(country_menu|country:)"))
    application.add_handler(CallbackQueryHandler(plan_callback, pattern=r"^plan:"))
    application.add_handler(CallbackQueryHandler(sbp_auto_callback, pattern=r"^sbp_auto:"))
    application.add_handler(CallbackQueryHandler(sbp_callback, pattern=r"^sbp:"))
    application.add_handler(CallbackQueryHandler(sub_manage_callback, pattern=r"^sub_manage$"))
    application.add_handler(CallbackQueryHandler(sub_cancel_callback, pattern=r"^sub_cancel$"))
    application.add_handler(CallbackQueryHandler(support_callback, pattern=r"^support(?::.+)?$"))
    application.add_handler(CallbackQueryHandler(support_close_callback, pattern=r"^support_close(?::.+)?$"))
    application.add_handler(CallbackQueryHandler(doc_callback, pattern=r"^doc:(terms|privacy)$"))
    application.add_handler(CallbackQueryHandler(forgetme_confirm_callback, pattern=r"^forgetme_confirm$"))
    application.add_handler(CallbackQueryHandler(forgetme_cancel_callback, pattern=r"^forgetme_cancel$"))
    application.add_handler(CallbackQueryHandler(howto_callback, pattern=r"^howto$"))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu"))
    application.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_fallback_handler))

    return application


# =============================== STANDALONE RUN ===============================
async def _run_standalone() -> None:
    app = create_application()
    await app.initialize()
    await app.start()
    await app.updater.start_polling()
    logger.info("Starting Telegram bot (polling)...")
    # idle ждёт сигналов, корректно работает на py3.12
    await app.updater.idle()
    await app.stop()
    await app.shutdown()


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
    # Silence low-level HTTP request-line logs to avoid leaking bot token in URLs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    asyncio.run(_run_standalone())


if __name__ == "__main__":
    main()

