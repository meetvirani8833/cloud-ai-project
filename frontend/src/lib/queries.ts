import { keepPreviousData, useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "./api";
import type {
  Anomaly, AuditEntry, Evaluation, Overview, Paged, Pricing, Recommendation, ReplayStatus, Service, Tenant,
  TenantRow, Timeseries, User,
} from "./types";

// Live data refreshes about once per replayed slot.
const LIVE = 5000;

const qs = (params: Record<string, string | number | null | undefined>) => {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== null && v !== undefined && v !== "") p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : "";
};

export const useReplay = () =>
  useQuery({ queryKey: ["replay"], queryFn: () => api<ReplayStatus>("/api/replay"), refetchInterval: LIVE });

export const useOverview = (tenantId: string | null, hours: number) =>
  useQuery({
    queryKey: ["overview", tenantId, hours],
    queryFn: () => api<Overview>(`/api/overview${qs({ tenant_id: tenantId, hours })}`),
    refetchInterval: LIVE,
    placeholderData: keepPreviousData,
  });

export const useTenants = () =>
  useQuery({ queryKey: ["tenants"], queryFn: () => api<TenantRow[]>("/api/tenants"), refetchInterval: LIVE });

export const useTenant = (id: string) =>
  useQuery({ queryKey: ["tenant", id], queryFn: () => api<Tenant>(`/api/tenants/${id}`) });

export const useServices = (tenantId?: string | null) =>
  useQuery({
    queryKey: ["services", tenantId ?? null],
    queryFn: () => api<Service[]>(`/api/services${qs({ tenant_id: tenantId })}`),
    refetchInterval: LIVE,
  });

export const useService = (id: string) =>
  useQuery({ queryKey: ["service", id], queryFn: () => api<Service>(`/api/services/${id}`), refetchInterval: LIVE });

export const useTimeseries = (id: string, hours: number) =>
  useQuery({
    queryKey: ["timeseries", id, hours],
    queryFn: () => api<Timeseries>(`/api/services/${id}/timeseries${qs({ hours })}`),
    refetchInterval: LIVE,
    placeholderData: keepPreviousData,
  });

export const useRecommendations = (params: { status?: string; tenant_id?: string | null; service_id?: string; limit?: number; offset?: number }) =>
  useQuery({
    queryKey: ["recommendations", params],
    queryFn: () => api<Paged<Recommendation>>(`/api/recommendations${qs(params)}`),
    refetchInterval: LIVE,
    placeholderData: keepPreviousData,
  });

export const useAnomalies = (params: { tenant_id?: string | null; service_id?: string; limit?: number }) =>
  useQuery({
    queryKey: ["anomalies", params],
    queryFn: () => api<Anomaly[]>(`/api/anomalies${qs(params)}`),
    refetchInterval: LIVE,
  });

export const usePricing = () => useQuery({ queryKey: ["pricing"], queryFn: () => api<Pricing[]>("/api/pricing") });
export const useUsers = () => useQuery({ queryKey: ["users"], queryFn: () => api<User[]>("/api/users") });
export const useAudit = () =>
  useQuery({ queryKey: ["audit"], queryFn: () => api<AuditEntry[]>("/api/audit?limit=200"), refetchInterval: 15000 });
export const useEvaluation = () =>
  useQuery({ queryKey: ["evaluation"], queryFn: () => api<Evaluation>("/api/reports/evaluation"), staleTime: 60000 });

/** Mutation that refreshes the live views afterwards. */
export function useAction<TVars, TResult = unknown>(fn: (v: TVars) => Promise<TResult>, invalidate: string[][] = []) {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: fn,
    onSettled: () => {
      for (const key of [["replay"], ["overview"], ["recommendations"], ["services"], ["tenants"], ...invalidate])
        qc.invalidateQueries({ queryKey: key });
    },
  });
}
