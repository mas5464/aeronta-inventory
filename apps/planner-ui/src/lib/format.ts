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

export function typeLabel(type: string): string {
  return type.replace(/_/g, " ");
}
