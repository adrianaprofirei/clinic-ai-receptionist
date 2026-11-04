"""Admin authentication: one shared password (ADMIN_PASSWORD), a signed cookie.

The cookie is `<expiry>.<hmac>`; the HMAC covers a hash of the current
password, so changing ADMIN_PASSWORD logs everyone out. It is HttpOnly and
SameSite=Strict, and state-changing requests must come from the same origin.
"""

from __future__ import annotations

import hashlib
import hmac
from urllib.parse import urlsplit

from fastapi import HTTPException, Request

from .config import Settings

ADMIN_COOKIE = "zb_admin"
SESSION_HOURS = 12


def _digest(value: str) -> bytes:
    return hashlib.sha256(value.encode()).digest()


def check_password(settings: Settings, candidate: str) -> bool:
    """Constant-time comparison (hashing first hides the length too)."""
    return bool(settings.admin_password) and hmac.compare_digest(_digest(candidate), _digest(settings.admin_password))


def _signature(settings: Settings, expires: int) -> str:
    message = f"admin|{expires}|{_digest(settings.admin_password).hex()}".encode()
    return hmac.new(settings.secret_key.encode(), message, hashlib.sha256).hexdigest()


def make_token(settings: Settings, now_ts: float) -> str:
    expires = int(now_ts) + SESSION_HOURS * 3600
    return f"{expires}.{_signature(settings, expires)}"


def token_valid(settings: Settings, token: str | None, now_ts: float) -> bool:
    if not token or "." not in token:
        return False
    expires_text, signature = token.split(".", 1)
    try:
        expires = int(expires_text)
    except ValueError:
        return False
    return expires > now_ts and hmac.compare_digest(signature, _signature(settings, expires))


def is_admin(request: Request) -> bool:
    settings: Settings = request.app.state.settings
    now_ts = request.app.state.clock.now_utc().timestamp()
    return token_valid(settings, request.cookies.get(ADMIN_COOKIE), now_ts)


def require_admin(request: Request) -> None:
    if not is_admin(request):
        raise HTTPException(status_code=401, detail="Autentificare necesară.")


def same_origin(request: Request) -> bool:
    """Browsers send Origin on POST; it must match the host we are served on."""
    origin = request.headers.get("origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc == request.url.netloc


def require_same_origin(request: Request) -> None:
    if not same_origin(request):
        raise HTTPException(status_code=403, detail="Cerere respinsă.")


def cookie_secure(request: Request) -> bool:
    setting = request.app.state.settings.cookie_secure
    return {"true": True, "false": False}.get(setting, request.url.scheme == "https")
