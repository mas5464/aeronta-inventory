# apps/web Dark / Light Theme Toggle — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a user-toggleable, `localStorage`-backed dark/light theme switch to `apps/web`, activating its already-existing (but dormant) light palette.

**Architecture:** `apps/web` is already dark-by-default with a complete `:root.light` override in `globals.css` (verified live to render cleanly) — nothing toggles it today. This wave adds a `useTheme` hook that toggles the `.light` class on `<html>`, an inline pre-paint anti-flash script in `index.html`, and a sun/moon toggle in the app header. No palette/token changes.

**Tech Stack:** React 18 + TypeScript + Tailwind (`darkMode: ["class"]`, CSS-variable tokens) + `lucide-react` + Vitest + Testing Library.

## Global Constraints

- Do NOT touch the (now-retired) `apps/planner-ui` or the BFF.
- **No token/palette changes** — both `:root` (dark, default) and `:root.light` (light override) already exist in `src/styles/globals.css` and render well. This wave only toggles between them.
- **Convention (critical):** `apps/web` uses `:root` = dark default + a `.light` **class** that opts into light (the OPPOSITE of the retired review UI's `data-theme` attribute). The hook toggles the `.light` class: **add** for light, **remove** for dark. Do NOT use `dataset.theme`.
- **Dark-first default:** a missing/any-non-`"light"` stored value resolves to `"dark"`. No `prefers-color-scheme` detection.
- `localStorage` key is exactly `"trax-web-theme"`.
- No WCAG contrast-test suite (explicitly out of scope). The pre-existing `bad`-badge dark-mode contrast item stays separately tracked; not addressed here.
- Test hygiene: reset `localStorage` and `document.documentElement.className` in `afterEach` for any test that toggles the theme; use `vi.restoreAllMocks()` for spy-based tests.
- Frontend commands (from `apps/web`): tests `npm test -- <file>`; typecheck+build `npm run build`; lint `npm run lint` (2 pre-existing shadcn/ui `react-refresh` warnings on badge.tsx/button.tsx are acceptable).
- This completes `apps/web` ↔ `apps/planner-ui` parity; retiring `apps/planner-ui` is a separate follow-up, NOT this plan.

---

### Task 1: `useTheme` hook

**Files:**
- Create: `apps/web/src/lib/useTheme.ts`
- Test: `apps/web/src/lib/useTheme.test.tsx`

**Interfaces:**
- Produces: `type Theme = "light" | "dark"`; `useTheme(): { theme: Theme; toggleTheme: () => void }`. Applies the theme by adding/removing the `.light` class on `document.documentElement`; persists to `localStorage["trax-web-theme"]`; dark-first default.

- [ ] **Step 1: Write the failing test**

Create `apps/web/src/lib/useTheme.test.tsx`:

```typescript
import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { useTheme } from "@/lib/useTheme";

const KEY = "trax-web-theme";

afterEach(() => {
  localStorage.clear();
  document.documentElement.className = "";
});

describe("useTheme", () => {
  it("defaults to dark when nothing is stored (no .light class)", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.classList.contains("light")).toBe(false);
  });

  it("reads a stored 'light' preference and applies the .light class on mount", () => {
    localStorage.setItem(KEY, "light");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.classList.contains("light")).toBe(true);
  });

  it("treats any non-'light' stored value as dark (dark-first)", () => {
    localStorage.setItem(KEY, "garbage");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.classList.contains("light")).toBe(false);
  });

  it("toggles dark→light: adds .light and persists 'light'", () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("light");
    expect(document.documentElement.classList.contains("light")).toBe(true);
    expect(localStorage.getItem(KEY)).toBe("light");
  });

  it("toggles light→dark: removes .light and persists 'dark'", () => {
    localStorage.setItem(KEY, "light");
    const { result } = renderHook(() => useTheme());
    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.classList.contains("light")).toBe(false);
    expect(localStorage.getItem(KEY)).toBe("dark");
  });
});
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd apps/web && npm test -- useTheme`
Expected: FAIL — `useTheme` module doesn't exist.

- [ ] **Step 3: Implement the hook**

Create `apps/web/src/lib/useTheme.ts`:

```typescript
import { useCallback, useState } from "react";

export type Theme = "light" | "dark";
const STORAGE_KEY = "trax-web-theme";

// apps/web convention: :root is the DARK default; the `.light` class opts into
// the light override (globals.css). So light = add class, dark = remove class.
// (This is the opposite of apps/planner-ui's data-theme attribute mechanism.)
function applyTheme(theme: Theme) {
  document.documentElement.classList.toggle("light", theme === "light");
}

function readInitialTheme(): Theme {
  try {
    return localStorage.getItem(STORAGE_KEY) === "light" ? "light" : "dark";
  } catch {
    return "dark"; // localStorage unavailable → dark default
  }
}

// Dark-first, user-toggleable theme. No prefers-color-scheme fallback — dark is
// the deliberate default (matches the CSS :root default), not inferred from OS.
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
      try {
        localStorage.setItem(STORAGE_KEY, next);
      } catch {
        /* localStorage unavailable — the class still applies for this session */
      }
      return next;
    });
  }, []);

  return { theme, toggleTheme };
}
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd apps/web && npm test -- useTheme`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/useTheme.ts apps/web/src/lib/useTheme.test.tsx
git commit -m "feat(web): add useTheme hook (dark-first, toggles .light class, localStorage)"
```

---

### Task 2: Header toggle + pre-paint anti-flash script

**Files:**
- Modify: `apps/web/index.html` (inline script in `<head>`)
- Modify: `apps/web/src/App.tsx` (header toggle button)
- Test: `apps/web/src/App.test.tsx` (extend)

**Interfaces:**
- Consumes: `useTheme` (Task 1); lucide `Sun`/`Moon`.
- Produces: the user-visible toggle. No new exports.

- [ ] **Step 1: Add the pre-paint anti-flash script to `index.html`**

In `apps/web/index.html`, add this inline script in `<head>` immediately after the `<title>` and BEFORE the body's module script (it must run before the app bundle so the class is set pre-paint):

```html
    <title>Trax Inventory Optimizer</title>
    <script>
      // Pre-paint theme: :root is dark by default, so only a stored "light"
      // preference needs applying here (before the bundle loads) to avoid a
      // dark→light flash for returning light-mode users. See src/lib/useTheme.ts.
      (function () {
        try {
          if (localStorage.getItem("trax-web-theme") === "light") {
            document.documentElement.classList.add("light");
          }
        } catch (e) {
          /* localStorage unavailable — fall back to the dark default */
        }
      })();
    </script>
```

(This is a raw inline script — not unit-testable; its read-and-apply logic mirrors `useTheme`'s `readInitialTheme`/`applyTheme`, which ARE unit-tested in Task 1, and it's covered by the live verification below.)

- [ ] **Step 2: Write the failing App.test.tsx additions**

In `apps/web/src/App.test.tsx`:

(a) Extend the existing `afterEach` so a toggled theme doesn't leak between tests:

```typescript
  afterEach(() => {
    vi.unstubAllGlobals();
    window.location.hash = "";
    localStorage.clear();
    document.documentElement.className = "";
  });
```

(b) Add a new test (the header renders the toggle; clicking it flips the `.light` class and the `aria-label`):

```typescript
  it("renders a theme toggle that flips the .light class and its aria-label", async () => {
    stubPendingFetch();
    const user = userEvent.setup();

    renderApp();

    // Dark default: the button offers switching TO light.
    const toDark = () => screen.queryByRole("button", { name: /switch to dark theme/i });
    const toLight = () => screen.queryByRole("button", { name: /switch to light theme/i });

    expect(toLight()).toBeInTheDocument();
    expect(document.documentElement.classList.contains("light")).toBe(false);

    await user.click(toLight()!);

    expect(document.documentElement.classList.contains("light")).toBe(true);
    // aria-label now offers switching back to dark.
    expect(toDark()).toBeInTheDocument();
  });
```

- [ ] **Step 3: Run to verify it fails**

Run: `cd apps/web && npm test -- App.test`
Expected: FAIL — no theme-toggle button exists yet.

- [ ] **Step 4: Wire the toggle into `App.tsx`**

In `apps/web/src/App.tsx`:

1. Add imports at the top:

```typescript
import { Moon, Sun } from "lucide-react";
import { useTheme } from "@/lib/useTheme";
```

2. Replace the header block (currently `<div className="px-6 py-4"><h1>…</h1></div>` inside `<header>`) so the title and a toggle button sit on a `justify-between` row. The `App` component gains a `useTheme()` call at the top of its body:

```tsx
export default function App() {
  const { theme, toggleTheme } = useTheme();
  return (
    <HashRouter>
      <div className="min-h-screen bg-bg text-ink">
        <header className="border-b border-line">
          <div className="flex items-center justify-between px-6 py-4">
            <h1 className="text-lg font-semibold">Trax Inventory Optimizer</h1>
            <button
              type="button"
              onClick={toggleTheme}
              aria-label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"}
              className="rounded-control p-2 text-ink-2 hover:text-ink focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-brand focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
            >
              {theme === "dark" ? <Sun className="h-4 w-4" /> : <Moon className="h-4 w-4" />}
            </button>
          </div>
          <AppNav />
        </header>
```

(Icon logic: in dark mode show the `Sun` — clicking it goes to light; in light mode show the `Moon`. The `aria-label` names the destination, satisfying WCAG icon-only-button labeling. Leave the rest of `App` — `<main>`, `<Routes>` — unchanged.)

- [ ] **Step 5: Run to verify it passes**

Run: `cd apps/web && npm test -- App.test`
Expected: all App tests pass — the new toggle test plus the existing nav/routing tests (the toggle is a `button`, so it doesn't collide with the existing `getByRole("link", …)` / `getByRole("heading", …)` assertions).

- [ ] **Step 6: Full frontend gate**

Run: `cd apps/web && npm test && npm run build && npm run lint`
Expected: all Vitest green, build clean (0 errors), lint 0 errors (2 pre-existing warnings acceptable).

- [ ] **Step 7: Commit**

```bash
git add apps/web/index.html apps/web/src/App.tsx apps/web/src/App.test.tsx
git commit -m "feat(web): add dark/light theme toggle in header + pre-paint anti-flash script"
```

---

## Final verification (after all tasks)

- `cd apps/web && npm test && npm run build && npm run lint` — full frontend suite green, build + lint clean.
- **Live Docker verification** (rebuild web; BFF unchanged): at `http://localhost:8089`, the header shows a sun/moon toggle; clicking it flips the whole app dark↔light (spot-check Overview + one more view, e.g. Reports); the choice persists across a page reload; a hard reload while in light mode shows **no dark flash** before the light theme applies (the inline script). Confirm no console errors.
- Update trackers per repo convention: `CLAUDE.md` (apps/web now has a dark/light toggle — Wave 4 of 4, parity complete), `ROADMAP.md`, `TASKS.md`, `.superpowers/sdd/progress.md`. Note that all four parity waves are done and retiring `apps/planner-ui` is the remaining mechanical follow-up. Do NOT touch `apps/planner-ui` docs.
