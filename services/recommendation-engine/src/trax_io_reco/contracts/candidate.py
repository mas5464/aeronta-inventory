"""Versioned contracts for one-key planning candidate frontiers.

The candidate layer is deliberately independent from the portfolio optimizer.  It
describes immutable, reconciled choices for one decision key; a later optimizer may
select one of those choices without re-reading source data or re-running policy math.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

from trax_io_reco.contracts.recommendation import RecommendationBatch

CANDIDATE_CONTRACT_VERSION = "candidate.v1"
CANDIDATE_PLANNER_VERSION = "candidate-planner-v1"

NonEmptyStr = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
CurrencyCode = Annotated[str, StringConstraints(pattern=r"^[A-Z]{3}$")]


class FrozenDict(dict):
    """JSON-compatible dictionary that rejects mutation after validation."""

    @staticmethod
    def _immutable(*_args: object, **_kwargs: object) -> None:
        raise TypeError("validated planning mappings are immutable")

    __setitem__ = _immutable
    __delitem__ = _immutable
    clear = _immutable
    pop = _immutable
    popitem = _immutable
    setdefault = _immutable
    update = _immutable
    __ior__ = _immutable

    def __copy__(self) -> FrozenDict:
        return self

    def __deepcopy__(self, _memo: dict[int, object]) -> FrozenDict:
        return self


def _decimal_input(value: object) -> object:
    """Reject binary floats at the contract boundary.

    Callers may provide ``Decimal``, integer, or decimal text.  Refusing floats keeps
    money and optimizer dimensions stable across JSON/Python execution paths.
    """

    if isinstance(value, (float, bool)):
        raise ValueError("decimal values must not be supplied as binary floats or booleans")
    return value


ExactDecimal = Annotated[Decimal, BeforeValidator(_decimal_input)]
NonNegativeDecimal = Annotated[
    Decimal,
    BeforeValidator(_decimal_input),
    Field(ge=Decimal("0")),
]
UnitIntervalDecimal = Annotated[
    Decimal,
    BeforeValidator(_decimal_input),
    Field(ge=Decimal("0"), le=Decimal("1")),
]

ActionKind = Literal[
    "no_change",
    "purchase",
    "transfer_in",
    "transfer_out",
    "adjust_policy",
    "reduce_stock",
    "sell",
]
CandidateKind = Literal[
    "no_change",
    "purchase",
    "transfer",
    "transfer_purchase",
    "adjust_policy",
    "reduce_stock",
    "sell",
]


class _CandidateBase(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", allow_inf_nan=False)

    contract_version: Literal["candidate.v1"] = CANDIDATE_CONTRACT_VERSION


class FingerprintComponent(_CandidateBase):
    """One immutable, result-affecting input not covered by a named field."""

    name: NonEmptyStr
    value: NonEmptyStr


class ServedForecastIdentity(_CandidateBase):
    """Forecast artifact actually served for one member of a pooled decision."""

    decision_key: NonEmptyStr
    forecast_model: NonEmptyStr
    forecast_version: NonEmptyStr


class ModelIdentity(_CandidateBase):
    """The actual model artifacts used to produce a candidate.

    There are intentionally no generic or configured-name defaults: the caller must
    report the served forecast and policy implementations that actually ran.
    """

    forecast_model: NonEmptyStr
    forecast_version: NonEmptyStr
    policy_model: NonEmptyStr
    policy_version: NonEmptyStr
    repair_model: NonEmptyStr | None = None
    repair_version: NonEmptyStr | None = None
    member_forecasts: tuple[ServedForecastIdentity, ...] = ()

    @model_validator(mode="before")
    @classmethod
    def _canonical_member_forecasts(cls, value: object) -> object:
        if not isinstance(value, dict) or "member_forecasts" not in value:
            return value
        normalized = dict(value)
        normalized["member_forecasts"] = tuple(
            sorted(
                normalized["member_forecasts"],
                key=lambda item: (
                    item.decision_key
                    if isinstance(item, ServedForecastIdentity)
                    else item["decision_key"]
                ),
            )
        )
        return normalized

    @model_validator(mode="after")
    def _paired_repair_identity(self) -> Self:
        if (self.repair_model is None) != (self.repair_version is None):
            raise ValueError("repair_model and repair_version must be supplied together")
        member_keys = [identity.decision_key for identity in self.member_forecasts]
        if len(member_keys) != len(set(member_keys)):
            raise ValueError("member forecast decision keys must be unique")
        return self


class CandidateFingerprintInputs(_CandidateBase):
    """Typed boundary for every input category that may affect a frontier.

    The per-key ``context_digest`` covers the canonical assembled source values.
    Additional versioned inputs remain explicit name/value components rather than a
    mutable arbitrary dictionary.  Budget and solver settings are intentionally absent:
    they select from a frontier and must not change how the frontier is generated.
    """

    tenant_id: NonEmptyStr
    decision_key: NonEmptyStr
    member_keys: tuple[NonEmptyStr, ...]
    source_snapshot_hash: NonEmptyStr
    context_digest: NonEmptyStr
    tenant_policy_version: NonEmptyStr
    observation_start: date | None = None
    observation_end: date | None = None
    as_of: date
    horizon_days: int = Field(ge=0)
    currency: CurrencyCode
    model_identity: ModelIdentity
    constraint_set_version: NonEmptyStr
    arbitration_version: NonEmptyStr
    economics_version: NonEmptyStr
    objective_definition_version: NonEmptyStr
    objective_inputs: tuple[FingerprintComponent, ...] = ()
    additional_result_inputs: tuple[FingerprintComponent, ...] = ()
    candidate_planner_version: Literal["candidate-planner-v1"] = CANDIDATE_PLANNER_VERSION

    @model_validator(mode="before")
    @classmethod
    def _canonical_collections(cls, value: object) -> object:
        if not isinstance(value, dict):
            return value
        normalized = dict(value)
        if "member_keys" in normalized:
            normalized["member_keys"] = tuple(sorted(normalized["member_keys"]))
        for field_name in ("objective_inputs", "additional_result_inputs"):
            if field_name in normalized:
                normalized[field_name] = tuple(
                    sorted(
                        normalized[field_name],
                        key=lambda item: (
                            item.name if isinstance(item, FingerprintComponent) else item["name"]
                        ),
                    )
                )
        return normalized

    @model_validator(mode="after")
    def _complete_and_unique(self) -> Self:
        if not self.member_keys:
            raise ValueError("member_keys must contain the decision key")
        if self.decision_key not in self.member_keys:
            raise ValueError("decision_key must be included in member_keys")
        if len(self.member_keys) != len(set(self.member_keys)):
            raise ValueError("member_keys must be unique")
        if self.member_keys != tuple(sorted(self.member_keys)):
            raise ValueError("member_keys must use canonical sorted order")
        forecast_member_keys = {
            identity.decision_key for identity in self.model_identity.member_forecasts
        }
        if forecast_member_keys and forecast_member_keys != set(self.member_keys):
            raise ValueError("member forecast identities must match member_keys exactly")
        if len(self.member_keys) > 1 and not forecast_member_keys:
            raise ValueError("pooled fingerprints require per-member forecast identities")
        if forecast_member_keys:
            primary = next(
                identity
                for identity in self.model_identity.member_forecasts
                if identity.decision_key == self.decision_key
            )
            if (
                primary.forecast_model != self.model_identity.forecast_model
                or primary.forecast_version != self.model_identity.forecast_version
            ):
                raise ValueError(
                    "primary forecast identity must match the decision member forecast"
                )
        if (self.observation_start is None) != (self.observation_end is None):
            raise ValueError("observation_start and observation_end must be supplied together")
        if (
            self.observation_start is not None
            and self.observation_end is not None
            and self.observation_end < self.observation_start
        ):
            raise ValueError("observation_end must be on or after observation_start")
        for components in (self.objective_inputs, self.additional_result_inputs):
            names = [component.name for component in components]
            if len(names) != len(set(names)):
                raise ValueError("fingerprint component names must be unique within each group")
        return self


class CandidateTargetLevels(_CandidateBase):
    rop: int = Field(ge=0)
    eoq: int = Field(ge=0)
    safety_stock: int = Field(ge=0)
    max_stock: int = Field(ge=0)


class CandidateActionLine(_CandidateBase):
    line_id: NonEmptyStr
    kind: ActionKind
    quantity: NonNegativeDecimal
    currency: CurrencyCode
    unit_acquisition_cash: NonNegativeDecimal
    source_location: NonEmptyStr | None = None
    destination_location: NonEmptyStr | None = None
    source_reference: NonEmptyStr | None = None

    @model_validator(mode="after")
    def _action_semantics(self) -> Self:
        if self.kind == "no_change":
            if self.quantity != 0 or self.unit_acquisition_cash != 0:
                raise ValueError("no_change must have zero quantity and zero acquisition cash")
        elif self.kind == "adjust_policy":
            if self.quantity != 0:
                raise ValueError("adjust_policy must have zero physical quantity")
        elif self.quantity <= 0:
            raise ValueError(f"{self.kind} must have positive quantity")
        if self.kind != "purchase" and self.unit_acquisition_cash != 0:
            raise ValueError("only purchase lines may commit acquisition cash")
        if self.kind in {"purchase", "transfer_in"} and self.destination_location is None:
            raise ValueError(f"{self.kind} requires destination_location")
        if self.kind == "transfer_in" and self.source_location is None:
            raise ValueError("transfer_in requires source_location")
        if self.kind == "transfer_out" and (
            self.source_location is None or self.destination_location is None
        ):
            raise ValueError("transfer_out requires source_location and destination_location")
        return self

    @property
    def acquisition_cash(self) -> Decimal:
        return self.quantity * self.unit_acquisition_cash


class LifecycleEconomics(_CandidateBase):
    """Inputs used to recompute a candidate's lifecycle-cost components."""

    currency: CurrencyCode
    inventory_unit_value: NonNegativeDecimal
    annual_holding_rate: NonNegativeDecimal
    ordering_cost_per_purchase: NonNegativeDecimal
    shortage_cost_per_unit: NonNegativeDecimal
    other_cost: NonNegativeDecimal = Decimal("0")
    horizon_days: int = Field(ge=0)


