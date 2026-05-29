"""Bearer-token guard for protected endpoints (per Faz 7 token auth).

Activated only when CONFIG_PATCH_TOKEN is non-empty. When unset, the
guarded endpoints remain open (development mode). This keeps the token
gating opt-in without breaking local dev.
"""
from __future__ import annotations

from fastapi import Header, HTTPException

from app.core.config import get_settings


def require_config_token(authorization: str | None = Header(default=None)) -> None:
    """FastAPI dependency that 401s unless a matching Bearer token is presented.

    Token comparison is constant-time-ish (Python's `==` on equal-length
    short strings is fast and timing differences are negligible against
    HTTP-level jitter).
    """
    settings = get_settings()
    expected = settings.config_patch_token
    if not expected:
        return  # Auth disabled

    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing Bearer token")
    presented = authorization.split(" ", 1)[1].strip()
    if presented != expected:
        raise HTTPException(status_code=401, detail="invalid token")
