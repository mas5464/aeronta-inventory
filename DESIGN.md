---
name: Aeronta Inventory
description: Governed autonomy for airline spares inventory — recommend, govern, and act on ROP/EOQ/safety-stock with a full audit trail.
colors:
  paper: "hsl(0 0% 100%)"
  panel: "hsl(210 40% 98%)"
  surface: "hsl(210 25% 94%)"
  border: "hsl(0 0% 87%)"
  ink: "hsl(218 23% 12%)"
  ink-muted: "hsl(218 8% 35%)"
  ink-secondary: "hsl(218 8% 48%)"
  coral: "hsl(16 100% 33%)"
  forest: "hsl(126 64% 11%)"
  cream: "hsl(38 62% 90%)"
  peach: "hsl(24 95% 73%)"
  mint: "hsl(155 39% 75%)"
  ring: "hsl(215 100% 64%)"
  success: "hsl(152 65% 26%)"
  warning: "hsl(40 100% 30%)"
  error: "hsl(16 100% 33%)"
  info: "hsl(215 75% 42%)"
  danger: "hsl(14 95% 26%)"
  series-blue: "hsl(215 70% 44%)"
  series-green: "hsl(152 70% 24%)"
  series-amber: "hsl(40 100% 33%)"
typography:
  headline:
    fontFamily: "Instrument Sans Variable, Instrument Sans, system-ui, -apple-system, sans-serif"
    fontSize: "32px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "-0.02em"
  title:
    fontFamily: "Instrument Sans Variable, Instrument Sans, system-ui, -apple-system, sans-serif"
    fontSize: "24px"
    fontWeight: 600
    lineHeight: 1.2
  body:
    fontFamily: "Instrument Sans Variable, Instrument Sans, system-ui, -apple-system, sans-serif"
    fontSize: "14px"
    fontWeight: 400
    lineHeight: 1.5
  label:
    fontFamily: "Instrument Sans Variable, Instrument Sans, system-ui, -apple-system, sans-serif"
    fontSize: "11px"
    fontWeight: 600
    lineHeight: 1.2
    letterSpacing: "0.08em"
rounded:
  sm: "8px"
  md: "12px"
  lg: "16px"
  full: "9999px"
spacing:
  xs: "4px"
  sm: "8px"
  md: "16px"
  lg: "24px"
  xl: "32px"
  2xl: "48px"
components:
  button-primary:
    backgroundColor: "{colors.ink}"
    textColor: "{colors.paper}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
    typography: "{typography.body}"
  button-primary-hover:
    backgroundColor: "{colors.ink}"
  button-outline:
    backgroundColor: "transparent"
    textColor: "{colors.ink}"
    rounded: "{rounded.md}"
    padding: "8px 16px"
  card:
    backgroundColor: "{colors.panel}"
    textColor: "{colors.ink}"
    rounded: "{rounded.lg}"
    padding: "16px"
  badge-brand:
    backgroundColor: "{colors.coral}"
    textColor: "{colors.coral}"
    rounded: "{rounded.full}"
    padding: "2px 8px"
---

# Design System: Aeronta Inventory

## Overview

**Creative North Star: "The Instrument Panel"**

Aeronta Inventory reads like a cockpit instrument panel, not a marketing surface: quiet, information-dense, and legible at a glance, with color spent only where it means something. Hierarchy comes from letterspacing, case, and position more than size — an 11px uppercase micro-label carries as much structural weight as a 32px headline, because the job is scanning a queue of hundreds of decisions, not being persuaded by one hero moment. The palette is literally ink on paper (light) or ink on warm charcoal (dark): near-neutral by default, with a single signature coral reserved for the brand's rare, deliberate moments — links, eyebrows, and risk. Every number that matters carries its own proof of origin (the `ProvChip`/`Metric` pair), so trust is built into the typography and layout, not bolted on as a tooltip afterthought.

**Key Characteristics:**
- Quiet and precise: near-neutral ink/paper base, color earned rather than decorative.
- Micro-label hierarchy: 11px uppercase letterspaced labels do the structural work large headings do elsewhere.
- Coral is singular and rare — brand accent and risk signal share one hue, which is the point: its scarcity is what makes it legible as urgent.
- Dense but calm: 14px running body text, tight paddings, quiet borders — built for a planner working a queue, not a landing page.
- Provenance is a rendered primitive, not an afterthought — a value and its trust signal are one inseparable component.

