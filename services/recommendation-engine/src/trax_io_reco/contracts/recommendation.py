"""The recommendation output contracts — the authoritative API/UI response shape
(spec §5.2). `RecommendationBatch` serializes to the exact JSON the UI/API returns.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from trax_io_reco.contracts.context import CurrentPolicy
from trax_io_reco.contracts.enums import (
    AogRiskLevel,
    AutonomyTier,
    EvidenceKind,
    RecommendationType,
)
from trax_io_reco.contracts.policy import AppliedConstraint, PolicyRecommendation


class _Base(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        allow_inf_nan=False,
    )


class Evidence(_Base):
    kind: EvidenceKind
    ref_id: str
    detail: str
    as_of: date | None = None


class CalculationMemberEvidence(_Base):
    """One key's exact contribution to a recommendation calculation."""

    pn: str
    location: str
    projection_kind: str
    projected_historical_demand: float = Field(ge=0.0)
    scheduled_demand_due: float = Field(ge=0.0)
    projected_demand: float = Field(ge=0.0)
    dispatchable_available: float = Field(ge=0.0)
    open_receipts_due: float = Field(ge=0.0)
    overdue_open_receipts_due: float = Field(ge=0.0)
    repair_receipts_due: float = Field(ge=0.0)
    expected_receipts_due: float = Field(ge=0.0)
    net_position: float
    scheduled_demand_status: Literal["available", "partial", "unavailable"] = "unavailable"
    scheduled_demand_undated_lines: int = Field(default=0, ge=0)
    scheduled_demand_undated_units: int = Field(default=0, ge=0)
    open_receipts_status: Literal["available", "partial", "unavailable"] = "unavailable"
    open_receipts_undated_lines: int = Field(default=0, ge=0)
    open_receipts_undated_units: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _reconciles(self) -> CalculationMemberEvidence:
        tolerance = 1e-6
        if self.overdue_open_receipts_due > self.open_receipts_due + tolerance:
            raise ValueError("overdue_open_receipts_due cannot exceed open_receipts_due")
        if (
            abs(
                self.projected_historical_demand + self.scheduled_demand_due - self.projected_demand
            )
            > tolerance
        ):
            raise ValueError("member projected demand must equal historical plus scheduled demand")
        if (
            abs(self.open_receipts_due + self.repair_receipts_due - self.expected_receipts_due)
            > tolerance
        ):
            raise ValueError("member expected receipts must equal open plus repair receipts")
        if (
            abs(
                self.dispatchable_available
                + self.expected_receipts_due
                - self.projected_demand
                - self.net_position
            )
            > tolerance
        ):
            raise ValueError(
                "member net position must reconcile availability, receipts, and demand"
            )
        source_states = (
            (
                "scheduled demand",
                self.scheduled_demand_status,
                self.scheduled_demand_due,
                self.scheduled_demand_undated_lines,
                self.scheduled_demand_undated_units,
            ),
            (
                "open receipts",
                self.open_receipts_status,
                self.open_receipts_due,
                self.open_receipts_undated_lines,
                self.open_receipts_undated_units,
            ),
        )
        for label, status, due, undated_lines, undated_units in source_states:
            if (undated_lines == 0) != (undated_units == 0):
                raise ValueError(f"{label} undated lines and units must both be zero or positive")
            if status == "available" and undated_lines:
                raise ValueError(f"available {label} cannot contain excluded undated lines")
            if status == "partial" and undated_lines == 0:
                raise ValueError(f"partial member {label} requires excluded undated lines")
            if status == "unavailable" and (due > tolerance or undated_lines or undated_units):
                raise ValueError(f"unavailable {label} cannot contain observed quantities")
        return self


