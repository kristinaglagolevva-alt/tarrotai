# telegram_bot.py
from __future__ import annotations

import asyncio
import logging
import os
import secrets
from datetime import datetime, timedelta, timezone
from typing import Dict, Any, List, Optional

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

logger = logging.getLogger("telegram_bot")

BOT_TOKEN = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
PROVIDER_TOKEN = (os.environ.get("TELEGRAM_PROVIDER_TOKEN") or "").strip()

APP_URL_RAW = (os.environ.get("TELEGRAM_APP_URL") or "").strip()  # например https://tarrotai.ru
APP_BUTTON_TEXT = os.environ.get("TELEGRAM_APP_BUTTON_TEXT") or "Открыть приложение"
BOT_VERSION = os.environ.get("TELEGRAM_BOT_VERSION") or os.environ.get("APP_VERSION") or "unknown"

# ----- Тарифы (как ты описал) -----
# amount — в копейках (RUB * 100)
PRODUCTS: List[Dict[str, Any]] = [
    {
        "code": "readings_15",
        "menu_label": "🔮 15 раскладов — 50 ₽",
        "menu_hint": "Пакет из пятнадцати раскладов: удобно для частых вопросов или подарка другу.",
        "title": "15 раскладов",
        "description": "Пакет из 15 раскладов AI Tarot",
        "amount": 50 * 100,
        "kind": "credits",
        "credits": 15,
        "priority": 10,
    },
    {
        "code": "sub_2weeks",
        "menu_label": "✨ Безлимит на 2 недели — 299 ₽",
        "menu_hint": "14 дней без ограничений: идеальный старт, чтобы успеть задать все вопросы.",
        "title": "Безлимит на 2 недели",
        "description": "Подписка AI Tarot на 14 дней",
        "amount": 299 * 100,
        "kind": "subscription",
        "days": 14,
        "priority": 20,
    },
    {
        "code": "sub_month",
        "menu_label": "🌟 Безлимит на месяц — 399 ₽",
        "menu_hint": "30 дней полного доступа: безлимитные расклады и максимальная гибкость.",
        "title": "Безлимит на месяц",
        "description": "Подписка AI Tarot на 30 дней",
        "amount": 399 * 100,
        "kind": "subscription",
        "days": 30,
        "priority": 30,
    },
]

# Простое хранилище заказов в памяти (для валидации precheckout и подтверждения оплаты)
# В проде лучше хранить в БД, но ты просил “без server.py”.
ORDERS: Dict[str, Dict[str, Any]] = {}


def _get_product(code: str) -> Optional[Dict[str, Any]]:
    for p in PRODUCTS:
        if p["code"] == code:
            return p
    return None


def _format_price_list() -> str:
    parts: List[str] = [
        "<b>Тарифы AI Tarot</b>",
        "Плати внутри Telegram и получи доступ сразу после оплаты.",
        "",
    ]
    for product in sorted(PRODUCTS, key=lambda x: x.get("priority", 0)):
        parts.append(f"<b>{product['menu_label']}</b>")
        hint = (product.get("menu_hint") or "").strip()
        if hint:
            parts.append(hint)
        parts.append("")
    parts.append("Нажми на подходящий план, чтобы получить счёт.")
    return "\n".join(parts).strip()


def _price_keyboard() -> InlineKeyboardMarkup:
    rows: List[List[InlineKeyboardButton]] = []
    for product in sorted(PRODUCTS, key=lambda x: x.get("priority", 0)):
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
) -> None:
    text = _format_price_list()
    markup = _price_keyboard()

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
    await _send_menu(update.effective_chat.id, context, include_app_link=True)


async def menu_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query:
        return
    await _safe_answer_query(query)
    if query.message:
        await _send_menu(query.message.chat.id, context, message=query.message, include_app_link=False)


async def buy_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if not query or not query.data:
        return
    await _safe_answer_query(query)

    if not PROVIDER_TOKEN:
        if query.message:
            await query.message.reply_text("Платёжный провайдер не настроен. Напишите в поддержку.")
        logger.error("TELEGRAM_PROVIDER_TOKEN is not set")
        return

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

    # payload должен быть уникальным и <= 128 байт
    payload = f"order:{product_code}:{secrets.token_hex(8)}"

    # сохраняем в памяти "pending"
    ORDERS[payload] = {
        "status": "pending",
        "product_code": product_code,
        "amount": int(product["amount"]),
        "currency": "RUB",
        "tg_user_id": query.from_user.id if query.from_user else None,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }

    label = product.get("title") or product_code
    if len(label) > 32:
        label = label[:32]

    prices = [LabeledPrice(label=label, amount=int(product["amount"]))]

    try:
        await context.bot.send_invoice(
            chat_id=chat_id,
            title=product.get("title") or "AI Tarot",
            description=product.get("description") or "AI Tarot",
            payload=payload,
            provider_token=PROVIDER_TOKEN,
            currency="RUB",
            prices=prices,
        )
        # Подсказываем пользователю
        await query.answer("Счёт отправлен, посмотрите сообщение выше.")
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

    payload = query.invoice_payload
    order = ORDERS.get(payload)

    if not order or order.get("status") != "pending":
        await query.answer(ok=False, error_message="Заказ не найден или уже обработан. Пожалуйста, оформите новый счёт.")
        return

    # Можно проверить сумму/валюту
    if query.total_amount != int(order.get("amount", -1)) or query.currency != order.get("currency", "RUB"):
        await query.answer(ok=False, error_message="Данные заказа не совпадают. Попробуйте сформировать счёт заново.")
        return

    await query.answer(ok=True)


def _format_subscription_confirmation(product: Dict[str, Any]) -> str:
    days = int(product.get("days") or 0)
    expires_at = (datetime.now(timezone.utc) + timedelta(days=days)).astimezone(timezone.utc)
    expires_text = expires_at.strftime("%d.%m.%Y")
    return (
        f"Готово! Подписка «{product.get('menu_label') or product.get('title') or product['code']}» активна. "
        f"Доступ действует до {expires_text}."
    )


def _format_credits_confirmation(product: Dict[str, Any]) -> str:
    credits = int(product.get("credits") or 0)
    return (
        f"Оплата прошла успешно! На балансе {credits} платных раскладов. "
        f"Открой приложение или мини-апп AI Tarot и начни расклады."
    )


async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.message
    if not message or not message.successful_payment:
        return

    payment = message.successful_payment
    payload = payment.invoice_payload
    order = ORDERS.get(payload)

    # Даже если order нет (редко), просто подтвердим оплату
    if not order:
        await message.reply_text("Платёж подтверждён! Открой приложение AI Tarot и начни расклады.")
        return

    if order.get("status") != "paid":
        order["status"] = "paid"
        order["paid_at"] = datetime.now(timezone.utc).isoformat()
        order["telegram_payment_charge_id"] = payment.telegram_payment_charge_id
        order["provider_payment_charge_id"] = payment.provider_payment_charge_id

    product = _get_product(order.get("product_code") or "")
    if product:
        if product.get("kind") == "subscription":
            text = _format_subscription_confirmation(product)
        elif product.get("kind") == "credits":
            text = _format_credits_confirmation(product)
        else:
            text = "Платёж подтверждён!"
    else:
        text = "Платёж подтверждён!"

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

