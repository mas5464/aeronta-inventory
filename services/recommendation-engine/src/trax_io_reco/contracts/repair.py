"""Versioned repair-history input contracts.

The canonical upload and native-connector paths normalize completed repair
lifecycle records into this shape before they may contribute repair-cycle
evidence. Open repair work is deliberately a separate contract introduced by
the repair-supply lane; a terminal observation must never be inferred to be
future supply.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    PositiveInt,
    field_validator,
    model_validator,
)

RepairTerminalStatus = Literal[
    "completed",
    "closed",
    "cancelled",
    "scrapped",
    "condemned",
]
RepairOutcome = Literal[
    "serviceable",
    "unserviceable",
    "scrapped",
    "condemned",
]
RepairWorkExclusionCode = Literal[
    "missing_order_identity",
    "missing_line_identity",
    "missing_opened_at",
    "future_opened_at",
    "missing_location",
    "location_mismatch",
    "terminal_status",
    "ineligible_status",
    "duplicate_order_line",
    "duplicate_serial",
    "serial_quantity_mismatch",
    "aggregate_wip_cap",
    "unidentified_aggregate_residual",
]
RepairPipelineWarningCode = Literal[
    "repair_pipeline_unavailable",
    "repair_work_excluded",
    "repair_identity_excluded",
    "repair_age_missing",
    "repair_source_duplicates",
    "repair_wip_mismatch",
    "repair_residual_unidentified",
]

_OPEN_REPAIR_STATUSES = frozenset(
    {
        "open",
        "in_progress",
        "in progress",
        "awaiting_parts",
        "awaiting parts",
        "awaiting_vendor",
        "awaiting vendor",
        "on_hold",
        "on hold",
    }
)
_TERMINAL_REPAIR_STATUSES = frozenset(
    {
        "completed",
        "closed",
        "cancelled",
        "canceled",
        "scrapped",
        "condemned",
    }
)


def parse_repair_timestamp(value: object) -> datetime | None:
    """Parse an ISO lifecycle timestamp and normalize it to UTC.

    Date-only values remain accepted for CSV interoperability and represent
    midnight UTC. Naive datetimes are treated as UTC because the canonical
    contract has no tenant-timezone field; native connectors should emit an
    explicit offset whenever one is available.
    """

    if isinstance(value, datetime):
        parsed = value
    else:
        raw = str(value or "").strip()
        if not raw:
            return None
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


class RepairCycleObservation(BaseModel):
    """One stable, terminal repair-order line observation."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    contract_version: Literal["repair-cycle-observation.v1"] = (
        "repair-cycle-observation.v1"
    )
    tenant_id: str = Field(min_length=1)
    repair_order_id: str = Field(min_length=1)
    repair_line_id: str = Field(min_length=1)
    part_number: str = Field(min_length=1)
    quantity: PositiveInt
    started_at: datetime
    completed_at: datetime
    status: RepairTerminalStatus
    shop_code: str | None = None
    vendor_code: str | None = None
    location_code: str | None = None
    outcome: RepairOutcome | None = None
    serial_number: str | None = None

    @field_validator("started_at", "completed_at", mode="before")
    @classmethod
    def _parse_timestamp(cls, value: object) -> datetime:
        parsed = parse_repair_timestamp(value)
        if parsed is None:
            raise ValueError("must be an ISO date or timestamp")
        return parsed

    @field_validator(
        "shop_code",
        "vendor_code",
        "location_code",
        "outcome",
        "serial_number",
        mode="before",
    )
    @classmethod
    def _blank_optional_string_is_none(cls, value: object) -> object:
        return None if value is None or str(value).strip() == "" else value

    @field_validator("status", "outcome", mode="before")
    @classmethod
    def _normalize_enum(cls, value: object) -> object:
        return str(value).strip().lower() if value is not None else value

    @model_validator(mode="after")
    def _coherent_terminal_observation(self) -> RepairCycleObservation:
        if self.completed_at < self.started_at:
            raise ValueError("completed_at must not precede started_at")
        if self.serial_number is not None and self.quantity != 1:
            raise ValueError("a serial-number observation must have quantity 1")
        if self.status == "cancelled" and self.outcome is not None:
            raise ValueError("cancelled repair cannot carry a completion outcome")
        if self.status == "scrapped" and self.outcome not in {None, "scrapped"}:
            raise ValueError("scrapped status contradicts the supplied outcome")
        if self.status == "condemned" and self.outcome not in {None, "condemned"}:
            raise ValueError("condemned status contradicts the supplied outcome")
        return self

    @property
    def is_observed_return(self) -> bool:
        """Whether this row may contribute observed repair-cycle duration."""

        return (
            self.status in {"completed", "closed"}
            and self.outcome
            not in {"unserviceable", "scrapped", "condemned"}
        )

    @property
    def shop_identity(self) -> str | None:
        return self.shop_code or self.vendor_code


