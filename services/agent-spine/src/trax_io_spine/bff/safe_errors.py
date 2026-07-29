"""Redacted validation failures for advisory planning and replay routes."""

from __future__ import annotations

from fastapi import Request
from fastapi.exception_handlers import request_validation_exception_handler
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


async def safe_request_validation_handler(
    request: Request,
    exc: RequestValidationError,
):
    """Keep rejected planning/replay input out of public 422 responses.

    FastAPI's default response includes Pydantic's ``input`` value. That is
    useful for generic APIs but violates the locked advisory contract because
    a rejected request can contain tenant data, decision keys, or arbitrary
    caller-authored strings. Other BFF routes retain FastAPI's established
    validation response for backward compatibility.
    """

    path = request.url.path
    if "/planning-runs" in path:
        code = "planning_request_invalid"
        message = "The planning request does not match the supported contract."
    elif "/replay-runs" in path:
        code = "replay_request_invalid"
        message = "The replay request does not match the supported contract."
    else:
        return await request_validation_exception_handler(request, exc)

    return JSONResponse(
        status_code=422,
        content={
            "detail": {
                "code": code,
                "message": message,
                "retryable": False,
            }
        },
    )


__all__ = ["safe_request_validation_handler"]
