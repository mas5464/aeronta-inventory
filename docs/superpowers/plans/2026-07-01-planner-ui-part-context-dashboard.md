# Planner UI — Part Context, Trends & Portfolio Dashboard — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Surface real part/inventory context (description, on-hand, need, demand, trends, stock breakdown, lead time, open orders) and a portfolio dashboard in the Planner UI, served from the feature store the BFF already builds.

**Architecture:** `PlannerStore` retains the in-memory `FeatureStoreClient` + `keys` universe it currently discards. Phase A enriches existing wire models with fields already on `Recommendation`. Phase C adds a `GET /parts/{pn}/{location}` → `PartContext` (read from `fs`) surfaced in a right-side part drawer with a demand-trend chart. Phase B adds a portfolio `GET /dashboard` behind a live `#/dashboard` section. Additive only — no engine change.

**Tech Stack:** BFF = FastAPI + pydantic (`services/agent-spine`, `--extra bff`, `uv`/pytest). UI = React 18 + TS + Vite + Vitest (`apps/planner-ui`), CSS Modules, `lucide-react`, dependency-free inline-SVG charts.

**Spec:** [docs/superpowers/specs/2026-07-01-planner-ui-part-context-dashboard-design.md](../specs/2026-07-01-planner-ui-part-context-dashboard-design.md)

## Global Constraints

- BFF: Python ≥3.12; ruff `line-length 100`, select `["E","F","I","B","UP","N","SIM"]`; wire models frozen + `extra="forbid"` (mirror `services/agent-spine/src/trax_io_spine/bff/models.py`). Run tests: `cd services/agent-spine && uv run --extra bff pytest`; lint: `uv run --extra dev ruff check .`.
- Every `fs` getter is wrapped so a missing/absent feature group degrades to `None` — the endpoint must never 500 on incomplete data. `GET /parts/...` returns 404 only for a key unknown to `fs`.
- Vendor-keyed getters (`get_lead_time_distribution`, `get_vendor_economics`) require a `vendor` (+ `condition` for lead time). Resolve `vendor="DEFAULT"` (the bridge's synthesized canonical vendor) and `condition="NEW"`; on any exception → `None`.
- `StockPosition` fields are `on_hand, serviceable, unserviceable_in_repair, allocated_reserved, rental, loan`. `LeadTimeDistribution` fields are `promised_lead_days, realized_mean_days, n_observations` (no percentiles).
- UI: no new runtime deps beyond `lucide-react` (already installed); charts are inline SVG like `apps/planner-ui/src/components/ChartRow.tsx`; CSS Modules + existing tokens; all numbers rounded for display; keep the 78 Vitest tests green.
- TDD throughout. Commit messages prefixed `#7`; end the body with `Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>`.

---

## File Structure

**BFF (`services/agent-spine/src/trax_io_spine/bff/`)**
- Modify `models.py` — enrich `QueueRow`/`RecommendationDetail`; add `StockBreakdown`, `LeadTimeView`, `OpenOrderView`, `DemandPoint`, `DemandSummary`, `PartAttributesView`, `PartContext`, `DashboardSummary` (+ `Breakdown`, `PartShortfall`).
- Modify `store.py` — retain `fs`/`keys`/`tenant`; enrich `_row`/`detail`; add `part_context(pn, location)` and `dashboard()`.
- Modify `app.py` — add `GET /parts/{pn}/{location}` and `GET /dashboard`.
- Tests: `tests/bff/test_part_context.py`, `tests/bff/test_dashboard.py`, and extend `tests/bff/test_store.py`/`test_app.py` (mirror existing bff test layout — find it under `services/agent-spine/tests/`).

**UI (`apps/planner-ui/src/`)**
- Modify `api/types.ts` — enrich `QueueRow`/`RecommendationDetail`; add TS mirrors of the new models.
- Modify `api/client.ts` — `getPartContext`, `getDashboard` on the interface + `HttpPlannerClient` + `FakePlannerClient`; extend `api/sample.ts` with seeded part contexts + dashboard.
- Modify `components/QueueTable.tsx` — On hand + Need columns.
- Modify `components/DetailPanel.tsx` — part header + on-hand/need/demand strip.
- Create `components/DemandTrend.tsx` (+ `.module.css`) — 24-month inline-SVG chart from `DemandPoint[]`.
- Create `components/PartDrawer.tsx` (+ `.module.css`) — right drawer hosting `DetailPanel` context + `DemandTrend`.
- Create `components/DashboardView.tsx` (+ `.module.css`) — KPIs, breakdowns, top-shortages.
- Modify `hooks/usePlanner.ts` — lazy `partContext` load on select with a stale-key guard.
- Modify `App.tsx` — `#/dashboard` route + `NavRail` wiring.
- Modify `components/NavRail.tsx` — Review + Dashboard live.

---

## Phase A — enrich existing wire models

### Task A1: Expose part fields on `QueueRow` + `RecommendationDetail` (BFF)

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/models.py`
- Modify: `services/agent-spine/src/trax_io_spine/bff/store.py`
- Test: `services/agent-spine/tests/bff/test_store.py` (add cases; create the file if the bff tests live elsewhere — mirror the existing bff test module)

**Interfaces:**
- Produces: `QueueRow` + `RecommendationDetail` gain `description: str`, `current_stock: int`, `shortage_quantity: float`, `recommended_location: str | None`, `horizon_days: int`.

- [ ] **Step 1: Write the failing test**

```python
# in the bff store test module
from datetime import UTC, datetime
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = "../recommendation-engine/examples/extract_sample"  # adjust to an abs/rel path that resolves from the test

def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=_SAMPLE, now=datetime(2026, 4, 1, tzinfo=UTC)
    )

