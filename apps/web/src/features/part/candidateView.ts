import type { CandidateDecimal } from "@/lib/api/types";

export function formatCandidateLabel(value: string): string {
  return value
    .split("_")
    .map((word) => `${word.slice(0, 1).toUpperCase()}${word.slice(1)}`)
    .join(" ");
}

/** Group a fixed-point decimal without converting it to a JavaScript number. */
export function formatCandidateDecimal(value: CandidateDecimal): string {
  const match = /^([+-]?)(\d+)(\.\d+)?$/.exec(value);
  if (!match) return value;

  const [, sign, whole, fraction = ""] = match;
  const grouped = whole.replace(/\B(?=(\d{3})+(?!\d))/g, ",");
  return `${sign}${grouped}${fraction}`;
}

/** Convert a unit-interval fixed-point decimal to a percentage using string math. */
export function formatCandidatePercent(value: CandidateDecimal): string {
  const match = /^(\d+)(?:\.(\d+))?$/.exec(value);
  if (!match) return `${formatCandidateDecimal(value)} (0–1 ratio)`;

  const whole = match[1];
  const fraction = match[2] ?? "";
  const digits = `${whole}${fraction}`;
  const decimalIndex = whole.length + 2;
  const padded =
    decimalIndex >= digits.length
      ? digits.padEnd(decimalIndex, "0")
      : digits;
  const percentage =
    decimalIndex >= padded.length
      ? padded
      : `${padded.slice(0, decimalIndex)}.${padded.slice(decimalIndex)}`;
  const [percentageWhole, percentageFraction] = percentage.split(".");
  const normalizedWhole = percentageWhole.replace(/^0+(?=\d)/, "");
  const normalizedFraction = percentageFraction?.replace(/0+$/, "");
  const normalized = normalizedFraction
    ? `${normalizedWhole}.${normalizedFraction}`
    : normalizedWhole;
  return `${formatCandidateDecimal(normalized)}%`;
}

export function formatCandidateMoney(
  currency: string,
  value: CandidateDecimal,
): string {
  return `${currency} ${formatCandidateDecimal(value)}`;
}
