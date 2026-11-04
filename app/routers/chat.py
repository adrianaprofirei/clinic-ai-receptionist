"""Chat endpoints.

POST /api/chat/session  -> history + mode (sets the HttpOnly session cookie)
POST /api/chat          -> {"message": "..."} or {"action": {...}}; answers with
                           NDJSON events: status, user, message, update, session, done
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from ..chat.service import ChatService
from ..chat.store import SESSION_COOKIE

router = APIRouter(prefix="/api/chat", tags=["chat"])


class ChatIn(BaseModel):
    message: str | None = Field(default=None, max_length=4000)
    action: dict[str, Any] | None = None


def client_ip(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_session_cookie(response, request: Request, session_id: str) -> None:
    settings = request.app.state.settings
    secure = {"true": True, "false": False}.get(settings.cookie_secure, request.url.scheme == "https")
    response.set_cookie(
        SESSION_COOKIE,
        session_id,
        max_age=settings.retention_hours * 3600,
        httponly=True,
        samesite="lax",
        secure=secure,
        path="/api/chat",
    )


@router.post("/session")
def open_session(request: Request) -> JSONResponse:
    service: ChatService = request.app.state.chat
    session_id, data = service.open_session(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse(data, headers={"Cache-Control": "no-store"})
    _set_session_cookie(response, request, session_id)
    return response


@router.post("")
async def post_chat(request: Request, body: ChatIn):
    service: ChatService = request.app.state.chat
    problem = service.precheck(body.message, body.action, client_ip(request))
    if problem is not None:
        status, payload = problem
        headers = {"Retry-After": str(payload["retry_after"])} if "retry_after" in payload else None
        return JSONResponse(payload, status_code=status, headers=headers)

    session_id, created = await run_in_threadpool(service.ensure_session, request.cookies.get(SESSION_COOKIE))

    loop = asyncio.get_running_loop()
    queue: asyncio.Queue[dict] = asyncio.Queue()

    def emit(event: dict) -> None:
        loop.call_soon_threadsafe(queue.put_nowait, event)

    # The turn runs to completion even if the visitor closes the tab mid-answer.
    task = asyncio.create_task(asyncio.to_thread(service.run_turn, session_id, body.message, body.action, emit))
    service.background.add(task)
    task.add_done_callback(service.background.discard)

    async def stream():
        while True:
            event = await queue.get()
            yield (json.dumps(event, ensure_ascii=False) + "\n").encode()
            if event.get("type") == "done":
                break

    response = StreamingResponse(
        stream(),
        media_type="application/x-ndjson",
        headers={"Cache-Control": "no-store", "X-Accel-Buffering": "no"},
    )
    if created:
        _set_session_cookie(response, request, session_id)
    return response
