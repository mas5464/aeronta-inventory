import type { Breakdown } from "@/lib/api/types";

export interface AtaRiskListProps {
  /** `by_ata` breakdown — one row per ATA chapter. */
  chapters: Breakdown[];
  /** Max rows to display, ranked by shortage descending. */
  limit?: number;
}

/**
 * Ranked horizontal-bar list of shortage risk by ATA chapter
 * (docs/DESIGN-SYSTEM.md's `HBarList`) — dependency-free, plain divs sized
 * by percentage, with the numeric shortage always shown as text (never
 * bar-length-only).
 */
export function AtaRiskList({ chapters, limit = 8 }: AtaRiskListProps) {
  if (chapters.length === 0) {
    return <p className="text-sm text-ink-2">No ATA chapter data available.</p>;
  }

  const ranked = [...chapters].sort((a, b) => b.shortage - a.shortage).slice(0, limit);
  const maxShortage = Math.max(1, ...ranked.map((c) => c.shortage));

  return (
    <ul className="flex flex-col gap-2" aria-label="Shortage risk by ATA chapter">
      {ranked.map((chapter) => {
        const pct = Math.round((chapter.shortage / maxShortage) * 100);
        return (
          <li key={chapter.key} className="flex items-center gap-3 text-sm">
            <span className="w-16 shrink-0 font-medium text-ink">ATA {chapter.key}</span>
            <span
              className="h-2 flex-1 rounded-full bg-panel-2"
              role="img"
              aria-label={`ATA ${chapter.key}: ${chapter.shortage} units short, ${chapter.count} parts`}
            >
              <span
                className="block h-2 rounded-full bg-bad"
                style={{ width: `${pct}%` }}
              />
            </span>
            <span className="w-20 shrink-0 text-right tabular-nums text-ink-2">
              {chapter.shortage.toLocaleString()} short
            </span>
          </li>
        );
      })}
    </ul>
  );
}
