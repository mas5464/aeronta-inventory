"""Build in-memory stores from a JSON demo/seed file so the CLI and API are runnable
without the production Iceberg/DynamoDB backends. The JSON shape is documented in the
README. Production callers wire a real FeatureStoreClient + InventoryStateProvider instead.
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any

from trax_io_feature_store import InMemoryFeatureStore
from trax_io_feature_store.schemas import (
    Criticality,
    CurrentPolicy,
    DemandHistory,
    DemandObservation,
    LeadTimeDistribution,
    OpenOrder,
    OpenOrdersSnapshot,
    PartAttributes,
    StockPosition,
    VendorEconomics,
)

from trax_io_reco.data.inventory_state import InMemoryInventoryState

_EXTRACT_DATE = date(2026, 4, 1)


def build_stores(
    data: dict[str, Any],
) -> tuple[InMemoryFeatureStore, InMemoryInventoryState, str, list[tuple[str, str]]]:
    tenant_id = data["tenant_id"]
    fs = InMemoryFeatureStore()
    inv = InMemoryInventoryState()
    keys: list[tuple[str, str]] = []

    for p in data["parts"]:
        pn, location = p["pn"], p["location"]
        keys.append((pn, location))
        vendor = p.get("vendor", "DEFAULT")
        units = p.get("monthly_units", [])
        rotable = bool(p.get("rotable", False))

        obs = [
            DemandObservation(
                bucket="month", period_start=date(2025, (i % 12) + 1, 1),
                removals=(u if rotable else 0), issues=(0 if rotable else u),
            )
            for i, u in enumerate(units)
        ]
        fs.seed(tenant_id, "demand_history", (pn, location),
                DemandHistory(tenant_id=tenant_id, pn=pn, location=location, observations=obs,
                              extract_date=_EXTRACT_DATE))
        fs.seed(tenant_id, "part_attributes", (pn,),
                PartAttributes(tenant_id=tenant_id, pn=pn, description=p.get("description"),
                               part_class=p.get("part_class", "expendable"),
                               shelf_life_days=p.get("shelf_life_days"),
                               hazardous_material=bool(p.get("hazmat", False)),
                               tool_control_item=bool(p.get("tool", False)),
                               extract_date=_EXTRACT_DATE))
        fs.seed(tenant_id, "criticality", (pn,),
                Criticality(tenant_id=tenant_id, pn=pn, raw_essentiality_code=str(p.get("tier", 4)),
                            canonical_tier=int(p.get("tier", 4)), extract_date=_EXTRACT_DATE))
        fs.seed(tenant_id, "vendor_economics", (pn, vendor),
                VendorEconomics(tenant_id=tenant_id, pn=pn, vendor=vendor,
                                unit_cost=Decimal(str(p.get("unit_cost", "100"))),
                                minimum_order_qty=int(p.get("min_oq", 1)),
                                extract_date=_EXTRACT_DATE))
        lead_mean = float(p.get("lead_mean_days", 21.0))
        fs.seed(tenant_id, "lead_time_distribution", (pn, vendor, "NEW"),
                LeadTimeDistribution(
                    tenant_id=tenant_id, pn=pn, vendor=vendor, condition="NEW",
                    promised_lead_days=lead_mean, realized_mean_days=lead_mean,
                    realized_p50_days=lead_mean, realized_p90_days=lead_mean * 1.3,
                    realized_p99_days=lead_mean * 1.6, promised_vs_actual_delta_mean=0.0,
                    n_observations=int(p.get("lead_obs", 10)), extract_date=_EXTRACT_DATE))
        open_qty = int(p.get("open_qty", 0))
        rcv = p.get("open_rcv_date")
        orders = (
            [OpenOrder(order_id="O1", order_type="PO", vendor=vendor, qty_open=open_qty,
                       expected_rcv_date=date.fromisoformat(rcv) if rcv else None)]
            if open_qty > 0 else []
        )
        fs.seed(tenant_id, "open_orders_snapshot", (pn, location),
                OpenOrdersSnapshot(tenant_id=tenant_id, pn=pn, location=location,
                                   snapshot_at=datetime(2026, 4, 1), orders=orders,
                                   total_open_qty=open_qty, extract_date=_EXTRACT_DATE))

        serviceable = int(p.get("serviceable", 0))
        in_repair = int(p.get("in_repair", 0))
        rop, eoq, ss, mx = p.get("current_policy", [5, 5, 2, 10])
        fs.seed(tenant_id, "stock_position", (pn, location),
                StockPosition(tenant_id=tenant_id, pn=pn, location=location,
                              on_hand=serviceable + in_repair, serviceable=serviceable,
                              allocated_reserved=int(p.get("allocated", 0)),
                              unserviceable_in_repair=in_repair, extract_date=_EXTRACT_DATE))
        fs.seed(tenant_id, "current_policy", (pn, location),
                CurrentPolicy(tenant_id=tenant_id, pn=pn, location=location, rop=rop, eoq=eoq,
                              safety_stock=ss, max_stock=mx, extract_date=_EXTRACT_DATE))

    return fs, inv, tenant_id, keys
