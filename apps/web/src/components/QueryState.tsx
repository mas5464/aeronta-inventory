import type { ReactNode } from "react";

export interface QueryLoadingProps {
  /** Loading copy, e.g. "Loading dashboard…". */
  label: string;
  className?: string;
}

/**
 * Shared loading state — `role="status"` + `aria-live="polite"` so a screen
 * reader announces it without interrupting, per WCAG 2.1 AA §4.1.3. Every
 * view's `isPending` branch renders this (Slice S8 hardening — the 7 views
 * had near-identical hand-written copies of this markup; consolidated here
 * to keep them from drifting).
 */
export function QueryLoading({ label, className }: QueryLoadingProps) {
  return (
    <div role="status" aria-live="polite" className={className ?? "p-6 text-ink-2"}>
      {label}
    </div>
  );
}

export interface QueryErrorProps {
  /** Short description of what failed, e.g. "Failed to load dashboard". */
  label: string;
  error: unknown;
  /** Re-run the failed query — TanStack Query's `refetch()`. */
  onRetry: () => void;
  className?: string;
}

/**
 * Shared error state — `role="alert"` (announced assertively, WCAG 2.1 AA
 * §4.1.3) with the underlying error message plus a **Retry** button wired to
 * the query's `refetch()`. Every view's `isError` branch used to render only
 * text with no way to recover short of a full page reload — this closes
 * that gap uniformly (Slice S8 hardening).
 */
export function QueryError({ label, error, onRetry, className }: QueryErrorProps) {
  const detail = error instanceof Error ? error.message : "unknown error";
  return (
    <div role="alert" className={className ?? "flex flex-col items-start gap-3 p-6 text-bad"}>
      <span>
        {label}: {detail}
      </span>
      <button
        type="button"
        onClick={onRetry}
        className="h-8 rounded-control border border-line px-3 text-xs font-semibold text-ink hover:bg-panel-2 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg"
      >
        Retry
      </button>
    </div>
  );
}

export interface QueryEmptyProps {
  children: ReactNode;
  className?: string;
}

/** Shared empty state — a plain, distinguishable "nothing here" message (not a loading/error look-alike). */
export function QueryEmpty({ children, className }: QueryEmptyProps) {
  return <p className={className ?? "text-sm text-ink-2"}>{children}</p>;
}
