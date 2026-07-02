import { useQuery } from "@tanstack/react-query";
import { bffClient, DEFAULT_TENANT } from "@/lib/api/client";
import type { ForecastSummary } from "@/lib/api/types";

export function forecastQueryKey(tenant: string) {
  return ["forecast", tenant] as const;
}

/** Read-heavy portfolio aggregate — `staleTime: 60s` (Slice S8 hardening); see useDashboard.ts. */
export function useForecast(tenant: string = DEFAULT_TENANT) {
  return useQuery<ForecastSummary>({
    queryKey: forecastQueryKey(tenant),
    queryFn: () => bffClient.getForecast(tenant),
    staleTime: 60_000,
  });
}
