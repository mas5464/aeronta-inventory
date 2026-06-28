"""Producer transport seam. FakeTransport for tests; real mTLS/AWS deferred."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict


class TransportError(Exception):
    """Connection-level failure; retryable by the producer."""


class TransportResponse(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    status_code: int
    retry_after_s: float | None = None
    body: dict | None = None


@runtime_checkable
class Transport(Protocol):
    def send(self, *, tenant_id: str, body: bytes) -> TransportResponse: ...


def _coerce(item: object) -> TransportResponse:
    if isinstance(item, TransportResponse):
        return item
    if isinstance(item, int):
        return TransportResponse(status_code=item)
    raise TypeError(f"unsupported scripted response: {item!r}")


class FakeTransport:
    def __init__(
        self, responses: Iterable[object] | None = None, *, default: int = 202
    ) -> None:
        self._queue = list(responses or [])
        self._default = default
        self.sent: list[tuple[str, bytes]] = []

    def send(self, *, tenant_id: str, body: bytes) -> TransportResponse:
        self.sent.append((tenant_id, body))
        if not self._queue:
            return TransportResponse(status_code=self._default)
        item = self._queue.pop(0)
        if isinstance(item, TransportError):
            raise item
        return _coerce(item)


class HttpsMtlsTransport:
    def send(self, *, tenant_id: str, body: bytes) -> TransportResponse:
        raise NotImplementedError("Phase 2: real mTLS + AWS transport")


class AsgiTransport:
    """Real in-process HTTP round-trip to a FastAPI app (no sockets/mTLS).

    ``httpx.ASGITransport`` is an async-only transport; ``httpx.Client`` (sync)
    cannot use it directly.  We therefore drive it via ``httpx.AsyncClient`` and
    bridge back to the synchronous ``Transport.send`` contract.

    Bridge strategy — loop-detection guard:
    * No running event loop  →  ``asyncio.run()`` (fast path, used by all current
      sync callers including the test suite).
    * Running event loop detected  →  submit ``asyncio.run()`` to a fresh
      ``ThreadPoolExecutor`` thread that owns its own loop.  This avoids the
      ``RuntimeError: asyncio.run() cannot be called from a running event loop``
      that would otherwise surface in pytest-asyncio, Starlette/FastAPI routes, or
      any ``await``-chain caller.  Thread overhead is acceptable; ``AsgiTransport``
      is test/dev infrastructure only.

    Any ``httpx.TransportError`` (connection-level) is caught and re-raised as a
    ``TransportError`` so the publisher never propagates raw transport errors.
    """

    def __init__(self, app: object, *, base_url: str = "http://emro.test") -> None:
        self._app = app
        self._base_url = base_url

    def send(self, *, tenant_id: str, body: bytes) -> TransportResponse:
        import asyncio
        import concurrent.futures

        import httpx

        async def _send() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=self._app), base_url=self._base_url
            ) as client:
                return await client.post(
                    f"/v1/tenants/{tenant_id}/events",
                    content=body,
                    headers={"content-type": "application/json"},
                )

        try:
            try:
                asyncio.get_running_loop()
                # A loop is already running (e.g. pytest-asyncio, FastAPI route).
                # Run asyncio.run() in a fresh thread that owns its own loop to
                # avoid RuntimeError.
                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    resp = pool.submit(asyncio.run, _send()).result()
            except RuntimeError:
                # No running loop — safe to call asyncio.run() directly.
                resp = asyncio.run(_send())
        except httpx.TransportError as exc:  # connection-level failure
            raise TransportError(str(exc)) from exc
        retry_after = resp.headers.get("retry-after")
        return TransportResponse(
            status_code=resp.status_code,
            retry_after_s=float(retry_after) if retry_after is not None else None,
        )