class RepairWorkItem(BaseModel):
    """One identifiable, non-terminal repair order line.

    The quantity belongs to the stable order-line identity. ``serial_number``
    remains optional because a non-serialized line may represent several
    otherwise indistinguishable units.
    """

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    contract_version: Literal["repair-work-item.v1"] = "repair-work-item.v1"
    tenant_id: str = Field(min_length=1)
    repair_order_id: str = Field(min_length=1)
    repair_line_id: str = Field(min_length=1)
    part_number: str = Field(min_length=1)
    quantity: PositiveInt
    location_code: str = Field(min_length=1)
    opened_at: datetime
    status: str = Field(min_length=1)
    shop_code: str | None = None
    vendor_code: str | None = None
    serial_number: str | None = None

    @field_validator("opened_at", mode="before")
    @classmethod
    def _parse_opened_at(cls, value: object) -> datetime:
        parsed = parse_repair_timestamp(value)
        if parsed is None:
            raise ValueError("must be an ISO date or timestamp")
        return parsed

    @field_validator(
        "shop_code",
        "vendor_code",
        "serial_number",
        mode="before",
    )
    @classmethod
    def _blank_work_string_is_none(cls, value: object) -> object:
        return None if value is None or str(value).strip() == "" else value

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: object) -> str:
        return str(value or "").strip().lower()

    @model_validator(mode="after")
    def _coherent_open_work(self) -> RepairWorkItem:
        if self.serial_number is not None and self.quantity != 1:
            raise ValueError("a serial-number work item must have quantity 1")
        if self.status in _TERMINAL_REPAIR_STATUSES:
            raise ValueError("terminal repair status cannot be open work")
        if self.status not in _OPEN_REPAIR_STATUSES:
            raise ValueError(f"unsupported open repair status '{self.status}'")
        return self

    @property
    def shop_identity(self) -> str | None:
        return self.shop_code or self.vendor_code


class IncludedRepairPosition(BaseModel):
    """The portion of one repair work item eligible for later return modeling."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    work_item: RepairWorkItem
    eligible_quantity: PositiveInt
    age_days: NonNegativeInt

    @model_validator(mode="after")
    def _eligible_does_not_exceed_line(self) -> IncludedRepairPosition:
        if self.eligible_quantity > self.work_item.quantity:
            raise ValueError("eligible quantity cannot exceed work-item quantity")
        return self


class RepairWorkExclusion(BaseModel):
    """One conservative exclusion from future time-phased repair supply."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    repair_order_id: str | None = None
    repair_line_id: str | None = None
    serial_number: str | None = None
    quantity: NonNegativeInt
    reason: RepairWorkExclusionCode
    detail: str = Field(min_length=1)


