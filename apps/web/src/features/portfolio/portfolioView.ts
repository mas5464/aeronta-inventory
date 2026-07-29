import type { PlanningRunStatus } from "@/lib/api/planningRuns";

export function parsePlanningScope(value: string):
  | { keys: { pn: string; location: string }[]; error: null }
  | { keys: []; error: string } {
  const entries = value
    .split(/[\n,]+/)
    .map((entry) => entry.trim())
    .filter(Boolean);
  if (entries.length === 0) {
    return { keys: [], error: "Add at least one part and location." };
  }
  if (entries.length > 200) {
    return { keys: [], error: "A planning run can include at most 200 keys." };
  }

  const keys: { pn: string; location: string }[] = [];
  for (const entry of entries) {
    const separator = entry.lastIndexOf("@");
    const pn = entry.slice(0, separator).trim();
    const location = entry.slice(separator + 1).trim();
    if (separator <= 0 || !pn || !location) {
      return {
        keys: [],
        error: `Use PN@LOCATION for every key; “${entry}” is not valid.`,
      };
    }
    keys.push({ pn, location });
  }

  keys.sort((left, right) =>
    `${left.pn}@${left.location}`.localeCompare(
      `${right.pn}@${right.location}`,
    ),
  );
  const ids = keys.map((key) => `${key.pn}@${key.location}`);
  if (new Set(ids).size !== ids.length) {
    return { keys: [], error: "Each planning key must be unique." };
  }
  return { keys, error: null };
}

export function formatPlanningMoney(
  value: string | number | null | undefined,
  currency = "USD",
): string {
  if (value === null || value === undefined) return "Not available";
  const parsed = Number(value);
  if (!Number.isFinite(parsed)) return String(value);
  try {
    return new Intl.NumberFormat("en-US", {
      style: "currency",
      currency,
      maximumFractionDigits: 2,
    }).format(parsed);
  } catch {
    return `${currency} ${parsed.toLocaleString("en-US")}`;
  }
}

export function formatPlanningNumber(
  value: string | number | null | undefined,
  maximumFractionDigits = 2,
): string {
  if (value === null || value === undefined) return "Not available";
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? parsed.toLocaleString("en-US", { maximumFractionDigits })
    : String(value);
}

export function formatPlanningPercent(
  value: string | number | null | undefined,
  maximumFractionDigits = 1,
): string {
  if (value === null || value === undefined) return "Not available";
  const parsed = Number(value);
  return Number.isFinite(parsed)
    ? `${(parsed * 100).toLocaleString("en-US", {
        maximumFractionDigits,
      })}%`
    : String(value);
}

export function formatPlanningDate(value: string | null | undefined): string {
  if (!value) return "Not yet";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString("en-US", {
        month: "short",
        day: "numeric",
        year: "numeric",
        hour: "numeric",
        minute: "2-digit",
      });
}

export function planningStatusLabel(status: PlanningRunStatus): string {
  return {
    queued: "Queued",
    running: "Running",
    completed: "Completed",
    infeasible: "Infeasible",
    failed: "Failed",
  }[status];
}
