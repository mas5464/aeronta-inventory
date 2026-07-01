// Display formatters. All numbers that reach the screen go through these so float
// artifacts and Decimal-as-string never leak into the UI.

export function money(value: number | string): string {
  return Number(value).toLocaleString(undefined, {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
  });
}

export function priority(value: number): string {
  return value.toFixed(1);
}

// Projected demand for spares is often sub-unit (0.42, 2.73) — the intermittent-demand
// signal this UI exists to surface. Show up to 2 decimals for values < 10 (trimming
// trailing zeros), whole numbers for larger values. Never rounds a real value to 0.
export function demand(value: number): string {
  if (value === 0) return "0";
  if (value >= 10) return String(Math.round(value));
  return String(Math.round(value * 100) / 100);
}

export function typeLabel(type: string): string {
  return type.replace(/_/g, " ");
}
