"""Per-request state for one chat turn."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy.orm import Session

from ..clock import Clock
from ..config import Settings
from ..events import EventBus
from ..models import ChatMessage, ChatSession
from .store import message_payload


@dataclass
class ChatContext:
    db: Session
    session: ChatSession
    settings: Settings
    clock: Clock
    bus: EventBus
    emit: Callable[[dict], None]
    now: datetime  # clinic-local
    now_utc: datetime
    ai_available: bool = False
    turn_ui: list[dict] = field(default_factory=list)  # cards produced during this turn

    @property
    def lang(self) -> str:
        return self.session.lang

    @property
    def mode(self) -> str:
        return self.session.mode

    def payload(self, msg: ChatMessage) -> dict:
        return message_payload(msg, self.clock.utc_naive_to_local(msg.created_at))

    def add(
        self,
        role: str,
        text: str | None = None,
        ui: list[dict] | None = None,
        *,
        llm_content: str | None = None,
        visible: bool = True,
        in_llm: bool = True,
        lang: str | None = None,
        tool_calls: list | None = None,
        tool_call_id: str | None = None,
    ) -> ChatMessage:
        msg = ChatMessage(
            session_id=self.session.id,
            role=role,
            content=text,
            llm_content=llm_content,
            ui=list(ui) if ui else None,
            lang=lang or self.lang,
            visible=visible,
            in_llm=in_llm,
            tool_calls=tool_calls,
            tool_call_id=tool_call_id,
            created_at=self.now_utc,
        )
        self.db.add(msg)
        self.db.flush()
        if visible:
            self.emit({"type": "message" if role == "assistant" else "user", "message": self.payload(msg)})
        return msg

    def say(self, text: str | None = None, ui: list[dict] | None = None, **kwargs) -> ChatMessage:
        if ui:
            self.turn_ui.extend(ui)
        return self.add("assistant", text, ui, **kwargs)

    def user(self, text: str, **kwargs) -> ChatMessage:
        return self.add("user", text, **kwargs)

    def update_ui(self, msg: ChatMessage, ui: list[dict]) -> None:
        msg.ui = list(ui)  # new object so SQLAlchemy sees the change
        self.db.flush()
        if msg.visible:
            self.emit({"type": "update", "message": self.payload(msg)})

    def status(self, text: str) -> None:
        self.emit({"type": "status", "text": text})

    def set_demo_state(self, **changes: object) -> dict:
        state = {**(self.session.demo_state or {}), **changes}
        self.session.demo_state = state
        return state

    @property
    def demo_state(self) -> dict:
        return self.session.demo_state or {}
