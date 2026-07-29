"""PgPlannerStore — the PlannerStore interface over Supabase Postgres (C1).

Same public surface as bff/store.PlannerStore (duck-typed into
create_planner_app); queue/decision state lives in SQL, static views are
seeded JSONB (pg/seed.py). This file grows over Tasks 9-12; each section is
labeled with its task.
"""
from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from trax_io_reco.contracts.context import TenantPolicyConfig
from trax_io_reco.contracts.recommendation import Recommendation

from trax_io_spine.bff.feeds import FEED_DEFINITIONS
from trax_io_spine.bff.models import (
    ActionResult,
    BulkApproveFilter,
    DashboardSummary,
    FeedConnectionStatus,
    FeedHealthRow,
    FeedHealthStrip,
    FeedsSummary,
    ForecastAccuracy,
    ForecastSummary,
    MethodCoverage,
    PartContext,
    PlanningTraceView,
    QueueRow,
    QueueSortKey,
    RecommendationDetail,
    RejectReason,
    Scenario,
    ScenarioAuditEvent,
    ScenarioParamsWire,
    ScenarioSolveResult,
    ScenarioStatus,
    ServiceLevelBand,
    ServiceLevelPolicy,
    TaskStatus,
)
from trax_io_spine.bff.scenario import (
    KeyStats,
    RepairScenarioInput,
    ScenarioSolver,
)
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
from trax_io_spine.contracts import GuardrailOutcome, HistoryEntry
from trax_io_spine.planning_inputs import (
    PLANNING_INPUTS_CONTRACT_VERSION,
    PlanningInputSnapshot,
    planning_input_coverage,
    planning_input_source_generation_hash,
    planning_input_source_snapshot_hash,
)
from trax_io_spine.scenario_result import (
    SCENARIO_INPUTS_CONTRACT_VERSION,
    build_scenario_result,
    repair_scenario_input_from_payload,
)
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
_PLANNING_TRACES_BY_RECOMMENDATION = "_planning_traces_by_recommendation"

# ---- C5 Task 7: empty-tenant fallbacks for the seeded-view snapshots --------
# A brand-new tenant (self-serve signup, zero uploads yet) has no
# `tenant_snapshots` rows at all: pg/seed.py only ever writes them atomically
# alongside part_keys/recommendations (one seeding transaction), so a missing
# snapshot row is a reliable signal for "this tenant has never been seeded",
# not data corruption. These constants are genuine DashboardSummary/
# ForecastSummary/FeedsSummary instances — schema-verified via model
# construction, not hand-typed JSON — built once at import time to match
# exactly what bff/store.py's PlannerStore.dashboard/forecast_summary/
# feeds_summary themselves compute for zero keys and an empty manifest (see
# each method's docstring there). `_snapshot`'s `default=` kwarg below serves
# them in place of raising when a kind was never seeded.
_EMPTY_DASHBOARD: dict = DashboardSummary(
    parts=0, total_on_hand=0, total_on_hand_value=0.0, total_shortage=0.0,
    total_projected_demand=0.0, aog_exposure=0, open_recommendations=0,
    net_cost_impact=0.0, by_criticality=(), by_ata=(), by_part_class=(),
    by_tier=(), top_shortages=(),
).model_dump(mode="json")

_policy_cfg = TenantPolicyConfig()
_EMPTY_FORECAST: dict = ForecastSummary(
    service_levels=ServiceLevelPolicy(
        bands=tuple(
            ServiceLevelBand(
                criticality_tier=tier,
                target_service_level=_policy_cfg.service_level_by_tier[tier],
                sku_count=0,
                actual_coverage=None,
            )
            for tier in sorted(_policy_cfg.service_level_by_tier)
        )
    ),
    method_coverage=MethodCoverage(total_skus=0, rows=()),
    accuracy=ForecastAccuracy(
        status="proxy",
        note=(
            "No demand history yet — this tenant has not uploaded any data. "
            "Once ingested, points compare real recent monthly DEMAND_HISTORY "
            "actuals against the engine's current demand projection."
        ),
        points=(),
    ),
).model_dump(mode="json")

