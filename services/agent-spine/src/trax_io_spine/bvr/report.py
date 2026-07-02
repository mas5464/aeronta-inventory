"""BVR assembly: posture + governance + forward look + the full report (spec §1–2).

`build_bvr_report` is pure: the BFF store maps its retained state into
`KeyFacts`/`RecState`/ledger tuples and calls this — bvr never imports bff.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

from trax_io_reco.contracts.enums import AutonomyTier
from trax_io_reco.contracts.recommendation import Recommendation

from trax_io_spine.bvr.attribution import (
    AttributionRates,
    KeyEconomics,
    _money,
    build_savings,
)
from trax_io_spine.bvr.models import (
    SCHEMA_VERSION,
    BvrPeriod,
    BvrReport,
    ExecutiveSummary,
    ForwardLook,
    ForwardOpportunity,
    Governance,
    Methodology,
    ServicePosture,
    TierPosture,
)
from trax_io_spine.contracts import HistoryEntry, WritebackStatus

TIER_TARGETS: dict[int, float] = {1: 0.995, 2: 0.98, 3: 0.95, 4: 0.92, 5: 0.90}

_POSTURE_NOTE = (
    "Posture (share of keys whose current ROP covers mean lead-time demand), "
    "not realized fill rate — realized service requires sequential monthly extracts."
)
_TOP_N = 10

# Design-doc "Tier A/B/C" labels (§2.3) mirror AutonomyTier.ADVISOR/BOUNDED/AUTONOMOUS —
# the mirrored IntEnum's own `.value` is the numeric 1/2/3, so the label is looked up here.
_TIER_LABEL: dict[AutonomyTier, str] = {
    AutonomyTier.ADVISOR: "A",
    AutonomyTier.BOUNDED: "B",
    AutonomyTier.AUTONOMOUS: "C",
}


@dataclass(frozen=True)
class KeyFacts:
    pn: str
    location: str
    criticality_tier: int  # 1..5
    rop: int
    mean_per_day: float
    lead_mean: float
    unit_cost: float | None


@dataclass(frozen=True)
class RecState:
    rec: Recommendation
    status: str  # "pending" | "approved" | "rejected" | "deferred"


def _posture(key_facts: list[KeyFacts]) -> ServicePosture:
    by_tier: dict[int, list[KeyFacts]] = {}
    for kf in key_facts:
        by_tier.setdefault(kf.criticality_tier, []).append(kf)
    tiers = []
    for tier in sorted(by_tier):
        keys = by_tier[tier]
        at = sum(1 for kf in keys if kf.rop >= kf.mean_per_day * kf.lead_mean)
        tiers.append(TierPosture(
            tier=tier, target_fill_rate=TIER_TARGETS.get(tier, 0.90),
            keys=len(keys), keys_at_posture=at,
            posture_rate=(at / len(keys)) if keys else 0.0,
        ))
    return ServicePosture(tiers=tuple(tiers), note=_POSTURE_NOTE)


def _governance(
    rec_states: list[RecState], ledger: tuple[HistoryEntry, ...], kill_switch: bool
) -> Governance:
    by_status = {"pending": 0, "approved": 0, "rejected": 0, "deferred": 0}
    for rs in rec_states:
        by_status[rs.status] = by_status.get(rs.status, 0) + 1
    decided = by_status["approved"] + by_status["rejected"]
    writes = {s: 0 for s in WritebackStatus}
    tier_mix = {"A": 0, "B": 0, "C": 0}
    rollbacks = 0
    for e in ledger:
        writes[e.status] += 1
        if e.tier is not None:
            label = _TIER_LABEL.get(e.tier)
            if label is not None:
                tier_mix[label] = tier_mix.get(label, 0) + 1
        if e.provenance_id.startswith("rollback:"):
            rollbacks += 1
    return Governance(
        recommendations_total=len(rec_states),
        pending=by_status["pending"], approved=by_status["approved"],
        rejected=by_status["rejected"], deferred=by_status["deferred"],
        approval_rate=(by_status["approved"] / decided) if decided else 0.0,
        override_rate=(by_status["rejected"] / decided) if decided else 0.0,
        writes_written=writes[WritebackStatus.WRITTEN],
        writes_shadowed=writes[WritebackStatus.SHADOWED],
        writes_failed=writes[WritebackStatus.FAILED],
        writes_deferred_open_order=writes[WritebackStatus.DEFERRED_OPEN_ORDER],
        rollbacks=rollbacks, tier_mix=tier_mix, kill_switch_engaged=kill_switch,
    )


def _forward(rec_states: list[RecState]) -> ForwardLook:
    pending = [rs.rec for rs in rec_states if rs.status == "pending"]
    ranked = sorted(pending, key=lambda r: r.estimated_cost_impact, reverse=True)
    return ForwardLook(
        open_pipeline_value=_money(float(sum(r.estimated_cost_impact for r in pending))),
        projected_demand_horizon=sum(r.projected_demand for r in pending),
        top_opportunities=tuple(
            ForwardOpportunity(
                pn=r.part_number, location=r.current_location, type=r.type.value,
                estimated_cost_impact=r.estimated_cost_impact,
            )
            for r in ranked[:_TOP_N]
        ),
    )


def build_bvr_report(
    *, tenant_id: str, extract_date: str | None, generated_at: datetime,
    key_facts: list[KeyFacts], rec_states: list[RecState],
    ledger: tuple[HistoryEntry, ...],
    baseline_for: Callable[[HistoryEntry], dict[str, int] | None],
    kill_switch: bool, rates: AttributionRates | None = None,
    agent_version: str = "spine-0.1.0",
) -> BvrReport:
    rates = rates or AttributionRates()
    econ_by_key = {
        (kf.pn, kf.location): KeyEconomics(
            unit_cost=kf.unit_cost, mean_per_day=kf.mean_per_day,
            lead_mean=kf.lead_mean, criticality_tier=kf.criticality_tier,
        )
        for kf in key_facts
    }
    savings = build_savings(
        ledger, baseline_for, lambda pn, loc: econ_by_key.get((pn, loc)), rates
    )
    posture = _posture(key_facts)
    governance = _governance(rec_states, ledger, kill_switch)
    forward = _forward(rec_states)

    changed_ats = [e.changed_at for e in ledger]
    at_target = sum(
        1 for t in posture.tiers if t.keys and t.posture_rate >= t.target_fill_rate
    )
    headline = f"{at_target}/{len(posture.tiers)} tiers at target posture"
    hashes = tuple(sorted({rs.rec.input_snapshot_hash for rs in rec_states}))

    return BvrReport(
        schema_version=SCHEMA_VERSION,
        tenant_id=tenant_id,
        period=BvrPeriod(
            extract_date=extract_date,
            decision_window_start=min(changed_ats) if changed_ats else None,
            decision_window_end=max(changed_ats) if changed_ats else None,
            generated_at=generated_at,
            label=f"Snapshot {extract_date}" if extract_date else "Snapshot (undated)",
        ),
        executive_summary=ExecutiveSummary(
            total_projected=savings.total_projected,
            changes_applied=governance.writes_written,
            changes_shadowed=governance.writes_shadowed,
            keys_under_management=len(key_facts),
            open_pipeline_value=forward.open_pipeline_value,
            service_headline=headline,
        ),
        savings=savings,
        service_posture=posture,
        governance=governance,
        forward_look=forward,
        methodology=Methodology(
            formulas=(
                savings.holding_cost_delta.formula,
                savings.ordering_cost_delta.formula,
                savings.stockout_risk_delta.formula,
                _POSTURE_NOTE,
            ),
            assumption_rates=rates.as_dict(),
            ledger_entries=len(ledger),
            recommendations=len(rec_states),
            keys=len(key_facts),
            input_snapshot_hashes=hashes,
            agent_version=agent_version,
            generated_by="trax_io_spine.bvr",
        ),
    )
