import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { TableCaption } from "@/components/table/TableChrome";
import { useAuth } from "@/lib/auth/useAuth";
import { activeTenant } from "@/lib/api/client";
import {
  CANONICAL_COLUMNS,
  CANONICAL_FILE_NAMES,
  REQUIRED_CANONICAL_FILES,
  canonicalTemplateCsv,
  createIngest,
  getIngest,
  ingestHistoryQueryKey,
  isTerminalIngestStatus,
  mintUploadUrls,
  putToStorage,
  type CanonicalFileName,
  type IngestErrorItem,
} from "@/lib/api/ingest";

/** Roles that may drive an upload/ingest run — mirrors the BFF's write-role
 * floor on `/uploads` + `/ingest` (viewer is 403'd server-side; this is the
 * same UX nicety as `Members.tsx`'s `canManage` check: skip firing a request
 * that would just 403). */
function canUpload(role: string | null): boolean {
  return role === "planner" || role === "admin" || role === "owner";
}

function triggerTemplateDownload(name: CanonicalFileName): void {
  const blob = new Blob([canonicalTemplateCsv(name)], { type: "text/csv" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = `${name}_template.csv`;
  document.body.appendChild(a);
  a.click();
  a.remove();
  setTimeout(() => URL.revokeObjectURL(url), 60_000);
}

function groupErrorsByFile(errors: IngestErrorItem[]): Map<string, IngestErrorItem[]> {
  const groups = new Map<string, IngestErrorItem[]>();
  for (const error of errors) {
    const list = groups.get(error.file) ?? [];
    list.push(error);
    groups.set(error.file, list);
  }
  return groups;
}

export interface UploadPanelProps {
  /** Poll interval for `GET .../ingest/{job_id}`, in ms. Defaults to a real
   * 2s cadence; tests pass a small value so polling resolves quickly under
   * real timers instead of needing fake-timer choreography. */
  pollIntervalMs?: number;
}

/**
 * C3 Task 6 — the upload surface for the six canonical files (parts, stock,
 * demand_history, locations, open_orders, vendors), consuming Task 5's BFF
 * routes (services/agent-spine/.../bff/ingest_routes.py): mint signed
 * upload URLs, PUT each file straight to Supabase Storage (never through
 * the BFF), create an ingest job, then poll it to a terminal state.
 *
 * Role-gated: renders nothing for `viewer` — the BFF's write-role floor is
 * the real enforcement boundary; this just avoids showing controls that
 * would 403 (mirrors `Members.tsx`'s `canManage` gate). `IngestHistory` is
 * mounted separately in `DataConnections.tsx` and stays visible for every
 * role.
 */
export function UploadPanel({ pollIntervalMs = 2000 }: UploadPanelProps) {
  const { role, tenantSlug } = useAuth();
  const tenant = tenantSlug ?? activeTenant();
  const queryClient = useQueryClient();

  const [files, setFiles] = useState<Partial<Record<CanonicalFileName, File>>>({});
  const [jobId, setJobId] = useState<number | null>(null);
  const invalidatedRef = useRef(false);

  const runMutation = useMutation({
    mutationFn: async () => {
      const selected = CANONICAL_FILE_NAMES.filter((name) => files[name] !== undefined);
      const { batch_id, targets } = await mintUploadUrls(tenant, selected);
      await Promise.all(
        selected.map((name) => {
          const target = targets[name];
          const file = files[name];
          if (!target || !file) {
            throw new Error(`No upload target minted for ${name}`);
          }
          return putToStorage(target.url, file);
        }),
      );
      const filePaths: Record<string, string> = {};
      for (const name of selected) {
        filePaths[name] = targets[name]!.path;
      }
      const { job_id } = await createIngest(tenant, batch_id, filePaths);
      return job_id;
    },
    onSuccess: (job_id) => {
      invalidatedRef.current = false;
      setJobId(job_id);
    },
  });

  const pollQuery = useQuery({
    queryKey: ["ingest-job", tenant, jobId],
    queryFn: () => getIngest(tenant, jobId as number),
    enabled: jobId !== null,
    refetchInterval: (query) => {
      const status = query.state.data?.status;
      if (status && isTerminalIngestStatus(status)) return false;
      return pollIntervalMs;
    },
  });

  const jobStatus = pollQuery.data?.status;
  useEffect(() => {
    if (jobStatus && isTerminalIngestStatus(jobStatus) && !invalidatedRef.current) {
      invalidatedRef.current = true;
      void queryClient.invalidateQueries({ queryKey: ingestHistoryQueryKey(tenant) });
    }
  }, [jobStatus, queryClient, tenant]);

  if (!canUpload(role)) {
    return null;
  }

  const requiredMissing = REQUIRED_CANONICAL_FILES.some((name) => files[name] === undefined);
  const isRunning = runMutation.isPending || (jobId !== null && !isTerminalIngestStatus(jobStatus ?? "queued"));
  const canRun = !requiredMissing && !isRunning;

  const isDone = jobStatus === "done";
  const isFailed = jobStatus === "failed" || jobStatus === "dead";
  const result = pollQuery.data?.result ?? null;
  const errors = pollQuery.data?.errors ?? null;

  return (
    <div className="flex flex-col gap-4">
      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {CANONICAL_FILE_NAMES.map((name) => {
          const spec = CANONICAL_COLUMNS[name];
          const inputId = `upload-${name}`;
          return (
            <div key={name} className="flex flex-col gap-1 rounded-md border border-line bg-panel-2 p-3">
              <div className="flex items-center justify-between gap-2">
                <label htmlFor={inputId} className="text-sm font-medium text-ink">
                  {spec.label} file
                </label>
                <Badge variant={spec.required ? "brand" : "default"}>
                  {spec.required ? "Required" : "Optional"}
                </Badge>
              </div>
              <input
                id={inputId}
                type="file"
                accept=".csv,.xlsx"
                disabled={isRunning}
                onChange={(e) => {
                  const file = e.target.files?.[0];
                  setFiles((prev) => {
                    const next = { ...prev };
                    if (file) {
                      next[name] = file;
                    } else {
                      delete next[name];
                    }
                    return next;
                  });
                }}
                className="text-xs text-ink-2 file:mr-2 file:rounded-control file:border-0 file:bg-panel file:px-2 file:py-1 file:text-xs file:text-ink"
              />
              {files[name] && <p className="text-xs text-ink-3">{files[name]!.name}</p>}
              <button
                type="button"
                onClick={() => triggerTemplateDownload(name)}
                className="self-start text-xs text-brand underline-offset-2 hover:underline"
              >
                Download template
              </button>
            </div>
          );
        })}
      </div>

      <div className="flex items-center gap-3">
        <Button
          type="button"
          disabled={!canRun}
          onClick={() => {
            setJobId(null);
            runMutation.mutate();
          }}
        >
          {isRunning ? "Running…" : "Run ingest"}
        </Button>
        {requiredMissing && (
          <p className="text-xs text-ink-2">Parts and Stock files are required.</p>
        )}
        {runMutation.isError && (
          <p role="alert" className="text-xs text-bad">
            {runMutation.error instanceof Error ? runMutation.error.message : "Upload failed"}
          </p>
        )}
      </div>

      {jobId !== null && !isDone && !isFailed && (
        <p role="status" className="text-sm text-ink-2">
          Ingest job #{jobId}: {jobStatus ?? "queued"}…
        </p>
      )}

      {isDone && result && (
        <div className="flex flex-col gap-1 rounded-md border border-good/40 bg-good/10 p-3 text-sm">
          <p className="font-medium text-ink">Ingest complete</p>
          <p className="text-ink-2">
            {result.keys} keys · {result.recommendations} recommendations
          </p>
          <Link to="/workbench" className="text-brand underline-offset-2 hover:underline">
            View in Workbench
          </Link>
        </div>
      )}

      {isFailed && errors && errors.length > 0 && (
        <div className="flex flex-col gap-3">
          <p role="alert" className="text-sm font-medium text-bad">
            Ingest failed — {errors.length} error{errors.length === 1 ? "" : "s"}
          </p>
          {Array.from(groupErrorsByFile(errors)).map(([file, fileErrors]) => (
            <div key={file} data-testid={`ingest-error-group-${file}`} className="flex flex-col gap-1">
              <h4 className="text-xs font-semibold uppercase text-ink-2">{file}</h4>
              <table className="w-full text-left text-xs">
                <TableCaption>{`Ingest errors for ${file}`}</TableCaption>
                <thead>
                  <tr className="text-ink-2">
                    <th scope="col" className="pb-1 pr-3 font-medium">Row</th>
                    <th scope="col" className="pb-1 pr-3 font-medium">Column</th>
                    <th scope="col" className="pb-1 font-medium">Message</th>
                  </tr>
                </thead>
                <tbody>
                  {fileErrors.map((error, index) => (
                    <tr key={index} className="border-t border-line">
                      <td className="py-1 pr-3 text-ink-2">{error.row ?? "—"}</td>
                      <td className="py-1 pr-3 text-ink-2">{error.column ?? "—"}</td>
                      <td className="py-1 text-ink">{error.message}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
