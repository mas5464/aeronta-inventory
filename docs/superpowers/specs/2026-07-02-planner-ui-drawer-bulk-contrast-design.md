# #7 Planner UI — Detail Drawer, Bulk Results & AAA Contrast (React follow-ups) — Design

**Date:** 2026-07-02
**Status:** Approved (design)
**Sub-project:** #7 Planner UI "Trax IO Review" — closes the remaining "React follow-ups" bullet in [ROADMAP.md](../../../ROADMAP.md) (detail right-drawer, bulk per-result detail, detail-pane URL routes, richer color-contrast/AAA audit)
**Authoritative inputs:** the live `apps/planner-ui` source (`App.tsx`, `hooks/usePlanner.ts`, `components/DetailPanel.tsx`, `components/QueueTable.tsx`, `styles/tokens.css`), `apps/planner-ui/UAT.md`'s K6 row

## 1. Context

ROADMAP.md's #7 section has one remaining unchecked bullet: "React follow-ups (remaining): detail right-drawer (currently below the table); bulk per-result detail; detail-pane URL routes; richer color-contrast/AAA audit." Everything else under #7 (BFF, core loop, tabs, bulk-approve, history/rollback, WCAG AA pass, ops-console redesign, part context/dashboard) is done. This spec closes that bullet.

The four items split into 3 independent pieces of work: (1) detail drawer + URL routing — designed together since URL-syncing only makes sense once the drawer has a clear open/close lifecycle; (2) bulk-approve per-result detail; (3) an AAA/AA contrast audit-and-fix.

## 2. Scope

**In scope:**
- Piece 1: convert `DetailPanel` from an inline block to a right-side overlay drawer; sync its open/selected state into the URL (`#/:tab/:id`), extending the existing `HashRouter` tab pattern.
- Piece 2: surface `BulkApproveResult.results` (currently discarded) as an expandable per-item breakdown when outcomes aren't uniform.
- Piece 3: a deterministic, dependency-free contrast test against the real `styles/tokens.css` tokens, and the color fixes it requires.

**Deferred / non-goals:**
- No change to `usePlanner`'s core write logic (approve/reject/defer/rollback), busy/double-submit guards, or the `decided` tab's read-only behavior — piece 1 is a presentational change over the same data flow.
- No table page-jump/scroll-to-row when deep-linking to a detail on a page that isn't currently loaded (YAGNI — the drawer itself shows everything needed; jumping pages adds real complexity for marginal benefit).
- No mobile/responsive drawer variant (this app is desktop-first, consistent with the rest of the ops console).
- Piece 3 only touches the 24 semantic pairs enumerated in §5 (real text-on-background pairs, in both color schemes) — `--border`/`--border-strong` and the `--crit-*` dot-indicator colors are explicitly excluded (non-text UI elements; WCAG doesn't define an AAA-equivalent for non-text contrast, and the criticality dots are already backed by sr-only text per a prior a11y fix, so they aren't the sole conveyor of information).

## 3. Piece 1 — Detail drawer + URL routing

