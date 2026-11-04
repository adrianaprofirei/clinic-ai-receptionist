"""Chat sessions and messages in SQLite (the per-browser memory)."""

from __future__ import annotations

import secrets
from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import ChatMessage, ChatSession

SESSION_COOKIE = "zb_chat"


def new_session_id() -> str:
    return secrets.token_urlsafe(24)


def get_session(db: Session, session_id: str | None, *, created_after: datetime) -> ChatSession | None:
    """Return the session unless it is unknown or older than the retention period."""
    if not session_id or len(session_id) > 64:
        return None
    session = db.get(ChatSession, session_id)
    if session is None or session.created_at < created_after:
        return None
    return session


def create_session(db: Session, *, now_utc: datetime, mode: str, lang: str = "ro") -> ChatSession:
    session = ChatSession(
        id=new_session_id(), created_at=now_utc, last_seen_at=now_utc, lang=lang, mode=mode, consent_shown=False
    )
    db.add(session)
    db.flush()
    return session


def visible_messages(db: Session, session_id: str) -> list[ChatMessage]:
    return list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.visible.is_(True))
            .order_by(ChatMessage.id)
        )
    )


def llm_rows(db: Session, session_id: str) -> list[ChatMessage]:
    return list(
        db.scalars(
            select(ChatMessage)
            .where(ChatMessage.session_id == session_id, ChatMessage.in_llm.is_(True))
            .order_by(ChatMessage.id)
        )
    )


def trim_history(rows: list[ChatMessage], keep: int) -> list[ChatMessage]:
    """Keep about the last `keep` user/assistant text messages.

    The window always starts on a user message so a tool result is never
    separated from the assistant message that requested it.
    """
    count, cut = 0, 0
    for index in range(len(rows) - 1, -1, -1):
        row = rows[index]
        if row.role in ("user", "assistant") and not row.tool_calls:
            count += 1
        if count > keep:
            cut = index + 1
            break
    window = rows[cut:]
    while window and window[0].role != "user":
        window = window[1:]
    return window


def message_payload(msg: ChatMessage, local_time: datetime) -> dict:
    return {
        "id": msg.id,
        "role": msg.role,
        "text": msg.content,
        "ui": msg.ui or [],
        "lang": msg.lang,
        "at": local_time.replace(microsecond=0).isoformat(),
    }


def expiry(created_at: datetime, retention_hours: int) -> datetime:
    return created_at + timedelta(hours=retention_hours)
