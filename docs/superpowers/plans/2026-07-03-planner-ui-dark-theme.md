# Planner UI Dark Theme & Accent Discipline (Phase 1) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make dark mode a user-togglable, near-black, purpose-built theme (not an OS-driven inversion of light), and introduce one bold "primary action" color reserved exclusively for the Approve button, in both color schemes.

**Architecture:** Split into 4 tasks: (1) a pure mechanism refactor — swap the dark variant from a `prefers-color-scheme` media query to a `[data-theme="dark"]` attribute selector with **identical values**, so nothing visually changes yet; (2) a theme hook + `localStorage` persistence + a toggle button in `NavRail`, now meaningful because the mechanism is JS-controllable; (3) the actual new color values (near-black surfaces, teal Tier-B, a new bold `action-primary` pair) for both schemes, verified against the existing tiered AAA/AA contrast test; (4) apply `action-primary` to the two places Approve renders today.

**Tech Stack:** React 18, TypeScript 5, Vite 5, Vitest 2 + Testing Library, CSS Modules, `lucide-react` icons.

## Global Constraints

- No new npm dependencies.
- Every task ends green on `npm test -- --run` and `npx tsc -b`.
- `tokens.contrast.test.ts`'s 48-pair-style matrix structure and its tiered thresholds (7:1 AAA for `text-primary`/`text-accent`/`text-danger`/`text-success`/`tier-*-fg`, 4.5:1 AA for `text-secondary`/`text-muted`) stay in force for every task — any new or changed token pair must clear its tier.
- No component's markup/structure changes except `NavRail.tsx` (new toggle button) and the two Approve-button call sites (`QueueTable.tsx`, `DetailPanel.module.css`) — this phase is tokens + one small control, not a component rebuild.

## Deviations from the approved spec (flagging for confirmation, not silently applying)

Investigating the actual current CSS surfaced two places where this plan departs from what the spec said, both because the underlying investigation changed the picture:

1. **Spec said "remap every other current blue usage" off `text-accent`/`bg-accent`.** This plan does **not** do that. `text-accent`/`bg-accent` are used non-CTA in ~10 places (`NavRail` active-state, `Tabs` active underline, `ChartRow`/`DashboardView` chart fills, `SummaryCards`, `DemandTrend`, `QueueTable` badges, the global `App.module.css` banner, focus rings). Recoloring all of them was the spec's literal words, but the actual design goal — "Approve is the one bold, unmistakable action" — is achieved more surgically by introducing a **new**, separate `action-primary` token pair used *only* by Approve, and leaving `text-accent`/`bg-accent` as the existing, calmer "info/highlight" role it already plays elsewhere. The reference site itself does this too (its own icon accents stay blue-ish; only its single CTA gets the bold reserved color) — restraint comes from *one thing being bold*, not from *removing every other use of a hue*. The one exception this plan does still remap is Tier B, because a filled badge/pill is visually closer to a button than an icon stroke or an underline is, so it's the one non-CTA usage that could plausibly be mistaken for "this is clickable."
2. **Spec proposed moving focus rings off the (assumed) Approve-reserved accent onto a neutral outline.** That proposal was written before it was clear whether `text-accent` itself would become the reserved color. Since deviation #1 means `text-accent` stays a separate, non-reserved token, the original concern (a focused-but-inert element reading as "click to approve") doesn't apply — focus rings stay on `text-accent`, unchanged, in this plan.

Both are judgment calls made during planning, not implementation improvisation — flagging per usual practice so they can be confirmed or overridden before work starts, not discovered after.

---

### Task 1: Dark variant becomes a `[data-theme="dark"]` attribute selector (mechanism only, values unchanged)

**Files:**
- Modify: `apps/planner-ui/src/styles/tokens.css`
- Modify: `apps/planner-ui/src/styles/tokens.contrast.test.ts`

**Interfaces:**
- Consumes: nothing new.
- Produces: `[data-theme="dark"]` as the selector later tasks' theme hook (Task 2) will set via `document.documentElement.dataset.theme`.

