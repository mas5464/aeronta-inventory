"""PgPlannerStore — the PlannerStore interface over Supabase Postgres (C1).

Same public surface as bff/store.PlannerStore (duck-typed into
create_planner_app); queue/decision state lives in SQL, static views are
seeded JSONB (pg/seed.py). This file grows over Tasks 9-12; each section is
labeled with its task.
"""
from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from trax_io_reco.contracts.recommendation import Recommendation

from trax_io_spine.bff.models import (
    ActionResult,
    BulkApproveFilter,
    DashboardSummary,
    FeedsSummary,
    ForecastSummary,
    FrontierPointWire,
    PartContext,
    QueueRow,
    QueueSortKey,
    RecommendationDetail,
    RejectReason,
    Scenario,
    ScenarioAuditEvent,
    ScenarioParamsWire,
    ScenarioSolveResult,
    ScenarioStatus,
    TaskStatus,
)
from trax_io_spine.bff.scenario import KeyStats, ScenarioSolver
from trax_io_spine.bff.store import (
    KillSwitchEngaged,
    PlannerStore,
    RecommendationNotFound,
    ScenarioNotFound,
    detail_view,
    row_view,
)
from trax_io_spine.bvr.models import BvrReport
from trax_io_spine.bvr.report import KeyFacts, RecState, build_bvr_report
from trax_io_spine.contracts import GuardrailOutcome
from trax_io_spine.supervisor import to_writeback_request

from .db import tenant_conn
from .writeback import PgWritebackTarget

_SORT_COLS = {
    QueueSortKey.PRIORITY: "priority",
    QueueSortKey.COST_IMPACT: "cost_impact",
    QueueSortKey.CONFIDENCE: "confidence",
    QueueSortKey.CRITICALITY: "criticality_tier",
}
_ROW_COLS = "rec, outcome, status, priority"