class LifecycleCostComponents(_CandidateBase):
    currency: CurrencyCode
    acquisition_cash: NonNegativeDecimal
    holding_cost: NonNegativeDecimal
    ordering_cost: NonNegativeDecimal
    shortage_cost: NonNegativeDecimal
    other_cost: NonNegativeDecimal
    total_lifecycle_cost: NonNegativeDecimal

    @model_validator(mode="after")
    def _total_reconciles(self) -> Self:
        expected = (
            self.acquisition_cash
            + self.holding_cost
            + self.ordering_cost
            + self.shortage_cost
            + self.other_cost
        )
        if self.total_lifecycle_cost != expected:
            raise ValueError("total_lifecycle_cost must equal the sum of all components")
        return self


class CandidateOutcome(_CandidateBase):
    projected_demand: NonNegativeDecimal
    available_before: NonNegativeDecimal
    expected_receipts_before: NonNegativeDecimal
    inbound_quantity: NonNegativeDecimal
    outbound_quantity: NonNegativeDecimal
    ending_net_position: ExactDecimal
    expected_shortage: NonNegativeDecimal
    expected_excess: NonNegativeDecimal
    expected_service_level: UnitIntervalDecimal
    expected_aog_risk: UnitIntervalDecimal

    @model_validator(mode="after")
    def _position_reconciles(self) -> Self:
        expected_net = (
            self.available_before
            + self.expected_receipts_before
            + self.inbound_quantity
            - self.outbound_quantity
            - self.projected_demand
        )
        if self.ending_net_position != expected_net:
            raise ValueError("ending_net_position does not reconcile")
        if self.expected_shortage != max(Decimal("0"), -expected_net):
            raise ValueError("expected_shortage does not reconcile to ending_net_position")
        if self.expected_excess != max(Decimal("0"), expected_net):
            raise ValueError("expected_excess does not reconcile to ending_net_position")
        expected_service = (
            Decimal("1")
            if self.projected_demand == 0
            else max(
                Decimal("0"),
                min(
                    Decimal("1"),
                    Decimal("1") - self.expected_shortage / self.projected_demand,
                ),
            )
        )
        if self.expected_service_level != expected_service:
            raise ValueError("expected_service_level does not reconcile to shortage")
        return self


