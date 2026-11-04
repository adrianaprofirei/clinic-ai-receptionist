"""Global daily caps on LLM usage, persisted so restarts do not reset them."""

from __future__ import annotations

from datetime import date

from sqlalchemy import update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from .clock import Clock
from .config import Settings
from .models import UsageDay


def _today(clock: Clock) -> date:
    return clock.now_local().date()


def _ensure_row(db: Session, day: date) -> None:
    if db.get(UsageDay, day) is None:
        db.add(UsageDay(day=day, llm_requests=0, tokens=0))
        try:
            db.commit()
        except IntegrityError:  # created concurrently
            db.rollback()


def budget_left(db: Session, clock: Clock, settings: Settings) -> bool:
    row = db.get(UsageDay, _today(clock))
    if row is None:
        return settings.daily_message_cap > 0 and settings.daily_token_cap > 0
    return row.llm_requests < settings.daily_message_cap and row.tokens < settings.daily_token_cap


def consume_request(db: Session, clock: Clock, settings: Settings) -> bool:
    """Atomically count one visitor message against today's cap."""
    day = _today(clock)
    _ensure_row(db, day)
    result = db.execute(
        update(UsageDay)
        .where(
            UsageDay.day == day,
            UsageDay.llm_requests < settings.daily_message_cap,
            UsageDay.tokens < settings.daily_token_cap,
        )
        .values(llm_requests=UsageDay.llm_requests + 1)
    )
    db.commit()
    return result.rowcount == 1


def add_tokens(db: Session, clock: Clock, tokens: int) -> None:
    if tokens <= 0:
        return
    day = _today(clock)
    _ensure_row(db, day)
    db.execute(update(UsageDay).where(UsageDay.day == day).values(tokens=UsageDay.tokens + tokens))
    db.commit()