## Colors

Near-neutral ink-on-paper base (warm charcoal-on-cream when flipped to dark), with a single signature coral as the only saturated brand color and a validated, colorblind-safe trio reserved exclusively for charts.

### Primary
- **Signature Coral** (`hsl(16 100% 33%)`, lifted to `hsl(17 84% 63%)` in dark): the one saturated brand color — links, eyebrows, highlights, brand-variant badges. It shares its hue with the semantic **Error** color by design: coral's rarity is what makes its appearance always read as "this matters." Never used as a primary button fill (see Do's and Don'ts).

### Secondary
- **Signature Forest** (`hsl(126 64% 11%)`; mint `hsl(155 39% 75%)` stands in on dark): reserved for brand-moment surfaces — a "dark island" panel dropped into an otherwise light page (the Reports hero card, the Login side panel) by applying the `.dark` class to that subtree alone, independent of the page's own theme.

### Tertiary
- **Cream Accent** (`hsl(38 62% 90%)`; warm brown `hsl(24 18% 21%)` in dark): selected/hover fills and notices — the active nav item's background.
- **Peach** (`hsl(24 95% 73%)`): an eyebrow-label override for hero/marquee moments (Reports hero, Login), distinct from the coral eyebrow default.

### Neutral
- **White Paper** (`hsl(0 0% 100%)`) / **Warm Charcoal** (`hsl(228 19% 8%)` dark): page background.
- **Near-White Panel** (`hsl(210 40% 98%)`) / **Charcoal Panel** (`hsl(220 16% 11%)` dark): sidebar, cards.
- **Quiet Surface** (`hsl(210 25% 94%)`) / (`hsl(220 16% 15%)` dark): one step below panel — hover fills, table-row hover.
- **Border** (`hsl(0 0% 87%)`) / (`hsl(218 12% 22%)` dark): the only line-drawing color in the system.
- **Ink** (`hsl(218 23% 12%)`) / **Warm Cream** (`hsl(40 31% 94%)` dark): primary text.
- **Muted Ink** (`hsl(218 8% 35%)`) and **Secondary Ink** (`hsl(218 8% 48%)`) (lighter in dark): two steps of de-emphasis for labels and metadata.

### Semantic Status (never reused as data series)
- **Success** `hsl(152 65% 26%)` deep mint-green · **Warning** `hsl(40 100% 30%)` deep amber · **Error** `hsl(16 100% 33%)` signature coral · **Info** `hsl(215 75% 42%)` platform blue · **Danger** `hsl(14 95% 26%)` a deeper coral, for hard stops distinct from ordinary errors.

### Data Visualization Palette
- **Series Blue** `hsl(215 70% 44%)` · **Series Green** `hsl(152 70% 24%)` · **Series Amber** `hsl(40 100% 33%)` — validated for lightness band, chroma floor, CVD (color-vision-deficiency) separation, and ≥3:1 contrast against the panel color, in both themes.

### Named Rules
**The Status ≠ Series Rule.** Semantic status colors (success/warning/error/info/danger) and the data-visualization series palette are two disjoint systems. A chart never borrows a status color, and a status badge never borrows a series color — mixing them breaks the reader's ability to tell "this is a category" from "this is a health signal."

**The Rare Coral Rule.** Coral is the system's only saturated brand color, and it stays rare on purpose. It marks links, eyebrows, highlights, and risk/error — never a primary action, never decoration, never more than a small fraction of any screen. Its scarcity is the signal.

## Typography

**Body Font:** Instrument Sans Variable (with Instrument Sans, system-ui, -apple-system fallback)
**Label/Mono Font:** ui-monospace / SFMono-Regular / Menlo / Consolas — used for part numbers and other identifier-shaped values.

**Character:** A humanist grotesque running at a compact 14px base, doing quiet, dense, high-legibility work rather than expressive display type. Tight negative letter-spacing (-0.02em) on the few large headings that exist keeps them from feeling like marketing type.