class ConstraintEvidence(_CandidateBase):
    constraint_id: NonEmptyStr
    source: NonEmptyStr
    value: str | None = None
    scope: Literal["policy", "action"] = "policy"
    hard: bool
    satisfied: bool
    binding: bool
    detail: str | None = None


class CandidateEvidence(_CandidateBase):
    kind: NonEmptyStr
    source: NonEmptyStr
    detail: NonEmptyStr
    reference_id: NonEmptyStr | None = None


class CandidateReconciliation(_CandidateBase):
    """Arithmetic ledger copied into the response for independent verification."""

    currency: CurrencyCode
    available_before: NonNegativeDecimal
    expected_receipts_before: NonNegativeDecimal
    projected_demand: NonNegativeDecimal
    transfer_in_quantity: NonNegativeDecimal
    purchase_quantity: NonNegativeDecimal
    outbound_quantity: NonNegativeDecimal
    total_inbound_quantity: NonNegativeDecimal
    action_quantity: NonNegativeDecimal
    ending_net_position: ExactDecimal
    expected_shortage: NonNegativeDecimal
    acquisition_cash: NonNegativeDecimal

    @model_validator(mode="after")
    def _ledger_reconciles(self) -> Self:
        if self.total_inbound_quantity != self.transfer_in_quantity + self.purchase_quantity:
            raise ValueError("total_inbound_quantity does not reconcile")
        if self.action_quantity != self.total_inbound_quantity + self.outbound_quantity:
            raise ValueError("action_quantity does not reconcile")
        expected_net = (
            self.available_before
            + self.expected_receipts_before
            + self.total_inbound_quantity
            - self.outbound_quantity
            - self.projected_demand
        )
        if self.ending_net_position != expected_net:
            raise ValueError("reconciliation ending_net_position does not reconcile")
        if self.expected_shortage != max(Decimal("0"), -expected_net):
            raise ValueError("reconciliation expected_shortage does not reconcile")
        return self


