import { describe, expect, it } from "vitest";
import { contrastRatio, hexToRgb } from "../lib/contrast";
import tokensCssRaw from "./tokens.css?raw";

const TOKENS_CSS = tokensCssRaw;

// Extracts `--name: value;` declarations from a single `{ ... }` block of raw CSS text.
// Intentionally narrow (matches this file's current flat, non-nested block structure) —
// see the plan/spec risk note if tokens.css's structure ever changes materially.
function parseDeclarations(block: string): Record<string, string> {
  const tokens: Record<string, string> = {};
  const re = /--([a-z0-9-]+):\s*([^;]+);/g;
  let match: RegExpExecArray | null;
  while ((match = re.exec(block)) !== null) {
    tokens[match[1]] = match[2].trim();
  }
  return tokens;
}

function rootBlock(css: string, afterIndex = 0): string {
  const rootAt = css.indexOf(":root", afterIndex);
  const openBrace = css.indexOf("{", rootAt);
  const closeBrace = css.indexOf("}", openBrace);
  return css.slice(openBrace + 1, closeBrace);
}

const LIGHT = parseDeclarations(rootBlock(TOKENS_CSS));
// The dark block is a `:root[data-theme="dark"] { ... }` selector (not a media query —
// this makes the theme JS-toggleable via document.documentElement.dataset.theme).
function darkBlock(css: string): string {
  const at = css.indexOf('[data-theme="dark"]');
  const openBrace = css.indexOf("{", at);
  const closeBrace = css.indexOf("}", openBrace);
  return css.slice(openBrace + 1, closeBrace);
}
const DARK = { ...LIGHT, ...parseDeclarations(darkBlock(TOKENS_CSS)) };

const SURFACES = ["surface-0", "surface-1", "surface-2"];
// 7:1 (AAA) for primary/high-emphasis content; 4.5:1 (AA) for tokens that exist
// specifically to recede below primary (text-secondary, text-muted) — holding those
// to 7:1 would erase the visual hierarchy they're designed to create.
const AAA_TEXT_TOKENS = [
  "text-primary",
  "text-accent",
  "text-danger",
  "text-success",
  "confidence-high-fg",
];
const AA_TEXT_TOKENS = ["text-secondary", "text-muted"];
const THEMED_PAIRS: [string, string][] = [
  ["text-accent", "bg-accent"],
  ["text-danger", "bg-danger"],
  ["text-success", "bg-success"],
  ["tier-a-fg", "tier-a-bg"],
  ["tier-b-fg", "tier-b-bg"],
  ["tier-c-fg", "tier-c-bg"],
  ["action-primary-fg", "action-primary-bg"],
  ["confidence-high-fg", "confidence-high-bg"],
];
const THEMED_THRESHOLD = 7.0; // every themed pair's fg token is in AAA_TEXT_TOKENS-equivalent territory

// A deliberate, narrow exception to the AAA-everywhere policy above: a compact
// circular badge digit on a saturated fill is closer to a status indicator than
// primary body text, and no foreground clears 7:1 against the existing
// --crit-1..--crit-5 backgrounds without retuning those already-shipped colors
// (a decision made explicitly, not a shortcut — see the plan's Task 2 for the
// computed numbers). Kept in its own array/threshold so THEMED_PAIRS above stays
// uniformly AAA.
const AA_THEMED_PAIRS: [string, string][] = [
  ["crit-badge-fg", "crit-1"],
  ["crit-badge-fg", "crit-2"],
  ["crit-badge-fg", "crit-3"],
  ["crit-badge-fg", "crit-4"],
  ["crit-badge-fg", "crit-5"],
];
const AA_THEMED_THRESHOLD = 4.5;

describe("contrast math sanity checks", () => {
  it("black on white is the maximum 21:1", () => {
    expect(contrastRatio("#000000", "#ffffff")).toBeCloseTo(21, 1);
  });

  it("identical colors are 1:1", () => {
    expect(contrastRatio("#336699", "#336699")).toBeCloseTo(1, 5);
  });

  it("hexToRgb parses a hex triplet", () => {
    expect(hexToRgb("#ff0080")).toEqual([255, 0, 128]);
  });
});

describe.each([
  ["light", LIGHT],
  ["dark", DARK],
])("%s tokens.css contrast (AAA text / AA muted-secondary)", (_name, tokens) => {
  it.each(AAA_TEXT_TOKENS.flatMap((fg) => SURFACES.map((bg) => [fg, bg] as const)))(
    "%s on %s is >= 7:1",
    (fg, bg) => {
      expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(7.0);
    },
  );

  it.each(AA_TEXT_TOKENS.flatMap((fg) => SURFACES.map((bg) => [fg, bg] as const)))(
    "%s on %s is >= 4.5:1",
    (fg, bg) => {
      expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(4.5);
    },
  );

  it.each(THEMED_PAIRS)("%s on %s is >= 7:1", (fg, bg) => {
    expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(THEMED_THRESHOLD);
  });

  it.each(AA_THEMED_PAIRS)("%s on %s is >= 4.5:1", (fg, bg) => {
    expect(contrastRatio(tokens[fg], tokens[bg])).toBeGreaterThanOrEqual(AA_THEMED_THRESHOLD);
  });
});