### Hierarchy
- **Headline** (600, 32px, 1.2 line-height, -0.02em): page titles only (e.g., "Overview," "Workbench"). One per view.
- **Title** (600, 24px, 1.2): reserved for KPI/metric values (the `Metric` component's number) — the system's only other "big number" moment, deliberately the same visual weight class as a page headline, so a dashboard's numbers command real attention.
- **Body** (400, 14px, 1.5): the running size for nearly everything — tables, cards, form fields, nav labels.
- **Label** (600, 11px, 1.2, 0.08em–0.14em uppercase): section eyebrows, table column headers, and card titles. This is the system's real structural workhorse (see Named Rule below).

### Named Rules
**The Micro-Label Rule.** Card and section headers render as 11px semibold uppercase letterspaced labels (`CardTitle`, `.eyebrow`, `<th>`), never as large bold headings. Hierarchy in this system comes from letterspacing, case, and position — not font size. A designer reaching for a bigger, bolder section heading is fighting the system, not extending it.

## Layout

Sidebar shell at `lg` and above (256px / `w-64` fixed rail, sticky, full-height, right border) collapsing to a horizontal top block below `lg` — one DOM instance of every control (brand mark, nav, session cluster), recomposed with CSS only, never duplicated markup. Primary navigation is a vertical rail at desktop width and a horizontally-scrolling row on narrower viewports.

Spacing runs on a 4px base scale (`4/8/16/24/32/48px` — `xs/sm/md/lg/xl/2xl`), applied consistently as component padding (cards and inputs sit at 16px internal padding) and gap. Tables are dense: 16px cell padding, 11px uppercase column headers on a `panel` background, row-hover tinted with `surface` at 50% opacity. In-place "drill panels" (`DrillableCard`/`DrillPanel`) expand content within the existing card grid rather than navigating away or opening a modal, animated with a quiet 180ms fade+4px drop (`drill-in`), zeroed under `prefers-reduced-motion`.

## Elevation & Depth

Flat by default, with shadow reserved as a response to interaction or floating state rather than a static hierarchy signal. Resting cards carry only `shadow-sm`; a card escalates to `shadow-md` on hover when it is interactive (drillable), and floating surfaces (tooltips) sit at `shadow-md`. Shadows are warm and soft in light mode (`rgb(30 26 18 / …)`, echoing the "ink on paper" palette rather than a neutral black) and deeper/cooler in dark mode. `shadow-lg`/`shadow-xl` exist in the scale for future modal/overlay escalation but are not yet in active use.

### Shadow Vocabulary
- **sm** (`0 1px 2px rgb(30 26 18 / 0.05)`; dark `0 1px 2px rgb(0 0 0 / 0.35)`): resting card default.
- **md** (`0 2px 6px -1px rgb(30 26 18 / 0.07), 0 1px 2px rgb(30 26 18 / 0.04)`; dark `0 4px 8px -2px rgb(0 0 0 / 0.4)`): hover-elevated cards, tooltips, popovers.
- **lg** / **xl**: reserved for higher-elevation overlays (dialogs, dropdowns) as the system grows; not yet exercised by a shipped component.

### Named Rules
**The Flat-At-Rest Rule.** Nothing floats until it needs to. A card's shadow only escalates in response to hover or being a genuinely floating surface (tooltip, popover) — never as ambient decoration on a static element.

## Shapes

Two radius tiers cover the whole system: **control** (`12px` — buttons, inputs, nav items, badges' pill shape uses `full`/`9999px` instead) and **card** (`16px` — cards, drill panels, hero tiles). Borders are a single hairline `border` color throughout — no secondary border weight or color exists. Badges and pills use `rounded-full` for a distinct silhouette from the rest of the system's soft-square language.

## Components

### Buttons
- **Shape:** `rounded-control` (12px).
- **Primary:** ink fill, paper text (`bg-ink text-bg`), 85% opacity on hover — never coral (see Named Rules, Colors).
- **Outline / Ghost:** transparent background, ink text, border on Outline only; both hover to the `panel-2`/`surface` fill.
- **Sizing:** default `h-9`, compact `sm` variant at `h-8` with `text-xs`.
- **Focus:** 2px `ring` color outline with 2px offset against the button's own background — never a border-color-only focus state.

### Chips / Badges
- **Style:** `rounded-full`, hairline border, 8px/2px padding, `text-xs`.
- **Variants:** `default` (neutral panel-2), `good`/`warn`/`bad` (status color at 15% background / 40% border opacity), `brand` (coral at the same opacity treatment).
- **Signature use — the `ProvChip`:** a status-colored badge pairing a non-color-dependent status dot, the data's source system, and its freshness, wrapped in a tooltip that discloses system-of-record, coverage %, confidence %, and whether the value is derived. This is the provenance invariant made visible — see the Metric/ProvChip pairing below.

### Cards / Containers
- **Corner Style:** `rounded-card` (16px).
- **Background:** `panel` (near-white / charcoal-panel), hairline `border`.
- **Shadow Strategy:** `shadow-sm` at rest, `shadow-md` on hover only when interactive (see Elevation & Depth).
- **Internal Padding:** 16px (`CardHeader`/`CardContent`).
- **CardTitle:** renders as the system's Label tier (11px uppercase letterspaced), not a heading — consistent with the Micro-Label Rule.

### Inputs / Fields
- **Style:** hairline `border`, `paper` background, `rounded-control`, 8px/16px padding.
- **Focus:** border shifts to `ring` color plus a 3px `ring` glow at 25% opacity (`box-shadow`), no outline.

### Navigation
- **Style:** vertical rail (desktop) / horizontal scroll row (mobile), `text-sm font-medium`, muted-ink default, hovers to `surface` background + full-ink text.
- **Active state:** `accent` (cream) background with `accent-foreground` text and semibold weight — the system's one non-neutral "you are here" signal, plus `aria-current="page"` for free via `NavLink`.
- **Focus:** 2px `ring` outline, 2px offset against the nav's own panel background.

### Query States (signature pattern)
A single shared trio — `QueryLoading` (`role="status"`, `aria-live="polite"`), `QueryError` (`role="alert"` plus a wired **Retry** button), `QueryEmpty` (a plain, deliberately distinguishable "nothing here" message) — used identically across all 7 views so loading/error/empty never drift into bespoke, inconsistent copy or markup per screen.

### Metric + ProvChip (signature component)
The provenance invariant, rendered: a `MetricValue<T>` cannot be displayed without its lineage attached. `Metric` renders a label, a Title-tier (24px/600) number, and a `ProvChip` underneath carrying source + freshness, expandable via tooltip to system-of-record, coverage, confidence, and derived-value disclosure. No other numeric display pattern exists in the product — if a number matters enough to show a planner, it is a `Metric`.

## Do's and Don'ts

### Do:
- **Do** keep primary actions ink-filled, reserving coral for links, eyebrows, highlights, and risk/error signaling only.
- **Do** render section/card headers as 11px uppercase letterspaced labels, not large headings — hierarchy is letterspacing and position, not size.
- **Do** attach a `ProvChip`/`Metric` to any operational number shown to a planner; a number without provenance shouldn't render on a live view (Reports/BVR is the sole, deliberate exception — it's a governed report, not a live number).
- **Do** keep shadows flat at rest and escalate only on hover or for genuinely floating surfaces.
- **Do** use the shared `QueryLoading`/`QueryError`/`QueryEmpty` trio for every async view state rather than one-off markup.
- **Do** pair any color-coded status indicator with a text label or icon — never color alone (WCAG 2.1 AA §6, already the `ProvChip` pattern).

### Don't:
- **Don't** use coral (or any color) as a primary button fill — ink (light) or cream (dark) is the only primary fill.
- **Don't** reuse a semantic status color (success/warning/error/info/danger) as a data-series color, or vice versa — they are validated as two disjoint systems.
- **Don't** introduce a second border color or weight — one hairline `border` token draws every line in the system.
- **Don't** apply the `.dark` class to a subtree for anything other than a deliberate "brand-moment" island (Reports hero, Login panel) — it is not a generic "make this card look nice" trick.
- **Don't** add motion beyond the existing quiet 150-250ms fades/transitions, and always respect `prefers-reduced-motion` (the system already zeroes all animation/transition duration under it).
