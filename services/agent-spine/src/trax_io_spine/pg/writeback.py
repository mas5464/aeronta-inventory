"""AuditedWritebackTarget backed by the writeback_ledger table (C1 Task 7).

Executable spec: InMemoryWritebackTarget (writeback/target.py). Observable
behavior must match it — history shape, idempotent replay, shadow semantics,
rollback linking. Current levels are DERIVED (latest WRITTEN entry's new_values)
rather than stored, so the ledger stays the single source of truth.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from trax_io_spine.contracts import (
    HistoryEntry,
    RollbackRequest,
    RollbackResult,
    RollbackStatus,
    WritebackRequest,
    WritebackResult,
    WritebackStatus,
)
from trax_io_spine.writeback.target import _AGENT_VERSION, _FIELDS

from .db import tenant_conn

_SELECT = (
    "select entry from writeback_ledger "
    "where tenant_id = %s and pn = %s and location = %s order by version"
)


class PgWritebackTarget:
    def __init__(
        self,
        pool,
        *,
        tenant_uuid: str,
        open_orders: set[tuple[str, str, str]] | None = None,
        rollback_window_days: int = 90,
        principal: str = "agent-spine",
    ) -> None:
        if rollback_window_days <= 0:
            raise ValueError("rollback_window_days must be > 0")
        self._pool = pool
        self._tenant_uuid = tenant_uuid
        self._open_orders = open_orders or set()
        self._window = rollback_window_days
        # The identity attributed to WRITTEN/SHADOWED entries this target
        # records (see write() below). Defaults to the autonomous agent
        # identity; a PgPlannerStore constructs its writeback target with the
        # verified caller's principal instead (C3 Task 0a). Rollback keeps
        # using RollbackRequest.principal — an explicit field on that request,
        # not this instance default.
        self._principal = principal

    # -- readers ------------------------------------------------------------
    def _entries(self, conn, pn: str, location: str) -> list[HistoryEntry]:
        rows = conn.execute(_SELECT, (self._tenant_uuid, pn, location)).fetchall()
        return [HistoryEntry.model_validate(r[0]) for r in rows]

    def get_history(self, *, tenant_id: str, pn: str, location: str) -> tuple[HistoryEntry, ...]:
        with tenant_conn(self._pool, tenant_uuid=self._tenant_uuid) as conn:
            return tuple(self._entries(conn, pn, location))

    def iter_history(self, tenant_id: str) -> tuple[HistoryEntry, ...]:
        with tenant_conn(self._pool, tenant_uuid=self._tenant_uuid) as conn:
            rows = conn.execute(
                "select entry from writeback_ledger where tenant_id = %s "
                "order by pn, location, version",
                (self._tenant_uuid,),
            ).fetchall()
            return tuple(HistoryEntry.model_validate(r[0]) for r in rows)

    # -- helpers ------------------------------------------------------------
    def _insert(self, conn, entry: HistoryEntry) -> None:
        conn.execute(
            "insert into writeback_ledger (tenant_id, pn, location, version, entry, changed_at)"
            " values (%s, %s, %s, %s, %s, %s)",
            (self._tenant_uuid, entry.pn, entry.location, entry.version,
             json.dumps(entry.model_dump(mode="json")), entry.changed_at),
        )

    def _record(self, conn, *, req: WritebackRequest, status: WritebackStatus,
                old_values, new_values, principal: str, changed_at: datetime) -> HistoryEntry:
        entries = self._entries(conn, req.pn, req.location)
        version = len(entries) + 1
        parent = next(
            (e.version for e in reversed(entries) if e.status is WritebackStatus.WRITTEN), None
        )
        entry = HistoryEntry(
            tenant_id=req.tenant_id, pn=req.pn, location=req.location, version=version,
            status=status, old_values=old_values, new_values=new_values,
            provenance_id=req.provenance_id, tier=req.tier, agent_version=_AGENT_VERSION,
            changed_by_principal=principal, idempotency_key=req.idempotency_key,
            parent_version=parent, changed_at=changed_at,
        )
        self._insert(conn, entry)
        return entry

    @staticmethod
    def _current_levels(entries: list[HistoryEntry]) -> dict[str, int] | None:
        latest = next(
            (e for e in reversed(entries) if e.status is WritebackStatus.WRITTEN), None
        )
        return dict(latest.new_values) if latest else None

    def _replay(self, conn, idempotency_key: str) -> WritebackResult | None:
        row = conn.execute(
            "select entry from writeback_ledger where tenant_id = %s "
            "and entry->>'idempotency_key' = %s order by version desc limit 1",
            (self._tenant_uuid, idempotency_key),
        ).fetchone()
        if row is None:
            return None
        e = HistoryEntry.model_validate(row[0])
        return WritebackResult(
            tenant_id=e.tenant_id, pn=e.pn, location=e.location, status=e.status,
            old_values=e.old_values, new_values=e.new_values, written_at=e.changed_at,
        )

    # -- protocol -----------------------------------------------------------
    def write(self, req: WritebackRequest) -> WritebackResult:
        with tenant_conn(self._pool, tenant_uuid=self._tenant_uuid) as conn:
            replayed = self._replay(conn, req.idempotency_key)
            if replayed is not None:
                return replayed
            key = (req.tenant_id, req.pn, req.location)
            if not req.shadow and key in self._open_orders:
                return WritebackResult(
                    tenant_id=req.tenant_id, pn=req.pn, location=req.location,
                    status=WritebackStatus.DEFERRED_OPEN_ORDER,
                )
            entries = self._entries(conn, req.pn, req.location)
            old_values = self._current_levels(entries)
            new_values = {f: getattr(req, f) for f in _FIELDS}
            now = datetime.now(UTC)
            status = WritebackStatus.SHADOWED if req.shadow else WritebackStatus.WRITTEN
            self._record(
                conn, req=req, status=status, old_values=old_values,
                new_values=new_values, principal=self._principal, changed_at=now,
            )
            return WritebackResult(
                tenant_id=req.tenant_id, pn=req.pn, location=req.location, status=status,
                old_values=old_values, new_values=new_values, written_at=now,
            )

    def rollback(self, req: RollbackRequest) -> RollbackResult:
        with tenant_conn(self._pool, tenant_uuid=self._tenant_uuid) as conn:
            entries = self._entries(conn, req.pn, req.location)
            latest = next(
                (e for e in reversed(entries) if e.status is WritebackStatus.WRITTEN), None
            )
            base = dict(tenant_id=req.tenant_id, pn=req.pn, location=req.location)
            if latest is None or latest.old_values is None:
                return RollbackResult(**base, status=RollbackStatus.NOTHING_TO_REVERT)
            if req.requested_at - latest.changed_at > timedelta(days=self._window):
                return RollbackResult(**base, status=RollbackStatus.OUTSIDE_WINDOW)
            current = self._current_levels(entries)
            to_values = dict(latest.old_values)
            entry = self._record(
                conn,
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
