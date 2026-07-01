"""Planner-UI BFF wire models — mirror the engine/spine contracts for the future TS client."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from trax_io_reco.contracts.enums import AogRiskLevel, AutonomyTier, RecommendationType

from trax_io_spine.contracts import WritebackResult


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class TaskStatus(StrEnum):
    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    DEFERRED = "deferred"


class RejectReason(StrEnum):
    WRONG_FOR_FLEET = "wrong_for_fleet"
    WRONG_ESSENTIALITY = "wrong_essentiality"
    BAD_LEAD_TIME = "bad_lead_time"
    PLANNER_OVERRIDE = "planner_override"
    OTHER = "other"


class QueueRow(_Base):
    recommendation_id: str
    pn: str
    location: str
    type: RecommendationType
    criticality_tier: int
    aog_risk_level: AogRiskLevel
    confidence_score: float
    recommended_quantity: float
    estimated_cost_impact: Decimal
    tier: AutonomyTier
    priority_score: float
    status: TaskStatus
    reason: str
    approvable: bool  # has a writable policy — approve writes rather than 409
    description: str
    current_stock: int
    shortage_quantity: float
    recommended_location: str | None
    horizon_days: int


class PagedQueue(_Base):
    items: tuple[QueueRow, ...]
    total: int
    limit: int
    offset: int


class _PolicyView(_Base):
    rop: int
    eoq: int
    safety_stock: int
    max_stock: int


class _EvidenceView(_Base):
    kind: str
    ref_id: str
    detail: str
    as_of: str | None = None


class RecommendationDetail(_Base):
    recommendation_id: str
    pn: str
    location: str
    type: RecommendationType
    criticality_tier: int
    aog_risk_level: AogRiskLevel
    confidence_score: float
    recommended_quantity: float
    estimated_cost_impact: Decimal
    tier: AutonomyTier
    status: TaskStatus
    reason: str
    provenance_id: str | None
    projected_demand: float
    current_policy: _PolicyView | None
    proposed_policy: _PolicyView | None
    supporting_evidence: tuple[_EvidenceView, ...]
    guardrail_flags: tuple[str, ...]
    description: str
    current_stock: int
    shortage_quantity: float
    recommended_location: str | None
    horizon_days: int


class RejectRequest(_Base):
    reason: RejectReason
    detail: str = ""


class DeferRequest(_Base):
    until: datetime | None = None


class BulkApproveFilter(_Base):
    tiers: tuple[AutonomyTier, ...] | None = None
    max_delta_pct: float | None = None
    criticality_min: int | None = None
    types: tuple[RecommendationType, ...] | None = None


class ActionResult(_Base):
    recommendation_id: str
    status: TaskStatus
    writeback: WritebackResult | None = None
    message: str = ""


class KillSwitchState(_Base):
    engaged: bool


class StockBreakdown(_Base):
    on_hand: int
    serviceable: int
    in_repair: int
    allocated: int
    rental: int
    loan: int


class LeadTimeView(_Base):
    promised_days: float | None
    realized_mean_days: float | None
    n_observations: int


class OpenOrderView(_Base):
    order_id: str
    order_type: str
    vendor: str | None
    qty_open: int
    expected_rcv_date: str | None


class DemandPoint(_Base):
    period_start: str
    removals: int
    issues: int
    total: int


class DemandSummary(_Base):
    total_24mo: int
    points: tuple[DemandPoint, ...]


class PartAttributesView(_Base):
    description: str
    ata_chapter: str | None
    part_class: str | None
    shelf_life_days: int | None
    hazardous_material: bool
    tool_control_item: bool
    criticality_tier: int | None


class PartContext(_Base):
    pn: str
    location: str
    attributes: PartAttributesView
    stock: StockBreakdown | None
    current_policy: _PolicyView | None
    proposed_policy: _PolicyView | None
    lead_time: LeadTimeView | None
    open_orders: tuple[OpenOrderView, ...]
    total_open_qty: int
    demand: DemandSummary | None
    unit_cost: float | None


class Breakdown(_Base):
    key: str
    count: int
    on_hand: int
    shortage: float


class PartShortfall(_Base):
    pn: str
    location: str
    shortage: float
    on_hand: int
    projected_demand: float


class DashboardSummary(_Base):
    parts: int
    total_on_hand: int
    total_on_hand_value: float
    total_shortage: float
    total_projected_demand: float
    aog_exposure: int
    open_recommendations: int
    net_cost_impact: float
    by_criticality: tuple[Breakdown, ...]
    by_ata: tuple[Breakdown, ...]
    by_part_class: tuple[Breakdown, ...]
    by_tier: tuple[Breakdown, ...]
    top_shortages: tuple[PartShortfall, ...]


# --------------------------------------------------------------------------- #
# Slice S5 — Forecast & Service Levels (PRD §6.6)
# --------------------------------------------------------------------------- #
class ServiceLevelBand(_Base):
    """One criticality tier's differentiated SL policy (spec: `TenantPolicyConfig.
    service_level_by_tier`) crossed with the real count of (PN, Location) keys
    classified into that tier by the feature store's `Criticality.canonical_tier`.

    `actual_coverage` is the honest on-hand-vs-shortage proxy already used by the
    Overview's SlInvestmentPanel — not a true fill-rate backtest (no such series is
    computed at serve time).
    """

    criticality_tier: int
    target_service_level: float
    sku_count: int
    actual_coverage: float | None


class ServiceLevelPolicy(_Base):
    bands: tuple[ServiceLevelBand, ...]


class MethodCoverageRow(_Base):
    """Count of (PN, Location) keys whose demand regime — and therefore forecast
    method — is `regime`, per the deterministic classifier (spec §6.1:
    `trax_io_reco.regime.classifier.classify`, thresholded on 24-month event counts).
    """

    regime: str
    method: str
    sku_count: int
    pct: float


class MethodCoverage(_Base):
    total_skus: int
    rows: tuple[MethodCoverageRow, ...]


class AccuracyPoint(_Base):
    """A single monthly period's actual-vs-projected demand, network-aggregated.

    This is NOT a backtested forecast accuracy metric — no held-out backtest runs
    at serve time. It's an honest proxy: recent actual demand (from real,
    monthly-bucketed `DEMAND_HISTORY` observations) vs. the engine's current
    constant-rate (mean-per-day) demand projection, scaled to this period's own
    length in days and aggregated across the portfolio. `projected` is a re-scaled
    constant rate, not a genuine per-period reforecast.
    """

    period_start: str
    actual: float
    projected: float


class ForecastAccuracy(_Base):
    status: str  # "proxy" (honest gap — see docstring) — never "connected" in v1
    note: str
    points: tuple[AccuracyPoint, ...]


class ForecastSummary(_Base):
    service_levels: ServiceLevelPolicy
    method_coverage: MethodCoverage
    accuracy: ForecastAccuracy


# --------------------------------------------------------------------------- #
# Slice S6 — What-If Scenarios (PRD §6.5)
# --------------------------------------------------------------------------- #
class ScenarioScopeKind(StrEnum):
    ALL = "all"
    CRITICALITY_TIER = "criticality_tier"
    ATA_CHAPTER = "ata_chapter"


class ScenarioParamsWire(_Base):
    """The What-If sliders. All optional fields fall back to the real
    `TenantPolicyConfig` / current-state defaults when unset (see `bff/scenario.py`).
    """

    service_level_target: float | None = None
    service_level_by_tier: dict[int, float] = {}
    budget_cap: float | None = Field(
        default=None,
        description=(
            "Informational only: flags whether the proposed investment exceeds this "
            "cap via `ScenarioSolveResult.budget_cap_binds`. Does NOT filter, scale, "
            "or otherwise constrain the solve — the solver always solves the full "
            "in-scope key set at the requested service level regardless of this cap."
        ),
    )
    lead_time_delta_pct: float = 0.0
    scope: ScenarioScopeKind = ScenarioScopeKind.ALL
    scope_value: str | None = None


class ScenarioOutcomeWire(_Base):
    """`projected_coverage` is the target cycle-service-level a fully-funded proposed
    policy would achieve (monotonic in the SL slider). `on_hand_gap_ratio` is the
    fraction of scoped keys whose current real on-hand already meets the proposed
    reorder point — real, useful, but NOT expected to be monotonic in SL (see
    `bff/scenario.py` module docstring).

    Simplification disclosure: every number here comes from one uniform (R,Q)
    normal-approximation solve (spec §6.2/§6.4 math) applied to ALL keys regardless of
    demand regime — an interactive approximation for a real-time What-If slider, not a
    re-run of the full recommendation engine. The engine's real per-regime policy
    dispatch (base-stock / (s,S) / (R,Q)) may differ materially from this uniform
    approximation, especially for ultra-rare and intermittent-demand keys.
    """

    service_level: float
    projected_investment: float
    projected_coverage: float
    on_hand_gap_ratio: float
    scored_keys: int


class FrontierPointWire(_Base):
    service_level: float
    projected_investment: float
    projected_coverage: float


class ScenarioSolveResult(_Base):
    """Response of `POST /scenarios/solve` — live, not persisted (API-SPEC.md).

    `skipped_keys` / `total_keys` is the honest data-quality disclosure: keys in the
    tenant's full real key universe (`total_keys`) that are missing demand history,
    criticality, vendor economics, or stock position cannot be scored at all
    (`skipped_keys`), independent of the scenario's own `scope` filter — how many of
    the *in-scope* keys were actually scored is `ScenarioOutcomeWire.scored_keys`.

    Simplification disclosure: `current`/`proposed`/`frontier` are all solved via one
    uniform (R,Q) normal-approximation model applied identically across every demand
    regime (see `ScenarioOutcomeWire` and `bff/scenario.py` module docstring) — an
    interactive approximation, not a re-run of the engine's regime-conditional policy
    dispatch. Treat this as directional for scenario comparison, not as a substitute
    for the real per-key recommendation the engine would produce.
    """

    params: ScenarioParamsWire
    current: ScenarioOutcomeWire
    proposed: ScenarioOutcomeWire
    delta_investment: float
    delta_coverage: float
    frontier: tuple[FrontierPointWire, ...]
    skipped_keys: int
    total_keys: int
    budget_cap_binds: bool


class SaveScenarioRequest(_Base):
    name: str
    params: ScenarioParamsWire
    result: ScenarioSolveResult


class ScenarioStatus(StrEnum):
    DRAFT = "draft"
    COMMITTED = "committed"


class Scenario(_Base):
    """A saved scenario (API-SPEC.md `Scenario`). `status` starts `DRAFT`; `commit`
    (`POST /scenarios/{id}/commit`) promotes it to `COMMITTED` and appends an
    in-memory `AuditEvent` — v1 does NOT write policies back to eMRO (out of scope;
    see `bff/scenario.py` module docstring and ADR follow-up)."""

    id: str
    name: str
    params: ScenarioParamsWire
    result: ScenarioSolveResult
    status: ScenarioStatus
    created_at: datetime
    committed_at: datetime | None = None


class ScenarioAuditEvent(_Base):
    """In-memory commit acknowledgement — NOT a real eMRO writeback (spec: Writeback
    is the ONLY agent with eMRO write permission; scenario commit is a planning-tool
    audit marker, not a policy write)."""

    scenario_id: str
    scenario_name: str
    action: Literal["commit"]
    at: datetime
    note: str = (
        "Scenario committed as the tenant's target plan. No eMRO writeback occurred — "
        "promoting a scenario's levers into live (ROP, EOQ, Safety Stock, Max) policy "
        "writes is out of scope for v1 (see docs/adr for the writeback seam)."
    )
