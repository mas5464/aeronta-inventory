import { useCallback, useMemo } from "react";
import { useSearchParams } from "react-router-dom";

export interface UrlSyncedStateConfig<T> {
  defaultValue: T;
  /** Params to merge into the URL for `value`; return an empty URLSearchParams
   * when `value` equals the defaults, so the URL stays clean. */
  serialize: (value: T) => URLSearchParams;
  /** Total function — garbage/missing params must resolve to defaults, never throw. */
  deserialize: (params: URLSearchParams) => T;
}

/**
 * Syncs a piece of state with the URL's query string via react-router-dom's
 * `useSearchParams`. Keys this hook doesn't own (as produced by `serialize`)
 * are preserved untouched — set calls merge rather than clobber — and every
 * set is one atomic `setSearchParams` call with `{ replace: true }` so filter
 * churn (e.g. typing in a search box) doesn't spam browser history.
 */
export function useUrlSyncedState<T>(config: UrlSyncedStateConfig<T>): [T, (value: T) => void] {
  const { defaultValue, serialize, deserialize } = config;
  const [searchParams, setSearchParams] = useSearchParams();

  const value = useMemo(() => deserialize(searchParams), [searchParams, deserialize]);

  const setValue = useCallback(
    (next: T) => {
      setSearchParams(
        (prev) => {
          // Keys this hook is responsible for: the union of what `serialize`
          // emits for the value already in the URL, the defaults, and the
          // value being written. Deleting all of them before re-appending
          // the next value's params guarantees no stale/duplicate occurrence
          // of an owned key survives — including a key whose CURRENT value in
          // the URL is garbage `serialize` would never itself have produced
          // (e.g. `?dir=sideways`), since that key is still covered by the
          // defaults/next-value passes.
          const ownedKeys = new Set<string>();
          for (const key of serialize(deserialize(prev)).keys()) ownedKeys.add(key);
          for (const key of serialize(defaultValue).keys()) ownedKeys.add(key);
          const nextParams = serialize(next);
          for (const key of nextParams.keys()) ownedKeys.add(key);

          const merged = new URLSearchParams(prev);
          for (const key of ownedKeys) merged.delete(key);
          for (const [key, val] of nextParams.entries()) merged.append(key, val);

          return merged;
        },
        { replace: true },
      );
    },
    [defaultValue, serialize, deserialize, setSearchParams],
  );

  return [value, setValue];
}
