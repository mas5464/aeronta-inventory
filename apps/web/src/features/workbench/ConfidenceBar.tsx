import { cn } from "@/lib/utils";

export interface ConfidenceBarProps {
  /** 0..1 confidence score. */
  score: number;
  className?: string;
}

function tone(score: number): string {
  if (score >= 0.8) return "bg-good";
  if (score >= 0.5) return "bg-warn";
  return "bg-bad";
}

/**
 * Horizontal confidence bar for the Workbench worklist. Never color-only —
 * always paired with the numeric percentage label (WCAG 2.1 AA §6).
 */
export function ConfidenceBar({ score, className }: ConfidenceBarProps) {
  const pct = Math.round(Math.min(1, Math.max(0, score)) * 100);

  return (
    <div className={cn("flex items-center gap-2", className)} data-testid="confidence-bar">
      <div
        role="progressbar"
        aria-valuenow={pct}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-label="Confidence"
        className="h-1.5 w-16 overflow-hidden rounded-full bg-panel-2"
      >
        <div className={cn("h-full rounded-full", tone(score))} style={{ width: `${pct}%` }} />
      </div>
      <span className="text-xs tabular-nums text-ink-2">{pct}%</span>
    </div>
  );
}
