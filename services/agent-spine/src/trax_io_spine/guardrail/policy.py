"""Deterministic autonomy band policy.

Decides whether a recommendation auto-writes or queues for approval, from its effective
tier + single-write delta + part criticality. A Protocol so Cedar backs the same seam in
production (the deployment slice) without changing the enforcer.
"""

from __future__ import annotations

from typing import Protocol

from pydantic import BaseModel, ConfigDict
from trax_io_reco.contracts.enums import AutonomyTier

from trax_io_spine.contracts import GuardrailStatus


class AutonomyConfig(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    bounded_max_delta_pct: float = 0.25
    autonomous_max_delta_pct: float = 1.0
    min_autonomous_criticality_tier: int = 4  # 1=most critical..5=least; only >= this auto-writes


class AutonomyPolicy(Protocol):
    def authorize(
        self, *, tier: AutonomyTier, delta_pct: float, criticality_tier: int
    ) -> GuardrailStatus: ...


class BandAutonomyPolicy:
    def __init__(self, config: AutonomyConfig | None = None) -> None:
        self._cfg = config or AutonomyConfig()

    def authorize(
        self, *, tier: AutonomyTier, delta_pct: float, criticality_tier: int
    ) -> GuardrailStatus:
        if tier is AutonomyTier.ADVISOR:
            return GuardrailStatus.QUEUED_FOR_APPROVAL
        if criticality_tier < self._cfg.min_autonomous_criticality_tier:
            return GuardrailStatus.QUEUED_FOR_APPROVAL
        ceiling = (
            self._cfg.autonomous_max_delta_pct
            if tier is AutonomyTier.AUTONOMOUS
            else self._cfg.bounded_max_delta_pct
        )
        if delta_pct <= ceiling:
            return GuardrailStatus.APPROVED_FOR_WRITE
        return GuardrailStatus.QUEUED_FOR_APPROVAL