def test_queue_row_carries_part_fields():
    rows = _store().queue()
    assert rows, "sample extract should produce recommendations"
    r = rows[0]
    assert isinstance(r.description, str) and r.description
    assert isinstance(r.current_stock, int)
    assert r.shortage_quantity >= 0.0
    assert r.horizon_days > 0

def test_detail_carries_part_fields():
    store = _store()
    rec_id = store.queue()[0].recommendation_id
    d = store.detail(rec_id)
    assert d.description and isinstance(d.current_stock, int)
    assert d.horizon_days > 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra bff pytest -k part_fields -q`
Expected: FAIL — `QueueRow` has no field `description` (pydantic) / attribute error.

- [ ] **Step 3: Add the fields to the models**

```python
# models.py — add to BOTH QueueRow and RecommendationDetail (after existing fields)
    description: str
    current_stock: int
    shortage_quantity: float
    recommended_location: str | None
    horizon_days: int
```

- [ ] **Step 4: Populate in the store**

```python
# store.py — in _row(...), add to the QueueRow(...) constructor:
    description=rec.description,
    current_stock=rec.current_stock,
    shortage_quantity=rec.shortage_quantity,
    recommended_location=rec.recommended_location,
    horizon_days=rec.horizon_days,
# store.py — in detail(...), add the same five kwargs to the RecommendationDetail(...) constructor.
```

- [ ] **Step 5: Run tests + lint**

Run: `cd services/agent-spine && uv run --extra bff pytest -k part_fields -q && uv run --extra dev ruff check .`
Expected: PASS, clean.

- [ ] **Step 6: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/bff/models.py services/agent-spine/src/trax_io_spine/bff/store.py services/agent-spine/tests
git commit -m "#7 bff: expose description/on-hand/shortage/recommended-location/horizon on queue+detail

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase C — retain feature store + PartContext + part drawer + demand trend

### Task C1: Retain `fs`/`keys` + assemble `PartContext` (BFF)

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/models.py`
- Modify: `services/agent-spine/src/trax_io_spine/bff/store.py`
- Test: `services/agent-spine/tests/bff/test_part_context.py`

**Interfaces:**
- Consumes: the retained `fs` (`FeatureStoreClient`), `tenant` (`TenantContext`), `keys` (`list[tuple[str,str]]`).
- Produces:
  - Models: `StockBreakdown(on_hand, serviceable, in_repair, allocated, rental, loan: int)`; `LeadTimeView(promised_days, realized_mean_days: float | None, n_observations: int)`; `OpenOrderView(order_id, order_type, vendor: str|None, qty_open: int, expected_rcv_date: str|None)`; `DemandPoint(period_start: str, removals: int, issues: int, total: int)`; `DemandSummary(total_24mo: int, points: tuple[DemandPoint,...])`; `PartAttributesView(description, ata_chapter: str|None, part_class: str|None, shelf_life_days: int|None, hazardous_material: bool, tool_control_item: bool, criticality_tier: int|None)`; `PartContext(pn, location, attributes: PartAttributesView, stock: StockBreakdown|None, current_policy: _PolicyView|None, proposed_policy: _PolicyView|None, lead_time: LeadTimeView|None, open_orders: tuple[OpenOrderView,...], total_open_qty: int, demand: DemandSummary|None, unit_cost: float|None)`.
  - `PlannerStore.part_context(pn: str, location: str) -> PartContext` (raises `RecommendationNotFound`-style `KeyError`/custom for a key not in `keys`).

- [ ] **Step 1: Write the failing test**

```python
# tests/bff/test_part_context.py
from datetime import UTC, datetime
import pytest
from trax_io_spine.bff.store import PlannerStore, RecommendationNotFound

_SAMPLE = "../recommendation-engine/examples/extract_sample"

def _store():
    return PlannerStore.from_extract(
        tenant_id="acme", extract_dir=_SAMPLE, now=datetime(2026, 4, 1, tzinfo=UTC)
    )

def test_part_context_assembles_from_feature_store():
    store = _store()
    pn, loc = store.keys[0]
    ctx = store.part_context(pn, loc)
    assert ctx.pn == pn and ctx.location == loc
    assert ctx.attributes.description  # from PartAttributes
    assert ctx.stock is None or ctx.stock.on_hand >= 0
    # the sample has a 120-row rotable demand series → some points
    assert ctx.demand is None or len(ctx.demand.points) >= 1
    assert ctx.total_open_qty >= 0

def test_part_context_unknown_key_raises():
    with pytest.raises(RecommendationNotFound):
        _store().part_context("NOPE", "NOWHERE")

def test_part_context_degrades_without_500(monkeypatch):
    store = _store()
    pn, loc = store.keys[0]
    # a getter that blows up must degrade to None, not propagate
    monkeypatch.setattr(store.fs, "get_stock_position", lambda **k: (_ for _ in ()).throw(RuntimeError))
    ctx = store.part_context(pn, loc)
    assert ctx.stock is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra bff pytest tests/bff/test_part_context.py -q`
Expected: FAIL — `PlannerStore` has no attribute `keys`/`fs`/`part_context`.

- [ ] **Step 3: Retain fs/keys/tenant + add the models**

