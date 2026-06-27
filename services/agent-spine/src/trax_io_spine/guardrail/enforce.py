"""Compose hard guardrails + band policy into a per-recommendation GuardrailOutcome."""

from __future__ import annotations

from trax_io_reco.contracts.enums import AutonomyTier
from trax_io_reco.contracts.recommendation import Recommendation

from trax_io_spine.contracts import ApprovalTask, GuardrailOutcome, GuardrailStatus
from trax_io_spine.guardrail.hard import (
    aog_forces_advisor,
    compute_delta_pct,
    hard_guardrail_violations,
)
from trax_io_spine.guardrail.policy import AutonomyPolicy, BandAutonomyPolicy


class GuardrailEnforcer:
    def __init__(self, policy: AutonomyPolicy | None = None) -> None:
        self._policy: AutonomyPolicy = policy or BandAutonomyPolicy()

    def enforce(self, rec: Recommendation) -> GuardrailOutcome:
        # Non-policy recommendations (e.g. Sell/Transfer with no ROP/EOQ change) are never
        # auto-written; a planner handles them.
        if rec.policy is None:
            return GuardrailOutcome(
                recommendation_id=rec.recommendation_id,
                status=GuardrailStatus.QUEUED_FOR_APPROVAL,
                tier=rec.suggested_autonomy_tier,
                delta_pct=0.0,
                reasons=("non_policy_recommendation",) + rec.guardrail_flags,
                approval_task=self._task(rec, rec.suggested_autonomy_tier, "non_policy"),
            )

        delta_pct = compute_delta_pct(rec.policy, rec.current_policy)
        violations = hard_guardrail_violations(rec, delta_pct=delta_pct)
        if violations:
            return GuardrailOutcome(
                recommendation_id=rec.recommendation_id,
                status=GuardrailStatus.REJECTED_HARD_GUARDRAIL,
                tier=AutonomyTier.ADVISOR,
                delta_pct=delta_pct,
                reasons=violations + rec.guardrail_flags,
            )

        tier = AutonomyTier.ADVISOR if aog_forces_advisor(rec) else rec.suggested_autonomy_tier
        status = self._policy.authorize(
            tier=tier, delta_pct=delta_pct, criticality_tier=rec.criticality_tier
        )
        task = (
            self._task(rec, tier, "band")
            if status is GuardrailStatus.QUEUED_FOR_APPROVAL
            else None
        )
        return GuardrailOutcome(
            recommendation_id=rec.recommendation_id,
            status=status,
            tier=tier,
            delta_pct=delta_pct,
            reasons=rec.guardrail_flags,
            approval_task=task,
        )

    @staticmethod
    def _task(rec: Recommendation, tier: AutonomyTier, reason: str) -> ApprovalTask:
        # Higher = more urgent: critical parts (low tier number) + AOG + low confidence.
        priority = (
            (6 - rec.criticality_tier) * 10.0
            + float(rec.aog_risk_level.value) * 5.0
            + (1.0 - rec.confidence_score) * 2.0
        )
        return ApprovalTask(
            task_id=f"{rec.tenant_id}:{rec.recommendation_id}",
            tenant_id=rec.tenant_id,
            pn=rec.part_number,
            location=rec.current_location,
            tier=tier,
            priority_score=priority,
            reason=reason,
        )
