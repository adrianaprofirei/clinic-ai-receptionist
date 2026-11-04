"""Chat orchestration: sessions, request checks, and routing each turn to the
AI agent or to the scripted demo flow."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Callable
from datetime import timedelta
from typing import Any, Protocol

from sqlalchemy.orm import Session, sessionmaker

from ..clinic import CLINIC
from ..clock import Clock
from ..config import Settings
from ..events import EventBus
from ..i18n import detect_lang
from ..models import ChatSession
from ..ratelimit import KeyedLocks, SlidingWindowLimiter
from . import cards, demo, store
from .context import ChatContext
from .texts import t

log = logging.getLogger("zambet.chat")

Emit = Callable[[dict], None]


class Agent(Protocol):
    """The AI receptionist (see app/chat/agent.py)."""

    def available(self, db: Session) -> bool: ...

    def handle_text(self, ctx: ChatContext, text: str) -> bool:
        """Answer one visitor message. Returns False if the AI failed and the
        caller should fall back to the scripted demo."""
        ...


class ChatService:
    def __init__(
        self,
        *,
        settings: Settings,
        session_factory: sessionmaker[Session],
        clock: Clock,
        bus: EventBus,
        agent: Agent | None = None,
    ) -> None:
        self.settings = settings
        self.session_factory = session_factory
        self.clock = clock
        self.bus = bus
        self.agent = agent
        self.text_limiter = SlidingWindowLimiter(settings.rate_limit_messages, settings.rate_limit_window_seconds)
        # Button clicks cost nothing, but are still capped to stop scripted flooding.
        self.action_limiter = SlidingWindowLimiter(
            settings.rate_limit_messages * 6, settings.rate_limit_window_seconds
        )
        self.locks = KeyedLocks()
        self.background: set[asyncio.Task] = set()

    # --- sessions ---------------------------------------------------------------------

    def _cutoff(self):
        return self.clock.utc_naive() - timedelta(hours=self.settings.retention_hours)

    def ai_available(self, db: Session) -> bool:
        return self.agent is not None and self.agent.available(db)

    def _context(self, db: Session, session: ChatSession, emit: Emit) -> ChatContext:
        return ChatContext(
            db=db,
            session=session,
            settings=self.settings,
            clock=self.clock,
            bus=self.bus,
            emit=emit,
            now=self.clock.now_local(),
            now_utc=self.clock.utc_naive(),
            ai_available=self.ai_available(db),
        )

    def _create(self, db: Session) -> ChatSession:
        mode = "ai" if self.ai_available(db) else "demo"
        session = store.create_session(db, now_utc=self.clock.utc_naive(), mode=mode)
        demo.greet(self._context(db, session, lambda _event: None))
        return session

    def session_info(self, session: ChatSession, ai_available: bool) -> dict:
        return {
            "mode": session.mode,
            "ai_available": ai_available,
            "lang": session.lang,
            "max_chars": self.settings.max_message_chars,
            "demo_step": (session.demo_state or {}).get("step", 0),
        }

    def open_session(self, session_id: str | None) -> tuple[str, dict]:
        """Return (session id, {session, messages}), creating the session if needed."""
        with self.session_factory() as db:
            session = store.get_session(db, session_id, created_after=self._cutoff()) or self._create(db)
            ai = self.ai_available(db)
            ctx = self._context(db, session, lambda _event: None)
            messages = [ctx.payload(m) for m in store.visible_messages(db, session.id)]
            info = self.session_info(session, ai)
            session.last_seen_at = self.clock.utc_naive()
            db.commit()
            return session.id, {"session": info, "messages": messages}

    def ensure_session(self, session_id: str | None) -> tuple[str, bool]:
        with self.session_factory() as db:
            session = store.get_session(db, session_id, created_after=self._cutoff())
            if session is not None:
                return session.id, False
            session = self._create(db)
            db.commit()
            return session.id, True

    # --- request checks (before streaming) ---------------------------------------------

    def precheck(self, message: str | None, action: dict[str, Any] | None, ip: str) -> tuple[int, dict] | None:
        """Cheap checks that answer with an HTTP error instead of a stream."""
        if action is not None:
            if not isinstance(action.get("type"), str) or len(json.dumps(action, default=str)) > 2000:
                return 400, {"error": "invalid_action"}
            allowed, retry = self.action_limiter.hit(ip)
            if not allowed:
                return 429, self._rate_limited("ro", retry)
            return None

        text = (message or "").strip()
        lang = detect_lang(text)
        if not text:
            return 400, {"error": "empty", "message": t("empty", lang)}
        if len(text) > self.settings.max_message_chars:
            n = self.settings.max_message_chars
            return 413, {"error": "too_long", "message": t("too_long", lang, n=n), "max_chars": n}
        allowed, retry = self.text_limiter.hit(ip)
        if not allowed:
            return 429, self._rate_limited(lang, retry)
        return None

    @staticmethod
    def _rate_limited(lang: str, retry: int) -> dict:
        return {
            "error": "rate_limited",
            "message": t("rate_limited", lang, phone=CLINIC.phone_display),
            "phone": {"label": CLINIC.phone_display, "tel": CLINIC.phone_tel},
            "retry_after": retry,
        }

    # --- one turn ------------------------------------------------------------------------

    def run_turn(self, session_id: str, message: str | None, action: dict | None, emit: Emit) -> None:
        """Process one request. Runs in a worker thread; always ends with `done`."""
        try:
            with self.locks.hold(session_id), self.session_factory() as db:
                session = store.get_session(db, session_id, created_after=self._cutoff()) or self._create(db)
                ctx = self._context(db, session, emit)
                if action is not None:
                    self._handle_action(ctx, action)
                else:
                    self._handle_text(ctx, (message or "").strip())
                session.last_seen_at = ctx.now_utc
                db.commit()
                emit({"type": "session", "session": self.session_info(session, ctx.ai_available)})
        except Exception:
            log.exception("Chat turn failed")
            emit({"type": "error", "error": "server_error", "message": t("fallback_reply", "ro")})
        finally:
            emit({"type": "done"})

    def _handle_action(self, ctx: ChatContext, action: dict) -> None:
        demo.handle_action(ctx, action)

    def _handle_text(self, ctx: ChatContext, text: str) -> None:
        ctx.session.lang = detect_lang(text, ctx.session.lang)
        if ctx.session.mode == "ai" and ctx.ai_available and self.agent is not None:
            if self.agent.handle_text(ctx, text):
                return
            ctx.ai_available = False  # the provider failed: fall through to the demo
        elif ctx.session.mode == "ai":
            ctx.user(text, in_llm=False)
        if ctx.session.mode == "ai":
            self._switch_to_demo(ctx)
            return
        demo.handle_text(ctx, text)

    def _switch_to_demo(self, ctx: ChatContext) -> None:
        """AI unavailable (no key, budget spent or provider error): offer the scripted flow."""
        ctx.session.mode = "demo"
        label = t("start_booking", ctx.lang)
        ctx.say(
            None,
            [cards.notice(t("ai_unavailable", ctx.lang), action=cards.option(
                label, cards.send_action("start_demo", label)))],
            in_llm=False,
        )
