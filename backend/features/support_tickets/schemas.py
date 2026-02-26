from __future__ import annotations

from datetime import datetime
from pydantic import BaseModel, Field
from typing import Literal, Optional, List

TicketStatus = Literal["open", "pending_support", "pending_user", "closed"]
TicketMessageDirection = Literal["user_to_support", "support_to_user", "system"]


class SupportTicketMessageOut(BaseModel):
    id: int
    ticket_id: str
    direction: TicketMessageDirection
    text: str
    created_at: datetime


class SupportTicketOut(BaseModel):
    ticket_id: str
    status: TicketStatus
    created_at: datetime
    updated_at: datetime
    last_user_message_at: Optional[datetime] = None
    last_support_reply_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    messages_count: int = 0


class SupportTicketListOut(BaseModel):
    items: List[SupportTicketOut] = Field(default_factory=list)


class SupportTicketDebugOut(BaseModel):
    ticket: SupportTicketOut
    messages: List[SupportTicketMessageOut] = Field(default_factory=list)
