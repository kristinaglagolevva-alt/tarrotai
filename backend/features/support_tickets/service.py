from __future__ import annotations

import secrets
from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy.ext.asyncio import AsyncSession

from models import SupportTicket
from . import repository
from .state_machine import transition_ticket_status


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def build_ticket_public_id(user_telegram_id: int) -> str:
    stamp = _now_utc().strftime("%Y%m%d-%H%M%S")
    rnd = secrets.token_hex(2).upper()
    return f"SUP-{stamp}-{int(user_telegram_id)}-{rnd}"


async def get_or_create_open_ticket(
    db: AsyncSession,
    *,
    user_id: int,
    telegram_user_id: int,
    source_chat_id: int,
    preferred_ticket_id: Optional[str] = None,
    support_chat_id: Optional[int] = None,
) -> tuple[SupportTicket, bool]:
    preferred = str(preferred_ticket_id or "").strip().upper()
    if preferred:
        row = await repository.get_ticket_by_public_id(db, preferred)
        if row and int(row.user_id) == int(user_id) and str(row.status).lower() != "closed":
            return row, False

    existing = await repository.get_recent_user_ticket(db, user_id=int(user_id), source_chat_id=int(source_chat_id))
    if existing and str(existing.status).lower() != "closed":
        return existing, False

    ticket_id = build_ticket_public_id(int(telegram_user_id))
    created = await repository.create_ticket(
        db,
        ticket_id=ticket_id,
        user_id=int(user_id),
        telegram_user_id=int(telegram_user_id),
        source_chat_id=int(source_chat_id),
        support_chat_id=support_chat_id,
    )
    return created, True


async def register_user_message(
    db: AsyncSession,
    *,
    ticket: SupportTicket,
    text: str,
    sender_telegram_id: int,
    telegram_chat_id: int,
    telegram_message_id: Optional[int] = None,
) -> None:
    now = _now_utc()
    ticket.status = transition_ticket_status(str(ticket.status), "user_message")
    ticket.updated_at = now
    ticket.last_user_message_at = now
    await repository.add_message(
        db,
        ticket=ticket,
        direction="user_to_support",
        text=text,
        sender_telegram_id=sender_telegram_id,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
    )


async def register_support_reply(
    db: AsyncSession,
    *,
    ticket: SupportTicket,
    text: str,
    sender_telegram_id: int,
    telegram_chat_id: int,
    telegram_message_id: Optional[int] = None,
) -> None:
    now = _now_utc()
    ticket.status = transition_ticket_status(str(ticket.status), "support_reply")
    ticket.updated_at = now
    ticket.last_support_reply_at = now
    if ticket.closed_at is not None:
        ticket.closed_at = None
    await repository.add_message(
        db,
        ticket=ticket,
        direction="support_to_user",
        text=text,
        sender_telegram_id=sender_telegram_id,
        telegram_chat_id=telegram_chat_id,
        telegram_message_id=telegram_message_id,
    )


async def set_ticket_status(
    db: AsyncSession,
    *,
    ticket: SupportTicket,
    status: str,
    system_text: Optional[str] = None,
    actor_telegram_id: Optional[int] = None,
    actor_chat_id: Optional[int] = None,
) -> SupportTicket:
    now = _now_utc()
    target = str(status or "").strip().lower()
    if target not in {"open", "pending_support", "pending_user", "closed"}:
        target = "open"
    ticket.status = target
    ticket.updated_at = now
    if target == "closed":
        ticket.closed_at = now
    elif ticket.closed_at is not None:
        ticket.closed_at = None

    if system_text:
        await repository.add_message(
            db,
            ticket=ticket,
            direction="system",
            text=system_text,
            sender_telegram_id=actor_telegram_id,
            telegram_chat_id=actor_chat_id,
        )
    return ticket


async def list_user_tickets_with_counts(db: AsyncSession, *, user_id: int, limit: int = 30) -> list[dict]:
    rows = await repository.list_user_tickets(db, user_id=int(user_id), limit=limit)
    out: list[dict] = []
    for row in rows:
        count = await repository.count_messages(db, ticket_pk=int(row.id))
        out.append(
            {
                "ticket_id": str(row.ticket_id),
                "status": str(row.status),
                "created_at": row.created_at,
                "updated_at": row.updated_at,
                "last_user_message_at": row.last_user_message_at,
                "last_support_reply_at": row.last_support_reply_at,
                "closed_at": row.closed_at,
                "messages_count": int(count),
            }
        )
    return out


async def list_support_tickets_with_counts(
    db: AsyncSession,
    *,
    statuses: Sequence[str] | None = None,
    limit: int = 50,
) -> list[dict]:
    rows = await repository.list_support_tickets(db, statuses=statuses, limit=limit)
    out: list[dict] = []
    for row in rows:
        count = await repository.count_messages(db, ticket_pk=int(row.id))
        out.append(
            {
                "ticket_id": str(row.ticket_id),
                "status": str(row.status),
                "telegram_user_id": int(row.telegram_user_id),
                "updated_at": row.updated_at,
                "messages_count": int(count),
                "last_user_message_at": row.last_user_message_at,
                "last_support_reply_at": row.last_support_reply_at,
            }
        )
    return out
