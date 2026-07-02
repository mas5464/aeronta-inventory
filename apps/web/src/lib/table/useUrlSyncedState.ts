import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

export interface UrlSyncedStateConfig<T> {
  defaultValue: T;
  /** Params to merge into the URL for `value`; return an empty URLSearchParams
   * when `value` equals the defaults, so the URL stays clean. */
  serialize: (value: T) => URLSearchParams;
  /** Total function — garbage/missing params must resolve to defaults, never throw. */
  deserialize: (params: URLSearchParams) => T;
  /**
   * The complete, static list of every param key `serialize` can ever emit
   * for this codec. `setValue` deletes exactly these keys before merging in
   * the next value's params — this must be an explicit list rather than
   * something derived from `serialize`/`deserialize` output, because a
   * garbage value already in the URL (e.g. `?tier=99`) deserializes to the
   * default and then *serializes back to nothing*, so round-tripping through
   * the codec can never discover that `tier` needs cleaning up.
   */
  ownedKeys: readonly string[];
}

/**
 * Syncs a piece of state with the URL's query string via react-router-dom's
 * `useSearchParams`. Keys this hook doesn't own (as produced by `serialize`)
 * are preserved untouched — set calls merge rather than clobber — and every
 * set is one atomic `setSearchParams` call with `{ replace: true }` so filter
 * churn (e.g. typing in a search box) doesn't spam browser history.
 */
export function useUrlSyncedState<T>(config: UrlSyncedStateConfig<T>): [T, (value: T) => void] {
  const { serialize, deserialize, ownedKeys } = config;
  const [searchParams, setSearchParams] = useSearchParams();

  const value = useMemo(() => deserialize(searchParams), [searchParams, deserialize]);

  const setValue = useCallback(
    (next: T) => {
      setSearchParams(
        (prev) => {
          // Delete every key this codec owns (the static `ownedKeys` list —
          // NOT something re-derived from serialize/deserialize on `prev`,
          // which would miss a garbage value already in the URL that
          // deserializes to the default and thus serializes back to nothing,
          // e.g. `?tier=99`) before re-appending the next value's params.
          // This guarantees no stale/garbage/duplicate occurrence of an
          // owned key survives a set call.
          const merged = new URLSearchParams(prev);
          for (const key of ownedKeys) merged.delete(key);
          for (const [key, val] of serialize(next).entries()) merged.append(key, val);

          return merged;
        },
        { replace: true },
      );
    },
    [ownedKeys, serialize, setSearchParams],
  );

  return [value, setValue];
}
