"""Trax IO reference event endpoint (fake_event_endpoint) — contract response codes."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from trax_io_event_publisher.schemas import EventEnvelope

RateLimiter = Callable[[str, EventEnvelope], bool]


def create_app(*, rate_limiter: RateLimiter | None = None) -> FastAPI:
    app = FastAPI(title="fake_event_endpoint")
    app.state.accepted = {}

    @app.post("/v1/tenants/{tenant_id}/events")
    async def ingest(tenant_id: str, request: Request) -> Response:
        raw = await request.body()
        try:
            env = EventEnvelope.model_validate_json(raw)
        except ValidationError as exc:
            return JSONResponse(status_code=400, content={"error": exc.errors(include_url=False)})
        if env.tenant_id != tenant_id:
            return JSONResponse(status_code=403, content={"error": "tenant mismatch"})
        if rate_limiter is not None and rate_limiter(tenant_id, env):
            return JSONResponse(
                status_code=429, content={"error": "rate limited"},
                headers={"Retry-After": "1"},
            )
        key = (tenant_id, env.event_id)
        if key in app.state.accepted:
            return JSONResponse(status_code=409, content={"error": "duplicate event_id"})
        app.state.accepted[key] = env
        return JSONResponse(status_code=202, content={"status": "accepted"})

    @app.post("/v1/tenants/{tenant_id}/events/replay")
    async def replay(tenant_id: str) -> Response:
        events = [
            e.model_dump(mode="json")
            for (tid, _), e in app.state.accepted.items()
            if tid == tenant_id
        ]
        return JSONResponse(status_code=200, content={"count": len(events), "events": events})

    return app
