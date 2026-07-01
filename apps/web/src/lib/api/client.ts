import type { DashboardSummary } from "@/lib/api/types";

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
};
