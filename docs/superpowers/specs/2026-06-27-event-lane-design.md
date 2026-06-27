# Trax IO — Event Lane (hot-parts recompute) for the Agent Spine (#4) — Design

**Date:** 2026-06-27
**Sub-project:** #4 Agent Spine (event-lane slice) + #2 Feature Store (online consumer)
**Status:** Design — approved in brainstorm, pending spec review → writing-plans
**Builds on:** [Agent Spine v1](2026-06-27-agent-spine-v1-design.md) · [ADR-0005](../../adr/2026-06-27-0005-deterministic-agent-spine-core.md). Gives #2's DynamoDB online `FeatureBundle` layer (built, currently consumer-less) its first consumer.

---

## 1. Context & goal

Design §4.1 specifies an event path: the eMRO Outbound Event Publisher emits seven domain events → EventBridge → Step Functions for **hot-parts and event-triggered recompute**. #2 ships the online `FeatureBundle` layer (one DynamoDB item per `(tenant_id, pn, location)`) for sub-10ms reads, but nothing consumes it yet — the only decision path today is #4's nightly/offline Supervisor over the `FeatureStoreClient`.

**Goal:** a deterministic, locally-verifiable **event lane** that, given a domain event, resolves the affected `(pn,location)` keys and recomputes them through the **existing #4 Supervisor + #11 engine against the online `FeatureBundle`** (not the offline batch) → enforce → route → writeback → `OrchestrationResult`. No AWS: the event transport, Step Functions, and AgentCore Runtime invocation are out of scope; this is the in-process recompute core they will call.

### Grounded facts (verified against the code)
- `FeatureBundle` (one per `(tenant,pn,location)`) carries: `stock_position`, `current_policy`, `demand_history` (windowed), `open_orders_snapshot`, `location_graph`, `part_attributes`, `criticality`, `interchangeable_graph`, and the vendor-keyed maps `vendor_economics: dict[vendor, …]` + `lead_time_distribution: dict["{vendor}|{condition}", …]`. It does **not** carry `causal_utilization`/`wash_rate_history` (the v1 engine never reads them) nor the `InventoryStateProvider` inputs (`scheduled_demand`/`aog_signal`/`repair_tat`).
- `DynamoDbOnlineStore.get_bundle(*, tenant, pn, location) -> FeatureBundle` (raises `FeatureStoreLookupError` on miss).
- The #4 `Supervisor(feature_store, inventory_state, …).run(tenant, keys, now)` builds `RecommendationService(feature_store, inventory_state)` internally — so injecting a **bundle-backed** `FeatureStoreClient` + `InventoryStateProvider` reuses the whole pipeline unchanged.

---

## 2. Scope

### In scope (this slice — new area `services/agent-spine/src/trax_io_spine/event_lane/`)
1. **`events.py`** — the seven design-§4.1 domain events + a typed `DomainEvent` envelope.
2. **`keys.py`** — `KeyResolver` Protocol + `DirectKeyResolver` (the events that name a `(pn,location)`).
3. **`adapters.py`** — `BundleFeatureStore(FeatureStoreClient)` + `BundleInventoryState(InventoryStateProvider)`: serve the engine's reads from fetched `FeatureBundle`(s).
4. **`OnlineStore` Protocol** (`get_bundle`) — `DynamoDbOnlineStore` satisfies it structurally; an `InMemoryOnlineStore` backs fast tests.
5. **`handler.py`** — `EventLaneHandler.handle(event) -> OrchestrationResult`.
6. Tests + adversarial review.

