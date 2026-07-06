import type {
  ActionResult,
  AutonomyTier,
  BulkApproveFilter,
  BulkApproveResult,
  BvrReport,
  DashboardSummary,
  DeferRequest,
  FeedsSummary,
  ForecastSummary,
  HistoryEntry,
  KillSwitchState,
  PagedQueue,
  PartContext,
  QueueSortKey,
  RecommendationDetail,
  RecommendationType,
  RejectReason,
  RollbackRequest,
  RollbackResult,
  SaveScenarioRequest,
  Scenario,
  ScenarioAuditEvent,
  ScenarioParams,
  ScenarioSolveResult,
  TaskStatus,
} from "@/lib/api/types";

export const DEFAULT_BFF_URL = "http://localhost:8001";
export const DEFAULT_TENANT = "acme";

const BASE_URL: string =
  (import.meta.env.VITE_BFF_URL as string | undefined) ?? DEFAULT_BFF_URL;

export class ApiError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly url: string,
  ) {
    super(message);
    this.name = "ApiError";
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const url = `${BASE_URL}${path}`;
  const response = await fetch(url, {
    headers: { "Content-Type": "application/json", ...init?.headers },
    ...init,
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      detail = body?.detail ?? detail;
    } catch {
      // response had no JSON body — fall back to statusText
    }
    throw new ApiError(`Request to ${path} failed: ${detail}`, response.status, url);
  }

  return (await response.json()) as T;
}

export interface RecommendationsExportParams {
  status?: TaskStatus;
  sortBy?: QueueSortKey;
  sortDir?: "asc" | "desc";
  tier?: AutonomyTier;
  type?: RecommendationType;
  aogMin?: number;
}

/**
 * Full URL to the BFF's CSV export route. Mirrors `getQueue`'s query-string
 * shape (always emits status/sort_by/sort_dir, omits tier/type/aog_min when
 * undefined) but has no limit/offset — the export covers the whole filtered
 * set. Consumed as an `<a href>` (browser navigation triggers the download via
 * the response's Content-Disposition header), not `fetch()`.
 */
export function recommendationsExportUrl(
  params: RecommendationsExportParams = {},
  tenant: string = DEFAULT_TENANT,
): string {
  const {
    status = "pending",
    sortBy = "priority_score",
    sortDir = "desc",
    tier,
    type,
    aogMin,
  } = params;
  const search = new URLSearchParams({ status, sort_by: sortBy, sort_dir: sortDir });
  if (tier !== undefined) search.set("tier", String(tier));
  if (type !== undefined) search.set("type", type);
  if (aogMin !== undefined) search.set("aog_min", String(aogMin));
  return `${BASE_URL}/v1/tenants/${encodeURIComponent(tenant)}/recommendations/export.csv?${search.toString()}`;
}

/**
 * Typed fetch wrapper over the Planner-UI BFF
 * (services/agent-spine/src/trax_io_spine/bff/app.py).
 */
