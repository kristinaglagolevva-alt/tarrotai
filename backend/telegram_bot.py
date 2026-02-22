# telegram_bot.py
from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

import asyncpg
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, LabeledPrice, Update, WebAppInfo
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

load_dotenv()

logger = logging.getLogger("telegram_bot")

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

APP_URL_RAW = (os.environ.get("TELEGRAM_APP_URL") or "").strip()  # например https://tarrotai.ru
APP_BUTTON_TEXT = os.environ.get("TELEGRAM_APP_BUTTON_TEXT") or "Открыть приложение"
BOT_VERSION = os.environ.get("TELEGRAM_BOT_VERSION") or os.environ.get("APP_VERSION") or "unknown"
YOOKASSA_REQUIRE_RECEIPT = str(os.environ.get("YOOKASSA_REQUIRE_RECEIPT", "1")).strip().lower() not in {"0", "false", "no"}
YOOKASSA_VAT_CODE = int(os.environ.get("YOOKASSA_VAT_CODE", "1"))
YOOKASSA_CONTACT_MODE = (os.environ.get("YOOKASSA_CONTACT_MODE") or "email").strip().lower()
ENABLE_YOOKASSA_PAYMENTS = str(os.environ.get("ENABLE_YOOKASSA_PAYMENTS", "1")).strip().lower() not in {"0", "false", "no"}
ENABLE_STARS_FALLBACK = str(os.environ.get("ENABLE_STARS_FALLBACK", "1")).strip().lower() not in {"0", "false", "no"}
ENABLE_CLICK_PAYMENTS = str(os.environ.get("ENABLE_CLICK_PAYMENTS", "0")).strip().lower() not in {"0", "false", "no"}
STARS_SUB_2WEEKS = max(1, int(os.environ.get("STARS_SUB_2WEEKS", "120")))
STARS_SUB_MONTH = max(1, int(os.environ.get("STARS_SUB_MONTH", "199")))
CLICK_CURRENCY = (os.environ.get("CLICK_CURRENCY") or "UZS").strip().upper()
CLICK_SUB_2WEEKS_AMOUNT = max(0, int(os.environ.get("CLICK_SUB_2WEEKS_AMOUNT", "0")))
CLICK_SUB_MONTH_AMOUNT = max(0, int(os.environ.get("CLICK_SUB_MONTH_AMOUNT", "0")))
CLICK_SUB_2WEEKS_LABEL = (os.environ.get("CLICK_SUB_2WEEKS_LABEL") or "🇺🇿 CLICK — 2 недели").strip()
CLICK_SUB_MONTH_LABEL = (os.environ.get("CLICK_SUB_MONTH_LABEL") or "🇺🇿 CLICK — месяц").strip()

