"""Adapters that let the #11 engine run over an online FeatureBundle.

`BundleFeatureStore` satisfies the `FeatureStoreClient` Protocol by reading from a dict of
fetched bundles. `BundleInventoryState` derives dated scheduled demand from the same
tenant-bound bundle snapshot and supplies empty defaults only for inputs not modeled online.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_reco.contracts.context import AogSignal, RepairTat, ScheduledDemandItem
from trax_io_reco.contracts.enums import EvidenceKind
from trax_io_reco.data.inventory_state import InventoryStateLookupError

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
        RequisitionSnapshot,
        StockPosition,
        VendorEconomics,
        WashRateHistory,
    )


def _present(value: object | None, what: str) -> object:
    if value is None:
        raise FeatureStoreLookupError(f"online bundle has no {what}")
    return value


def _checked_bundle(
    bundle: FeatureBundle,
    *,
    tenant_id: str,
    pn: str,
    location: str,
) -> FeatureBundle:
    """Reject a corrupt bundle or one stored under a disagreeing dictionary key."""

    try:
        # ``model_copy(update=...)`` does not re-run Pydantic validators. Revalidate
        # from primitives at this trust boundary so an in-memory/event-lane caller
        # cannot bypass the nested FeatureBundle identity invariant.
        bundle = type(bundle).model_validate(bundle.model_dump())
    except (TypeError, ValueError) as exc:
        raise FeatureStoreLookupError(
            f"invalid online bundle for tenant={tenant_id} pn={pn} location={location}"
        ) from exc

    actual = (bundle.tenant_id, bundle.pn, bundle.location)
    expected = (tenant_id, pn, location)
    if actual != expected:
        raise FeatureStoreLookupError(
            f"online bundle identity mismatch expected={expected!r} actual={actual!r}"
        )
    return bundle


def _checked_requisition(
    snapshot: RequisitionSnapshot,
    *,
    tenant_id: str,
    pn: str,
    location: str,
) -> RequisitionSnapshot:
    """Reject nested requisition data copied from another tenant or inventory key."""

    actual = (snapshot.tenant_id, snapshot.pn, snapshot.location)
    expected = (tenant_id, pn, location)
    if actual != expected:
        raise FeatureStoreLookupError(
            f"requisition snapshot identity mismatch expected={expected!r} actual={actual!r}"
        )
    return snapshot


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
        return _checked_bundle(
            b,
            tenant_id=self._tenant_id,
            pn=pn,
            location=location,
        )

    def _any_pn(self, pn: str) -> FeatureBundle:
        for (bpn, location), b in self._bundles.items():
            if bpn == pn:
                return _checked_bundle(
                    b,
                    tenant_id=self._tenant_id,
                    pn=bpn,
                    location=location,
                )
        raise FeatureStoreLookupError(f"no online bundle for pn={pn}")

    def _any_location(self, location: str) -> FeatureBundle:
        for (pn, bloc), b in self._bundles.items():
            if bloc == location:
                return _checked_bundle(
                    b,
                    tenant_id=self._tenant_id,
                    pn=pn,
                    location=bloc,
                )
        raise FeatureStoreLookupError(f"no online bundle for location={location}")

    # -- (pn, location)-level ----------------------------------------------
    def get_stock_position(self, *, tenant: TenantContext, pn: str, location: str) -> StockPosition:
        self._check(tenant)
        return _present(  # type: ignore[return-value]
            self._kv(pn, location).stock_position, "stock_position"
        )

    def get_current_policy(self, *, tenant: TenantContext, pn: str, location: str) -> CurrentPolicy:
        self._check(tenant)
        return _present(  # type: ignore[return-value]
            self._kv(pn, location).current_policy, "current_policy"
        )

    def get_demand_history(self, *, tenant: TenantContext, pn: str, location: str) -> DemandHistory:
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

    def get_requisition_snapshot(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> RequisitionSnapshot:
        self._check(tenant)
        snapshot = _present(
            self._kv(pn, location).requisition_snapshot,
            "requisition_snapshot",
        )
        return _checked_requisition(  # type: ignore[arg-type]
            snapshot,
            tenant_id=self._tenant_id,
            pn=pn,
            location=location,
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

    def get_interchangeable_graph(self, *, tenant: TenantContext, pn: str) -> InterchangeableGraph:
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
    """Tenant-bound `InventoryStateProvider` backed by online feature bundles.

    A zero-argument instance remains a safe legacy empty provider: it owns no data and
    therefore cannot leak across tenants. Any bundle-backed instance requires an
    explicit tenant id and enforces tenant, dictionary-key, bundle-key, and nested
    requisition-key agreement before returning data.
    """

    def __init__(
        self,
        tenant_id: str | None = None,
        bundles: dict[tuple[str, str], FeatureBundle] | None = None,
    ) -> None:
        if (tenant_id is None) != (bundles is None):
            raise ValueError("tenant_id and bundles must be supplied together")
        self._tenant_id = tenant_id
        self._bundles = bundles

    def _check(self, tenant: TenantContext) -> None:
        if self._tenant_id is not None and tenant.tenant_id != self._tenant_id:
            raise InventoryStateLookupError(
                f"no online inventory state for tenant={tenant.tenant_id} "
                f"(provider is {self._tenant_id})"
            )

    def _kv(self, tenant: TenantContext, pn: str, location: str) -> FeatureBundle | None:
        self._check(tenant)
        if self._bundles is None:
            return None
        bundle = self._bundles.get((pn, location))
        if bundle is None:
            raise InventoryStateLookupError(
                f"no online inventory state for pn={pn} location={location}"
            )
        try:
            return _checked_bundle(
                bundle,
                tenant_id=self._tenant_id or tenant.tenant_id,
                pn=pn,
                location=location,
            )
        except FeatureStoreLookupError as exc:
            raise InventoryStateLookupError(str(exc)) from exc

    def _any_pn(self, tenant: TenantContext, pn: str) -> FeatureBundle | None:
        self._check(tenant)
        if self._bundles is None:
            return None
        for (bundle_pn, location), bundle in self._bundles.items():
            if bundle_pn != pn:
                continue
            try:
                return _checked_bundle(
                    bundle,
                    tenant_id=self._tenant_id or tenant.tenant_id,
                    pn=bundle_pn,
                    location=location,
                )
            except FeatureStoreLookupError as exc:
                raise InventoryStateLookupError(str(exc)) from exc
        raise InventoryStateLookupError(f"no online inventory state for pn={pn}")

    def get_scheduled_demand(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> tuple[ScheduledDemandItem, ...]:
        bundle = self._kv(tenant, pn, location)
        if bundle is None or bundle.requisition_snapshot is None:
            return ()
        try:
            snapshot = _checked_requisition(
                bundle.requisition_snapshot,
                tenant_id=self._tenant_id or tenant.tenant_id,
                pn=pn,
                location=location,
            )
        except FeatureStoreLookupError as exc:
            raise InventoryStateLookupError(str(exc)) from exc

        dated_lines = sorted(
            (line for line in snapshot.lines if line.need_by is not None),
            key=lambda line: (
                line.need_by,
                line.requisition_id,
                line.qty_needed,
                line.alt_source_location or "",
            ),
        )
        return tuple(
            ScheduledDemandItem(
                due_date=line.need_by,
                qty=line.qty_needed,
                source_ref=line.requisition_id,
                source_kind=EvidenceKind.REQUISITION,
            )
            for line in dated_lines
        )

    def get_scheduled_demand_status(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> Literal["available", "unavailable"]:
        bundle = self._kv(tenant, pn, location)
        if bundle is None or bundle.requisition_snapshot is None:
            return "unavailable"
        try:
            _checked_requisition(
                bundle.requisition_snapshot,
                tenant_id=self._tenant_id or tenant.tenant_id,
                pn=pn,
                location=location,
            )
        except FeatureStoreLookupError as exc:
            raise InventoryStateLookupError(str(exc)) from exc
        return "available"

    def get_aog_signal(self, *, tenant: TenantContext, pn: str, location: str) -> AogSignal:
        self._kv(tenant, pn, location)
        return AogSignal()

    def get_repair_tat(self, *, tenant: TenantContext, pn: str) -> RepairTat:
        self._any_pn(tenant, pn)
        return RepairTat()
