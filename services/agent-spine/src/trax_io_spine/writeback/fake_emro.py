"""In-memory FastAPI mock of the eMRO Writeback REST surface (#6).

Pins the request/response contract so the writeback client + integration tests run with no
AWS, and #6 implements the same shape. Behind the `emro` extra (FastAPI imported lazily).
"""

from __future__ import annotations

from typing import Any

_FIELDS = ("rop", "eoq", "safety_stock", "max_stock")


def create_fake_emro(open_orders: set[tuple[str, str, str]] | None = None) -> Any:
    from fastapi import FastAPI, Response
    from fastapi.responses import JSONResponse

    blocked = open_orders or set()
    levels: dict[tuple[str, str, str], dict[str, int]] = {}
    seen: dict[str, dict[str, Any]] = {}
    history: list[dict[str, Any]] = []

    app = FastAPI(title="fake_emro")

    @app.post("/inventory-levels")
    def write_level(body: dict[str, Any]) -> Response:
        idem = str(body["idempotency_key"])
        if idem in seen:
            return JSONResponse(seen[idem])
        key = (body["tenant_id"], body["pn"], body["location"])
        if key in blocked:
            return JSONResponse({"status": "deferred_open_order"}, status_code=409)
        new_values = {f: int(body[f]) for f in _FIELDS}
        old_values = levels.get(key)
        levels[key] = new_values
        payload = {"status": "written", "old_values": old_values, "new_values": new_values}
        seen[idem] = payload
        history.append(
            {"tenant_id": key[0], "pn": key[1], "location": key[2], "values": new_values}
        )
        return JSONResponse(payload)

    @app.get("/history")
    def get_history() -> list[dict[str, Any]]:
        return history

    return app