class PolicyCandidate(_CandidateBase):
    candidate_id: Annotated[str, StringConstraints(pattern=r"^cand_[0-9a-f]{64}$")]
    tenant_id: NonEmptyStr
    pn: NonEmptyStr
    location: NonEmptyStr
    decision_key: NonEmptyStr
    member_keys: tuple[NonEmptyStr, ...]
    candidate_kind: CandidateKind
    label: NonEmptyStr
    is_no_change: bool
    feasible: bool
    infeasibility_reasons: tuple[NonEmptyStr, ...] = ()
    model_identity: ModelIdentity
    current_levels: CandidateTargetLevels
    target_levels: CandidateTargetLevels
    actions: tuple[CandidateActionLine, ...]
    action_quantity: NonNegativeDecimal
    lifecycle_costs: LifecycleCostComponents
    outcome: CandidateOutcome
    confidence: UnitIntervalDecimal
    constraints: tuple[ConstraintEvidence, ...]
    evidence: tuple[CandidateEvidence, ...]
    reconciliation: CandidateReconciliation

    @model_validator(mode="after")
    def _candidate_reconciles(self) -> Self:
        if not self.member_keys or self.decision_key not in self.member_keys:
            raise ValueError("decision_key must be included in member_keys")
        if len(self.member_keys) != len(set(self.member_keys)):
            raise ValueError("member_keys must be unique")
        if self.member_keys != tuple(sorted(self.member_keys)):
            raise ValueError("candidate member_keys must use canonical sorted order")
        forecast_member_keys = {
            identity.decision_key for identity in self.model_identity.member_forecasts
        }
        if forecast_member_keys and forecast_member_keys != set(self.member_keys):
            raise ValueError("member forecast identities must match candidate member_keys")
        if len(self.member_keys) > 1 and not forecast_member_keys:
            raise ValueError("pooled candidates require per-member forecast identities")
        if forecast_member_keys:
            primary = next(
                identity
                for identity in self.model_identity.member_forecasts
                if identity.decision_key == self.decision_key
            )
            if (
                primary.forecast_model != self.model_identity.forecast_model
                or primary.forecast_version != self.model_identity.forecast_version
            ):
                raise ValueError(
                    "primary forecast identity must match the decision member forecast"
                )
        if not self.actions:
            raise ValueError("actions must not be empty")
        if not self.evidence:
            raise ValueError("evidence must not be empty")
        if len({action.line_id for action in self.actions}) != len(self.actions):
            raise ValueError("action line ids must be unique")
        if len({constraint.constraint_id for constraint in self.constraints}) != len(
            self.constraints
        ):
            raise ValueError("constraint ids must be unique")
        hard_failures = tuple(
            constraint.constraint_id
            for constraint in self.constraints
            if constraint.hard and not constraint.satisfied
        )
        if self.feasible and (hard_failures or self.infeasibility_reasons):
            raise ValueError("a feasible candidate cannot contain infeasibility evidence")
        if not self.feasible and not (hard_failures or self.infeasibility_reasons):
            raise ValueError("an infeasible candidate must explain why")

        currencies = {action.currency for action in self.actions}
        currencies.add(self.lifecycle_costs.currency)
        currencies.add(self.reconciliation.currency)
        if len(currencies) != 1:
            raise ValueError("candidate currencies must match")

        action_quantity = sum(
            (action.quantity for action in self.actions if action.kind != "adjust_policy"),
            Decimal("0"),
        )
        if self.action_quantity != action_quantity:
            raise ValueError("action_quantity must equal finalized action-line quantities")
        acquisition_cash = sum(
            (action.acquisition_cash for action in self.actions),
            Decimal("0"),
        )
        if self.lifecycle_costs.acquisition_cash != acquisition_cash:
            raise ValueError("acquisition cash must be recomputed from purchase action lines")
        if self.reconciliation.acquisition_cash != acquisition_cash:
            raise ValueError("reconciliation acquisition cash does not match action lines")
        if self.reconciliation.action_quantity != self.action_quantity:
            raise ValueError("reconciliation action quantity does not match candidate")
        if self.reconciliation.available_before != self.outcome.available_before:
            raise ValueError("outcome and reconciliation available quantities do not match")
        if self.reconciliation.expected_receipts_before != self.outcome.expected_receipts_before:
            raise ValueError("outcome and reconciliation receipt quantities do not match")
        if self.reconciliation.projected_demand != self.outcome.projected_demand:
            raise ValueError("outcome and reconciliation projected demand do not match")
        if self.reconciliation.total_inbound_quantity != self.outcome.inbound_quantity:
            raise ValueError("outcome and reconciliation inbound quantities do not match")
        if self.reconciliation.outbound_quantity != self.outcome.outbound_quantity:
            raise ValueError("outcome and reconciliation outbound quantities do not match")
        if self.reconciliation.ending_net_position != self.outcome.ending_net_position:
            raise ValueError("outcome and reconciliation net positions do not match")
        if self.reconciliation.expected_shortage != self.outcome.expected_shortage:
            raise ValueError("outcome and reconciliation shortage do not match")

        if self.is_no_change:
            if self.candidate_kind != "no_change":
                raise ValueError("is_no_change requires candidate_kind=no_change")
            if self.current_levels != self.target_levels:
                raise ValueError("no-change target levels must equal current levels")
            if len(self.actions) != 1 or self.actions[0].kind != "no_change":
                raise ValueError("no-change must contain exactly one no_change action line")
            if self.action_quantity != 0 or self.lifecycle_costs.acquisition_cash != 0:
                raise ValueError("no-change must commit zero quantity and acquisition cash")
        elif any(action.kind == "no_change" for action in self.actions):
            raise ValueError("non-baseline candidates cannot contain a no_change action")
        else:
            if self.candidate_kind == "no_change":
                raise ValueError("candidate_kind=no_change requires is_no_change")
            movement_kinds = {
                action.kind for action in self.actions if action.kind != "adjust_policy"
            }
            expected_movements = {
                "purchase": {"purchase"},
                "transfer": {"transfer_in", "transfer_out"},
                "transfer_purchase": {"transfer_in", "purchase"},
                "adjust_policy": set(),
                "reduce_stock": {"reduce_stock"},
                "sell": {"sell"},
            }[self.candidate_kind]
            if self.candidate_kind == "transfer":
                if len(movement_kinds) != 1 or not movement_kinds <= expected_movements:
                    raise ValueError("transfer candidate must have one transfer direction")
            elif movement_kinds != expected_movements:
                raise ValueError(f"{self.candidate_kind} action lines do not match candidate kind")
            if self.candidate_kind == "adjust_policy" and not any(
                action.kind == "adjust_policy" for action in self.actions
            ):
                raise ValueError("adjust_policy candidate requires an adjust_policy line")
        return self


