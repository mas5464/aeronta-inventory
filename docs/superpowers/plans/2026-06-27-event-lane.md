# Event Lane Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give #2's DynamoDB online `FeatureBundle` layer its first consumer — an event-triggered hot-parts recompute that resolves a domain event to `(pn,location)` keys and runs them through the existing #4 Supervisor + #11 engine against the online bundle, returning an `OrchestrationResult`.

**Architecture:** New `src/trax_io_spine/event_lane/` package. A `BundleFeatureStore` adapter implements the `FeatureStoreClient` Protocol over a dict of fetched `FeatureBundle`s (and a `BundleInventoryState` over the engine's empty `InventoryStateProvider` defaults), so the whole Supervisor + engine pipeline runs unchanged on the online layer. An `EventLaneHandler` ties it together: resolve keys → `get_bundle` each → wrap in the adapters → `Supervisor.run`. All in-process; no AWS.

**Tech Stack:** Python 3.12, pydantic v2, `uv` + `pytest` + `ruff`. Reuses `trax-io-feature-store` (#2) and `trax-io-reco` (#11) — already path deps of `services/agent-spine`.

Spec: [docs/superpowers/specs/2026-06-27-event-lane-design.md](../specs/2026-06-27-event-lane-design.md).

## Global Constraints

- Work in `services/agent-spine/`; branch off `main`. ruff: `line-length = 100`, `select = ["E","F","I","B","UP","N","SIM"]`; no mypy; Python `>=3.12`; `pythonpath = ["src"]`. All contracts pydantic v2 `ConfigDict(frozen=True, extra="forbid")`. Commit after every green task.
- Canonical tenant binding: `from trax_io_feature_store import TenantContext`. Miss/absent → `from trax_io_feature_store import FeatureStoreLookupError`.
- The bundle's vendor maps are keyed `vendor_economics[vendor]` and `lead_time_distribution[f"{vendor}|{condition}"]`.
- `InventoryStateProvider` (from `trax_io_reco.data.inventory_state`) — its empty defaults are: `get_scheduled_demand → ()`; `get_aog_signal → AogSignal()`; `get_repair_tat → RepairTat()` (`AogSignal`/`RepairTat`/`ScheduledDemandItem` from `trax_io_reco.contracts.context`).
- The 12 `FeatureStoreClient` methods (exact signatures, all keyword-only after `tenant`): `get_demand_history(tenant,pn,location)`, `get_causal_utilization(tenant,ac_type,destination)`, `get_lead_time_distribution(tenant,pn,vendor,condition)`, `get_wash_rate_history(tenant,pn,location)`, `get_vendor_economics(tenant,pn,vendor)`, `get_part_attributes(tenant,pn)`, `get_criticality(tenant,pn)`, `get_interchangeable_graph(tenant,pn)`, `get_location_graph(tenant,location)`, `get_open_orders_snapshot(tenant,pn,location)`, `get_stock_position(tenant,pn,location)`, `get_current_policy(tenant,pn,location)`. The bundle does NOT carry `causal_utilization`/`wash_rate_history` (engine never reads them) → those two always raise.
- `Supervisor` (from `trax_io_spine.supervisor`): `Supervisor(*, feature_store, inventory_state, enforcer=None, writeback=None, config=None, service=None).run(*, tenant, keys, now, reporting_horizon_days=30) -> OrchestrationResult`.
- `WritebackTarget`/`InMemoryWritebackTarget` from `trax_io_spine.writeback.target`; `OrchestrationResult` from `trax_io_spine.contracts`.
- `FeatureBundle` + the group models from `trax_io_feature_store.schemas`; `materialize_bundle` from `trax_io_feature_store.materialize`; `build_stores_from_extract` from `trax_io_reco.data.extract_loader`.

---

## File Structure

```
services/agent-spine/
├── src/trax_io_spine/event_lane/
│   ├── __init__.py
│   ├── events.py          # EventKind, 7 payloads, DomainEvent
│   ├── keys.py            # KeyResolver Protocol + DirectKeyResolver
│   ├── adapters.py        # BundleFeatureStore + BundleInventoryState
│   ├── online.py          # OnlineStore Protocol + InMemoryOnlineStore
│   └── handler.py         # EventLaneHandler
└── tests/event_lane/
    ├── __init__.py
    ├── test_events.py
    ├── test_keys.py
    ├── test_adapters.py
    ├── test_handler.py
    └── test_integration.py
```

---

## Task 1: Domain event contracts

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/event_lane/__init__.py` (empty)
- Create: `services/agent-spine/src/trax_io_spine/event_lane/events.py`
- Test: `services/agent-spine/tests/event_lane/__init__.py` (empty) + `tests/event_lane/test_events.py`

**Interfaces:**
- Produces: `EventKind` (StrEnum, 7 members); payloads `FlightCompletedPayload`, `StockMovedPayload`, `WoScheduledPayload`, `VendorPriceChangedPayload`, `PlanPublishedPayload`, `RemovalRecordedPayload`, `EoPublishedPayload`; `DomainEvent{tenant_id, kind: EventKind, occurred_at: datetime, schema_version: str = "1.0.0", payload, event_id: str | None = None}`.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/event_lane/__init__.py` (empty) and `services/agent-spine/tests/event_lane/test_events.py`:
```python
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from trax_io_spine.event_lane.events import (
    DomainEvent,
    EventKind,
    RemovalRecordedPayload,
    StockMovedPayload,
)


def test_event_kind_has_the_seven_design_kinds() -> None:
    assert {k.value for k in EventKind} == {
        "flight_completed", "stock_moved", "wo_scheduled", "vendor_price_changed",
        "plan_published", "removal_recorded", "eo_published",
    }


def test_stock_moved_event_round_trips_json() -> None:
    ev = DomainEvent(
        tenant_id="acme", kind=EventKind.STOCK_MOVED, occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload=StockMovedPayload(pn="PN-A", from_location="LOC-1", to_location="LOC-2", qty=3),
    )
    assert DomainEvent.model_validate_json(ev.model_dump_json()) == ev
    assert ev.schema_version == "1.0.0"


def test_payload_is_frozen_and_forbids_extra() -> None:
    with pytest.raises(ValidationError):
        RemovalRecordedPayload(pn="PN-A", tail="C-FABC", location="LOC-1", bogus=1)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/event_lane/test_events.py -q`
Expected: FAIL with `ModuleNotFoundError: No module named 'trax_io_spine.event_lane'`.

- [ ] **Step 3: Implement `events.py`**

Create `services/agent-spine/src/trax_io_spine/event_lane/__init__.py` (empty), then `services/agent-spine/src/trax_io_spine/event_lane/events.py`:
```python
"""Domain event contracts — the seven eMRO Outbound Event Publisher events (design §4.1).

Promoted from the original 2026-04-14 agent-spine plan. The schema is contract-first and
semver-governed (``schema_version``); the event lane consumes these in-process.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict


class EventKind(StrEnum):
    FLIGHT_COMPLETED = "flight_completed"
    STOCK_MOVED = "stock_moved"
    WO_SCHEDULED = "wo_scheduled"
    VENDOR_PRICE_CHANGED = "vendor_price_changed"
    PLAN_PUBLISHED = "plan_published"
    REMOVAL_RECORDED = "removal_recorded"
    EO_PUBLISHED = "eo_published"


class _Payload(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class FlightCompletedPayload(_Payload):
    tail: str
    ac_type: str
    destination: str
    flight_hours: float = 0.0
    cycles: int = 0


class StockMovedPayload(_Payload):
    pn: str
    from_location: str
    to_location: str
    qty: int


class WoScheduledPayload(_Payload):
    wo: str
    location: str
    scheduled_start: datetime
    tail: str | None = None


class VendorPriceChangedPayload(_Payload):
    pn: str
    vendor: str
    old_price: float
    new_price: float
    lead_days: int


class PlanPublishedPayload(_Payload):
    plan_id: str
    fleet: str
    horizon_days: int


class RemovalRecordedPayload(_Payload):
    pn: str
    tail: str
    location: str
    removal_reason: str = ""


class EoPublishedPayload(_Payload):
    eo_number: str
    ata_chapter: str
    affected_fleet: str
    criticality: Literal["AD", "SB", "FLEET_CAMPAIGN", "OTHER"] = "OTHER"


Payload = (
    FlightCompletedPayload
    | StockMovedPayload
    | WoScheduledPayload
    | VendorPriceChangedPayload
    | PlanPublishedPayload
    | RemovalRecordedPayload
    | EoPublishedPayload
)


class DomainEvent(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    tenant_id: str
    kind: EventKind
    occurred_at: datetime
    payload: Payload
    schema_version: str = "1.0.0"
    event_id: str | None = None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/event_lane/test_events.py -q`
Expected: 3 passed. (Each payload has distinct field names, so pydantic's smart union resolves the JSON round-trip unambiguously.)

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/event_lane/ services/agent-spine/tests/event_lane/
git commit -m "#4 event-lane: domain event contracts (7 design §4.1 events + envelope)"
```

---

## Task 2: Key resolver

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/event_lane/keys.py`
- Test: `services/agent-spine/tests/event_lane/test_keys.py`

**Interfaces:**
- Consumes: `DomainEvent`, `EventKind`, `StockMovedPayload`, `RemovalRecordedPayload` (Task 1).
- Produces: `KeyResolver` Protocol with `resolve(self, event: DomainEvent) -> set[tuple[str, str]]`; `DirectKeyResolver` implementing it.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/event_lane/test_keys.py`:
```python
from datetime import UTC, datetime

from trax_io_spine.event_lane.events import (
    DomainEvent,
    EoPublishedPayload,
    EventKind,
    RemovalRecordedPayload,
    StockMovedPayload,
)
from trax_io_spine.event_lane.keys import DirectKeyResolver

_NOW = datetime(2026, 4, 1, tzinfo=UTC)


def _ev(kind: EventKind, payload: object) -> DomainEvent:
    return DomainEvent(tenant_id="acme", kind=kind, occurred_at=_NOW, payload=payload)


def test_stock_moved_resolves_both_endpoints() -> None:
    ev = _ev(EventKind.STOCK_MOVED,
             StockMovedPayload(pn="PN-A", from_location="LOC-1", to_location="LOC-2", qty=3))
    assert DirectKeyResolver().resolve(ev) == {("PN-A", "LOC-1"), ("PN-A", "LOC-2")}


def test_removal_recorded_resolves_one_key() -> None:
    ev = _ev(EventKind.REMOVAL_RECORDED,
             RemovalRecordedPayload(pn="PN-A", tail="C-FABC", location="LOC-1"))
    assert DirectKeyResolver().resolve(ev) == {("PN-A", "LOC-1")}


def test_fan_out_event_resolves_empty_in_v1() -> None:
    ev = _ev(EventKind.EO_PUBLISHED,
             EoPublishedPayload(eo_number="EO-1", ata_chapter="32", affected_fleet="A320"))
    assert DirectKeyResolver().resolve(ev) == set()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/event_lane/test_keys.py -q`
Expected: FAIL with `ModuleNotFoundError: trax_io_spine.event_lane.keys`.

- [ ] **Step 3: Implement `keys.py`**

Create `services/agent-spine/src/trax_io_spine/event_lane/keys.py`:
```python
"""Resolve a domain event to the (pn, location) keys to recompute.

v1 handles the events that name a (pn, location) directly. Fan-out events (eo_published by ATA,
vendor_price_changed pn-wide, flight_completed by AC-type, plan_published by fleet, wo_scheduled
which carries no pn) need catalog enumeration / a BOM lookup and resolve to an empty set here —
a production KeyResolver plugs into the same Protocol.
"""

from __future__ import annotations

from typing import Protocol

from trax_io_spine.event_lane.events import (
    DomainEvent,
    EventKind,
    RemovalRecordedPayload,
    StockMovedPayload,
)


class KeyResolver(Protocol):
    def resolve(self, event: DomainEvent) -> set[tuple[str, str]]: ...


class DirectKeyResolver:
    """Resolves only the events whose payload names an explicit (pn, location)."""

    def resolve(self, event: DomainEvent) -> set[tuple[str, str]]:
        if event.kind is EventKind.STOCK_MOVED and isinstance(event.payload, StockMovedPayload):
            p = event.payload
            return {(p.pn, p.from_location), (p.pn, p.to_location)}
        if event.kind is EventKind.REMOVAL_RECORDED and isinstance(
            event.payload, RemovalRecordedPayload
        ):
            p = event.payload
            return {(p.pn, p.location)}
        return set()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/event_lane/test_keys.py -q`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/event_lane/keys.py services/agent-spine/tests/event_lane/test_keys.py
git commit -m "#4 event-lane: DirectKeyResolver (stock_moved/removal_recorded; fan-out deferred)"
```

---

## Task 3: Bundle adapters

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/event_lane/adapters.py`
- Test: `services/agent-spine/tests/event_lane/test_adapters.py`

**Interfaces:**
- Consumes: `FeatureBundle` + group models + `FeatureStoreLookupError`/`TenantContext` (`trax_io_feature_store`); `InventoryStateProvider` defaults (`AogSignal`, `RepairTat` from `trax_io_reco.contracts.context`).
- Produces:
  - `BundleFeatureStore(tenant_id: str, bundles: dict[tuple[str, str], FeatureBundle])` implementing all 12 `FeatureStoreClient` methods.
  - `BundleInventoryState()` implementing `InventoryStateProvider` (empty defaults).

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/event_lane/test_adapters.py`:
```python
from datetime import date

import pytest
from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.schemas import (
    Criticality,
    CurrentPolicy,
    FeatureBundle,
    PartAttributes,
    StockPosition,
    VendorEconomics,
)

from trax_io_spine.event_lane.adapters import BundleFeatureStore, BundleInventoryState

ACME = TenantContext(tenant_id="acme")
_D = date(2026, 4, 1)


def _bundle() -> FeatureBundle:
    return FeatureBundle(
        tenant_id="acme", pn="PN-A", location="LOC-1",
        stock_position=StockPosition(tenant_id="acme", pn="PN-A", location="LOC-1",
                                     on_hand=10, serviceable=10, extract_date=_D),
        current_policy=CurrentPolicy(tenant_id="acme", pn="PN-A", location="LOC-1",
                                     rop=5, eoq=4, safety_stock=2, max_stock=12, extract_date=_D),
        part_attributes=PartAttributes(tenant_id="acme", pn="PN-A", extract_date=_D),
        criticality=Criticality(tenant_id="acme", pn="PN-A", raw_essentiality_code="4",
                                canonical_tier=4, extract_date=_D),
        vendor_economics={"DEFAULT": VendorEconomics(tenant_id="acme", pn="PN-A", vendor="DEFAULT",
                                                     unit_cost=100, extract_date=_D)},
    )


def _store() -> BundleFeatureStore:
    return BundleFeatureStore("acme", {("PN-A", "LOC-1"): _bundle()})


def test_serves_key_level_and_part_level_reads() -> None:
    s = _store()
    assert s.get_stock_position(tenant=ACME, pn="PN-A", location="LOC-1").serviceable == 10
    assert s.get_current_policy(tenant=ACME, pn="PN-A", location="LOC-1").rop == 5
    assert s.get_part_attributes(tenant=ACME, pn="PN-A").pn == "PN-A"
    assert s.get_criticality(tenant=ACME, pn="PN-A").canonical_tier == 4
    assert s.get_vendor_economics(tenant=ACME, pn="PN-A", vendor="DEFAULT").unit_cost == 100


def test_missing_field_raises_lookup() -> None:
    # bundle has no demand_history -> miss
    with pytest.raises(FeatureStoreLookupError):
        _store().get_demand_history(tenant=ACME, pn="PN-A", location="LOC-1")


def test_unmodeled_groups_always_miss() -> None:
    with pytest.raises(FeatureStoreLookupError):
        _store().get_causal_utilization(tenant=ACME, ac_type="A320", destination="YYZ")
    with pytest.raises(FeatureStoreLookupError):
        _store().get_wash_rate_history(tenant=ACME, pn="PN-A", location="LOC-1")


def test_cross_tenant_raises() -> None:
    other = TenantContext(tenant_id="other")
    with pytest.raises(FeatureStoreLookupError):
        _store().get_stock_position(tenant=other, pn="PN-A", location="LOC-1")


def test_inventory_state_defaults_are_empty() -> None:
    inv = BundleInventoryState()
    assert inv.get_scheduled_demand(tenant=ACME, pn="PN-A", location="LOC-1") == ()
    assert inv.get_aog_signal(tenant=ACME, pn="PN-A", location="LOC-1") is not None
    assert inv.get_repair_tat(tenant=ACME, pn="PN-A") is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/event_lane/test_adapters.py -q`
Expected: FAIL with `ModuleNotFoundError: trax_io_spine.event_lane.adapters`.

- [ ] **Step 3: Implement `adapters.py`**

Create `services/agent-spine/src/trax_io_spine/event_lane/adapters.py`:
```python
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
    def get_stock_position(self, *, tenant: TenantContext, pn: str, location: str) -> StockPosition:
        self._check(tenant)
        return _present(self._kv(pn, location).stock_position, "stock_position")  # type: ignore[return-value]

    def get_current_policy(self, *, tenant: TenantContext, pn: str, location: str) -> CurrentPolicy:
        self._check(tenant)
        return _present(self._kv(pn, location).current_policy, "current_policy")  # type: ignore[return-value]

    def get_demand_history(self, *, tenant: TenantContext, pn: str, location: str) -> DemandHistory:
        self._check(tenant)
        return _present(self._kv(pn, location).demand_history, "demand_history")  # type: ignore[return-value]

    def get_open_orders_snapshot(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> OpenOrdersSnapshot:
        self._check(tenant)
        return _present(self._kv(pn, location).open_orders_snapshot, "open_orders_snapshot")  # type: ignore[return-value]

    # -- part-level ---------------------------------------------------------
    def get_part_attributes(self, *, tenant: TenantContext, pn: str) -> PartAttributes:
        self._check(tenant)
        return _present(self._any_pn(pn).part_attributes, "part_attributes")  # type: ignore[return-value]

    def get_criticality(self, *, tenant: TenantContext, pn: str) -> Criticality:
        self._check(tenant)
        return _present(self._any_pn(pn).criticality, "criticality")  # type: ignore[return-value]

    def get_interchangeable_graph(self, *, tenant: TenantContext, pn: str) -> InterchangeableGraph:
        self._check(tenant)
        return _present(self._any_pn(pn).interchangeable_graph, "interchangeable_graph")  # type: ignore[return-value]

    # -- location-level -----------------------------------------------------
    def get_location_graph(self, *, tenant: TenantContext, location: str) -> LocationGraph:
        self._check(tenant)
        return _present(self._any_location(location).location_graph, "location_graph")  # type: ignore[return-value]

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
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/event_lane/test_adapters.py -q`
Expected: 5 passed. (If `_present`'s `# type: ignore` comments push a line past 100 chars, ruff `E501` will flag — split the line; the implementer fixes lint before committing.)

- [ ] **Step 5: Lint + commit**

Run: `cd services/agent-spine && uv run --extra dev ruff check .` (fix any findings).
```bash
git add services/agent-spine/src/trax_io_spine/event_lane/adapters.py services/agent-spine/tests/event_lane/test_adapters.py
git commit -m "#4 event-lane: BundleFeatureStore + BundleInventoryState adapters"
```

---

## Task 4: Online store + handler

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/event_lane/online.py`
- Create: `services/agent-spine/src/trax_io_spine/event_lane/handler.py`
- Test: `services/agent-spine/tests/event_lane/test_handler.py`

**Interfaces:**
- Consumes: `DomainEvent`/`EventKind`/payloads (T1), `DirectKeyResolver`/`KeyResolver` (T2), `BundleFeatureStore`/`BundleInventoryState` (T3); `FeatureBundle`/`FeatureStoreLookupError`/`TenantContext`; `Supervisor`, `InMemoryWritebackTarget`, `OrchestrationResult`, `WritebackTarget`.
- Produces:
  - `OnlineStore` Protocol: `get_bundle(self, *, tenant: TenantContext, pn: str, location: str) -> FeatureBundle`.
  - `InMemoryOnlineStore(bundles: list[FeatureBundle])` implementing it.
  - `EventLaneHandler(online_store: OnlineStore, writeback: WritebackTarget, *, resolver: KeyResolver | None = None, enforcer=None, config=None)` with `handle(self, event: DomainEvent) -> OrchestrationResult`.

- [ ] **Step 1: Write the failing test**

Create `services/agent-spine/tests/event_lane/test_handler.py`:
```python
from datetime import UTC, date, datetime

import pytest
from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.schemas import FeatureBundle, StockPosition

from trax_io_spine.event_lane.events import DomainEvent, EventKind, StockMovedPayload
from trax_io_spine.event_lane.online import InMemoryOnlineStore

ACME = TenantContext(tenant_id="acme")
_D = date(2026, 4, 1)


def _bundle(loc: str) -> FeatureBundle:
    return FeatureBundle(
        tenant_id="acme", pn="PN-A", location=loc,
        stock_position=StockPosition(tenant_id="acme", pn="PN-A", location=loc,
                                     on_hand=5, serviceable=5, extract_date=_D),
    )


def test_in_memory_online_store_get_and_miss() -> None:
    store = InMemoryOnlineStore([_bundle("LOC-1")])
    assert store.get_bundle(tenant=ACME, pn="PN-A", location="LOC-1").location == "LOC-1"
    with pytest.raises(FeatureStoreLookupError):
        store.get_bundle(tenant=ACME, pn="PN-A", location="NOPE")
    with pytest.raises(FeatureStoreLookupError):
        store.get_bundle(tenant=TenantContext(tenant_id="other"), pn="PN-A", location="LOC-1")


def test_handler_empty_keys_returns_empty_result() -> None:
    from trax_io_spine.event_lane.handler import EventLaneHandler
    from trax_io_spine.writeback.target import InMemoryWritebackTarget

    handler = EventLaneHandler(
        online_store=InMemoryOnlineStore([_bundle("LOC-1")]),
        writeback=InMemoryWritebackTarget(),
    )
    # eo_published is a fan-out event -> DirectKeyResolver returns empty -> no recompute
    from trax_io_spine.event_lane.events import EoPublishedPayload

    ev = DomainEvent(
        tenant_id="acme", kind=EventKind.EO_PUBLISHED, occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload=EoPublishedPayload(eo_number="EO-1", ata_chapter="32", affected_fleet="A320"),
    )
    res = handler.handle(ev)
    assert res.summary["recommendations"] == 0
    assert res.written == () and res.queued == ()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/event_lane/test_handler.py -q`
Expected: FAIL with `ModuleNotFoundError: trax_io_spine.event_lane.online`.

- [ ] **Step 3: Implement `online.py`**

Create `services/agent-spine/src/trax_io_spine/event_lane/online.py`:
```python
"""OnlineStore Protocol + an in-memory implementation for the event lane.

`DynamoDbOnlineStore` (#2) satisfies this Protocol structurally; `InMemoryOnlineStore` backs
fast, AWS-free tests.
"""

from __future__ import annotations

from typing import Protocol

from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.schemas import FeatureBundle


class OnlineStore(Protocol):
    def get_bundle(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> FeatureBundle: ...


class InMemoryOnlineStore:
    def __init__(self, bundles: list[FeatureBundle]) -> None:
        self._by_key = {(b.tenant_id, b.pn, b.location): b for b in bundles}

    def get_bundle(self, *, tenant: TenantContext, pn: str, location: str) -> FeatureBundle:
        b = self._by_key.get((tenant.tenant_id, pn, location))
        if b is None:
            raise FeatureStoreLookupError(
                f"no online bundle for tenant={tenant.tenant_id} pn={pn} location={location}"
            )
        return b
```

- [ ] **Step 4: Implement `handler.py`**

Create `services/agent-spine/src/trax_io_spine/event_lane/handler.py`:
```python
"""Event lane handler: a domain event -> hot-parts recompute against the online bundle."""

from __future__ import annotations

from typing import Any

from trax_io_feature_store import FeatureStoreLookupError, TenantContext
from trax_io_feature_store.schemas import FeatureBundle

from trax_io_spine.contracts import OrchestrationResult
from trax_io_spine.event_lane.adapters import BundleFeatureStore, BundleInventoryState
from trax_io_spine.event_lane.events import DomainEvent
from trax_io_spine.event_lane.keys import DirectKeyResolver, KeyResolver
from trax_io_spine.event_lane.online import OnlineStore
from trax_io_spine.supervisor import Supervisor
from trax_io_spine.writeback.target import WritebackTarget

_EMPTY_SUMMARY = {
    "recommendations": 0, "written": 0, "deferred": 0, "failed": 0,
    "queued": 0, "rejected": 0, "skipped": 0,
}


class EventLaneHandler:
    def __init__(
        self,
        online_store: OnlineStore,
        writeback: WritebackTarget,
        *,
        resolver: KeyResolver | None = None,
        enforcer: Any = None,
        config: Any = None,
    ) -> None:
        self._online = online_store
        self._writeback = writeback
        self._resolver: KeyResolver = resolver or DirectKeyResolver()
        self._enforcer = enforcer
        self._config = config

    def handle(self, event: DomainEvent) -> OrchestrationResult:
        tenant = TenantContext(tenant_id=event.tenant_id)
        keys = self._resolver.resolve(event)
        if not keys:
            return OrchestrationResult(
                tenant_id=event.tenant_id, generated_at=event.occurred_at,
                summary=dict(_EMPTY_SUMMARY),
            )

        bundles: dict[tuple[str, str], FeatureBundle] = {}
        for pn, location in keys:
            try:
                bundles[(pn, location)] = self._online.get_bundle(
                    tenant=tenant, pn=pn, location=location
                )
            except FeatureStoreLookupError:
                continue  # no online row for this key yet

        if not bundles:
            return OrchestrationResult(
                tenant_id=event.tenant_id, generated_at=event.occurred_at,
                summary=dict(_EMPTY_SUMMARY),
            )

        supervisor = Supervisor(
            feature_store=BundleFeatureStore(event.tenant_id, bundles),
            inventory_state=BundleInventoryState(),
            writeback=self._writeback,
            enforcer=self._enforcer,
            config=self._config,
        )
        return supervisor.run(tenant=tenant, keys=list(bundles), now=event.occurred_at)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/event_lane/test_handler.py -q`
Expected: 2 passed.

- [ ] **Step 6: Lint + commit**

Run: `cd services/agent-spine && uv run --extra dev ruff check .` (fix any findings).
```bash
git add services/agent-spine/src/trax_io_spine/event_lane/online.py \
  services/agent-spine/src/trax_io_spine/event_lane/handler.py \
  services/agent-spine/tests/event_lane/test_handler.py
git commit -m "#4 event-lane: OnlineStore Protocol + InMemoryOnlineStore + EventLaneHandler"
```

---

## Task 5: End-to-end integration

**Files:**
- Test: `services/agent-spine/tests/event_lane/test_integration.py`

**Interfaces:**
- Consumes: everything. Drives a real `RecommendationService` (via the Supervisor the handler builds) over bundles materialized from #11's committed extract sample.

- [ ] **Step 1: Write the integration test**

Create `services/agent-spine/tests/event_lane/test_integration.py`:
```python
"""Event lane end-to-end over #11's extract sample, materialized into online bundles."""

from datetime import UTC, datetime
from pathlib import Path

from trax_io_feature_store import TenantContext
from trax_io_feature_store.materialize import materialize_bundle
from trax_io_reco.data.extract_loader import build_stores_from_extract

from trax_io_spine.event_lane.events import DomainEvent, EventKind, RemovalRecordedPayload
from trax_io_spine.event_lane.handler import EventLaneHandler
from trax_io_spine.event_lane.online import InMemoryOnlineStore
from trax_io_spine.writeback.target import InMemoryWritebackTarget

_SAMPLE = (
    Path(__file__).resolve().parents[2] / "recommendation-engine" / "examples" / "extract_sample"
)


def _online_store_from_sample(tenant_id: str):
    fs, _inv, tid, keys = build_stores_from_extract(str(_SAMPLE), tenant_id=tenant_id)
    tenant = TenantContext(tenant_id=tid)
    bundles = [
        materialize_bundle(fs, tenant=tenant, pn=pn, location=loc) for pn, loc in keys
    ]
    return InMemoryOnlineStore(bundles), keys


def test_removal_event_recomputes_the_affected_key() -> None:
    store, keys = _online_store_from_sample("acme")
    pn, loc = keys[0]
    handler = EventLaneHandler(online_store=store, writeback=InMemoryWritebackTarget())
    ev = DomainEvent(
        tenant_id="acme", kind=EventKind.REMOVAL_RECORDED,
        occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload=RemovalRecordedPayload(pn=pn, tail="C-FABC", location=loc),
    )
    res = handler.handle(ev)
    total = res.summary["recommendations"]
    routed = (
        res.summary["written"] + res.summary["deferred"] + res.summary["failed"]
        + res.summary["queued"] + res.summary["rejected"]
    )
    assert routed == total  # every recommendation lands in exactly one bucket
    assert res.summary["skipped"] == 0  # the key's bundle has the required inputs


def test_cross_tenant_event_writes_nothing() -> None:
    store, keys = _online_store_from_sample("acme")
    pn, loc = keys[0]
    writeback = InMemoryWritebackTarget()
    handler = EventLaneHandler(online_store=store, writeback=writeback)
    ev = DomainEvent(
        tenant_id="other-airline", kind=EventKind.REMOVAL_RECORDED,
        occurred_at=datetime(2026, 4, 1, tzinfo=UTC),
        payload=RemovalRecordedPayload(pn=pn, tail="C-FABC", location=loc),
    )
    res = handler.handle(ev)  # no bundle for tenant other-airline -> empty
    assert res.summary["recommendations"] == 0
    assert writeback.history == []
```

- [ ] **Step 2: Run the integration test**

Run: `cd services/agent-spine && uv run --extra dev pytest tests/event_lane/test_integration.py -q`
Expected: 2 passed.

- [ ] **Step 3: Full suite + lint**

Run: `cd services/agent-spine && uv run --extra dev --extra emro --extra cedar pytest -q && uv run --extra dev ruff check .`
Expected: all green (the prior 49 + the new event-lane tests), ruff clean.

- [ ] **Step 4: Commit**

```bash
git add services/agent-spine/tests/event_lane/test_integration.py
git commit -m "#4 event-lane: end-to-end recompute over the extract sample + tenant isolation"
```

---

## Post-implementation

- [ ] Update `ROADMAP.md` (#4: event lane done — the online layer now has a consumer; note deferred fan-out resolution + incremental bundle update + AWS transport) and `TASKS.md`; add a short event-lane note to `services/agent-spine/README.md`.
- [ ] Adversarial review of `BundleFeatureStore`'s read-mapping + tenant chokepoint + the handler's key-drop path before declaring done.

---

## Self-Review notes (author)

- **Spec coverage:** events.py §3.1 → T1; KeyResolver/DirectKeyResolver §3.2 → T2; BundleFeatureStore/BundleInventoryState §3.3 → T3; OnlineStore + InMemoryOnlineStore §3.4 → T4; EventLaneHandler §3.5 → T4; testing §5 (events, keys, adapters incl. tenant + unmodeled-miss, end-to-end + tenant isolation) → tests in T1–T5; deferred items §2 (fan-out resolver returns `set()`, no incremental update, no AWS) honored.
- **Placeholder scan:** none — every code/test block is complete.
- **Type consistency:** `BundleFeatureStore(tenant_id, bundles)`, `BundleInventoryState()`, `OnlineStore.get_bundle(*, tenant, pn, location)`, `InMemoryOnlineStore(bundles: list)`, `EventLaneHandler(online_store, writeback, *, resolver, enforcer, config).handle(event) -> OrchestrationResult`, `DirectKeyResolver.resolve(event) -> set[tuple[str,str]]` — used identically across tasks; the 12 `FeatureStoreClient` signatures and the bundle map keys (`vendor`, `"{vendor}|{condition}"`) match #2 verbatim.
