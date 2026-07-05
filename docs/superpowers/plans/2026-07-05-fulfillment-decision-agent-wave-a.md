# Sub-project #12 Wave A — Requisition Data Wiring Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Wire the currently-unconsumed `order_plan_data_requisition` extract domain (#9) into the feature store and expose it on the recommender context — pure data plumbing, no ranking or decision logic.

**Architecture:** A new `RequisitionSnapshot` schema (demand-side, deliberately separate from the existing supply-side `OpenOrdersSnapshot`) flows through three layers already established by every other feature group in this codebase: `extract_loader.py` seeds it from raw extract rows → `FeatureStoreClient` stores/retrieves it by `(pn, location)` → `FeatureReader` adapts it to a miss-tolerant optional read → `ContextAssembler` attaches it to `PartLocationContext`.

**Tech Stack:** Python 3.12, pydantic v2, pytest, uv.

## Global Constraints

- Pure data plumbing only — no ranking, routing, or priority-classification logic (that's Wave B, out of scope here).
- `RequisitionSnapshot` stays a distinct schema from `OpenOrdersSnapshot` — one is demand, the other is supply. Do not merge them.
- REPAIR-path availability is not addressed here at all (it's read from the already-existing `OpenOrdersSnapshot` RO entries in Wave B, unchanged by this wave) — do not add any repair-TAT modeling; no data source for it exists anywhere in the 21-domain extract registry (spec §3).
- Priority is explicitly NOT derived in this wave — the raw requisition extract has no priority field, and deriving one from `criticality`/`aog_signal` belongs to Wave B's ranking logic.
- Grounding note: this plan's scope is wider than the approved spec's §4 literally listed (schema + loader + context field). Reading the actual codebase during planning surfaced two additional, mechanically necessary layers the spec didn't call out by name: `FeatureStoreClient`'s getter method and `FeatureReader`'s adapter method — without both, `PartLocationContext.requisition` would always be `None` even when real data exists, defeating the spec's own stated goal. This is a completion of the already-approved capability, not new scope.

---

### Task 1: `ROADMAP.md` — Sub-project #12 section

**Files:**
- Modify: `ROADMAP.md` (insert after the existing `## Wave 3 — Go-Live` section, i.e. after its `**Wave 3 exit:**` line and the `---` divider, before `## Lighthouse Customer Milestones`)

**Interfaces:** None — documentation only.

- [ ] **Step 1: Insert the new Wave 4 / Sub-project #12 section**

Insert this block (matching the existing sections' header/divider format exactly — a `##` wave heading, blank line, `###` sub-project heading with the same `(P-priority, team) 🏗️` suffix style, a `Plan:` line, blank line, then a `---` divider before the next section):

```markdown
## Wave 4 — Fulfillment-Path Decision Agent

### Sub-project #12 — Fulfillment-Path Decision Agent (P2, eMRO team) 🏗️
Plan: [2026-07-05-fulfillment-decision-agent-wave-a-design.md](docs/superpowers/specs/2026-07-05-fulfillment-decision-agent-wave-a-design.md)

---
```

Do not add any `[x]`/`[ ]` bullets yet — Task 5 adds the first dated completion bullet once Wave A actually ships, matching how every other sub-project section in this file is written retrospectively.

- [ ] **Step 2: Commit**

```bash
git add ROADMAP.md
git commit -m "docs: add Sub-project #12 (Fulfillment-Path Decision Agent) roadmap section"
```

---

### Task 2: `RequisitionSnapshot` schema + feature-store getter

**Files:**
- Modify: `services/feature-store/src/trax_io_feature_store/schemas/features.py` (new schema section, after the existing `# 10. open_orders_snapshot` section)
- Modify: `services/feature-store/src/trax_io_feature_store/client.py` (new Protocol method + concrete implementation + import)
- Test: `services/feature-store/tests/test_schemas.py`

**Interfaces:**
- Produces: `RequisitionLine(requisition_id: str, qty_needed: NonNegativeInt, need_by: date | None, alt_source_location: str | None)`, `RequisitionSnapshot(tenant_id: str, pn: str, location: str, snapshot_at: datetime, lines: list[RequisitionLine], total_qty_needed: NonNegativeInt, extract_date: date)`, and `FeatureStoreClient.get_requisition_snapshot(*, tenant, pn, location) -> RequisitionSnapshot` — all consumed by Task 3 and Task 4.

- [ ] **Step 1: Write the failing test**

Add to `services/feature-store/tests/test_schemas.py` (add `RequisitionLine, RequisitionSnapshot` to the existing `from trax_io_feature_store.schemas import (...)` block at the top of the file, alongside `OpenOrdersSnapshot`/`OpenOrder` — read that import block yourself first to place the new names correctly):

```python
def test_requisition_snapshot_validates():
    snap = RequisitionSnapshot(
        tenant_id="aircanada",
        pn="P-INT",
        location="YYZ-MAIN",
        snapshot_at=datetime(2026, 4, 15, 6, 0, tzinfo=UTC),
        lines=[
            RequisitionLine(
                requisition_id="REQ_1001_1",
                qty_needed=3,
                need_by=date(2026, 5, 1),
                alt_source_location="YOW",
            )
        ],
        total_qty_needed=3,
        extract_date=EXTRACT,
    )
    assert snap.lines[0].requisition_id == "REQ_1001_1"
    assert snap.total_qty_needed == 3
```

(`EXTRACT`, `UTC`, `date`, `datetime` are already imported/defined at the top of this test file for the neighboring `OpenOrdersSnapshot` test — reuse them, don't reimport.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/feature-store && uv run --extra dev pytest tests/test_schemas.py::test_requisition_snapshot_validates -v`
Expected: FAIL with `ImportError` or `NameError` (`RequisitionSnapshot`/`RequisitionLine` not defined)

- [ ] **Step 3: Write the schema**

In `services/feature-store/src/trax_io_feature_store/schemas/features.py`, after the existing `# 10. open_orders_snapshot` section (the `OpenOrder`/`OpenOrdersSnapshot` classes), add:

```python
# ---------------------------------------------------------------------------
# 11. requisition_snapshot
# ---------------------------------------------------------------------------


class RequisitionLine(_Base):
    requisition_id: str
    qty_needed: NonNegativeInt
    need_by: date | None = None
    alt_source_location: str | None = None


class RequisitionSnapshot(_Base):
    """Open (unfulfilled) demand-side requisition lines per (pn, location) as of
    snapshot_at. Deliberately separate from OpenOrdersSnapshot: this is demand,
    that is supply."""

    tenant_id: str
    pn: str
    location: str
    snapshot_at: datetime
    lines: list[RequisitionLine] = Field(default_factory=list)
    total_qty_needed: NonNegativeInt
    extract_date: date
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/feature-store && uv run --extra dev pytest tests/test_schemas.py::test_requisition_snapshot_validates -v`
Expected: PASS

- [ ] **Step 5: Add the feature-store getter**

Read `services/feature-store/src/trax_io_feature_store/client.py`'s existing import block (starts around line 20, `from trax_io_feature_store.schemas import (...)`) and add `RequisitionSnapshot` to it, alongside `OpenOrdersSnapshot`.

Find the `get_open_orders_snapshot` Protocol method declaration (a `def get_open_orders_snapshot(self, *, tenant: TenantContext, pn: str, location: str) -> OpenOrdersSnapshot: ...` line inside the `@runtime_checkable` `Protocol` class near the top of the file) and add immediately after it:

```python
    def get_requisition_snapshot(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> RequisitionSnapshot: ...
```

Find the concrete implementation's `get_open_orders_snapshot` method (`return self._fetch(tenant, "open_orders_snapshot", (pn, location))`) and add immediately after it, in the same class:

```python
    def get_requisition_snapshot(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> RequisitionSnapshot:
        return self._fetch(tenant, "requisition_snapshot", (pn, location))  # type: ignore[return-value]
```

- [ ] **Step 6: Run the feature-store test suite**

Run: `cd services/feature-store && uv run --extra dev pytest`
Expected: all tests pass, including a new `test_in_memory_store_conforms_to_protocol`-style check (in `services/feature-store/tests/test_client.py`) — if that test iterates the Protocol's methods generically, it will already cover the new method with no changes needed; if it fails, read that test and fix only what the new method requires (do not weaken the test).

Run: `cd services/feature-store && uv run --extra dev ruff check .`
Expected: no issues

- [ ] **Step 7: Commit**

```bash
git add services/feature-store/src/trax_io_feature_store/schemas/features.py services/feature-store/src/trax_io_feature_store/client.py services/feature-store/tests/test_schemas.py
git commit -m "feature-store: add RequisitionSnapshot schema and getter"
```

---

### Task 3: `extract_loader.py` — wire domain #9

**Files:**
- Modify: `services/recommendation-engine/src/trax_io_reco/data/extract_loader.py`
- Test: `services/recommendation-engine/tests/test_extract_loader.py`

**Interfaces:**
- Consumes: `RequisitionLine`, `RequisitionSnapshot` from Task 2 (`trax_io_feature_store.schemas`); `fs.seed(tenant_id, bucket, key, value)` (existing); `_i(v, default=0) -> int` and `_parse_date(v) -> date | None` (existing helpers, lines 77 and 107 of this same file).
- Produces: every extract with an `order_plan_data_requisition` domain now seeds a `RequisitionSnapshot` per `(pn, location)`, retrievable via Task 2's `fs.get_requisition_snapshot(...)`.

- [ ] **Step 1: Write the failing test**

Add to `services/recommendation-engine/tests/test_extract_loader.py` (add `RequisitionSnapshot` awareness is not needed directly in the test — it queries via `fs.get_requisition_snapshot`, which returns the object; no new imports needed beyond what's already imported):

```python
def test_extract_loader_wires_requisition_snapshot(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(extract_dir, "order_plan_data_requisition", [
        {"hostpartid": "HYD-PUMP-001", "hostlocid": "YYZ", "hostorderid": "REQ_1001_1",
         "orderstatus": "OPEN", "planquantity": "5", "receivedquantity": "2",
         "planrcvdate": "2026-05-01", "hostreplsourcelocid": "YOW"},
        {"hostpartid": "HYD-PUMP-001", "hostlocid": "YYZ", "hostorderid": "REQ_1002_1",
         "orderstatus": "CLOSED", "planquantity": "1", "receivedquantity": "0",
         "planrcvdate": "2026-05-02", "hostreplsourcelocid": None},
    ])
    fs, _, tenant_id, _ = build_stores_from_extract(extract_dir)
    snap = fs.get_requisition_snapshot(
        tenant=TenantContext(tenant_id=tenant_id), pn="HYD-PUMP-001", location="YYZ"
    )
    assert snap.total_qty_needed == 3  # only the OPEN line counts: 5 - 2 = 3
    assert len(snap.lines) == 1
    assert snap.lines[0].requisition_id == "REQ_1001_1"
    assert snap.lines[0].need_by == date(2026, 5, 1)
    assert snap.lines[0].alt_source_location == "YOW"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/recommendation-engine && uv run --extra dev pytest tests/test_extract_loader.py::test_extract_loader_wires_requisition_snapshot -v`
Expected: FAIL — `fs.get_requisition_snapshot(...)` raises `FeatureStoreLookupError` (nothing seeded yet) or the key `"order_plan_data_requisition"` isn't read at all

- [ ] **Step 3: Wire the domain**

In `services/recommendation-engine/src/trax_io_reco/data/extract_loader.py`:

Add `RequisitionLine, RequisitionSnapshot` to whatever existing import brings in `OpenOrder, OpenOrdersSnapshot` at the top of the file (read that import line yourself first — do not guess its exact current form).

Find the `rows` dict tuple (currently listing `"stock_amount", "stock_level_upload", "part_master", "part_criticality", "pn_vendor_price", "demand_history_rotables", "demand_history_expendables", "location_master", "order_plan", "order_plan_closed_orders", "part_chain_details", "events"`) and add `"order_plan_data_requisition"` to it.

Immediately after the existing `# (g) open_orders_snapshot <- order_plan #8 (OPEN)` block, add:

```python
    # (i) requisition_snapshot <- order_plan_data_requisition #9 (OPEN)   (optional)
    req_by_key: dict[tuple[str, str], list[RequisitionLine]] = defaultdict(list)
    for r in rows["order_plan_data_requisition"]:
        pn, loc = r.get("hostpartid"), r.get("hostlocid")
        if not pn or not loc or str(r.get("orderstatus") or "").upper() != "OPEN":
            continue
        qty_needed = max(0, _i(r.get("planquantity")) - _i(r.get("receivedquantity")))
        if qty_needed <= 0:
            continue
        req_by_key[(pn, loc)].append(RequisitionLine(
            requisition_id=str(r.get("hostorderid") or "?"),
            qty_needed=qty_needed,
            need_by=_parse_date(r.get("planrcvdate")),
            alt_source_location=r.get("hostreplsourcelocid") or None,
        ))
    for (pn, loc), lines in req_by_key.items():
        fs.seed(tenant_id, "requisition_snapshot", (pn, loc), RequisitionSnapshot(
            tenant_id=tenant_id, pn=pn, location=loc, snapshot_at=datetime.combine(
                extract_date, datetime.min.time()),
            lines=lines, total_qty_needed=sum(rl.qty_needed for rl in lines),
            extract_date=extract_date))
```

This is a near-verbatim mirror of the existing `order_plan`/`OpenOrdersSnapshot` block a few lines above it — same aggregate-by-`(pn, loc)` pattern, same `OPEN`-status filter, same non-positive-quantity discard, same `fs.seed(...)` call shape.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd services/recommendation-engine && uv run --extra dev pytest tests/test_extract_loader.py::test_extract_loader_wires_requisition_snapshot -v`
Expected: PASS

- [ ] **Step 5: Run the full test suite**

Run: `cd services/recommendation-engine && uv run --extra dev pytest`
Expected: all tests pass, including every pre-existing `test_extract_loader.py` case (the sample extract fixture already includes an `order_plan_data_requisition.json` file per `tests/fixtures/extract_fixture.py`'s domain list — confirm this doesn't break any pre-existing assertion; if the fixture's default content for this domain produces any requisition lines that affect an unrelated existing test, read and understand why before changing anything).

Run: `cd services/recommendation-engine && uv run --extra dev ruff check .`
Expected: no issues

- [ ] **Step 6: Commit**

```bash
git add services/recommendation-engine/src/trax_io_reco/data/extract_loader.py services/recommendation-engine/tests/test_extract_loader.py
git commit -m "recommendation-engine: wire order_plan_data_requisition (#9) into RequisitionSnapshot"
```

---

### Task 4: `FeatureReader` + `ContextAssembler` + `PartLocationContext` — expose to recommenders

**Files:**
- Modify: `services/recommendation-engine/src/trax_io_reco/data/feature_reader.py`
- Modify: `services/recommendation-engine/src/trax_io_reco/data/assembler.py`
- Modify: `services/recommendation-engine/src/trax_io_reco/contracts/context.py`
- Test: extend `services/recommendation-engine/tests/test_extract_loader.py` (no dedicated assembler/feature_reader test file exists in this codebase today — confirmed by searching; this integration-level file is the established seam for proving data flows end-to-end into a real batch run)

**Interfaces:**
- Consumes: `RequisitionSnapshot` from Task 2; `FeatureStoreLookupError` (already imported in `feature_reader.py`).
- Produces: `PartLocationContext.requisition: RequisitionSnapshot | None` — the final, recommender-visible field. Wave B will consume this; no recommender reads it yet in this wave.

- [ ] **Step 1: Write the failing test**

Add to `services/recommendation-engine/tests/test_extract_loader.py`, reusing the same fixture pattern as Task 3's test:

```python
def test_extract_loader_requisition_reaches_part_location_context(tmp_path) -> None:
    extract_dir = write_sample_extract(tmp_path / "extract")
    _write(extract_dir, "order_plan_data_requisition", [
        {"hostpartid": "HYD-PUMP-001", "hostlocid": "YYZ", "hostorderid": "REQ_2001_1",
         "orderstatus": "OPEN", "planquantity": "4", "receivedquantity": "0",
         "planrcvdate": "2026-06-01", "hostreplsourcelocid": None},
    ])
    fs, inv, tenant_id, keys = build_stores_from_extract(extract_dir)
    assembler = ContextAssembler(features=FeatureReader(fs), inventory_state=inv)
    ctx = assembler.assemble(
        tenant=TenantContext(tenant_id=tenant_id), pn="HYD-PUMP-001", location="YYZ"
    )
    assert ctx.requisition is not None
    assert ctx.requisition.total_qty_needed == 4

    # A key with no requisition data must still assemble cleanly (optional field).
    ctx2 = assembler.assemble(
        tenant=TenantContext(tenant_id=tenant_id), pn="FILTER-EXP-042", location="YYZ"
    )
    assert ctx2.requisition is None
```

Add the needed imports at the top of the test file: `from trax_io_reco.data.assembler import ContextAssembler` and `from trax_io_reco.data.feature_reader import FeatureReader` (check these aren't already imported under different names before adding — read the current top-of-file import block first).

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/recommendation-engine && uv run --extra dev pytest tests/test_extract_loader.py::test_extract_loader_requisition_reaches_part_location_context -v`
Expected: FAIL — `PartLocationContext` has no `requisition` attribute yet (`AttributeError`), or `FeatureReader` has no `get_requisition` method

- [ ] **Step 3: Add the context field**

In `services/recommendation-engine/src/trax_io_reco/contracts/context.py`, add `RequisitionSnapshot` to the existing `from trax_io_feature_store.schemas import (...)` block (currently listing `CausalUtilization, Criticality, DemandHistory, InterchangeableGraph, LeadTimeDistribution, LocationGraph, OpenOrdersSnapshot, PartAttributes, VendorEconomics`), then add a new field to `PartLocationContext` immediately after the existing `open_orders: OpenOrdersSnapshot | None = None` line:

```python
    requisition: RequisitionSnapshot | None = None
```

- [ ] **Step 4: Add the `FeatureReader` adapter method**

In `services/recommendation-engine/src/trax_io_reco/data/feature_reader.py`, add `RequisitionSnapshot` to the existing `from trax_io_feature_store.schemas import (...)` block, then add this method in the `# ---- optional groups (None on miss) ---- #` section, mirroring `get_open_orders` exactly:

```python
    def get_requisition(
        self, *, tenant: TenantContext, pn: str, location: str
    ) -> RequisitionSnapshot | None:
        try:
            return self._c.get_requisition_snapshot(tenant=tenant, pn=pn, location=location)
        except FeatureStoreLookupError:
            return None
```

- [ ] **Step 5: Wire it into `ContextAssembler`**

In `services/recommendation-engine/src/trax_io_reco/data/assembler.py`, in the `assemble` method's `# Optional FS reads.` section (alongside the existing `open_orders = self._fr.get_open_orders(...)` line), add:

```python
        requisition = self._fr.get_requisition(tenant=tenant, pn=pn, location=location)
```

Then add `requisition=requisition,` to the `PartLocationContext(...)` constructor call at the end of `assemble`, alongside the existing `open_orders=open_orders,` line.

- [ ] **Step 6: Run test to verify it passes**

Run: `cd services/recommendation-engine && uv run --extra dev pytest tests/test_extract_loader.py::test_extract_loader_requisition_reaches_part_location_context -v`
Expected: PASS

- [ ] **Step 7: Run the full test suite**

Run: `cd services/recommendation-engine && uv run --extra dev pytest`
Expected: all tests pass — this is the highest-risk step in this wave, since `PartLocationContext` and `ContextAssembler` are used by every existing recommender; confirm nothing broke.

Run: `cd services/recommendation-engine && uv run --extra dev ruff check .`
Expected: no issues

- [ ] **Step 8: Commit**

```bash
git add services/recommendation-engine/src/trax_io_reco/data/feature_reader.py services/recommendation-engine/src/trax_io_reco/data/assembler.py services/recommendation-engine/src/trax_io_reco/contracts/context.py services/recommendation-engine/tests/test_extract_loader.py
git commit -m "recommendation-engine: expose RequisitionSnapshot on PartLocationContext"
```

---

### Task 5: `ROADMAP.md` completion bullet + tracker check

Do this task **last**, after Tasks 1-4 are committed — it needs the real, final test counts.

**Files:**
- Modify: `ROADMAP.md`
- Modify: `CLAUDE.md` (only if its run/test-command table cites a `services/feature-store` test count anywhere — check first, don't assume)

**Interfaces:** None — documentation only.

- [ ] **Step 1: Get the real current counts**

Run: `cd services/feature-store && uv run --extra dev pytest -q 2>&1 | tail -5` and note the total.
Run: `cd services/recommendation-engine && uv run --extra dev pytest -q 2>&1 | tail -5` and note the total.

- [ ] **Step 2: Add the Wave A completion bullet**

Under the `### Sub-project #12` heading added in Task 1, add a dated `[x]` bullet describing what shipped. Read 2-3 existing bullets under Sub-project #7 or #8 first for the exact voice/structure convention (a bold label, 2-4 sentences of what changed and why, a bolded test-count sentence, a trailing em-dash date). Then write one in that same style covering: the new `RequisitionSnapshot` schema (deliberately separate from `OpenOrdersSnapshot`), domain #9 wiring in `extract_loader.py`, and the `FeatureReader`/`ContextAssembler`/`PartLocationContext` exposure — plus explicitly note the standing v1 limitation from spec §3 (REPAIR routing will only ever be possible for already-open repair orders, since no repair-TAT data source exists anywhere in the extract registry). Cite the real test counts from Step 1, not estimates.

- [ ] **Step 3: Check CLAUDE.md**

Search `CLAUDE.md` for any existing `services/feature-store` test-count citation (e.g. in the run/test/build command table near the top of the file, or in prose describing the package). If one exists, update it to the real count from Step 1. If none exists, make no change — do not invent a new tracker entry this project doesn't already have a place for.

- [ ] **Step 4: Commit**

```bash
git add ROADMAP.md CLAUDE.md
git commit -m "docs: Wave A (requisition data wiring) tracker updates"
```

(Omit `CLAUDE.md` from the `git add` if Step 3 found nothing to change.)

---

## Final verification

1. `cd services/feature-store && uv run --extra dev pytest && uv run --extra dev ruff check .` — all green.
2. `cd services/recommendation-engine && uv run --extra dev pytest && uv run --extra dev ruff check .` — all green.
3. Live-verify is not applicable — this wave has no UI or user-visible behavior; correctness is fully covered by the automated tests above.