_EMPTY_FEEDS: dict = FeedsSummary(
    # No manifest means no latest-run evidence. Definitions remain useful as
    # capability metadata, but cannot make an unseeded tenant look connected.
    health=FeedHealthStrip(
        connected=0,
        partial=0,
        not_connected=len(FEED_DEFINITIONS),
        extract_date=None,
    ),
    feeds=tuple(
        FeedHealthRow(
            feed_id=d.feed_id,
            name=d.name,
            status=FeedConnectionStatus.NOT_CONNECTED,
            domains=d.domains,
            rows=None, last_sync=None, notes=d.notes,
        )
        for d in FEED_DEFINITIONS
    ),
).model_dump(mode="json")

# Mirrors the shape pg/seed.py writes for "current_policies" (minus
# `seeded_at`, which nothing here reads) — zero policies, zero keys, no
# extract date, exactly what a never-seeded tenant's portfolio actually is.
_EMPTY_CURRENT_POLICIES: dict = {"policies": {}, "keys_total": 0, "extract_date": None}


@dataclass(frozen=True)
class _PersistedScenarioInputs:
    source_manifest: dict
    key_universe: tuple[tuple[str, str], ...]
    procurement_inputs: tuple[KeyStats, ...]
    repair_inputs: tuple[RepairScenarioInput, ...]
    tenant_policy: TenantPolicyConfig


