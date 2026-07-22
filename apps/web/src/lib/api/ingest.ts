import { activeTenant, request } from "@/lib/api/client";

/**
 * Upload/ingest client (C3 Task 6), mirroring
 * services/agent-spine/src/trax_io_spine/bff/ingest_routes.py's
 * `/v1/tenants/{tenant}/uploads` + `/v1/tenants/{tenant}/ingest*` routes.
 *
 * Kept as its own module (rather than folded into `bffClient` in client.ts)
 * per the C3 task-6 brief, mirroring `lib/api/members.ts` — still built on
 * the shared `request<T>` helper, so auth-header attach + 401 handling +
 * ApiError mapping come free for the two BFF-proper calls (`mintUploadUrls`,
 * `createIngest`, `getIngest`, `listIngests`). `putToStorage` is the one
 * exception — see its own docstring.
 */

/** The six canonical files a tenant can upload — column names here ARE the
 * public connector spec (services/recommendation-engine/.../ingest/canonical.py,
 * the Python source of truth mirrored here since the BFF doesn't serve this
 * list over HTTP). */
export type CanonicalFileName =
  | "parts"
  | "stock"
  | "demand_history"
  | "locations"
  | "open_orders"
  | "vendors";

export const CANONICAL_FILE_NAMES: CanonicalFileName[] = [
  "parts",
  "stock",
  "demand_history",
  "locations",
  "open_orders",
  "vendors",
];

/** Mirrors `REQUIRED_FILES` in canonical.py — the engine cannot run without
 * at least parts + stock. `POST .../ingest` 422s if either is missing. */
export const REQUIRED_CANONICAL_FILES: CanonicalFileName[] = ["parts", "stock"];

export interface CanonicalFileSpec {
  name: CanonicalFileName;
  label: string;
  required: boolean;
  /** Required columns followed by optional columns, in spec order — this IS
   * the header row of the client-generated CSV template. */
  columns: string[];
}

/** Mirrors `CANONICAL_FILES` in canonical.py column-for-column — see that
 * module's docstring: "Column names here ARE the public connector spec". */
export const CANONICAL_COLUMNS: Record<CanonicalFileName, CanonicalFileSpec> = {
  parts: {
    name: "parts",
    label: "Parts",
    required: true,
    columns: [
      "part_number",
      "description",
      "criticality",
      "part_class",
      "unit_cost",
      "repairable",
      "shelf_life_days",
      "hazmat",
      "ata_chapter",
      "is_kit",
    ],
  },
  stock: {
    name: "stock",
    label: "Stock",
    required: true,
    columns: [
      "part_number",
      "location_code",
      "on_hand",
      "allocated",
      "in_repair",
      "current_rop",
      "current_eoq",
      "current_safety_stock",
      "current_max",
    ],
  },
  demand_history: {
    name: "demand_history",
    label: "Demand history",
    required: false,
    columns: ["part_number", "location_code", "period", "quantity", "transaction_type"],
  },
  locations: {
    name: "locations",
    label: "Locations",
    required: false,
    columns: ["location_code", "parent_location_code"],
  },
  open_orders: {
    name: "open_orders",
    label: "Open orders",
    required: false,
    columns: ["part_number", "location_code", "quantity", "expected_date", "order_type"],
  },
  vendors: {
    name: "vendors",
    label: "Vendors",
    required: false,
    columns: [
      "part_number",
      "vendor_code",
      "unit_price",
      "lead_time_days",
      "min_order_qty",
      "condition",
      "preferred",
    ],
  },
};

/** A client-generated CSV of just the header row — lets a planner see
 * exactly which columns a canonical file expects before uploading. */
export function canonicalTemplateCsv(name: CanonicalFileName): string {
  return `${CANONICAL_COLUMNS[name].columns.join(",")}\n`;
}

export interface UploadTarget {
  url: string;
  path: string;
}

export type UploadTargets = Partial<Record<CanonicalFileName, UploadTarget>>;

export interface MintUploadsResponse {
  batch_id: string;
  targets: UploadTargets;
}

export type IngestStatus = "queued" | "running" | "done" | "failed" | "dead";

/** Row/column are only present on row-level validation errors (0-based row
 * index) — file-level errors (missing file, missing column) leave both
 * null/absent (services/recommendation-engine/.../ingest/validate.py). */
export interface IngestErrorItem {
  file: string;
  row?: number | null;
  column?: string | null;
  message: string;
}

export interface IngestResult {
  files: string[];
  keys: number;
  recommendations: number;
  seeded_at: string;
}

/** `GET .../ingest/{job_id}` response shape — deliberately distinct from
 * `IngestHistoryItem` below: the poll response has no `id`/`uploaded_by`,
 * the history item has no `errors` (see ingest_routes.py + pg/uploads.py). */
export interface IngestJob {
  status: IngestStatus;
  result: IngestResult | null;
  errors: IngestErrorItem[] | null;
}

export interface IngestHistoryItem {
  id: number;
  status: IngestStatus;
  result: IngestResult | null;
  uploaded_by: string | null;
  created_at: string;
}

/** A job is done polling once it reaches a terminal status. */
export function isTerminalIngestStatus(status: IngestStatus): boolean {
  return status === "done" || status === "failed" || status === "dead";
}

export function mintUploadUrls(
  tenant: string,
  files: CanonicalFileName[],
): Promise<MintUploadsResponse> {
  return request<MintUploadsResponse>(`/v1/tenants/${encodeURIComponent(tenant)}/uploads`, {
    method: "POST",
    body: JSON.stringify({ files }),
  });
}

/**
 * Plain PUT direct to the signed Supabase Storage URL the mint step
 * returned — deliberately NOT routed through `request<T>()`: uploaded
 * files never transit the BFF (see ingest_routes.py's module docstring),
 * and a signed URL is itself the credential, so no `Authorization` header
 * is attached or needed.
 */
export async function putToStorage(url: string, file: File): Promise<void> {
  const response = await fetch(url, { method: "PUT", body: file });
  if (!response.ok) {
    throw new Error(`Upload to storage failed: ${response.status} ${response.statusText}`);
  }
}

export function createIngest(
  tenant: string,
  batchId: string,
  files: Record<string, string>,
): Promise<{ job_id: number }> {
  return request<{ job_id: number }>(`/v1/tenants/${encodeURIComponent(tenant)}/ingest`, {
    method: "POST",
    body: JSON.stringify({ batch_id: batchId, files }),
  });
}

export function getIngest(tenant: string, jobId: number): Promise<IngestJob> {
  return request<IngestJob>(
    `/v1/tenants/${encodeURIComponent(tenant)}/ingest/${encodeURIComponent(String(jobId))}`,
  );
}

export function listIngests(tenant: string = activeTenant()): Promise<IngestHistoryItem[]> {
  return request<IngestHistoryItem[]>(`/v1/tenants/${encodeURIComponent(tenant)}/ingest`);
}

/** Query key shared between `UploadPanel` (invalidates it once a run reaches
 * a terminal state) and `IngestHistory` (queries it) — kept here rather than
 * in either component file so neither has to import from the other. */
export function ingestHistoryQueryKey(tenant: string) {
  return ["ingest-history", tenant] as const;
}
