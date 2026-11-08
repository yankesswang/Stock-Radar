"""Request correlation and compact access logging."""

from __future__ import annotations

import contextvars
import logging
import time
from uuid import uuid4

from fastapi import Request
from fastapi.responses import JSONResponse


logger = logging.getLogger("stock_radar.http")
request_id_context: contextvars.ContextVar[str] = contextvars.ContextVar(
    "request_id", default="-"
)


def request_id() -> str:
    return request_id_context.get()


async def request_context_middleware(request: Request, call_next):
    correlation_id = request.headers.get("x-request-id", "").strip()[:128] or str(uuid4())
    token = request_id_context.set(correlation_id)
    started = time.perf_counter()
    try:
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled request failure method=%s path=%s request_id=%s",
                request.method,
                request.url.path,
                correlation_id,
            )
            response = JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "internal_error",
                        "message": "Internal server error",
                        "request_id": correlation_id,
                    }
                },
            )
        response.headers["X-Request-ID"] = correlation_id
        elapsed_ms = (time.perf_counter() - started) * 1000
        logger.info(
            "method=%s path=%s status=%s duration_ms=%.1f request_id=%s",
            request.method,
            request.url.path,
            response.status_code,
            elapsed_ms,
            correlation_id,
        )
        return response
    finally:
        request_id_context.reset(token)
