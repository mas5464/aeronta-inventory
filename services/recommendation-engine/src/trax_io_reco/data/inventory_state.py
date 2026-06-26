"""Engine-owned provider for the inputs the Feature Store does not yet model
(spec §10): scheduled demand, AOG signal, repair TAT. v1 stub backed by an in-memory dict.

NOTE: stock_position and current_policy were promoted into Feature Store #2 in Phase 2 and
are now read via the FeatureStoreClient (see data/feature_reader.py). AOG and repair-TAT have
no extract source yet; scheduled demand is sparse in v1 — these remain provider-served stubs.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from trax_io_feature_store import TenantContext

from trax_io_reco.contracts.context import AogSignal, RepairTat, ScheduledDemandItem


class InventoryStateLookupError(LookupError):
    """Raised when a required inventory-state input is missing for a key."""


@runtime_checkable
class InventoryStateProvider(Protocol):
    def get_scheduled_demand(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> tuple[ScheduledDemandItem, ...]: ...

    def get_aog_signal(self, *, tenant: TenantContext, pn: str, location: str) -> AogSignal: ...

    def get_repair_tat(self, *, tenant: TenantContext, pn: str) -> RepairTat: ...


_BUCKETS = frozenset({"scheduled_demand", "aog_signal", "repair_tat"})


class InMemoryInventoryState:
    """In-memory stub implementing InventoryStateProvider. Seed one row at a time.
    All groups are optional and return their empty default on miss (spec §10)."""

    def __init__(self) -> None:
        self._data: dict[tuple[str, str, tuple[str, ...]], object] = {}

    def seed(self, tenant_id: str, bucket: str, key: tuple[str, ...], value: object) -> None:
        if bucket not in _BUCKETS:
            raise ValueError(f"unknown bucket: {bucket}")
        self._data[(tenant_id, bucket, key)] = value

    def _get(self, tenant: TenantContext, bucket: str, key: tuple[str, ...]) -> object | None:
        return self._data.get((tenant.tenant_id, bucket, key))

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
