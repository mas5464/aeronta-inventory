import type { HistoryEntry, RollbackResult, RollbackStatus, WritebackStatus } from "@/lib/api/types";

/** The value dict keys are fixed by the writeback contract. */
export function formatPolicyValues(v: Record<string, number>): string {
  return `ROP ${v.rop} · EOQ ${v.eoq} · SS ${v.safety_stock} · Max ${v.max_stock}`;
}

const STATUS_LABELS: Record<WritebackStatus, string> = {
  written: "Written",
  deferred_open_order: "Deferred (open order)",
  failed: "Failed",
  shadowed: "Shadowed",
};

export function writebackStatusLabel(s: WritebackStatus): string {
  return STATUS_LABELS[s];
}

/** Badge variant per status — color reinforces the always-present text label
 * (color-not-only). Uses the existing Badge variants (good/warn/bad/default). */
const STATUS_VARIANTS: Record<WritebackStatus, "good" | "warn" | "bad" | "default"> = {
  written: "good",
  deferred_open_order: "warn",
  failed: "bad",
  shadowed: "default",
};

export function writebackStatusVariant(s: WritebackStatus): "good" | "warn" | "bad" | "default" {
  return STATUS_VARIANTS[s];
}

/**
 * The latest applied write that can be reverted — scan newest-first for a
 * `written` entry whose old_values (the prior value to restore) is known.
 */
export function latestRevertibleEntry(history: HistoryEntry[]): HistoryEntry | null {
  const latestWritten = [...history].reverse().find((e) => e.status === "written");
  return latestWritten && latestWritten.old_values !== null ? latestWritten : null;
}

const ROLLBACK_STATUS_MESSAGES: Record<RollbackStatus, string> = {
  rolled_back: "",
  outside_window: "This change is outside the rollback window and can no longer be reverted.",
  nothing_to_revert: "Nothing to roll back — no prior agent-applied value is on record.",
};

/**
 * A user-facing message for a rollback result that did NOT succeed, or null
 * when it rolled back cleanly. The BFF accepts the request but returns
 * `outside_window` / `nothing_to_revert` with `error_message` left null for
 * these expected refusals, so they must be mapped to text here — otherwise the
 * dialog stays open with no feedback and the planner can only keep clicking.
 */
export function rollbackResultMessage(result: RollbackResult): string | null {
  if (result.status === "rolled_back") return null;
  return result.error_message ?? ROLLBACK_STATUS_MESSAGES[result.status] ?? "Rollback did not complete.";
}
