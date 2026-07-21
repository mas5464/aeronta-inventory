"""PgPlannerStore — the PlannerStore interface over Supabase Postgres (C1).

Same public surface as bff/store.PlannerStore (duck-typed into
create_planner_app); queue/decision state lives in SQL, static views are
seeded JSONB (pg/seed.py). This file grows over Tasks 9-12; each section is
labeled with its task.
"""
from __future__ import annotations

from trax_io_reco.contracts.recommendation import Recommendation

from trax_io_spine.bff.models import (
    QueueRow,
    QueueSortKey,
    RecommendationDetail,
    TaskStatus,
)
from trax_io_spine.bff.store import (
    RecommendationNotFound,
    detail_view,
    row_view,
)
from trax_io_spine.contracts import GuardrailOutcome

from .db import tenant_conn
from .writeback import PgWritebackTarget

_SORT_COLS = {
    QueueSortKey.PRIORITY: "priority",
    QueueSortKey.COST_IMPACT: "cost_impact",
    QueueSortKey.CONFIDENCE: "confidence",
    QueueSortKey.CRITICALITY: "criticality_tier",
}
_ROW_COLS = "rec, outcome, status, priority"


class PgPlannerStore:
    def __init__(self, pool, *, tenant_slug: str, tenant_uuid: str, open_orders=None):
        self._pool = pool
        self.tenant_id = tenant_slug  # attribute parity with PlannerStore
        self._uuid = tenant_uuid
        self.writeback = PgWritebackTarget(
            pool, tenant_uuid=tenant_uuid, open_orders=open_orders
        )

    # ---- Task 9: queue reads ---------------------------------------------
    def _conn(self):
        return tenant_conn(self._pool, tenant_uuid=self._uuid)

    @staticmethod
    def _parse(row) -> tuple[Recommendation, GuardrailOutcome, TaskStatus, float]:
        rec = Recommendation.model_validate(row[0])
        outcome = GuardrailOutcome.model_validate(row[1])
        return rec, outcome, TaskStatus(row[2]), float(row[3])

    def _where(self, *, status, tier, type_, aog_min):
        clauses, params = ["tenant_id = %s::uuid", "status = %s"], [self._uuid, status.value]
        if tier is not None:
            clauses.append("tier = %s")
            params.append(int(tier))
        if type_ is not None:
            clauses.append("rec_type = %s")
            params.append(str(type_))
        if aog_min is not None:
            clauses.append("aog_level >= %s")
            params.append(int(aog_min))
        return " and ".join(clauses), params

    def _select(self, conn, *, status, sort_by, sort_dir, tier, type_, aog_min,
                limit=None, offset=None):
        where, params = self._where(status=status, tier=tier, type_=type_, aog_min=aog_min)
        direction = "desc" if sort_dir == "desc" else "asc"
        sql = (
            f"select {_ROW_COLS} from recommendations where {where} "  # noqa: S608
            f"order by {_SORT_COLS[sort_by]} {direction}, rec_id asc"
        )
        if limit is not None:
            sql += " limit %s offset %s"
            params += [limit, offset or 0]
        return conn.execute(sql, params).fetchall()

    def _rows(self, raw) -> list[QueueRow]:
        parsed = [self._parse(r) for r in raw]
        return [row_view(*fields) for fields in parsed]

    def queue(self, *, status: TaskStatus = TaskStatus.PENDING, limit: int = 50):
        with self._conn() as conn:
            raw = self._select(
                conn, status=status, sort_by=QueueSortKey.PRIORITY, sort_dir="desc",
                tier=None, type_=None, aog_min=None, limit=limit, offset=0,
            )
            return self._rows(raw)

    def list_queue_page(
        self, *, status: TaskStatus = TaskStatus.PENDING, limit: int = 50, offset: int = 0,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY, sort_dir: str = "desc",
        tier=None, type_=None, aog_min=None,
    ) -> tuple[list[QueueRow], int]:
        with self._conn() as conn:
            where, params = self._where(
                status=status, tier=tier, type_=type_, aog_min=aog_min
            )
            total = conn.execute(
                f"select count(*) from recommendations where {where}",  # noqa: S608
                params,
            ).fetchone()[0]
            raw = self._select(
                conn, status=status, sort_by=sort_by, sort_dir=sort_dir,
                tier=tier, type_=type_, aog_min=aog_min, limit=limit, offset=offset,
            )
            return self._rows(raw), total

    def list_queue_all(
        self, *, status: TaskStatus = TaskStatus.PENDING,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY, sort_dir: str = "desc",
        tier=None, type_=None, aog_min=None,
    ) -> list[QueueRow]:
        with self._conn() as conn:
            raw = self._select(
                conn, status=status, sort_by=sort_by, sort_dir=sort_dir,
                tier=tier, type_=type_, aog_min=aog_min,
            )
            return self._rows(raw)

    def detail(self, rec_id: str) -> RecommendationDetail:
        with self._conn() as conn:
            row = conn.execute(
                "select rec, outcome, status from recommendations "
                "where tenant_id = %s::uuid and rec_id = %s",
                (self._uuid, rec_id),
            ).fetchone()
        if row is None:
            raise RecommendationNotFound(rec_id)
        rec = Recommendation.model_validate(row[0])
        outcome = GuardrailOutcome.model_validate(row[1])
        return detail_view(rec, outcome, TaskStatus(row[2]))
