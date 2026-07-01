import type {
  ActionResult,
  BulkApproveFilter,
  BulkApproveResult,
  DashboardSummary,
  DeferRequest,
  ForecastSummary,
  KillSwitchState,
  PagedQueue,
  PartContext,
  RecommendationDetail,
  RejectReason,
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

  /**
   * Slice S3 — Workbench + AI Recommendations.
   * Mirrors services/agent-spine/src/trax_io_spine/bff/app.py's
   * `/v1/tenants/{tenant}/recommendations*` + `/killswitch` routes.
   */

  getQueue(
    status: TaskStatus = "pending",
    limit: number = 50,
    offset: number = 0,
    tenant: string = DEFAULT_TENANT,
  ): Promise<PagedQueue> {
    const params = new URLSearchParams({
      status,
      limit: String(limit),
      offset: String(offset),
    });
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
};
