"""BVR report schema — the 'BVR schema locked' deliverable (spec §1).

Frozen pydantic models; `SCHEMA_VERSION` is semver (additive change => minor).
Every monetary figure in this schema is PROJECTED (spec honesty contract):
computed from the writeback ledger vs the pre-agent extract baseline with
disclosed formulas — never presented as realized savings.
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict

SCHEMA_VERSION = "1.0.0"


class _Base(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class BvrPeriod(_Base):
    extract_date: str | None  # the snapshot's extract_date (ISO date string)
    decision_window_start: datetime | None  # min ledger changed_at (None: no writes)
    decision_window_end: datetime | None  # max ledger changed_at
    generated_at: datetime
    label: str  # e.g. "Snapshot 2024-04-01"


class ExecutiveSummary(_Base):
    total_projected: Decimal
    changes_applied: int  # WRITTEN ledger entries
    changes_shadowed: int  # SHADOWED ledger entries
    keys_under_management: int
    open_pipeline_value: Decimal
    service_headline: str  # e.g. "3/5 tiers at target posture"


class ProjectedComponent(_Base):
    name: str
    amount: Decimal  # positive = projected benefit; negatives reported as-is
    formula: str  # human-readable, restated in Methodology
    inputs: dict[str, float | int]
    assumptions: tuple[str, ...]


class SavingsAttribution(_Base):
    holding_cost_delta: ProjectedComponent
    ordering_cost_delta: ProjectedComponent
    stockout_risk_delta: ProjectedComponent
    # Applied (WRITTEN) vs shadowed (SHADOWED) are never silently blended:
    total_projected_applied: Decimal
    total_projected_shadowed: Decimal
    total_projected: Decimal  # applied + shadowed
    changes_total: int
    changes_valued: int  # the "N of M valued" coverage disclosure
    assumption_rates: dict[str, float]


class TierPosture(_Base):
    tier: int  # essentiality 1..5
    target_fill_rate: float  # design §5.5
    keys: int
    keys_at_posture: int  # rop >= mean_per_day * lead_mean
    posture_rate: float  # keys_at_posture / keys (0.0 when keys == 0)


class ServicePosture(_Base):
    tiers: tuple[TierPosture, ...]
    note: str  # the posture-not-realized disclosure


class Governance(_Base):
    recommendations_total: int
    pending: int
    approved: int
    rejected: int
    deferred: int
    approval_rate: float  # approved / decided (0.0 when none decided)
    override_rate: float  # rejected / decided
    writes_written: int
    writes_shadowed: int
    writes_failed: int
    writes_deferred_open_order: int
    rollbacks: int  # ledger entries whose provenance_id startswith "rollback:"
    tier_mix: dict[str, int]  # ledger writes by AutonomyTier value ("A"/"B"/"C")
    kill_switch_engaged: bool


class ForwardOpportunity(_Base):
    pn: str
    location: str
    type: str  # RecommendationType value
    estimated_cost_impact: Decimal


class ForwardLook(_Base):
    open_pipeline_value: Decimal  # sum of pending recs' estimated_cost_impact
    projected_demand_horizon: float
    top_opportunities: tuple[ForwardOpportunity, ...]  # impact-ranked, max 10


class Methodology(_Base):
    formulas: tuple[str, ...]
    assumption_rates: dict[str, float]
    ledger_entries: int
    recommendations: int
    keys: int
    input_snapshot_hashes: tuple[str, ...]  # distinct, sorted
    agent_version: str
    generated_by: str


class BvrReport(_Base):
    schema_version: str
    tenant_id: str
    period: BvrPeriod
    executive_summary: ExecutiveSummary
    savings: SavingsAttribution
    service_posture: ServicePosture
    governance: Governance
    forward_look: ForwardLook
    methodology: Methodology
