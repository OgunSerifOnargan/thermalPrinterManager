"""Centralized exception → HTTP response mapping (per L3)."""
from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.core.errors import ERROR_POLICY, PrinterError
from app.services.printer_service import (
    ReprintExpiredError,
    ReprintNotEligibleError,
    ReprintNotFoundError,
)


log = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def install(app: FastAPI) -> None:
    @app.exception_handler(PrinterError)
    async def printer_error_handler(_: Request, exc: PrinterError):
        policy = ERROR_POLICY[exc.code]
        return JSONResponse(
            status_code=policy.http_status,
            content={
                "ok": False,
                "error_code": exc.code.value,
                "detail": exc.detail,
                "message_tr": policy.user_message_tr,
                "message_en": policy.user_message_en,
                "ts": _now_iso(),
            },
        )

    @app.exception_handler(ReprintNotFoundError)
    async def reprint_not_found(_: Request, exc: ReprintNotFoundError):
        return JSONResponse(
            status_code=404,
            content={"ok": False, "error_code": "NOT_FOUND",
                     "detail": str(exc), "ts": _now_iso()},
        )

    @app.exception_handler(ReprintExpiredError)
    async def reprint_expired(_: Request, exc: ReprintExpiredError):
        return JSONResponse(
            status_code=410,
            content={"ok": False, "error_code": "GONE",
                     "detail": str(exc), "ts": _now_iso()},
        )

    @app.exception_handler(ReprintNotEligibleError)
    async def reprint_not_eligible(_: Request, exc: ReprintNotEligibleError):
        return JSONResponse(
            status_code=409,
            content={"ok": False, "error_code": "CONFLICT",
                     "detail": str(exc), "ts": _now_iso()},
        )

    # G2: catch-all for anything not handled above. Without this, an unexpected
    # KeyError / AttributeError / RuntimeError surfaces as Starlette's plain
    # "Internal Server Error" page — no JSON body, nothing in the JSONL log,
    # nothing for the UI to render. We swap that for a structured response and
    # an ERROR-level log entry that includes the traceback.
    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception):
        # Let FastAPI's built-in handlers keep owning their classes — only
        # truly unrecognized exceptions reach here.
        if isinstance(exc, (HTTPException, RequestValidationError)):
            raise exc
        log.exception(
            "unhandled exception",
            extra={
                "op": "unhandled_exception",
                "path": request.url.path,
                "method": request.method,
                "exc_type": type(exc).__name__,
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "ok": False,
                "error_code": "INTERNAL_ERROR",
                "detail": f"{type(exc).__name__}: {exc}",
                "ts": _now_iso(),
            },
        )