### 3.1 New component: `Drawer`
A small presentational wrapper (`components/Drawer.tsx` + `.module.css`): fixed-position backdrop + slide-in panel from the right, an explicit close (×) button, Escape-key close, backdrop-click close, and a focus trap + focus-restore-on-close (mirroring the `useFocusTrap` pattern `apps/web` already established for its dialogs — reimplemented locally since the two frontends don't share a library). It takes `open: boolean` + `onClose: () => void` and renders `children` inside the panel. `DetailPanel` is unaware of the drawer — it keeps rendering the same content it does today, just now as `Drawer`'s child, so `DetailPanel.test.tsx` needs minimal changes.

### 3.2 Routing
One more explicit route alongside the existing ones in `App.tsx`:
```
<Route path="/:tab/:id" element={<PlannerView client={client} tenant={tenant} />} />
```
placed before the existing `/:tab` route. `PlannerView` reads both via `useParams<{ tab: string; id?: string }>()`.

### 3.3 State sync (mirrors the existing tab-sync effect)
- **URL → state**: an effect calls `p.select(id)` when the URL's `:id` differs from `p.selectedId`, and a new `p.deselect()` when the URL has no `:id` but something is still selected (covers back/forward navigation and direct URL edits).
- **State → URL**: `QueueTable`'s row click handler moves from calling `p.select(id)` directly to calling `navigate(...)`: to `/:tab/:id` normally, or back to `/:tab` if the clicked row is already selected (satisfies "re-click toggles closed"). `Drawer`'s `onClose` (Escape/backdrop/×) also navigates to `/:tab`.
- `usePlanner` gains `deselect()`: clears `selectedId`/`detail`/`history`/`partContext` (the same fields `setTab` already clears), without touching `rows`/`tab`.

### 3.4 Deep-link correctness
`select(id)`'s part-context fetch currently requires the row to already be in the loaded page (`rows.find(...)`); it's a silent no-op otherwise. Since `RecommendationDetail` already carries `pn`/`location`, the fast in-page lookup stays as the primary path (zero behavior change for a normal click), with a fallback to the resolved `detail` response's `pn`/`location` when the row isn't in `rows` — so `#/pending/:id` works from a cold load or for a row on a different page.

### 3.5 Loading state
The drawer only mounts when `selectedId` is set (no more persistent "Select a recommendation…" placeholder). Between `select(id)` firing and `getDetail` resolving, the open drawer shows a brief "Loading…" in place of the content.

## 4. Piece 2 — Bulk-approve per-result detail

- `usePlanner` gains `bulkResults: ActionResult[] | null`, set only inside `bulkApprove`'s `onDone` callback, and cleared everywhere `banner` already is (start of `runWrite`, tab switch) so it never bleeds into an unrelated banner.
- Uniformity check: `results.every(r => r.writeback?.status === results[0].writeback?.status)`. `writeback` is always non-null here because `bulk_approve`'s targets are pre-filtered server-side to recs with a writable policy.
- When uniform (the common case), rendering is byte-for-byte identical to today — just the banner line.
- When mixed, a native `<details>` under the banner: `<summary>See per-item results (N)</summary>`, then one line per item — `{writeback.pn} · {writeback.location} — {message}` (`message` already reads like `"written (written)"` / `"written (deferred_open_order)"`). Native `<details>`/`<summary>` needs no custom ARIA/state management.

## 5. Piece 3 — AAA/AA contrast audit + fix

### 5.1 `src/lib/contrast.ts` (new, pure, no dependency)
`hexToRgb`, `relativeLuminance` (WCAG formula), `contrastRatio(fg, bg)`.

### 5.2 `src/styles/tokens.contrast.test.ts` (new)
Reads `styles/tokens.css` as text at test time and regex-extracts the `:root {…}` block and the `@media (prefers-color-scheme: dark) { :root {…} }` block into two `Record<string,string>` token maps — so the test can never silently drift from the actual source of truth. Enumerates, per color scheme:
- 6 text tokens (`text-primary`, `text-secondary`, `text-muted`, `text-accent`, `text-danger`, `text-success`) × 3 surfaces (`surface-0/1/2`) = 18 pairs
- 6 "themed chip" pairs: `text-accent`/`bg-accent`, `text-danger`/`bg-danger`, `text-success`/`bg-success`, `tier-{a,b,c}-fg`/`tier-{a,b,c}-bg`

24 pairs × 2 schemes = 48 assertions, each against a tiered threshold: **7:1 (AAA)** for `text-primary`/`text-accent`/`text-danger`/`text-success`/`tier-*-fg` (primary/high-emphasis content), **4.5:1 (AA)** for `text-secondary`/`text-muted` (tokens that exist specifically to recede below primary — holding them to 7:1 would erase the visual hierarchy they're designed to create).

### 5.3 Token fixes (hue-preserving lightness adjustments; verified 48/48 passing)
| token | light | dark |
|---|---|---|
| `text-danger` | `#a32d2d → #932929` | `#f09595 → #f4b0b0` |
| `text-success` | `#0f6e56 → #0c5844` | `#5dcaa5 → #74d2b2` |
| `text-accent` | `#185fa5 → #14508a` | `#85b7eb → #c7def6` |
| `text-muted` | `#888780 → #6e6d67` | `#888780 → #9c9b95` |
| `tier-a-fg` | `#854f0b → #724409` | *(already passes)* |
| `tier-b-fg` | *(already passes)* | `#85b7eb → #9bc4ef` |
| `tier-c-fg` | *(already passes)* | `#97c459 → #a8cd73` |

### 5.4 UAT.md
`K6` updates to reflect the new automated coverage (the ratio math is now locked in by `tokens.contrast.test.ts`); keep a lighter manual spot-check for genuine font-rendering/anti-aliasing edge cases the math can't see.

## 6. Testing strategy
- `Drawer`: open/close via prop, Escape, backdrop click, ×, focus trap + restore-on-close — new `Drawer.test.tsx`.
- `App.tsx` / routing: `:id` sync both directions (select → URL, URL → select), deep-link with a row not on the current page (mocked `getDetail` returning `pn`/`location` used for part-context), tab switch clears `:id`.
- `usePlanner`: `deselect()`, `bulkResults` populated only on `bulkApprove`, cleared on the next write/tab switch.
- `tokens.contrast.test.ts`: the 48-pair matrix itself is the regression test — no separate unit tests needed for `contrast.ts` beyond a couple of known-value sanity checks (e.g., black-on-white = 21:1).
- Existing `DetailPanel.test.tsx`/`QueueTable.test.tsx` suites should need minimal changes (content contract unchanged); update fixtures only where props actually change.

## 7. Build & verify
`cd apps/planner-ui && npm test` (Vitest, expect the current 111 + new drawer/routing/bulk-result/contrast tests) and `npx tsc -b`. Live-verify via the preview MCP against the running Docker deploy (or `npm run dev`): open a recommendation → drawer slides in from the right, URL updates to `#/pending/:id`; copy that URL into a fresh tab → same detail loads; Escape/backdrop/× all close it; bulk-approve a mixed batch → disclosure appears; toggle light/dark and eyeball the adjusted colors still read as their original hue.

## 8. Risks & mitigations
- **Deep-link part-context fallback adds one round-trip on the cold-load path** (part-context now waits on `getDetail` resolving first when the row isn't pre-loaded) — acceptable since it's the already-documented "supplementary, failure-tolerant" panel, and the common in-page-click path is unaffected.
- **Color changes are visually subtle but real** — every adjusted token keeps its hue, moved only in lightness; still worth an eyeball pass in the browser before considering this done, not just trusting the math.
- **`tokens.css`'s regex-based block extraction** is intentionally narrow (matches this file's actual current format) rather than a general CSS parser — if the file's structure changes materially (e.g., token blocks split across multiple files), the test's parser needs a matching update; a reasonable, documented coupling given the token file is small, stable, and singular.
