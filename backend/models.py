from __future__ import annotations
from typing import Optional, List, Dict, Any

from sqlalchemy import (
    BigInteger,
    String,
    DateTime,
    func,
    ForeignKey,
    Integer,
    JSON,
    Text,
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
