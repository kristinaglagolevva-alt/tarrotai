from __future__ import annotations
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    BigInteger,
    String,
    DateTime,
    Boolean,
    func,
    ForeignKey,
    Integer,
    JSON,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from db import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)

    username: Mapped[Optional[str]] = mapped_column(String(64))
    first_name: Mapped[Optional[str]] = mapped_column(String(128))
    last_name: Mapped[Optional[str]] = mapped_column(String(128))
    photo_url: Mapped[Optional[str]] = mapped_column(String(512))
    subscription_until: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    paid_readings_balance: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    memory_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    retention_nudges_opt_in: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    retention_nudge_hour_local: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    retention_nudge_tz: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    card_days: Mapped[List["CardOfDay"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    readings: Mapped[List["Reading"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    payment_transactions: Mapped[List["PaymentTransaction"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    sbp_orders: Mapped[List["SbpOrder"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    sbp_autopay_subscriptions: Mapped[List["SbpAutopaySubscription"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    support_tickets: Mapped[List["SupportTicket"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    memory_events: Mapped[List["UserMemoryEvent"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )

    memory_profile: Mapped[Optional["UserMemoryProfile"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
        uselist=False,
    )

    retention_nudge_logs: Mapped[List["RetentionNudgeLog"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class CardOfDay(Base):
    __tablename__ = "card_of_day"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    # YYYY-MM-DD
    day_key: Mapped[str] = mapped_column(String(10), index=True)

    topic: Mapped[str] = mapped_column(String(32), default="relations")
    question: Mapped[str] = mapped_column(String(1024), default="")

    card_index: Mapped[int] = mapped_column(Integer)
    card_name: Mapped[str] = mapped_column(String(128), default="")
    description: Mapped[str] = mapped_column(String(4096), default="")

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="card_days")


class Reading(Base):
    """
    История раскладов (не "карта дня").
    cards хранится как список объектов:
      [
        {
          "position": "past",
          "title": "Прошлое",
          "card_index": 12,
          "card_name": "Повешенный",
          "is_reversed": false,
          "meaning": "..."
        },
        ...
      ]
    """
    __tablename__ = "readings"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    spread_type: Mapped[str] = mapped_column(String(32), index=True)  # ppf | three_cards | decision | custom
    topic: Mapped[str] = mapped_column(String(32), default="relations")
    question: Mapped[str] = mapped_column(String(1024), default="")

    cards: Mapped[List[Dict[str, Any]]] = mapped_column(JSON)
    description: Mapped[str] = mapped_column(Text, default="")

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="readings")


class PaymentTransaction(Base):
    __tablename__ = "payment_transactions"
    __table_args__ = (
        UniqueConstraint("telegram_payment_charge_id", name="uq_payment_transactions_tg_charge"),
        UniqueConstraint("provider_payment_charge_id", name="uq_payment_transactions_provider_charge"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    invoice_payload: Mapped[str] = mapped_column(String(128), default="", index=True)
    product_code: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(32), default="credits")

    amount: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    credits_delta: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    subscription_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")

    telegram_payment_charge_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    provider_payment_charge_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    refunded_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="payment_transactions")


class SbpOrder(Base):
    __tablename__ = "sbp_orders"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_sbp_orders_order_id"),
        UniqueConstraint("yookassa_payment_id", name="uq_sbp_orders_payment_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    order_id: Mapped[str] = mapped_column(String(64), index=True)
    plan_code: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String(8), default="RUB")

    status: Mapped[str] = mapped_column(String(32), default="pending", server_default="pending", index=True)
    yookassa_payment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    confirmation_url: Mapped[Optional[str]] = mapped_column(String(2048), nullable=True)
    idempotence_key: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    activation_applied: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    success_notified: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    paid_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)

    provider_payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    provider_notification: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="sbp_orders")


class SbpAutopaySubscription(Base):
    __tablename__ = "sbp_autopay_subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_sbp_autopay_user_id"),
        UniqueConstraint("payment_method_id", name="uq_sbp_autopay_payment_method"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    plan_code: Mapped[str] = mapped_column(String(64), default="sub_month", index=True)
    amount: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    currency: Mapped[str] = mapped_column(String(8), default="RUB")
    interval_days: Mapped[int] = mapped_column(Integer, default=30, server_default="30")

    payment_method_id: Mapped[str] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), default="active", server_default="active", index=True)

    next_charge_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), index=True)
    last_charged_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    last_payment_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    fail_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="sbp_autopay_subscriptions")


class SupportTicket(Base):
    __tablename__ = "support_tickets"
    __table_args__ = (
        UniqueConstraint("ticket_id", name="uq_support_tickets_ticket_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_id: Mapped[str] = mapped_column(String(64), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    source_chat_id: Mapped[int] = mapped_column(BigInteger, index=True)
    support_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    support_inbox_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    status: Mapped[str] = mapped_column(String(32), default="open", server_default="open", index=True)
    last_user_message_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    last_support_reply_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)
    closed_at: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True, index=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="support_tickets")
    messages: Mapped[List["SupportTicketMessage"]] = relationship(
        back_populates="ticket",
        cascade="all, delete-orphan",
    )


class SupportTicketMessage(Base):
    __tablename__ = "support_ticket_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    ticket_pk: Mapped[int] = mapped_column(ForeignKey("support_tickets.id", ondelete="CASCADE"), index=True)
    ticket_id: Mapped[str] = mapped_column(String(64), index=True)
    direction: Mapped[str] = mapped_column(String(32), default="user_to_support")
    message_text: Mapped[str] = mapped_column(Text, default="")
    sender_telegram_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True, index=True)
    telegram_chat_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    telegram_message_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    ticket: Mapped["SupportTicket"] = relationship(back_populates="messages")


class UserMemoryEvent(Base):
    __tablename__ = "user_memory_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    source_kind: Mapped[str] = mapped_column(String(32), index=True, default="reading")
    source_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)
    event_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)

    topic: Mapped[str] = mapped_column(String(32), default="other", index=True)
    spread_type: Mapped[str] = mapped_column(String(32), default="", index=True)
    question: Mapped[str] = mapped_column(String(1024), default="")
    cards: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    primary_card: Mapped[str] = mapped_column(String(128), default="", index=True)
    primary_card_reversed: Mapped[bool] = mapped_column(Boolean, default=False, server_default="false")
    sentiment_label: Mapped[str] = mapped_column(String(32), default="neutral")
    tags: Mapped[List[str]] = mapped_column(JSON, default=list)
    summary: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="memory_events")


class UserMemoryProfile(Base):
    __tablename__ = "user_memory_profiles"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_user_memory_profiles_user_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)

    recurring_topics: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    repeated_cards: Mapped[List[Dict[str, Any]]] = mapped_column(JSON, default=list)
    cycle_hints: Mapped[List[str]] = mapped_column(JSON, default=list)
    last_changes: Mapped[Dict[str, Any]] = mapped_column(JSON, default=dict)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )
    updated_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="memory_profile")


class RetentionNudgeLog(Base):
    __tablename__ = "retention_nudge_log"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    nudge_type: Mapped[str] = mapped_column(String(32), default="daily")
    scheduled_for: Mapped[Optional[DateTime]] = mapped_column(DateTime(timezone=True), nullable=True)
    sent_at: Mapped[DateTime] = mapped_column(DateTime(timezone=True), server_default=func.now(), index=True)
    status: Mapped[str] = mapped_column(String(32), default="sent", server_default="sent", index=True)
    payload: Mapped[Optional[Dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[DateTime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
    )

    user: Mapped["User"] = relationship(back_populates="retention_nudge_logs")