- [ ] **Step 1: Write the failing test**

The existing test file already parses "the dark block" by searching for the text `prefers-color-scheme: dark`. Change it to search for the new selector instead, so it fails until Step 3 lands:

In `apps/planner-ui/src/styles/tokens.contrast.test.ts`, replace:

```ts
const LIGHT = parseDeclarations(rootBlock(TOKENS_CSS));
// The dark block is the second `:root { ... }`, nested inside the dark media query.
const darkMediaAt = TOKENS_CSS.indexOf("prefers-color-scheme: dark");
const DARK = { ...LIGHT, ...parseDeclarations(rootBlock(TOKENS_CSS, darkMediaAt)) };
```

with:

```ts
const LIGHT = parseDeclarations(rootBlock(TOKENS_CSS));
// The dark block is a `:root[data-theme="dark"] { ... }` selector (not a media query —
// this makes the theme JS-toggleable via document.documentElement.dataset.theme).
function darkBlock(css: string): string {
  const at = css.indexOf('[data-theme="dark"]');
  const openBrace = css.indexOf("{", at);
  const closeBrace = css.indexOf("}", openBrace);
  return css.slice(openBrace + 1, closeBrace);
}
const DARK = { ...LIGHT, ...parseDeclarations(darkBlock(TOKENS_CSS)) };
```

(`rootBlock`'s helper assumed a `:root` selector immediately before the brace; the new dark selector is `:root[data-theme="dark"]`, not nested `:root` inside a media query, so this needs its own small helper rather than reusing `rootBlock` with an offset.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: FAIL — `darkBlock` finds nothing (returns an empty string, `parseDeclarations` returns `{}`), so `DARK` collapses to just the light values, and every dark-scheme assertion either passes vacuously against light values or the whole file errors on an empty slice. Either way, the failure confirms the parser is looking for a selector that doesn't exist yet.

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/styles/tokens.css`, replace:

```css
@media (prefers-color-scheme: dark) {
  :root {
    --surface-0: #1f1e1c;
    --surface-1: #2a2926;
    --surface-2: #333230;
    --border: rgba(255, 255, 255, 0.12);
    --border-strong: rgba(255, 255, 255, 0.22);
    --text-primary: #f3f1ea;
    --text-secondary: #b4b2a9;
    --text-muted: #9c9b95;
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
    --crit-1: #f09595;
    --crit-2: #f0997b;
    --crit-3: #ef9f27;
    --crit-4: #97c459;
    --crit-5: #5dcaa5;
  }
}
```

with (same values, new selector — Task 3 changes the values, this step only changes the mechanism):

```css
:root[data-theme="dark"] {
  --surface-0: #1f1e1c;
  --surface-1: #2a2926;
  --surface-2: #333230;
  --border: rgba(255, 255, 255, 0.12);
  --border-strong: rgba(255, 255, 255, 0.22);
  --text-primary: #f3f1ea;
  --text-secondary: #b4b2a9;
  --text-muted: #9c9b95;
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
  --crit-1: #f09595;
  --crit-2: #f0997b;
  --crit-3: #ef9f27;
  --crit-4: #97c459;
  --crit-5: #5dcaa5;
}
```

Note this means dark mode is **no longer applied automatically by OS preference** as of this step — it now only applies when something sets `data-theme="dark"` on `<html>`. That "something" is Task 2. Between Task 1 and Task 2 landing, the app will render light-only; this is expected and momentary (both tasks land in the same session).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: PASS — all 51 tests green, identical results to before (values didn't change, only the selector the test looks for).

- [ ] **Step 5: Commit**

```bash
cd apps/planner-ui && git add src/styles/tokens.css src/styles/tokens.contrast.test.ts
git commit -m "planner-ui: dark theme becomes a [data-theme] attribute selector (values unchanged)"
```

---

### Task 2: Theme hook + `localStorage` persistence + `NavRail` toggle

**Files:**
- Create: `apps/planner-ui/src/hooks/useTheme.ts`
- Create: `apps/planner-ui/src/hooks/useTheme.test.ts`
- Modify: `apps/planner-ui/src/components/NavRail.tsx`
- Modify: `apps/planner-ui/src/components/NavRail.module.css`
- Modify: `apps/planner-ui/src/components/NavRail.test.tsx`
- Modify: `apps/planner-ui/src/App.tsx` (mount the hook once at the top level so it applies before first paint)

**Interfaces:**
- Produces: `useTheme(): { theme: "light" | "dark"; toggleTheme: () => void }` from `hooks/useTheme.ts`. `NavRail` gains an optional `theme`/`onToggleTheme` prop pair (kept as props, not an internal hook call, so `NavRail.test.tsx`'s existing `renderNav()` helper doesn't need to change its rendering setup).

- [ ] **Step 1: Write the failing tests**

Create `apps/planner-ui/src/hooks/useTheme.test.ts`:

```ts
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useTheme } from "./useTheme";