# ----- Тарифы -----
# amount — в копейках (RUB * 100)
PRODUCTS: List[Dict[str, Any]] = [
    {
        "code": "sub_2weeks",
        "menu_label": "💳 По карте или SberPay — 2 недели — 99 ₽",
        "menu_hint": "14 дней без ограничений через стандартную оплату Telegram (карта/SberPay).",
        "title": "Безлимит на 2 недели",
        "description": "Подписка AI Tarot на 14 дней",
        "amount": 99 * 100,
        "kind": "subscription",
        "days": 14,
        "priority": 10,
        "currency": "RUB",
        "provider_mode": "yookassa",
    },
    {
        "code": "sub_month",
        "menu_label": "💳 По карте или SberPay — месяц — 179 ₽",
        "menu_hint": "30 дней полного доступа через стандартную оплату Telegram (карта/SberPay).",
        "title": "Безлимит на месяц",
        "description": "Подписка AI Tarot на 30 дней",
        "amount": 179 * 100,
        "kind": "subscription",
        "days": 30,
        "priority": 20,
        "currency": "RUB",
        "provider_mode": "yookassa",
    },
    {
        "code": "sub_2weeks_xtr",
        "menu_label": f"⭐ Безлимит на 2 недели — {STARS_SUB_2WEEKS} Stars",
        "menu_hint": "Резервный способ оплаты через Telegram Stars (обычно работает на iPhone/вне РФ).",
        "title": "Безлимит на 2 недели (Stars)",
        "description": "Подписка AI Tarot на 14 дней (Telegram Stars)",
        "amount": STARS_SUB_2WEEKS,
        "kind": "subscription",
        "days": 14,
        "priority": 110,
        "currency": "XTR",
        "provider_mode": "stars",
    },
    {
        "code": "sub_month_xtr",
        "menu_label": f"⭐ Безлимит на месяц — {STARS_SUB_MONTH} Stars",
        "menu_hint": "Резервный способ оплаты через Telegram Stars (обычно работает на iPhone/вне РФ).",
        "title": "Безлимит на месяц (Stars)",
        "description": "Подписка AI Tarot на 30 дней (Telegram Stars)",
        "amount": STARS_SUB_MONTH,
        "kind": "subscription",
        "days": 30,
        "priority": 120,
        "currency": "XTR",
        "provider_mode": "stars",
    },
    {
        "code": "sub_2weeks_click",
        "menu_label": CLICK_SUB_2WEEKS_LABEL,
        "menu_hint": "Оплата через CLICK (Узбекистан).",
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
        "menu_hint": "Оплата через CLICK (Узбекистан).",
        "title": "Безлимит на месяц (CLICK)",
        "description": "Подписка AI Tarot на 30 дней через CLICK",
        "amount": CLICK_SUB_MONTH_AMOUNT,
        "kind": "subscription",
        "days": 30,
        "priority": 220,
        "currency": CLICK_CURRENCY,
        "provider_mode": "click",
    },
]

PLAN_ORDER = ["sub_2weeks", "sub_month"]
PLAN_TITLES = {
    "sub_2weeks": "2 недели",
    "sub_month": "Месяц",
}

# Простое хранилище заказов в памяти (для валидации precheckout и подтверждения оплаты)
# В проде лучше хранить в БД, но ты просил “без server.py”.
ORDERS: Dict[str, Dict[str, Any]] = {}


def _get_product(code: str) -> Optional[Dict[str, Any]]:
    for p in PRODUCTS:
        if p["code"] == code:
            return p
    return None


def _kopecks_to_rub_value(amount_kopecks: int) -> str:
    return f"{max(0, int(amount_kopecks)) / 100:.2f}"


def _build_provider_data_for_receipt(product: Dict[str, Any]) -> str:
    amount_kopecks = int(product.get("amount") or 0)
    title = str(product.get("title") or product.get("code") or "AI Tarot").strip() or "AI Tarot"
    title = title[:128]

    receipt_item: Dict[str, Any] = {
        "description": title,
        "quantity": "1.00",
        "amount": {
            "value": _kopecks_to_rub_value(amount_kopecks),
            "currency": "RUB",
        },
        "vat_code": int(YOOKASSA_VAT_CODE),
    }

    payload = {
        "receipt": {
            "items": [receipt_item],
        }
    }
    return json.dumps(payload, ensure_ascii=False)


def _products_for_mode(mode: str = "menu") -> List[Dict[str, Any]]:
    mode_l = str(mode or "menu").strip().lower()
    items = PRODUCTS
    if not ENABLE_YOOKASSA_PAYMENTS:
        items = [p for p in items if str(p.get("provider_mode") or "").lower() != "yookassa"]
    if not ENABLE_STARS_FALLBACK:
        items = [p for p in items if str(p.get("currency") or "RUB").upper() != "XTR"]
    if not ENABLE_CLICK_PAYMENTS or not CLICK_PROVIDER_TOKEN:
        items = [p for p in items if str(p.get("provider_mode") or "").lower() != "click"]
    items = [p for p in items if int(p.get("amount") or 0) > 0]

    if mode_l in {"card", "cards", "sberpay", "card_sberpay"}:
        items = [p for p in items if str(p.get("provider_mode") or "").lower() == "yookassa"]
    elif mode_l in {"click", "uz", "uzbekistan"}:
        items = [p for p in items if str(p.get("provider_mode") or "").lower() == "click"]

    return sorted(items, key=lambda x: x.get("priority", 0))