def _result_wire(params: ScenarioParamsWire, result) -> ScenarioSolveResult:
    """Mirrors `PlannerStore._result_wire` (bff/store.py:1032-1050) — that method
    only ever touches instance state via the `_outcome_wire` staticmethod, so it's
    reproduced here as a module-level function calling the staticmethod directly
    rather than modifying bff/store.py."""
    return ScenarioSolveResult(
        params=params,
        current=PlannerStore._outcome_wire(result.current),
        proposed=PlannerStore._outcome_wire(result.proposed),
        delta_investment=result.delta_investment,
        delta_coverage=result.delta_coverage,
        frontier=tuple(
            FrontierPointWire(
                service_level=p.service_level,
                projected_investment=p.projected_investment,
                projected_coverage=p.projected_coverage,
            )
            for p in result.frontier
        ),
        skipped_keys=result.skipped_keys,
        total_keys=result.total_keys,
        budget_cap_binds=result.budget_cap_binds,
    )


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

    # ---- Task 10: decisions ----------------------------------------------
    @property
    def kill_switch(self) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                "select engaged from kill_switches where tenant_id = %s::uuid",
                (self._uuid,),
            ).fetchone()
            return bool(row and row[0])

    def _decision(self, conn, *, rec_id, action, payload=None, principal="planner"):
        conn.execute(
            "insert into decisions (tenant_id, rec_id, action, payload, principal)"
            " values (%s::uuid, %s, %s, %s, %s)",
            (self._uuid, rec_id, action, json.dumps(payload or {}), principal),
        )
        conn.execute("delete from bvr_cache where tenant_id = %s::uuid", (self._uuid,))

    def _load_entry(self, conn, rec_id):
        row = conn.execute(
            "select rec, outcome, status from recommendations "
            "where tenant_id = %s::uuid and rec_id = %s for update",
            (self._uuid, rec_id),
        ).fetchone()
        if row is None:
            raise RecommendationNotFound(rec_id)
        return (
            Recommendation.model_validate(row[0]),
            GuardrailOutcome.model_validate(row[1]),
            TaskStatus(row[2]),
        )

    def _set_status(self, conn, rec_id, status: TaskStatus, **extra):
        sets, params = ["status = %s", "decided_at = now()"], [status.value]
        for col, val in extra.items():
            sets.append(f"{col} = %s")
            params.append(val)
        params += [self._uuid, rec_id]
        conn.execute(
            f"update recommendations set {', '.join(sets)} "  # noqa: S608
            "where tenant_id = %s::uuid and rec_id = %s",
            params,
        )

    def approve(self, rec_id: str) -> ActionResult:
        if self.kill_switch:
            raise KillSwitchEngaged(self.tenant_id)
        with self._conn() as conn:
            # Re-check under a row lock inside the write transaction: the seeder always
            # creates the kill_switches row, so FOR SHARE serializes against a concurrent
            # engage (closes the cross-connection TOCTOU).
            row = conn.execute(
                "select engaged from kill_switches "
                "where tenant_id = %s::uuid for share",
                (self._uuid,),
            ).fetchone()
            if row and row[0]:
                raise KillSwitchEngaged(self.tenant_id)
            rec, outcome, _ = self._load_entry(conn, rec_id)
            if rec.policy is None:
                raise ValueError(f"recommendation {rec_id} has no writable policy")
            self._set_status(conn, rec_id, TaskStatus.APPROVED)
            self._decision(conn, rec_id=rec_id, action="approve")
        idem = (
            f"{rec.tenant_id}:{rec.part_number}:{rec.current_location}:"
            f"{rec.input_snapshot_hash}"
        )
        result = self.writeback.write(
            to_writeback_request(rec, idempotency_key=idem, tier=outcome.tier)
        )
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.APPROVED, writeback=result,
            message=f"written ({result.status.value})",
        )

    def reject(self, rec_id: str, reason: RejectReason, detail: str = "") -> ActionResult:
        with self._conn() as conn:
            self._load_entry(conn, rec_id)
            self._set_status(
                conn, rec_id, TaskStatus.REJECTED,
                reject_reason=reason.value, reject_detail=detail,
            )
            self._decision(
                conn, rec_id=rec_id, action="reject",
                payload={"reason": reason.value, "detail": detail},
            )
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.REJECTED, message=reason.value
        )

    def defer(self, rec_id: str, until=None) -> ActionResult:
        with self._conn() as conn:
            self._load_entry(conn, rec_id)
            self._set_status(conn, rec_id, TaskStatus.DEFERRED, deferred_until=until)
            self._decision(conn, rec_id=rec_id, action="defer")
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.DEFERRED, message="deferred"
        )

    def bulk_approve(self, filter: BulkApproveFilter) -> tuple[int, list[ActionResult]]:
        if self.kill_switch:
            raise KillSwitchEngaged(self.tenant_id)
        with self._conn() as conn:
            raw = conn.execute(
                "select rec_id, rec, outcome from recommendations "
                "where tenant_id = %s::uuid and status = 'pending' and approvable",
                (self._uuid,),
            ).fetchall()
        targets = []
        for rec_id, rec_j, out_j in raw:
            rec = Recommendation.model_validate(rec_j)
            outcome = GuardrailOutcome.model_validate(out_j)
            if filter.tiers is not None and outcome.tier not in filter.tiers:
                continue
            if (
                filter.max_delta_pct is not None
                and outcome.delta_pct > filter.max_delta_pct
            ):
                continue
            if (
                filter.criticality_min is not None
                and rec.criticality_tier < filter.criticality_min
            ):
                continue
            if filter.types is not None and rec.type not in filter.types:
                continue
            targets.append(rec_id)
        results = [self.approve(rid) for rid in targets]
        with self._conn() as conn:
            self._decision(
                conn, rec_id=None, action="bulk_approve",
                payload={
                    "filter": filter.model_dump(mode="json"),
                    "approved_count": len(results),
                    "rec_ids": targets,
                },
            )
        return len(results), results

    def set_kill_switch(self, engaged: bool) -> None:
        with self._conn() as conn:
            conn.execute(
                "insert into kill_switches (tenant_id, engaged, updated_at)"
                " values (%s::uuid, %s, now()) on conflict (tenant_id)"
                " do update set engaged = excluded.engaged, updated_at = now()",
                (self._uuid, engaged),
            )
            self._decision(
                conn, rec_id=None, action="kill_switch", payload={"engaged": engaged}
            )

    def history(self, *, pn: str, location: str):
        return self.writeback.get_history(tenant_id=self.tenant_id, pn=pn, location=location)

    def rollback(self, req):
        result = self.writeback.rollback(req)
        with self._conn() as conn:
            self._decision(
                conn, rec_id=None, action="rollback",
                payload={"pn": req.pn, "location": req.location,
                         "status": result.status.value},
            )
        return result

    # ---- Task 11: seeded-view reads ---------------------------------------
    def _snapshot(self, conn, kind: str) -> dict:
        row = conn.execute(
            "select payload from tenant_snapshots "
            "where tenant_id = %s::uuid and kind = %s",
            (self._uuid, kind),
        ).fetchone()
        if row is None:
            raise LookupError(f"tenant {self.tenant_id}: no seeded snapshot {kind!r}")
        return row[0]

    def part_context(self, pn: str, location: str) -> PartContext:
        with self._conn() as conn:
            row = conn.execute(
                "select context from part_contexts "
                "where tenant_id = %s::uuid and pn = %s and location = %s",
                (self._uuid, pn, location),
            ).fetchone()
        if row is None:
            # Match PlannerStore.part_context (bff/store.py:538-540): unknown
            # (pn, location) — not in the tenant's key universe — raises
            # RecommendationNotFound, not KeyError.
            raise RecommendationNotFound(f"{pn}/{location}")
        return PartContext.model_validate(row[0])

    def dashboard(self) -> DashboardSummary:
        """Seeded `dashboard_static` snapshot, returned as-is.

        Read bff/store.py:669-753 before writing this: `open_recommendations`
        and `net_cost_impact` (lines 737-738) are the *only* two DashboardSummary
        fields that touch `self._entries` at all, and that read
        (lines 679-711 — `by_key`/`has_rec`) is keyed purely on *entry
        existence* per (pn, location) — deduped to the first-inserted entry for
        the rare case of a duplicate key (line 676-678 comment) — never on
        `entry.status`. A `status = 'pending'` recompute (the original plan
        here) is a proven mismatch on this repo's own extract_sample fixture:
        it both drops the dedup (double-counting duplicate-key rows) and
        excludes non-pending entries the in-memory formula still counts, and
        it does not — and structurally cannot, since Postgres row order isn't
        the dict's insertion order — reproduce "first inserted wins" for a
        duplicate key without an explicit ordinal column.
        None of the Task 10 decision verbs (approve/reject/defer/bulk_approve/
        rollback/set_kill_switch) insert or delete `recommendations` rows —
        they only flip `status` on existing ones — so these two fields are
        provably invariant across every decision this store supports, and the
        snapshot `pg/seed.py` writes (computed via a real `store.dashboard()`
        call) is exactly the "live" value at all times under that invariant.
        """
        with self._conn() as conn:
            return DashboardSummary.model_validate(self._snapshot(conn, "dashboard_static"))

    def forecast_summary(self) -> ForecastSummary:
        with self._conn() as conn:
            return ForecastSummary.model_validate(self._snapshot(conn, "forecast_summary"))

    def feeds_summary(self) -> FeedsSummary:
        with self._conn() as conn:
            return FeedsSummary.model_validate(self._snapshot(conn, "feeds_summary"))

    # ---- Task 12: scenarios + BVR ------------------------------------------
    def _key_stats(self) -> list[KeyStats]:
        """`part_keys.key_stats` -> `KeyStats` objects. `KeyStats` is a plain frozen
        dataclass (not pydantic) — the seeder wrote it via
        `json.dumps(dataclasses.asdict(ks))`, so rows are reconstructed with
        `KeyStats(**row[0])`, not `.model_validate`."""
        with self._conn() as conn:
            rows = conn.execute(
                "select key_stats from part_keys where tenant_id = %s::uuid "
                "order by pn, location",
                (self._uuid,),
            ).fetchall()
        return [KeyStats(**r[0]) for r in rows]

    def _keys_total(self, conn) -> int:
        return self._snapshot(conn, "current_policies")["keys_total"]

    def solve_scenario(self, params: ScenarioParamsWire) -> ScenarioSolveResult:
        """`POST .../scenarios/solve` — live solve, not persisted (API-SPEC.md)."""
        key_stats = self._key_stats()
        with self._conn() as conn:
            total_keys = self._keys_total(conn)
        solver = ScenarioSolver(key_stats, total_keys_in_universe=total_keys)
        result = solver.solve(PlannerStore._to_solver_params(params))
        return _result_wire(params, result)

    def save_scenario(
        self, name: str, params: ScenarioParamsWire, result: ScenarioSolveResult
    ) -> Scenario:
        scenario = Scenario(
            id=str(uuid.uuid4()),
            name=name,
            params=params,
            result=result,
            status=ScenarioStatus.DRAFT,
            created_at=datetime.now(UTC),
        )
        with self._conn() as conn:
            conn.execute(
                "insert into scenarios (tenant_id, scenario_id, payload, created_at)"
                " values (%s::uuid, %s, %s, %s)",
                (self._uuid, scenario.id,
                 json.dumps(scenario.model_dump(mode="json")), scenario.created_at),
            )
        return scenario

    def list_scenarios(self) -> list[Scenario]:
        with self._conn() as conn:
            rows = conn.execute(
                "select payload from scenarios where tenant_id = %s::uuid "
                "order by created_at desc",
                (self._uuid,),
            ).fetchall()
        return [Scenario.model_validate(r[0]) for r in rows]

    def _load_scenario(self, conn, scenario_id: str, *, for_update: bool = False) -> Scenario:
        sql = "select payload from scenarios where tenant_id = %s::uuid and scenario_id = %s"
        if for_update:
            sql += " for update"
        row = conn.execute(sql, (self._uuid, scenario_id)).fetchone()
        if row is None:
            raise ScenarioNotFound(scenario_id)
        return Scenario.model_validate(row[0])

    def get_scenario(self, scenario_id: str) -> Scenario:
        with self._conn() as conn:
            return self._load_scenario(conn, scenario_id)

    def delete_scenario(self, scenario_id: str) -> None:
        with self._conn() as conn:
            row = conn.execute(
                "delete from scenarios where tenant_id = %s::uuid and scenario_id = %s "
                "returning scenario_id",
                (self._uuid, scenario_id),
            ).fetchone()
        if row is None:
            raise ScenarioNotFound(scenario_id)

    def commit_scenario(self, scenario_id: str) -> ScenarioAuditEvent:
        """Promote a saved scenario to COMMITTED + append an audited marker.

        Does NOT write policies back to eMRO — Writeback is the only agent with
        eMRO write permission; a scenario commit is a planning-tool decision
        record, not a policy write (mirrors bff/store.py:1092-1109)."""
        now = datetime.now(UTC)
        with self._conn() as conn:
            scenario = self._load_scenario(conn, scenario_id, for_update=True)
            committed = scenario.model_copy(
                update={"status": ScenarioStatus.COMMITTED, "committed_at": now}
            )
            conn.execute(
                "update scenarios set payload = %s "
                "where tenant_id = %s::uuid and scenario_id = %s",
                (json.dumps(committed.model_dump(mode="json")), self._uuid, scenario_id),
            )
            event = ScenarioAuditEvent(
                scenario_id=scenario_id, scenario_name=committed.name,
                action="commit", at=now,
            )
            conn.execute(
                "insert into scenario_audit (tenant_id, event, at) values"
                " (%s::uuid, %s, %s)",
                (self._uuid, json.dumps(event.model_dump(mode="json")), now),
            )
        return event

    def scenario_audit_log(self) -> list[ScenarioAuditEvent]:
        with self._conn() as conn:
            rows = conn.execute(
                "select event from scenario_audit where tenant_id = %s::uuid "
                "order by at asc, id asc",
                (self._uuid,),
            ).fetchall()
        return [ScenarioAuditEvent.model_validate(r[0]) for r in rows]

    def bvr(self) -> BvrReport:
        """The Business Value Report (spec 2026-07-02), sourced from Postgres —
        cached in `bvr_cache`; every Task-10 decision (`_decision`) already deletes
        the cache row, so a cache miss here always means "state changed since the
        last compute" (mirrors the in-memory `_bvr_cache` invalidation in
        bff/store.py:629-667)."""
        with self._conn() as conn:
            cached = conn.execute(
                "select report from bvr_cache where tenant_id = %s::uuid",
                (self._uuid,),
            ).fetchone()
            if cached is not None:
                return BvrReport.model_validate(cached[0])
            meta = self._snapshot(conn, "current_policies")
            raw = conn.execute(
                "select rec, status from recommendations where tenant_id = %s::uuid",
                (self._uuid,),
            ).fetchall()

        policies = meta["policies"]
        key_facts: list[KeyFacts] = []
        policy_of: dict[tuple[str, str], dict | None] = {}
        for ks in self._key_stats():
            pol = policies.get(f"{ks.pn}|{ks.location}")
            policy_of[(ks.pn, ks.location)] = pol
            key_facts.append(KeyFacts(
                pn=ks.pn, location=ks.location, criticality_tier=ks.criticality_tier,
                rop=pol["rop"] if pol else 0, mean_per_day=ks.mean_per_day,
                lead_mean=ks.lead_mean,
                unit_cost=ks.unit_cost if ks.unit_cost > 0 else None,
            ))
        rec_states = [
            RecState(rec=Recommendation.model_validate(r), status=s) for r, s in raw
        ]

        def baseline_for(entry):
            # Postgres stores baseline policies as plain dicts at seed time (see
            # pg/seed.py) — the same shape the in-memory `baseline_for` builds by
            # hand, so it's returned as-is here (bff/store.py:651-656).
            return policy_of.get((entry.pn, entry.location))

        report = build_bvr_report(
            tenant_id=self.tenant_id, extract_date=meta.get("extract_date"),
            generated_at=datetime.now(UTC), key_facts=key_facts, rec_states=rec_states,
            ledger=self.writeback.iter_history(self.tenant_id),
            baseline_for=baseline_for, kill_switch=self.kill_switch,
            keys_total_portfolio=meta["keys_total"],
        )
        with self._conn() as conn:
            conn.execute(
                "insert into bvr_cache (tenant_id, report) values (%s::uuid, %s) "
                "on conflict (tenant_id) do update set report = excluded.report,"
                " computed_at = now()",
                (self._uuid, json.dumps(report.model_dump(mode="json"))),
            )
        return report
