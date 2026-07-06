# apps/web CSV Export — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add server-side CSV export of the full filtered recommendations set to `apps/web`, surfaced as an "Export CSV" link on both the Workbench and AI Recommendations views.

**Architecture:** A new BFF route (`GET .../recommendations/export.csv`) reuses the store's existing filter/sort logic (`_sorted_entries`) with no pagination, serializes every matching `QueueRow` to CSV via Python's stdlib `csv` module, and returns it with a `Content-Disposition: attachment` header. The `apps/web` frontend builds the export URL from current filter state and triggers download via a plain `<a href>` (browser navigation, not `fetch()`) — which also sidesteps the known standalone-dev CORS gap.

**Tech Stack:** Python 3.12 + FastAPI + pydantic + pytest (`services/agent-spine`); React 18 + TypeScript + Vitest + Testing Library (`apps/web`).

## Global Constraints

- Do NOT touch the (now-retired) `apps/planner-ui` review UI at all — it kept its existing client-side export unchanged for the duration of this parity effort.
- Do NOT implement any of Waves 2–4's territory (no writeback-history UI, no Reports/BVR view, no dark theme). This wave is CSV export only.
- The CSV column set is exactly these 14, in this order (verbatim from `apps/planner-ui/src/lib/queryView.ts:84-99`): `recommendation_id, pn, location, description, type, tier, criticality_tier, aog_risk_level, confidence_score, recommended_quantity, estimated_cost_impact, priority_score, status, reason`.
- Every one of those 14 fields already exists on `QueueRow` (`services/agent-spine/src/trax_io_spine/bff/models.py:45-64`) — no new data is computed, only re-serialized.
- **Route declaration order is load-bearing:** the export route MUST be declared BEFORE the existing `/recommendations/{rec_id}` detail route (currently `app.py:100`). FastAPI matches in declaration order; a literal `/recommendations/export.csv` declared after `/recommendations/{rec_id}` would be captured as `rec_id="export.csv"` and 404. Insert the export route immediately after the `queue` route (ends `app.py:98`) and before `detail` (`app.py:100`).
- CSV cell coercion is `str(value)` for every column. Verified: all enum fields stringify to their bare value — `TaskStatus`/`RecommendationType` (StrEnum) → `"pending"`/`"purchase"`; `AutonomyTier`/`AogRiskLevel` (IntEnum) → `"1"`/`"3"`; `Decimal` → its numeric string.
- Backend tests run with: `cd services/agent-spine && uv run --extra dev --extra bff pytest <path>`. Lint: `uv run --extra dev ruff check .`.
- Frontend tests run with: `cd apps/web && npm test`. Typecheck/build: `npm run build`. Lint: `npm run lint`.
- The unified `apps/web` must remain embeddable in eMRO later (iframe/module) — no work required in this wave (both apps already use `HashRouter`, iframes isolate CSS), but do not introduce anything that would preclude it.

---

### Task 1: `PlannerStore.list_queue_all` — the unpaginated filtered+sorted query

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/store.py` (add method next to `list_queue_page`, which is at lines 446-474)
- Test: `services/agent-spine/tests/bff/test_store_reads.py` (existing file — add tests alongside the existing `list_queue_page` tests)

**Interfaces:**
- Consumes: the store's existing private `self._sorted_entries(status=, sort_by=, sort_dir=, tier=, type_=, aog_min=)` (store.py:414-440) and `self._row(entry)` (store.py:319-335).
- Produces: `list_queue_all(*, status: TaskStatus = TaskStatus.PENDING, sort_by: QueueSortKey = QueueSortKey.PRIORITY, sort_dir: str = "desc", tier: AutonomyTier | None = None, type_: RecommendationType | None = None, aog_min: AogRiskLevel | None = None) -> list[QueueRow]` — every matching row, no pagination, same order `list_queue_page` pages through.

- [ ] **Step 1: Write the failing tests**

Add to `services/agent-spine/tests/bff/test_store_reads.py` (the file already imports `QueueSortKey`, `TaskStatus`, `AogRiskLevel`, `PlannerStore` and defines `_store()`):

```python
def test_list_queue_all_returns_every_pending_row_no_pagination():
    store = _store()
    all_rows = store.list_queue_all()
    # Same total the paged query reports, but every row in one list (no slicing).
    _, total = store.list_queue_page(limit=1, offset=0)
    assert len(all_rows) == total
    assert all(r.status is TaskStatus.PENDING for r in all_rows)


