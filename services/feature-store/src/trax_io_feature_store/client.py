"""FeatureStoreClient Protocol + in-memory reference implementation.

The Protocol is the integration contract between the Agent Spine (sub-project
#4) and this service (sub-project #2). Any production backend
(GlueIcebergFeatureStore in later phases) MUST conform to the same surface so
the Spine swaps implementations at startup via DI.

Tenant isolation is the single most important invariant in this module: every
read requires a TenantContext. Cross-tenant reads raise
FeatureStoreLookupError; calls made without a TenantContext raise
MissingTenantContextError. The chokepoint is enforced at the client interface
level so downstream specialists cannot forget.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from trax_io_feature_store.schemas import (
    CausalUtilization,
    Criticality,
    DemandHistory,
    InterchangeableGraph,
    LeadTimeDistribution,
    LocationGraph,
    OpenOrdersSnapshot,
    PartAttributes,
    VendorEconomics,
    WashRateHistory,
)


class FeatureStoreLookupError(LookupError):
    """Raised when a feature lookup fails (missing part, cross-tenant read, etc.)."""


class MissingTenantContextError(ValueError):
    """Raised when a client call is made without a TenantContext.

    This is the tenant-isolation chokepoint. Do NOT make this recoverable —
    the absence of a tenant means we do not know which KMS key, which Iceberg
    namespace, or which DynamoDB table to read from. Failing closed is the
    only safe behavior.
    """


@dataclass(frozen=True, slots=True)
class TenantContext:
    """Identifies the tenant whose feature store is being read.

    In production, this is populated from the Cedar-authorized request
    context (see design §4.5). In tests and the in-memory stub, callers
    construct it directly.
    """

    tenant_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.tenant_id.strip():
            raise ValueError("tenant_id must be non-empty")


@runtime_checkable
class FeatureStoreClient(Protocol):
    """Read-side contract for the Trax IO Feature Store.

    Every method takes an explicit `tenant` kwarg. The production backend
    additionally verifies the Cedar principal matches the tenant; the
    in-memory stub relies on the kwarg alone.
    """

    def get_demand_history(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> DemandHistory: ...

    def get_causal_utilization(
        self, *, tenant: TenantContext, ac_type: str, destination: str
    ) -> CausalUtilization: ...

    def get_lead_time_distribution(
        self, *, tenant: TenantContext, pn: str, vendor: str, condition: str
    ) -> LeadTimeDistribution: ...

    def get_wash_rate_history(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> WashRateHistory: ...

    def get_vendor_economics(
        self, *, tenant: TenantContext, pn: str, vendor: str
    ) -> VendorEconomics: ...

    def get_part_attributes(self, *, tenant: TenantContext, pn: str) -> PartAttributes: ...

    def get_criticality(self, *, tenant: TenantContext, pn: str) -> Criticality: ...

    def get_interchangeable_graph(
        self, *, tenant: TenantContext, pn: str
    ) -> InterchangeableGraph: ...

    def get_location_graph(
        self, *, tenant: TenantContext, location: str
    ) -> LocationGraph: ...

    def get_open_orders_snapshot(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> OpenOrdersSnapshot: ...


def _require_tenant(tenant: TenantContext | None) -> TenantContext:
    if tenant is None:
        raise MissingTenantContextError(
            "tenant kwarg is required on all FeatureStoreClient reads"
        )
    if not isinstance(tenant, TenantContext):
        raise MissingTenantContextError(
            f"tenant must be TenantContext, got {type(tenant).__name__}"
        )
    return tenant


class InMemoryFeatureStore:
    """Dict-backed FeatureStoreClient for tests and shadow-mode pilots.

    Per ADR-0002 this is the reference implementation that the production
    GlueIcebergFeatureStore must remain observationally equivalent to. Shared
    contract tests (Sub-plan #2 Phase 6) run the same scenarios against
    both.

    Seed the store with `.seed(tenant_id, bucket, key, value)` where `bucket`
    is one of the feature-group names (e.g. "demand_history").
    """

    _BUCKETS = (
        "demand_history",
        "causal_utilization",
        "lead_time_distribution",
        "wash_rate_history",
        "vendor_economics",
        "part_attributes",
        "criticality",
        "interchangeable_graph",
        "location_graph",
        "open_orders_snapshot",
    )

    def __init__(self) -> None:
        # {tenant_id: {bucket: {key: value}}}
        self._data: dict[str, dict[str, dict[tuple[str, ...], object]]] = {}

    # --- seeding -------------------------------------------------------

    def seed(self, tenant_id: str, bucket: str, key: tuple[str, ...], value: object) -> None:
        if bucket not in self._BUCKETS:
            raise ValueError(f"unknown bucket: {bucket}")
        self._data.setdefault(tenant_id, {}).setdefault(bucket, {})[key] = value

    # --- internal ------------------------------------------------------

    def _fetch(self, tenant: TenantContext, bucket: str, key: tuple[str, ...]) -> object:
        tenant = _require_tenant(tenant)
        try:
            value = self._data[tenant.tenant_id][bucket][key]
        except KeyError as exc:
            raise FeatureStoreLookupError(
                f"no {bucket} row for tenant={tenant.tenant_id} key={key}"
            ) from exc
        return value

    # --- FeatureStoreClient surface -----------------------------------

    def get_demand_history(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> DemandHistory:
        return self._fetch(tenant, "demand_history", (pn, location))  # type: ignore[return-value]

    def get_causal_utilization(
        self, *, tenant: TenantContext, ac_type: str, destination: str
    ) -> CausalUtilization:
        return self._fetch(tenant, "causal_utilization", (ac_type, destination))  # type: ignore[return-value]

    def get_lead_time_distribution(
        self, *, tenant: TenantContext, pn: str, vendor: str, condition: str
    ) -> LeadTimeDistribution:
        return self._fetch(tenant, "lead_time_distribution", (pn, vendor, condition))  # type: ignore[return-value]

    def get_wash_rate_history(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> WashRateHistory:
        return self._fetch(tenant, "wash_rate_history", (pn, location))  # type: ignore[return-value]

    def get_vendor_economics(
        self, *, tenant: TenantContext, pn: str, vendor: str
    ) -> VendorEconomics:
        return self._fetch(tenant, "vendor_economics", (pn, vendor))  # type: ignore[return-value]

    def get_part_attributes(self, *, tenant: TenantContext, pn: str) -> PartAttributes:
        return self._fetch(tenant, "part_attributes", (pn,))  # type: ignore[return-value]

    def get_criticality(self, *, tenant: TenantContext, pn: str) -> Criticality:
        return self._fetch(tenant, "criticality", (pn,))  # type: ignore[return-value]

    def get_interchangeable_graph(
        self, *, tenant: TenantContext, pn: str
    ) -> InterchangeableGraph:
        return self._fetch(tenant, "interchangeable_graph", (pn,))  # type: ignore[return-value]

    def get_location_graph(self, *, tenant: TenantContext, location: str) -> LocationGraph:
        return self._fetch(tenant, "location_graph", (location,))  # type: ignore[return-value]

    def get_open_orders_snapshot(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> OpenOrdersSnapshot:
        return self._fetch(tenant, "open_orders_snapshot", (pn, location))  # type: ignore[return-value]
