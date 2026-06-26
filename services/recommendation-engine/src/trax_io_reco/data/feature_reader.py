"""Typed wrapper over the nine FeatureStoreClient reads the engine uses (spec §5.4).

Required groups (demand_history, vendor_economics, part_attributes, criticality)
propagate FeatureStoreLookupError; optional groups return None on miss.
"""

from __future__ import annotations

from trax_io_feature_store import FeatureStoreClient, FeatureStoreLookupError, TenantContext
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
)


class FeatureReader:
    def __init__(self, client: FeatureStoreClient) -> None:
        self._c = client

    # ---- required groups (propagate on miss) ---- #
    def get_demand_history(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> DemandHistory:
        return self._c.get_demand_history(tenant=tenant, pn=pn, location=location)

    def get_part_attributes(self, *, tenant: TenantContext, pn: str) -> PartAttributes:
        return self._c.get_part_attributes(tenant=tenant, pn=pn)

    def get_criticality(self, *, tenant: TenantContext, pn: str) -> Criticality:
        return self._c.get_criticality(tenant=tenant, pn=pn)

    def get_vendor_economics(
        self, *, tenant: TenantContext, pn: str, vendor: str
    ) -> VendorEconomics:
        return self._c.get_vendor_economics(tenant=tenant, pn=pn, vendor=vendor)

    # ---- optional groups (None on miss) ---- #
    def get_lead_time(
        self, *, tenant: TenantContext, pn: str, vendor: str, condition: str
    ) -> LeadTimeDistribution | None:
        try:
            return self._c.get_lead_time_distribution(
                tenant=tenant, pn=pn, vendor=vendor, condition=condition
            )
        except FeatureStoreLookupError:
            return None

    def get_location_graph(self, *, tenant: TenantContext, location: str) -> LocationGraph | None:
        try:
            return self._c.get_location_graph(tenant=tenant, location=location)
        except FeatureStoreLookupError:
            return None

    def get_open_orders(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> OpenOrdersSnapshot | None:
        try:
            return self._c.get_open_orders_snapshot(tenant=tenant, pn=pn, location=location)
        except FeatureStoreLookupError:
            return None

    def get_interchange(self, *, tenant: TenantContext, pn: str) -> InterchangeableGraph | None:
        try:
            return self._c.get_interchangeable_graph(tenant=tenant, pn=pn)
        except FeatureStoreLookupError:
            return None

    def get_causal(
        self, *, tenant: TenantContext, ac_type: str, destination: str
    ) -> CausalUtilization | None:
        try:
            return self._c.get_causal_utilization(
                tenant=tenant, ac_type=ac_type, destination=destination
            )
        except FeatureStoreLookupError:
            return None
