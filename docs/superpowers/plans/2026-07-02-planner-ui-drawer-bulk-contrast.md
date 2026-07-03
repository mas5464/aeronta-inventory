# Planner UI: Detail Drawer, Bulk Results & AAA Contrast — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Close the last unchecked bullet under ROADMAP.md's #7 sub-project by (1) converting `DetailPanel` into a right-side overlay drawer that's deep-linkable via the URL, (2) surfacing per-item bulk-approve outcomes that are currently fetched then discarded, and (3) locking in AAA/AA color contrast with a real, dependency-free automated test plus the color fixes it requires.

**Architecture:** Three independently-testable slices over the existing `apps/planner-ui` React app: a new presentational `Drawer` wrapper component + a `usePlanner`/routing extension for piece 1; a new `usePlanner` state field for piece 2; a new pure math module + CSS-token-parsing test for piece 3. No new npm dependencies anywhere.

**Tech Stack:** React 18, TypeScript 5, Vite 5, Vitest 2 + React Testing Library + jsdom, react-router-dom (`HashRouter`), CSS Modules.

## Global Constraints

- No new npm dependencies — everything (focus trap, contrast math, CSS token parsing) is hand-rolled, matching this codebase's existing "dependency-free" convention (`DemandTrend`, the sibling `apps/web`'s `useFocusTrap`).
- Every task ends green on `cd apps/planner-ui && npm test -- --run` and `npx tsc -b` with zero new lint/type errors.
- Follow the codebase's existing test-fixture idioms exactly: `FakePlannerClient(SAMPLE_SEED.map((e) => ({ ...e })))` for App-level tests (isolates seed mutation across tests), `baseClient({...overrides})` for hook-level tests, per-test method overrides (`fake.approve = async (...) => {...}`) for simulating server variance the fake doesn't model by default.
- Commit after every task (not every step) — one commit per task, following this repo's per-slice commit convention.

---

### Task 1: `FakePlannerClient.approve()` populates a real `writeback` result

**Why first:** piece 2's bulk-result UI (Task 6) needs `ActionResult.writeback.pn/location/status` to build its per-row breakdown. Today the fake always returns `writeback: null` — a real gap versus the actual BFF (which always populates it for a successful approve), not just a testing inconvenience.

**Files:**
- Modify: `apps/planner-ui/src/api/client.ts:284-303` (`FakePlannerClient.approve`)
- Test: `apps/planner-ui/src/api/client.test.ts`

**Interfaces:**
- Consumes: `HistoryEntry` (already defined in `api/types.ts`) returned by `FakePlannerClient`'s private `record()` method (`client.ts:226-259`) — has `tenant_id, pn, location, status, old_values, new_values, changed_at`.
- Produces: `ActionResult.writeback: WritebackResult | null` now populated (not always `null`) for every successful `approve()` call. No signature changes — same `PlannerClient` interface.

- [ ] **Step 1: Write the failing test**

Add to `apps/planner-ui/src/api/client.test.ts`, right after the existing `"approve removes the row from the pending queue"` test (around line 62):

```ts
  it("approve records a full writeback result (pn, location, status, values)", async () => {
    const c = new FakePlannerClient(SAMPLE_SEED);
    const res = await c.approve("acme", "rec-hyd-yyz");
    expect(res.writeback).toMatchObject({
      tenant_id: "acme",
      pn: "HYD-PUMP-001",
      location: "YYZ",
      status: "written",
      old_values: null, // first write for this pn/location: no prior applied value
      new_values: { rop: 9, eoq: 12, safety_stock: 4, max_stock: 24 },
      error_message: null,
    });
    expect(res.message).toBe("written (written)");
  });
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/planner-ui && npx vitest run src/api/client.test.ts -t "records a full writeback"`
Expected: FAIL — `res.writeback` is `null`, not an object; `res.message` is `"written"`, not `"written (written)"`.

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/api/client.ts`, replace the `approve` method (lines 284-303) with:

```ts
  async approve(tenant: string, id: string): Promise<ActionResult> {
    if (this.engaged) throw new PlannerError(423, "kill switch engaged");
    const e = this.require(id);
    if (e.detail.proposed_policy === null) {
      throw new PlannerError(409, `recommendation ${id} has no writable policy`);
    }
    const entry = this.record(
      tenant,
      e.detail.pn,
      e.detail.location,
      policyValues(e.detail.proposed_policy),
      e.detail.provenance_id ?? "unknown",
      e.detail.tier,
      "agent-spine",
      new Date().toISOString(),
    );
    e.row = { ...e.row, status: "approved" };
    e.detail = { ...e.detail, status: "approved" };
    return {
      recommendation_id: id,
      status: "approved",
      writeback: {
        tenant_id: entry.tenant_id,
        pn: entry.pn,
        location: entry.location,
        status: entry.status,
        old_values: entry.old_values,
        new_values: entry.new_values,
        written_at: entry.changed_at,
        error_message: null,
      },
      message: `written (${entry.status})`,
    };
  }
