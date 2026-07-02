"""Writeback target Protocol + an in-memory implementation for tests and `--dry-run`."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Protocol

from trax_io_spine.contracts import (
    HistoryEntry,
    RollbackRequest,
    RollbackResult,
    RollbackStatus,
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

    def __init__(
        self,
        open_orders: set[tuple[str, str, str]] | None = None,
        *,
        rollback_window_days: int = 90,
    ) -> None:
        if rollback_window_days <= 0:
            raise ValueError("rollback_window_days must be > 0")
        self._open_orders = open_orders or set()
        self._levels: dict[tuple[str, str, str], dict[str, int]] = {}
        self._seen: dict[str, WritebackResult] = {}
        self.history: list[WritebackResult] = []
        self._history: dict[tuple[str, str, str], list[HistoryEntry]] = {}
        self._window = rollback_window_days

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

    def iter_history(self, tenant_id: str) -> tuple[HistoryEntry, ...]:
        """Every ledger entry for `tenant_id`, sorted by (pn, location, version).

        BVR input (spec §2): the report attributes over the WHOLE tenant ledger,
        not one key. In-memory-target-only by design — v1-local reports run on
        the BFF's InMemoryWritebackTarget (fake_emro is backed by this class);
        a real-eMRO enumeration API is a deferred writeback-REST concern.
        """
        entries = [
            e
            for (tid, _pn, _loc), items in self._history.items()
            if tid == tenant_id
            for e in items
        ]
        return tuple(sorted(entries, key=lambda e: (e.pn, e.location, e.version)))

    def rollback(self, req: RollbackRequest) -> RollbackResult:
        key = (req.tenant_id, req.pn, req.location)
        entries = self._history.get(key, [])
        latest = next(
            (e for e in reversed(entries) if e.status is WritebackStatus.WRITTEN), None
        )
        base = dict(tenant_id=req.tenant_id, pn=req.pn, location=req.location)
        if latest is None or latest.old_values is None:
            return RollbackResult(**base, status=RollbackStatus.NOTHING_TO_REVERT)
        if req.requested_at - latest.changed_at > timedelta(days=self._window):
            return RollbackResult(**base, status=RollbackStatus.OUTSIDE_WINDOW)

        current = self._levels.get(key)
        to_values = dict(latest.old_values)
        self._levels[key] = dict(to_values)
        # _record computes parent_version as the most-recent WRITTEN entry = `latest` (the one
        # being reverted), which is exactly the link we want — no correction needed.
        entry = self._record(
            key=key,
            req=WritebackRequest(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                rop=to_values["rop"], eoq=to_values["eoq"],
                safety_stock=to_values["safety_stock"], max_stock=to_values["max_stock"],
                provenance_id=f"rollback:{latest.provenance_id}",
                idempotency_key=f"rollback:{latest.version}:{req.requested_at.isoformat()}",
                tier=latest.tier,
            ),
            status=WritebackStatus.WRITTEN, old_values=current, new_values=to_values,
            principal=req.principal, changed_at=req.requested_at,
        )
        return RollbackResult(
            **base, status=RollbackStatus.ROLLED_BACK, from_values=current,
            to_values=to_values, reverted_from_version=latest.version,
            new_version=entry.version, rolled_back_at=req.requested_at,
        )

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