class RepairPipeline(BaseModel):
    """Reconciled open-repair WIP for one tenant/part/location/as-of.

    ``identified_open_quantity`` is deduplicated by repair order and line.
    ``unidentified_source_quantity`` preserves rows whose physical quantity is
    visible but whose stable order/line identity is not. Both quantities consume
    the aggregate WIP before a residual is declared, so one missing-identity unit
    cannot also appear as unidentified aggregate residual.
    ``aggregate_residual_quantity`` is the positive aggregate WIP left after
    all observed source quantity is removed. ``source_overflow_quantity``
    discloses the opposite mismatch without treating it as additional physical
    supply.
    Phase 5 intentionally grants zero time-phased credit; Phase 6 consumes only
    ``included`` positions and computes horizon-specific expected returns.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["repair-pipeline.v1"] = "repair-pipeline.v1"
    tenant_id: str = Field(min_length=1)
    part_number: str = Field(min_length=1)
    location_code: str = Field(min_length=1)
    as_of: date
    status: Literal["available", "partial", "unavailable"]
    aggregate_wip_quantity: NonNegativeInt
    identified_open_quantity: NonNegativeInt
    unidentified_source_quantity: NonNegativeInt = 0
    eligible_quantity: NonNegativeInt
    excluded_identifiable_quantity: NonNegativeInt
    aggregate_residual_quantity: NonNegativeInt
    source_overflow_quantity: NonNegativeInt
    time_phased_credit_quantity: Literal[0] = 0
    included: tuple[IncludedRepairPosition, ...] = ()
    exclusions: tuple[RepairWorkExclusion, ...] = ()
    warning_codes: tuple[RepairPipelineWarningCode, ...] = ()
    evidence_source: Literal["open_orders_snapshot+stock_position"] = (
        "open_orders_snapshot+stock_position"
    )

    @model_validator(mode="after")
    def _quantities_reconcile(self) -> RepairPipeline:
        if self.eligible_quantity > self.aggregate_wip_quantity:
            raise ValueError("eligible repair quantity cannot exceed aggregate physical WIP")
        if self.eligible_quantity > self.identified_open_quantity:
            raise ValueError("eligible repair quantity cannot exceed identified open work")
        if (
            self.eligible_quantity + self.excluded_identifiable_quantity
            != self.identified_open_quantity
        ):
            raise ValueError("identified repair quantity does not reconcile")
        observed_source_quantity = (
            self.identified_open_quantity + self.unidentified_source_quantity
        )
        if self.aggregate_residual_quantity != max(
            0, self.aggregate_wip_quantity - observed_source_quantity
        ):
            raise ValueError("aggregate repair residual does not reconcile")
        if self.source_overflow_quantity != max(
            0, observed_source_quantity - self.aggregate_wip_quantity
        ):
            raise ValueError("repair source overflow does not reconcile")
        if sum(position.eligible_quantity for position in self.included) != (
            self.eligible_quantity
        ):
            raise ValueError("included repair positions do not reconcile")
        if self.status == "available" and (
            self.exclusions
            or self.warning_codes
            or self.aggregate_residual_quantity
            or self.source_overflow_quantity
        ):
            raise ValueError("available repair pipeline cannot carry exclusions or warnings")
        if self.status == "unavailable" and (
            "repair_pipeline_unavailable" not in self.warning_codes
        ):
            raise ValueError("unavailable repair pipeline requires an unavailable warning")
        return self


class RepairItemReturnProbability(BaseModel):
    """Residual return probability for one eligible repair line and horizon."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    repair_order_id: str = Field(min_length=1)
    repair_line_id: str = Field(min_length=1)
    serial_number: str | None = None
    quantity: PositiveInt
    age_days: NonNegativeInt
    return_probability: float = Field(ge=0.0, le=1.0)
    serviceable_probability: float = Field(ge=0.0, le=1.0)
    expected_serviceable_units: NonNegativeFloat

    @model_validator(mode="after")
    def _item_return_reconciles(self) -> RepairItemReturnProbability:
        tolerance = 1e-9
        if self.serviceable_probability > self.return_probability + tolerance:
            raise ValueError("serviceable probability cannot exceed return probability")
        expected = self.quantity * self.serviceable_probability
        if abs(self.expected_serviceable_units - expected) > tolerance:
            raise ValueError("item expected repair returns do not reconcile")
        return self