```

(The only change from today: capture `record()`'s return value as `entry`, and build `writeback` from it instead of passing `null`; `message` now mirrors the real BFF's `f"written ({status})"` format instead of a fixed string.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/planner-ui && npx vitest run src/api/client.test.ts`
Expected: PASS — all tests in this file green (confirms the change doesn't break any existing assertion; none of them check `writeback`/`message` on this path today).

- [ ] **Step 5: Commit**

```bash
cd apps/planner-ui && git add src/api/client.ts src/api/client.test.ts
git commit -m "planner-ui: FakePlannerClient.approve() populates a real writeback result"
```

---

### Task 2: `usePlanner` gains `deselect()` and a deep-link-safe part-context fallback

**Files:**
- Modify: `apps/planner-ui/src/hooks/usePlanner.ts`
- Test: `apps/planner-ui/src/hooks/usePlanner.test.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces: `PlannerState.deselect: () => void` — clears `selectedId`/`detail`/`history`/`partContext` without touching `rows`, `tab`, or `page`. `select(id)`'s part-context fetch now has a fallback path that later tasks (Task 5) rely on for deep-link correctness.

- [ ] **Step 1: Write the failing tests**

Add to `apps/planner-ui/src/hooks/usePlanner.test.ts`, as a new `describe` block after `"usePlanner guards"` (end of file):

```ts
describe("usePlanner selection", () => {
  it("deselect clears the detail/history/part-context without touching rows or tab", async () => {
    const client = new FakePlannerClient(SAMPLE_SEED.map((e) => ({ ...e })));
    const { result } = await ready(client);
    act(() => result.current.select("rec-hyd-yyz"));
    await waitFor(() => expect(result.current.detail).not.toBeNull());

    act(() => result.current.deselect());
    expect(result.current.selectedId).toBeNull();
    expect(result.current.detail).toBeNull();
    expect(result.current.history).toEqual([]);
    expect(result.current.partContext).toBeNull();
    expect(result.current.rows).toHaveLength(4); // untouched
    expect(result.current.tab).toBe("pending"); // untouched
  });

  it("a deep-link selection (row not on the loaded page) still loads part context from the resolved detail", async () => {
    const getPartContext = vi.fn(async (_t: string, pn: string, location: string) => ({
      pn,
      location,
      attributes: {
        description: "d",
        ata_chapter: null,
        part_class: null,
        shelf_life_days: null,
        hazardous_material: false,
        tool_control_item: false,
        criticality_tier: null,
      },
      stock: null,
      current_policy: null,
      proposed_policy: null,
      lead_time: null,
      open_orders: [],
      total_open_qty: 0,
      demand: null,
      unit_cost: null,
    }));
    const client = baseClient({
      getQueue: vi.fn(async () => ({ items: [], total: 0, limit: 50, offset: 0 })), // row isn't loaded
      getPartContext,
    });
    const { result } = await ready(client);
    expect(result.current.rows).toHaveLength(0);

    act(() => result.current.select("rec-a")); // detailFor("rec-a") resolves to pn "rec-a", location "YYZ"
    await waitFor(() => expect(result.current.partContext?.pn).toBe("rec-a"));
    expect(getPartContext).toHaveBeenCalledWith("acme", "rec-a", "YYZ");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/hooks/usePlanner.test.ts -t "usePlanner selection"`
Expected: FAIL — `result.current.deselect` is not a function (first test); the second test fails because `select()`'s current implementation only calls `getPartContext` when the row is found via `rows.find(...)`, which is empty here, so `partContext` stays `null` and the `waitFor` times out.

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/hooks/usePlanner.ts`, replace the `select` callback (current lines ~130-168) with:

```ts
  const select = useCallback(
    (id: string) => {
      setSelectedId(id);
      setHistory([]);
      setPartContext(null);
      const seq = ++selectSeq.current;

      const fetchPartContext = (pn: string, location: string) => {
        client
          .getPartContext(tenant, pn, location)
          .then((pc) => {
            if (seq === selectSeq.current) setPartContext(pc);
          })
          .catch((err) => {
            // Part context is supplementary — a failure here shouldn't clobber the
            // detail/history banner or block the rest of the selection flow.
            console.error("Failed to load part context", err);
          });
      };

      // Fast path: the row is already on the loaded page, so part context can load
      // in parallel with getDetail/getHistory below. Deep-links (or a row on a
      // different page) fall back to the pn/location on the resolved detail instead.
      const row = rows.find((r) => r.recommendation_id === id);
      if (row) fetchPartContext(row.pn, row.location);

      client
        .getDetail(tenant, id)
        .then((d) => {
          // Drop the response if a newer selection has since been made.
          if (seq !== selectSeq.current) return;
          setDetail(d);
          if (!row) fetchPartContext(d.pn, d.location);
          // Pull this part/location's writeback history alongside the detail.
          return client.getHistory(tenant, d.pn, d.location).then((h) => {
            if (seq === selectSeq.current) setHistory(h);
          });
        })
        .catch((err) => {
          if (seq === selectSeq.current) setBanner(messageFor(err));
        });
    },
    [client, tenant, rows],
  );

  const deselect = useCallback(() => {
    setSelectedId(null);
    setDetail(null);
    setHistory([]);
    setPartContext(null);
    selectSeq.current++; // invalidate any in-flight fetch tied to the old selection
  }, []);
```

Add `deselect: () => void;` to the `PlannerState` interface (right after `select: (id: string) => void;`), and add `deselect,` to the hook's returned object (right after `select,` in the final `return { ... }` statement).

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/hooks/usePlanner.test.ts`
Expected: PASS — all tests in the file green, including the pre-existing `"loads the part context when a row is selected"` test (unaffected: it uses the fast path).

- [ ] **Step 5: Commit**

```bash
cd apps/planner-ui && git add src/hooks/usePlanner.ts src/hooks/usePlanner.test.ts
git commit -m "planner-ui: usePlanner gains deselect() and a deep-link part-context fallback"
```

---

### Task 3: `usePlanner` surfaces bulk-approve per-item results

**Files:**
- Modify: `apps/planner-ui/src/hooks/usePlanner.ts`
- Test: `apps/planner-ui/src/hooks/usePlanner.test.ts`

**Interfaces:**
- Consumes: `ActionResult` (already imported as a type in `api/types.ts`; not yet imported into `usePlanner.ts`).
- Produces: `PlannerState.bulkResults: ActionResult[] | null` — populated only by `bulkApprove`'s completion, cleared at the start of every write (`runWrite`) and on tab switch (`setTab`).

- [ ] **Step 1: Write the failing tests**

Add to `apps/planner-ui/src/hooks/usePlanner.test.ts`, as a new `describe` block:

```ts
describe("usePlanner bulk results", () => {
  it("stores per-item results only from bulkApprove, cleared by the next write", async () => {
    const results: ActionResult[] = [
      { recommendation_id: "rec-a", status: "approved", writeback: null, message: "written (written)" },
      {
        recommendation_id: "rec-b",
        status: "approved",
        writeback: null,
        message: "written (deferred_open_order)",
      },
    ];
    const bulkApprove = vi.fn(async () => ({ approved_count: 2, results }));
    const client = baseClient({ bulkApprove });
    const { result } = await ready(client);

    act(() => result.current.bulkApprove({}));
    await waitFor(() => expect(result.current.bulkResults).toEqual(results));

    // A subsequent single approve clears the stale bulk results.
    act(() => result.current.approve("rec-a"));
    await waitFor(() => expect(result.current.busy).toBe(false));
    expect(result.current.bulkResults).toBeNull();
  });

  it("clears bulkResults on tab switch", async () => {
    const results: ActionResult[] = [
      { recommendation_id: "rec-a", status: "approved", writeback: null, message: "ok" },
    ];
    const client = baseClient({
      bulkApprove: vi.fn(async () => ({ approved_count: 1, results })),
    });
    const { result } = await ready(client);
    act(() => result.current.bulkApprove({}));
    await waitFor(() => expect(result.current.bulkResults).toEqual(results));

    act(() => result.current.setTab("decided"));
    expect(result.current.bulkResults).toBeNull();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/hooks/usePlanner.test.ts -t "usePlanner bulk results"`
Expected: FAIL — `result.current.bulkResults` is `undefined` (the field doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/hooks/usePlanner.ts`:

1. Add `ActionResult` to the type import at the top of the file (extend the existing `import type { ... } from "../api/types";` block to include `ActionResult`).

2. Add `bulkResults: ActionResult[] | null;` to the `PlannerState` interface, after `banner: string | null;`.

3. Add the state declaration, after `const [banner, setBanner] = useState<string | null>(null);`:

```ts
  const [bulkResults, setBulkResults] = useState<ActionResult[] | null>(null);
```

4. In `setTab`, add `setBulkResults(null);` alongside the existing `setBanner(null);`:

```ts
  const setTab = useCallback((next: PlannerTab) => {
    setTabState(next);
    setPage(0);
    setSelectedId(null);
    setDetail(null);
    setHistory([]);
    setPartContext(null);
    setBanner(null);
    setBulkResults(null);
    selectSeq.current++;
  }, []);
```

5. In `runWrite`, add `setBulkResults(null);` alongside the existing `setBanner(null);` (at the start, before the `try`):

```ts
  const runWrite = useCallback(
    async <T,>(fn: () => Promise<T>, onDone?: (result: T) => void) => {
      if (inFlight.current) return;
      inFlight.current = true;
      setBusy(true);
      setBanner(null);
      setBulkResults(null);
      try {
        ...
```

6. Replace `bulkApprove`'s `onDone` callback:

```ts
  const bulkApprove = useCallback(
    (filter: BulkApproveFilter) =>
      void runWrite(
        () => client.bulkApprove(tenant, filter),
        (res) => {
          const n = res.approved_count;
          setBanner(`Approved ${n} recommendation${n === 1 ? "" : "s"}.`);
          setBulkResults(res.results);
        },
      ),
    [runWrite, client, tenant],
  );
```

7. Add `bulkResults,` to the hook's final returned object, alongside `banner,`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/hooks/usePlanner.test.ts`
Expected: PASS — all tests green.

- [ ] **Step 5: Commit**

```bash
cd apps/planner-ui && git add src/hooks/usePlanner.ts src/hooks/usePlanner.test.ts
git commit -m "planner-ui: usePlanner surfaces bulk-approve per-item results"
```

---

### Task 4: New `Drawer` component (overlay, focus trap, Escape/backdrop/close-button)

A small, self-contained presentational component with no dependency on `usePlanner` or routing — testable entirely in isolation.

**Files:**
- Create: `apps/planner-ui/src/components/Drawer.tsx`
- Create: `apps/planner-ui/src/components/Drawer.module.css`
- Test: `apps/planner-ui/src/components/Drawer.test.tsx`

**Interfaces:**
- Consumes: nothing from this codebase (pure React + DOM APIs).
- Produces: `Drawer({ open: boolean; onClose: () => void; children: ReactNode })` — a component. Later tasks (Task 5) import and wrap `DetailPanel` with it. Renders nothing (`null`) when `open` is `false`; renders a `role="dialog"` panel with a close button when `open` is `true`.

- [ ] **Step 1: Write the failing tests**

Create `apps/planner-ui/src/components/Drawer.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { Drawer } from "./Drawer";

describe("Drawer", () => {
  it("renders nothing when closed", () => {
    render(
      <Drawer open={false} onClose={vi.fn()}>
        <p>content</p>
      </Drawer>,
    );
    expect(screen.queryByText("content")).not.toBeInTheDocument();
  });

  it("renders children in a dialog when open", () => {
    render(
      <Drawer open onClose={vi.fn()}>
        <p>content</p>
      </Drawer>,
    );
    expect(screen.getByText("content")).toBeInTheDocument();
    expect(screen.getByRole("dialog")).toBeInTheDocument();
  });

  it("closes on Escape", async () => {
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose}>
        <p>content</p>
      </Drawer>,
    );
    await userEvent.keyboard("{Escape}");
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes on backdrop click but not when clicking inside the panel", async () => {
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose}>
        <p>content</p>
      </Drawer>,
    );
    await userEvent.click(screen.getByText("content"));
    expect(onClose).not.toHaveBeenCalled();

    await userEvent.click(screen.getByRole("dialog").parentElement!); // the backdrop
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("closes via the explicit close button", async () => {
    const onClose = vi.fn();
    render(
      <Drawer open onClose={onClose}>
        <p>content</p>
      </Drawer>,
    );
    await userEvent.click(screen.getByRole("button", { name: /close/i }));
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it("traps Tab focus within the panel", async () => {
    render(
      <Drawer open onClose={vi.fn()}>
        <button>First</button>
        <button>Last</button>
      </Drawer>,
    );
    const closeBtn = screen.getByRole("button", { name: /close/i }); // first focusable (renders before children)
    screen.getByRole("button", { name: "Last" }).focus();
    await userEvent.tab();
    expect(closeBtn).toHaveFocus(); // wraps forward past Last back to Close
  });

  it("restores focus to the previously-focused element on close", async () => {
    function Harness() {
      const [open, setOpen] = useState(false);
      return (
        <div>
          <button onClick={() => setOpen(true)}>Open</button>
          <Drawer open={open} onClose={() => setOpen(false)}>
            <p>content</p>
          </Drawer>
        </div>
      );
    }
    render(<Harness />);
    const opener = screen.getByRole("button", { name: "Open" });
    opener.focus();
    await userEvent.click(opener);
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    await userEvent.keyboard("{Escape}");
    expect(opener).toHaveFocus();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/components/Drawer.test.tsx`
Expected: FAIL — `Cannot find module './Drawer'` (the component doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `apps/planner-ui/src/components/Drawer.tsx`:

```tsx
import { useEffect, useRef, type ReactNode } from "react";
import styles from "./Drawer.module.css";

interface Props {
  open: boolean;
  onClose: () => void;
  children: ReactNode;
}

const FOCUSABLE_SELECTOR =
  'a[href], button:not([disabled]), textarea:not([disabled]), input:not([disabled]), select:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusableElements(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
}

// Dependency-free overlay: traps Tab within the panel, Escape/backdrop/close-button
// all close it, and focus returns to whatever was focused before it opened. Mirrors
// the useFocusTrap pattern the sibling apps/web established for its dialogs.
export function Drawer({ open, onClose, children }: Props) {
  const panelRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const previouslyFocused = document.activeElement as HTMLElement | null;
    panelRef.current?.focus();

    function onKeyDown(e: KeyboardEvent) {
      if (e.key === "Escape") {
        onClose();
        return;
      }
      const panel = panelRef.current;
      if (e.key !== "Tab" || !panel) return;
      const focusable = focusableElements(panel);
      if (focusable.length === 0) return;
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }

    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("keydown", onKeyDown);
      previouslyFocused?.focus();
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div className={styles.backdrop} onClick={onClose}>
      <div
        className={styles.panel}
        role="dialog"
        aria-modal="true"
        aria-label="Recommendation detail"
        tabIndex={-1}
        ref={panelRef}
        onClick={(e) => e.stopPropagation()}
      >
        <button type="button" className={styles.close} aria-label="Close" onClick={onClose}>
          ×
        </button>
        {children}
      </div>
    </div>
  );
}
```

Create `apps/planner-ui/src/components/Drawer.module.css`:

```css
.backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.35);
  display: flex;
  justify-content: flex-end;
  z-index: 100;
}

.panel {
  width: min(420px, 100vw);
  height: 100%;
  background: var(--surface-2);
  box-shadow: -4px 0 16px rgba(0, 0, 0, 0.15);
  overflow-y: auto;
  position: relative;
  padding: 1.5rem;
  animation: slide-in 200ms ease;
}

@keyframes slide-in {
  from {
    transform: translateX(100%);
  }
  to {
    transform: translateX(0);
  }
}

.close {
  position: absolute;
  top: 12px;
  right: 12px;
  width: 28px;
  height: 28px;
  border: 0;
  background: transparent;
  font-size: 20px;
  line-height: 1;
  color: var(--text-secondary);
  cursor: pointer;
  border-radius: var(--radius);
}

.close:hover {
  background: var(--surface-1);
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/components/Drawer.test.tsx`
Expected: PASS — all 7 tests green.

- [ ] **Step 5: Commit**

```bash
cd apps/planner-ui && git add src/components/Drawer.tsx src/components/Drawer.module.css src/components/Drawer.test.tsx
git commit -m "planner-ui: add Drawer overlay component (focus trap, Escape/backdrop/close)"
```

---

### Task 5: Wire `Drawer` + URL routing (`/:tab/:id`) into `App.tsx`

**Files:**
- Modify: `apps/planner-ui/src/App.tsx`
- Modify: `apps/planner-ui/src/App.module.css` (reuses the existing `.loading` class — no new class needed)
- Test: `apps/planner-ui/src/App.test.tsx`

**Interfaces:**
- Consumes: `Drawer` (Task 4), `usePlanner().deselect` (Task 2).
- Produces: no new exports — this is the integration point. `QueueTable`'s `onSelect` prop now receives a navigate-driven handler instead of `p.select` directly (no change to `QueueTable.tsx` itself, since its prop type was already the generic `(id: string) => void`).

- [ ] **Step 1: Write the failing tests**

Add to `apps/planner-ui/src/App.test.tsx`, after the existing `"selecting a row reveals its provenance"` test:

```tsx
  it("selecting a row updates the URL with its id", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    const matches = await screen.findAllByText("HYD-PUMP-001");
    await userEvent.click(matches[0]);
    expect(await screen.findByText("Why this is queued")).toBeInTheDocument();
    await waitFor(() => expect(window.location.hash).toBe("#/pending/rec-hyd-yyz"));
  });

  it("re-clicking the selected row closes the drawer and drops the id from the URL", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    const matches = await screen.findAllByText("HYD-PUMP-001");
    await userEvent.click(matches[0]);
    await waitFor(() => expect(window.location.hash).toBe("#/pending/rec-hyd-yyz"));

    await userEvent.click(matches[0]);
    await waitFor(() => expect(window.location.hash).toBe("#/pending"));
    expect(screen.queryByText("Why this is queued")).not.toBeInTheDocument();
  });

  it("Escape closes the drawer and drops the id from the URL", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    const matches = await screen.findAllByText("HYD-PUMP-001");
    await userEvent.click(matches[0]);
    await waitFor(() => expect(window.location.hash).toBe("#/pending/rec-hyd-yyz"));

    await userEvent.keyboard("{Escape}");
    await waitFor(() => expect(window.location.hash).toBe("#/pending"));
  });

  it("deep-links directly to a selected recommendation from the URL", async () => {
    window.location.hash = "#/pending/rec-hyd-yyz";
    render(<App client={freshClient()} tenant="acme" />);
    expect(await screen.findByText("Why this is queued")).toBeInTheDocument();
  });

  it("switching tabs while a detail is open closes the drawer and clears the id from the URL", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    const matches = await screen.findAllByText("HYD-PUMP-001");
    await userEvent.click(matches[0]);
    await waitFor(() => expect(window.location.hash).toBe("#/pending/rec-hyd-yyz"));

    await userEvent.click(screen.getByRole("tab", { name: /decided/i }));
    await waitFor(() => expect(window.location.hash).toContain("/decided"));
    expect(window.location.hash).not.toContain("rec-hyd-yyz");
    expect(screen.queryByText("Why this is queued")).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/App.test.tsx -t "url"`
(Vitest's `-t` is case-insensitive substring match against test names; this won't match all 5 new tests by name alone, so instead run the whole file and read the output.)
Run: `cd apps/planner-ui && npx vitest run src/App.test.tsx`
Expected: the 5 new tests FAIL (`window.location.hash` never changes on row click today — `QueueTable`'s `onSelect` still calls `p.select` directly with no navigation); all pre-existing tests still PASS.

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/App.tsx`:

1. Add the import:

```tsx
import { Drawer } from "./components/Drawer";
```

2. Add the new route in the `App` component, before the existing `/:tab` route:

```tsx
export function App({ client, tenant }: Props) {
  return (
    <HashRouter future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>
      <Routes>
        <Route path="/dashboard" element={<DashboardView client={client} tenant={tenant} />} />
        <Route path="/reports" element={<ReportsView client={client} tenant={tenant} />} />
        <Route path="/:tab/:id" element={<PlannerView client={client} tenant={tenant} />} />
        <Route path="/:tab" element={<PlannerView client={client} tenant={tenant} />} />
        <Route path="*" element={<Navigate to="/pending" replace />} />
      </Routes>
    </HashRouter>
  );
}
```

3. In `PlannerView`, extend the `useParams()` destructure and add the id-sync effect (right after the existing tab-sync effect):

```tsx
function PlannerView({ client, tenant }: Props) {
  const p = usePlanner(client, tenant);
  const navigate = useNavigate();
  const { tab: tabParam, id: idParam } = useParams();
  const urlTab: PlannerTab = tabParam === "decided" ? "decided" : "pending";

  useEffect(() => {
    if (urlTab !== p.tab) p.setTab(urlTab);
  }, [urlTab, p.tab, p.setTab]);

  useEffect(() => {
    if (idParam) {
      if (idParam !== p.selectedId) p.select(idParam);
    } else if (p.selectedId) {
      p.deselect();
    }
  }, [idParam, p.selectedId, p.select, p.deselect]);

  const [filter, setFilter] = useState<QueueFilter>({});
  ...
```

4. Still in `PlannerView`, add two handlers right after `onBulkApprove`:

```tsx
  const onBulkApprove = () => p.bulkApprove({ tiers: filter.tiers, types: filter.types });
  const onSelectRow = (id: string) => navigate(id === p.selectedId ? `/${p.tab}` : `/${p.tab}/${id}`);
  const onCloseDrawer = () => navigate(`/${p.tab}`);
```

5. Change `QueueTable`'s `onSelect` prop and wrap `DetailPanel` in `Drawer`:

```tsx
              <QueueTable
                rows={view}
                selectedId={p.selectedId}
                onSelect={onSelectRow}
                onApprove={p.approve}
                disabled={paused}
                busy={p.busy}
                decided={decided}
                sort={sort}
                onSort={onSort}
              />
              {!decided && (
                <Pager page={p.page} limit={p.limit} total={p.total} onPrev={p.prevPage} onNext={p.nextPage} />
              )}
              <Drawer open={p.selectedId != null} onClose={onCloseDrawer}>
                {p.selectedId && !p.detail ? (
                  <p className={styles.loading} role="status">
                    Loading…
                  </p>
                ) : (
                  <DetailPanel
                    detail={p.detail}
                    history={p.history}
                    partContext={p.partContext}
                    onApprove={p.approve}
                    onReject={p.reject}
                    onDefer={p.defer}
                    onRollback={p.rollback}
                    approveDisabled={paused}
                    busy={p.busy}
                    decided={decided}
                  />
                )}
              </Drawer>
```

(Only `onSelect` and the `DetailPanel` block change; `QueueTable`'s other props and the `Pager` are unchanged.)

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/App.test.tsx`
Expected: PASS — all tests green, including every pre-existing test (e.g. `"selecting a row reveals its provenance"` still passes: `Drawer` renders its children into the DOM when open, so `DetailPanel`'s content is found exactly as before).

- [ ] **Step 5: Commit**

```bash
cd apps/planner-ui && git add src/App.tsx src/App.test.tsx
git commit -m "planner-ui: wire the Drawer + /:tab/:id URL routing into the detail selection flow"
```

---

### Task 6: Wire the bulk-results disclosure into `App.tsx`

**Files:**
- Modify: `apps/planner-ui/src/App.tsx`
- Modify: `apps/planner-ui/src/App.module.css`
- Test: `apps/planner-ui/src/App.test.tsx`

**Interfaces:**
- Consumes: `usePlanner().bulkResults` (Task 3), `ActionResult` type, `FakePlannerClient.approve()`'s populated `writeback` (Task 1).
- Produces: no new exports.

- [ ] **Step 1: Write the failing tests**

Add to `apps/planner-ui/src/App.test.tsx`, after the existing `"bulk-approving Tier A clears the matching approvable rows"` test:

```tsx
  it("bulk-approving a mixed-outcome batch shows an expandable per-item breakdown", async () => {
    const fake = freshClient();
    const realApprove = fake.approve.bind(fake);
    let calls = 0;
    fake.approve = async (tenant, id) => {
      const res = await realApprove(tenant, id);
      calls++;
      if (calls === 2 && res.writeback) {
        return {
          ...res,
          writeback: { ...res.writeback, status: "deferred_open_order" },
          message: "written (deferred_open_order)",
        };
      }
      return res;
    };

    render(<App client={fake} tenant="acme" />);
    await screen.findByText("acme · 4 pending");
    await userEvent.click(screen.getByLabelText("Tier A"));
    await userEvent.click(screen.getByRole("button", { name: /approve matching/i }));

    await waitFor(() => expect(screen.getByText("acme · 2 pending")).toBeInTheDocument());
    expect(screen.getByRole("alert")).toHaveTextContent(/approved 2 recommendations/i);
    const disclosure = screen.getByText(/see per-item results/i);
    await userEvent.click(disclosure);
    expect(screen.getByText(/written \(deferred_open_order\)/)).toBeInTheDocument();
  });

  it("bulk-approving a uniform batch does not show a per-item breakdown", async () => {
    render(<App client={freshClient()} tenant="acme" />);
    await screen.findByText("acme · 4 pending");
    await userEvent.click(screen.getByLabelText("Tier A"));
    await userEvent.click(screen.getByRole("button", { name: /approve matching/i }));
    await waitFor(() => expect(screen.getByText("acme · 2 pending")).toBeInTheDocument());
    expect(screen.queryByText(/see per-item results/i)).not.toBeInTheDocument();
  });
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/App.test.tsx`
Expected: the 2 new tests FAIL — no disclosure is rendered at all today (`p.bulkResults` isn't read anywhere in `App.tsx` yet).

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/App.tsx`:

1. Add the type import (new import line, since `App.tsx` doesn't currently import from `./api/types`):

```tsx
import type { ActionResult } from "./api/types";
```

2. Add a local helper function, next to the existing `downloadCsv` helper:

```tsx
function allSameOutcome(results: ActionResult[]): boolean {
  return results.every((r) => r.writeback?.status === results[0].writeback?.status);
}
```

3. Replace the banner rendering block:

```tsx
        {p.banner && (
          <div className={styles.banner} role="alert">
            {p.banner}
            {p.bulkResults && !allSameOutcome(p.bulkResults) && (
              <details className={styles.bulkDetails}>
                <summary>See per-item results ({p.bulkResults.length})</summary>
                <ul className={styles.bulkList}>
                  {p.bulkResults.map((r) => (
                    <li key={r.recommendation_id}>
                      {r.writeback ? `${r.writeback.pn} · ${r.writeback.location}` : r.recommendation_id}
                      {" — "}
                      {r.message}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}
```

4. Add to `apps/planner-ui/src/App.module.css`, after the existing `.banner` rule:

```css
.bulkDetails {
  margin-top: 8px;
}

.bulkDetails summary {
  cursor: pointer;
  font-weight: 500;
}

.bulkList {
  margin: 8px 0 0;
  padding-left: 18px;
  font-size: 12px;
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/App.test.tsx`
Expected: PASS — all tests green, including the pre-existing `"bulk-approving Tier A clears the matching approvable rows"` test (its own assertions only check the banner text, unaffected by the disclosure addition).

- [ ] **Step 5: Commit**

```bash
cd apps/planner-ui && git add src/App.tsx src/App.module.css src/App.test.tsx
git commit -m "planner-ui: surface bulk-approve per-item results as an expandable disclosure"
```

---

### Task 7: AAA/AA contrast test + color fixes + UAT.md

**Files:**
- Create: `apps/planner-ui/src/lib/contrast.ts`
- Create: `apps/planner-ui/src/styles/tokens.contrast.test.ts`
- Modify: `apps/planner-ui/src/styles/tokens.css`
- Modify: `apps/planner-ui/UAT.md`

**Interfaces:**
- Consumes: nothing from earlier tasks — fully independent.
- Produces: `hexToRgb(hex: string): [number, number, number]`, `relativeLuminance(hex: string): number`, `contrastRatio(fgHex: string, bgHex: string): number` — exported from `lib/contrast.ts`, used only by the new test.

- [ ] **Step 1: Write the failing test**

Create `apps/planner-ui/src/lib/contrast.ts` (the module the test imports — written now as a stub-free real implementation, since this task's TDD cycle is "write the test that exercises it, then implement it fully"; here the implementation is short enough to write in the same step as the test file that drives it):

```ts
// WCAG 2.x contrast-ratio math (https://www.w3.org/TR/WCAG21/#contrast-minimum).
// Pure, dependency-free — used only by the token-pair audit test below.

export function hexToRgb(hex: string): [number, number, number] {
  const h = hex.replace("#", "");
  const r = parseInt(h.slice(0, 2), 16);
  const g = parseInt(h.slice(2, 4), 16);
  const b = parseInt(h.slice(4, 6), 16);
  return [r, g, b];
}

function channelLuminance(c: number): number {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function relativeLuminance(hex: string): number {
  const [r, g, b] = hexToRgb(hex);
  return 0.2126 * channelLuminance(r) + 0.7152 * channelLuminance(g) + 0.0722 * channelLuminance(b);
}

export function contrastRatio(fgHex: string, bgHex: string): number {
  const l1 = relativeLuminance(fgHex);
  const l2 = relativeLuminance(bgHex);
  const lighter = Math.max(l1, l2);
  const darker = Math.min(l1, l2);
  return (lighter + 0.05) / (darker + 0.05);
}
```

Create `apps/planner-ui/src/styles/tokens.contrast.test.ts`:

```ts
import { readFileSync } from "node:fs";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";
import { contrastRatio, hexToRgb } from "../lib/contrast";

const TOKENS_PATH = fileURLToPath(new URL("./tokens.css", import.meta.url));
const TOKENS_CSS = readFileSync(TOKENS_PATH, "utf-8");

// Extracts `--name: value;` declarations from a single `{ ... }` block of raw CSS text.
// Intentionally narrow (matches this file's current flat, non-nested block structure) —
// see the plan/spec risk note if tokens.css's structure ever changes materially.
function parseDeclarations(block: string): Record<string, string> {
  const tokens: Record<string, string> = {};
  const re = /--([a-z0-9-]+):\s*([^;]+);/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(block)) !== null) {
    tokens[match[1]] = match[2].trim();
  }
  return tokens;
}

function rootBlock(css: string, afterIndex = 0): string {
  const rootAt = css.indexOf(":root", afterIndex);
  const openBrace = css.indexOf("{", rootAt);
  const closeBrace = css.indexOf("}", openBrace);
  return css.slice(openBrace + 1, closeBrace);
}

const LIGHT = parseDeclarations(rootBlock(TOKENS_CSS));
// The dark block is the second `:root { ... }`, nested inside the dark media query.
const darkMediaAt = TOKENS_CSS.indexOf("prefers-color-scheme: dark");
const DARK = { ...LIGHT, ...parseDeclarations(rootBlock(TOKENS_CSS, darkMediaAt)) };

const SURFACES = ["surface-0", "surface-1", "surface-2"];
// 7:1 (AAA) for primary/high-emphasis content; 4.5:1 (AA) for tokens that exist
// specifically to recede below primary (text-secondary, text-muted) — holding those
// to 7:1 would erase the visual hierarchy they're designed to create.
const AAA_TEXT_TOKENS = ["text-primary", "text-accent", "text-danger", "text-success"];
const AA_TEXT_TOKENS = ["text-secondary", "text-muted"];
const THEMED_PAIRS: [string, string][] = [
  ["text-accent", "bg-accent"],
  ["text-danger", "bg-danger"],
  ["text-success", "bg-success"],
  ["tier-a-fg", "tier-a-bg"],
  ["tier-b-fg", "tier-b-bg"],
  ["tier-c-fg", "tier-c-bg"],
];
const THEMED_THRESHOLD = 7.0; // every themed pair's fg token is in AAA_TEXT_TOKENS-equivalent territory

describe("contrast math sanity checks", () => {
  it("black on white is the maximum 21:1", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 1);
  });

  it("identical colors are 1:1", () => {
    expect(contrastRatio("#336699", "#336699")).toBeCloseTo(1, 5);
  });

  it("hexToRgb parses a hex triplet", () => {
    expect(hexToRgb("#ff0080")).toEqual([255, 0, 128]);
  });
});

describe.each([
  ["light", LIGHT],
  ["dark", DARK],
])("%s tokens.css contrast (AAA text / AA muted-secondary)", (_name, tokens) => {
  it.each(AAA_TEXT_TOKENS.flatMap((fg) => SURFACES.map((bg) => [fg, bg] as const)))(
    "%s on %s is >= 7:1",
    (fg, bg) => {
      expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(7.0);
    },
  );

  it.each(AA_TEXT_TOKENS.flatMap((fg) => SURFACES.map((bg) => [fg, bg] as const)))(
    "%s on %s is >= 4.5:1",
    (fg, bg) => {
      expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each(THEMED_PAIRS)("%s on %s is >= 7:1", (fg, bg) => {
    expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(THEMED_THRESHOLD);
  });
});
```

- [ ] **Step 2: Run test to verify it fails on the real (unfixed) tokens.css**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: the 3 sanity checks PASS; 11 of the 48 token-pair checks FAIL (the exact pairs listed in Step 3's table below) — confirming the test correctly detects today's real gaps before any fix is applied.

- [ ] **Step 3: Fix the failing token colors**

In `apps/planner-ui/src/styles/tokens.css`, in the light `:root` block (top of the file), replace:

```css
  --text-secondary: #5f5e5a;
  --text-muted: #888780;
  --text-accent: #185fa5;
  --bg-accent: #e6f1fb;
  --bg-danger: #fcebeb;
  --text-danger: #a32d2d;
  --bg-success: #e1f5ee;
  --text-success: #0f6e56;
```

with:

```css
  --text-secondary: #5f5e5a;
  --text-muted: #6e6d67;
  --text-accent: #14508a;
  --bg-accent: #e6f1fb;
  --bg-danger: #fcebeb;
  --text-danger: #932929;
  --bg-success: #e1f5ee;
  --text-success: #0c5844;
```

Still in the light `:root` block, in the "Autonomy-tier palette" comment section, replace:

```css
  --tier-a-fg: #854f0b;
```

with:

```css
  --tier-a-fg: #724409;
```

In the dark `:root` block (inside `@media (prefers-color-scheme: dark)`), replace:

```css
    --text-accent: #85b7eb;
    --bg-accent: #0c447c;
    --bg-danger: #501313;
    --text-danger: #f09595;
    --bg-success: #04342c;
    --text-success: #5dcaa5;
    --tier-a-bg: #412402;
    --tier-a-fg: #fac775;
    --tier-b-bg: #042c53;
    --tier-b-fg: #85b7eb;
    --tier-c-bg: #173404;
    --tier-c-fg: #97c459;
```

with:

```css
    --text-accent: #c7def6;
    --bg-accent: #0c447c;
    --bg-danger: #501313;
    --text-danger: #f4b0b0;
    --bg-success: #04342c;
    --text-success: #74d2b2;
    --tier-a-bg: #412402;
    --tier-a-fg: #fac775;
    --tier-b-bg: #042c53;
    --tier-b-fg: #9bc4ef;
    --tier-c-bg: #173404;
    --tier-c-fg: #a8cd73;
```

And, also in the dark block, replace:

```css
    --text-muted: #888780;
```

with:

```css
    --text-muted: #9c9b95;
```

(`--text-primary`, `--text-secondary`, `--bg-accent`, `--bg-danger`, `--bg-success`, `--tier-a-bg`, `--tier-a-fg` in dark mode, `--tier-b-bg`, `--tier-c-bg`, and all `--surface-*`/`--border*`/`--crit-*` values are untouched — they already clear their required threshold.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: PASS — all 51 tests green (3 sanity checks + 48 token-pair checks).

- [ ] **Step 5: Update UAT.md**

In `apps/planner-ui/UAT.md`, replace the K6 table row:

```
| K6 | Color-contrast spot check (light & dark mode) | Text meets WCAG AA contrast; dark mode via `prefers-color-scheme` | MANUAL (visual) |
```

with:

```
| K6 | Color-contrast (light & dark mode) | Primary/accent/danger/success/tier-badge text meets AAA (7:1); secondary/muted text meets AA (4.5:1) | tokens.contrast.test.ts (48-pair matrix); MANUAL spot-check for font-rendering/anti-aliasing only |
```

Replace the K-section traceability row:

```
| K Accessibility | 6 | 3 | K3 (full keyboard sweep), K5 (SR), K6 (contrast) |
```

with:

```
| K Accessibility | 6 | 4 | K3 (full keyboard sweep), K5 (SR) |
```

Remove the `K6` bullet from the "Manual-only items to consider automating later" list (it's automated now, so delete this line):

```
- K6 — automated color-contrast (axe-core) in light & dark mode.
```

- [ ] **Step 6: Commit**

```bash
cd apps/planner-ui && git add src/lib/contrast.ts src/styles/tokens.contrast.test.ts src/styles/tokens.css UAT.md
git commit -m "planner-ui: automated AAA/AA contrast audit + token color fixes"
```

---

## Final verification (after all 7 tasks)

- [ ] Run the full suite: `cd apps/planner-ui && npm test -- --run` — expect the pre-existing 111 tests plus every new test added across Tasks 1–7, all green.
- [ ] Typecheck: `cd apps/planner-ui && npx tsc -b` — expect zero errors.
- [ ] Live-verify via the preview MCP or `npm run dev` (or the running Docker deploy — rebuild `ui` first if using Docker): open a recommendation → drawer slides in from the right, URL updates to `#/pending/:id`; paste that URL fresh → same detail loads; Escape/backdrop-click/× all close it; bulk-approve Tier A → banner shows (no disclosure, since the fake's default outcomes are uniform); toggle light/dark mode and eyeball that the adjusted `text-danger`/`text-success`/`text-accent`/`text-muted`/tier-badge colors still read as their original hue, just slightly shifted.
- [ ] Update trackers: `ROADMAP.md`'s #7 section (check off "React follow-ups (remaining)", note the new test count), `TASKS.md` (new dated completion entry), `CLAUDE.md` if the `apps/planner-ui` test-count bullet needs bumping.
