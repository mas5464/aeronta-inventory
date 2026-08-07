"""In-memory Planner store: the approval queue + lifecycle over the real Supervisor pipeline."""

from __future__ import annotations

import calendar
import json
import uuid
from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Literal

from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.online_store import OnlineGeneration
from trax_io_feature_store.snapshot import load_store
from trax_io_forecasting.projector import StatisticalProjector
from trax_io_reco.contracts.candidate import CandidateFrontier
from trax_io_reco.contracts.context import ScheduledDemandItem, TenantPolicyConfig
from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType
from trax_io_reco.contracts.recommendation import Recommendation
from trax_io_reco.contracts.repair import RepairReturnProfile
from trax_io_reco.data.extract_loader import build_stores_from_extract
from trax_io_reco.data.inventory_state import InMemoryInventoryState
from trax_io_reco.demand.basis import demand_basis_trace
from trax_io_reco.position.repair_pipeline import build_repair_pipeline
from trax_io_reco.regime.classifier import (
    classify,
    demanded_units_24mo_from,
    events_24mo_from,
)
from trax_io_reco.repair_returns import project_repair_returns
from trax_io_reco.service import RecommendationService

from trax_io_spine.bff.feeds import FEED_DEFINITIONS
from trax_io_spine.bff.models import (
    AccuracyPoint,
    ActionResult,
    Breakdown,
    BulkApproveFilter,
    DashboardSummary,
    DemandPoint,
    DemandSummary,
    FeedConnectionStatus,
    FeedHealthRow,
    FeedHealthStrip,
    FeedsSummary,
    ForecastAccuracy,
    ForecastSummary,
    LeadTimeView,
    MethodCoverage,
    MethodCoverageRow,
    OpenOrderView,
    PartAttributesView,
    PartContext,
    PartShortfall,
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
    StockBreakdown,
    SupplyCycleLaneView,
    TaskStatus,
    _EvidenceView,
    _PolicyView,
)
from trax_io_spine.bff.planning_trace import build_planning_trace
from trax_io_spine.bff.scenario import (
    KeyStats,
    RepairScenarioInput,
    ScenarioParams,
    ScenarioSolver,
    SolveResult,
    build_key_stats,
    build_repair_scenario_inputs,
)
from trax_io_spine.bvr.models import BvrReport
from trax_io_spine.bvr.report import KeyFacts, RecState, build_bvr_report
from trax_io_spine.contracts import (
    GuardrailOutcome,
    GuardrailStatus,
    HistoryEntry,
    RollbackRequest,
    RollbackResult,
)
from trax_io_spine.event_lane.adapters import BundleFeatureStore, BundleInventoryState
from trax_io_spine.guardrail.enforce import GuardrailEnforcer
from trax_io_spine.guardrail.messages import humanize_guardrail_codes
from trax_io_spine.planning_inputs import (
    PlanningInputSnapshot,
    planning_input_coverage,
    planning_input_model_profile,
    planning_input_source_generation_hash,
    planning_input_source_snapshot_hash,
)
from trax_io_spine.scenario_result import build_scenario_result
from trax_io_spine.supervisor import to_writeback_request
from trax_io_spine.writeback.target import InMemoryWritebackTarget

_REPAIR_RETURN_HORIZONS = (30, 60, 90)

# Regime-level fallback labels for legacy snapshots that did not persist served model
# identity. They describe only the distribution/path that can be proven from the v1
# contract; they must not claim an optional EB/Croston/GB implementation ran.
_REGIME_METHOD = {
    "ultra_rare": "Historical compound-Poisson",
    "intermittent": "Compound-Poisson (model identity unavailable)",
    "moderate": "Historical + scheduled (moving average)",
    "high_volume": "Historical + scheduled (moving average)",
}


class KillSwitchEngaged(Exception):  # noqa: N818
    """Raised when an approve/bulk-approve is attempted while the kill switch is engaged."""


class RecommendationNotFound(Exception):  # noqa: N818
    """Raised when a recommendation_id is unknown to this tenant's store."""


class ScenarioNotFound(Exception):  # noqa: N818
    """Raised when a scenario_id is unknown to this tenant's store."""


@dataclass
class _ScenarioEntry:
    scenario: Scenario


@dataclass
class _Entry:
    rec: Recommendation
    outcome: GuardrailOutcome
    status: TaskStatus
    reject_reason: str | None = None
    reject_detail: str = ""
    deferred_until: datetime | None = None


def _policy_view(p) -> _PolicyView | None:
    if p is None:
        return None
    return _PolicyView(rop=p.rop, eoq=p.eoq, safety_stock=p.safety_stock, max_stock=p.max_stock)


def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - feature groups may be absent; degrade to None
        return None


def _matching_supply_cycle(
    feature,
    *,
    tenant_id: str,
    pn: str,
    vendor: str,
    condition: Literal["NEW", "REP"],
):
    """Accept only the exact tenant/part/vendor/lane requested by this context.

    Feature clients normally enforce this identity themselves.  Rechecking at
    the BFF boundary prevents a corrupt adapter from turning another lane (or
    tenant) into planner-visible evidence.
    """

    if feature is None:
        return None
    actual = (
        getattr(feature, "tenant_id", None),
        getattr(feature, "pn", None),
        getattr(feature, "vendor", None),
        getattr(feature, "condition", None),
    )
    expected = (tenant_id, pn, vendor, condition)
    return feature if actual == expected else None


def _matching_part_location_feature(
    feature,
    *,
    tenant_id: str,
    pn: str,
    location: str,
):
    """Reject an explicitly mismatched keyed feature at the BFF boundary.

    Older test doubles and transitional adapters can omit identity attributes;
    those remain readable. Any identity that is present, however, must match
    the tenant-scoped route exactly.
    """

    if feature is None:
        return None
    expected = {
        "tenant_id": tenant_id,
        "pn": pn,
        "location": location,
    }
    for field_name, expected_value in expected.items():
        actual = getattr(feature, field_name, None)
        if actual is not None and str(actual) != expected_value:
            return None
    return feature


def _as_repair_date(value) -> date | None:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        try:
            return date.fromisoformat(raw[:10])
        except ValueError:
            return None
    return None


def _repair_pipeline_as_of(*, entry, open_orders, stock, manifest: dict) -> date | None:
    """Choose the newest trustworthy planning cutoff already carried by the read.

    Served calculation evidence wins. Legacy snapshots fall back through the
    selected recommendation and keyed source snapshots; no render-time "today"
    is invented when every source predates an explicit cutoff.
    """

    recommendation = getattr(entry, "rec", None)
    calculation = getattr(recommendation, "calculation_evidence", None)
    candidates = (
        getattr(calculation, "as_of", None),
        getattr(recommendation, "generated_at", None),
        getattr(open_orders, "snapshot_at", None),
        getattr(open_orders, "extract_date", None),
        getattr(stock, "extract_date", None),
        manifest.get("extract_date"),
    )
    return next(
        (parsed for value in candidates if (parsed := _as_repair_date(value)) is not None),
        None,
    )


def _repair_cycle_at_or_before(repair_cycle, *, as_of: date):
    """Reject REP evidence that would look past the immutable pipeline cutoff."""

    if repair_cycle is None:
        return None
    cutoff = _as_repair_date(
        getattr(repair_cycle, "data_cutoff", None)
        or getattr(repair_cycle, "extract_date", None)
    )
    return None if cutoff is not None and cutoff > as_of else repair_cycle


def _disclose_fallback_censoring(
    profile: RepairReturnProfile,
    *,
    repair_cycle_time=None,
) -> RepairReturnProfile:
    """Make aggregate-REP fallback semantics explicit on the served contract.

    Open-line ages still condition each return probability, but when the model
    is a REP quantile/promise fallback those ages did not fit the distribution.
    Only a true Kaplan-Meier model may claim right-censored observations were
    included in fitting.
    """

    payload = profile.model_dump(mode="json")
    if profile.evidence.method == "kaplan_meier":
        if repair_cycle_time is None:
            return profile
        payload["evidence"].update(
            {
                "source": (
                    f"{repair_cycle_time.source}+open_work_right_censoring"
                ),
                "data_cutoff": (
                    repair_cycle_time.data_cutoff.isoformat()
                    if repair_cycle_time.data_cutoff is not None
                    else None
                ),
                "model_version": (
                    f"{profile.evidence.model_version}+"
                    f"{repair_cycle_time.model_version}"
                ),
                "proxy_definition": repair_cycle_time.proxy_definition,
            }
        )
        return RepairReturnProfile.model_validate(payload)
    if profile.evidence.right_censored_observations == 0:
        return profile
    payload["warning_codes"] = sorted(
        {
            *profile.warning_codes,
            "repair_return_right_censoring_not_fitted",
        }
    )
    # This contract field means observations used as right-censoring inputs to
    # the fit. A fallback curve merely age-conditions open work; it must not
    # report those units as fitted censoring observations.
    payload["evidence"]["right_censored_observations"] = 0
    if profile.status == "available":
        payload["status"] = "partial"
    return RepairReturnProfile.model_validate(payload)


