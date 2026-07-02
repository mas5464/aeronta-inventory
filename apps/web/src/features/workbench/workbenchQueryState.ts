import type { AutonomyTier, QueueSortKey, RecommendationType } from "@/lib/api/types";

/**
 * Pure, framework-free codec for the Workbench's URL-synced query state
 * (task F4): sort/filter pills now drive the BFF's server-side sort/filter
 * params (`GET .../recommendations?sort_by=&sort_dir=&tier=&type=&aog_min=`,
 * BFF commit 0d3c04d) instead of a client-side narrowing of the loaded page.
 *
 * `aogOnly` is a UI-level boolean pill ("AOG risk only") that maps to the
 * BFF's `aog_min=3` (High/Critical) at request time — see Workbench.tsx.
 */
export interface WorkbenchQueryState {
  sort: QueueSortKey;
  dir: "asc" | "desc";
  tier: AutonomyTier | "all";
  type: RecommendationType | "all";
  aogOnly: boolean;
}

export const DEFAULT_WORKBENCH_QUERY_STATE: WorkbenchQueryState = {
  sort: "priority_score",
  dir: "desc",
  tier: "all",
  type: "all",
  aogOnly: false,
};

/**
 * The complete, static list of every URL param key `encodeWorkbenchQueryState`
 * can ever emit — the `useUrlSyncedState` `ownedKeys` config for the
 * Workbench. Kept next to the codec (rather than re-derived from it at the
 * `useUrlSyncedState` call site) so the two can't drift; see the codec test
 * asserting every key a fully-non-default state encodes to is a subset of
 * this list.
 */
export const WORKBENCH_QUERY_KEYS: readonly string[] = ["sort", "dir", "tier", "type", "aog"];

const VALID_SORT_KEYS: readonly QueueSortKey[] = [
  "priority_score",
  "estimated_cost_impact",
  "confidence_score",
  "criticality_tier",
];

const VALID_TIERS: readonly AutonomyTier[] = [1, 2, 3];

const VALID_TYPES: readonly RecommendationType[] = [
  "purchase",
  "transfer",
  "reduce_stock",
  "sell",
  "adjust_min_max",
];

/**
 * Encodes `state` as `URLSearchParams`, omitting any key whose value equals
 * the corresponding default — so the URL stays clean at the default state
 * (matches `useUrlSyncedState`'s "empty params at defaults" contract).
 */
export function encodeWorkbenchQueryState(state: WorkbenchQueryState): URLSearchParams {
  const params = new URLSearchParams();
  if (state.sort !== DEFAULT_WORKBENCH_QUERY_STATE.sort) params.set("sort", state.sort);
  if (state.dir !== DEFAULT_WORKBENCH_QUERY_STATE.dir) params.set("dir", state.dir);
  if (state.tier !== DEFAULT_WORKBENCH_QUERY_STATE.tier) params.set("tier", String(state.tier));
  if (state.type !== DEFAULT_WORKBENCH_QUERY_STATE.type) params.set("type", state.type);
  if (state.aogOnly !== DEFAULT_WORKBENCH_QUERY_STATE.aogOnly) {
    params.set("aog", String(state.aogOnly));
  }
  return params;
}

/**
 * Decodes `params` into a `WorkbenchQueryState`. Total function: missing or
 * garbage values (e.g. `?sort=bogus&dir=sideways&tier=99`) fall back to the
 * matching default field rather than throwing, per `useUrlSyncedState`'s
 * deserialize contract.
 */
export function decodeWorkbenchQueryState(params: URLSearchParams): WorkbenchQueryState {
  const rawSort = params.get("sort");
  const sort = (VALID_SORT_KEYS as readonly string[]).includes(rawSort ?? "")
    ? (rawSort as QueueSortKey)
    : DEFAULT_WORKBENCH_QUERY_STATE.sort;

  const rawDir = params.get("dir");
  const dir = rawDir === "asc" || rawDir === "desc" ? rawDir : DEFAULT_WORKBENCH_QUERY_STATE.dir;

  const rawTier = params.get("tier");
  const parsedTier = rawTier === null ? NaN : Number(rawTier);
  const tier = (VALID_TIERS as readonly number[]).includes(parsedTier)
    ? (parsedTier as AutonomyTier)
    : DEFAULT_WORKBENCH_QUERY_STATE.tier;

  const rawType = params.get("type");
  const type = (VALID_TYPES as readonly string[]).includes(rawType ?? "")
    ? (rawType as RecommendationType)
    : DEFAULT_WORKBENCH_QUERY_STATE.type;

  const rawAog = params.get("aog");
  const aogOnly = rawAog === null ? DEFAULT_WORKBENCH_QUERY_STATE.aogOnly : rawAog === "true";

  return { sort, dir, tier, type, aogOnly };
}
