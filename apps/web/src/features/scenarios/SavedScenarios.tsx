import { useEffect, useRef, useState } from "react";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import type { Scenario } from "@/lib/api/types";
import { useFocusTrap } from "@/lib/useFocusTrap";

export interface SavedScenariosProps {
  scenarios: Scenario[];
  onDelete: (scenarioId: string) => void;
  onCommit: (scenarioId: string) => void;
  isDeleting?: boolean;
  isCommitting?: boolean;
}

const currencyFormatter = new Intl.NumberFormat("en-US", {
  style: "currency",
  currency: "USD",
  maximumFractionDigits: 0,
});

const pctFormatter = new Intl.NumberFormat("en-US", {
  style: "percent",
  maximumFractionDigits: 1,
});

const dateFormatter = new Intl.DateTimeFormat("en-US", {
  dateStyle: "medium",
  timeStyle: "short",
});

interface CommitConfirmDialogProps {
  scenarioName: string;
  onCancel: () => void;
  onConfirm: () => void;
  isCommitting?: boolean;
}

/**
 * The commit confirm step, extracted so `useFocusTrap` can be called
 * unconditionally at its own component root (only one instance is ever
 * mounted at a time — `confirmingCommitId` gates a single row). WCAG 2.1 AA:
 * traps focus while open, Escape cancels (mirrors `RejectDialog`).
 *
 * Focus restoration on close is handled by the *parent* (`SavedScenarios`),
 * not this component's own `useFocusTrap` call — this dialog replaces its
 * own trigger ("Commit") in the same render, so by the time this
 * component's effect ran, React had already blurred that trigger to
 * `<body>`, AND closing re-renders a brand-new "Commit" DOM node (not the
 * one that was clicked) — a captured element reference can't help either
 * way. See `SavedScenarios`'s `commitButtonRefs` + restore-focus effect.
 */
function CommitConfirmDialog({
  scenarioName,
  onCancel,
  onConfirm,
  isCommitting,
}: CommitConfirmDialogProps) {
  const containerRef = useRef<HTMLDivElement>(null);
  useFocusTrap(containerRef, onCancel);

  return (
    <div
      ref={containerRef}
      role="dialog"
      aria-modal="true"
      aria-label={`Confirm commit for ${scenarioName}`}
      className="flex items-center gap-2 rounded-md border border-line bg-panel-2 p-2"
    >
      <span className="text-xs text-ink-2">
        Commit as the tenant's target plan? No eMRO writeback occurs.
      </span>
      <Button variant="ghost" size="sm" onClick={onCancel} disabled={isCommitting}>
        Cancel
      </Button>
      <Button size="sm" onClick={onConfirm} disabled={isCommitting}>
        Confirm commit
      </Button>
    </div>
  );
}

/**
 * Saved scenarios: list, select two to compare (PRD §6.5 "Save, name, compare
 * scenarios"), and commit (with a confirm step — commit is audited and cannot be
 * un-done from this UI). Commit does NOT write policies back to eMRO — see
 * `bff/models.py`'s `ScenarioAuditEvent` docstring; the confirm dialog surfaces that.
 */
