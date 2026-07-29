"""Policy recommendation contract — forward-compatible mirror of
trax_io.contracts.policy.PolicyRecommendation (spec §5.1).

The model_validator double-enforces the §6.2 floors (`rop >= safety_stock`,
`max_stock >= rop + eoq`) at the type layer. The contract default `model_id="stub"`
is preserved; the engine overrides it to "deterministic-v1" at construction time.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, NonNegativeInt, model_validator

from trax_io_reco.contracts.enums import PolicyKind


class AppliedConstraint(BaseModel):
    """One hard constraint considered by the deterministic policy calculation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: str | None = None
    binding: bool
    source: str
    scope: Literal["policy", "action"] = "policy"


class PolicyRecommendation(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    pn: str
    location: str
    rop: NonNegativeInt
    eoq: NonNegativeInt
    safety_stock: NonNegativeInt
    max_stock: NonNegativeInt
    policy_kind: PolicyKind
    service_level_target: float = 0.95
    provenance_id: str
    model_id: str = "stub"
    applied_constraints: tuple[AppliedConstraint, ...] = ()
    constraint_flags: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _floors(self) -> PolicyRecommendation:
        if self.rop < self.safety_stock:
            raise ValueError("rop must be >= safety_stock")
        if self.max_stock < self.rop + self.eoq:
            raise ValueError("max_stock must be >= rop + eoq")
        return self
