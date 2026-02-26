from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from models import SupportTicket, SupportTicketMessage


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


async def get_ticket_by_public_id(db: AsyncSession, ticket_id: str) -> Optional[SupportTicket]:
    q: Select[tuple[SupportTicket]] = select(SupportTicket).where(SupportTicket.ticket_id == str(ticket_id))
    res = await db.execute(q)
    return res.scalar_one_or_none()


async def get_recent_user_ticket(
    db: AsyncSession,
    *,
    user_id: int,
    source_chat_id: Optional[int] = None,
    include_closed: bool = False,
) -> Optional[SupportTicket]:
    q = select(SupportTicket).where(SupportTicket.user_id == int(user_id))
    if source_chat_id is not None:
        q = q.where(SupportTicket.source_chat_id == int(source_chat_id))
    if not include_closed:
        q = q.where(SupportTicket.status != "closed")
    q = q.order_by(SupportTicket.updated_at.desc()).limit(1)
    res = await db.execute(q)
    return res.scalar_one_or_none()


async def create_ticket(
    db: AsyncSession,
    *,
    ticket_id: str,
    user_id: int,
    telegram_user_id: int,
    source_chat_id: int,
    support_chat_id: Optional[int] = None,
) -> SupportTicket:
    now = _now_utc()
    row = SupportTicket(
        ticket_id=str(ticket_id),
        user_id=int(user_id),
        telegram_user_id=int(telegram_user_id),
        source_chat_id=int(source_chat_id),
        support_chat_id=int(support_chat_id) if support_chat_id else None,
        status="open",
        created_at=now,
        updated_at=now,
        last_user_message_at=now,
    )
    db.add(row)
    await db.flush()
    return row


async def add_message(
    db: AsyncSession,
    *,
    ticket: SupportTicket,
    direction: str,
    text: str,
    sender_telegram_id: Optional[int] = None,
    telegram_chat_id: Optional[int] = None,
    telegram_message_id: Optional[int] = None,
) -> SupportTicketMessage:
    row = SupportTicketMessage(
        ticket_pk=int(ticket.id),
        ticket_id=str(ticket.ticket_id),
        direction=str(direction),
        message_text=str(text or ""),
        sender_telegram_id=int(sender_telegram_id) if sender_telegram_id else None,
        telegram_chat_id=int(telegram_chat_id) if telegram_chat_id else None,
        telegram_message_id=int(telegram_message_id) if telegram_message_id else None,
    )
    db.add(row)
    await db.flush()
    return row


async def count_messages(db: AsyncSession, *, ticket_pk: int) -> int:
    q = await db.execute(select(func.count(SupportTicketMessage.id)).where(SupportTicketMessage.ticket_pk == int(ticket_pk)))
    return int(q.scalar() or 0)


async def list_ticket_messages(db: AsyncSession, *, ticket_pk: int, limit: int = 100) -> Sequence[SupportTicketMessage]:
    q = (
        select(SupportTicketMessage)
        .where(SupportTicketMessage.ticket_pk == int(ticket_pk))
        .order_by(SupportTicketMessage.created_at.asc())
        .limit(max(1, min(int(limit), 500)))
    )
    res = await db.execute(q)
    return list(res.scalars().all())


async def list_user_tickets(db: AsyncSession, *, user_id: int, limit: int = 30) -> Sequence[SupportTicket]:
    q = (
        select(SupportTicket)
        .where(SupportTicket.user_id == int(user_id))
        .order_by(SupportTicket.updated_at.desc())
        .limit(max(1, min(int(limit), 200)))
    )
    res = await db.execute(q)
    return list(res.scalars().all())


async def list_support_tickets(
    db: AsyncSession,
    *,
    statuses: Sequence[str] | None = None,
    limit: int = 50,
) -> Sequence[SupportTicket]:
    q = select(SupportTicket)
    if statuses:
        normalized = [str(s).strip().lower() for s in statuses if str(s).strip()]
        if normalized:
            q = q.where(SupportTicket.status.in_(normalized))
    q = q.order_by(SupportTicket.updated_at.desc()).limit(max(1, min(int(limit), 300)))
    res = await db.execute(q)
    return list(res.scalars().all())