def test_list_queue_all_matches_paged_order_and_content():
    store = _store()
    all_rows = store.list_queue_all(sort_by=QueueSortKey.COST_IMPACT, sort_dir="asc")
    # A page larger than the whole set == the whole set, in identical order.
    page, total = store.list_queue_page(
        limit=100_000, offset=0, sort_by=QueueSortKey.COST_IMPACT, sort_dir="asc"
    )
    assert [r.recommendation_id for r in all_rows] == [r.recommendation_id for r in page]
    assert len(all_rows) == total


def test_list_queue_all_applies_tier_and_status_filters():
    store = _store()
    approved = store.list_queue_all(status=TaskStatus.APPROVED)
    assert all(r.status is TaskStatus.APPROVED for r in approved)
    tier1 = store.list_queue_all(tier=1)
    assert all(r.tier == 1 for r in tier1)
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agent-spine && uv run --extra dev --extra bff pytest tests/bff/test_store_reads.py -k list_queue_all -v`
Expected: FAIL with `AttributeError: 'PlannerStore' object has no attribute 'list_queue_all'`.

- [ ] **Step 3: Implement `list_queue_all`**

In `services/agent-spine/src/trax_io_spine/bff/store.py`, add immediately after `list_queue_page` (after line 474):

```python
    def list_queue_all(
        self,
        *,
        status: TaskStatus = TaskStatus.PENDING,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY,
        sort_dir: str = "desc",
        tier: AutonomyTier | None = None,
        type_: RecommendationType | None = None,
        aog_min: AogRiskLevel | None = None,
    ) -> list[QueueRow]:
        """Full filtered+sorted queue with NO pagination — every matching row.

        Backs the CSV export route (which must cover the whole filtered set, not
        one page). Shares `_sorted_entries` with `list_queue_page` so filter/sort
        semantics are identical; the only difference is the absence of a slice.
        """
        entries = self._sorted_entries(
            status=status,
            sort_by=sort_by,
            sort_dir=sort_dir,
            tier=tier,
            type_=type_,
            aog_min=aog_min,
        )
        return [self._row(e) for e in entries]
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/agent-spine && uv run --extra dev --extra bff pytest tests/bff/test_store_reads.py -k list_queue_all -v`
Expected: 3 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/bff/store.py services/agent-spine/tests/bff/test_store_reads.py
git commit -m "feat(bff): add PlannerStore.list_queue_all for unpaginated CSV export"
```

---

### Task 2: `queue_rows_to_csv` — the pure CSV serializer

**Files:**
- Create: `services/agent-spine/src/trax_io_spine/bff/csv_export.py`
- Test: `services/agent-spine/tests/bff/test_csv_export.py` (new file)

**Interfaces:**
- Consumes: `QueueRow` (`trax_io_spine.bff.models.QueueRow`).
- Produces: `CSV_COLUMNS: tuple[str, ...]` (the 14 column names, in order) and `queue_rows_to_csv(rows: list[QueueRow]) -> str` (a full CSV document: header line + one line per row, `\r\n`-terminated as the stdlib `csv` module emits, every cell quoted).

- [ ] **Step 1: Write the failing tests**

Create `services/agent-spine/tests/bff/test_csv_export.py`:

