"""Database tables."""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import JSON, Boolean, Date, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base


class Booking(Base):
    """An appointment. `start`/`end` are clinic-local wall-clock times."""

    __tablename__ = "bookings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[str] = mapped_column(String(12), unique=True, index=True)
    service_id: Mapped[str] = mapped_column(String(32))
    start: Mapped[datetime] = mapped_column(DateTime, index=True)
    end: Mapped[datetime] = mapped_column(DateTime)
    patient_name: Mapped[str] = mapped_column(String(80))
    phone: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16), default="confirmed")  # confirmed | cancelled
    source: Mapped[str] = mapped_column(String(16))  # ai | demo | seed
    lang: Mapped[str] = mapped_column(String(2), default="ro")
    session_id: Mapped[str | None] = mapped_column(String(64), index=True, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # UTC
    cancelled_by: Mapped[str | None] = mapped_column(String(16), nullable=True)  # admin | patient

    claims: Mapped[list[SlotClaim]] = relationship(
        back_populates="booking", cascade="all, delete-orphan", passive_deletes=True
    )

    @property
    def is_active(self) -> bool:
        return self.status == "confirmed"


class SlotClaim(Base):
    """One row per 15-minute block an active booking occupies.

    The primary key on `block_start` makes double booking impossible at the
    database level, even if two requests race past the application check.
    """

    __tablename__ = "slot_claims"

    block_start: Mapped[datetime] = mapped_column(DateTime, primary_key=True)
    booking_id: Mapped[int] = mapped_column(ForeignKey("bookings.id", ondelete="CASCADE"), index=True)

    booking: Mapped[Booking] = relationship(back_populates="claims")


class ChatSession(Base):
    """One per browser (id stored in an HttpOnly cookie)."""

    __tablename__ = "chat_sessions"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, index=True)  # UTC
    last_seen_at: Mapped[datetime] = mapped_column(DateTime)  # UTC
    lang: Mapped[str] = mapped_column(String(2), default="ro")
    mode: Mapped[str] = mapped_column(String(8), default="ai")  # ai | demo
    consent_shown: Mapped[bool] = mapped_column(Boolean, default=False)
    # A validated booking proposal waiting for the visitor's explicit confirmation.
    pending: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Scripted demo flow state (chosen service, day, slot...).
    demo_state: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    # Placeholder -> real value for phone numbers hidden from the LLM.
    pii: Mapped[dict | None] = mapped_column(JSON, nullable=True)

    messages: Mapped[list[ChatMessage]] = relationship(
        back_populates="session", cascade="all, delete-orphan", passive_deletes=True
    )


class ChatMessage(Base):
    """A chat message. `visible` rows are shown in the widget; the others only
    exist so the LLM keeps its tool-calling context."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("chat_sessions.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(16))  # user | assistant | tool
    content: Mapped[str | None] = mapped_column(Text, nullable=True)  # what the visitor sees
    llm_content: Mapped[str | None] = mapped_column(Text, nullable=True)  # what the LLM sees, if different
    tool_calls: Mapped[list | None] = mapped_column(JSON, nullable=True)
    tool_call_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    ui: Mapped[list | None] = mapped_column(JSON, nullable=True)
    lang: Mapped[str | None] = mapped_column(String(2), nullable=True)
    visible: Mapped[bool] = mapped_column(Boolean, default=True)
    in_llm: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime)  # UTC

    session: Mapped[ChatSession] = relationship(back_populates="messages")


class UsageDay(Base):
    """LLM usage per clinic-local day, for the global daily caps."""

    __tablename__ = "usage_days"

    day: Mapped[date] = mapped_column(Date, primary_key=True)
    llm_requests: Mapped[int] = mapped_column(Integer, default=0)
    tokens: Mapped[int] = mapped_column(Integer, default=0)
