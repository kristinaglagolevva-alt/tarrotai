from __future__ import annotations

from typing import Literal

TicketStatus = Literal["open", "pending_support", "pending_user", "closed"]
TicketAction = Literal[
    "user_message",
    "support_reply",
    "mark_pending_support",
    "mark_pending_user",
    "close",
    "reopen",
]


def transition_ticket_status(current: str, action: TicketAction) -> TicketStatus:
    cur = str(current or "open").strip().lower()

    if action == "user_message":
        # User wrote to support: waiting for support response.
        return "pending_support"
    if action == "support_reply":
        # Support replied: waiting for user follow-up.
        return "pending_user"
    if action == "mark_pending_support":
        return "pending_support"
    if action == "mark_pending_user":
        return "pending_user"
    if action == "close":
        return "closed"
    if action == "reopen":
        return "open" if cur == "closed" else (cur or "open")  # type: ignore[return-value]

    return "open"
