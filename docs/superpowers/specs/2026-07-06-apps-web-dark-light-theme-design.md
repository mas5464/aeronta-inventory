# apps/web: Dark / Light Theme Toggle (Feature-Parity Wave 4 of 4 — final)

## Context

The last of the four waves bringing `apps/web` ("Trax Inventory Optimizer")
to feature parity with `apps/planner-ui`. Waves 1 (CSV export), 2 (writeback
history + rollback), and 3 (Reports/BVR view) shipped and are on `main`. After
this wave, `apps/web` is at parity and retiring `apps/planner-ui` becomes a
separate mechanical follow-up.

`apps/planner-ui` has a user-toggleable, `localStorage`-backed, dark-first
theme (its `useTheme` hook + a NavRail sun/moon toggle). `apps/web` has no
toggle — but, crucially, it is **not** light-only.

### The key finding (verified live)

`apps/web` is already fully token-based and **dark by default**, with a
complete **light override already written but dormant**:
- `tailwind.config` maps every color to a CSS variable (`hsl(var(--x))`) and
  sets `darkMode: ["class"]`.
- `src/styles/globals.css`: `:root` defines the **dark** palette (the default);
  `:root.light` defines a full **light** override.
- Nothing anywhere adds the `.light` class (verified: the only `.light`
  reference in `src/` is its own CSS definition), so the app renders dark-only
  in practice.
- **Verified live** (toggling `.light` on `document.documentElement` against
  the running `:8089` deployment): the light override flips the entire app
  cleanly — body, cards, nav, KPI tiles, provenance chips, donut, tier bars,
  disclosure callouts all render correctly in light. The light palette is
  production-quality; it has simply never been switched on.

So this wave is **only the toggle + persistence**, not building a theme.

## What already exists (do not rebuild)

- Both palettes (`:root` dark, `:root.light`) in `globals.css` — unchanged by
  this wave.
- `lucide-react` (Sun/Moon icons) — already a dependency.
- The app shell header in `apps/web/src/App.tsx`: a `<header>` containing
  `<div className="px-6 py-4"><h1>Trax Inventory Optimizer</h1></div>` then
  `<AppNav />`. The toggle goes in that header (app-shell level → present on
  all 7 views).

## Convention difference from planner-ui (important)

`apps/planner-ui`'s `useTheme` sets `document.documentElement.dataset.theme =
"dark"|"light"` (light is its CSS `:root` default; dark applied via
`[data-theme="dark"]`). `apps/web` uses the **opposite** convention: `:root`
is **dark** by default and a `.light` **class** opts into light. So this
wave's hook toggles the **`.light` class** (add for light, remove for dark) —
NOT a `data-theme` attribute. Do not copy planner-ui's attribute mechanism
verbatim; adapt it to the class convention `globals.css` already uses.

## Design

### `useTheme` hook — `apps/web/src/lib/useTheme.ts`

Adapted from `apps/planner-ui/src/hooks/useTheme.ts`:

- `export type Theme = "light" | "dark"`.
- `STORAGE_KEY = "trax-web-theme"` (distinct from planner-ui's `"trax-io-theme"`
  — the two apps are separate origins, but a distinct key is unambiguous).
- **Dark-first default:** a missing/any-non-`"light"` stored value → `"dark"`.
  No `prefers-color-scheme` fallback (dark is the deliberate default, matching
  the CSS `:root` default and planner-ui).
- `applyTheme(theme)`: `theme === "light"` → `document.documentElement.classList.add("light")`;
  else `…classList.remove("light")`.
- `useState<Theme>` initializer reads the stored theme and calls `applyTheme`
  (syncs React state to the DOM state the pre-paint script — below — already
  set). `toggleTheme` flips dark↔light, calls `applyTheme`, and writes
  `localStorage`.
- Returns `{ theme, toggleTheme }`.

### Flash-of-wrong-theme prevention — inline script in `index.html`

A tiny, dependency-free inline script in `apps/web/index.html`'s `<head>`,
BEFORE the `/src/main.tsx` module script, applies the stored theme before
first paint:

```html
<script>
  (function () {
    try {
      if (localStorage.getItem("trax-web-theme") === "light") {
        document.documentElement.classList.add("light");
      }
    } catch (e) { /* localStorage unavailable — fall back to the dark default */ }
  })();
</script>
```

Rationale: dark is the CSS default, so the only flash risk is a returning
light-preferring user briefly seeing dark before React mounts. Running this in
`<head>` before the app bundle loads eliminates it — the bulletproof,
standard anti-flash pattern, and it sidesteps the flash-class bug
`apps/planner-ui` hit (where a default applied post-mount flashed). The
`useTheme` hook's initializer then reads the same key and syncs React state to
this already-applied DOM state (idempotent). The `try/catch` degrades to the
dark default if `localStorage` is unavailable.

### Toggle control — in `App.tsx`'s header

A sun/moon icon button in the header, right-aligned beside the title (wrap the
existing title `<div>` + a new button in a `flex items-center
justify-between` row). It:
- Calls `useTheme()` in `App` (or a small `AppHeader`), renders lucide `Moon`
  when the theme is `"dark"` (click → switch to light) and `Sun` when
  `"light"` (click → switch to dark).
- Carries an `aria-label` that names the destination: `"Switch to light
  theme"` when dark, `"Switch to dark theme"` when light (WCAG icon-only-button
  labeling; matches planner-ui's toggle).
- Uses the app's existing button styling vocabulary (the `focus-visible` ring
  classes already used on nav links); an icon-only control sized comfortably
  (≥ the surrounding nav hit targets).

## Testing

**`useTheme` hook** (`apps/web/src/lib/useTheme.test.tsx`): dark-first default
when `localStorage` is empty (and `.light` absent); reads a stored `"light"`
(applies `.light` + returns `"dark"`→ no; returns `"light"`); `toggleTheme`
flips dark→light (adds `.light`, writes `localStorage` `"light"`) and
light→dark (removes `.light`, writes `"dark"`). Reset `localStorage` +
`document.documentElement.className` in `afterEach`; use `vi.restoreAllMocks()`
if any spy is used (per the Wave-2 test-hygiene lesson).

**Header toggle** (extend `apps/web/src/App.test.tsx`): the toggle button
renders with the correct `aria-label` for the current theme; clicking it
toggles `document.documentElement.classList.contains("light")`. The existing
App tests (nav items, routing) stay green — the header change must not disturb
them (the nav-label assertions and the `stubPendingFetch` pattern are
untouched).

**Live Docker verification** (rebuild web; BFF unchanged): at
`http://localhost:8089`, the header shows a theme toggle; clicking it flips the
whole app dark↔light (spot-check Overview + one other view); the choice
persists across a reload; a hard reload in light mode shows **no dark flash**
before the light theme applies.

## Out of scope

- Any `apps/planner-ui` change (it keeps its own theme) and any BFF change.
- **No token/palette edits** — both `:root` and `:root.light` already exist and
  render well; this wave only toggles between them.
- **No WCAG contrast-test suite** (planner-ui has one; explicitly not ported
  here per the scope decision). Any token-contrast concern — e.g. the `bad`
  badge's dark-mode ratio flagged in this session's earlier UX audit — remains
  a separate, already-tracked item, not addressed or re-litigated here.
- No `prefers-color-scheme` OS-preference detection (dark is the deliberate
  default; the toggle is the user's control).
- Retiring `apps/planner-ui` — a separate mechanical follow-up once this ships.
