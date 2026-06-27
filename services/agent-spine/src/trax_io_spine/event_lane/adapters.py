"""Adapters that let the #11 engine run over an online FeatureBundle.

`BundleFeatureStore` satisfies the `FeatureStoreClient` Protocol by reading from a dict of
fetched bundles; `BundleInventoryState` supplies the engine's empty `InventoryStateProvider`
defaults (the bundle models none of those inputs).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_reco.contracts.context import AogSignal, RepairTat, ScheduledDemandItem

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_feature_store.schemas import (
        CausalUtilization,
        Criticality,
        CurrentPolicy,
        DemandHistory,
        FeatureBundle,
        InterchangeableGraph,
        LeadTimeDistribution,
        LocationGraph,
        OpenOrdersSnapshot,
        PartAttributes,
        StockPosition,
        VendorEconomics,
        WashRateHistory,
    )


def _present(value: object | None, what: str) -> object:
    if value is None:
        raise FeatureStoreLookupError(f"online bundle has no {what}")
    return value


class BundleFeatureStore:
    """`FeatureStoreClient` over a dict of online FeatureBundles, keyed by (pn, location)."""

    def __init__(self, tenant_id: str, bundles: dict[tuple[str, str], FeatureBundle]) -> None:
        self._tenant_id = tenant_id
        self._bundles = bundles

    # -- helpers ------------------------------------------------------------
    def _check(self, tenant: TenantContext) -> None:
        if tenant.tenant_id != self._tenant_id:
            raise FeatureStoreLookupError(
                f"no online data for tenant={tenant.tenant_id} (store is {self._tenant_id})"
            )

    def _kv(self, pn: str, location: str) -> FeatureBundle:
        b = self._bundles.get((pn, location))
        if b is None:
            raise FeatureStoreLookupError(f"no online bundle for pn={pn} location={location}")
        return b

    def _any_pn(self, pn: str) -> FeatureBundle:
        for (bpn, _), b in self._bundles.items():
            if bpn == pn:
                return b
        raise FeatureStoreLookupError(f"no online bundle for pn={pn}")

    def _any_location(self, location: str) -> FeatureBundle:
        for (_, bloc), b in self._bundles.items():
            if bloc == location:
                return b
        raise FeatureStoreLookupError(f"no online bundle for location={location}")

    # -- (pn, location)-level ----------------------------------------------
    def get_stock_position(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> StockPosition:
        self._check(tenant)
        return _present(  # type: ignore[return-value]
            self._kv(pn, location).stock_position, "stock_position"
        )

    def get_current_policy(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> CurrentPolicy:
        self._check(tenant)
        return _present(  # type: ignore[return-value]
            self._kv(pn, location).current_policy, "current_policy"
        )

    def get_demand_history(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> DemandHistory:
        self._check(tenant)
        return _present(  # type: ignore[return-value]
            self._kv(pn, location).demand_history, "demand_history"
        )

    def get_open_orders_snapshot(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> OpenOrdersSnapshot:
        self._check(tenant)
        return _present(  # type: ignore[return-value]
            self._kv(pn, location).open_orders_snapshot, "open_orders_snapshot"
        )

    # -- part-level ---------------------------------------------------------
    def get_part_attributes(self, *, tenant: TenantContext, pn: str) -> PartAttributes:
        self._check(tenant)
        return _present(  # type: ignore[return-value]
            self._any_pn(pn).part_attributes, "part_attributes"
        )

    def get_criticality(self, *, tenant: TenantContext, pn: str) -> Criticality:
        self._check(tenant)
        return _present(  # type: ignore[return-value]
            self._any_pn(pn).criticality, "criticality"
        )

    def get_interchangeable_graph(
        self, *, tenant: TenantContext, pn: str
    ) -> InterchangeableGraph:
        self._check(tenant)
        return _present(  # type: ignore[return-value]
            self._any_pn(pn).interchangeable_graph, "interchangeable_graph"
        )

    # -- location-level -----------------------------------------------------
    def get_location_graph(self, *, tenant: TenantContext, location: str) -> LocationGraph:
        self._check(tenant)
        return _present(  # type: ignore[return-value]
            self._any_location(location).location_graph, "location_graph"
        )

    # -- vendor-keyed -------------------------------------------------------
    def get_vendor_economics(
        self, *, tenant: TenantContext, pn: str, vendor: str
    ) -> VendorEconomics:
        self._check(tenant)
        ve = self._any_pn(pn).vendor_economics.get(vendor)
        return _present(ve, f"vendor_economics[{vendor}]")  # type: ignore[return-value]

    def get_lead_time_distribution(
        self, *, tenant: TenantContext, pn: str, vendor: str, condition: str
    ) -> LeadTimeDistribution:
        self._check(tenant)
        lt = self._any_pn(pn).lead_time_distribution.get(f"{vendor}|{condition}")
        return _present(lt, f"lead_time_distribution[{vendor}|{condition}]")  # type: ignore[return-value]

    # -- not modeled online (engine never reads these) ----------------------
    def get_causal_utilization(
        self, *, tenant: TenantContext, ac_type: str, destination: str
    ) -> CausalUtilization:
        raise FeatureStoreLookupError("causal_utilization is not in the online bundle")

    def get_wash_rate_history(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> WashRateHistory:
        raise FeatureStoreLookupError("wash_rate_history is not in the online bundle")


class BundleInventoryState:
    """`InventoryStateProvider` empty defaults (the bundle models none of these inputs)."""

    def get_scheduled_demand(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> tuple[ScheduledDemandItem, ...]:
        return ()

    def get_aog_signal(self, *, tenant: TenantContext, pn: str, location: str) -> AogSignal:
        return AogSignal()

    def get_repair_tat(self, *, tenant: TenantContext, pn: str) -> RepairTat:
        return RepairTat()
