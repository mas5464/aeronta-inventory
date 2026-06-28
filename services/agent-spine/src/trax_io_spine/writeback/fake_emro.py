"""In-memory FastAPI mock of the eMRO Writeback REST surface (#6).

Backed by a single InMemoryWritebackTarget so the mock and the in-memory reference share one
behavior definition (no drift). Behind the `emro` extra (FastAPI imported lazily).
"""

from __future__ import annotations

from typing import Any

from trax_io_spine.contracts import (
    RollbackRequest,
    WritebackRequest,
    WritebackStatus,
)
from trax_io_spine.writeback.target import InMemoryWritebackTarget


def create_fake_emro(
    open_orders: set[tuple[str, str, str]] | None = None, *, rollback_window_days: int = 90
) -> Any:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse

    target = InMemoryWritebackTarget(open_orders, rollback_window_days=rollback_window_days)
    app = FastAPI(title="fake_emro")

    @app.post("/inventory-levels")
    def write_level(body: dict[str, Any]) -> JSONResponse:
        result = target.write(WritebackRequest.model_validate(body))
        code = 409 if result.status is WritebackStatus.DEFERRED_OPEN_ORDER else 200
        return JSONResponse(result.model_dump(mode="json"), status_code=code)

    @app.get("/history")
    def get_history(tenant_id: str, pn: str, location: str) -> JSONResponse:
        entries = target.get_history(tenant_id=tenant_id, pn=pn, location=location)
        return JSONResponse([e.model_dump(mode="json") for e in entries])

    @app.post("/rollback")
    def rollback(body: dict[str, Any]) -> JSONResponse:
        result = target.rollback(RollbackRequest.model_validate(body))
        return JSONResponse(result.model_dump(mode="json"))

    return app