```python
"""apps/web CSV export — the pure QueueRow -> CSV serializer (no HTTP)."""

from __future__ import annotations

import csv
import io
from decimal import Decimal

from trax_io_spine.bff.csv_export import CSV_COLUMNS, queue_rows_to_csv
from trax_io_spine.bff.models import QueueRow


def _row(**overrides) -> QueueRow:
    base = dict(
        recommendation_id="rec-1",
        pn="19000-231-3",
        location="YYC",
        type="purchase",
        criticality_tier=2,
        aog_risk_level=3,
        confidence_score=0.92,
        recommended_quantity=4,
        estimated_cost_impact=Decimal("-1200.00"),
        tier=2,
        priority_score=88.4,
        status="pending",
        reason="Projected shortage within lead time",
        approvable=True,
        description="WATER TANK HEATER BLANKET",
        current_stock=1,
        shortage_quantity=3,
        recommended_location=None,
        horizon_days=90,
    )
    base.update(overrides)
    return QueueRow(**base)


def test_csv_columns_are_the_14_canonical_columns_in_order():
    assert CSV_COLUMNS == (
        "recommendation_id", "pn", "location", "description", "type", "tier",
        "criticality_tier", "aog_risk_level", "confidence_score",
        "recommended_quantity", "estimated_cost_impact", "priority_score",
        "status", "reason",
    )


def test_header_plus_one_row_per_entry():
    csv_text = queue_rows_to_csv([_row(recommendation_id="a"), _row(recommendation_id="b")])
    parsed = list(csv.reader(io.StringIO(csv_text)))
    assert parsed[0] == list(CSV_COLUMNS)
    assert len(parsed) == 3  # header + 2 rows
    assert parsed[1][0] == "a"
    assert parsed[2][0] == "b"


def test_enum_and_decimal_cells_render_as_bare_values():
    parsed = list(csv.reader(io.StringIO(queue_rows_to_csv([_row()]))))
    header, data = parsed[0], parsed[1]
    cell = dict(zip(header, data))
    assert cell["type"] == "purchase"          # StrEnum
    assert cell["tier"] == "2"                  # IntEnum
    assert cell["aog_risk_level"] == "3"        # IntEnum
    assert cell["status"] == "pending"          # StrEnum
    assert cell["estimated_cost_impact"] == "-1200.00"  # Decimal


def test_comma_and_quote_in_reason_round_trip():
    tricky = 'Shortage, per vendor "ACME", within lead time'
    parsed = list(csv.reader(io.StringIO(queue_rows_to_csv([_row(reason=tricky)]))))
    cell = dict(zip(parsed[0], parsed[1]))
    assert cell["reason"] == tricky  # csv.reader un-escapes what csv.writer escaped


def test_empty_rows_yields_header_only():
    parsed = list(csv.reader(io.StringIO(queue_rows_to_csv([]))))
    assert parsed == [list(CSV_COLUMNS)]
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agent-spine && uv run --extra dev --extra bff pytest tests/bff/test_csv_export.py -v`
Expected: FAIL at import (`ModuleNotFoundError: No module named 'trax_io_spine.bff.csv_export'`).

- [ ] **Step 3: Implement the serializer**

Create `services/agent-spine/src/trax_io_spine/bff/csv_export.py`:

```python
"""Pure QueueRow -> CSV serialization for the apps/web export route.

The 14-column set + order below is canonical here. Cells are the bare
str() of each value: StrEnum/IntEnum fields stringify to their value
("pending", "3"), Decimal to its numeric string — a flat, spreadsheet-
friendly rendering of each field.
"""

from __future__ import annotations

import csv
import io

from trax_io_spine.bff.models import QueueRow

CSV_COLUMNS: tuple[str, ...] = (
    "recommendation_id",
    "pn",
    "location",
    "description",
    "type",
    "tier",
    "criticality_tier",
    "aog_risk_level",
    "confidence_score",
    "recommended_quantity",
    "estimated_cost_impact",
    "priority_score",
    "status",
    "reason",
)


def queue_rows_to_csv(rows: list[QueueRow]) -> str:
    """Serialize queue rows to a CSV document (header + one line per row)."""
    buffer = io.StringIO()
    writer = csv.writer(buffer, quoting=csv.QUOTE_ALL)
    writer.writerow(CSV_COLUMNS)
    for row in rows:
        writer.writerow([str(getattr(row, col)) for col in CSV_COLUMNS])
    return buffer.getvalue()
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/agent-spine && uv run --extra dev --extra bff pytest tests/bff/test_csv_export.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/bff/csv_export.py services/agent-spine/tests/bff/test_csv_export.py
git commit -m "feat(bff): add queue_rows_to_csv pure serializer (14-column planner-ui shape)"
```