class CalculationEvidence(_Base):
    """Immutable arithmetic used to create one served recommendation.

    This carrier is produced inside the recommendation engine from the exact
    ``DemandProjection`` and ``NetPosition`` used for the action.  Downstream
    services map it; they never attempt to reconstruct statistical forecasts or
    interchange roll-ups from source snapshots.
    """

    as_of: date
    horizon_days: int = Field(ge=0)
    projection_kind: str
    served_historical_per_day: float = Field(ge=0.0)
    projected_historical_demand: float = Field(ge=0.0)
    scheduled_demand_due: float = Field(ge=0.0)
    projected_demand: float = Field(ge=0.0)
    dispatchable_available: float = Field(ge=0.0)
    open_receipts_due: float = Field(ge=0.0)
    overdue_open_receipts_due: float = Field(ge=0.0)
    repair_receipts_due: float = Field(ge=0.0)
    expected_receipts_due: float = Field(ge=0.0)
    net_position: float
    shortage_before_action: float = Field(ge=0.0)
    pooled_group_id: str | None = None
    members: tuple[CalculationMemberEvidence, ...]
    scheduled_demand_status: Literal["available", "partial", "unavailable"] = "unavailable"
    scheduled_demand_undated_lines: int = Field(default=0, ge=0)
    scheduled_demand_undated_units: int = Field(default=0, ge=0)
    open_receipts_status: Literal["available", "partial", "unavailable"] = "unavailable"
    open_receipts_undated_lines: int = Field(default=0, ge=0)
    open_receipts_undated_units: int = Field(default=0, ge=0)
    pooling_scope: Literal["single_key", "complete_group", "worklist_partial"] = "single_key"
    excluded_member_keys: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _reconciles(self) -> CalculationEvidence:
        tolerance = 1e-6
        if not self.members:
            raise ValueError("calculation evidence must contain at least one member")
        if self.overdue_open_receipts_due > self.open_receipts_due + tolerance:
            raise ValueError("overdue_open_receipts_due cannot exceed open_receipts_due")
        if (
            abs(
                self.projected_historical_demand + self.scheduled_demand_due - self.projected_demand
            )
            > tolerance
        ):
            raise ValueError("projected demand must equal historical plus scheduled demand")
        if (
            abs(
                self.served_historical_per_day * self.horizon_days
                - self.projected_historical_demand
            )
            > tolerance
        ):
            raise ValueError(
                "projected historical demand must equal served rate times horizon days"
            )
        if (
            abs(self.open_receipts_due + self.repair_receipts_due - self.expected_receipts_due)
            > tolerance
        ):
            raise ValueError("expected receipts must equal open plus repair receipts")
        if (
            abs(
                self.dispatchable_available
                + self.expected_receipts_due
                - self.projected_demand
                - self.net_position
            )
            > tolerance
        ):
            raise ValueError("net position must reconcile availability, receipts, and demand")
        if abs(max(0.0, -self.net_position) - self.shortage_before_action) > tolerance:
            raise ValueError("shortage_before_action must equal the negative net-position floor")

        sums = {
            "projected_historical_demand": sum(
                member.projected_historical_demand for member in self.members
            ),
            "scheduled_demand_due": sum(member.scheduled_demand_due for member in self.members),
            "projected_demand": sum(member.projected_demand for member in self.members),
            "dispatchable_available": sum(member.dispatchable_available for member in self.members),
            "open_receipts_due": sum(member.open_receipts_due for member in self.members),
            "overdue_open_receipts_due": sum(
                member.overdue_open_receipts_due for member in self.members
            ),
            "repair_receipts_due": sum(member.repair_receipts_due for member in self.members),
            "expected_receipts_due": sum(member.expected_receipts_due for member in self.members),
            "net_position": sum(member.net_position for member in self.members),
            "scheduled_demand_undated_lines": sum(
                member.scheduled_demand_undated_lines for member in self.members
            ),
            "scheduled_demand_undated_units": sum(
                member.scheduled_demand_undated_units for member in self.members
            ),
            "open_receipts_undated_lines": sum(
                member.open_receipts_undated_lines for member in self.members
            ),
            "open_receipts_undated_units": sum(
                member.open_receipts_undated_units for member in self.members
            ),
        }
        for field, member_sum in sums.items():
            if abs(member_sum - float(getattr(self, field))) > tolerance:
                raise ValueError(f"{field} must equal the sum of member evidence")
        if len(self.members) > 1 and self.pooled_group_id is None:
            raise ValueError("pooled_group_id is required when multiple members are served")
        member_keys = [(member.pn, member.location) for member in self.members]
        if len(member_keys) != len(set(member_keys)):
            raise ValueError("calculation member keys must be unique")
        if len(self.excluded_member_keys) != len(set(self.excluded_member_keys)):
            raise ValueError("excluded_member_keys must be unique")
        included_key_labels = {f"{pn}@{location}" for pn, location in member_keys}
        if included_key_labels.intersection(self.excluded_member_keys):
            raise ValueError("included and excluded member keys must be disjoint")
        for field_name in ("scheduled_demand_status", "open_receipts_status"):
            states = {getattr(member, field_name) for member in self.members}
            expected = (
                "available"
                if states == {"available"}
                else ("unavailable" if states == {"unavailable"} else "partial")
            )
            if getattr(self, field_name) != expected:
                raise ValueError(f"{field_name} must summarize the member availability states")
        if self.pooling_scope == "worklist_partial" and not self.excluded_member_keys:
            raise ValueError("worklist_partial pooling requires excluded_member_keys")
        if self.pooling_scope != "worklist_partial" and self.excluded_member_keys:
            raise ValueError("excluded_member_keys require worklist_partial pooling")
        if self.pooling_scope == "complete_group" and len(self.members) < 2:
            raise ValueError("complete_group pooling requires multiple member contributions")
        if self.pooling_scope == "single_key":
            if len(self.members) != 1 or self.pooled_group_id is not None:
                raise ValueError("single_key pooling requires exactly one member and no group id")
        elif self.pooled_group_id is None:
            raise ValueError("pooled calculation evidence requires pooled_group_id")
        aggregate_states = (
            (
                "scheduled demand",
                self.scheduled_demand_status,
                self.scheduled_demand_due,
                self.scheduled_demand_undated_lines,
                self.scheduled_demand_undated_units,
            ),
            (
                "open receipts",
                self.open_receipts_status,
                self.open_receipts_due,
                self.open_receipts_undated_lines,
                self.open_receipts_undated_units,
            ),
        )
        for label, status, due, undated_lines, undated_units in aggregate_states:
            if status == "available" and (undated_lines or undated_units):
                raise ValueError(f"available aggregate {label} cannot contain undated exclusions")
            if status == "unavailable" and (due > tolerance or undated_lines or undated_units):
                raise ValueError(
                    f"unavailable aggregate {label} cannot contain observed quantities"
                )
        return self