export const bffClient = {
  baseUrl: BASE_URL,

  getDashboard(tenant: string = DEFAULT_TENANT): Promise<DashboardSummary> {
    return request<DashboardSummary>(`/v1/tenants/${encodeURIComponent(tenant)}/dashboard`);
  },

  getPartContext(
    pn: string,
    location: string,
    tenant: string = DEFAULT_TENANT,
  ): Promise<PartContext> {
    return request<PartContext>(
      `/v1/tenants/${encodeURIComponent(tenant)}/parts/${encodeURIComponent(pn)}/${encodeURIComponent(location)}`,
    );
  },

  getHistory(
    pn: string,
    location: string,
    tenant: string = DEFAULT_TENANT,
  ): Promise<HistoryEntry[]> {
    const params = new URLSearchParams({ pn, location });
    return request<HistoryEntry[]>(
      `/v1/tenants/${encodeURIComponent(tenant)}/history?${params.toString()}`,
    );
  },

  rollback(req: RollbackRequest, tenant: string = DEFAULT_TENANT): Promise<RollbackResult> {
    return request<RollbackResult>(`/v1/tenants/${encodeURIComponent(tenant)}/rollback`, {
      method: "POST",
      body: JSON.stringify(req),
    });
  },

  /**
   * Slice S3 — Workbench + AI Recommendations.
   * Mirrors services/agent-spine/src/trax_io_spine/bff/app.py's
   * `/v1/tenants/{tenant}/recommendations*` + `/killswitch` routes.
   */

  /**
   * `sortBy`/`sortDir`/`tier`/`type`/`aogMin` are task F4's server-side
   * sort/filter params on `GET .../recommendations` (BFF commit 0d3c04d) —
   * all optional; omitted params fall back to the BFF's own defaults
   * (priority_score desc, no tier/type/aog_min filter), reproducing the
   * pre-F4 behavior byte-for-byte for existing ≤4-arg callers.
   */
  getQueue(
    status: TaskStatus = "pending",
    limit: number = 50,
    offset: number = 0,
    tenant: string = DEFAULT_TENANT,
    sortBy: QueueSortKey = "priority_score",
    sortDir: "asc" | "desc" = "desc",
    tier?: AutonomyTier,
    type?: RecommendationType,
    aogMin?: number,
  ): Promise<PagedQueue> {
    const params = new URLSearchParams({
      status,
      limit: String(limit),
      offset: String(offset),
      sort_by: sortBy,
      sort_dir: sortDir,
    });
    if (tier !== undefined) params.set("tier", String(tier));
    if (type !== undefined) params.set("type", type);
    if (aogMin !== undefined) params.set("aog_min", String(aogMin));
    return request<PagedQueue>(
      `/v1/tenants/${encodeURIComponent(tenant)}/recommendations?${params.toString()}`,
    );
  },

  getRecommendation(
    recommendationId: string,
    tenant: string = DEFAULT_TENANT,
  ): Promise<RecommendationDetail> {
    return request<RecommendationDetail>(
      `/v1/tenants/${encodeURIComponent(tenant)}/recommendations/${encodeURIComponent(recommendationId)}`,
    );
  },

  approve(recommendationId: string, tenant: string = DEFAULT_TENANT): Promise<ActionResult> {
    return request<ActionResult>(
      `/v1/tenants/${encodeURIComponent(tenant)}/recommendations/${encodeURIComponent(recommendationId)}/approve`,
      { method: "POST" },
    );
  },

  reject(
    recommendationId: string,
    reason: RejectReason,
    detail: string = "",
    tenant: string = DEFAULT_TENANT,
  ): Promise<ActionResult> {
    return request<ActionResult>(
      `/v1/tenants/${encodeURIComponent(tenant)}/recommendations/${encodeURIComponent(recommendationId)}/reject`,
      { method: "POST", body: JSON.stringify({ reason, detail }) },
    );
  },

  defer(
    recommendationId: string,
    until?: string | null,
    tenant: string = DEFAULT_TENANT,
  ): Promise<ActionResult> {
    const body: DeferRequest = until ? { until } : {};
    return request<ActionResult>(
      `/v1/tenants/${encodeURIComponent(tenant)}/recommendations/${encodeURIComponent(recommendationId)}/defer`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },

  bulkApprove(
    filter: BulkApproveFilter,
    tenant: string = DEFAULT_TENANT,
  ): Promise<BulkApproveResult> {
    return request<BulkApproveResult>(
      `/v1/tenants/${encodeURIComponent(tenant)}/recommendations/bulk-approve`,
      { method: "POST", body: JSON.stringify(filter) },
    );
  },

  getKillSwitch(tenant: string = DEFAULT_TENANT): Promise<KillSwitchState> {
    return request<KillSwitchState>(`/v1/tenants/${encodeURIComponent(tenant)}/killswitch`);
  },

  setKillSwitch(
    engaged: boolean,
    tenant: string = DEFAULT_TENANT,
  ): Promise<KillSwitchState> {
    return request<KillSwitchState>(`/v1/tenants/${encodeURIComponent(tenant)}/killswitch`, {
      method: "POST",
      body: JSON.stringify({ engaged }),
    });
  },

  /**
   * Slice S5 — Forecast & Service Levels.
   * Mirrors services/agent-spine/src/trax_io_spine/bff/app.py's
   * `/v1/tenants/{tenant}/forecast` route.
   */
  getForecast(tenant: string = DEFAULT_TENANT): Promise<ForecastSummary> {
    return request<ForecastSummary>(`/v1/tenants/${encodeURIComponent(tenant)}/forecast`);
  },

  /**
   * Slice S6 — What-If Scenarios.
   * Mirrors services/agent-spine/src/trax_io_spine/bff/app.py's
   * `/v1/tenants/{tenant}/scenarios*` routes.
   */

  solveScenario(
    params: ScenarioParams,
    tenant: string = DEFAULT_TENANT,
  ): Promise<ScenarioSolveResult> {
    return request<ScenarioSolveResult>(
      `/v1/tenants/${encodeURIComponent(tenant)}/scenarios/solve`,
      { method: "POST", body: JSON.stringify(params) },
    );
  },

  saveScenario(
    body: SaveScenarioRequest,
    tenant: string = DEFAULT_TENANT,
  ): Promise<Scenario> {
    return request<Scenario>(`/v1/tenants/${encodeURIComponent(tenant)}/scenarios`, {
      method: "POST",
      body: JSON.stringify(body),
    });
  },

  listScenarios(tenant: string = DEFAULT_TENANT): Promise<Scenario[]> {
    return request<Scenario[]>(`/v1/tenants/${encodeURIComponent(tenant)}/scenarios`);
  },

  getScenario(scenarioId: string, tenant: string = DEFAULT_TENANT): Promise<Scenario> {
    return request<Scenario>(
      `/v1/tenants/${encodeURIComponent(tenant)}/scenarios/${encodeURIComponent(scenarioId)}`,
    );
  },

  deleteScenario(
    scenarioId: string,
    tenant: string = DEFAULT_TENANT,
  ): Promise<{ deleted: string }> {
    return request<{ deleted: string }>(
      `/v1/tenants/${encodeURIComponent(tenant)}/scenarios/${encodeURIComponent(scenarioId)}`,
      { method: "DELETE" },
    );
  },

  commitScenario(
    scenarioId: string,
    tenant: string = DEFAULT_TENANT,
  ): Promise<ScenarioAuditEvent> {
    return request<ScenarioAuditEvent>(
      `/v1/tenants/${encodeURIComponent(tenant)}/scenarios/${encodeURIComponent(scenarioId)}/commit`,
      { method: "POST" },
    );
  },

  /**
   * Slice S7 — Data & Connections / feed health.
   * Mirrors services/agent-spine/src/trax_io_spine/bff/app.py's
   * `/v1/tenants/{tenant}/feeds` route.
   */
  getFeeds(tenant: string = DEFAULT_TENANT): Promise<FeedsSummary> {
    return request<FeedsSummary>(`/v1/tenants/${encodeURIComponent(tenant)}/feeds`);
  },

  /**
   * Slice S8 — Business Value Report (BVR).
   * Mirrors services/agent-spine/src/trax_io_spine/bff/app.py's
   * `/v1/tenants/{tenant}/reports/bvr` route.
   */
  getBvr(tenant: string = DEFAULT_TENANT): Promise<BvrReport> {
    return request<BvrReport>(`/v1/tenants/${encodeURIComponent(tenant)}/reports/bvr`);
  },

  /**
   * URL to a BVR document (HTML or PDF), consumed as an `<a href>` — a browser
   * navigation triggers the render/download via the BFF's Content-Disposition,
   * not `fetch()` (same pattern/rationale as `recommendationsExportUrl`).
   */
  bvrDocumentUrl(tenant: string = DEFAULT_TENANT, kind: "html" | "pdf"): string {
    return `${BASE_URL}/v1/tenants/${encodeURIComponent(tenant)}/reports/bvr.${kind}`;
  },
};