---

### Task 3: `GET .../recommendations/export.csv` route

**Files:**
- Modify: `services/agent-spine/src/trax_io_spine/bff/app.py` (insert route between the `queue` route ending at line 98 and the `detail` route at line 100; add the `csv_export` import near the top)
- Test: `services/agent-spine/tests/bff/test_export.py` (new file)

**Interfaces:**
- Consumes: `PlannerStore.list_queue_all(...)` (Task 1), `queue_rows_to_csv(...)` + `CSV_COLUMNS` (Task 2), the existing `_store(tenant_id)` helper (app.py:45-49), and FastAPI's `Response`/`Query` (already imported, app.py:7).
- Produces: route `GET /v1/tenants/{tenant_id}/recommendations/export.csv` with query params `status` (default `TaskStatus.PENDING`), `sort_by`, `sort_dir`, `tier`, `type`, `aog_min` — identical types/defaults to the existing `queue` route (app.py:66-80) minus `limit`/`offset`. Returns `text/csv` with `Content-Disposition: attachment; filename="trax-io-{status}-recommendations.csv"`.

- [ ] **Step 1: Write the failing tests**

Create `services/agent-spine/tests/bff/test_export.py`:

```python
"""apps/web CSV export route — content, headers, filter narrowing, tenant 404."""

from __future__ import annotations

import csv
import io
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from trax_io_spine.bff.app import create_planner_app
from trax_io_spine.bff.csv_export import CSV_COLUMNS
from trax_io_spine.bff.store import PlannerStore

_SAMPLE = (
    Path(__file__).resolve().parents[3] / "recommendation-engine" / "examples" / "extract_sample"
)
_NOW = datetime(2026, 4, 1, tzinfo=UTC)


def _client() -> TestClient:
    store = PlannerStore.from_extract(tenant_id="acme", extract_dir=str(_SAMPLE), now=_NOW)
    return TestClient(create_planner_app({"acme": store}))


def test_export_route_returns_csv_with_attachment_header():
    resp = _client().get("/v1/tenants/acme/recommendations/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert resp.headers["content-disposition"] == (
        'attachment; filename="trax-io-pending-recommendations.csv"'
    )


def test_export_body_has_14_column_header_and_one_row_per_pending_rec():
    client = _client()
    resp = client.get("/v1/tenants/acme/recommendations/export.csv")
    parsed = list(csv.reader(io.StringIO(resp.text)))
    assert parsed[0] == list(CSV_COLUMNS)
    # data rows == the full pending queue total the paged endpoint reports.
    total = client.get("/v1/tenants/acme/recommendations?limit=1&offset=0").json()["total"]
    assert len(parsed) - 1 == total


def test_export_narrows_with_tier_filter_like_the_paged_endpoint():
    client = _client()
    export = client.get("/v1/tenants/acme/recommendations/export.csv?tier=1")
    parsed = list(csv.reader(io.StringIO(export.text)))
    total_tier1 = client.get(
        "/v1/tenants/acme/recommendations?tier=1&limit=1&offset=0"
    ).json()["total"]
    assert len(parsed) - 1 == total_tier1
    tier_col = list(CSV_COLUMNS).index("tier")
    assert all(row[tier_col] == "1" for row in parsed[1:])


def test_export_filename_reflects_status():
    resp = _client().get("/v1/tenants/acme/recommendations/export.csv?status=approved")
    assert resp.headers["content-disposition"] == (
        'attachment; filename="trax-io-approved-recommendations.csv"'
    )


def test_export_unknown_tenant_404():
    resp = _client().get("/v1/tenants/ghost/recommendations/export.csv")
    assert resp.status_code == 404


def test_export_csv_path_is_not_shadowed_by_the_detail_route():
    # Regression guard: export.csv must NOT be captured as rec_id="export.csv"
    # by the /recommendations/{rec_id} detail route. A 404 here would mean the
    # export route is declared AFTER the detail route (wrong order).
    resp = _client().get("/v1/tenants/acme/recommendations/export.csv")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd services/agent-spine && uv run --extra dev --extra bff pytest tests/bff/test_export.py -v`