class Recommendation(_Base):
    recommendation_id: str
    tenant_id: str
    type: RecommendationType
    part_number: str
    description: str
    current_location: str
    recommended_location: str | None = None
    current_stock: int
    projected_demand: float
    shortage_quantity: float = Field(ge=0.0)
    recommended_quantity: float
    estimated_cost_impact: Decimal
    aog_risk_level: AogRiskLevel
    criticality_tier: int = 5  # 1 (most critical) .. 5; drives ranking weight
    reason: str
    supporting_evidence: tuple[Evidence, ...]
    confidence_score: float = Field(ge=0.0, le=1.0)
    horizon_days: int
    suggested_autonomy_tier: AutonomyTier
    guardrail_flags: tuple[str, ...] = ()
    generated_at: datetime
    input_snapshot_hash: str
    policy: PolicyRecommendation | None = None
    current_policy: CurrentPolicy | None = None
    applied_constraints: tuple[AppliedConstraint, ...] = ()
    calculation_evidence: CalculationEvidence | None = None

    @model_validator(mode="after")
    def _calculation_matches_served_recommendation(self) -> Recommendation:
        evidence = self.calculation_evidence
        if evidence is None:
            return self
        tolerance = 1e-6
        if evidence.horizon_days != self.horizon_days:
            raise ValueError("calculation evidence horizon must match recommendation horizon")
        if abs(evidence.projected_demand - self.projected_demand) > tolerance:
            raise ValueError(
                "calculation evidence demand must match recommendation projected demand"
            )
        if not any(
            member.pn == self.part_number and member.location == self.current_location
            for member in evidence.members
        ):
            raise ValueError("calculation evidence must include the recommendation decision key")
        return self


class SkippedKey(_Base):
    pn: str
    location: str
    reason: str


class BatchSummary(_Base):
    total: int
    by_type: dict[str, int] = Field(default_factory=dict)
    by_aog: dict[int, int] = Field(default_factory=dict)


class RecommendationBatch(_Base):
    tenant_id: str
    generated_at: datetime
    reporting_horizon_days: int = 30
    recommendations: tuple[Recommendation, ...] = ()
    skipped: tuple[SkippedKey, ...] = ()
    summary: BatchSummary
