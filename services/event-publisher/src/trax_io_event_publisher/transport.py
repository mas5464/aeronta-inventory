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
