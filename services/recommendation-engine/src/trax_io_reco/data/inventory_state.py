"""Engine-owned provider for the inputs the Feature Store does not yet model
(spec §2.1 / §10): on-hand stock, current policy, scheduled demand, AOG signal,
repair TAT. v1 stub backed by an in-memory dict; promotes to feature-store #2 later.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from trax_io_feature_store import TenantContext

from trax_io_reco.contracts.context import (
    AogSignal,
    CurrentPolicy,
    RepairTat,
    ScheduledDemandItem,
    StockPosition,
)


class InventoryStateLookupError(LookupError):
    """Raised when a REQUIRED inventory-state input (stock position, current policy)
    is missing for a key."""


@runtime_checkable
class InventoryStateProvider(Protocol):
    def get_stock_position(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> StockPosition: ...

    def get_current_policy(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> CurrentPolicy: ...

    def get_scheduled_demand(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> tuple[ScheduledDemandItem, ...]: ...

    def get_aog_signal(self, *, tenant: TenantContext, pn: str, location: str) -> AogSignal: ...

    def get_repair_tat(self, *, tenant: TenantContext, pn: str) -> RepairTat: ...


_BUCKETS = frozenset(
    {"stock_position", "current_policy", "scheduled_demand", "aog_signal", "repair_tat"}
)


class InMemoryInventoryState:
    """In-memory stub implementing InventoryStateProvider. Seed one row at a time.

    Required buckets (stock_position, current_policy) raise InventoryStateLookupError on
    miss; optional buckets return their empty default (spec §10).
    """

    def __init__(self) -> None:
        self._data: dict[tuple[str, str, tuple[str, ...]], object] = {}

    def seed(self, tenant_id: str, bucket: str, key: tuple[str, ...], value: object) -> None:
        if bucket not in _BUCKETS:
            raise ValueError(f"unknown bucket: {bucket}")
        self._data[(tenant_id, bucket, key)] = value

    def _get(self, tenant: TenantContext, bucket: str, key: tuple[str, ...]) -> object | None:
        return self._data.get((tenant.tenant_id, bucket, key))

    def get_stock_position(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> StockPosition:
        v = self._get(tenant, "stock_position", (pn, location))
        if v is None:
            raise InventoryStateLookupError(f"stock_position missing for {pn}/{location}")
        return v  # type: ignore[return-value]

    def get_current_policy(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> CurrentPolicy:
        v = self._get(tenant, "current_policy", (pn, location))
        if v is None:
            raise InventoryStateLookupError(f"current_policy missing for {pn}/{location}")
        return v  # type: ignore[return-value]

    def get_scheduled_demand(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> tuple[ScheduledDemandItem, ...]:
        v = self._get(tenant, "scheduled_demand", (pn, location))
        return tuple(v) if v is not None else ()  # type: ignore[arg-type]

    def get_aog_signal(self, *, tenant: TenantContext, pn: str, location: str) -> AogSignal:
        v = self._get(tenant, "aog_signal", (pn, location))
        return v if v is not None else AogSignal()  # type: ignore[return-value]

    def get_repair_tat(self, *, tenant: TenantContext, pn: str) -> RepairTat:
        v = self._get(tenant, "repair_tat", (pn,))
        return v if v is not None else RepairTat()  # type: ignore[return-value]
