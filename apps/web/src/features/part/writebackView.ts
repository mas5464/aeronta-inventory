import type { HistoryEntry, WritebackStatus } from "@/lib/api/types";

/** Verbatim from planner-ui's valueSummary — the value dict keys are fixed. */
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
 * The latest applied write that can be reverted — mirrors planner-ui: scan
 * newest-first for a `written` entry whose old_values (the prior value to
 * restore) is known.
 */
export function latestRevertibleEntry(history: HistoryEntry[]): HistoryEntry | null {
  const latestWritten = [...history].reverse().find((e) => e.status === "written");
  return latestWritten && latestWritten.old_values !== null ? latestWritten : null;
}