```python
# store.py — from_extract: keep fs/keys/tenant.
    @classmethod
    def from_extract(cls, *, tenant_id, extract_dir, now, writeback=None):
        fs, inv, tid, keys = build_stores_from_extract(extract_dir, tenant_id=tenant_id)
        tenant = TenantContext(tenant_id=tid)
        batch = RecommendationService(feature_store=fs, inventory_state=inv).run(
            tenant=tenant, keys=keys, now=now
        )
        store = cls(tenant_id=tid, writeback=writeback or InMemoryWritebackTarget())
        store.fs = fs
        store.tenant = tenant
        store.keys = list(keys)
        enforcer = GuardrailEnforcer()
        for rec in batch.recommendations:
            store._ingest(rec, enforcer.enforce(rec))
        return store
# add to the @dataclass fields (with defaults so existing constructors still work):
    fs: object | None = None
    tenant: object | None = None
    keys: list = field(default_factory=list)
```

```python
# models.py — new models (mirror the frozen _Base style; reuse _PolicyView)
class StockBreakdown(_Base):
    on_hand: int
    serviceable: int
    in_repair: int
    allocated: int
    rental: int
    loan: int

class LeadTimeView(_Base):
    promised_days: float | None
    realized_mean_days: float | None
    n_observations: int

class OpenOrderView(_Base):
    order_id: str
    order_type: str
    vendor: str | None
    qty_open: int
    expected_rcv_date: str | None

class DemandPoint(_Base):
    period_start: str
    removals: int
    issues: int
    total: int

class DemandSummary(_Base):
    total_24mo: int
    points: tuple[DemandPoint, ...]

class PartAttributesView(_Base):
    description: str
    ata_chapter: str | None
    part_class: str | None
    shelf_life_days: int | None
    hazardous_material: bool
    tool_control_item: bool
    criticality_tier: int | None

class PartContext(_Base):
    pn: str
    location: str
    attributes: PartAttributesView
    stock: StockBreakdown | None
    current_policy: _PolicyView | None
    proposed_policy: _PolicyView | None
    lead_time: LeadTimeView | None
    open_orders: tuple[OpenOrderView, ...]
    total_open_qty: int
    demand: DemandSummary | None
    unit_cost: float | None
```

- [ ] **Step 4: Implement `part_context` (each getter wrapped)**

```python
# store.py — add. `_safe(fn)` returns None on any exception.
def _safe(fn):
    try:
        return fn()
    except Exception:  # noqa: BLE001 - feature groups may be absent; degrade to None
        return None

# in PlannerStore:
    def part_context(self, pn: str, location: str):
        if (pn, location) not in self.keys:
            raise RecommendationNotFound(f"{pn}/{location}")
        t = self.tenant
        attrs = _safe(lambda: self.fs.get_part_attributes(tenant=t, pn=pn))
        crit = _safe(lambda: self.fs.get_criticality(tenant=t, pn=pn))
        sp = _safe(lambda: self.fs.get_stock_position(tenant=t, pn=pn, location=location))
        cp = _safe(lambda: self.fs.get_current_policy(tenant=t, pn=pn, location=location))
        lt = _safe(lambda: self.fs.get_lead_time_distribution(tenant=t, pn=pn, vendor="DEFAULT", condition="NEW"))
        oo = _safe(lambda: self.fs.get_open_orders_snapshot(tenant=t, pn=pn, location=location))
        dh = _safe(lambda: self.fs.get_demand_history(tenant=t, pn=pn, location=location))
        ve = _safe(lambda: self.fs.get_vendor_economics(tenant=t, pn=pn, vendor="DEFAULT"))
        entry = next((e for e in self._entries.values()
                      if e.rec.part_number == pn and e.rec.current_location == location), None)
        return PartContext(
            pn=pn, location=location,
            attributes=PartAttributesView(
                description=(attrs.description if attrs and attrs.description else pn),
                ata_chapter=attrs.ata_chapter if attrs else None,
                part_class=attrs.part_class if attrs else None,
                shelf_life_days=attrs.shelf_life_days if attrs else None,
                hazardous_material=bool(attrs and attrs.hazardous_material),
                tool_control_item=bool(attrs and attrs.tool_control_item),
                criticality_tier=crit.canonical_tier if crit else None,
            ),
            stock=StockBreakdown(
                on_hand=sp.on_hand, serviceable=sp.serviceable, in_repair=sp.unserviceable_in_repair,
                allocated=sp.allocated_reserved, rental=sp.rental, loan=sp.loan,
            ) if sp else None,
            current_policy=_policy_view(cp) if cp else None,
            proposed_policy=_policy_view(entry.rec.policy) if entry and entry.rec.policy else None,
            lead_time=LeadTimeView(
                promised_days=lt.promised_lead_days, realized_mean_days=lt.realized_mean_days,
                n_observations=lt.n_observations,
            ) if lt else None,
            open_orders=tuple(
                OpenOrderView(order_id=o.order_id, order_type=o.order_type, vendor=o.vendor,
                              qty_open=o.qty_open,
                              expected_rcv_date=o.expected_rcv_date.isoformat() if o.expected_rcv_date else None)
                for o in (oo.orders if oo else [])
            ),
            total_open_qty=oo.total_open_qty if oo else 0,
            demand=DemandSummary(
                total_24mo=sum(o.removals + o.issues for o in dh.observations),
                points=tuple(
                    DemandPoint(period_start=o.period_start.isoformat(), removals=o.removals,
                                issues=o.issues, total=o.removals + o.issues)
                    for o in sorted(dh.observations, key=lambda o: o.period_start)
                ),
            ) if dh else None,
            unit_cost=float(ve.unit_cost) if ve else None,
        )
```
Note: `_policy_view` already exists in `store.py`; `_PolicyView` maps `rop/eoq/safety_stock/max_stock`. Import the new models at the top of `store.py`.

- [ ] **Step 5: Run tests + lint**

Run: `cd services/agent-spine && uv run --extra bff pytest tests/bff/test_part_context.py -q && uv run --extra dev ruff check .`
Expected: PASS, clean.