Expected: FAIL — the route doesn't exist; requests to `export.csv` are captured by the `/recommendations/{rec_id}` detail route and return 404 (or the content-type assertion fails).

- [ ] **Step 3: Add the import and the route**

In `services/agent-spine/src/trax_io_spine/bff/app.py`, add to the `from trax_io_spine.bff...` imports near the top (after the `store` import block, around line 35):

```python
from trax_io_spine.bff.csv_export import queue_rows_to_csv
```

Then insert this route **between** the `queue` route (which ends with `return PagedQueue(...)` at line 98) and the `@app.get(base + "/recommendations/{rec_id}")` detail route (line 100). Declaration order is load-bearing — see Global Constraints.

```python
    @app.get(base + "/recommendations/export.csv")
    def export_csv(
        tenant_id: str,
        status: TaskStatus = TaskStatus.PENDING,
        sort_by: QueueSortKey = QueueSortKey.PRIORITY,
        sort_dir: Literal["asc", "desc"] = "desc",
        tier: AutonomyTier | None = Query(None),  # noqa: B008
        type: RecommendationType | None = Query(None),  # noqa: B008
        aog_min: AogRiskLevel | None = Query(None),  # noqa: B008
    ) -> Response:
        # Full filtered set, no pagination (export must cover every matching row,
        # not one page). Same filter/sort params as the queue route above, minus
        # limit/offset. MUST be declared before /recommendations/{rec_id} or the
        # literal "export.csv" path is captured as rec_id. `type` shadows the
        # builtin only within this signature (matches the queue route's param name,
        # which the QueueRow/BulkApproveFilter wire contract uses); passed to the
        # store as type_.
        rows = _store(tenant_id).list_queue_all(
            status=status,
            sort_by=sort_by,
            sort_dir=sort_dir,
            tier=tier,
            type_=type,
            aog_min=aog_min,
        )
        return Response(
            content=queue_rows_to_csv(rows),
            media_type="text/csv",
            headers={
                "Content-Disposition": (
                    f'attachment; filename="trax-io-{status}-recommendations.csv"'
                ),
            },
        )
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd services/agent-spine && uv run --extra dev --extra bff pytest tests/bff/test_export.py -v`
Expected: 6 passed.

- [ ] **Step 5: Run the full BFF suite + lint to confirm no regressions**

