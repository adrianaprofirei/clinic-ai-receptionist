"""Test helpers: an app on a temp database with a frozen clock, and a chat client
that understands the NDJSON stream."""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import datetime

from fastapi.testclient import TestClient

from app.clock import FixedClock
from app.config import Settings
from app.factory import create_app

from .conftest import NOW


def make_settings(tmp_path, **overrides) -> Settings:
    base = Settings(
        database_url=f"sqlite:///{tmp_path / 'app.db'}",
        admin_password="test-password",
        secret_key="test-secret",
        maintenance_interval_minutes=0,
        seed_demo_data=False,
        log_level="warning",
    )
    return replace(base, **overrides)


def make_app(tmp_path, *, now: datetime = NOW, **overrides):
    clock = FixedClock(now)
    app = create_app(make_settings(tmp_path, **overrides), clock=clock)
    return app, clock


@dataclass
class Turn:
    status: int
    events: list[dict] = field(default_factory=list)
    error: dict | None = None

    def of(self, kind: str) -> list[dict]:
        return [e for e in self.events if e["type"] == kind]

    @property
    def messages(self) -> list[dict]:
        return [e["message"] for e in self.of("message")]

    @property
    def texts(self) -> list[str]:
        return [m["text"] for m in self.messages if m["text"]]

    def cards(self, kind: str | None = None, *, include_updates: bool = False) -> list[dict]:
        sources = self.messages + ([e["message"] for e in self.of("update")] if include_updates else [])
        return [c for m in sources for c in m["ui"] if kind is None or c["type"] == kind]

    def card(self, kind: str, **kwargs) -> dict:
        found = self.cards(kind, **kwargs)
        assert found, f"no {kind} card in {self.events}"
        return found[-1]

    @property
    def session(self) -> dict:
        return self.of("session")[-1]["session"]


class ChatClient:
    def __init__(self, client: TestClient) -> None:
        self.http = client

    def open(self) -> dict:
        response = self.http.post("/api/chat/session")
        assert response.status_code == 200
        return response.json()

    def _post(self, body: dict) -> Turn:
        response = self.http.post("/api/chat", json=body)
        if response.headers["content-type"].startswith("application/json"):
            return Turn(status=response.status_code, error=response.json())
        events = [json.loads(line) for line in response.text.splitlines() if line.strip()]
        assert events[-1]["type"] == "done"
        return Turn(status=response.status_code, events=events)

    def say(self, text: str) -> Turn:
        return self._post({"message": text})

    def act(self, kind: str, **data) -> Turn:
        return self._post({"action": {"type": kind, **data}})

    def send(self, payload: dict) -> Turn:
        """Click a button: post the `send` payload exactly as the widget does."""
        body = {"message": payload["text"]} if "text" in payload else {"action": payload["action"]}
        return self._post(body)
