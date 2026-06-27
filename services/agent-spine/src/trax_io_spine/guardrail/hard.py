"""Non-bypassable §6.2 hard-guardrail verifiers (defense-in-depth over #11's clamps).

The engine already clamps; the spine re-derives the headline single-write delta and the
AOG-forces-Tier-A rule from the Recommendation itself, so the two layers cannot silently
diverge. Shelf-life/hazmat/tool clamps require part_attributes the spine does not re-fetch
in v1; those arrive on ``rec.guardrail_flags`` and are surfaced (not re-verified) downstream.
"""

from __future__ import annotations

from trax_io_feature_store.schemas import CurrentPolicy
from trax_io_reco.contracts.enums import AogRiskLevel
from trax_io_reco.contracts.policy import PolicyRecommendation
from trax_io_reco.contracts.recommendation import Recommendation

_FIELDS = ("rop", "eoq", "safety_stock", "max_stock")


def compute_delta_pct(policy: PolicyRecommendation, current: CurrentPolicy | None) -> float:
    """Max relative change across the four policy values vs the current policy.

    Returns 0.0 when there is no current policy (first-time seed: no baseline to delta against).
    """
    if current is None:
        return 0.0
    deltas: list[float] = []
    for f in _FIELDS:
        old = getattr(current, f)
        new = getattr(policy, f)
        if old == 0:
            if new != 0:
                deltas.append(1.0)  # 0 -> nonzero: treat as a full-band (100%) change
            continue
        deltas.append(abs(new - old) / old)
    return max(deltas) if deltas else 0.0


def hard_guardrail_violations(rec: Recommendation, *, delta_pct: float) -> tuple[str, ...]:
    """Reasons a recommendation must be rejected outright. Empty tuple = passes."""
    violations: list[str] = []
    if delta_pct > 1.0:
        violations.append("delta_exceeds_100pct")
    return tuple(violations)


def aog_forces_advisor(rec: Recommendation) -> bool:
    """An active AOG signal forces the most conservative tier (human approval)."""
    return rec.aog_risk_level >= AogRiskLevel.HIGH