def _optional_text(source, *field_names: str) -> str | None:
    for field_name in field_names:
        value = getattr(source, field_name, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    return None


def _optional_iso(source, *field_names: str) -> str | None:
    for field_name in field_names:
        value = getattr(source, field_name, None)
        if value is None:
            continue
        if hasattr(value, "isoformat"):
            return str(value.isoformat())
        rendered = str(value).strip()
        if rendered:
            return rendered
    return None


def _supply_cycle_lane_view(
    feature,
    *,
    condition: Literal["NEW", "REP"],
) -> SupplyCycleLaneView:
    """Project one canonical feature without deriving or borrowing evidence."""

    lane_name = (
        "NEW procurement lead-time"
        if condition == "NEW"
        else "REP repair-cycle"
    )
    if feature is None:
        return SupplyCycleLaneView(
            condition=condition,
            status="unavailable",
            unavailable_reason=f"No {lane_name} evidence is available.",
        )
    if getattr(feature, "evidence_status", "legacy_unknown") == "legacy_unknown":
        return SupplyCycleLaneView(
            condition=condition,
            status="unavailable",
            unavailable_reason=(
                f"{lane_name} evidence predates trustworthy provenance."
            ),
        )

    status = getattr(feature, "evidence_status", None)
    if status not in {"observed", "configured_fallback"}:
        return SupplyCycleLaneView(
            condition=condition,
            status="unavailable",
            unavailable_reason=f"{lane_name} evidence has an unsupported status.",
        )

    proxy_label = None
    if condition == "REP":
        proxy_label = (
            "RO cycle-time proxy"
            if status == "observed"
            else "Configured repair promise"
        )
    try:
        return SupplyCycleLaneView(
            condition=condition,
            status=status,
            mean_days=feature.realized_mean_days,
            p50_days=feature.realized_p50_days,
            p90_days=feature.realized_p90_days,
            p99_days=feature.realized_p99_days,
            n_observations=feature.n_observations,
            source=feature.source,
            grouping_level=feature.grouping_level,
            confidence=feature.confidence,
            data_cutoff=(
                feature.data_cutoff.isoformat()
                if feature.data_cutoff is not None
                else None
            ),
            model_version=feature.model_version,
            classification_source=feature.classification_source,
            proxy_definition=feature.proxy_definition,
            proxy_label=proxy_label,
        )
    except (AttributeError, TypeError, ValueError):
        # A malformed or partially upgraded feature cannot be served as observed
        # evidence.  Preserve the lane boundary and fail closed.
        return SupplyCycleLaneView(
            condition=condition,
            status="unavailable",
            unavailable_reason=f"{lane_name} evidence failed contract validation.",
        )


def row_view(rec, outcome, status: TaskStatus, priority: float) -> QueueRow:
    return QueueRow(
        recommendation_id=rec.recommendation_id, pn=rec.part_number,
        location=rec.current_location, type=rec.type, criticality_tier=rec.criticality_tier,
        aog_risk_level=rec.aog_risk_level, confidence_score=rec.confidence_score,
        recommended_quantity=rec.recommended_quantity,
        estimated_cost_impact=rec.estimated_cost_impact, tier=outcome.tier,
        priority_score=priority, status=status,
        reason=rec.reason,
        approvable=rec.policy is not None,
        description=rec.description,
        current_stock=rec.current_stock,
        shortage_quantity=rec.shortage_quantity,
        recommended_location=rec.recommended_location,
        horizon_days=rec.horizon_days,
    )


def detail_view(rec, outcome, status: TaskStatus) -> RecommendationDetail:
    return RecommendationDetail(
        recommendation_id=rec.recommendation_id, pn=rec.part_number,
        location=rec.current_location, type=rec.type, criticality_tier=rec.criticality_tier,
        aog_risk_level=rec.aog_risk_level, confidence_score=rec.confidence_score,
        recommended_quantity=rec.recommended_quantity,
        estimated_cost_impact=rec.estimated_cost_impact, tier=outcome.tier,
        status=status, reason=rec.reason,
        provenance_id=rec.policy.provenance_id if rec.policy else None,
        projected_demand=rec.projected_demand,
        current_policy=_policy_view(rec.current_policy),
        proposed_policy=_policy_view(rec.policy),
        supporting_evidence=tuple(
            _EvidenceView(
                kind=str(e.kind), ref_id=e.ref_id, detail=e.detail,
                as_of=e.as_of.isoformat() if e.as_of else None,
            )
            for e in rec.supporting_evidence
        ),
        guardrail_flags=rec.guardrail_flags,
        guardrail_notes=humanize_guardrail_codes(outcome.reasons),
        description=rec.description,
        current_stock=rec.current_stock,
        shortage_quantity=rec.shortage_quantity,
        recommended_location=rec.recommended_location,
        horizon_days=rec.horizon_days,
    )


def _read_manifest(extract_dir: str) -> dict:
    """Tolerant manifest read — mirrors `extract_loader.build_stores_from_extract`'s own
    manifest handling exactly (missing file -> `{}`, corrupt JSON -> `{}` + log, never
    raises). `build_stores_from_extract` already parses this file internally but
    discards it once it has `tenant_id`/`extract_date`; Slice S7 needs the per-domain
    `artifacts` list too, so the store re-reads it once at seed time rather than
    threading a new return value through the loader's public contract."""
    path = Path(extract_dir) / "manifest.json"
    if not path.exists():
        return {}
    try:
        manifest = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    return manifest if isinstance(manifest, dict) else {}


def _load_candidate_frontiers(path: Path | None) -> tuple[CandidateFrontier, ...]:
    """Load the additive candidate artifact.

    ``None`` (or an auto-discovered path that does not exist) is the intentional
    legacy value: old recommendation snapshots predate candidate planning and must
    remain bootable without fabricating options.
    """
    if path is None or not path.exists():
        return ()
    raw = json.loads(path.read_text())
    if not isinstance(raw, list):
        raise ValueError(f"invalid candidate frontier snapshot in {path}: expected a list")
    return tuple(CandidateFrontier.model_validate(item) for item in raw)


def _load_scheduled_demand_snapshot(
    snapshot_dir: Path, *, tenant_id: str
) -> InMemoryInventoryState | None:
    """Load the additive forward-demand artifact; absence means legacy/unavailable."""
    path = snapshot_dir / "scheduled_demand.json"
    if not path.exists():
        return None
    raw = json.loads(path.read_text())
    snapshot_format = raw.get("format") if isinstance(raw, dict) else None
    if snapshot_format not in {1, 2}:
        raise ValueError(
            f"unsupported scheduled_demand snapshot in {path} (expected format 1 or 2)"
        )
    entries = raw.get("entries")
    if not isinstance(entries, list):
        raise ValueError(f"invalid scheduled_demand snapshot entries in {path}")

    inventory_state = InMemoryInventoryState()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError(f"invalid scheduled_demand snapshot entry in {path}")
        pn, location, items_raw = (
            entry.get("pn"),
            entry.get("location"),
            entry.get("items"),
        )
        if not isinstance(pn, str) or not isinstance(location, str):
            raise ValueError(f"invalid scheduled_demand snapshot key in {path}")
        if not isinstance(items_raw, list):
            raise ValueError(f"invalid scheduled_demand snapshot items in {path}")
        if snapshot_format == 1 and not items_raw:
            raise ValueError(
                f"scheduled_demand format 1 persists only non-empty items in {path}"
            )
        items = tuple(ScheduledDemandItem.model_validate(item) for item in items_raw)
        inventory_state.seed(
            tenant_id, "scheduled_demand", (pn, location), items
        )
    return inventory_state


@dataclass
class PlannerStore:
    tenant_id: str
    writeback: InMemoryWritebackTarget = field(default_factory=InMemoryWritebackTarget)
    kill_switch: bool = False
    _entries: dict[str, _Entry] = field(default_factory=dict)
    _candidate_frontiers: dict[tuple[str, str], CandidateFrontier] = field(
        default_factory=dict,
        repr=False,
    )
    fs: object | None = None
    inventory_state: object | None = None
    tenant: object | None = None
    keys: list = field(default_factory=list)
    # Slice S6 — What-If Scenarios: lazily-built, memoized per-key demand/lead-time/
    # cost primitives (built once from `fs`/`keys`, reused across every solve — see
    # `bff/scenario.py` module docstring) + the in-memory saved-scenario repo.
    _key_stats_cache: list[KeyStats] | None = field(default=None, repr=False)
    _repair_scenario_inputs_cache: list[RepairScenarioInput] | None = field(
        default=None,
        repr=False,
    )
    _scenarios: dict[str, _ScenarioEntry] = field(default_factory=dict)
    _audit_log: list[ScenarioAuditEvent] = field(default_factory=list)
    # Slice S8 — BVR: memoized Business Value Report, invalidated by every decision
    # action (approve/reject/defer/bulk_approve/rollback) so it always reflects the
    # current lifecycle state. See `bvr()` below.
    _bvr_cache: BvrReport | None = field(default=None, repr=False)
    # Slice S7 — Data & Connections: the seeded extract's manifest, retained verbatim
    # (empty dict when the extract dir has no manifest.json, or it's corrupt/unreadable
    # — degrade gracefully rather than fail store construction over an optional file).
    # Additive-only field with a byte-compatible default; `from_extract`/`from_snapshot`
    # keep working unchanged for every existing caller that doesn't care about feeds.
    _manifest: dict = field(default_factory=dict, repr=False)
    _planning_input_snapshot_cache: PlanningInputSnapshot | None = field(
        default=None,
        repr=False,
    )

    @classmethod
    def from_extract(
        cls, *, tenant_id: str, extract_dir: str, now: datetime,
        writeback: InMemoryWritebackTarget | None = None,
        pool_by_part: bool = False,
        use_statistical: bool = False,
        as_of: date | None = None,
    ) -> PlannerStore:
        # pool_by_part: network-pooled on-hand/demand for real eMRO extracts (where
        # policies key at planning locations but stock lives at physical ones). Off by
        # default so the committed sample loads per-location exactly as before.
        # use_statistical: inject #5's StatisticalProjector (Croston/SBA/TSB) for the
        # intermittent regime instead of the deterministic HistoricalScheduledProjector
        # default. Off by default so existing behavior/tests are unchanged.
        fs, inv, tid, keys = build_stores_from_extract(
            extract_dir, tenant_id=tenant_id, pool_by_part=pool_by_part
        )
        tenant = TenantContext(tenant_id=tid)
        projector = StatisticalProjector() if use_statistical else None
        preview = RecommendationService(
            feature_store=fs, inventory_state=inv, projector=projector
        ).run_with_frontiers(
            tenant=tenant,
            keys=keys,
            now=now,
            as_of=as_of,
        )
        return cls._build(
            fs=fs, tenant=tenant, keys=keys,
            recommendations=preview.recommendation_batch.recommendations,
            candidate_frontiers=preview.frontiers,
            writeback=writeback,
            inventory_state=inv,
            manifest=_read_manifest(extract_dir),
        )

    @classmethod
    def from_online(
        cls,
        *,
        tenant_id: str,
        online_store,
        keys,
        now: datetime,
        writeback: InMemoryWritebackTarget | None = None,
        use_statistical: bool = False,
        as_of: date | None = None,
        manifest: dict | None = None,
        generation: OnlineGeneration | None = None,
    ) -> PlannerStore:
        """Build the serving store from already-populated online feature bundles.

        This is the native-connector counterpart to :meth:`from_extract`. The
        data-side population job owns Iceberg -> online materialization; the BFF
        only reads those committed bundles and therefore never writes AWS state at
        boot. Every read carries the explicit tenant context, and the returned
        bundle identity is checked before it can enter recommendation or BFF
        assembly.
        """

        tenant = TenantContext(tenant_id=tenant_id)
        if generation is not None and generation.tenant_id != tenant_id:
            raise FeatureStoreLookupError(
                "online generation tenant mismatch "
                f"expected={tenant_id!r} actual={generation.tenant_id!r}"
            )
        requested_keys = list(keys)
        bundles = {}
        for raw_key in requested_keys:
            if (
                not isinstance(raw_key, (list, tuple))
                or len(raw_key) != 2
                or not all(isinstance(value, str) and value for value in raw_key)
            ):
                raise ValueError(f"invalid online planning key: {raw_key!r}")
            pn, location = raw_key
            get_bundle_kwargs = {
                "tenant": tenant,
                "pn": pn,
                "location": location,
            }
            if generation is not None:
                get_bundle_kwargs["generation"] = generation
            bundle = online_store.get_bundle(
                **get_bundle_kwargs,
            )
            actual = (bundle.tenant_id, bundle.pn, bundle.location)
            expected = (tenant_id, pn, location)
            if actual != expected:
                raise FeatureStoreLookupError(
                    "online bundle identity mismatch "
                    f"expected={expected!r} actual={actual!r}"
                )
            bundles[(pn, location)] = bundle

        feature_store = BundleFeatureStore(tenant_id, bundles)
        inventory_state = BundleInventoryState(tenant_id, bundles)
        projector = StatisticalProjector() if use_statistical else None
        preview = RecommendationService(
            feature_store=feature_store,
            inventory_state=inventory_state,
            projector=projector,
        ).run_with_frontiers(
            tenant=tenant,
            keys=[tuple(key) for key in requested_keys],
            now=now,
            as_of=as_of,
        )
        return cls._build(
            fs=feature_store,
            tenant=tenant,
            keys=requested_keys,
            recommendations=preview.recommendation_batch.recommendations,
            candidate_frontiers=preview.frontiers,
            writeback=writeback,
            inventory_state=inventory_state,
            manifest=manifest,
        )

    @classmethod
    def from_snapshot(
        cls, *, tenant_id: str, extract_dir: str, recs_file: str, now: datetime,
        writeback: InMemoryWritebackTarget | None = None,
        pool_by_part: bool = False,
        frontiers_file: str | None = None,
    ) -> PlannerStore:
        """Fast boot path: rebuild the feature/inventory stores from the extract (cheap —
        JSON parsing, no `RecommendationService.run`) and load precomputed recommendations
        from `recs_file` (written by `bff/precompute.py`) instead of recomputing them.

        `now` is accepted for interface symmetry with `from_extract` (the recommendations
        were already generated against a fixed `now` at precompute time) but is otherwise
        unused here — the recs are loaded as-is.
        """
        del now  # recommendations already carry their own generated_at from precompute
        fs, inv, tid, keys = build_stores_from_extract(
            extract_dir, tenant_id=tenant_id, pool_by_part=pool_by_part
        )
        tenant = TenantContext(tenant_id=tid)
        raw = json.loads(Path(recs_file).read_text())
        recommendations = [Recommendation.model_validate(obj) for obj in raw]
        candidate_path = (
            Path(frontiers_file)
            if frontiers_file is not None
            else Path(recs_file).with_name("frontiers.json")
        )
        candidate_frontiers = _load_candidate_frontiers(candidate_path)
        return cls._build(
            fs=fs, tenant=tenant, keys=keys,
            recommendations=recommendations,
            candidate_frontiers=candidate_frontiers,
            writeback=writeback,
            inventory_state=inv,
            manifest=_read_manifest(extract_dir),
        )

    @classmethod
    def from_snapshot_dir(
        cls, *, tenant_id: str, snapshot_dir: str,
        writeback: InMemoryWritebackTarget | None = None,
    ) -> PlannerStore:
        """Fastest boot path: load the COMPLETE snapshot dir written by
        `bff/precompute.py` — the built (pooled) feature store, keys universe,
        manifest, and recommendations. No extract parsing, no pooling, and no
        engine run at boot; the extract dir is not needed at all (spec:
        docs/superpowers/specs/2026-07-02-fast-boot-feature-store-snapshot-design.md).

        Fail-loud by design: a missing artifact, a tenant mismatch, or feature-model
        schema drift (snapshot written by an older package version) raises rather
        than silently falling back to the slow path — unset PLANNER_SNAPSHOT_DIR to
        boot the old way.
        """
        sd = Path(snapshot_dir)
        for artifact in ("meta.json", "feature_store.json", "keys.json", "recs.json"):
            if not (sd / artifact).exists():
                raise FileNotFoundError(f"snapshot dir {snapshot_dir} is missing {artifact}")
        meta = json.loads((sd / "meta.json").read_text())
        if meta.get("tenant") != tenant_id:
            raise ValueError(
                f"snapshot tenant {meta.get('tenant')!r} does not match "
                f"requested tenant {tenant_id!r}"
            )
        if meta.get("snapshot_format") != 1:
            raise ValueError(
                f"unsupported snapshot_format {meta.get('snapshot_format')!r} "
                f"in {snapshot_dir}/meta.json (expected 1)"
            )
        fs = load_store(sd / "feature_store.json")
        keys = [tuple(k) for k in json.loads((sd / "keys.json").read_text())]
        recommendations = [
            Recommendation.model_validate(obj)
            for obj in json.loads((sd / "recs.json").read_text())
        ]
        candidate_frontiers = _load_candidate_frontiers(sd / "frontiers.json")
        inventory_state = _load_scheduled_demand_snapshot(sd, tenant_id=tenant_id)
        return cls._build(
            fs=fs, tenant=TenantContext(tenant_id=tenant_id), keys=keys,
            recommendations=recommendations,
            candidate_frontiers=candidate_frontiers,
            writeback=writeback,
            inventory_state=inventory_state,
            manifest=_read_manifest(str(sd)),  # tolerant: absent manifest -> {} (feeds degrade)
        )

    @classmethod
    def _build(
        cls, *, fs, tenant: TenantContext, keys: list[tuple[str, str]],
        recommendations, writeback: InMemoryWritebackTarget | None,
        candidate_frontiers=(),
        inventory_state=None,
        manifest: dict | None = None,
    ) -> PlannerStore:
        recommendations = tuple(recommendations)
        wrong_recommendations = [
            recommendation.recommendation_id
            for recommendation in recommendations
            if recommendation.tenant_id != tenant.tenant_id
        ]
        if wrong_recommendations:
            raise ValueError(
                "recommendation tenant mismatch for requested tenant "
                f"{tenant.tenant_id!r}: {wrong_recommendations!r}"
            )

        normalized_keys: list[tuple[str, str]] = []
        for raw_key in keys:
            if (
                not isinstance(raw_key, (list, tuple))
                or len(raw_key) != 2
                or not all(isinstance(value, str) and value for value in raw_key)
            ):
                raise ValueError(f"invalid planning key in snapshot: {raw_key!r}")
            normalized_keys.append((raw_key[0], raw_key[1]))
        if len(set(normalized_keys)) != len(normalized_keys):
            raise ValueError("duplicate planning key in snapshot")

        candidate_frontiers = tuple(candidate_frontiers)
        frontiers_by_key: dict[tuple[str, str], CandidateFrontier] = {}
        for frontier in candidate_frontiers:
            if frontier.tenant_id != tenant.tenant_id:
                raise ValueError(
                    "candidate frontier tenant mismatch for requested tenant "
                    f"{tenant.tenant_id!r}: {frontier.frontier_fingerprint!r}"
                )
            candidate_keys = {
                (candidate.pn, candidate.location)
                for candidate in frontier.candidates
            }
            if len(candidate_keys) != 1:
                raise ValueError(
                    "candidate frontier must contain one part/location decision key: "
                    f"{frontier.frontier_fingerprint!r}"
                )
            candidate_key = next(iter(candidate_keys))
            if frontier.decision_key != f"{candidate_key[0]}@{candidate_key[1]}":
                raise ValueError(
                    "candidate frontier decision key does not match candidate part/location: "
                    f"{frontier.frontier_fingerprint!r}"
                )
            if candidate_key not in normalized_keys:
                raise ValueError(
                    "candidate frontier decision key is outside the planning-key universe: "
                    f"{candidate_key!r}"
                )
            if candidate_key in frontiers_by_key:
                raise ValueError(f"duplicate candidate frontier for planning key {candidate_key!r}")
            frontiers_by_key[candidate_key] = frontier

        feature_data = getattr(fs, "_data", None)
        if isinstance(feature_data, dict):
            feature_tenants = set(feature_data)
            if feature_tenants != {tenant.tenant_id}:
                raise ValueError(
                    "feature snapshot tenant mismatch: "
                    f"expected {tenant.tenant_id!r}, found {sorted(feature_tenants)!r}"
                )
            for buckets in feature_data.values():
                for entries in buckets.values():
                    for value in entries.values():
                        value_tenant = getattr(value, "tenant_id", tenant.tenant_id)
                        if value_tenant != tenant.tenant_id:
                            raise ValueError(
                                "feature value tenant mismatch: "
                                f"expected {tenant.tenant_id!r}, found {value_tenant!r}"
                            )
            stock_keys = set(
                feature_data[tenant.tenant_id].get("stock_position", {})
            )
            missing_stock = set(normalized_keys) - stock_keys
            if missing_stock:
                raise ValueError(
                    "planning keys missing from feature snapshot stock_position: "
                    f"{sorted(missing_stock)!r}"
                )

        inventory_data = getattr(inventory_state, "_data", None)
        if isinstance(inventory_data, dict):
            wrong_inventory_tenants = {
                storage_key[0]
                for storage_key in inventory_data
                if isinstance(storage_key, tuple)
                and storage_key
                and storage_key[0] != tenant.tenant_id
            }
            if wrong_inventory_tenants:
                raise ValueError(
                    "inventory snapshot tenant mismatch: "
                    f"{sorted(wrong_inventory_tenants)!r}"
                )

        store = cls(tenant_id=tenant.tenant_id, writeback=writeback or InMemoryWritebackTarget())
        store.fs = fs
        store.inventory_state = inventory_state
        store.tenant = tenant
        store.keys = normalized_keys
        store._candidate_frontiers = frontiers_by_key
        store._manifest = manifest or {}
        enforcer = GuardrailEnforcer()
        for rec in recommendations:
            store._ingest(rec, enforcer.enforce(rec))
        return store

    def _ingest(self, rec: Recommendation, outcome: GuardrailOutcome) -> None:
        if outcome.status is GuardrailStatus.QUEUED_FOR_APPROVAL:
            self._entries[rec.recommendation_id] = _Entry(rec, outcome, TaskStatus.PENDING)
        elif outcome.status is GuardrailStatus.DEFERRED_OPEN_ORDER:
            self._entries[rec.recommendation_id] = _Entry(rec, outcome, TaskStatus.DEFERRED)
        elif outcome.status is GuardrailStatus.APPROVED_FOR_WRITE:
            self.writeback.write(self._req(rec, outcome))
            self._entries[rec.recommendation_id] = _Entry(rec, outcome, TaskStatus.APPROVED)
        else:  # REJECTED_HARD_GUARDRAIL
            self._entries[rec.recommendation_id] = _Entry(rec, outcome, TaskStatus.REJECTED)

    def _req(self, rec: Recommendation, outcome: GuardrailOutcome):
        idem = f"{rec.tenant_id}:{rec.part_number}:{rec.current_location}:{rec.input_snapshot_hash}"
        return to_writeback_request(rec, idempotency_key=idem, tier=outcome.tier)

    def _get(self, rec_id: str) -> _Entry:
        entry = self._entries.get(rec_id)
        if entry is None:
            raise RecommendationNotFound(rec_id)
        return entry

    @staticmethod
    def _priority(entry: _Entry) -> float:
        return entry.outcome.approval_task.priority_score if entry.outcome.approval_task else 0.0

    def _row(self, entry: _Entry) -> QueueRow:
        return row_view(entry.rec, entry.outcome, entry.status, self._priority(entry))

    def set_kill_switch(self, engaged: bool) -> None:
        self.kill_switch = engaged
        self._bvr_cache = None

    def approve(self, rec_id: str) -> ActionResult:
        if self.kill_switch:
            raise KillSwitchEngaged(self.tenant_id)
        self._bvr_cache = None
        entry = self._get(rec_id)
        if entry.status is not TaskStatus.PENDING:
            raise ValueError(
                f"recommendation {rec_id} is {entry.status.value}, not pending approval"
            )
        if entry.rec.policy is None:
            raise ValueError(f"recommendation {rec_id} has no writable policy")
        result = self.writeback.write(self._req(entry.rec, entry.outcome))
        entry.status = TaskStatus.APPROVED
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.APPROVED, writeback=result,
            message=f"written ({result.status.value})",
        )

    def reject(self, rec_id: str, reason: RejectReason, detail: str = "") -> ActionResult:
        self._bvr_cache = None
        entry = self._get(rec_id)
        entry.status = TaskStatus.REJECTED
        entry.reject_reason = reason.value
        entry.reject_detail = detail
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.REJECTED, message=reason.value
        )

    def defer(self, rec_id: str, until: datetime | None = None) -> ActionResult:
        self._bvr_cache = None
        entry = self._get(rec_id)
        entry.status = TaskStatus.DEFERRED
        entry.deferred_until = until
        return ActionResult(
            recommendation_id=rec_id, status=TaskStatus.DEFERRED, message="deferred"
        )

    def _matches(self, entry: _Entry, f: BulkApproveFilter) -> bool:
        if f.tiers is not None and entry.outcome.tier not in f.tiers:
            return False
        if f.max_delta_pct is not None and entry.outcome.delta_pct > f.max_delta_pct:
            return False
        if f.criticality_min is not None and entry.rec.criticality_tier < f.criticality_min:
            return False
        return f.types is None or entry.rec.type in f.types

    def bulk_approve(self, filter: BulkApproveFilter) -> tuple[int, list[ActionResult]]:
        if self.kill_switch:
            raise KillSwitchEngaged(self.tenant_id)
        self._bvr_cache = None
        targets = [
            rid for rid, e in self._entries.items()
            if e.status is TaskStatus.PENDING
            and e.rec.policy is not None
            and self._matches(e, filter)
        ]
        results = [self.approve(rid) for rid in targets]
        return len(results), results

    def history(self, *, pn: str, location: str) -> tuple[HistoryEntry, ...]:
        return self.writeback.get_history(tenant_id=self.tenant_id, pn=pn, location=location)

    def rollback(self, req: RollbackRequest) -> RollbackResult:
        self._bvr_cache = None
        return self.writeback.rollback(req)

    # Sort-key extractors for `_sorted_entries` — one per `QueueSortKey` member.
    # `estimated_cost_impact` is a `Decimal` on the underlying `Recommendation`; cast to
    # `float` so it sorts against the other (already-float) keys with the same semantics
    # a numeric `sort()` key expects.
    _SORT_KEY_FNS = {
        QueueSortKey.PRIORITY: lambda self, e: self._priority(e),
        QueueSortKey.COST_IMPACT: lambda self, e: float(e.rec.estimated_cost_impact),
        QueueSortKey.CONFIDENCE: lambda self, e: e.rec.confidence_score,
        QueueSortKey.CRITICALITY: lambda self, e: e.rec.criticality_tier,
    }

    def _sorted_entries(
        self,
        *,
        status: TaskStatus,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY,
        sort_dir: str = "desc",
        tier: AutonomyTier | None = None,
        type_: RecommendationType | None = None,
        aog_min: AogRiskLevel | None = None,
    ) -> list[_Entry]:
        # Filter first, then a stable two-pass sort: recommendation_id ASC is ALWAYS
        # applied first (as the tie-break), then the requested sort key on top of it —
        # so paging stays deterministic across requests even when many entries share
        # the same sort-key value (defaults reproduce the original priority-desc,
        # id-tie-break ordering exactly).
        entries = [e for e in self._entries.values() if e.status is status]
        if tier is not None:
            entries = [e for e in entries if e.outcome.tier == tier]
        if type_ is not None:
            entries = [e for e in entries if e.rec.type == type_]
        if aog_min is not None:
            entries = [e for e in entries if e.rec.aog_risk_level >= aog_min]

        entries.sort(key=lambda e: e.rec.recommendation_id)
        key_fn = self._SORT_KEY_FNS[sort_by]
        entries.sort(key=lambda e: key_fn(self, e), reverse=(sort_dir == "desc"))
        return entries

    def queue(self, *, status: TaskStatus = TaskStatus.PENDING, limit: int = 50) -> list[QueueRow]:
        entries = self._sorted_entries(status=status)
        return [self._row(e) for e in entries[:limit]]

    def list_queue_page(
        self,
        *,
        status: TaskStatus = TaskStatus.PENDING,
        limit: int = 50,
        offset: int = 0,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY,
        sort_dir: str = "desc",
        tier: AutonomyTier | None = None,
        type_: RecommendationType | None = None,
        aog_min: AogRiskLevel | None = None,
    ) -> tuple[list[QueueRow], int]:
        """Paged queue query: full filtered+sorted set, sliced to one page + its total.

        Free-text search intentionally stays client-side over the loaded page for now
        — not implemented server-side in this task. Sort (`sort_by`/`sort_dir`) and
        filter (`tier`/`type_`/`aog_min`) are server-side (task F2); every new keyword
        defaults to reproducing the pre-F2 behavior byte-for-byte.
        """
        entries = self._sorted_entries(
            status=status,
            sort_by=sort_by,
            sort_dir=sort_dir,
            tier=tier,
            type_=type_,
            aog_min=aog_min,
        )
        page = entries[offset : offset + limit]
        return [self._row(e) for e in page], len(entries)

    def list_queue_all(
        self,
        *,
        status: TaskStatus = TaskStatus.PENDING,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY,
        sort_dir: str = "desc",
        tier: AutonomyTier | None = None,
        type_: RecommendationType | None = None,
        aog_min: AogRiskLevel | None = None,
    ) -> list[QueueRow]:
        """Full filtered+sorted queue with NO pagination — every matching row.

        Backs the CSV export route (which must cover the whole filtered set, not
        one page). Shares `_sorted_entries` with `list_queue_page` so filter/sort
        semantics are identical; the only difference is the absence of a slice.
        """
        entries = self._sorted_entries(
            status=status,
            sort_by=sort_by,
            sort_dir=sort_dir,
            tier=tier,
            type_=type_,
            aog_min=aog_min,
        )
        return [self._row(e) for e in entries]

    def detail(self, rec_id: str) -> RecommendationDetail:
        entry = self._get(rec_id)
        return detail_view(entry.rec, entry.outcome, entry.status)

    def planning_input_snapshot(
        self,
        keys: tuple[tuple[str, str], ...] | None = None,
    ) -> PlanningInputSnapshot:
        """Return one immutable planning-input read through the store boundary."""

        if keys is None and self._planning_input_snapshot_cache is not None:
            return self._planning_input_snapshot_cache

        if keys is None:
            requested_keys = tuple(
                sorted(
                    (tuple(key) for key in self.keys),
                    key=lambda key: f"{key[0]}@{key[1]}",
                )
            )
        else:
            requested: list[tuple[str, str]] = []
            for raw_key in keys:
                if (
                    not isinstance(raw_key, (tuple, list))
                    or len(raw_key) != 2
                    or not all(
                        isinstance(value, str) and value
                        for value in raw_key
                    )
                ):
                    raise ValueError(
                        "planning input keys must be non-empty part/location pairs"
                    )
                requested.append((raw_key[0], raw_key[1]))
            if len(requested) != len(set(requested)):
                raise ValueError("planning input keys must be unique")
            requested_keys = tuple(requested)

        known_keys = {tuple(key) for key in self.keys}
        if any(key not in known_keys for key in requested_keys):
            raise RecommendationNotFound("planning input key is unavailable")

        contexts = tuple(
            self.part_context(pn, location)
            for pn, location in requested_keys
        )
        if keys is None:
            eligible_contexts = tuple(
                context
                for context in contexts
                if context.candidate_frontier is not None
            )
            coverage = planning_input_coverage(
                contexts,
                total_key_count=len(requested_keys),
                returned_key_count=len(eligible_contexts),
            )
            source_snapshot_hash = planning_input_source_snapshot_hash(
                contexts,
                coverage=coverage,
            )
            snapshot = PlanningInputSnapshot(
                contexts=eligible_contexts,
                source_snapshot_hash=source_snapshot_hash,
                source_generation_hash=planning_input_source_generation_hash(
                    source_snapshot_hash
                ),
                coverage=coverage,
                seeded_at=None,
            )
            self._planning_input_snapshot_cache = snapshot
            return snapshot

        coverage = planning_input_coverage(contexts)
        generation = self.current_planning_source_generation_hash()
        if generation is None:  # pragma: no cover - full snapshot always computes
            raise RuntimeError("planning source generation is unavailable")
        return PlanningInputSnapshot(
            contexts=contexts,
            source_snapshot_hash=planning_input_source_snapshot_hash(contexts),
            source_generation_hash=generation,
            coverage=coverage,
            seeded_at=None,
        )

    def current_planning_source_snapshot_hash(self) -> str | None:
        """Return a cached full-scope marker without scanning the key universe."""

        snapshot = self._planning_input_snapshot_cache
        return snapshot.source_snapshot_hash if snapshot is not None else None

    def current_planning_source_generation_hash(self) -> str | None:
        """Return the cached or freshly computed full-universe generation."""

        snapshot = self._planning_input_snapshot_cache
        if snapshot is None:
            snapshot = self.planning_input_snapshot()
        return snapshot.source_generation_hash

    def current_planning_model_profile(self) -> dict[str, str]:
        """Return the current in-memory trusted candidate/model versions."""

        snapshot = self._planning_input_snapshot_cache
        if snapshot is None:
            snapshot = self.planning_input_snapshot()
        return planning_input_model_profile(snapshot.contexts)

    def part_context(
        self,
        pn: str,
        location: str,
        recommendation_id: str | None = None,
    ) -> PartContext:
        if (pn, location) not in self.keys:
            raise RecommendationNotFound(f"{pn}/{location}")
        t = self.tenant
        attrs = _safe(lambda: self.fs.get_part_attributes(tenant=t, pn=pn))
        crit = _safe(lambda: self.fs.get_criticality(tenant=t, pn=pn))
        sp = _matching_part_location_feature(
            _safe(lambda: self.fs.get_stock_position(tenant=t, pn=pn, location=location)),
            tenant_id=self.tenant_id,
            pn=pn,
            location=location,
        )
        cp = _safe(lambda: self.fs.get_current_policy(tenant=t, pn=pn, location=location))
        lt_new_raw = _safe(
            lambda: self.fs.get_lead_time_distribution(
                tenant=t, pn=pn, vendor="DEFAULT", condition="NEW"
            )
        )
        lt_rep_raw = _safe(
            lambda: self.fs.get_lead_time_distribution(
                tenant=t, pn=pn, vendor="DEFAULT", condition="REP"
            )
        )
        lt_new = _matching_supply_cycle(
            lt_new_raw,
            tenant_id=self.tenant_id,
            pn=pn,
            vendor="DEFAULT",
            condition="NEW",
        )
        lt_rep = _matching_supply_cycle(
            lt_rep_raw,
            tenant_id=self.tenant_id,
            pn=pn,
            vendor="DEFAULT",
            condition="REP",
        )
        oo = _matching_part_location_feature(
            _safe(
                lambda: self.fs.get_open_orders_snapshot(
                    tenant=t, pn=pn, location=location
                )
            ),
            tenant_id=self.tenant_id,
            pn=pn,
            location=location,
        )
        dh = _safe(lambda: self.fs.get_demand_history(tenant=t, pn=pn, location=location))
        ve = _safe(lambda: self.fs.get_vendor_economics(tenant=t, pn=pn, vendor="DEFAULT"))
        scheduled = (
            _safe(
                lambda: self.inventory_state.get_scheduled_demand(
                    tenant=t, pn=pn, location=location
                )
            )
            if self.inventory_state is not None
            else None
        )
        scheduled_status_reader = (
            getattr(self.inventory_state, "get_scheduled_demand_status", None)
            if self.inventory_state is not None
            else None
        )
        scheduled_status = (
            _safe(
                lambda: scheduled_status_reader(
                    tenant=t,
                    pn=pn,
                    location=location,
                )
            )
            if callable(scheduled_status_reader)
            else ("available" if scheduled else "unavailable")
        )
        # Snapshot-format v2 and modern providers distinguish a successful,
        # observed-empty feed from an unavailable source. Preserve that signal
        # for legacy recommendations instead of collapsing both to tuple
        # truthiness. Partial/unknown coverage remains conservatively unavailable.
        scheduled_for_trace = (
            tuple(scheduled or ()) if scheduled_status == "available" else None
        )
        matches = [
            e
            for e in self._entries.values()
            if e.rec.part_number == pn and e.rec.current_location == location
        ]
        # One key may have an Adjust-Min/Max recommendation plus a higher-ranked
        # transfer/purchase/sell recommendation. The latter has no proposed policy,
        # so insertion/ranking order is not a truthful source for part-context policy
        # selection. Prefer a policy-carrying recommendation, then use only persisted
        # fields for the deterministic no-query fallback. An explicit recommendation
        # id selects only the trace; proposed-policy selection remains independent.
        policy_entry = min(
            matches,
            key=lambda e: (
                e.rec.policy is None,
                e.rec.type.value,
                e.rec.horizon_days,
                e.rec.recommendation_id,
            ),
            default=None,
        )
        if recommendation_id is None:
            trace_entry = policy_entry
        else:
            trace_entry = self._entries.get(recommendation_id)
            if trace_entry is None or (
                trace_entry.rec.part_number,
                trace_entry.rec.current_location,
            ) != (pn, location):
                # One tenant's PlannerStore never contains another tenant's
                # recommendation. Use the same not-found response for an unknown id
                # and a key mismatch so selection cannot become an identifier oracle.
                raise RecommendationNotFound(f"{pn}/{location}")
        repair_as_of = _repair_pipeline_as_of(
            # recommendation_id selects only the calculation trace. Keep the
            # physical repair snapshot invariant across recommendation views
            # (and therefore identical to the persisted default context).
            entry=policy_entry,
            open_orders=oo,
            stock=sp,
            manifest=self._manifest,
        )
        repair_pipeline = (
            _safe(
                lambda: build_repair_pipeline(
                    tenant_id=self.tenant_id,
                    part_number=pn,
                    location_code=location,
                    open_orders=oo,
                    aggregate_wip_quantity=int(sp.unserviceable_in_repair),
                    as_of=repair_as_of,
                )
            )
            # The aggregate stock-position WIP is a required reconciliation
            # source. Missing stock is unknown, never an observed zero.
            if repair_as_of is not None and sp is not None
            else None
        )
        part_class = str(getattr(attrs, "part_class", "") or "").lower()
        repair_cycle_time = (
            _repair_cycle_at_or_before(
                lt_rep,
                as_of=repair_pipeline.as_of,
            )
            if repair_pipeline is not None
            else None
        )
        repair_return_profile = (
            _safe(
                lambda: _disclose_fallback_censoring(
                    project_repair_returns(
                        pipeline=repair_pipeline,
                        horizons=_REPAIR_RETURN_HORIZONS,
                        # Only the additive raw REP carrier can activate
                        # Kaplan-Meier. Legacy distributions retain an empty
                        # carrier and fall back without fabricating durations.
                        completed_cycle_days=(
                            repair_cycle_time.observed_cycle_days
                            if repair_cycle_time is not None
                            else ()
                        ),
                        repair_cycle_time=repair_cycle_time,
                    ),
                    repair_cycle_time=repair_cycle_time,
                )
            )
            if repair_pipeline is not None
            and part_class in {"repairable", "rotable"}
            else None
        )
        return PartContext(
            pn=pn,
            location=location,
            attributes=PartAttributesView(
                description=(attrs.description if attrs and attrs.description else pn),
                ata_chapter=attrs.ata_chapter if attrs else None,
                part_class=attrs.part_class if attrs else None,
                shelf_life_days=attrs.shelf_life_days if attrs else None,
                hazardous_material=bool(attrs and attrs.hazardous_material),
                tool_control_item=bool(attrs and attrs.tool_control_item),
                criticality_tier=crit.canonical_tier if crit else None,
            ),
            stock=(
                StockBreakdown(
                    on_hand=sp.on_hand,
                    serviceable=sp.serviceable,
                    in_repair=sp.unserviceable_in_repair,
                    allocated=sp.allocated_reserved,
                    rental=sp.rental,
                    loan=sp.loan,
                )
                if sp
                else None
            ),
            current_policy=_policy_view(cp) if cp else None,
            proposed_policy=(
                _policy_view(policy_entry.rec.policy)
                if policy_entry and policy_entry.rec.policy
                else None
            ),
            lead_time=(
                LeadTimeView(
                    promised_days=lt_new.promised_lead_days,
                    realized_mean_days=lt_new.realized_mean_days,
                    n_observations=lt_new.n_observations,
                )
                if lt_new
                else None
            ),
            procurement_lead_time=_supply_cycle_lane_view(
                lt_new,
                condition="NEW",
            ),
            repair_cycle_time=_supply_cycle_lane_view(
                lt_rep,
                condition="REP",
            ),
            open_orders=tuple(
                OpenOrderView(
                    order_id=str(getattr(o, "order_id", "") or ""),
                    order_type=str(getattr(o, "order_type", "") or ""),
                    vendor=_optional_text(o, "vendor", "vendor_code"),
                    qty_open=int(getattr(o, "qty_open", 0) or 0),
                    expected_rcv_date=_optional_iso(o, "expected_rcv_date"),
                    order_line_id=_optional_text(o, "order_line_id"),
                    opened_at=_optional_iso(o, "opened_at"),
                    status=_optional_text(o, "status"),
                    serial_number=_optional_text(o, "serial_number"),
                    location=(
                        _optional_text(o, "location", "location_code")
                        or _optional_text(oo, "location", "location_code")
                        or location
                    ),
                    shop=_optional_text(o, "shop", "shop_code"),
                )
                for o in (oo.orders if oo else [])
            ),
            total_open_qty=oo.total_open_qty if oo else 0,
            open_orders_status=(
                "unavailable"
                if oo is None
                else (
                    "partial"
                    if any(order.expected_rcv_date is None for order in oo.orders)
                    else "available"
                )
            ),
            repair_pipeline=repair_pipeline,
            repair_return_profile=repair_return_profile,
            demand=(
                DemandSummary(
                    total_24mo=demanded_units_24mo_from(dh),
                    points=tuple(
                        DemandPoint(
                            period_start=o.period_start.isoformat(),
                            removals=o.removals,
                            issues=o.issues,
                            total=o.removals + o.issues,
                        )
                        for o in sorted(dh.observations, key=lambda o: o.period_start)
                    ),
                )
                if dh
                else None
            ),
            unit_cost=float(ve.unit_cost) if ve else None,
            planning_trace=build_planning_trace(
                demand_history=dh,
                recommendation=trace_entry.rec if trace_entry else None,
                scheduled_demand=scheduled_for_trace,
                open_orders=oo,
                vendor_economics=ve,
                part_attributes=attrs,
            ),
            candidate_frontier=self._candidate_frontiers.get((pn, location)),
        )

    def bvr(self) -> BvrReport:
        """The Business Value Report (spec 2026-07-02) — memoized; every decision
        action invalidates the cache so the report always reflects the current
        lifecycle state. Projected-only: see trax_io_spine.bvr."""
        if self._bvr_cache is not None:
            return self._bvr_cache
        policy_of = {}
        key_facts = []
        for ks in self._key_stats():
            pol = _safe(lambda ks=ks: self.fs.get_current_policy(
                tenant=self.tenant, pn=ks.pn, location=ks.location))
            policy_of[(ks.pn, ks.location)] = pol
            key_facts.append(KeyFacts(
                pn=ks.pn, location=ks.location, criticality_tier=ks.criticality_tier,
                rop=pol.rop if pol else 0, mean_per_day=ks.mean_per_day,
                lead_mean=ks.lead_mean,
                unit_cost=ks.unit_cost if ks.unit_cost > 0 else None,
            ))
        rec_states = [
            RecState(rec=e.rec, status=e.status.value) for e in self._entries.values()
        ]

        def baseline_for(entry):
            pol = policy_of.get((entry.pn, entry.location))
            if pol is None:
                return None
            return {"rop": pol.rop, "eoq": pol.eoq,
                    "safety_stock": pol.safety_stock, "max_stock": pol.max_stock}

        self._bvr_cache = build_bvr_report(
            tenant_id=self.tenant_id,
            extract_date=self._manifest.get("extract_date"),
            generated_at=datetime.now(UTC),
            key_facts=key_facts, rec_states=rec_states,
            ledger=self.writeback.iter_history(self.tenant_id),
            baseline_for=baseline_for, kill_switch=self.kill_switch,
            keys_total_portfolio=len(self.keys),
        )
        return self._bvr_cache

    def dashboard(self) -> DashboardSummary:
        t = self.tenant
        rows = []  # per-key facts
        # Index entries once by (pn, location) so the per-key loop below is O(1)
        # per lookup instead of an O(n) scan into self._entries — overall
        # O(keys + entries) rather than O(keys * entries). Feature-store getters
        # (self.fs.*) are already O(1) dict lookups, so those are left as-is.
        # Multiple recommendations can share a (pn, location) key (e.g. a rejected
        # duplicate); keep the first-inserted match to mirror the original
        # next(x for x in self._entries.values() if ...) scan order exactly.
        by_key: dict[tuple[str, str], _Entry] = {}
        for e in self._entries.values():
            key = (e.rec.part_number, e.rec.current_location)
            if key not in by_key:
                by_key[key] = e
        for pn, loc in self.keys:
            sp = _safe(
                lambda pn=pn, loc=loc: self.fs.get_stock_position(tenant=t, pn=pn, location=loc)
            )
            attrs = _safe(lambda pn=pn: self.fs.get_part_attributes(tenant=t, pn=pn))
            crit = _safe(lambda pn=pn: self.fs.get_criticality(tenant=t, pn=pn))
            ve = _safe(
                lambda pn=pn: self.fs.get_vendor_economics(tenant=t, pn=pn, vendor="DEFAULT")
            )
            e = by_key.get((pn, loc))
            rec = e.rec if e else None
            rows.append(
                dict(
                    pn=pn,
                    loc=loc,
                    on_hand=sp.on_hand if sp else 0,
                    unit_cost=float(ve.unit_cost) if ve else 0.0,
                    shortage=rec.shortage_quantity if rec else 0.0,
                    demand=rec.projected_demand if rec else 0.0,
                    aog=rec.aog_risk_level if rec else 0,
                    cost=float(rec.estimated_cost_impact) if rec else 0.0,
                    crit=crit.canonical_tier if crit else None,
                    ata=attrs.ata_chapter if attrs else None,
                    pclass=attrs.part_class if attrs else None,
                    tier=e.outcome.tier if e else None,
                    has_rec=rec is not None,
                )
            )

        def breakdown(field: str) -> tuple[Breakdown, ...]:
            groups: dict = {}
            for r in rows:
                k = r[field]
                if k is None:
                    continue
                g = groups.setdefault(str(k), dict(count=0, on_hand=0, shortage=0.0))
                g["count"] += 1
                g["on_hand"] += r["on_hand"]
                g["shortage"] += r["shortage"]
            return tuple(
                Breakdown(key=k, count=g["count"], on_hand=g["on_hand"], shortage=g["shortage"])
                for k, g in sorted(groups.items())
            )

        shortfalls = [r for r in rows if r["shortage"] > 0]
        top = sorted(shortfalls, key=lambda r: r["shortage"], reverse=True)[:10]
        return DashboardSummary(
            parts=len(rows),
            total_on_hand=sum(r["on_hand"] for r in rows),
            total_on_hand_value=sum(r["on_hand"] * r["unit_cost"] for r in rows),
            total_shortage=sum(r["shortage"] for r in rows),
            total_projected_demand=sum(r["demand"] for r in rows),
            aog_exposure=sum(1 for r in rows if r["aog"] >= 3),
            open_recommendations=sum(1 for r in rows if r["has_rec"]),
            net_cost_impact=sum(r["cost"] for r in rows),
            by_criticality=breakdown("crit"),
            by_ata=breakdown("ata"),
            by_part_class=breakdown("pclass"),
            by_tier=breakdown("tier"),
            top_shortages=tuple(
                PartShortfall(
                    pn=r["pn"],
                    location=r["loc"],
                    shortage=r["shortage"],
                    on_hand=r["on_hand"],
                    projected_demand=r["demand"],
                )
                for r in top
            ),
        )

    @staticmethod
    def _days_in_period(period_start: str) -> int:
        """Real length (in days) of a monthly DEMAND_HISTORY bucket (`bucket="month"`,
        see `extract_loader.build_stores_from_extract`), given its ISO `period_start`
        (always the 1st of the month). Used to scale the portfolio's constant-rate
        demand projection to each period's own length instead of splitting one total
        evenly across periods regardless of how many days they actually cover."""
        d = date.fromisoformat(period_start)
        return calendar.monthrange(d.year, d.month)[1]

    def forecast_summary(self) -> ForecastSummary:
        """Slice S5 — Forecast & Service Levels (PRD §6.6).

        Three honestly-scoped pieces, all derived from the same real per-key data the
        rest of the BFF already loads (self.fs / self.keys — no new data source):

        - service_levels: REAL. `TenantPolicyConfig().service_level_by_tier` (spec
          §5.3) crossed with the real count of keys per `Criticality.canonical_tier`.
          `actual_coverage` is the same honest on-hand-vs-shortage proxy the Overview's
          SlInvestmentPanel uses — not a true fill-rate backtest.
        - method_coverage: REAL. Every key's demand regime is computed with the exact
          deterministic classifier the engine runs (`trax_io_reco.regime.classifier.
          classify`, spec §6.1) over its real `DemandHistory` — cheap (event-count
          arithmetic), so this runs over the full portfolio rather than sampling.
          Regime is then mapped to the forecast method that actually serves it in v1
          (`_REGIME_METHOD`).
        - accuracy: HONEST GAP. No backtest runs at serve time, so this is NOT a MAPE/
          bias metric. It's a labeled proxy: recent real actual demand (from
          DEMAND_HISTORY observations, rolled into the two most recent MONTHLY
          buckets present in the extract — `bucket="month"`, not 90-day) vs. the
          shared historical-basis per-day rate, summed across the portfolio and
          scaled to each period's own real length in days. Discrete scheduled demand
          is intentionally excluded from this historical proxy. This is a
          constant-rate projection re-scaled per period, not a genuine per-period
          reforecast — if the rendered periods happen to be equal-length, the
          projected values will look flat, which is truthful rather than a bug.
        """
        t = self.tenant
        policy_cfg = TenantPolicyConfig()

        by_key: dict[tuple[str, str], _Entry] = {}
        for e in self._entries.values():
            key = (e.rec.part_number, e.rec.current_location)
            if key not in by_key:
                by_key[key] = e

        tier_counts: dict[int, int] = {}
        tier_on_hand: dict[int, int] = {}
        tier_shortage: dict[int, float] = {}
        regime_counts: dict[str, int] = {}
        actual_by_period: dict[str, float] = {}
        mean_per_day_total = 0.0
        actual_total = 0.0

        for pn, loc in self.keys:
            crit = _safe(lambda pn=pn: self.fs.get_criticality(tenant=t, pn=pn))
            if crit is not None:
                tier = crit.canonical_tier
                tier_counts[tier] = tier_counts.get(tier, 0) + 1
                sp = _safe(
                    lambda pn=pn, loc=loc: self.fs.get_stock_position(
                        tenant=t, pn=pn, location=loc
                    )
                )
                e = by_key.get((pn, loc))
                rec = e.rec if e else None
                tier_on_hand[tier] = tier_on_hand.get(tier, 0) + (sp.on_hand if sp else 0)
                tier_shortage[tier] = tier_shortage.get(tier, 0.0) + (
                    rec.shortage_quantity if rec else 0.0
                )

            dh = _safe(
                lambda pn=pn, loc=loc: self.fs.get_demand_history(tenant=t, pn=pn, location=loc)
            )
            if dh is not None:
                basis = demand_basis_trace(dh)
                events = events_24mo_from(dh)
                if basis.demand_event_count is not None and basis.exposure_days > 0:
                    regime = classify(
                        events_24mo=events,
                        history_days=basis.exposure_days,
                    )
                    regime_counts[regime.value] = regime_counts.get(regime.value, 0) + 1

                # Honest accuracy proxy: bucket real actual demand by period_start
                # (monthly buckets — see extract_loader.build_stores_from_extract),
                # and separately accumulate the portfolio's current constant-rate
                # demand projection (mean per day) so each rendered period below can
                # be scaled by its own real length instead of splitting one total
                # evenly across periods.
                for o in dh.observations:
                    period_key = o.period_start.isoformat()
                    actual_by_period[period_key] = actual_by_period.get(
                        period_key, 0.0
                    ) + (o.removals + o.issues)

                e = by_key.get((pn, loc))
                if e is not None:
                    actual_total += basis.demanded_units
                    mean_per_day_total += basis.historical_per_day

        bands = tuple(
            ServiceLevelBand(
                criticality_tier=tier,
                target_service_level=policy_cfg.service_level_by_tier.get(tier, 0.0),
                sku_count=tier_counts.get(tier, 0),
                actual_coverage=(
                    None
                    if tier not in tier_counts
                    else (
                        1.0
                        if (tier_on_hand[tier] + tier_shortage[tier]) == 0
                        else tier_on_hand[tier]
                        / (tier_on_hand[tier] + tier_shortage[tier])
                    )
                ),
            )
            for tier in sorted(policy_cfg.service_level_by_tier)
        )

        total_skus = sum(regime_counts.values())
        coverage_rows = tuple(
            MethodCoverageRow(
                regime=regime,
                method=_REGIME_METHOD.get(regime, "Unclassified"),
                sku_count=count,
                pct=(count / total_skus) if total_skus else 0.0,
            )
            for regime, count in sorted(regime_counts.items())
        )

        # Recent-vs-projected accuracy proxy, bucketed by the (at most) two most
        # recent distinct period_start values present in the extract — an honest
        # "last observed period(s) vs current projection" comparison, not a backtest.
        # Each period gets its OWN projected value: the portfolio's current
        # constant-rate projection (mean_per_day_total) scaled by that period's
        # real length in days, not one total split evenly across periods.
        recent_periods = sorted(actual_by_period)[-2:]
        accuracy_points = tuple(
            AccuracyPoint(
                period_start=period,
                actual=actual_by_period[period],
                projected=mean_per_day_total * self._days_in_period(period),
            )
            for period in recent_periods
        )

        return ForecastSummary(
            service_levels=ServiceLevelPolicy(bands=bands),
            method_coverage=MethodCoverage(total_skus=total_skus, rows=coverage_rows),
            accuracy=ForecastAccuracy(
                status="proxy",
                note=(
                    "No backtest runs at serve time. Points compare real recent "
                    "monthly DEMAND_HISTORY actuals against the engine's current "
                    "constant-rate (mean-per-day) demand projection scaled to each "
                    "period's own length — an honest proxy, not a per-period "
                    "reforecast or a MAPE/bias backtest."
                ),
                points=accuracy_points,
            ),
        )

    # ----------------------------------------------------------------------- #
    # Slice S7 — Data & Connections / feed health (PRD §6.7)
    # ----------------------------------------------------------------------- #
    def _manifest_artifact_status(self) -> dict[str, str]:
        """Return the latest manifest's per-domain status, failing closed.

        ``FEED_DEFINITIONS`` describes what the application is capable of consuming;
        it is not evidence that a source completed in the latest extract.  A missing
        or malformed artifact list therefore produces no successful domains.
        Duplicate domains or unknown statuses invalidate the manifest conservatively.
        """
        artifacts = self._manifest.get("artifacts")
        if not isinstance(artifacts, list) or self._manifest_extract_date() is None:
            return {}

        statuses: dict[str, str] = {}
        for artifact in artifacts:
            if not isinstance(artifact, dict):
                return {}
            domain = artifact.get("domain")
            status = artifact.get("status")
            if (
                not isinstance(domain, str)
                or not domain
                or status not in {"succeeded", "failed", "skipped"}
                or domain in statuses
            ):
                return {}
            statuses[domain] = status
        return statuses

    def _manifest_row_count(
        self, domain: str, *, artifact_status: dict[str, str] | None = None
    ) -> int | None:
        """`row_count` per domain when the manifest carries it (the committed sample
        manifest does not — see `bff/feeds.py`/`FeedHealthRow` docstring) — never
        fabricated, always `None` when absent rather than guessed from `self.keys`.
        Counts from failed/skipped/ambiguous artifacts are never reported as current.
        """
        statuses = artifact_status or self._manifest_artifact_status()
        if statuses.get(domain) != "succeeded":
            return None
        artifacts = self._manifest.get("artifacts")
        if not isinstance(artifacts, list):
            return None
        matching = [
            artifact
            for artifact in artifacts
            if isinstance(artifact, dict) and artifact.get("domain") == domain
        ]
        if len(matching) != 1:
            return None
        count = matching[0].get("row_count")
        if isinstance(count, int) and not isinstance(count, bool) and count >= 0:
            return count
        return None

    def _manifest_extract_date(self) -> str | None:
        """Return a validated ISO extract date or ``None`` for corrupt metadata."""
        raw = self._manifest.get("extract_date")
        if not isinstance(raw, str):
            return None
        try:
            date.fromisoformat(raw)
        except ValueError:
            return None
        return raw

    def feeds_summary(self) -> FeedsSummary:
        """Slice S7 — Data & Connections (PRD §6.7): the honest feed-health surface.

        Every row's `domains`/`notes` and maximum attainable status come from the
        static, code-verified capability mapping in `bff/feeds.py` (cross-checked
        against `domains.py` and `extract_loader.py`).  The row's actual status and
        sync date are latest-manifest authoritative:

        - every backing domain succeeded: the capability status (connected/partial);
        - only some backing domains succeeded: partial;
        - none succeeded, or the manifest is absent/malformed: not connected.

        `rows` is the manifest artifact's `row_count` when present (the committed
        sample manifest has none, so this is `None` there — not fabricated from
        `len(self.keys)`, which is a recommendation-engine key count, not a raw
        per-domain extract row count). `last_sync` is reported only when at least one
        backing domain succeeded.  Missing/corrupt manifests fail conservatively
        without changing the backward-compatible response model.
        """
        artifact_status = self._manifest_artifact_status()
        extract_date = self._manifest_extract_date()

        rows: list[FeedHealthRow] = []
        for d in FEED_DEFINITIONS:
            succeeded = sum(
                artifact_status.get(domain) == "succeeded" for domain in d.domains
            )
            if not d.domains or succeeded == 0:
                status = FeedConnectionStatus.NOT_CONNECTED
            elif succeeded < len(d.domains):
                status = FeedConnectionStatus.PARTIAL
            else:
                status = d.status
            row_counts = [
                count
                for domain in d.domains
                if (
                    count := self._manifest_row_count(
                        domain, artifact_status=artifact_status
                    )
                )
                is not None
            ]
            rows.append(
                FeedHealthRow(
                    feed_id=d.feed_id,
                    name=d.name,
                    status=status,
                    domains=d.domains,
                    rows=(sum(row_counts) if row_counts else None),
                    last_sync=(extract_date if succeeded else None),
                    notes=d.notes,
                )
            )

        connected = sum(1 for r in rows if r.status is FeedConnectionStatus.CONNECTED)
        partial = sum(1 for r in rows if r.status is FeedConnectionStatus.PARTIAL)
        not_connected = sum(1 for r in rows if r.status is FeedConnectionStatus.NOT_CONNECTED)

        return FeedsSummary(
            health=FeedHealthStrip(
                connected=connected,
                partial=partial,
                not_connected=not_connected,
                extract_date=extract_date,
            ),
            feeds=tuple(rows),
        )

    # ----------------------------------------------------------------------- #
    # Slice S6 — What-If Scenarios (PRD §6.5)
    # ----------------------------------------------------------------------- #
    def _key_stats(self) -> list[KeyStats]:
        """Memoized per-key demand/lead-time/cost primitives — built once per store
        instance from the real `fs`/`keys`, reused across every `solve_scenario` call
        (including all 7 frontier points of a single solve) so repeated slider drags
        don't re-derive them (spec: solver must stay interactive over 22.9K keys)."""
        if self._key_stats_cache is None:
            self._key_stats_cache = build_key_stats(fs=self.fs, tenant=self.tenant, keys=self.keys)
        return self._key_stats_cache

    def _repair_scenario_inputs(self) -> list[RepairScenarioInput]:
        if self._repair_scenario_inputs_cache is None:
            self._repair_scenario_inputs_cache = build_repair_scenario_inputs(
                fs=self.fs,
                tenant=self.tenant,
                keys=self.keys,
            )
        return self._repair_scenario_inputs_cache

    @staticmethod
    def _to_solver_params(wire: ScenarioParamsWire) -> ScenarioParams:
        return ScenarioParams(
            service_level_target=wire.service_level_target,
            service_level_by_tier=dict(wire.service_level_by_tier),
            budget_cap=wire.budget_cap,
            lead_time_delta_pct=wire.lead_time_delta_pct,
            procurement_lead_time_delta_pct=(
                wire.procurement_lead_time_delta_pct
            ),
            repair_tat_delta_pct=wire.repair_tat_delta_pct,
            scope=wire.scope.value,
            scope_value=wire.scope_value,
        )

    def _result_wire(self, params: ScenarioParamsWire, result: SolveResult) -> ScenarioSolveResult:
        return build_scenario_result(
            tenant_id=self.tenant_id,
            source_manifest=self._manifest,
            key_universe=self.keys,
            procurement_inputs=self._key_stats(),
            repair_inputs=self._repair_scenario_inputs(),
            params=params,
            result=result,
        )

    def solve_scenario(self, params: ScenarioParamsWire) -> ScenarioSolveResult:
        """`POST .../scenarios/solve` — live solve, not persisted (API-SPEC.md)."""
        solver = ScenarioSolver(
            self._key_stats(),
            total_keys_in_universe=len(self.keys),
            repair_inputs=self._repair_scenario_inputs(),
        )
        result = solver.solve(self._to_solver_params(params))
        return self._result_wire(params, result)

    def save_scenario(
        self, name: str, params: ScenarioParamsWire, result: ScenarioSolveResult
    ) -> Scenario:
        # Never persist a client-supplied result under a tenant-scoped identity.
        # Re-solving is deterministic and prevents stale UI races or a result
        # copied from another tenant from crossing the save boundary.
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
        self._scenarios[scenario.id] = _ScenarioEntry(scenario)
        return scenario

    def list_scenarios(self) -> list[Scenario]:
        return sorted(
            (e.scenario for e in self._scenarios.values()),
            key=lambda s: s.created_at,
            reverse=True,
        )

    def _get_scenario_entry(self, scenario_id: str) -> _ScenarioEntry:
        entry = self._scenarios.get(scenario_id)
        if entry is None:
            raise ScenarioNotFound(scenario_id)
        return entry

    def get_scenario(self, scenario_id: str) -> Scenario:
        return self._get_scenario_entry(scenario_id).scenario

    def delete_scenario(self, scenario_id: str) -> None:
        self._get_scenario_entry(scenario_id)  # raises ScenarioNotFound if absent
        del self._scenarios[scenario_id]

    def commit_scenario(self, scenario_id: str) -> ScenarioAuditEvent:
        """Promote a saved scenario to COMMITTED + append an audited marker.

        Does NOT write policies back to eMRO — Writeback is the only agent with eMRO
        write permission (CLAUDE.md cross-cutting rule); a scenario commit is a
        planning-tool decision record, not a policy write. See `ScenarioAuditEvent`.
        """
        entry = self._get_scenario_entry(scenario_id)
        now = datetime.now(UTC)
        committed = entry.scenario.model_copy(
            update={"status": ScenarioStatus.COMMITTED, "committed_at": now}
        )
        entry.scenario = committed
        event = ScenarioAuditEvent(
            scenario_id=scenario_id, scenario_name=committed.name, action="commit", at=now
        )
        self._audit_log.append(event)
        return event

    def scenario_audit_log(self) -> list[ScenarioAuditEvent]:
        return list(self._audit_log)
