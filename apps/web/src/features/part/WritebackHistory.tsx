import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardHeader, CardTitle } from "@/components/ui/card";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { useHistory } from "@/lib/api/useWriteback";
import type { HistoryEntry } from "@/lib/api/types";
import { formatPolicyValues, latestRevertibleEntry, writebackStatusLabel, writebackStatusVariant } from "@/features/part/writebackView";

function changedOn(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(0, 10);
}

export interface WritebackHistoryProps {
  pn: string;
  location: string;
  onRollback: (entry: HistoryEntry) => void;
}

export function WritebackHistory({ pn, location, onRollback }: WritebackHistoryProps) {
  const { data, isPending, isError, error, refetch } = useHistory(pn, location);
  const history = data ?? [];
  const revertible = latestRevertibleEntry(history);

  return (
    <Card id="history">
      <CardHeader className="flex flex-row items-center justify-between gap-3">
        <CardTitle>Writeback history</CardTitle>
        <Button
          variant="outline"
          size="sm"
          disabled={revertible === null}
          title={revertible === null ? "Nothing to roll back — no prior agent-applied value is on record" : undefined}
          onClick={() => revertible && onRollback(revertible)}
        >
          Roll back last change
        </Button>
      </CardHeader>
      <CardContent>
        {isPending ? (
          <QueryLoading label={`Loading history for ${pn} / ${location}…`} />
        ) : isError ? (
          <QueryError label={`Failed to load history for ${pn} / ${location}`} error={error} onRetry={() => refetch()} />
        ) : history.length === 0 ? (
          <p className="text-sm text-ink-2">No prior writes for {pn} · {location}.</p>
        ) : (
          <ol className="flex flex-col gap-2">
            {[...history].reverse().map((e) => (
              <li key={e.version} className="flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-line pt-2 text-sm">
                <span className="font-medium text-ink">v{e.version}</span>
                <Badge variant={writebackStatusVariant(e.status)}>{writebackStatusLabel(e.status)}</Badge>
                <span className="text-ink">{formatPolicyValues(e.new_values)}</span>
                <span className="text-xs text-ink-3">{changedOn(e.changed_at)} · {e.changed_by_principal}</span>
              </li>
            ))}
          </ol>
        )}
      </CardContent>
    </Card>
  );
}