- [ ] **Step 6: Commit**

```bash
git add services/agent-spine
git commit -m "#7 bff: retain feature store + assemble PartContext (stock/lead-time/open-orders/demand)

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task C2: `GET /parts/{pn}/{location}` endpoint (BFF)

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/app.py`
- Test: `services/agent-spine/tests/bff/test_app.py` (extend)

**Interfaces:**
- Consumes: `PlannerStore.part_context` (Task C1), `PartContext` model.
- Produces: `GET /v1/tenants/{tenant_id}/parts/{pn}/{location}` → `PartContext` (404 on unknown key / unknown tenant).

- [ ] **Step 1: Write the failing test** (use FastAPI `TestClient` as the existing app tests do)

```python
def test_get_part_context(client_and_store):  # reuse the app-test fixture that builds create_planner_app
    client, store = client_and_store
    pn, loc = store.keys[0]
    r = client.get(f"/v1/tenants/acme/parts/{pn}/{loc}")
    assert r.status_code == 200
    body = r.json()
    assert body["pn"] == pn and body["attributes"]["description"]

def test_get_part_context_unknown_404(client_and_store):
    client, _ = client_and_store
    assert client.get("/v1/tenants/acme/parts/NOPE/NOWHERE").status_code == 404
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra bff pytest tests/bff/test_app.py -k part_context -q`
Expected: FAIL — 404 route not found (no such endpoint).

- [ ] **Step 3: Add the route**

