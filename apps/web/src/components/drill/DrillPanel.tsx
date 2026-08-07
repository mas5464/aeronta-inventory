import { useEffect, useRef, type KeyboardEvent, type ReactNode, type RefObject } from "react";
import { X } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

export interface DrillPanelProps {
  /** `id` matching the opening `DrillableCard`'s `aria-controls`. */
  id: string;
  /** Panel heading — also used as the accessible name (`aria-label`). */
  title: string;
  onClose: () => void;
  /**
   * The trigger button to return focus to when this panel closes. Optional —
   * a caller without a stable trigger ref may omit it, but every current
   * caller (Overview's `DrillableCard`s) supplies one so Escape/close never
   * strands focus on `<body>`.
   */
  restoreFocusTo?: RefObject<HTMLButtonElement>;
  children: ReactNode;
  className?: string;
}

/**
 * The in-place disclosure body opened by a `DrillableCard`. Deliberately NOT
 * a modal and NOT focus-trapped: this is a `role="region"` — a labeled
 * landmark occupying normal document flow (rendered by the parent directly
 * below the card that opened it), not an overlay that suspends the rest of
 * the page. Trapping focus inside a non-modal region would violate WCAG 2.1
 * AA §2.1.2 (no keyboard trap) — Tab must be free to carry the user on into
 * the next card/panel/footer, exactly like any other in-flow content.
 *
 * Accessibility contract:
 * - `role="region"` + `aria-label={title}` + `id` (wired to the opening
 *   card's `aria-controls`) — a screen-reader user can jump straight to it.
 * - The heading is `tabIndex={-1}` and receives focus via an effect on
 *   mount, so keyboard/AT users land inside the newly-revealed content
 *   immediately (WCAG 2.4.3 focus order) without trapping them there.
 * - Escape closes, handled by `onKeyDown` on the region itself (not a
 *   `document` listener) — scoped so it only fires while focus is inside
 *   this panel, consistent with this panel's Tab-can-leave design.
 * - On close, focus returns to `restoreFocusTo` (the button that opened this
 *   panel). Unlike `useFocusTrap`'s modal dialogs — which can't reuse a
 *   captured trigger reference because closing re-renders a *new* trigger
 *   node in the same JSX slot (see that hook's docstring + `SavedScenarios`'s
 *   `commitButtonRefs` workaround) — `DrillableCard`'s trigger never
 *   unmounts, so a plain ref passed down from the parent is sufficient and
 *   simpler: no ref-map, no "previously open id" bookkeeping.
 */
export function DrillPanel({ id, title, onClose, restoreFocusTo, children, className }: DrillPanelProps) {
  const headingRef = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    headingRef.current?.focus();
  }, []);

  useEffect(() => {
    // NOTE: `restoreFocusTo` is intentionally not in the deps array — this
    // effect's only job is its cleanup, which must run exactly once on
    // unmount (mirrors `useFocusTrap`'s mount/unmount-only contract). This
    // project's ESLint config does not register `eslint-plugin-react-hooks`
    // (see .eslintrc.cjs), so no `exhaustive-deps` suppression is needed.
    return () => {
      restoreFocusTo?.current?.focus();
    };
  }, []);

  function handleKeyDown(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "Escape") {
      event.stopPropagation();
      onClose();
    }
  }

  return (
    <div
      id={id}
      role="region"
      aria-label={title}
      onKeyDown={handleKeyDown}
      className={cn("animate-drill-in rounded-card border border-brand/40 bg-panel p-4", className)}
    >
      <div className="mb-3 flex items-center justify-between gap-2">
        <h3 ref={headingRef} tabIndex={-1} className="text-sm font-semibold text-ink focus:outline-none">
          {title}
        </h3>
        <Button variant="ghost" size="sm" onClick={onClose} aria-label={`Close ${title}`}>
          <X aria-hidden="true" className="h-4 w-4" />
        </Button>
      </div>
      {children}
    </div>
  );
}
