import { useEffect, type RefObject } from "react";

const FOCUSABLE_SELECTOR = [
  "a[href]",
  "button:not([disabled])",
  "input:not([disabled])",
  "select:not([disabled])",
  "textarea:not([disabled])",
  '[tabindex]:not([tabindex="-1"])',
].join(",");

/**
 * Dependency-free focus trap for inline `role="dialog"` affordances
 * (`RejectDialog`, the scenario commit-confirm dialog) — WCAG 2.1 AA §2.1.2
 * ("no keyboard trap" — but the reverse: an *open* dialog must trap focus
 * within itself) + §2.4.3 (focus order). On mount: remembers the
 * previously-focused element and moves focus into the dialog (the first
 * focusable child, or the container itself). While open: Tab/Shift+Tab wrap
 * within the dialog's focusable elements; Escape invokes `onClose`. On
 * unmount: restores focus to whatever was focused before the dialog opened.
 *
 * Mirrors DemandTrend/HealthMixDonut's dependency-free-primitives
 * convention — no `focus-trap-react`/radix-dialog install for what a ~30
 * line hook covers for this app's inline (non-portal) dialogs.
 *
 * Contract: the effect attaches once per **mount** of the component calling
 * this hook (it does not re-run when `containerRef.current` merely flips
 * from null to set within an always-mounted parent). Both call sites
 * (`RejectDialog`, `CommitConfirmDialog`) satisfy this — each is a distinct
 * component instance that mounts fresh when the dialog opens (conditionally
 * rendered by its parent), not a ref toggled inside one long-lived component.
 *
 * Restoring focus: by default the hook captures `document.activeElement` at
 * mount time. That is correct when the trigger element stays mounted
 * alongside the dialog (`RejectDialog` — the "Dismiss" button it replaces
 * nothing, it just grows a sibling). It is **wrong** when the dialog
 * replaces its own trigger in the same render (`{open ? <Dialog/> :
 * <Trigger/>}`) — React unmounts+blurs the trigger as part of the same
 * commit that mounts the dialog, so by the time *this* component's effect
 * runs, `document.activeElement` has already fallen back to `<body>`
 * (verified empirically). Worse, a *captured* trigger reference doesn't
 * help either in that pattern — closing the dialog re-renders a brand-new
 * DOM node for the trigger branch, not the original element, so
 * `oldNode.focus()` is a silent no-op on a detached node. For that swap
 * pattern, don't rely on this hook's restore step at all: have the *parent*
 * that owns both branches re-focus the freshly-rendered trigger via its own
 * effect after the state flips back (see `SavedScenarios`'s
 * `commitButtonRefs` + restore-focus effect for the worked example).
 * `restoreFocusTo` remains available for callers with a trigger that
 * genuinely stays mounted throughout (same requirement as the default
 * capture, just supplied explicitly instead of inferred from
 * `document.activeElement`).
 */
export function useFocusTrap<T extends HTMLElement>(
  containerRef: RefObject<T>,
  onClose: () => void,
  restoreFocusTo?: HTMLElement | null,
) {
  useEffect(() => {
    const container = containerRef.current;
    if (!container) return undefined;

    const previouslyFocused = restoreFocusTo ?? (document.activeElement as HTMLElement | null);

    function focusable(): HTMLElement[] {
      if (!container) return [];
      return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR));
    }

    const initial = focusable();
    (initial[0] ?? container).focus();

    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        event.stopPropagation();
        onClose();
        return;
      }

      if (event.key !== "Tab") return;

      const elements = focusable();
      if (elements.length === 0) {
        event.preventDefault();
        return;
      }

      const first = elements[0];
      const last = elements[elements.length - 1];
      const active = document.activeElement;

      if (event.shiftKey) {
        if (active === first || !container?.contains(active)) {
          event.preventDefault();
          last.focus();
        }
      } else if (active === last || !container?.contains(active)) {
        event.preventDefault();
        first.focus();
      }
    }

    container.addEventListener("keydown", handleKeyDown);
    return () => {
      container.removeEventListener("keydown", handleKeyDown);
      previouslyFocused?.focus();
    };
    // NOTE: `onClose`/`restoreFocusTo` are intentionally not in the deps
    // array — the trap's lifecycle is mount/unmount only (see the "Contract"
    // section of this hook's docstring above). This project's ESLint config
    // does not register `eslint-plugin-react-hooks` (see .eslintrc.cjs), so
    // no `exhaustive-deps` suppression is needed or possible here.
  }, [containerRef]);
}
