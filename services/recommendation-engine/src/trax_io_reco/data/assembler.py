"""Assemble one PartLocationContext from the feature store + inventory-state provider
+ tenant config (spec §4.2). Resolves the vendor deterministically (preferred open-order
vendor, else a configured default) before vendor-keyed reads.
"""

from __future__ import annotations

from datetime import date

from trax_io_feature_store import FeatureStoreLookupError, TenantContext

from trax_io_reco.contracts.context import PartLocationContext, TenantPolicyConfig
from trax_io_reco.data.feature_reader import FeatureReader
from trax_io_reco.data.inventory_state import InventoryStateProvider


class ContextAssembler:
    def __init__(
        self,
        *,
        features: FeatureReader,
        inventory_state: InventoryStateProvider,
        config: TenantPolicyConfig | None = None,
        default_vendor: str = "DEFAULT",
        default_condition: str = "NEW",
    ) -> None:
        self._fr = features
        self._inv = inventory_state
        self._config = config or TenantPolicyConfig()
        self._default_vendor = default_vendor
        self._default_condition = default_condition

    def assemble(self, *, tenant: TenantContext, pn: str, location: str) -> PartLocationContext:
        # Required FS reads (propagate on miss → caller records a skipped key).
        demand_history = self._fr.get_demand_history(tenant=tenant, pn=pn, location=location)
        part_attributes = self._fr.get_part_attributes(tenant=tenant, pn=pn)
        criticality = self._fr.get_criticality(tenant=tenant, pn=pn)

        # Optional FS reads.
        open_orders = self._fr.get_open_orders(tenant=tenant, pn=pn, location=location)
        requisition = self._fr.get_requisition(tenant=tenant, pn=pn, location=location)
        interchange = self._fr.get_interchange(tenant=tenant, pn=pn)
        location_graph = self._fr.get_location_graph(tenant=tenant, location=location)

        procurement_vendor = self._resolve_vendor(open_orders, order_type="PO")
        repair_vendor = self._resolve_vendor(open_orders, order_type="RO")
        try:
            vendor_economics = self._fr.get_vendor_economics(
                tenant=tenant,
                pn=pn,
                vendor=procurement_vendor,
            )
        except FeatureStoreLookupError:
            if procurement_vendor == self._default_vendor:
                raise
            vendor_economics = self._fr.get_vendor_economics(
                tenant=tenant,
                pn=pn,
                vendor=self._default_vendor,
            )
        lead_time = self._fr.get_procurement_lead_time(
            tenant=tenant,
            pn=pn,
            vendor=procurement_vendor,
        )
        if lead_time is None and procurement_vendor != self._default_vendor:
            lead_time = self._fr.get_procurement_lead_time(
                tenant=tenant,
                pn=pn,
                vendor=self._default_vendor,
            )
        repair_cycle_time = self._fr.get_repair_cycle_time(
            tenant=tenant,
            pn=pn,
            vendor=repair_vendor,
        )
        if repair_cycle_time is None and repair_vendor != self._default_vendor:
            repair_cycle_time = self._fr.get_repair_cycle_time(
                tenant=tenant,
                pn=pn,
                vendor=self._default_vendor,
            )

        # Stock position + current policy are now Feature-Store groups (Phase 2 promotion);
        # required, propagate FeatureStoreLookupError on miss.
        stock_position = self._fr.get_stock_position(tenant=tenant, pn=pn, location=location)
        current_policy = self._fr.get_current_policy(tenant=tenant, pn=pn, location=location)

        # Provider reads (genuine gap inputs).
        scheduled = self._inv.get_scheduled_demand(tenant=tenant, pn=pn, location=location)
        status_reader = getattr(self._inv, "get_scheduled_demand_status", None)
        scheduled_status = (
            status_reader(tenant=tenant, pn=pn, location=location)
            if callable(status_reader)
            else ("available" if scheduled else "unavailable")
        )
        if (
            scheduled_status == "available"
            and requisition is not None
            and any(line.need_by is None for line in requisition.lines)
        ):
            scheduled_status = "partial"
        aog_signal = self._inv.get_aog_signal(tenant=tenant, pn=pn, location=location)
        repair_tat = self._inv.get_repair_tat(tenant=tenant, pn=pn)

        return PartLocationContext(
            tenant_id=tenant.tenant_id,
            pn=pn,
            location=location,
            stock_position=stock_position,
            current_policy=current_policy,
            vendor_economics=vendor_economics,
            part_attributes=part_attributes,
            criticality=criticality,
            lead_time=lead_time,
            repair_cycle_time=repair_cycle_time,
            location_graph=location_graph,
            open_orders=open_orders,
            requisition=requisition,
            interchange_group=interchange,
            demand_history=demand_history,
            causal=None,  # causal scaling deferred to v2 (spec §4.5); wired, unused in v1
            scheduled_demand=scheduled,
            scheduled_demand_status=scheduled_status,
            aog_signal=aog_signal,
            repair_tat=repair_tat,
            tenant_policy_config=self._config,
        )

    def _resolve_vendor(self, open_orders: object, *, order_type: str) -> str:
        orders = getattr(open_orders, "orders", None)
        if orders:
            # Procurement and repair vendors are resolved from their own lanes.
            # In particular, an earlier RO must never select procurement economics.
            ordered = sorted(
                (
                    order
                    for order in orders
                    if getattr(order, "order_type", None) == order_type
                ),
                key=lambda o: (
                    getattr(o, "expected_rcv_date", None) or date.max,
                    getattr(o, "order_id", ""),
                ),
            )
            if ordered:
                first_vendor = getattr(ordered[0], "vendor", None)
                if first_vendor:
                    return first_vendor
        return self._default_vendor
