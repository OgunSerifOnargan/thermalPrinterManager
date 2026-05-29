"""G2: any unexpected exception → structured JSON 500 + ERROR log line."""
from __future__ import annotations

from fastapi import APIRouter
from httpx import ASGITransport, AsyncClient

from app.api import error_handlers


async def test_unhandled_exception_returns_structured_500():
    # Build a tiny app with the handlers installed and a route that intentionally
    # raises something the existing handlers don't know about.
    from fastapi import FastAPI

    app = FastAPI()
    error_handlers.install(app)
    router = APIRouter()

    @router.get("/boom")
    async def boom():
        raise RuntimeError("kaboom")

    app.include_router(router)

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/boom")
    assert r.status_code == 500
    body = r.json()
    assert body["ok"] is False
    assert body["error_code"] == "INTERNAL_ERROR"
    assert "RuntimeError" in body["detail"]
    assert "kaboom" in body["detail"]
    assert "ts" in body


async def test_http_exception_passes_through():
    """We must NOT swallow HTTPException — FastAPI's default handler owns it."""
    from fastapi import FastAPI, HTTPException

    app = FastAPI()
    error_handlers.install(app)

    @app.get("/teapot")
    async def teapot():
        raise HTTPException(status_code=418, detail="I'm a teapot")

    transport = ASGITransport(app=app, raise_app_exceptions=False)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        r = await c.get("/teapot")
    assert r.status_code == 418
    assert r.json() == {"detail": "I'm a teapot"}