### Out of scope (designed-for / deferred)
- **Fan-out key resolution** for events that need catalog enumeration or a lookup: `eo_published` (ATA → parts), `vendor_price_changed` (pn → all locations), `flight_completed` (AC-type → parts), `plan_published` (fleet → parts), and `wo_scheduled` (carries a location but **no pn** → needs the WO's bill of materials). The `KeyResolver` Protocol is the seam; a production resolver (GSI/index/lake query) plugs in later. In v1 these resolve to an empty key set (a logged no-op).
- **Incremental online-bundle update** from the event (the "online updated by the event lane" half of §4.2). This slice **recomputes against the current bundle**; bundle freshness stays the nightly Glue's job until a separate population slice.
- The AWS transport (EventBridge/Kinesis/Step Functions/Lambda) and AgentCore Runtime invocation — this is the in-process core they wrap.

---

## 3. Components

### 3.1 `events.py` — domain event contracts
Pydantic v2 `frozen=True, extra="forbid"`. `EventKind` (StrEnum, the seven kinds). One payload model per kind (`StockMovedPayload{pn, from_location, to_location, qty}`, `RemovalRecordedPayload{pn, tail, location, removal_reason}`, plus `FlightCompleted`/`WoScheduled`/`VendorPriceChanged`/`PlanPublished`/`EoPublished` per §4.1). `DomainEvent{tenant_id, kind, occurred_at: datetime, schema_version: str = "1.0.0", payload, event_id: str | None}`. (Mirrors the original 2026-04-14 agent-spine plan's `events.py`; promoted here.)

### 3.2 `keys.py` — `KeyResolver` + `DirectKeyResolver`
- `KeyResolver` Protocol: `resolve(self, event: DomainEvent) -> set[tuple[str, str]]` (the `(pn, location)` keys to recompute).
- `DirectKeyResolver`: `stock_moved` → `{(pn, from_location), (pn, to_location)}`; `removal_recorded` → `{(pn, location)}`; **every other kind → `set()`** (empty; deferred to a fan-out resolver). Pure, no I/O.

### 3.3 `adapters.py` — bundle-backed engine inputs
- **`BundleFeatureStore(tenant_id: str, bundles: dict[tuple[str, str], FeatureBundle])`** implements all 12 `FeatureStoreClient` reads from the fetched bundles:
  - `(pn,location)`-level (`stock_position`, `current_policy`, `demand_history`, `open_orders_snapshot`) → the matching bundle's field, else `FeatureStoreLookupError`.
  - part-level (`part_attributes`, `criticality`, `interchangeable_graph`) → any bundle for that `pn`.
  - location-level (`location_graph`) → any bundle for that `location`.
  - vendor reads (`vendor_economics(pn, vendor)`, `lead_time_distribution(pn, vendor, condition)`) → the bundle's vendor maps (`vendor`, `"{vendor}|{condition}"`), else miss.
  - `causal_utilization` / `wash_rate_history` → always `FeatureStoreLookupError` (the engine never calls them; the Protocol requires the methods).
  - Every method takes the `tenant` kwarg and raises on a tenant mismatch (the chokepoint).
- **`BundleInventoryState()`** implements `InventoryStateProvider` with the same empty defaults the engine's in-memory stub uses: no scheduled demand, no active AOG, default repair-TAT. (The bundle carries none of these; recompute runs on online features only.)

### 3.4 `OnlineStore` Protocol + `InMemoryOnlineStore`
- `OnlineStore` Protocol: `get_bundle(self, *, tenant: TenantContext, pn: str, location: str) -> FeatureBundle`. `DynamoDbOnlineStore` already matches it structurally.
- `InMemoryOnlineStore(bundles: list[FeatureBundle])` — dict-backed; same tenant chokepoint + `FeatureStoreLookupError` on miss. For fast unit/integration tests (moto-free); the moto-backed `DynamoDbOnlineStore` is exercised by #2's own suite.

### 3.5 `handler.py` — `EventLaneHandler`
`EventLaneHandler(online_store: OnlineStore, writeback: WritebackTarget, *, resolver: KeyResolver | None = None, enforcer=None, config=None)`. `handle(self, event: DomainEvent) -> OrchestrationResult`:
1. `tenant = TenantContext(event.tenant_id)`; `keys = self._resolver.resolve(event)`.
2. If `keys` is empty → return an empty `OrchestrationResult` (logged no-op).
3. For each key, `online_store.get_bundle(...)`; a `FeatureStoreLookupError` drops that key (no online row yet).
4. Build `BundleFeatureStore(tenant_id, bundles)` + `BundleInventoryState()`; construct a `Supervisor(feature_store=…, inventory_state=…, writeback=self._writeback, enforcer=self._enforcer, config=self._config)`.
5. `return supervisor.run(tenant=tenant, keys=list(bundles), now=event.occurred_at)`.

The `writeback` target (and optional Cedar enforcer) are injected once into the handler; the per-event `Supervisor` reuses them. The whole #4 enforce/route/writeback chain runs unchanged.

---

## 4. Data flow

```
DomainEvent(stock_moved, pn=P, from=A, to=B)
  → DirectKeyResolver → {(P,A), (P,B)}
  → OnlineStore.get_bundle ×2  (miss → drop key)
  → BundleFeatureStore + BundleInventoryState
  → Supervisor.run(tenant, [(P,A),(P,B)], now=event.occurred_at)
        └ RecommendationService over the bundles → batch
        └ GuardrailEnforcer (hard §6.2 + autonomy policy) → route
        └ WritebackTarget.write(approved)   [InMemory in tests / fake_emro / real #6]
  → OrchestrationResult { written, queued, rejected, deferred, skipped, summary }
```

---

## 5. Testing

- **`events.py`**: payload validation + envelope round-trip; `EventKind` matches the seven §4.1 kinds.
- **`keys.py`**: `stock_moved` → two keys; `removal_recorded` → one; each deferred kind → empty set.
- **`adapters.py`**: `BundleFeatureStore` serves a populated bundle to a real `RecommendationService` (the engine reads it end-to-end); a missing field → `FeatureStoreLookupError`; `causal`/`wash` always miss; a tenant mismatch raises; `BundleInventoryState` returns the empty defaults.
- **End-to-end** (`InMemoryOnlineStore` seeded from #11's extract-sample bundles + `InMemoryWritebackTarget`): a `stock_moved` event recomputes exactly the two keys and routes them (written/queued/rejected buckets sum to the recomputed count); an event whose keys have no online bundle → empty result; **tenant isolation** (an event for tenant X over Y's data writes nothing).
- Conventions mirror the package: `uv` + `pytest` + `ruff` (line-length 100, select E/F/I/B/UP/N/SIM), pydantic frozen, `pythonpath=["src"]`. The `InMemoryOnlineStore` keeps the core tests dependency-free; reuse the `dynamodb` extra only if a `DynamoDbOnlineStore` integration test is added.
- Adversarial review of the adapter's read-mapping + tenant chokepoint + the resolver after build.

---

## 6. Risks

- **Adapter drift from the real `FeatureStoreClient` semantics.** Mitigation: a test runs the *real* `RecommendationService` against a `BundleFeatureStore`, proving observational parity with the offline path for a populated key.
- **Windowed online demand changes the recommendation vs the offline batch.** Intended (online = recent-window features for hot recompute); documented, not a defect.
- **Empty-key no-op hides a missing fan-out resolver.** Mitigation: the handler logs the dropped/empty-key events; the deferred kinds are explicit in `DirectKeyResolver` (each returns `set()` deliberately, not by omission), so adding a fan-out resolver is a localized change.
- **`(pn,location)` vs part-level reads in `BundleFeatureStore`.** Part/location-level reads pick "any bundle for that pn/location"; if the recompute set spans multiple locations of one pn with divergent part attributes (shouldn't happen — part attrs are pn-level), the first is used. Documented; the test covers the multi-key case.