const STORAGE_KEY = "trax-io-theme";

describe("useTheme", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  it("defaults to dark when nothing is stored", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("reads a previously-stored preference on mount", () => {
    localStorage.setItem(STORAGE_KEY, "light");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("toggleTheme flips the theme, applies the attribute, and persists it", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");

    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("light");

    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(localStorage.getItem(STORAGE_KEY)).toBe("dark");
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/planner-ui && npx vitest run src/hooks/useTheme.test.ts`
Expected: FAIL — `Cannot find module './useTheme'` (the hook doesn't exist yet).

- [ ] **Step 3: Write minimal implementation**

Create `apps/planner-ui/src/hooks/useTheme.ts`:

```ts
import { useCallback, useState } from "react";

export type Theme = "light" | "dark";
const STORAGE_KEY = "trax-io-theme";

function applyTheme(theme: Theme) {
  document.documentElement.dataset.theme = theme;
}

function readInitialTheme(): Theme {
  const stored = localStorage.getItem(STORAGE_KEY);
  return stored === "light" ? "light" : "dark"; // dark-first: any other/missing value defaults to dark
}

// Dark-first, user-toggleable theme. No prefers-color-scheme fallback — dark is the
// deliberate default for a first-time visitor, not inferred from OS settings.
export function useTheme(): { theme: Theme; toggleTheme: () => void } {
  const [theme, setTheme] = useState<Theme>(() => {
    const initial = readInitialTheme();
    applyTheme(initial);
    return initial;
  });

  const toggleTheme = useCallback(() => {
    setTheme((current) => {
      const next: Theme = current === "dark" ? "light" : "dark";
      applyTheme(next);
      localStorage.setItem(STORAGE_KEY, next);
      return next;
    });
  }, []);

  return { theme, toggleTheme };
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/planner-ui && npx vitest run src/hooks/useTheme.test.ts`
Expected: PASS — all 3 tests green.

- [ ] **Step 5: Wire the hook into `NavRail` and `App.tsx`**

In `apps/planner-ui/src/App.tsx`, add the import and call the hook once at the top of the `App` component (before the `return`), then pass its values down to wherever `NavRail` is rendered (inside `PlannerView`, `DashboardView`, and `ReportsView` — the three views currently rendering `<NavRail .../>`). Add the import:

```tsx
import { useTheme } from "./hooks/useTheme";
```

In `App`, call the hook and pass `theme`/`onToggleTheme` down as extra props on `Props` threaded to each view, OR — simpler, since `NavRail` is the only consumer — call `useTheme()` **inside `NavRail` itself** instead of threading it through three separate view components. Do this instead of touching `App.tsx`:

In `apps/planner-ui/src/components/NavRail.tsx`, replace the full file with:

```tsx
import { ClipboardCheck, FileText, History, LayoutDashboard, Moon, Settings, Sun } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTheme } from "../hooks/useTheme";
import styles from "./NavRail.module.css";

export type NavSection = "review" | "dashboard" | "reports";

// App-shell navigation. "Review", "Dashboard", and "Reports" are the live sections;
// the others are placeholders for future sections, shown disabled so the shell reads
// as a system.
const ITEMS: { id: NavSection | "writebacks" | "settings"; label: string; icon: typeof ClipboardCheck; live?: boolean; href?: string }[] = [
  { id: "review", label: "Review", icon: ClipboardCheck, live: true, href: "#/pending" },
  { id: "dashboard", label: "Dashboard", icon: LayoutDashboard, live: true, href: "#/dashboard" },
  { id: "reports", label: "Reports", icon: FileText, live: true, href: "#/reports" },
  { id: "writebacks", label: "Writebacks", icon: History },
  { id: "settings", label: "Settings", icon: Settings },
];

interface Props {
  active?: NavSection;
}

export function NavRail({ active = "review" }: Props) {
  const navigate = useNavigate();
  const { theme, toggleTheme } = useTheme();
  return (
    <nav className={styles.rail} aria-label="Sections">
      <div className={styles.brand} aria-hidden="true">
        T
      </div>
      {ITEMS.map(({ id, label, icon: Icon, live, href }) => {
        const current = live && id === active;
        return (
          <button
            key={id}
            type="button"
            className={`${styles.item} ${current ? styles.active : ""}`}
            aria-current={current ? "page" : undefined}
            disabled={!live}
            title={live ? label : `${label} — coming soon`}
            onClick={live && href ? () => navigate(href.replace(/^#/, "")) : undefined}
          >
            <Icon size={20} aria-hidden="true" />
            <span className={styles.label}>{label}</span>
          </button>
        );
      })}
      <button
        type="button"
        className={styles.themeToggle}
        onClick={toggleTheme}
        title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
        aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
      >
        {theme === "dark" ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
      </button>
    </nav>
  );
}
```

(Calling `useTheme()` from every `NavRail` instance is safe even though multiple views each render their own `NavRail` — React state is per-component-instance, but `document.documentElement.dataset.theme` and `localStorage` are global, so every instance reads the same persisted value on mount and toggling from any one of them updates the single shared DOM attribute correctly. Only one `NavRail` is ever mounted at a time in this app, since it's rendered per-route, so this is a non-issue in practice — noted for completeness.)

In `apps/planner-ui/src/components/NavRail.module.css`, add (after the existing `.label` rule):

```css
.themeToggle {
  display: flex;
  align-items: center;
  justify-content: center;
  width: 36px;
  height: 36px;
  margin-top: auto;
  margin-bottom: 4px;
  border: none;
  background: none;
  border-radius: 8px;
  color: var(--text-muted);
  cursor: pointer;
}

.themeToggle:hover {
  background: var(--surface-1);
  color: var(--text-secondary);
}
```

`margin-top: auto` on a `flex-direction: column` container pushes this button to the bottom of the rail — matches the design's placement (below the nav items, separate from Settings).

- [ ] **Step 6: Update the existing `NavRail.test.tsx`**

The existing tests render `NavRail` inside a bare `<MemoryRouter>`, with no theme-related setup. Since `useTheme()` now runs inside `NavRail`, its side effects (`localStorage`, `document.documentElement.dataset.theme`) will leak between tests unless cleaned up. Add a `beforeEach`/`afterEach` to `apps/planner-ui/src/components/NavRail.test.tsx`:

Replace:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import { NavRail } from "./NavRail";

function renderNav(active?: "review" | "dashboard") {
  return render(
    <MemoryRouter>
      <NavRail active={active} />
    </MemoryRouter>,
  );
}

describe("NavRail", () => {
```

with:

```tsx
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NavRail } from "./NavRail";

function renderNav(active?: "review" | "dashboard") {
  return render(
    <MemoryRouter>
      <NavRail active={active} />
    </MemoryRouter>,
  );
}

describe("NavRail", () => {
  beforeEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

  afterEach(() => {
    localStorage.clear();
    document.documentElement.removeAttribute("data-theme");
  });

```

(Only the imports and the new `beforeEach`/`afterEach` block are added; the two existing `it(...)` tests are unchanged.) Add one more test to the same file, at the end, before the closing `});`:

```tsx

  it("renders a theme toggle button that flips data-theme on click", async () => {
    const { default: userEvent } = await import("@testing-library/user-event");
    renderNav();
    const toggle = screen.getByRole("button", { name: /switch to light mode/i });
    expect(document.documentElement.dataset.theme).toBe("dark");
    await userEvent.default.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("light");
  });
```

Also add `import userEvent from "@testing-library/user-event";` to the top imports and simplify the new test to use it directly rather than a dynamic import (dynamic import above is unnecessary — use the static import instead):

```tsx
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { NavRail } from "./NavRail";
```

and the test body becomes:

```tsx
  it("renders a theme toggle button that flips data-theme on click", async () => {
    renderNav();
    const toggle = screen.getByRole("button", { name: /switch to light mode/i });
    expect(document.documentElement.dataset.theme).toBe("dark");
    await userEvent.click(toggle);
    expect(document.documentElement.dataset.theme).toBe("light");
  });
```

- [ ] **Step 7: Run tests to verify everything passes**

Run: `cd apps/planner-ui && npx vitest run src/hooks/useTheme.test.ts src/components/NavRail.test.tsx`
Expected: PASS — 3 (useTheme) + 3 (NavRail, was 2) = 6 tests green.

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — full suite green (was 184; +6 from this task: 3 useTheme + 1 new NavRail test — wait, that's +4, not +6; the exact new count is 184 + 3 (useTheme) + 1 (NavRail) = 188). `npx tsc -b` clean.

- [ ] **Step 8: Commit**

```bash
cd apps/planner-ui && git add src/hooks/useTheme.ts src/hooks/useTheme.test.ts src/components/NavRail.tsx src/components/NavRail.module.css src/components/NavRail.test.tsx
git commit -m "planner-ui: dark-first theme toggle (useTheme hook + NavRail control)"
```

---

### Task 3: New near-black dark palette, retuned light, Tier-B teal, new `action-primary` tokens

All values below were computed and verified with the same WCAG relative-luminance math `contrast.ts` already implements — every pair listed clears its tier with real margin (not a bare-minimum pass). Most existing dark values need **no change at all**: near-black surfaces are darker than the old dark surfaces, so every already-light foreground color gets *more* contrast, not less.

**Files:**
- Modify: `apps/planner-ui/src/styles/tokens.css`
- Modify: `apps/planner-ui/src/styles/tokens.contrast.test.ts`

**Interfaces:**
- Produces: `--action-primary-bg`, `--action-primary-fg` (new tokens, both schemes) — consumed by Task 4.

- [ ] **Step 1: Write the failing tests**

In `apps/planner-ui/src/styles/tokens.contrast.test.ts`, add `"action-primary-fg"` to the AAA tier list and add `action-primary` as a new themed pair (it's a filled button, same "bold/reserved" tier as the other AAA text):

Replace:

```ts
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
```

with:

```ts
const AAA_TEXT_TOKENS = ["text-primary", "text-accent", "text-danger", "text-success"];
const AA_TEXT_TOKENS = ["text-secondary", "text-muted"];
const THEMED_PAIRS: [string, string][] = [
  ["text-accent", "bg-accent"],
  ["text-danger", "bg-danger"],
  ["text-success", "bg-success"],
  ["tier-a-fg", "tier-a-bg"],
  ["tier-b-fg", "tier-b-bg"],
  ["tier-c-fg", "tier-c-bg"],
  ["action-primary-fg", "action-primary-bg"],
];
```

(`action-primary-fg` is always white in both schemes, so it doesn't need its own entry in `AAA_TEXT_TOKENS` × `SURFACES` — it's never rendered directly on a surface, only on its own `action-primary-bg` fill, which the `THEMED_PAIRS` addition above already covers.)

- [ ] **Step 2: Run test to verify it fails**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: FAIL — `action-primary-fg`/`action-primary-bg` don't exist in `tokens.css` yet, so `contrastRatio(undefined, undefined)` throws or the parsed values are `undefined`, failing the new `it.each` case for both light and dark.

- [ ] **Step 3: Write minimal implementation**

In `apps/planner-ui/src/styles/tokens.css`, replace the light `:root` block:

```css
:root {
  --surface-0: #faf9f5;
  --surface-1: #f3f1ea;
  --surface-2: #ffffff;
  --border: rgba(0, 0, 0, 0.1);
  --border-strong: rgba(0, 0, 0, 0.2);
  --text-primary: #1a1a18;
  --text-secondary: #5f5e5a;
  --text-muted: #6e6d67;
  --text-accent: #14508a;
  --bg-accent: #e6f1fb;
  --bg-danger: #fcebeb;
  --text-danger: #932929;
  --bg-success: #e1f5ee;
  --text-success: #0c5844;
  --radius: 8px;

  /* Autonomy-tier palette: A = amber (needs review), B = blue, C = green. */
  --tier-a-bg: #faeeda;
  --tier-a-fg: #724409;
  --tier-b-bg: #e6f1fb;
  --tier-b-fg: #0c447c;
  --tier-c-bg: #eaf3de;
  --tier-c-fg: #27500a;

  /* Criticality dot ramp: 1 (red) .. 5 (green). */
  --crit-1: #e24b4a;
  --crit-2: #d85a30;
  --crit-3: #ba7517;
  --crit-4: #639922;
  --crit-5: #1d9e75;

  --font-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
```

with (Tier B moves off blue to teal; `action-primary` is new; everything else unchanged):

```css
:root {
  --surface-0: #faf9f5;
  --surface-1: #f3f1ea;
  --surface-2: #ffffff;
  --border: rgba(0, 0, 0, 0.1);
  --border-strong: rgba(0, 0, 0, 0.2);
  --text-primary: #1a1a18;
  --text-secondary: #5f5e5a;
  --text-muted: #6e6d67;
  --text-accent: #14508a;
  --bg-accent: #e6f1fb;
  --bg-danger: #fcebeb;
  --text-danger: #932929;
  --bg-success: #e1f5ee;
  --text-success: #0c5844;
  --radius: 8px;

  /* Reserved exclusively for the Approve button (row + drawer) — no other element uses this. */
  --action-primary-bg: #094fc2;
  --action-primary-fg: #ffffff;

  /* Autonomy-tier palette: A = amber (needs review), B = teal, C = green. B moved off
     blue so a Tier-B badge (a filled pill) is never visually confused with the
     action-primary button above. */
  --tier-a-bg: #faeeda;
  --tier-a-fg: #724409;
  --tier-b-bg: #dcf7f2;
  --tier-b-fg: #095851;
  --tier-c-bg: #eaf3de;
  --tier-c-fg: #27500a;

  /* Criticality dot ramp: 1 (red) .. 5 (green). */
  --crit-1: #e24b4a;
  --crit-2: #d85a30;
  --crit-3: #ba7517;
  --crit-4: #639922;
  --crit-5: #1d9e75;

  --font-sans: ui-sans-serif, system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
}
```

Then replace the `:root[data-theme="dark"]` block (from Task 1) with the near-black palette:

```css
:root[data-theme="dark"] {
  --surface-0: #0a0a0c;
  --surface-1: #131316;
  --surface-2: #1c1c20;
  --border: rgba(255, 255, 255, 0.12);
  --border-strong: rgba(255, 255, 255, 0.22);
  --text-primary: #f3f1ea;
  --text-secondary: #b4b2a9;
  --text-muted: #9c9b95;
  --text-accent: #c7def6;
  --bg-accent: #0c447c;
  --bg-danger: #501313;
  --text-danger: #f4b0b0;
  --bg-success: #04342c;
  --text-success: #74d2b2;

  /* Reserved exclusively for the Approve button (row + drawer) — no other element uses this. */
  --action-primary-bg: #114fc8;
  --action-primary-fg: #ffffff;

  /* Autonomy-tier palette: A = amber, B = teal (moved off blue — see the light block's
     comment), C = green. */
  --tier-a-bg: #412402;
  --tier-a-fg: #fac775;
  --tier-b-bg: #063a3a;
  --tier-b-fg: #3ed7c4;
  --tier-c-bg: #173404;
  --tier-c-fg: #a8cd73;
  --crit-1: #f09595;
  --crit-2: #f0997b;
  --crit-3: #ef9f27;
  --crit-4: #97c459;
  --crit-5: #5dcaa5;
}
```

Note what did **not** change from the Task 1 values: `surface-0/1/2` are the only structural change (near-black replacing the old `#1f1e1c`/`#2a2926`/`#333230` family); `border`/`border-strong` stay as translucent white (already surface-agnostic); `text-primary`/`text-secondary`/`text-muted`/`text-danger`/`bg-danger`/`text-success`/`bg-success`/`text-accent`/`bg-accent`/`tier-a-*`/`tier-c-*`/`crit-*` are all **byte-identical** to Task 1's values — verified in Step 2 below that they still clear their tier against the new, darker surfaces (they do, with more margin than before, since a darker background only increases contrast against an already-light foreground).

- [ ] **Step 4: Run test to verify it passes**

Run: `cd apps/planner-ui && npx vitest run src/styles/tokens.contrast.test.ts`
Expected: PASS — 53 tests green (51 + 2 new `action-primary-fg`/`action-primary-bg` cases, one per scheme).

- [ ] **Step 5: Run the full suite**

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — full suite green, same count as the end of Task 2 plus the 2 new contrast cases (190 total). `npx tsc -b` clean.

- [ ] **Step 6: Commit**

```bash
cd apps/planner-ui && git add src/styles/tokens.css src/styles/tokens.contrast.test.ts
git commit -m "planner-ui: near-black dark palette, Tier-B moves to teal, new action-primary tokens"
```

---

### Task 4: Approve buttons get the bold `action-primary` fill treatment

**Files:**
- Modify: `apps/planner-ui/src/components/DetailPanel.module.css`
- Modify: `apps/planner-ui/src/components/QueueTable.tsx`
- Modify: `apps/planner-ui/src/components/QueueTable.module.css`
- Test: `apps/planner-ui/src/components/DetailPanel.test.tsx`, `apps/planner-ui/src/components/QueueTable.test.tsx` (existing tests, verify they still pass — no new tests needed, see below)

**Interfaces:**
- Consumes: `--action-primary-bg` / `--action-primary-fg` (Task 3).

- [ ] **Step 1: Confirm the existing tests exercise the right thing (no new tests needed)**

Both `DetailPanel.test.tsx`'s `"approve and defer fire their handlers"` test and `QueueTable.test.tsx`'s row-approve tests already assert `getByRole("button", { name: "Approve" })` and click it — they test *behavior* (the click fires the right handler), not the specific CSS class or color, so they don't need new assertions for a pure visual restyle. This step is a checkpoint, not a code change: run them now to record the baseline before touching any CSS.

Run: `cd apps/planner-ui && npx vitest run src/components/DetailPanel.test.tsx src/components/QueueTable.test.tsx`
Expected: PASS (baseline, before this task's CSS changes).

- [ ] **Step 2: Restyle `DetailPanel`'s `.approve` from an outline to a filled `action-primary` button**

In `apps/planner-ui/src/components/DetailPanel.module.css`, replace:

```css
.approve {
  border-color: var(--text-accent);
  color: var(--text-accent);
}
```

with:

```css
.approve {
  border-color: var(--action-primary-bg);
  background: var(--action-primary-bg);
  color: var(--action-primary-fg);
}

.approve:disabled {
  background: var(--surface-2);
  border-color: var(--border-strong);
  color: var(--text-muted);
}
```

(The `.approve:disabled` override is needed because the parent `.actions button:disabled` rule — already in this file, unchanged — only sets `opacity: 0.4`, which would otherwise dim the bold blue fill rather than visually returning it to a neutral "unavailable" look consistent with every other disabled button in the app.)

- [ ] **Step 3: Give `QueueTable`'s row-level Approve button the same treatment (it currently has no distinct class at all)**

In `apps/planner-ui/src/components/QueueTable.tsx`, find the Approve button (inside the `decided ? (...) : (...)` ternary in the row-rendering loop):

```tsx
                  <button
                    type="button"
                    disabled={approveDisabled}
                    title={
                      !r.approvable
                        ? "Advisory recommendation — nothing to write"
                        : disabled
                          ? "Approvals are paused — resume the agent to approve"
                          : undefined
                    }
                    onClick={() => onApprove(r.recommendation_id)}
                  >
                    Approve
                  </button>
```

Add `className={styles.approve}`:

```tsx
                  <button
                    type="button"
                    className={styles.approve}
                    disabled={approveDisabled}
                    title={
                      !r.approvable
                        ? "Advisory recommendation — nothing to write"
                        : disabled
                          ? "Approvals are paused — resume the agent to approve"
                          : undefined
                    }
                    onClick={() => onApprove(r.recommendation_id)}
                  >
                    Approve
                  </button>
```

In `apps/planner-ui/src/components/QueueTable.module.css`, add a new rule after the existing `.actions button:disabled` rule:

```css
.approve {
  background: var(--action-primary-bg);
  color: var(--action-primary-fg);
  border-color: var(--action-primary-bg);
}

.approve:hover:not(:disabled) {
  filter: brightness(1.1);
}

.approve:disabled {
  background: var(--surface-2);
  color: var(--text-primary);
  border-color: var(--border-strong);
}
```

(`.actions button` still supplies the base padding/font-size/border-radius/cursor; `.approve` only overrides color. `.approve:hover` needs its own rule since the existing `.actions button:hover:not(:disabled) { background: var(--surface-1); }` would otherwise flatten the fill to a neutral gray on hover — `filter: brightness(1.1)` keeps it recognizably the same blue, just slightly lighter, standard for a filled-button hover state.)

- [ ] **Step 4: Run tests to verify nothing broke**

Run: `cd apps/planner-ui && npx vitest run src/components/DetailPanel.test.tsx src/components/QueueTable.test.tsx`
Expected: PASS — identical results to Step 1's baseline (these tests assert behavior, which is unchanged; only the pure-CSS visual treatment changed, which these tests don't inspect).

Run: `cd apps/planner-ui && npm test -- --run`
Expected: PASS — full suite green (190, unchanged from Task 3 — no new tests in this task).

Run: `cd apps/planner-ui && npx tsc -b`
Expected: clean.

- [ ] **Step 5: Commit**

```bash
cd apps/planner-ui && git add src/components/DetailPanel.module.css src/components/QueueTable.tsx src/components/QueueTable.module.css
git commit -m "planner-ui: Approve buttons get the bold action-primary fill treatment"
```

---

## Final verification (after all 4 tasks)

- [ ] Run the full suite: `cd apps/planner-ui && npm test -- --run` — expect 190 tests, all green.
- [ ] `npx tsc -b` — zero errors.
- [ ] Live-verify via the preview MCP or `npm run dev`: app loads in dark mode by default (near-black surfaces) with no `localStorage` set; click the sun/moon toggle in `NavRail` — theme flips instantly, persists across a page reload; a Tier-B row shows a teal badge, not blue; the Approve button (both a table row's and one inside the open Drawer) renders as a solid filled blue button, distinct from every other neutral-outlined action button on the page.
- [ ] Update trackers: `ROADMAP.md`'s #7 section (new bullet for this phase), `TASKS.md` (dated completion entry), `CLAUDE.md` if the `apps/planner-ui` test-count bullet needs bumping (190).
- [ ] Note in the tracker update: Phases 2–4 (confidence & rationale treatment, table/badge conventions, navigation shell) remain — separate spec/plan/build cycles, not part of this phase.