Run: `cd services/agent-spine && uv run --extra dev --extra bff pytest tests/bff/ -q && uv run --extra dev ruff check src/trax_io_spine/bff/`
Expected: all pass, ruff clean. (The existing `test_app.py` route tests must still pass — confirms the new route didn't disturb the existing `/recommendations` or `/recommendations/{rec_id}` routes.)

- [ ] **Step 6: Commit**

```bash
git add services/agent-spine/src/trax_io_spine/bff/app.py services/agent-spine/tests/bff/test_export.py
git commit -m "feat(bff): add GET recommendations/export.csv route (declared before detail route)"
```

---

### Task 4: `recommendationsExportUrl()` frontend URL builder

**Files:**
- Modify: `apps/web/src/lib/api/client.ts` (add exported function + params interface; `DEFAULT_BFF_URL`, `DEFAULT_TENANT`, `BASE_URL`, and the enum types already live here)
- Test: `apps/web/src/lib/api/client.test.ts` (existing file — add a `describe` block)

**Interfaces:**
- Consumes: `BASE_URL` (client.ts:28), `DEFAULT_TENANT` (client.ts:26), and types `TaskStatus`, `QueueSortKey`, `AutonomyTier`, `RecommendationType` from `@/lib/api/types`.
- Produces: `recommendationsExportUrl(params?: RecommendationsExportParams, tenant?: string): string` — a full URL to the export route. Always emits `status`, `sort_by`, `sort_dir` (mirroring `getQueue`'s always-emitted params minus limit/offset); omits `tier`/`type`/`aog_min` when undefined (mirroring `getQueue`'s omit-when-undefined behavior, client.ts:115-117). Defaults: `status="pending"`, `sortBy="priority_score"`, `sortDir="desc"`.

- [ ] **Step 1: Write the failing tests**

Add to `apps/web/src/lib/api/client.test.ts` (imports at top already include `bffClient`, `DEFAULT_BFF_URL`; add `recommendationsExportUrl` to that import). Append this `describe` block:

```typescript
describe("recommendationsExportUrl", () => {
  it("builds the export URL with default status/sort, omitting tier/type/aog", () => {
    const url = recommendationsExportUrl({}, "acme");
    expect(url).toBe(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations/export.csv?status=pending&sort_by=priority_score&sort_dir=desc`,
    );
  });

  it("defaults to the acme tenant when none is given", () => {
    expect(recommendationsExportUrl()).toContain("/v1/tenants/acme/recommendations/export.csv");
  });

  it("appends tier/type/aog_min when provided", () => {
    const url = recommendationsExportUrl(
      { status: "pending", sortBy: "estimated_cost_impact", sortDir: "asc", tier: 2, type: "purchase", aogMin: 3 },
      "acme",
    );
    expect(url).toBe(
      `${DEFAULT_BFF_URL}/v1/tenants/acme/recommendations/export.csv?status=pending&sort_by=estimated_cost_impact&sort_dir=asc&tier=2&type=purchase&aog_min=3`,
    );
  });

  it("omits tier/type/aog_min entirely when undefined", () => {
    const url = recommendationsExportUrl({ status: "pending" }, "acme");
    expect(url).not.toContain("tier=");
    expect(url).not.toContain("type=");
    expect(url).not.toContain("aog_min=");
  });
});
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd apps/web && npm test -- client.test`
Expected: FAIL — `recommendationsExportUrl` is not exported (compile/import error).

- [ ] **Step 3: Implement the URL builder**

In `apps/web/src/lib/api/client.ts`, add the type imports if not already present (`QueueSortKey`, `AutonomyTier`, `RecommendationType`, `TaskStatus` are already imported for `getQueue`). Add near the top-level exports (e.g. just above `export const bffClient = {`):

```typescript
export interface RecommendationsExportParams {
  status?: TaskStatus;
  sortBy?: QueueSortKey;
  sortDir?: "asc" | "desc";
  tier?: AutonomyTier;
  type?: RecommendationType;
  aogMin?: number;
}

/**
 * Full URL to the BFF's CSV export route. Mirrors `getQueue`'s query-string
 * shape (always emits status/sort_by/sort_dir, omits tier/type/aog_min when
 * undefined) but has no limit/offset — the export covers the whole filtered
 * set. Consumed as an `<a href>` (browser navigation triggers the download via
 * the response's Content-Disposition header), not `fetch()`.
 */
export function recommendationsExportUrl(
  params: RecommendationsExportParams = {},
  tenant: string = DEFAULT_TENANT,
): string {
  const {
    status = "pending",
    sortBy = "priority_score",
    sortDir = "desc",
    tier,
    type,
    aogMin,
  } = params;
  const search = new URLSearchParams({ status, sort_by: sortBy, sort_dir: sortDir });
  if (tier !== undefined) search.set("tier", String(tier));
  if (type !== undefined) search.set("type", type);
  if (aogMin !== undefined) search.set("aog_min", String(aogMin));
  return `${BASE_URL}/v1/tenants/${encodeURIComponent(tenant)}/recommendations/export.csv?${search.toString()}`;
}
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd apps/web && npm test -- client.test`
Expected: the 4 new tests pass (plus all existing client tests still green).

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/api/client.ts apps/web/src/lib/api/client.test.ts
git commit -m "feat(web): add recommendationsExportUrl BFF export-URL builder"
```

---

### Task 5: Workbench "Export CSV" link

**Files:**
- Modify: `apps/web/src/features/workbench/Workbench.tsx` (add the link to the bulk-action row at lines 272-291; `Button` is already imported at line 4, `AOG_ONLY_MIN` is defined at line 47)
- Test: `apps/web/src/features/workbench/Workbench.test.tsx` (existing file)

**Interfaces:**
- Consumes: `recommendationsExportUrl` + `RecommendationsExportParams` (Task 4), Workbench's existing `queryState` (`{ sort, dir, tier, type, aogOnly }`, from `workbenchQueryState.ts`), `AOG_ONLY_MIN` (Workbench.tsx:47), and the `Button` component's `asChild` prop (renders its child as the element — confirmed `apps/web/src/components/ui/button.tsx` uses `@radix-ui/react-slot`).
- Produces: an `<a href={...}>Export CSV</a>` whose `href` reflects the currently-active Workbench filters/sort, always with `status=pending`.

- [ ] **Step 1: Write the failing test**

Add to `apps/web/src/features/workbench/Workbench.test.tsx` (it already has `renderWithProviders`, `mockFetchRouter`, `row()`, and imports from `@testing-library/react`). Add a test that renders Workbench, waits for the queue to load, and asserts the export link's href:

```typescript
it("renders an Export CSV link whose href reflects the active filters", async () => {
  const fetchMock = mockFetchRouter({
    queue: { items: [row()], total: 1, limit: 25, offset: 0 },
    killswitch: { engaged: false },
  });
  vi.stubGlobal("fetch", fetchMock);

  // Deep-link an active tier=2 filter via the URL so the export href must reflect it.
  renderWithProviders(<Workbench />, ["/?tier=2"]);

  const link = await screen.findByRole("link", { name: /export csv/i });
  const href = link.getAttribute("href") ?? "";
  expect(href).toContain("/recommendations/export.csv?");
  expect(href).toContain("status=pending");
  expect(href).toContain("tier=2");
});
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/web && npm test -- Workbench.test`
Expected: FAIL — no link with accessible name "Export CSV" is found.

- [ ] **Step 3: Add the Export CSV link**

In `apps/web/src/features/workbench/Workbench.tsx`, add the import at the top (alongside the existing `@/lib/api/...` imports):

```typescript
import { recommendationsExportUrl } from "@/lib/api/client";
```

Compute the export URL inside the component body (near where `candidates`/other derived values are computed, before the `return`):

```typescript
  const exportUrl = recommendationsExportUrl({
    status: "pending",
    sortBy: queryState.sort,
    sortDir: queryState.dir,
    tier: queryState.tier === "all" ? undefined : queryState.tier,
    type: queryState.type === "all" ? undefined : queryState.type,
    aogMin: queryState.aogOnly ? AOG_ONLY_MIN : undefined,
  });
```

Then, in the bulk-action row (`<div className="flex items-center gap-3">` at line 273), add the export link after the "Accept high-confidence" `Button` and its success/help text (keep it inside that same flex row). Use `Button` with `asChild` wrapping a real anchor:

```tsx
        <Button variant="outline" size="sm" asChild>
          <a href={exportUrl} download>
            Export CSV
          </a>
        </Button>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web && npm test -- Workbench.test`
Expected: all Workbench tests pass, including the new one.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/features/workbench/Workbench.tsx apps/web/src/features/workbench/Workbench.test.tsx
git commit -m "feat(web): add Export CSV link to Workbench reflecting active filters"
```

---

### Task 6: AI Recommendations "Export CSV" link

**Files:**
- Modify: `apps/web/src/features/recommendations/AiRecommendations.tsx` (add link to the header region, lines 59-62)
- Test: `apps/web/src/features/recommendations/AiRecommendations.test.tsx` (existing file)

**Interfaces:**
- Consumes: `recommendationsExportUrl` (Task 4) and the `Button` component's `asChild` prop.
- Produces: an `<a href={...}>Export CSV</a>` fixed to `status=pending` with no other params (this view has no filter/sort UI — see `AiRecommendations.tsx:22`, `useQueue("pending", 50, 0)`), exporting the full pending queue rather than the 10 displayed cards.

- [ ] **Step 1: Write the failing test**

The file already has `renderWithProviders(ui)`, `mockFetchRouter({ queue, details, killswitch? })`, `row()`, and `detail()` helpers, and the standard `afterEach(() => vi.unstubAllGlobals())`. Add this test inside the existing `describe("AiRecommendations", ...)` block, mirroring the existing tests' setup exactly:

```typescript
it("renders an Export CSV link fixed to status=pending", async () => {
  const queue: PagedQueue = { items: [row()], total: 1, limit: 50, offset: 0 };
  vi.stubGlobal("fetch", mockFetchRouter({ queue, details: { "rec-1": detail() } }));

  renderWithProviders(<AiRecommendations />);

  const link = await screen.findByRole("link", { name: /export csv/i });
  const href = link.getAttribute("href") ?? "";
  expect(href).toContain("/recommendations/export.csv?");
  expect(href).toContain("status=pending");
  expect(href).not.toContain("tier=");
  expect(href).not.toContain("type=");
  expect(href).not.toContain("aog_min=");
});
```

(The header — and thus the link — renders once the queue query resolves; the component early-returns a `<QueryLoading>` while pending, so `findByRole`'s built-in wait is required, not optional.)

- [ ] **Step 2: Run the test to verify it fails**

Run: `cd apps/web && npm test -- AiRecommendations.test`
Expected: FAIL — no "Export CSV" link found.

- [ ] **Step 3: Add the Export CSV link**

In `apps/web/src/features/recommendations/AiRecommendations.tsx`, add imports:

```typescript
import { Button } from "@/components/ui/button";
import { recommendationsExportUrl } from "@/lib/api/client";
```

Replace the header block (lines 59-62) so the title row carries the export link on the right:

```tsx
      <header className="flex items-start justify-between gap-4">
        <div>
          <h1 className="text-xl font-semibold text-ink">AI Recommendations</h1>
          <p className="text-sm text-ink-2">Recommendation → reason → action, explained.</p>
        </div>
        <Button variant="outline" size="sm" asChild>
          <a href={recommendationsExportUrl({ status: "pending" })} download>
            Export CSV
          </a>
        </Button>
      </header>
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd apps/web && npm test -- AiRecommendations.test`
Expected: all AI Recommendations tests pass, including the new one.

- [ ] **Step 5: Full frontend gate**

Run: `cd apps/web && npm test && npm run build && npm run lint`
Expected: all Vitest tests pass, `tsc -b && vite build` clean (0 errors), eslint 0 errors (the 2 pre-existing shadcn/ui `react-refresh` warnings on badge.tsx/button.tsx are acceptable — they predate this work).

- [ ] **Step 6: Commit**

```bash
git add apps/web/src/features/recommendations/AiRecommendations.tsx apps/web/src/features/recommendations/AiRecommendations.test.tsx
git commit -m "feat(web): add Export CSV link to AI Recommendations (full pending queue)"
```

---

## Final verification (after all tasks)

- `cd services/agent-spine && uv run --extra dev --extra bff pytest -q && uv run --extra dev ruff check .` — full backend suite green, ruff clean.
- `cd apps/web && npm test && npm run build && npm run lint` — full frontend suite green, build clean, lint clean (bar the 2 pre-existing warnings).
- **Live verification against the Docker deployment** (the stack is already up — bff :8001, web :8089): rebuild the web container (`docker compose -p trax-io-planner up --build -d web`), then in a browser at `http://localhost:8089`:
  - Workbench → toggle a tier/type/AOG filter → click "Export CSV" → confirm a file `trax-io-pending-recommendations.csv` downloads whose row count matches the filtered total (not just the 25-row page), and whose header is the 14 columns.
  - AI Recommendations → click "Export CSV" → confirm the same file downloads with the full pending set (more than the 10 displayed cards).
  - Directly hit `http://localhost:8089/v1/tenants/acme/recommendations/export.csv?tier=1` and confirm it streams CSV (proves the nginx same-origin proxy passes the route through and the download header is honored).
- Update trackers per this repo's end-of-slice convention: `CLAUDE.md` (note apps/web now has CSV export), `ROADMAP.md`, `TASKS.md`, and mark this as Wave 1 of 4 of the apps/web feature-parity effort. Do NOT update the (now-retired) `apps/planner-ui`'s own docs.
