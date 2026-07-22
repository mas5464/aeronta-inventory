import { useQuery } from "@tanstack/react-query";
import { Badge } from "@/components/ui/badge";
import { QueryError, QueryLoading } from "@/components/QueryState";
import { TableCaption } from "@/components/table/TableChrome";
import { activeTenant } from "@/lib/api/client";
import {
  ingestHistoryQueryKey,
  listIngests,
  type IngestHistoryItem,
  type IngestStatus,
} from "@/lib/api/ingest";

function statusVariant(status: IngestStatus): "good" | "warn" | "bad" | "default" {
  if (status === "done") return "good";
  if (status === "running" || status === "queued") return "warn";
  if (status === "failed" || status === "dead") return "bad";
  return "default";
}

function statusLabel(status: IngestStatus): string {
  return status.length === 0 ? status : status.charAt(0).toUpperCase() + status.slice(1);
}

function formatWhen(iso: string): string {
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toISOString().slice(0, 16).replace("T", " ");
}

function keyCount(item: IngestHistoryItem): string {
  return item.result ? String(item.result.keys) : "—";
}

/**
 * C3 Task 6 — an ingest run history table sourced from `GET .../ingest`
 * (services/agent-spine/.../bff/ingest_routes.py, `IngestJobStore.list_recent`).
 * Visible to every role (unlike `UploadPanel`, which hides its controls from
 * `viewer`) — history is read-only, so there's nothing to gate.
 */
export function IngestHistory() {
  const tenant = activeTenant();
  const { data, isPending, isError, error, refetch } = useQuery<IngestHistoryItem[]>({
    queryKey: ingestHistoryQueryKey(tenant),
    queryFn: () => listIngests(tenant),
  });

  if (isPending) {
    return <QueryLoading label="Loading ingest history…" />;
  }

  if (isError) {
    return <QueryError label="Failed to load ingest history" error={error} onRetry={() => refetch()} />;
  }

  const jobs = data ?? [];

  if (jobs.length === 0) {
    return <p className="text-sm text-ink-2">No uploads yet.</p>;
  }

  return (
    <table className="w-full text-left text-sm">
      <TableCaption>Ingest run history</TableCaption>
      <thead>
        <tr className="border-b border-line text-xs text-ink-2">
          <th scope="col" className="p-3 font-medium">When</th>
          <th scope="col" className="p-3 font-medium">Uploaded by</th>
          <th scope="col" className="p-3 font-medium">Status</th>
          <th scope="col" className="p-3 font-medium">Keys</th>
        </tr>
      </thead>
      <tbody>
        {jobs.map((job) => (
          <tr key={job.id} className="border-t border-line">
            <td className="p-3 tabular-nums text-ink-2">{formatWhen(job.created_at)}</td>
            <td className="p-3 text-ink-2">{job.uploaded_by ?? "—"}</td>
            <td className="p-3">
              <Badge variant={statusVariant(job.status)}>{statusLabel(job.status)}</Badge>
            </td>
            <td className="p-3 tabular-nums text-ink-2">{keyCount(job)}</td>
          </tr>
        ))}
      </tbody>
    </table>
  );
}
