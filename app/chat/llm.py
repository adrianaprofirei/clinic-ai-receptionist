"""LLM access behind a small interface, so the agent can be tested with a
scripted fake and the provider can be swapped (any OpenAI-compatible API works
through OPENAI_BASE_URL)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Protocol

from ..config import Settings

if TYPE_CHECKING:  # the OpenAI SDK (v3) ships its own HTTP client, httpx2
    import httpx2

log = logging.getLogger("zambet.llm")


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text, validated by the tool runner


@dataclass(frozen=True)
class LLMReply:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    tokens: int = 0


class LLMError(Exception):
    """The provider failed (network, auth, quota, malformed answer...)."""


class LLMClient(Protocol):
    def complete(self, messages: list[dict], tools: list[dict], *, tool_choice: str = "auto") -> LLMReply: ...


class OpenAIChat:
    """Chat Completions with tool calling."""

    def __init__(self, settings: Settings, *, http_client: httpx2.Client | None = None) -> None:
        from openai import OpenAI

        kwargs: dict = {
            "api_key": settings.openai_api_key,
            "timeout": settings.openai_timeout_seconds,
            "max_retries": 1,
        }
        if settings.openai_base_url:
            kwargs["base_url"] = settings.openai_base_url
        if http_client is not None:
            kwargs["http_client"] = http_client
        self._client = OpenAI(**kwargs)
        self._model = settings.openai_model
        self._max_tokens = settings.max_reply_tokens
        self._reasoning_effort = settings.openai_reasoning_effort or None
        self._temperature = settings.openai_temperature

    def complete(self, messages: list[dict], tools: list[dict], *, tool_choice: str = "auto") -> LLMReply:
        import openai

        params: dict = {
            "model": self._model,
            "messages": messages,
            "tools": tools,
            "tool_choice": tool_choice,
            "max_completion_tokens": self._max_tokens,
        }
        if self._reasoning_effort:
            params["reasoning_effort"] = self._reasoning_effort
        if self._temperature is not None:
            params["temperature"] = self._temperature
        try:
            response = self._client.chat.completions.create(**params)
        except openai.OpenAIError as exc:
            raise LLMError(f"{type(exc).__name__}: {exc}") from exc
        if not response.choices:
            raise LLMError("empty response")

        choice = response.choices[0]
        message = choice.message
        calls = [
            ToolCall(id=call.id, name=call.function.name, arguments=call.function.arguments or "{}")
            for call in (message.tool_calls or [])
            if getattr(call, "type", "function") == "function"
        ]
        tokens = response.usage.total_tokens if response.usage else 0
        return LLMReply(content=message.content, tool_calls=calls, finish_reason=choice.finish_reason, tokens=tokens)
