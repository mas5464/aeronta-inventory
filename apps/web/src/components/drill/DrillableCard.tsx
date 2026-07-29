import type { ReactNode, RefObject } from "react";
import { ChevronDown } from "lucide-react";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

export interface DrillableCardProps {
  /** Card title, rendered inside the trigger button (replaces CardTitle's own text). */
  title: string;
  /** Whether this card's drill panel is currently open. */
  open: boolean;
  /** Toggle callback — fired on trigger click. */
  onToggle: () => void;
  /** `id` of the `DrillPanel` this card discloses — wired to `aria-controls`. */
  panelId: string;
  /**
   * Forwarded to the trigger `<button>` — lets the parent pass this same ref
   * as `DrillPanel`'s `restoreFocusTo`, so focus returns here when the panel
   * closes. Optional: a caller with no restore need may omit it.
   */
  triggerRef?: RefObject<HTMLButtonElement>;
  /** The card's own content (e.g. a `Metric`) — unaffected by `open`. */
  children: ReactNode;
  className?: string;
}

/**
 * A `Card` whose header is a full-width disclosure trigger: clicking it opens
 * an in-place `DrillPanel` (rendered by the parent immediately below this
 * card) showing the full breakdown behind the card's headline number. The
 * card's own content — the `Metric` — renders unchanged below the trigger.
 *
 * This is a disclosure, not a modal: the trigger button NEVER unmounts when
 * the panel opens (see `useFocusTrap`'s docstring on the trigger-swap
 * hazard — a trigger that gets replaced by its own dialog in the same render
 * can't reliably regain focus on close). Keeping the trigger mounted here
 * means `DrillPanel`'s focus-restore can always find it.
 */
export function DrillableCard({
  title,
  open,
  onToggle,
  panelId,
  triggerRef,
  children,
  className,
}: DrillableCardProps) {
  return (
    <Card className={cn("transition-shadow hover:shadow-md", className)}>
      <CardHeader className="p-0">
        <button
          ref={triggerRef}
          type="button"
          onClick={onToggle}
          aria-expanded={open}
          aria-controls={panelId}
          className={cn(
            "flex w-full items-center justify-between gap-2 rounded-t-card p-4 text-left",
            "hover:bg-panel-2",
            "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2 focus-visible:ring-offset-bg",
          )}
        >
          <CardTitle className="p-0">{title}</CardTitle>
          <ChevronDown
            aria-hidden="true"
            className={cn("h-4 w-4 shrink-0 text-ink-2 transition-transform", open && "rotate-180")}
          />
        </button>
      </CardHeader>
      <CardContent>{children}</CardContent>
    </Card>
  );
}