```python
# app.py — add inside create_planner_app, alongside the other routes; import PartContext + RecommendationNotFound
    @app.get(base + "/parts/{pn}/{location}")
    def part_context(tenant_id: str, pn: str, location: str) -> PartContext:
        try:
            return _store(tenant_id).part_context(pn, location)
        except RecommendationNotFound as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/agent-spine && uv run --extra bff pytest tests/bff/test_app.py -k part_context -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine
git commit -m "#7 bff: GET /parts/{pn}/{location} endpoint

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task C3: `getPartContext` client + TS mirrors + fake (UI)

**Files:**
- Modify: `apps/planner-ui/src/api/types.ts`, `apps/planner-ui/src/api/client.ts`, `apps/planner-ui/src/api/sample.ts`
- Test: `apps/planner-ui/src/api/client.test.ts` (extend)

**Interfaces:**
- Produces: TS interfaces `StockBreakdown`, `LeadTimeView`, `OpenOrderView`, `DemandPoint`, `DemandSummary`, `PartAttributesView`, `PartContext` (field-for-field mirrors of Task C1). `PlannerClient.getPartContext(tenant, pn, location): Promise<PartContext>`; `SAMPLE_PART_CONTEXT` for the fake.

- [ ] **Step 1: Write the failing test**

```typescript
// client.test.ts (add)
it("HttpPlannerClient.getPartContext hits the parts URL", async () => {
  const fetchMock = vi.fn(async (_url: string) => new Response(JSON.stringify({ pn: "P", location: "L", attributes: { description: "d" }, open_orders: [], total_open_qty: 0 }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  const ctx = await new HttpPlannerClient("http://bff").getPartContext("acme", "HYD-PUMP-001", "YYZ");
  expect(ctx.pn).toBe("P");
  expect(String(fetchMock.mock.calls[0][0])).toContain("/parts/HYD-PUMP-001/YYZ");
});

it("FakePlannerClient.getPartContext returns a seeded context", async () => {
  const c = new FakePlannerClient(SAMPLE_SEED);
  const ctx = await c.getPartContext("acme", "HYD-PUMP-001", "YYZ");
  expect(ctx.attributes.description).toBeTruthy();
  expect(ctx.demand?.points.length ?? 0).toBeGreaterThan(0);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/planner-ui && npm test -- src/api/client.test.ts`
Expected: FAIL — `getPartContext` is not a function / type missing.

- [ ] **Step 3: Add the TS mirrors** (`types.ts`)

```typescript
export interface StockBreakdown { on_hand: number; serviceable: number; in_repair: number; allocated: number; rental: number; loan: number; }
export interface LeadTimeView { promised_days: number | null; realized_mean_days: number | null; n_observations: number; }
export interface OpenOrderView { order_id: string; order_type: string; vendor: string | null; qty_open: number; expected_rcv_date: string | null; }
export interface DemandPoint { period_start: string; removals: number; issues: number; total: number; }
export interface DemandSummary { total_24mo: number; points: DemandPoint[]; }
export interface PartAttributesView { description: string; ata_chapter: string | null; part_class: string | null; shelf_life_days: number | null; hazardous_material: boolean; tool_control_item: boolean; criticality_tier: number | null; }
export interface PartContext { pn: string; location: string; attributes: PartAttributesView; stock: StockBreakdown | null; current_policy: PolicyView | null; proposed_policy: PolicyView | null; lead_time: LeadTimeView | null; open_orders: OpenOrderView[]; total_open_qty: number; demand: DemandSummary | null; unit_cost: number | null; }
```
Also add `description: string; current_stock: number; shortage_quantity: number; recommended_location: string | null; horizon_days: number;` to `QueueRow` and `RecommendationDetail` (Phase A UI mirror).

- [ ] **Step 4: Implement the clients** (`client.ts`)

```typescript
// interface:
  getPartContext(tenant: string, pn: string, location: string): Promise<PartContext>;
// HttpPlannerClient:
  async getPartContext(tenant: string, pn: string, location: string): Promise<PartContext> {
    return this.json(await fetch(`${this.base(tenant)}/parts/${encodeURIComponent(pn)}/${encodeURIComponent(location)}`));
  }
// FakePlannerClient: return a seeded map keyed by `${pn}/${location}`, default from SAMPLE_PART_CONTEXT
  async getPartContext(_t: string, pn: string, location: string): Promise<PartContext> {
    return SAMPLE_PART_CONTEXT(pn, location);
  }
```
Add `SAMPLE_PART_CONTEXT(pn, location)` to `sample.ts` returning a realistic context (description, stock breakdown, a ~12-point demand series so `DemandTrend` has data, lead_time, one open order). Keep the existing `QueueRow`/detail seeds valid by adding the five new Phase-A fields (`description`, `current_stock`, `shortage_quantity`, `recommended_location`, `horizon_days`) to every seed row/detail.

- [ ] **Step 5: Run tests**

Run: `cd apps/planner-ui && npm test -- src/api/client.test.ts`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add apps/planner-ui/src/api
git commit -m "#7 planner-ui: getPartContext client + PartContext TS mirrors + fake seed

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task C4: `DemandTrend` chart (UI)

**Files:**
- Create: `apps/planner-ui/src/components/DemandTrend.tsx`, `apps/planner-ui/src/components/DemandTrend.module.css`
- Test: `apps/planner-ui/src/components/DemandTrend.test.tsx`

**Interfaces:**
- Consumes: `DemandPoint[]` (Task C3).
- Produces: `DemandTrend({ points }: { points: DemandPoint[] })` — inline-SVG bar/area chart of `total` per period, `role="img"` + `aria-label`, empty state when no points.

- [ ] **Step 1: Write the failing test**

```typescript
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { DemandTrend } from "./DemandTrend";

describe("DemandTrend", () => {
  it("renders a labelled chart with a bar per point", () => {
    const points = [
      { period_start: "2026-01-01", removals: 2, issues: 0, total: 2 },
      { period_start: "2026-02-01", removals: 0, issues: 1, total: 1 },
    ];
    render(<DemandTrend points={points} />);
    expect(screen.getByRole("img", { name: /demand/i })).toBeInTheDocument();
  });
  it("shows an empty state with no points", () => {
    render(<DemandTrend points={[]} />);
    expect(screen.getByText(/no demand history/i)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/planner-ui && npm test -- src/components/DemandTrend.test.tsx`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement** (model on `ChartRow.tsx`'s inline-SVG approach)

```tsx
import type { DemandPoint } from "../api/types";
import styles from "./DemandTrend.module.css";

export function DemandTrend({ points }: { points: DemandPoint[] }) {
  if (points.length === 0) return <p className={styles.empty}>No demand history for this part.</p>;
  const max = Math.max(1, ...points.map((p) => p.total));
  const W = 320, H = 90, bw = W / points.length;
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Demand history trend" className={styles.chart}>
      {points.map((p, i) => {
        const h = (p.total / max) * (H - 16);
        return <rect key={p.period_start} x={i * bw + 1} y={H - h} width={Math.max(1, bw - 2)} height={h} className={styles.bar} />;
      })}
    </svg>
  );
}
```
CSS: `.bar { fill: var(--text-accent); }`, `.empty { color: var(--text-muted); font-size: 12px; }`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/planner-ui && npm test -- src/components/DemandTrend.test.tsx`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/planner-ui/src/components/DemandTrend.*
git commit -m "#7 planner-ui: DemandTrend inline-SVG chart

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task C5: Part drawer + enriched detail/queue + lazy load (UI)

**Files:**
- Modify: `apps/planner-ui/src/components/QueueTable.tsx` (On hand + Need columns), `apps/planner-ui/src/components/DetailPanel.tsx` (part header + on-hand/need/demand strip + render `DemandTrend` + stock/lead-time/open-orders when a `partContext` prop is present), `apps/planner-ui/src/hooks/usePlanner.ts` (lazy `partContext`), `apps/planner-ui/src/App.tsx` (pass `partContext`; optionally wrap the detail in a right-drawer container)
- Test: extend `QueueTable.test.tsx`, `DetailPanel.test.tsx`, `usePlanner.test.ts`, `App.test.tsx`

**Interfaces:**
- Consumes: `getPartContext` (C3), `DemandTrend` (C4).
- Produces: `usePlanner` exposes `partContext: PartContext | null`; `select` also fetches it (guarded by the same `selectSeq` token as `getDetail`). `DetailPanel` gains `partContext?: PartContext | null`.

- [ ] **Step 1: Write the failing tests**

```typescript
// QueueTable.test.tsx — new columns
it("shows on-hand and need columns", () => {
  render(<QueueTable rows={ROWS} selectedId={null} onSelect={vi.fn()} onApprove={vi.fn()} />);
  expect(screen.getByRole("columnheader", { name: /on hand/i })).toBeInTheDocument();
  expect(screen.getByRole("columnheader", { name: /need/i })).toBeInTheDocument();
});

// usePlanner.test.ts — lazy part context on select (extend the tabs harness / baseClient stub with getPartContext)
it("loads the part context when a row is selected", async () => {
  const client = baseClient({ getPartContext: vi.fn(async () => ({ pn: "P", location: "L", attributes: { description: "d" }, open_orders: [], total_open_qty: 0, stock: null, current_policy: null, proposed_policy: null, lead_time: null, demand: null, unit_cost: null })) });
  const { result } = await ready(client);
  act(() => result.current.select("rec-a"));
  await waitFor(() => expect(result.current.partContext?.pn).toBe("P"));
});

// App.test.tsx — selecting a row shows the demand trend (freshClient's fake getPartContext returns a series)
it("selecting a row shows the part's demand trend", async () => {
  render(<App client={freshClient()} tenant="acme" />);
  const parts = await screen.findAllByText("HYD-PUMP-001");
  await userEvent.click(parts[0]);
  expect(await screen.findByRole("img", { name: /demand/i })).toBeInTheDocument();
});
```
Add `getPartContext` to the `usePlanner.test.ts` `baseClient` stub so the guards tests still compile.

- [ ] **Step 2: Run to verify failures**

Run: `cd apps/planner-ui && npm test`
Expected: FAIL on the new assertions (columns/partContext/trend absent) and the stub type gap.

- [ ] **Step 3: Implement**

- `QueueTable.tsx`: add two `<td>`s — On hand (`r.current_stock`) and Need (`r.shortage_quantity`, rounded) — with matching `<th>`s (non-sortable is fine, or add to `COLUMNS` if sorting them; keep it simple with plain headers) placed after the Cost column.
- `usePlanner.ts`: add `const [partContext, setPartContext] = useState<PartContext | null>(null);`. In `select`, after (or alongside) `getDetail`, `setPartContext(null)` then `client.getPartContext(tenant, row.pn, row.location)` — resolve `pn/location` from the selected `QueueRow` (look it up in `rows`), guarded by the same `seq === selectSeq.current` check; clear it in `runWrite`/`setTab` like `history`. Return `partContext`.
- `DetailPanel.tsx`: accept `partContext?: PartContext | null`. Render a part header (`partContext.attributes.description` · part_class · ATA) and an "on hand N · serviceable N · in repair N · need N · demand N/Hd" strip; render `<DemandTrend points={partContext.demand?.points ?? []} />`; a compact lead-time + open-orders block. Guard everything on `partContext` being present.
- `App.tsx`: pass `partContext={p.partContext}` to `DetailPanel`. (Right-drawer container is optional polish — a `className` that fixes the detail to a right column on wide screens; keep the stacked fallback.)

- [ ] **Step 4: Run tests + build**

Run: `cd apps/planner-ui && npm test && npm run build`
Expected: PASS; tsc + vite clean.

- [ ] **Step 5: Commit**

```bash
git add apps/planner-ui/src
git commit -m "#7 planner-ui: part drawer — on-hand/need columns, part header, lazy PartContext + demand trend

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Phase B — portfolio dashboard

### Task B1: `dashboard()` aggregation + models (BFF)

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/models.py`, `services/agent-spine/src/trax_io_spine/bff/store.py`
- Test: `services/agent-spine/tests/bff/test_dashboard.py`

**Interfaces:**
- Produces: `Breakdown(key: str, count: int, on_hand: int, shortage: float)`; `PartShortfall(pn, location: str, shortage: float, on_hand: int, projected_demand: float)`; `DashboardSummary(parts, total_on_hand: int, total_on_hand_value: float, total_shortage: float, total_projected_demand: float, aog_exposure: int, open_recommendations: int, net_cost_impact: float, by_criticality/by_ata/by_part_class/by_tier: tuple[Breakdown,...], top_shortages: tuple[PartShortfall,...])`; `PlannerStore.dashboard() -> DashboardSummary`.

- [ ] **Step 1: Write the failing test**

```python
# tests/bff/test_dashboard.py
from datetime import UTC, datetime
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = "../recommendation-engine/examples/extract_sample"

def _store():
    return PlannerStore.from_extract(tenant_id="acme", extract_dir=_SAMPLE, now=datetime(2026,4,1,tzinfo=UTC))

def test_dashboard_aggregates_portfolio():
    d = _store().dashboard()
    assert d.parts == len(_store().keys)  # portfolio-wide (all keys), not just recommendations
    assert d.total_on_hand >= 0
    assert d.open_recommendations >= 0
    assert isinstance(d.by_criticality, tuple)
    # top_shortages sorted desc by shortage
    shorts = [s.shortage for s in d.top_shortages]
    assert shorts == sorted(shorts, reverse=True)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/agent-spine && uv run --extra bff pytest tests/bff/test_dashboard.py -q`
Expected: FAIL — no `dashboard` method.

- [ ] **Step 3: Add models + implement** (iterate `keys`, read `fs`, overlay recommendation entries)

```python
# store.py — PlannerStore.dashboard()
    def dashboard(self):
        t = self.tenant
        rows = []  # per-key facts
        for pn, loc in self.keys:
            sp = _safe(lambda pn=pn, loc=loc: self.fs.get_stock_position(tenant=t, pn=pn, location=loc))
            attrs = _safe(lambda pn=pn: self.fs.get_part_attributes(tenant=t, pn=pn))
            crit = _safe(lambda pn=pn: self.fs.get_criticality(tenant=t, pn=pn))
            ve = _safe(lambda pn=pn: self.fs.get_vendor_economics(tenant=t, pn=pn, vendor="DEFAULT"))
            e = next((x for x in self._entries.values()
                      if x.rec.part_number == pn and x.rec.current_location == loc), None)
            rec = e.rec if e else None
            rows.append(dict(
                pn=pn, loc=loc,
                on_hand=sp.on_hand if sp else 0,
                unit_cost=float(ve.unit_cost) if ve else 0.0,
                shortage=rec.shortage_quantity if rec else 0.0,
                demand=rec.projected_demand if rec else 0.0,
                aog=rec.aog_risk_level if rec else 0,
                cost=float(rec.estimated_cost_impact) if rec else 0.0,
                crit=crit.canonical_tier if crit else None,
                ata=attrs.ata_chapter if attrs else None,
                pclass=attrs.part_class if attrs else None,
                tier=e.outcome.tier if e else None,
                has_rec=rec is not None,
            ))

        def breakdown(field):
            groups: dict = {}
            for r in rows:
                k = r[field]
                if k is None:
                    continue
                g = groups.setdefault(str(k), dict(count=0, on_hand=0, shortage=0.0))
                g["count"] += 1; g["on_hand"] += r["on_hand"]; g["shortage"] += r["shortage"]
            return tuple(Breakdown(key=k, count=g["count"], on_hand=g["on_hand"], shortage=g["shortage"])
                        for k, g in sorted(groups.items()))

        top = sorted((r for r in rows if r["shortage"] > 0), key=lambda r: r["shortage"], reverse=True)[:10]
        return DashboardSummary(
            parts=len(rows),
            total_on_hand=sum(r["on_hand"] for r in rows),
            total_on_hand_value=sum(r["on_hand"] * r["unit_cost"] for r in rows),
            total_shortage=sum(r["shortage"] for r in rows),
            total_projected_demand=sum(r["demand"] for r in rows),
            aog_exposure=sum(1 for r in rows if r["aog"] >= 3),
            open_recommendations=sum(1 for r in rows if r["has_rec"]),
            net_cost_impact=sum(r["cost"] for r in rows),
            by_criticality=breakdown("crit"),
            by_ata=breakdown("ata"),
            by_part_class=breakdown("pclass"),
            by_tier=breakdown("tier"),
            top_shortages=tuple(PartShortfall(pn=r["pn"], location=r["loc"], shortage=r["shortage"],
                                              on_hand=r["on_hand"], projected_demand=r["demand"]) for r in top),
        )
```
Add the `Breakdown`, `PartShortfall`, `DashboardSummary` models to `models.py` (frozen `_Base`) and import them in `store.py`.

- [ ] **Step 4: Run tests + lint**

Run: `cd services/agent-spine && uv run --extra bff pytest tests/bff/test_dashboard.py -q && uv run --extra dev ruff check .`
Expected: PASS, clean.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine
git commit -m "#7 bff: portfolio dashboard aggregation over the keys universe

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task B2: `GET /dashboard` endpoint (BFF)

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/app.py`
- Test: `services/agent-spine/tests/bff/test_app.py` (extend)

- [ ] **Step 1: Write the failing test**

```python
def test_get_dashboard(client_and_store):
    client, store = client_and_store
    r = client.get("/v1/tenants/acme/dashboard")
    assert r.status_code == 200
    assert r.json()["parts"] == len(store.keys)
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd services/agent-spine && uv run --extra bff pytest tests/bff/test_app.py -k dashboard -q`
Expected: FAIL — no route.

- [ ] **Step 3: Add the route**

```python
# app.py — import DashboardSummary
    @app.get(base + "/dashboard")
    def dashboard(tenant_id: str) -> DashboardSummary:
        return _store(tenant_id).dashboard()
```

- [ ] **Step 4: Run + commit**

Run: `cd services/agent-spine && uv run --extra bff pytest tests/bff/test_app.py -k dashboard -q`
Expected: PASS.
```bash
git add services/agent-spine
git commit -m "#7 bff: GET /dashboard endpoint

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task B3: `getDashboard` client + mirrors + fake (UI)

**Files:**
- Modify: `apps/planner-ui/src/api/types.ts`, `apps/planner-ui/src/api/client.ts`, `apps/planner-ui/src/api/sample.ts`
- Test: `apps/planner-ui/src/api/client.test.ts` (extend)

**Interfaces:**
- Produces: TS `Breakdown`, `PartShortfall`, `DashboardSummary` mirrors; `PlannerClient.getDashboard(tenant): Promise<DashboardSummary>`; `SAMPLE_DASHBOARD` for the fake.

- [ ] **Step 1: Write the failing test**

```typescript
it("FakePlannerClient.getDashboard returns portfolio totals", async () => {
  const d = await new FakePlannerClient(SAMPLE_SEED).getDashboard("acme");
  expect(d.parts).toBeGreaterThan(0);
  expect(Array.isArray(d.top_shortages)).toBe(true);
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/planner-ui && npm test -- src/api/client.test.ts`
Expected: FAIL — no `getDashboard`.

- [ ] **Step 3: Implement** — add the TS mirrors (`Breakdown { key; count; on_hand; shortage }`, `PartShortfall { pn; location; shortage; on_hand; projected_demand }`, `DashboardSummary { parts; total_on_hand; total_on_hand_value; total_shortage; total_projected_demand; aog_exposure; open_recommendations; net_cost_impact; by_criticality; by_ata; by_part_class; by_tier; top_shortages }`), the interface method, the `HttpPlannerClient.getDashboard` (`GET .../dashboard`), and `FakePlannerClient.getDashboard` returning `SAMPLE_DASHBOARD` (computed from `SAMPLE_SEED` rows: parts/on-hand/shortage/etc.).

- [ ] **Step 4: Run + commit**

Run: `cd apps/planner-ui && npm test -- src/api/client.test.ts`
Expected: PASS.
```bash
git add apps/planner-ui/src/api
git commit -m "#7 planner-ui: getDashboard client + DashboardSummary mirrors + fake

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

### Task B4: Dashboard section + route + nav (UI)

**Files:**
- Create: `apps/planner-ui/src/components/DashboardView.tsx`, `apps/planner-ui/src/components/DashboardView.module.css`
- Modify: `apps/planner-ui/src/App.tsx` (add `#/dashboard` route + a `DashboardView` render path), `apps/planner-ui/src/components/NavRail.tsx` (Dashboard live → navigates)
- Test: `apps/planner-ui/src/components/DashboardView.test.tsx`, extend `App.test.tsx`

**Interfaces:**
- Consumes: `getDashboard` (B3), `SummaryCards`-style tiles, `ChartRow`-style bars.
- Produces: `DashboardView({ client, tenant })` fetching `/dashboard` and rendering KPI tiles + breakdown bars + a top-shortages table.

- [ ] **Step 1: Write the failing tests**

```typescript
// DashboardView.test.tsx
it("renders portfolio KPIs and top shortages", async () => {
  render(<DashboardView client={new FakePlannerClient(SAMPLE_SEED)} tenant="acme" />);
  expect(await screen.findByText(/parts/i)).toBeInTheDocument();
  expect(screen.getByText(/on hand/i)).toBeInTheDocument();
});

// App.test.tsx — the Dashboard route renders the dashboard, not the queue
it("navigates to the Dashboard section", async () => {
  window.location.hash = "#/dashboard";
  render(<App client={freshClient()} tenant="acme" />);
  expect(await screen.findByText(/portfolio/i)).toBeInTheDocument();
  window.location.hash = "";
});
```

- [ ] **Step 2: Run to verify failures**

Run: `cd apps/planner-ui && npm test -- src/components/DashboardView.test.tsx src/App.test.tsx`
Expected: FAIL — module/route missing.

- [ ] **Step 3: Implement**

- `DashboardView.tsx`: on mount, `client.getDashboard(tenant)` into state (loading/error). Render a heading ("Portfolio"), KPI tiles (reuse the `SummaryCards` tile styling: parts, on-hand value, total need, AOG exposure, net cost), breakdown bar lists (reuse the `ChartRow` bar styling for by-criticality / by-part-class), and a top-shortages `<table>` (pn·location, on hand, need, projected demand). Round all numbers.
- `App.tsx`: add a route `#/dashboard` → `<DashboardView client={client} tenant={tenant} />` inside the same `HashRouter`. Since `PlannerView` currently owns `/:tab`, add `<Route path="/dashboard" element={<DashboardView .../>} />` BEFORE the `/:tab` route (so "dashboard" isn't captured as a tab), and keep `/:tab` for pending/decided. The shell (`NavRail` + `main`) can wrap both, or `DashboardView` renders inside its own shell — simplest: give `DashboardView` the same `NavRail` + `main` wrapper.
- `NavRail.tsx`: make Dashboard `live` and clickable → navigate to `#/dashboard`; mark Review vs Dashboard active from the current route. (NavRail gains an `active: "review" | "dashboard"` prop + `onNavigate`, or uses `useNavigate`/`useLocation`.)

- [ ] **Step 4: Run tests + build**

Run: `cd apps/planner-ui && npm test && npm run build`
Expected: PASS; tsc + vite clean.

- [ ] **Step 5: Commit**

```bash
git add apps/planner-ui/src
git commit -m "#7 planner-ui: Dashboard section (#/dashboard) + nav-rail wiring

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Task Z: Docs, trackers, Docker redeploy

**Files:** `apps/planner-ui/UAT.md` (add a section for part context + dashboard cases), `ROADMAP.md`, `TASKS.md`, `CLAUDE.md`; rebuild the Docker UI + BFF images.

- [ ] **Step 1** Update `UAT.md` with cases for: enriched columns (on hand / need), the part drawer (description, stock breakdown, lead time, open orders, demand trend), and the Dashboard (KPIs, breakdowns, top shortages), each mapped to its Vitest/pytest test. Bump the test-count references.
- [ ] **Step 2** Update `ROADMAP.md` / `TASKS.md` / `CLAUDE.md` (the `services/agent-spine` BFF bullet gains `/parts` + `/dashboard`; the `apps/planner-ui` bullet gains the part drawer + Dashboard).
- [ ] **Step 3** Redeploy: `cd <repo> && docker compose build bff ui && docker compose up -d` (single build — do NOT run concurrent builds; BuildKit races). Verify: `curl -s localhost:8088/v1/tenants/acme/dashboard | python3 -c "import sys,json;print(json.load(sys.stdin)['parts'],'parts')"` and open http://localhost:8088.
- [ ] **Step 4** Commit + push.

```bash
git add -A
git commit -m "#7 planner-ui: UAT + trackers for part context + dashboard; docker redeploy

Co-Authored-By: Claude Opus 4.8 <noreply@anthropic.com>"
```

---

## Self-Review

**Spec coverage:** §3.1 retain fs → C1. §3.2 enrich models → A1 (+ C3 UI mirror). §3.3 PartContext → C1/C2; UI drawer + trend → C4/C5. §3.4 dashboard → B1/B2; UI → B4. §4.1 client/fakes → C3/B3. §4.2 enriched queue/detail → C5. §4.3 drawer+trend → C4/C5. §4.4 Dashboard section → B4. §7 tests → each task's TDD. §8 acceptance → the full task set + Task Z redeploy. No gaps.

**Placeholder scan:** every code step carries real code. The two "find the bff test module / fixture" notes point at concrete existing files the implementer must read (`services/agent-spine/tests/`, the app-test `create_planner_app` fixture) — a real instruction, since fixture names are owned by existing tests, not deferred work.

**Type consistency:** `part_context(pn, location)` / `getPartContext(tenant, pn, location)`; `dashboard()` / `getDashboard(tenant)`; model field names identical between BFF (Task C1/B1) and TS mirrors (C3/B3): `StockBreakdown{on_hand,serviceable,in_repair,allocated,rental,loan}`, `LeadTimeView{promised_days,realized_mean_days,n_observations}`, `DemandPoint{period_start,removals,issues,total}`, `PartContext{...}`, `DashboardSummary{...}`, `Breakdown{key,count,on_hand,shortage}`, `PartShortfall{pn,location,shortage,on_hand,projected_demand}`. `_safe` used identically in C1 + B1. Consistent.