def _plan_key_from_code(code: str) -> str:
    raw = str(code or "").strip().lower()
    if raw.endswith("_click"):
        return raw[:-6]
    if raw.endswith("_xtr"):
        return raw[:-4]
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
    if currency == "XTR":
        return f"{amount} Stars"
    return f"{amount} {currency}"


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


def _plan_summary_label(plan_key: str) -> str:
    title = PLAN_TITLES.get(plan_key, plan_key)
    values: List[str] = []
    card_product = _product_for_plan_and_provider(plan_key, "yookassa")
    click_product = _product_for_plan_and_provider(plan_key, "click")
    if card_product:
        values.append(_format_amount_label(card_product))
    if click_product:
        values.append(_format_amount_label(click_product))
    if not values:
        return title
    return f"{title} — {' / '.join(values)}"


def _plans_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for plan_key in _available_plan_keys():
        rows.append([InlineKeyboardButton(_plan_summary_label(plan_key), callback_data=f"plan:{plan_key}")])
    if not rows:
        rows.append([InlineKeyboardButton("Обновить", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def _format_plan_list() -> str:
    parts: List[str] = [
        "<b>Тарифы AI Tarot</b>",
        "Шаг 1 из 2: выберите план в рублях/сумах.",
        "После выбора плана покажу способы оплаты: СБП, CLICK, по карте.",
        "",
    ]
    for plan_key in _available_plan_keys():
        parts.append(f"• <b>{_plan_summary_label(plan_key)}</b>")
    parts.append("")
    parts.append("Нажмите на подходящий план.")
    return "\n".join(parts).strip()


def _method_keyboard(plan_key: str) -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []

    card_product = _product_for_plan_and_provider(plan_key, "yookassa")
    click_product = _product_for_plan_and_provider(plan_key, "click")

    if card_product:
        rows.append(
            [
                InlineKeyboardButton(
                    f"💳 По карте или SberPay — {_format_amount_label(card_product)}",
                    callback_data=f"buy:{card_product['code']}",
                )
            ]
        )
        rows.append([InlineKeyboardButton(f"🟢 СБП — {_format_amount_label(card_product)}", callback_data=f"sbp:{plan_key}")])

    if click_product:
        rows.append(
            [
                InlineKeyboardButton(
                    f"🇺🇿 CLICK — {_format_amount_label(click_product)}",
                    callback_data=f"buy:{click_product['code']}",
                )
            ]
        )

    rows.append([InlineKeyboardButton("⬅️ Назад к планам", callback_data="menu")])
    return InlineKeyboardMarkup(rows)


def _format_methods_text(plan_key: str) -> str:
    title = PLAN_TITLES.get(plan_key, plan_key)
    return (
        f"<b>{title}</b>\n"
        f"Шаг 2 из 2: выберите способ оплаты.\n\n"
        f"• СБП\n"
        f"• CLICK\n"
        f"• По карте / SberPay"
    )


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
    elif mode_l in {"click", "uz", "uzbekistan"}:
        parts = [
            "<b>Оплата через CLICK</b>",
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


def _build_app_button() -> Optional[InlineKeyboardButton]:
    """
    Кнопка открытия Mini App.
    Если https-домен (добавлен в BotFather Web App) → web_app кнопка.
    Если t.me/startapp → обычная url кнопка.
    """
    if not APP_URL_RAW:
        return None

    url = APP_URL_RAW

    if url.startswith("https://t.me/"):
        return InlineKeyboardButton(APP_BUTTON_TEXT, url=url)

    if url.startswith("https://"):
        return InlineKeyboardButton(APP_BUTTON_TEXT, web_app=WebAppInfo(url=url))

    logger.warning("TELEGRAM_APP_URL is invalid (%s), skipping WebApp button", url)
    return None


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
    if mode_l in {"menu", "buy_credits", "credits", "buy"}:
        text = _format_plan_list()
        markup = _plans_keyboard()
    else:
        text = _format_price_list(mode_l)
        markup = _price_keyboard(mode_l)

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
    mode = str((context.args or ["menu"])[0] or "menu").strip().lower()
    await _send_menu(update.effective_chat.id, context, include_app_link=True, mode=mode)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await _safe_answer_query(query)
    if query.message:
        await _send_menu(query.message.chat.id, context, message=query.message, include_app_link=False, mode="menu")


async def plan_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await _safe_answer_query(query)

    _, plan_key = (query.data.split(":", 1) + [""])[:2]
    plan_key = str(plan_key or "").strip().lower()
    if plan_key not in _available_plan_keys():
        if query.message:
            await query.message.reply_text("Этот план временно недоступен. Выберите другой.")
        return

    if query.message:
        await query.message.edit_text(
            _format_methods_text(plan_key),
            reply_markup=_method_keyboard(plan_key),
            parse_mode=ParseMode.HTML,
        )


async def sbp_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await _safe_answer_query(query)

    _, plan_key = (query.data.split(":", 1) + [""])[:2]
    plan_key = str(plan_key or "").strip().lower()
    card_product = _product_for_plan_and_provider(plan_key, "yookassa")
    if not card_product:
        if query.message:
            await query.message.reply_text("СБП для этого плана временно недоступен.")
        return

    app_btn = _build_app_button()
    rows: List[List[InlineKeyboardButton]] = []
    if app_btn:
        rows.append([app_btn])
    rows.append([InlineKeyboardButton("⬅️ Назад к способам оплаты", callback_data=f"plan:{plan_key}")])

    text = (
        f"<b>СБП — {PLAN_TITLES.get(plan_key, plan_key)}</b>\n"
        f"Сумма: {_format_amount_label(card_product)}\n\n"
        "Оплата по СБП проходит в мини-приложении. Нажмите кнопку ниже, затем в профиле выберите оплату через СБП."
    )

    if query.message:
        await query.message.edit_text(
            text,
            reply_markup=InlineKeyboardMarkup(rows),
            parse_mode=ParseMode.HTML,
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
    provider_mode = str(product.get("provider_mode") or ("stars" if currency == "XTR" else "yookassa")).lower()
    if provider_mode == "yookassa" and not PROVIDER_TOKEN:
        if query.message:
            await query.message.reply_text(
                "Оплата картой временно недоступна. Выберите другой способ оплаты."
            )
        logger.error("TELEGRAM_PROVIDER_TOKEN is not set for yookassa product=%s", product_code)
        return
    if provider_mode == "click" and not CLICK_PROVIDER_TOKEN:
        if query.message:
            await query.message.reply_text(
                "Оплата через CLICK временно недоступна. Выберите другой способ оплаты."
            )
        logger.error("CLICK provider token is not set for product=%s", product_code)
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

    if provider_mode == "yookassa":
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
        # В PTB параметр provider_token обязателен в сигнатуре даже для Telegram Stars.
        invoice_kwargs["provider_token"] = ""

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
    application.add_handler(CommandHandler("menu", start))
    application.add_handler(CallbackQueryHandler(buy_handler, pattern=r"^buy:"))
    application.add_handler(CallbackQueryHandler(plan_callback, pattern=r"^plan:"))
    application.add_handler(CallbackQueryHandler(sbp_callback, pattern=r"^sbp:"))
    application.add_handler(CallbackQueryHandler(menu_callback, pattern=r"^menu"))
    application.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    application.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

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
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN is not set")
    asyncio.run(_run_standalone())


if __name__ == "__main__":
    main()