class PgPlannerStore:
    def __init__(
        self, pool, *, tenant_slug: str, tenant_uuid: str, open_orders=None,
        principal: str = "planner",
    ):
        self._pool = pool
        self.tenant_id = tenant_slug  # attribute parity with PlannerStore
        self._uuid = tenant_uuid
        self._open_orders = open_orders
        # The verified caller attributed to decisions() this store records and
        # to the PgWritebackTarget it constructs below — defaults to "planner"
        # so the dev/no-auth boot path (bff/app.py without a verifier) and
        # every pre-existing call site behave exactly as before (C3 Task 0a).
        # The BFF's `_store` factory overrides this per-request with the
        # verified caller's `sub` claim via `with_principal`.
        self._principal = principal
        self.writeback = PgWritebackTarget(
            pool, tenant_uuid=tenant_uuid, open_orders=open_orders, principal=principal
        )

    def with_principal(self, principal: str) -> PgPlannerStore:
        """A fresh facade over the same pool/tenant, attributing subsequent
        decisions + writeback entries to a different verified caller.

        Returns a new instance rather than mutating this one in place: this
        store may be a long-lived, shared-across-requests object (constructed
        once and kept in the BFF's `stores` dict), and FastAPI's sync `def`
        routes run in a thread pool — mutating shared state here would race
        under concurrent requests. Construction is cheap: neither this class
        nor PgWritebackTarget cache anything beyond the pool reference."""
        return PgPlannerStore(
            self._pool, tenant_slug=self.tenant_id, tenant_uuid=self._uuid,
            open_orders=self._open_orders, principal=principal,
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

    def _decision(self, conn, *, rec_id, action, payload=None, principal=None):
        conn.execute(
            "insert into decisions (tenant_id, rec_id, action, payload, principal)"
            " values (%s::uuid, %s, %s, %s, %s)",
            (self._uuid, rec_id, action, json.dumps(payload or {}),
             principal or self._principal),
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
            rec, outcome, status = self._load_entry(conn, rec_id)
            if status is not TaskStatus.PENDING:
                raise ValueError(
                    f"recommendation {rec_id} is {status.value}, not pending approval"
                )
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
    def _snapshot(self, conn, kind: str, *, default: dict | None = None) -> dict:
        """`default` (C5 Task 7): served in place of raising when this tenant has
        never been seeded at all (a brand-new self-serve signup, zero uploads) —
        every one of pg/seed.py's `_SEEDED_TABLES` (including `tenant_snapshots`)
        is written in ONE transaction, so a missing row here reliably means "no
        data yet", not corruption of an otherwise-populated tenant. Callers that
        don't pass `default` keep the old fail-loud behavior unchanged."""
        row = conn.execute(
            "select payload from tenant_snapshots "
            "where tenant_id = %s::uuid and kind = %s",
            (self._uuid, kind),
        ).fetchone()
        if row is None:
            if default is not None:
                return default
            raise LookupError(f"tenant {self.tenant_id}: no seeded snapshot {kind!r}")
        return row[0]

    def part_context(
        self,
        pn: str,
        location: str,
        recommendation_id: str | None = None,
    ) -> PartContext:
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
        payload = dict(row[0])
        traces = payload.pop(_PLANNING_TRACES_BY_RECOMMENDATION, {})
        context = PartContext.model_validate(payload)
        if recommendation_id is None:
            return context

        selected = traces.get(recommendation_id)
        if selected is None:
            # The row and its embedded trace map are tenant-scoped by RLS. Unknown
            # ids and ids belonging to another key/tenant share one non-enumerating
            # response, matching the in-memory store.
            raise RecommendationNotFound(f"{pn}/{location}")
        return context.model_copy(
            update={"planning_trace": PlanningTraceView.model_validate(selected)}
        )

    def current_planning_source_snapshot_hash(self) -> str | None:
        """Return the all-eligible planning-input generation in one row read."""

        with self._conn() as conn:
            row = conn.execute(
                """
                select payload->>'source_snapshot_hash'
                from tenant_snapshots
                where tenant_id = %s::uuid and kind = 'planning_inputs'
                """,
                (self._uuid,),
            ).fetchone()
        if row is None:
            return None
        source_snapshot_hash = row[0]
        if (
            not isinstance(source_snapshot_hash, str)
            or not source_snapshot_hash.startswith("candidate_snapshot_")
        ):
            raise ValueError("planning input snapshot header is corrupt")
        return source_snapshot_hash

    def current_planning_source_generation_hash(self) -> str | None:
        """Return the common full-universe planning generation in one row read."""

        with self._conn() as conn:
            row = conn.execute(
                """
                select payload->>'source_generation_hash'
                from tenant_snapshots
                where tenant_id = %s::uuid and kind = 'planning_inputs'
                """,
                (self._uuid,),
            ).fetchone()
        if row is None:
            return None
        generation = row[0]
        if (
            not isinstance(generation, str)
            or not generation.startswith("planning_generation_")
            or len(generation) != len("planning_generation_") + 64
        ):
            raise ValueError("planning input generation header is corrupt")
        return generation

    def current_planning_model_profile(self) -> dict[str, str] | None:
        """Return fixed-cardinality trusted model versions in one row read."""

        with self._conn() as conn:
            row = conn.execute(
                """
                select payload->'model_profile'
                from tenant_snapshots
                where tenant_id = %s::uuid and kind = 'planning_inputs'
                """,
                (self._uuid,),
            ).fetchone()
        if row is None:
            return None
        profile = row[0]
        expected_fields = {
            "tenant_policy_version",
            "forecast_version",
            "repair_model_version",
            "candidate_planner_version",
        }
        if (
            not isinstance(profile, dict)
            or set(profile) != expected_fields
            or any(not isinstance(value, str) or not value for value in profile.values())
        ):
            raise ValueError("planning input model profile header is corrupt")
        return dict(profile)

    @staticmethod
    def _planning_context(payload: Mapping) -> PartContext:
        public_payload = dict(payload)
        public_payload.pop(_PLANNING_TRACES_BY_RECOMMENDATION, None)
        return PartContext.model_validate(public_payload)

    def planning_input_snapshot(
        self,
        keys: tuple[tuple[str, str], ...] | None = None,
    ) -> PlanningInputSnapshot:
        """Read exact planning contexts from one tenant transaction.

        ``None`` selects the complete eligible universe in canonical
        decision-key order. An explicit key tuple is loaded in one query and
        returned in caller order; missing keys fail without disclosing any
        cross-tenant row.
        """

        normalized_keys: tuple[tuple[str, str], ...] | None = None
        if keys is not None:
            values: list[tuple[str, str]] = []
            for raw_key in keys:
                if (
                    not isinstance(raw_key, (tuple, list))
                    or len(raw_key) != 2
                    or not all(
                        isinstance(value, str) and value
                        for value in raw_key
                    )
                ):
                    raise ValueError("planning input keys must be non-empty part/location pairs")
                values.append((raw_key[0], raw_key[1]))
            if len(values) != len(set(values)):
                raise ValueError("planning input keys must be unique")
            normalized_keys = tuple(values)

        with self._conn() as conn:
            header_row = conn.execute(
                """
                select payload, seeded_at
                from tenant_snapshots
                where tenant_id = %s::uuid and kind = 'planning_inputs'
                """,
                (self._uuid,),
            ).fetchone()
            header = dict(header_row[0]) if header_row is not None else None
            seeded_at = header_row[1] if header_row is not None else None
            if header is None:
                raise LookupError(
                    f"tenant {self.tenant_id}: no seeded snapshot 'planning_inputs'"
                )
            source_generation_hash = header.get("source_generation_hash")
            if (
                not isinstance(source_generation_hash, str)
                or not source_generation_hash.startswith("planning_generation_")
                or len(source_generation_hash)
                != len("planning_generation_") + 64
            ):
                raise ValueError("planning input generation header is corrupt")

            contexts: list[PartContext] = []
            universe_inputs: list[PartContext | Mapping] = []
            if normalized_keys is None:
                with conn.cursor(name="planning_input_snapshot_reader") as cursor:
                    cursor.execute(
                        """
                        select
                          case
                            when context ? 'candidate_frontier'
                             and context->'candidate_frontier' <> 'null'::jsonb
                            then context
                            else null
                          end as eligible_context,
                          pn,
                          location,
                          context #>> '{attributes,criticality_tier}'
                        from part_contexts
                        where tenant_id = %s::uuid
                        order by
                          coalesce(
                            context #>> '{candidate_frontier,decision_key}',
                            pn || '@' || location
                          ),
                          pn,
                          location
                        """,
                        (self._uuid,),
                    )
                    for payload, pn, location, criticality in cursor:
                        if payload is None:
                            universe_inputs.append(
                                {
                                    "pn": pn,
                                    "location": location,
                                    "attributes": {
                                        "criticality_tier": (
                                            int(criticality)
                                            if criticality is not None
                                            else None
                                        )
                                    },
                                    "candidate_frontier": None,
                                }
                            )
                            continue
                        context = self._planning_context(payload)
                        contexts.append(context)
                        universe_inputs.append(context)
            elif normalized_keys:
                pns = [key[0] for key in normalized_keys]
                locations = [key[1] for key in normalized_keys]
                with conn.cursor(name="planning_input_explicit_reader") as cursor:
                    cursor.execute(
                        """
                        select requested.pn, requested.location, stored.context
                        from unnest(%s::text[], %s::text[]) with ordinality
                          as requested(pn, location, ordinal)
                        left join part_contexts stored
                          on stored.tenant_id = %s::uuid
                         and stored.pn = requested.pn
                         and stored.location = requested.location
                        order by requested.ordinal
                        """,
                        (pns, locations, self._uuid),
                    )
                    for pn, location, payload in cursor:
                        if payload is None:
                            raise RecommendationNotFound(f"{pn}/{location}")
                        contexts.append(self._planning_context(payload))

        context_tuple = tuple(contexts)
        if normalized_keys is None:
            if (
                header.get("contract_version")
                != PLANNING_INPUTS_CONTRACT_VERSION
            ):
                raise ValueError("planning input snapshot contract is unsupported")
            stored_hash = header.get("source_snapshot_hash")
            stored_coverage = header.get("coverage")
            if not isinstance(stored_coverage, dict):
                raise ValueError("planning input snapshot does not reconcile")
            coverage = {
                field: int(value)
                for field, value in stored_coverage.items()
                if isinstance(field, str)
                and isinstance(value, int)
                and not isinstance(value, bool)
            }
            if coverage != stored_coverage:
                raise ValueError("planning input snapshot coverage is corrupt")
            observed = planning_input_coverage(
                universe_inputs,
                total_key_count=len(universe_inputs),
                returned_key_count=len(context_tuple),
            )
            if coverage != observed:
                raise ValueError("planning input snapshot coverage does not reconcile")
            source_snapshot_hash = planning_input_source_snapshot_hash(
                universe_inputs,
                coverage=coverage,
            )
            if stored_hash != source_snapshot_hash:
                raise ValueError("planning input snapshot does not reconcile")
            if source_generation_hash != planning_input_source_generation_hash(
                source_snapshot_hash
            ):
                raise ValueError("planning input generation does not reconcile")
        else:
            coverage = planning_input_coverage(context_tuple)
            source_snapshot_hash = planning_input_source_snapshot_hash(
                context_tuple
            )

        return PlanningInputSnapshot(
            contexts=context_tuple,
            source_snapshot_hash=source_snapshot_hash,
            source_generation_hash=source_generation_hash,
            coverage=coverage,
            seeded_at=seeded_at,
        )

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

        A never-seeded tenant (no upload yet) has no `dashboard_static` row at
        all — `_snapshot`'s `default=_EMPTY_DASHBOARD` serves the all-zero
        equivalent instead of raising (C5 Task 7).
        """
        with self._conn() as conn:
            return DashboardSummary.model_validate(
                self._snapshot(conn, "dashboard_static", default=_EMPTY_DASHBOARD)
            )

    def forecast_summary(self) -> ForecastSummary:
        with self._conn() as conn:
            return ForecastSummary.model_validate(
                self._snapshot(conn, "forecast_summary", default=_EMPTY_FORECAST)
            )

    def feeds_summary(self) -> FeedsSummary:
        with self._conn() as conn:
            return FeedsSummary.model_validate(
                self._snapshot(conn, "feeds_summary", default=_EMPTY_FEEDS)
            )

    # ---- Task 12: scenarios + BVR ------------------------------------------
    def _key_stats(self, conn=None) -> list[KeyStats]:
        """`part_keys.key_stats` -> `KeyStats` objects. `KeyStats` is a plain frozen
        dataclass (not pydantic) — the seeder wrote it via
        `json.dumps(dataclasses.asdict(ks))`, so rows are reconstructed with
        `KeyStats(**row[0])`, not `.model_validate`.  Portfolio keys that could not
        be scored (for example, unavailable demand history) carry the explicit
        ``{"scorable": false}`` sentinel and remain queryable/billable while being
        excluded from scenario math.

        Accepts an optional open connection to reuse (e.g. `bvr()`'s single
        transaction) — bare calls (`solve_scenario`) behave as before, opening
        their own connection."""

        def _load(c) -> list[KeyStats]:
            rows = c.execute(
                "select key_stats from part_keys where tenant_id = %s::uuid "
                "order by pn, location",
                (self._uuid,),
            ).fetchall()
            return [
                KeyStats(**payload)
                for row in rows
                if (payload := row[0]).get("scorable") is not False
            ]

        if conn is not None:
            return _load(conn)
        with self._conn() as c:
            return _load(c)

    def _scenario_inputs(self, conn=None) -> _PersistedScenarioInputs:
        """Hydrate the immutable seed-time inputs used by every PG scenario solve."""

        def _load(c) -> _PersistedScenarioInputs:
            payload = self._snapshot(
                c,
                "scenario_inputs",
                default={
                    "contract_version": SCENARIO_INPUTS_CONTRACT_VERSION,
                    "source_tenant_id": self.tenant_id,
                    "source_manifest": {},
                    "key_universe": [],
                    "procurement_inputs": [],
                    "repair_inputs": [],
                    "tenant_policy": TenantPolicyConfig().model_dump(mode="json"),
                },
            )
            if payload.get("contract_version") != SCENARIO_INPUTS_CONTRACT_VERSION:
                raise ValueError("unsupported persisted scenario-input contract")
            source_manifest = payload.get("source_manifest")
            if not isinstance(source_manifest, Mapping):
                raise ValueError("persisted scenario source manifest must be an object")
            key_rows = payload.get("key_universe")
            if not isinstance(key_rows, list):
                raise ValueError("persisted scenario key universe must be an array")
            key_universe: list[tuple[str, str]] = []
            for row in key_rows:
                if not isinstance(row, list | tuple) or len(row) != 2:
                    raise ValueError("persisted scenario key is malformed")
                key = (str(row[0]), str(row[1]))
                if not all(key):
                    raise ValueError("persisted scenario key cannot be empty")
                key_universe.append(key)
            if len(key_universe) != len(set(key_universe)):
                raise ValueError("persisted scenario key universe contains duplicates")

            procurement_rows = payload.get("procurement_inputs")
            repair_rows = payload.get("repair_inputs")
            if not isinstance(procurement_rows, list) or not isinstance(
                repair_rows,
                list,
            ):
                raise ValueError("persisted scenario input collections must be arrays")
            procurement = tuple(KeyStats(**item) for item in procurement_rows)
            repair = tuple(
                repair_scenario_input_from_payload(item) for item in repair_rows
            )
            source_tenant_id = payload.get("source_tenant_id")
            if not isinstance(source_tenant_id, str) or not source_tenant_id:
                raise ValueError("persisted scenario source tenant is unavailable")
            if any(
                item.pipeline.tenant_id != source_tenant_id for item in repair
            ):
                raise ValueError("persisted repair input crosses source tenants")
            universe = set(key_universe)
            if any((item.pn, item.location) not in universe for item in procurement):
                raise ValueError("persisted procurement input is outside key universe")
            if any((item.pn, item.location) not in universe for item in repair):
                raise ValueError("persisted repair input is outside key universe")
            return _PersistedScenarioInputs(
                source_manifest=dict(source_manifest),
                key_universe=tuple(key_universe),
                procurement_inputs=procurement,
                repair_inputs=repair,
                tenant_policy=TenantPolicyConfig.model_validate(
                    payload.get("tenant_policy")
                ),
            )

        if conn is not None:
            return _load(conn)
        with self._conn() as c:
            return _load(c)

    def _keys_total(self, conn) -> int:
        return self._snapshot(
            conn, "current_policies", default=_EMPTY_CURRENT_POLICIES
        )["keys_total"]

    def solve_scenario(self, params: ScenarioParamsWire) -> ScenarioSolveResult:
        """`POST .../scenarios/solve` — live solve, not persisted (API-SPEC.md)."""
        with self._conn() as conn:
            inputs = self._scenario_inputs(conn)
        solver = ScenarioSolver(
            list(inputs.procurement_inputs),
            total_keys_in_universe=len(inputs.key_universe),
            repair_inputs=list(inputs.repair_inputs),
        )
        result = solver.solve(PlannerStore._to_solver_params(params))
        return build_scenario_result(
            tenant_id=self.tenant_id,
            source_manifest=inputs.source_manifest,
            key_universe=inputs.key_universe,
            procurement_inputs=inputs.procurement_inputs,
            repair_inputs=inputs.repair_inputs,
            params=params,
            result=result,
            tenant_policy=inputs.tenant_policy,
        )

    def save_scenario(
        self, name: str, params: ScenarioParamsWire, result: ScenarioSolveResult
    ) -> Scenario:
        # A client result is display state, not an authoritative tenant-scoped
        # calculation. Re-solve from the immutable server-side snapshot.
        del result
        authoritative_result = self.solve_scenario(params)
        scenario = Scenario(
            id=str(uuid.uuid4()),
            name=name,
            params=params,
            result=authoritative_result,
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
        bff/store.py:629-667). All reads + the cache upsert happen inside ONE
        `tenant_conn` transaction (C1 final-review flagged the stale-serve window
        of the old multi-connection version — closed here)."""
        with self._conn() as conn:
            cached = conn.execute(
                "select report from bvr_cache where tenant_id = %s::uuid",
                (self._uuid,),
            ).fetchone()
            if cached is not None:
                return BvrReport.model_validate(cached[0])
            # C5 Task 7: a never-seeded tenant has no `current_policies` row —
            # default to the empty portfolio (0 keys, no policies) rather than
            # raising, matching dashboard/forecast/feeds below.
            meta = self._snapshot(conn, "current_policies", default=_EMPTY_CURRENT_POLICIES)
            raw = conn.execute(
                "select rec, status from recommendations where tenant_id = %s::uuid",
                (self._uuid,),
            ).fetchall()
            key_stats = self._key_stats(conn)
            ledger = tuple(
                HistoryEntry.model_validate(r[0])
                for r in conn.execute(
                    "select entry from writeback_ledger where tenant_id = %s::uuid "
                    "order by pn, location, version",
                    (self._uuid,),
                ).fetchall()
            )
            ks_row = conn.execute(
                "select engaged from kill_switches where tenant_id = %s::uuid",
                (self._uuid,),
            ).fetchone()
            kill_switch = bool(ks_row and ks_row[0])

            policies = meta["policies"]
            key_facts: list[KeyFacts] = []
            policy_of: dict[tuple[str, str], dict | None] = {}
            for ks in key_stats:
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
                ledger=ledger,
                baseline_for=baseline_for, kill_switch=kill_switch,
                keys_total_portfolio=meta["keys_total"],
            )
            conn.execute(
                "insert into bvr_cache (tenant_id, report) values (%s::uuid, %s) "
                "on conflict (tenant_id) do update set report = excluded.report,"
                " computed_at = now()",
                (self._uuid, json.dumps(report.model_dump(mode="json"))),
            )
        return report
