"""The AI receptionist: an OpenAI tool-calling loop around the shared booking flow.

One visitor message = at most MAX_TOOL_ROUNDS + 1 model calls. Around the loop:
prompt-injection refusal (no model call), daily caps, phone masking, output
scrubbing, and deterministic cards (consent notice, emergency call card) that
do not depend on what the model writes.
"""

from __future__ import annotations

import json
import logging
import re
import secrets

from sqlalchemy.orm import Session

from ..clinic import strip_accents
from ..clock import Clock
from ..config import Settings
from ..guardrails import looks_like_injection, mask_phones, mentions_emergency, scrub_reply, unmask
from .. import usage
from . import cards, flow, store
from .context import ChatContext
from .llm import LLMClient, LLMError
from .prompt import build_system_prompt
from .texts import t
from .tools import TOOLS, ToolRunner

log = logging.getLogger("zambet.agent")

MAX_TOOL_ROUNDS = 4
MAX_CALLS_PER_ROUND = 4
_ASKS_FOR_CONTACT = re.compile(r"\b(nume\w*|telefon\w*|name|phone)\b")


def _well_formed(messages: list[dict]) -> list[dict]:
    """Drop tool-call messages whose results are missing (and orphan results)."""
    out: list[dict] = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        if msg["role"] == "assistant" and msg.get("tool_calls"):
            ids = {c["id"] for c in msg["tool_calls"]}
            results = []
            j = i + 1
            while j < len(messages) and messages[j]["role"] == "tool":
                results.append(messages[j])
                j += 1
            if ids == {r["tool_call_id"] for r in results}:
                out.append(msg)
                out.extend(results)
            i = j
            continue
        if msg["role"] != "tool":
            out.append(msg)
        i += 1
    return out


class Agent:
    def __init__(self, settings: Settings, clock: Clock, llm: LLMClient) -> None:
        self.settings = settings
        self.clock = clock
        self.llm = llm
        self.canary = f"ref-{secrets.token_hex(4)}"

    def available(self, db: Session) -> bool:
        return usage.budget_left(db, self.clock, self.settings)

    # --- context -------------------------------------------------------------------------

    def build_messages(self, ctx: ChatContext) -> list[dict]:
        rows = store.trim_history(store.llm_rows(ctx.db, ctx.session.id), self.settings.history_messages)
        messages: list[dict] = [{"role": "system", "content": build_system_prompt(ctx.now, self.settings, self.canary)}]
        for row in rows:
            if row.role == "tool":
                messages.append({"role": "tool", "tool_call_id": row.tool_call_id, "content": row.content or ""})
            elif row.role == "assistant" and row.tool_calls:
                messages.append({"role": "assistant", "content": row.content, "tool_calls": row.tool_calls})
            else:
                content = row.llm_content or row.content
                if content:
                    messages.append({"role": row.role, "content": content})
        return [messages[0], *_well_formed(messages[1:])]

    # --- one visitor message ---------------------------------------------------------------

    def handle_text(self, ctx: ChatContext, text: str) -> bool:
        lang = ctx.lang
        if looks_like_injection(text):
            log.warning("Refused a prompt-injection attempt (session %s…)", ctx.session.id[:6])
            ctx.user(text, in_llm=False)
            ctx.say(t("refusal", lang), in_llm=False)
            return True

        pii = dict(ctx.session.pii or {})
        masked = mask_phones(text, pii)
        ctx.session.pii = pii
        ctx.user(text, llm_content=masked if masked != text else None)

        if not usage.consume_request(ctx.db, self.clock, self.settings):
            log.info("Daily AI budget reached; falling back to the scripted demo")
            return False

        runner = ToolRunner(ctx)
        messages = self.build_messages(ctx)
        ui: list[dict] = []
        pending: dict | None = None
        content: str | None = None
        try:
            for round_no in range(MAX_TOOL_ROUNDS + 1):
                choice = "auto" if round_no < MAX_TOOL_ROUNDS else "none"
                reply = self.llm.complete(messages, TOOLS, tool_choice=choice)
                usage.add_tokens(ctx.db, self.clock, reply.tokens)
                content = reply.content
                if not reply.tool_calls or choice == "none":
                    break
                calls = reply.tool_calls[:MAX_CALLS_PER_ROUND]
                serialized = [
                    {"id": c.id, "type": "function", "function": {"name": c.name, "arguments": c.arguments}}
                    for c in calls
                ]
                messages.append({"role": "assistant", "content": reply.content, "tool_calls": serialized})
                ctx.add("assistant", reply.content, visible=False, tool_calls=serialized)
                for call in calls:
                    ctx.status(runner.status_text(call.name, call.arguments))
                    result = runner.run(call.name, call.arguments)
                    payload = json.dumps(result.data, ensure_ascii=False)
                    messages.append({"role": "tool", "tool_call_id": call.id, "content": payload})
                    ctx.add("tool", payload, visible=False, tool_call_id=call.id)
                    ui.extend(result.ui)
                    pending = result.pending or pending
                content = None
        except LLMError as exc:
            log.warning("LLM call failed: %s", exc)
            if not ui:
                return False
            content = None

        self._reply(ctx, content or "", ui, pending, emergency=mentions_emergency(text))
        return True

    # --- composing the answer -----------------------------------------------------------------

    def _reply(self, ctx: ChatContext, raw: str, ui: list[dict], pending: dict | None, *, emergency: bool) -> None:
        lang = ctx.lang
        pii = ctx.session.pii or {}
        visible = scrub_reply(
            unmask(raw, pii),
            secrets=[self.canary, self.settings.admin_password, self.settings.secret_key],
        )
        if visible is None:
            log.warning("Blocked a reply that leaked a secret (session %s…)", ctx.session.id[:6])
            visible = raw = t("refusal", lang)
        if not visible:
            visible = raw = self._fallback_text(ui, lang)

        booking_cards = [c for c in ui if c["type"] == "booking"]
        slot_cards = [c for c in ui if c["type"] == "slots"][-2:]
        other_cards = [c for c in ui if c["type"] not in ("booking", "slots")]

        if booking_cards:
            ctx.say(None, booking_cards, in_llm=False)
            other_cards.append(cards.menu_reply(lang, "ai", [("qr_directions", "directions"), ("qr_thanks", "thanks")]))

        asks_contact = "?" in visible and _ASKS_FOR_CONTACT.search(strip_accents(visible))
        if not ctx.session.consent_shown and (pending or asks_contact):
            ctx.say(None, [cards.consent_card(lang)], in_llm=False)
            ctx.session.consent_shown = True

        main_ui = other_cards + slot_cards
        if emergency:
            main_ui.append(cards.emergency_card(lang, ctx.now))
        msg = ctx.say(visible, main_ui, llm_content=raw if raw != visible else None)
        if pending:
            flow.attach_summary(ctx, pending, msg)

        if emergency and not slot_cards and not booking_cards:
            options = [
                cards.option(t("qr_earliest", lang), cards.send_text(t("qr_earliest", lang))),
                cards.option(t("qr_no_thanks", lang), cards.send_text(t("qr_no_thanks", lang))),
            ]
            ctx.say(t("pain_offer", lang), [cards.quick_replies(options)])

    @staticmethod
    def _fallback_text(ui: list[dict], lang: str) -> str:
        kinds = {c["type"] for c in ui}
        if "summary" in kinds:
            return "Verifică, te rog, detaliile și confirmă:" if lang == "ro" else "Please check the details and confirm:"
        if "slots" in kinds:
            return "Uite orele libere:" if lang == "ro" else "Here are the free times:"
        return t("fallback_reply", lang)