class CandidateFrontier(_CandidateBase):
    frontier_fingerprint: Annotated[
        str,
        StringConstraints(pattern=r"^frontier_[0-9a-f]{64}$"),
    ]
    output_digest: Annotated[
        str,
        StringConstraints(pattern=r"^output_[0-9a-f]{64}$"),
    ]
    planner_version: Literal["candidate-planner-v1"]
    tenant_id: NonEmptyStr
    decision_key: NonEmptyStr
    member_keys: tuple[NonEmptyStr, ...]
    currency: CurrencyCode
    candidates: tuple[PolicyCandidate, ...]
    total_options_considered: int = Field(ge=1)
    dominated_options_removed: int = Field(ge=0)

    @model_validator(mode="after")
    def _frontier_is_coherent(self) -> Self:
        if not self.candidates:
            raise ValueError("a frontier must contain candidates")
        if self.decision_key not in self.member_keys:
            raise ValueError("decision_key must be included in member_keys")
        if len(self.member_keys) != len(set(self.member_keys)):
            raise ValueError("member_keys must be unique")
        if self.member_keys != tuple(sorted(self.member_keys)):
            raise ValueError("frontier member_keys must use canonical sorted order")
        if len({candidate.candidate_id for candidate in self.candidates}) != len(self.candidates):
            raise ValueError("candidate ids must be unique")
        if sum(candidate.is_no_change for candidate in self.candidates) != 1:
            raise ValueError("an eligible frontier must retain exactly one no-change candidate")
        for candidate in self.candidates:
            if candidate.tenant_id != self.tenant_id:
                raise ValueError("candidate tenant does not match frontier tenant")
            if candidate.decision_key != self.decision_key:
                raise ValueError("candidate decision key does not match frontier")
            if candidate.member_keys != self.member_keys:
                raise ValueError("candidate member keys do not match frontier")
            if candidate.lifecycle_costs.currency != self.currency:
                raise ValueError("candidate currency does not match frontier")
        if self.total_options_considered != (len(self.candidates) + self.dominated_options_removed):
            raise ValueError("frontier option counts do not reconcile")
        expected_order = tuple(
            sorted(
                self.candidates,
                key=lambda candidate: (not candidate.is_no_change, candidate.candidate_id),
            )
        )
        if self.candidates != expected_order:
            raise ValueError("frontier candidates must use canonical stable order")
        return self


