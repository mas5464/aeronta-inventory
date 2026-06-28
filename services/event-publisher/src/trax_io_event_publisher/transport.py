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
    """Real in-process HTTP round-trip to a FastAPI app (no sockets/mTLS)."""

    def __init__(self, app: object, *, base_url: str = "http://emro.test") -> None:
        import httpx

        self._app = app
        self._base_url = base_url
        # ASGITransport is async-only; keep app reference and create async client per call
        self._asgi_transport = httpx.ASGITransport(app=app)

    def send(self, *, tenant_id: str, body: bytes) -> TransportResponse:
        import asyncio

        import httpx

        async def _send() -> httpx.Response:
            async with httpx.AsyncClient(
                transport=self._asgi_transport, base_url=self._base_url
            ) as client:
                return await client.post(
                    f"/v1/tenants/{tenant_id}/events",
                    content=body,
                    headers={"content-type": "application/json"},
                )

        try:
            resp = asyncio.run(_send())
        except httpx.TransportError as exc:  # connection-level failure
            raise TransportError(str(exc)) from exc
        retry_after = resp.headers.get("retry-after")
        return TransportResponse(
            status_code=resp.status_code,
            retry_after_s=float(retry_after) if retry_after is not None else None,
        )
