"""Pydantic v2 models for the 10 feature groups called out in design §4.2.

These are the schemas only — no materialization logic, no ETL. The models
are the authoritative contract for what flows through the Iceberg + DynamoDB
layers; Glue jobs and the online-layer writer will serialize/deserialize
against them.

Every model carries `tenant_id` so that mis-tenanted rows fail loudly at
validation time. The production Iceberg layout partitions on
`(tenant_id, extract_date)`; `extract_date` is captured on each row.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Literal

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    NonNegativeFloat,
    NonNegativeInt,
    field_validator,
    model_validator,
)


class _Base(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
    )


# ---------------------------------------------------------------------------
# 1. demand_history
# ---------------------------------------------------------------------------


class DemandObservation(_Base):
    """One bucketed demand observation (day/week/month)."""

    bucket: Literal["day", "week", "month"]
    period_start: date
    removals: NonNegativeInt = 0  # rotable
    issues: NonNegativeInt = 0  # expendable
    # Event counts are deliberately distinct from demanded units: one source row
    # with QTY=7 is one event and seven units. ``None`` identifies legacy payloads
    # that predate event-count persistence.
    removal_events: NonNegativeInt | None = None
    issue_events: NonNegativeInt | None = None


class DemandHistory(_Base):
    """Rolled removals + issues per (tenant, pn, location).

    Source: AC_PN_TRANSACTION_HISTORY + PN_INVENTORY_HISTORY (design §4.3).
    Interchange rollup is applied before policy calc but stored separately.
    """

    tenant_id: str
    pn: str
    location: str
    interchange_group_id: str | None = None
    observation_start: date | None = None
    observation_end: date | None = None
    bucket: Literal["day", "week", "month"] | None = None
    event_count_source: Literal["observed", "bucket_fallback", "unavailable"] = "unavailable"
    observations: list[DemandObservation] = Field(default_factory=list)
    extract_date: date
    source: Literal["nightly_extract", "event_cdc"] = "nightly_extract"

    @model_validator(mode="after")
    def _valid_observation_window(self) -> DemandHistory:
        if (self.observation_start is None) != (self.observation_end is None):
            raise ValueError("observation_start and observation_end must be supplied together")
        if (
            self.observation_start is not None
            and self.observation_end is not None
            and self.observation_end < self.observation_start
        ):
            raise ValueError("observation_end must not precede observation_start")
        if self.event_count_source == "observed" and any(
            observation.removal_events is None or observation.issue_events is None
            for observation in self.observations
        ):
            raise ValueError(
                "event_count_source='observed' requires explicit removal_events "
                "and issue_events on every observation"
            )
        return self


# ---------------------------------------------------------------------------
# 2. causal_utilization
# ---------------------------------------------------------------------------


class CausalUtilization(_Base):
    """Flight hours + cycles by AC type x destination x day.

    Source: AC_ACTUAL_FLIGHTS x AC_MASTER (design §4.3).
    """

    tenant_id: str
    ac_type: str
    destination: str
    observation_date: date
    flight_hours: NonNegativeFloat
    flight_cycles: NonNegativeInt
    extract_date: date


# ---------------------------------------------------------------------------
# 3. lead_time_distribution
# ---------------------------------------------------------------------------


class LeadTimeDistribution(_Base):
    """Supply-cycle distribution per ``(pn, vendor, condition)``.

    ``NEW`` is procurement lead time. ``REP`` is descriptive repair cycle time;
    an ``order_creation_to_last_receipt`` proxy is explicitly not projected
    repair supply. Provenance defaults make pre-Phase-3 snapshots load without
    silently relabeling their blended metrics as observed or configured.
    """

    tenant_id: str
    pn: str
    vendor: str
    condition: Literal["NEW", "SV", "OH", "AR", "USED", "REP"]
    promised_lead_days: NonNegativeFloat | None = None
    realized_mean_days: NonNegativeFloat
    realized_p50_days: NonNegativeFloat
    realized_p90_days: NonNegativeFloat
    realized_p99_days: NonNegativeFloat
    promised_vs_actual_delta_mean: float | None = None
    n_observations: NonNegativeInt
    # Additive raw duration carrier for survival/censoring consumers. Legacy
    # distributions remain valid with an empty tuple and fall back explicitly.
    observed_cycle_days: tuple[NonNegativeFloat, ...] = ()
    extract_date: date
    evidence_status: Literal[
        "observed",
        "configured_fallback",
        "legacy_unknown",
    ] = "legacy_unknown"
    source: Literal[
        "order_plan_closed_orders",
        "pn_vendor_price",
        "legacy_unknown",
    ] = "legacy_unknown"
    grouping_level: Literal[
        "part_vendor_condition",
        "part_condition",
        "legacy_unknown",
    ] = "legacy_unknown"
    confidence: Literal["high", "medium", "low", "unknown"] = "unknown"
    data_cutoff: date | None = None
    model_version: str = Field(default="legacy-v0", min_length=1)
    proxy_definition: Literal[
        "order_creation_to_last_receipt",
        "configured_repair_promise",
    ] | None = None
    classification_source: Literal[
        "explicit_order_type",
        "legacy_order_id_prefix",
        "configured_condition",
        "legacy_default_new",
        "legacy_unknown",
    ] = "legacy_unknown"

    @model_validator(mode="after")
    def _coherent_provenance(self) -> LeadTimeDistribution:
        # Missing Phase-3 fields identify an old snapshot. Keep it loadable and
        # explicitly unknown instead of retroactively inferring provenance.
        if self.evidence_status == "legacy_unknown":
            return self

        if self.data_cutoff is None:
            raise ValueError("observed/configured supply-cycle evidence requires data_cutoff")
        if self.grouping_level == "legacy_unknown" or self.confidence == "unknown":
            raise ValueError(
                "observed/configured supply-cycle evidence requires grouping and confidence"
            )
        if self.condition not in {"NEW", "REP"} or self.model_version == "legacy-v0":
            raise ValueError(
                "new supply-cycle evidence requires a NEW/REP lane and non-legacy model"
            )
        if not (
            self.realized_p50_days
            <= self.realized_p90_days
            <= self.realized_p99_days
        ):
            raise ValueError("supply-cycle quantiles must be monotonic")
        if tuple(sorted(self.observed_cycle_days)) != self.observed_cycle_days:
            raise ValueError("observed supply-cycle durations must be sorted")

        if self.evidence_status == "observed":
            if self.source != "order_plan_closed_orders" or self.n_observations == 0:
                raise ValueError(
                    "observed supply-cycle evidence requires closed orders and observations"
                )
            if self.classification_source not in {
                "explicit_order_type",
                "legacy_order_id_prefix",
            }:
                raise ValueError("observed supply-cycle evidence requires order classification")
            expected_proxy = (
                "order_creation_to_last_receipt" if self.condition == "REP" else None
            )
            if self.proxy_definition != expected_proxy:
                raise ValueError(
                    "REP observed evidence must use order_creation_to_last_receipt; "
                    "procurement evidence must not carry a repair proxy"
                )
            if self.observed_cycle_days and (
                len(self.observed_cycle_days) != self.n_observations
            ):
                raise ValueError(
                    "observed supply-cycle durations must reconcile to observation count"
                )
            if self.model_version == "supply-cycle-v2" and (
                len(self.observed_cycle_days) != self.n_observations
            ):
                raise ValueError(
                    "supply-cycle-v2 observed evidence requires every raw duration"
                )
        else:
            if (
                self.source != "pn_vendor_price"
                or self.n_observations != 0
                or self.promised_lead_days is None
                or self.classification_source
                not in {
                    "explicit_order_type",
                    "configured_condition",
                    "legacy_default_new",
                }
                or self.confidence != "low"
            ):
                raise ValueError(
                    "configured fallback requires a classified price promise and zero "
                    "observations"
                )
            if self.observed_cycle_days:
                raise ValueError(
                    "configured fallback cannot carry observed cycle durations"
                )
            expected_proxy = "configured_repair_promise" if self.condition == "REP" else None
            if self.proxy_definition != expected_proxy:
                raise ValueError(
                    "REP configured fallback must identify configured_repair_promise; "
                    "procurement fallback must not carry a repair proxy"
                )
        return self


# ---------------------------------------------------------------------------
# 4. wash_rate_history
# ---------------------------------------------------------------------------


class WashRatePoint(_Base):
    period_month: date  # first day of month
    wash_rate: float = Field(ge=0.0, le=1.0)


class WashRateHistory(_Base):
    """Trend of the PartMaster wash rate formula (design §4.3)."""

    tenant_id: str
    pn: str
    location: str
    points: list[WashRatePoint] = Field(default_factory=list)
    extract_date: date


# ---------------------------------------------------------------------------
# 5. vendor_economics
# ---------------------------------------------------------------------------


class VendorEconomics(_Base):
    """Cost + commercial terms per (pn, vendor).

    Source: PN_VENDOR_PRICE, ORDER_INVOICE, PKG_TRAX_PTC.getKitCost (§4.3).
    """

    tenant_id: str
    pn: str
    vendor: str
    unit_cost: Decimal
    market_value_unit_cost: Decimal | None = None
    average_cost: Decimal | None = None
    kit_cost: Decimal | None = None
    repair_cost_24mo_avg: Decimal | None = None
    minimum_order_qty: NonNegativeInt = 1
    currency: str = "USD"
    extract_date: date


# ---------------------------------------------------------------------------
# 6. part_attributes
# ---------------------------------------------------------------------------


class PartAttributes(_Base):
    """Hard-constraint + descriptive attributes per PN (design §4.3)."""

    tenant_id: str
    pn: str
    description: str | None = None
    ata_chapter: str | None = None
    part_class: Literal["rotable", "repairable", "expendable", "consumable"] | None = None
    shelf_life_days: NonNegativeInt | None = None
    hazardous_material: bool = False
    tool_control_item: bool = False
    fleet_effectivity_tail_count: NonNegativeInt | None = None
    extract_date: date


# ---------------------------------------------------------------------------
# 7. criticality
# ---------------------------------------------------------------------------


class Criticality(_Base):
    """Essentiality code normalized to canonical 5-tier scale (design §4.3)."""

    tenant_id: str
    pn: str
    raw_essentiality_code: str
    canonical_tier: Literal[1, 2, 3, 4, 5]
    mapping_source: Literal["auto_inferred", "planner_override"] = "auto_inferred"
    extract_date: date


# ---------------------------------------------------------------------------
# 8. interchangeable_graph
# ---------------------------------------------------------------------------


class InterchangeEdge(_Base):
    from_pn: str
    to_pn: str
    one_way: bool = False


class InterchangeableGraph(_Base):
    """Interchangeability rollup per PN (design §4.3).

    The rollup is applied before the policy calc; one-way chains are honored
    or else stock is over-sized.
    """

    tenant_id: str
    pn: str
    group_id: str
    members: list[str] = Field(default_factory=list)
    edges: list[InterchangeEdge] = Field(default_factory=list)
    extract_date: date


# ---------------------------------------------------------------------------
# 9. location_graph
# ---------------------------------------------------------------------------


class LocationNode(_Base):
    location: str
    related_main_warehouse: str | None = None
    role: Literal["main", "outstation"] = "outstation"


class LocationGraph(_Base):
    """Location hierarchy from LOCATION_MASTER.RELATED_MAIN_WAREHOUSE."""

    tenant_id: str
    location: str
    node: LocationNode
    children: list[str] = Field(default_factory=list)
    extract_date: date


# ---------------------------------------------------------------------------
# 10. open_orders_snapshot
# ---------------------------------------------------------------------------


class OpenOrder(_Base):
    order_id: str
    order_type: Literal["PO", "RO"]
    vendor: str | None = None
    qty_open: NonNegativeInt
    expected_rcv_date: date | None = None
    order_line_id: str | None = None
    opened_at: datetime | None = None
    status: str = "OPEN"
    serial_number: str | None = None
    shop: str | None = None
    location: str | None = None

    @field_validator(
        "order_line_id",
        "serial_number",
        "shop",
        "location",
        mode="before",
    )
    @classmethod
    def _blank_repair_evidence_is_none(cls, value: object) -> object:
        return None if value is None or str(value).strip() == "" else str(value)

    @field_validator("status", mode="before")
    @classmethod
    def _normalize_status(cls, value: object) -> str:
        normalized = str(value or "").strip()
        return normalized.upper() if normalized else "OPEN"


class OpenOrdersSnapshot(_Base):
    """Open POs + ROs per (pn, location) as of snapshot_at."""

    tenant_id: str
    pn: str
    location: str
    snapshot_at: datetime
    orders: list[OpenOrder] = Field(default_factory=list)
    total_open_qty: NonNegativeInt
    extract_date: date


# ---------------------------------------------------------------------------
# 11. requisition_snapshot
# ---------------------------------------------------------------------------


class RequisitionLine(_Base):
    requisition_id: str
    qty_needed: NonNegativeInt
    need_by: date | None = None
    alt_source_location: str | None = None


class RequisitionSnapshot(_Base):
    """Open (unfulfilled) demand-side requisition lines per (pn, location) as of
    snapshot_at. Deliberately separate from OpenOrdersSnapshot: this is demand,
    that is supply."""

    tenant_id: str
    pn: str
    location: str
    snapshot_at: datetime
    lines: list[RequisitionLine] = Field(default_factory=list)
    total_qty_needed: NonNegativeInt
    extract_date: date


# ---------------------------------------------------------------------------
# 12. stock_position   (promoted from the engine's gap stubs — source: stock_amount #18)
# ---------------------------------------------------------------------------


class StockPosition(_Base):
    """On-hand stock by (pn, location). Source: stock_amount #18.

    Dispatchable stock is ``serviceable - allocated_reserved``; in-repair, rental, and
    loan are excluded from dispatchable availability (consumed by the recommendation engine).
    """

    tenant_id: str
    pn: str
    location: str
    on_hand: NonNegativeInt
    serviceable: NonNegativeInt
    unserviceable_in_repair: NonNegativeInt = 0
    allocated_reserved: NonNegativeInt = 0
    rental: NonNegativeInt = 0
    loan: NonNegativeInt = 0
    extract_date: date


# ---------------------------------------------------------------------------
# 13. current_policy   (the existing PN_INVENTORY_LEVEL values — source: stock_level_upload #19)
# ---------------------------------------------------------------------------


class CurrentPolicy(_Base):
    """The current ROP/EOQ/SS/Max in PN_INVENTORY_LEVEL. Source: stock_level_upload #19
    (the canonical extract transposes PN/LOCATION; the SQL is corrected at source)."""

    tenant_id: str
    pn: str
    location: str
    rop: NonNegativeInt
    eoq: NonNegativeInt
    safety_stock: NonNegativeInt
    max_stock: NonNegativeInt
    replenishment_lead_days: NonNegativeFloat = 0.0
    extract_date: date


# ---------------------------------------------------------------------------
# Online feature bundle — the denormalized per-(pn, location) row (design §4.2).
# ---------------------------------------------------------------------------


class FeatureBundle(_Base):
    """All features touching one ``(tenant_id, pn, location)``, for a single sub-10ms read.

    The online DynamoDB layer (design §4.2) is keyed on ``(tenant_id, pn, location)`` and serves
    one item per inference key so event-triggered inference does a single point lookup instead of
    ~12 feature reads. The vendor-keyed groups are kept as small maps (``vendor`` and
    ``vendor|condition``) so the engine can still resolve a vendor without leaving the bundle.
    Optional members are ``None`` when the offline lake has no row for that key.
    """

    tenant_id: str
    pn: str
    location: str
    stock_position: StockPosition | None = None
    current_policy: CurrentPolicy | None = None
    demand_history: DemandHistory | None = None
    open_orders_snapshot: OpenOrdersSnapshot | None = None
    requisition_snapshot: RequisitionSnapshot | None = None
    location_graph: LocationGraph | None = None
    part_attributes: PartAttributes | None = None
    criticality: Criticality | None = None
    interchangeable_graph: InterchangeableGraph | None = None
    vendor_economics: dict[str, VendorEconomics] = Field(default_factory=dict)
    lead_time_distribution: dict[str, LeadTimeDistribution] = Field(default_factory=dict)

    @field_validator("pn", "location")
    @classmethod
    def _non_empty_key(cls, v: str) -> str:
        # The online sort key is derived from (pn, location); an empty component is never valid.
        if not v:
            raise ValueError("pn and location must be non-empty")
        return v

    @model_validator(mode="after")
    def _nested_features_match_bundle_identity(self) -> FeatureBundle:
        """Reject cross-key or cross-tenant features before they enter the online lane."""

        expected = {
            "tenant_id": self.tenant_id,
            "pn": self.pn,
            "location": self.location,
        }

        def require_identity(
            name: str,
            feature: _Base | None,
            fields: tuple[str, ...],
        ) -> None:
            if feature is None:
                return
            mismatches = {
                field: (expected[field], getattr(feature, field))
                for field in fields
                if getattr(feature, field) != expected[field]
            }
            if mismatches:
                raise ValueError(f"{name} identity mismatch: {mismatches}")

        for field in (
            "stock_position",
            "current_policy",
            "demand_history",
            "open_orders_snapshot",
            "requisition_snapshot",
        ):
            require_identity(
                field,
                getattr(self, field),
                ("tenant_id", "pn", "location"),
            )
        for field in (
            "part_attributes",
            "criticality",
            "interchangeable_graph",
        ):
            require_identity(
                field,
                getattr(self, field),
                ("tenant_id", "pn"),
            )
        require_identity(
            "location_graph",
            self.location_graph,
            ("tenant_id", "location"),
        )
        if (
            self.location_graph is not None
            and self.location_graph.node.location != self.location
        ):
            raise ValueError(
                "location_graph.node identity mismatch: "
                f"expected location={self.location!r}, "
                f"got {self.location_graph.node.location!r}"
            )

        for key, feature in self.vendor_economics.items():
            require_identity(
                f"vendor_economics[{key!r}]",
                feature,
                ("tenant_id", "pn"),
            )
            if key != feature.vendor:
                raise ValueError(
                    "vendor_economics map key mismatch: "
                    f"key={key!r}, feature.vendor={feature.vendor!r}"
                )
        for key, feature in self.lead_time_distribution.items():
            require_identity(
                f"lead_time_distribution[{key!r}]",
                feature,
                ("tenant_id", "pn"),
            )
            expected_key = f"{feature.vendor}|{feature.condition}"
            if key != expected_key:
                raise ValueError(
                    "lead_time_distribution map key mismatch: "
                    f"key={key!r}, expected={expected_key!r}"
                )
        return self
