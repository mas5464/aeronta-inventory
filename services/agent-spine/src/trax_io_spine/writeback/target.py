"""Writeback target Protocol + an in-memory implementation for tests and `--dry-run`."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol

from trax_io_spine.contracts import (
    HistoryEntry,
    RollbackRequest,
    RollbackResult,
    WritebackRequest,
    WritebackResult,
    WritebackStatus,
)

_FIELDS = ("rop", "eoq", "safety_stock", "max_stock")
_AGENT_VERSION = "agent-spine-v1"


class WritebackTarget(Protocol):
    def write(self, req: WritebackRequest) -> WritebackResult: ...


class AuditedWritebackTarget(WritebackTarget, Protocol):
    """WritebackTarget + provenance history & rollback (the #6 hardening surface)."""

    def get_history(
        self, *, tenant_id: str, pn: str, location: str
    ) -> tuple[HistoryEntry, ...]: ...
    def rollback(self, req: RollbackRequest) -> RollbackResult: ...


class InMemoryWritebackTarget:
    """Dict-backed eMRO stand-in. Idempotent by key; defers on a simulated open order."""

    def __init__(self, open_orders: set[tuple[str, str, str]] | None = None) -> None:
        self._open_orders = open_orders or set()
        self._levels: dict[tuple[str, str, str], dict[str, int]] = {}
        self._seen: dict[str, WritebackResult] = {}
        self.history: list[WritebackResult] = []
        self._history: dict[tuple[str, str, str], list[HistoryEntry]] = {}

    def _record(
        self,
        *,
        key: tuple[str, str, str],
        req: WritebackRequest,
        status: WritebackStatus,
        old_values: dict[str, int] | None,
        new_values: dict[str, int],
        principal: str,
        changed_at: datetime,
    ) -> HistoryEntry:
        entries = self._history.setdefault(key, [])
        version = len(entries) + 1
        parent = next(
            (e.version for e in reversed(entries) if e.status is WritebackStatus.WRITTEN), None
        )
        entry = HistoryEntry(
            tenant_id=key[0], pn=key[1], location=key[2], version=version, status=status,
            old_values=old_values, new_values=new_values, provenance_id=req.provenance_id,
            tier=req.tier, agent_version=_AGENT_VERSION, changed_by_principal=principal,
            idempotency_key=req.idempotency_key, parent_version=parent, changed_at=changed_at,
        )
        entries.append(entry)
        return entry

    def get_history(self, *, tenant_id: str, pn: str, location: str) -> tuple[HistoryEntry, ...]:
        return tuple(self._history.get((tenant_id, pn, location), ()))

    def write(self, req: WritebackRequest) -> WritebackResult:
        if req.idempotency_key in self._seen:
            return self._seen[req.idempotency_key]

        key = (req.tenant_id, req.pn, req.location)
        if req.shadow:
            new_values = {f: getattr(req, f) for f in _FIELDS}
            old_values = self._levels.get(key)
            now = datetime.now(UTC)
            self._record(
                key=key, req=req, status=WritebackStatus.SHADOWED,
                old_values=old_values, new_values=new_values,
                principal="agent-spine", changed_at=now,
            )
            result = WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                status=WritebackStatus.SHADOWED, old_values=old_values,
                new_values=new_values, written_at=now,
            )
            self._seen[req.idempotency_key] = result
            return result

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
        now = datetime.now(UTC)
        self._record(
            key=key, req=req, status=WritebackStatus.WRITTEN,
            old_values=old_values, new_values=new_values,
            principal="agent-spine", changed_at=now,
        )
        result = WritebackResult(
            tenant_id=req.tenant_id, pn=req.pn, location=req.location,
            status=WritebackStatus.WRITTEN, old_values=old_values, new_values=new_values,
            written_at=now,
        )
        self._seen[req.idempotency_key] = result
        self.history.append(result)
        return result