export function SavedScenarios({
  scenarios,
  onDelete,
  onCommit,
  isDeleting,
  isCommitting,
}: SavedScenariosProps) {
  const [compareIds, setCompareIds] = useState<string[]>([]);
  const [confirmingCommitId, setConfirmingCommitId] = useState<string | null>(null);
  const [committedAck, setCommittedAck] = useState<string | null>(null);
  // Per-scenario "Commit" button DOM nodes, keyed by scenario id, kept
  // current via each button's ref callback. Needed because closing
  // CommitConfirmDialog re-renders a *new* "Commit" button node (it's a
  // different JSX branch, not the same persistent element React can reuse
  // across the swap) — so restoring focus can't rely on a node captured
  // before the dialog opened; it must re-look-up the fresh node after the
  // state flips back. See useFocusTrap.ts's docstring for the full story.
  const commitButtonRefs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const previouslyConfirmingId = useRef<string | null>(null);

  useEffect(() => {
    // Escape/Cancel just closed the confirm dialog for this scenario —
    // move focus back to its (freshly-rendered) "Commit" button so focus
    // isn't lost to <body> (WCAG 2.1 AA §2.4.3 focus order).
    if (previouslyConfirmingId.current && confirmingCommitId === null) {
      commitButtonRefs.current.get(previouslyConfirmingId.current)?.focus();
    }
    previouslyConfirmingId.current = confirmingCommitId;
  }, [confirmingCommitId]);

  function toggleCompare(id: string) {
    setCompareIds((prev) => {
      if (prev.includes(id)) return prev.filter((x) => x !== id);
      if (prev.length >= 2) return [prev[1], id];
      return [...prev, id];
    });
  }

  function handleConfirmCommit(id: string) {
    onCommit(id);
    setConfirmingCommitId(null);
    setCommittedAck(id);
  }

  const compared = scenarios.filter((s) => compareIds.includes(s.id));

  if (scenarios.length === 0) {
    return <p className="text-sm text-ink-2">No saved scenarios yet.</p>;
  }

  return (
    <div className="flex flex-col gap-4">
      <ul className="flex flex-col gap-2" aria-label="Saved scenarios">
        {scenarios.map((scenario) => (
          <li
            key={scenario.id}
            className="flex flex-col gap-2 rounded-md border border-line bg-panel p-3 sm:flex-row sm:items-center sm:justify-between"
          >
            <div className="flex flex-col gap-1">
              <div className="flex items-center gap-2">
                <label className="flex items-center gap-1.5 text-sm">
                  <input
                    type="checkbox"
                    checked={compareIds.includes(scenario.id)}
                    onChange={() => toggleCompare(scenario.id)}
                    aria-label={`Select ${scenario.name} to compare`}
                  />
                  <span className="font-medium text-ink">{scenario.name}</span>
                </label>
                <Badge variant={scenario.status === "committed" ? "good" : "default"}>
                  {scenario.status}
                </Badge>
              </div>
              <span className="text-xs text-ink-2">
                Saved {dateFormatter.format(new Date(scenario.created_at))} ·{" "}
                {pctFormatter.format(scenario.result.proposed.service_level)} SL ·{" "}
                {currencyFormatter.format(scenario.result.proposed.projected_investment)}
              </span>
            </div>
            <div className="flex items-center gap-2">
              {scenario.status === "draft" ? (
                confirmingCommitId === scenario.id ? (
                  <CommitConfirmDialog
                    scenarioName={scenario.name}
                    onCancel={() => setConfirmingCommitId(null)}
                    onConfirm={() => handleConfirmCommit(scenario.id)}
                    isCommitting={isCommitting}
                  />
                ) : (
                  <Button
                    ref={(node) => {
                      if (node) commitButtonRefs.current.set(scenario.id, node);
                      else commitButtonRefs.current.delete(scenario.id);
                    }}
                    variant="outline"
                    size="sm"
                    onClick={() => setConfirmingCommitId(scenario.id)}
                  >
                    Commit
                  </Button>
                )
              ) : (
                <span className="text-xs text-ink-2">
                  Committed{" "}
                  {scenario.committed_at ? dateFormatter.format(new Date(scenario.committed_at)) : ""}
                </span>
              )}
              <Button
                variant="ghost"
                size="sm"
                onClick={() => onDelete(scenario.id)}
                disabled={isDeleting}
              >
                Delete
              </Button>
            </div>
          </li>
        ))}
      </ul>

      {committedAck && (
        <div role="status" className="rounded-md border border-good/40 bg-good/10 p-3 text-sm text-good">
          Scenario committed and recorded in the audit log. No eMRO writeback occurred — promoting
          scenario levers into live policy writes is out of scope for v1.
        </div>
      )}

      {compared.length === 2 && (
        <div className="flex flex-col gap-2 rounded-md border border-line bg-panel-2 p-3">
          <h3 className="text-sm font-semibold text-ink">Compare scenarios</h3>
          <table className="w-full text-sm">
            <thead>
              <tr className="text-left text-xs text-ink-2">
                <th scope="col" className="pb-2 pr-3 font-medium">
                  Metric
                </th>
                {compared.map((s) => (
                  <th key={s.id} scope="col" className="pb-2 pr-3 font-medium">
                    {s.name}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              <tr className="border-t border-line/60">
                <td className="py-1.5 pr-3 text-ink-2">Service level</td>
                {compared.map((s) => (
                  <td key={s.id} className="py-1.5 pr-3 tabular-nums text-ink">
                    {pctFormatter.format(s.result.proposed.service_level)}
                  </td>
                ))}
              </tr>
              <tr className="border-t border-line/60">
                <td className="py-1.5 pr-3 text-ink-2">Projected investment</td>
                {compared.map((s) => (
                  <td key={s.id} className="py-1.5 pr-3 tabular-nums text-ink">
                    {currencyFormatter.format(s.result.proposed.projected_investment)}
                  </td>
                ))}
              </tr>
              <tr className="border-t border-line/60">
                <td className="py-1.5 pr-3 text-ink-2">Investment delta vs. plan</td>
                {compared.map((s) => (
                  <td key={s.id} className="py-1.5 pr-3 tabular-nums text-ink">
                    {currencyFormatter.format(s.result.delta_investment)}
                  </td>
                ))}
              </tr>
              <tr className="border-t border-line/60">
                <td className="py-1.5 pr-3 text-ink-2">Skipped parts</td>
                {compared.map((s) => (
                  <td key={s.id} className="py-1.5 pr-3 tabular-nums text-ink">
                    {s.result.skipped_keys.toLocaleString()} / {s.result.total_keys.toLocaleString()}
                  </td>
                ))}
              </tr>
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
