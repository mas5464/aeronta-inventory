"""Writeback target Protocol + an in-memory implementation for tests and `--dry-run`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from trax_io_spine.contracts import WritebackRequest, WritebackResult, WritebackStatus

_FIELDS = ("rop", "eoq", "safety_stock", "max_stock")


class WritebackTarget(Protocol):
    def write(self, req: WritebackRequest) -> WritebackResult: ...


class InMemoryWritebackTarget:
    """Dict-backed eMRO stand-in. Idempotent by key; defers on a simulated open order."""

    def __init__(self, open_orders: set[tuple[str, str, str]] | None = None) -> None:
        self._open_orders = open_orders or set()
        self._levels: dict[tuple[str, str, str], dict[str, int]] = {}
        self._seen: dict[str, WritebackResult] = {}
        self.history: list[WritebackResult] = []

    def write(self, req: WritebackRequest) -> WritebackResult:
        if req.idempotency_key in self._seen:
            return self._seen[req.idempotency_key]

        key = (req.tenant_id, req.pn, req.location)
        if key in self._open_orders:
            result = WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                status=WritebackStatus.DEFERRED_OPEN_ORDER,
            )
            self._seen[req.idempotency_key] = result
            return result

        new_values = {f: getattr(req, f) for f in _FIELDS}
        old_values = self._levels.get(key)
        self._levels[key] = new_values
        result = WritebackResult(
            tenant_id=req.tenant_id, pn=req.pn, location=req.location,
            status=WritebackStatus.WRITTEN, old_values=old_values, new_values=new_values,
            written_at=datetime.now(UTC),
        )
        self._seen[req.idempotency_key] = result
        self.history.append(result)
        return result
