"""Assemble a `FeatureBundle` for the online layer from any offline `FeatureStoreClient`.

This is the pure core of the nightly-Glue / event-lane population of the DynamoDB online table:
read the latest features for one ``(tenant, pn, location)`` and pack them into a single bundle. It
works against any `FeatureStoreClient` — the production `GlueIcebergFeatureStore` or the in-memory
stub — so the same assembly logic is exercised in tests and in the writer.

Optional feature groups that are absent in the offline lake become ``None`` (the bundle tolerates
gaps). Vendor-keyed groups (`vendor_economics`, `lead_time_distribution`) are fetched for the
canonical ``DEFAULT`` vendor plus any vendor named on the open orders, so the bundle is
self-sufficient for the engine's vendor resolution without a second round-trip.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING

from trax_io_feature_store.client import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.schemas import DemandHistory, FeatureBundle

if TYPE_CHECKING:  # pragma: no cover -- typing only
    from trax_io_feature_store.client import FeatureStoreClient

_CANONICAL_VENDOR = "DEFAULT"
# Keep the online bundle "thin" (design §4.2) and well under DynamoDB's 400 KB item cap: cap the
# number of demand observations carried online (the most recent are what inference needs). Full
# history stays in the offline Iceberg lake.
_DEFAULT_DEMAND_WINDOW = 24


def _opt[T](fn: Callable[[], T]) -> T | None:
    """Run a feature read, returning None on a miss (the bundle tolerates absent groups)."""
    try:
        return fn()
    except FeatureStoreLookupError:
        return None


def _window_demand(demand: DemandHistory | None, window: int) -> DemandHistory | None:
    """Keep only the most recent ``window`` observations (full history stays in Iceberg)."""
    if demand is None or len(demand.observations) <= window:
        return demand
    recent = sorted(demand.observations, key=lambda o: o.period_start)[-window:]
    return demand.model_copy(update={"observations": recent})


def materialize_bundle(
    offline: FeatureStoreClient,
    *,
    tenant: TenantContext,
    pn: str,
    location: str,
    conditions: Iterable[str] = ("NEW",),
    demand_window: int = _DEFAULT_DEMAND_WINDOW,
) -> FeatureBundle:
    """Read the latest features for ``(tenant, pn, location)`` and pack them into a bundle.

    Absent optional groups become ``None``. A ``None`` for a group the engine *requires* (e.g.
    ``stock_position``) means "data absent for this key" — NOT zero; the engine's online path must
    fail closed on it, exactly as the offline assembler propagates a miss. ``demand_history`` is
    windowed to ``demand_window`` recent observations to keep the item thin (design §4.2).
    """
    open_orders = _opt(
        lambda: offline.get_open_orders_snapshot(tenant=tenant, pn=pn, location=location)
    )

    vendors = {_CANONICAL_VENDOR}
    if open_orders is not None:
        vendors |= {o.vendor for o in open_orders.orders if o.vendor}

    vendor_economics = {}
    lead_time = {}
    for vendor in sorted(vendors):
        ve = _opt(lambda v=vendor: offline.get_vendor_economics(tenant=tenant, pn=pn, vendor=v))
        if ve is not None:
            vendor_economics[vendor] = ve
        for condition in conditions:
            lt = _opt(
                lambda v=vendor, c=condition: offline.get_lead_time_distribution(
                    tenant=tenant, pn=pn, vendor=v, condition=c
                )
            )
            if lt is not None:
                lead_time[f"{vendor}|{condition}"] = lt

    return FeatureBundle(
        tenant_id=tenant.tenant_id,
        pn=pn,
        location=location,
        stock_position=_opt(
            lambda: offline.get_stock_position(tenant=tenant, pn=pn, location=location)
        ),
        current_policy=_opt(
            lambda: offline.get_current_policy(tenant=tenant, pn=pn, location=location)
        ),
        demand_history=_window_demand(
            _opt(lambda: offline.get_demand_history(tenant=tenant, pn=pn, location=location)),
            demand_window,
        ),
        open_orders_snapshot=open_orders,
        location_graph=_opt(lambda: offline.get_location_graph(tenant=tenant, location=location)),
        part_attributes=_opt(lambda: offline.get_part_attributes(tenant=tenant, pn=pn)),
        criticality=_opt(lambda: offline.get_criticality(tenant=tenant, pn=pn)),
        interchangeable_graph=_opt(
            lambda: offline.get_interchangeable_graph(tenant=tenant, pn=pn)
        ),
        vendor_economics=vendor_economics,
        lead_time_distribution=lead_time,
    )
