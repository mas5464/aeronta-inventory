# #7 Planner UI — Table & Badge Conventions (Phase 3 of 4: Airvoyant-inspired redesign) — Design

**Date:** 2026-07-03
**Status:** Approved (design)
**Sub-project:** #7 Planner UI "Trax IO Review" — Phase 3 of a 4-phase visual redesign inspired by an external reference (an aviation-parts-procurement tool). Phases 1 (dark theme) and 2 (confidence & rationale treatment) already shipped. Phase 4 (navigation shell) is a separate, later spec.
**Authoritative inputs:** the original reference-site observation captured during Phase 1's brainstorm ("Tabs with a count badge on the active tab (e.g. 'Quotes 2')"; "a dense data table with small circular rank/score badges in the first column and a per-row CTA button in the last column"), the current `apps/planner-ui/src/components/{Tabs,QueueTable}.tsx` + `.module.css`, `apps/planner-ui/src/hooks/usePlanner.ts`

## 1. Context

Two conventions from the reference site remain unaddressed after Phases 1–2: a row-count badge on the active queue tab, and a circular rank/score badge in the table's first column (Trax's equivalent of that column is `QueueTable`'s "Part" cell, which today shows a plain 7px colored dot for criticality, conveyed only by color plus a screen-reader-only label). Both are presentation changes over data Trax already has — no new backend endpoints or data plumbing required.

## 2. Scope

**In scope:**
- A count badge on the currently-active queue tab (Pending or Decided), reusing `usePlanner`'s existing `total` value.
- Replacing `QueueTable`'s plain criticality dot with a 20px circular badge showing the actual tier number (1–5), colored per the existing `--crit-1`..`--crit-5` ramp.

**Deferred / non-goals:**
- Showing a count on the *inactive* tab as well — would require fetching that tab's count from a query that isn't otherwise made, a new data dependency this phase doesn't take on. Revisit only if a future need justifies the extra fetch.
- Fixing the Decided tab's count-accuracy limitation (capped by `DECIDED_FETCH_LIMIT` per status, merged client-side) — a pre-existing, already-documented Wave-3 limitation. The badge surfaces the exact same number `KillSwitchHeader` already shows today; it doesn't make the number less accurate, it just reuses it in a new spot.
- Navigation-shell restructuring (NavRail → top-nav) — Phase 4, a separate spec.
- Any other table convention not named above (e.g., reordering columns, changing row density) — out of scope for a phase focused specifically on the two named badge conventions.

## 3. Tab count badge

`Tabs.tsx` gains an optional `count?: number` per tab entry, passed only for the currently-active tab from `App.tsx` (which already computes `p.total` for whichever tab is loaded — the identical value `KillSwitchHeader` reads today, so no new data fetching). Rendered as a small pill badge immediately after the tab's label — the same visual construction already used for Tier/AOG/Confidence badges elsewhere in this app (rounded pill, small caps, a background/foreground token pair), not plain inline text, for internal visual consistency with the rest of the app's badge vocabulary. The inactive tab renders with no badge at all, exactly as it does today.

No new color token is needed — the badge reuses `--surface-1`/`--text-secondary`, the exact neutral pair `QueueTable.module.css`'s existing `.status` badge already uses, since a count is informational, not a status signal, and doesn't need to compete visually with anything.

## 4. Criticality badge

`QueueTable.tsx`'s Part-column dot:

```tsx
<span
  className={styles.dot}
  style={{ background: `var(--crit-${r.criticality_tier})` }}
  aria-hidden="true"
/>
<span className={styles.srOnly}>Criticality {r.criticality_tier}. </span>
```

becomes a 20px circular badge showing the tier number as visible text, colored per the same `--crit-{tier}` token used today (the token now backs a background fill with a readable foreground, rather than being the dot's only color):

```tsx
<span
  className={styles.critBadge}
  style={{ background: `var(--crit-${r.criticality_tier})` }}
  aria-label={`Criticality ${r.criticality_tier}`}
>
  {r.criticality_tier}
</span>
```

The separate `styles.srOnly` span is removed — the badge's own `aria-label` carries the accessible name directly (one element, one accessible name), replacing the previous "visually hidden dot + separate hidden text" pattern with a single labeled element. This is also a genuine legibility improvement for sighted users: today, criticality is conveyed *only* by color (plus screen-reader-only text sighted users never see) — the numbered badge gives everyone a precise, at-a-glance readout instead of a relative color cue that requires memorizing the ramp.

The existing `--crit-1`..`--crit-5` values are unchanged — no new contrast verification needed for the *hue* choices, but the badge's foreground-on-background combination (visible digit color on the crit-color fill) is new and must clear the same contrast discipline every other token pair in this app does; if plain white/black digit text doesn't clear the tier's required threshold against a given `--crit-N` background, the implementation computes and verifies an explicit per-tier foreground (mirroring how Tier A/B/C already use a distinct foreground *and* background pair, not a single color).

## 5. Testing

- `Tabs.test.tsx`: the active tab shows its count as visible text; the inactive tab does not.
- `QueueTable.test.tsx`: the badge shows the correct tier number as visible text; `aria-label` matches; the old `srOnly` "Criticality N." text is gone (replaced by the label).
- Extend `tokens.contrast.test.ts` only if a new digit-foreground token is needed per §4's contrast note — otherwise no token changes.
- Per this project's now-established practice for UI-visible changes: live-verify computed styles in a browser (not just the automated suite) before considering the phase complete. Phases 1 and 2 each caught a real, test-invisible CSS bug this way — specifically, check whether the new single-class `.critBadge`-style rule in `QueueTable.module.css` risks the same specificity trap Phase 1's Approve button hit (a plain single-class rule losing to a more-specific pre-existing selector in the same file) before declaring this phase done.

## 6. Out of scope, tracked for later

Phase 4 (navigation shell: NavRail → top-nav) is a separate design/plan/build cycle, sequenced after this one lands. Both-tabs-counts and the Decided-tab count-accuracy limitation remain explicitly deferred, per §2.