class RepairReturnHorizon(BaseModel):
    """Expected serviceable repair receipts within one inclusive horizon."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    horizon_days: NonNegativeInt
    eligible_quantity: NonNegativeInt
    expected_units: NonNegativeFloat
    variance_units: NonNegativeFloat
    p10_units: NonNegativeFloat
    p90_units: NonNegativeFloat
    mean_serviceable_probability: float = Field(ge=0.0, le=1.0)
    item_probabilities: tuple[RepairItemReturnProbability, ...] = ()

    @model_validator(mode="after")
    def _horizon_reconciles(self) -> RepairReturnHorizon:
        tolerance = 1e-9
        if self.expected_units > self.eligible_quantity + tolerance:
            raise ValueError("expected repair returns cannot exceed eligible work")
        if not (
            self.p10_units
            <= self.expected_units + tolerance
            <= self.p90_units + tolerance
            <= self.eligible_quantity + tolerance
        ):
            raise ValueError("repair return probability band is not ordered")
        expected = sum(
            item.expected_serviceable_units for item in self.item_probabilities
        )
        if abs(self.expected_units - expected) > tolerance:
            raise ValueError("repair return item expectations do not reconcile")
        if self.eligible_quantity:
            mean_probability = self.expected_units / self.eligible_quantity
            if abs(self.mean_serviceable_probability - mean_probability) > tolerance:
                raise ValueError("mean repair return probability does not reconcile")
        elif self.mean_serviceable_probability != 0:
            raise ValueError("empty eligible work must have zero return probability")
        return self


class RepairReturnEvidence(BaseModel):
    """Lineage for the survival or fallback model used by a return profile."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )

    method: Literal[
        "kaplan_meier",
        "lognormal_quantile",
        "deterministic_promise",
        "unavailable",
    ]
    completed_observations: NonNegativeInt
    right_censored_observations: NonNegativeInt
    serviceable_yield: float = Field(ge=0.0, le=1.0)
    tat_multiplier: float = Field(gt=0.0)
    source: str = Field(min_length=1)
    confidence: Literal["high", "medium", "low", "unavailable"]
    data_cutoff: date | None = None
    model_version: str = Field(min_length=1)
    proxy_definition: str | None = None


class RepairReturnProfile(BaseModel):
    """Horizon-specific, age-conditioned repair-supply projection."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    contract_version: Literal["repair-return-profile.v1"] = (
        "repair-return-profile.v1"
    )
    tenant_id: str = Field(min_length=1)
    part_number: str = Field(min_length=1)
    location_code: str = Field(min_length=1)
    as_of: date
    status: Literal["available", "partial", "unavailable"]
    eligible_quantity: NonNegativeInt
    excluded_quantity: NonNegativeInt
    aggregate_residual_quantity: NonNegativeInt
    horizons: tuple[RepairReturnHorizon, ...]
    exclusions: tuple[RepairWorkExclusion, ...] = ()
    evidence: RepairReturnEvidence
    warning_codes: tuple[str, ...] = ()

    @model_validator(mode="after")
    def _profile_is_bounded_and_monotone(self) -> RepairReturnProfile:
        horizon_days = [horizon.horizon_days for horizon in self.horizons]
        if horizon_days != sorted(set(horizon_days)):
            raise ValueError("repair return horizons must be unique and sorted")
        if any(
            horizon.eligible_quantity != self.eligible_quantity
            for horizon in self.horizons
        ):
            raise ValueError("repair return horizons must use the profile eligibility")
        expected = [horizon.expected_units for horizon in self.horizons]
        if any(
            later + 1e-9 < earlier
            for earlier, later in zip(expected, expected[1:], strict=False)
        ):
            raise ValueError("expected repair returns must be nondecreasing by horizon")
        identities: set[tuple[str, str]] = set()
        for horizon in self.horizons:
            current = {
                (item.repair_order_id, item.repair_line_id): item.return_probability
                for item in horizon.item_probabilities
            }
            if not identities:
                identities = set(current)
            elif set(current) != identities:
                raise ValueError("repair return item identities must persist across horizons")
        for identity in identities:
            probabilities = [
                next(
                    item.return_probability
                    for item in horizon.item_probabilities
                    if (item.repair_order_id, item.repair_line_id) == identity
                )
                for horizon in self.horizons
            ]
            if any(
                later + 1e-9 < earlier
                for earlier, later in zip(
                    probabilities,
                    probabilities[1:],
                    strict=False,
                )
            ):
                raise ValueError("item repair-return probability must be nondecreasing")
        if self.status == "unavailable" and any(
            horizon.expected_units for horizon in self.horizons
        ):
            raise ValueError("unavailable repair evidence cannot produce expected supply")
        return self
