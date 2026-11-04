"""Templates, static files and security headers."""

from __future__ import annotations

import hashlib
from pathlib import Path

from fastapi.templating import Jinja2Templates
from starlette.staticfiles import StaticFiles
from starlette.types import ASGIApp, Message, Receive, Scope, Send

BASE_DIR = Path(__file__).resolve().parent
STATIC_DIR = BASE_DIR / "static"
TEMPLATES_DIR = BASE_DIR / "templates"

_asset_versions: dict[tuple[str, float], str] = {}


def asset(path: str) -> str:
    """URL of a static file with a content hash, so it can be cached forever."""
    file = STATIC_DIR / path
    try:
        mtime = file.stat().st_mtime
    except OSError:
        return f"/static/{path}"
    key = (path, mtime)
    if key not in _asset_versions:
        _asset_versions[key] = hashlib.sha256(file.read_bytes()).hexdigest()[:10]
    return f"/static/{path}?v={_asset_versions[key]}"


templates = Jinja2Templates(directory=TEMPLATES_DIR)
templates.env.globals["asset"] = asset


class CachedStaticFiles(StaticFiles):
    """Versioned asset URLs (?v=hash) are immutable."""

    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        if response.status_code == 200 and b"v=" in scope.get("query_string", b""):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response


CSP = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self'",
        "font-src 'self'",
        "connect-src 'self'",
        "frame-src https://www.openstreetmap.org",
        "frame-ancestors 'none'",
        "base-uri 'self'",
        "form-action 'self'",
        "object-src 'none'",
    ]
)

SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "X-Frame-Options": "DENY",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=(), payment=()",
}


class SecurityHeadersMiddleware:
    """Plain ASGI middleware (safe for streaming and SSE responses)."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app
        self.headers = [(k.lower().encode(), v.encode()) for k, v in SECURITY_HEADERS.items()]

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                present = {name for name, _ in headers}
                headers.extend((k, v) for k, v in self.headers if k not in present)
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)