class CandidatePreviewBatch(_CandidateBase):
    """Additive service response for recommendation plus per-key candidate preview."""

    tenant_id: NonEmptyStr
    recommendation_batch: RecommendationBatch
    frontiers: tuple[CandidateFrontier, ...]

    @model_validator(mode="after")
    def _preview_is_coherent(self) -> Self:
        if self.recommendation_batch.tenant_id != self.tenant_id:
            raise ValueError("recommendation batch tenant does not match candidate preview")
        if any(frontier.tenant_id != self.tenant_id for frontier in self.frontiers):
            raise ValueError("candidate frontier tenant does not match preview tenant")
        decision_keys = [frontier.decision_key for frontier in self.frontiers]
        if len(decision_keys) != len(set(decision_keys)):
            raise ValueError("candidate preview decision keys must be unique")
        expected_order = tuple(
            sorted(self.frontiers, key=lambda frontier: frontier.decision_key)
        )
        if self.frontiers != expected_order:
            raise ValueError("candidate frontiers must use canonical decision-key order")
        return self


__all__ = [
    "ActionKind",
    "CANDIDATE_CONTRACT_VERSION",
    "CANDIDATE_PLANNER_VERSION",
    "CandidateKind",
    "CandidateActionLine",
    "CandidateEvidence",
    "CandidateFingerprintInputs",
    "CandidateFrontier",
    "CandidateOutcome",
    "CandidatePreviewBatch",
    "CandidateReconciliation",
    "CandidateTargetLevels",
    "ConstraintEvidence",
    "CurrencyCode",
    "FingerprintComponent",
    "LifecycleCostComponents",
    "LifecycleEconomics",
    "ModelIdentity",
    "PolicyCandidate",
    "ServedForecastIdentity",
]
