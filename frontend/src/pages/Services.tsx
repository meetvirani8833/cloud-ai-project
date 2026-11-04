import { ArrowLeftIcon, CaretRightIcon, MagnifyingGlassIcon } from "@phosphor-icons/react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { DemandChart, ReplicaChart } from "../components/charts/charts";
import { FilterSelect, Page, PageHeader } from "../components/layout/AppShell";
import { KpiTile } from "../components/ui/KpiTile";
import { useToast } from "../components/ui/overlay";
import { TickMeter } from "../components/ui/meters";
import { Badge, Button, Card, CardHeader, ChartGhost, Field, Loading, inputClass } from "../components/ui/primitives";
import { EmptyState, ErrorState, QueryView } from "../components/ui/states";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { money, num, pct, triggerLabel } from "../lib/format";
import { useAction, useRecommendations, useReplay, useService, useServices, useTenants, useTimeseries } from "../lib/queries";
import type { Service, ServiceMode } from "../lib/types";
import { RecCard } from "./Recommendations";

const MODE_LABEL: Record<ServiceMode, { label: string; tone: "brand" | "warn" | "neutral"; help: string }> = {
  autopilot: { label: "Autopilot", tone: "brand", help: "Applies changes within the guardrail automatically; larger changes need approval" },
  recommend: { label: "Approval required", tone: "warn", help: "Every change waits for an admin" },
  manual: { label: "Manual", tone: "neutral", help: "CloudOpt only observes; no recommendations" },
};

export function ModeBadge({ mode }: { mode: ServiceMode }) {
  const m = MODE_LABEL[mode];
  return <Badge tone={m.tone} title={m.help}>{m.label}</Badge>;
}

export function ServicesTable({ tenantId, hideTenant }: { tenantId?: string | null; hideTenant?: boolean }) {
  const q = useServices(tenantId);
  const [search, setSearch] = useState("");
  const rows = useMemo(() => (q.data ?? []).filter((s) =>
    `${s.name} ${s.tenant_name ?? ""}`.toLowerCase().includes(search.trim().toLowerCase())), [q.data, search]);
  return (
    <>
      <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
        <div className="relative w-full max-w-xs">
          <MagnifyingGlassIcon size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
          <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search services" className={`${inputClass} pl-9`} aria-label="Search services" />
        </div>
        <span className="text-xs text-muted num">{rows.length} services</span>
      </div>
      <QueryView query={q} loading={<Loading className="h-56" label="Loading services" />}
        isEmpty={(d) => d.length === 0} empty={<EmptyState compact title="No services" />}>
        {() => rows.length === 0 ? <EmptyState compact title="No services match your search" /> : (<>
          <ul className="divide-y divide-line md:hidden">
            {rows.map((s) => {
              const u = s.cpu_util_peak ?? null;
              return (
                <li key={s.id}>
                  <Link to={`/services/${s.id}`} className="flex items-center gap-3 px-4 py-3.5 active:bg-canvas">
                    <div className="min-w-0 flex-1 space-y-1.5">
                      <div className="flex items-center gap-2">
                        <span className="truncate font-medium">{s.name}</span>
                        <ModeBadge mode={s.mode} />
                      </div>
                      <div className="truncate text-xs text-muted">{hideTenant ? triggerLabel[s.trigger] ?? s.trigger : s.tenant_name} · {s.replicas} replicas</div>
                      {u != null && (
                        <div className="flex items-center gap-2.5">
                          <TickMeter value={Math.min(u, 1)} marker={0.9} tone={s.sla_violation ? "bad" : "brand"} label={`${s.name} peak utilisation`} />
                          <span className={`num w-10 shrink-0 text-right text-xs ${s.sla_violation ? "font-semibold text-bad" : "text-ink-2"}`}>{pct(u)}</span>
                        </div>
                      )}
                    </div>
                    <CaretRightIcon size={16} className="shrink-0 text-faint" />
                  </Link>
                </li>
              );
            })}
          </ul>
          <div className="hidden overflow-x-auto md:block">
            <table className="w-full min-w-[820px] text-sm">
              <thead className="text-xs text-muted">
                <tr className="border-b border-line text-left">
                  <th className="px-4 py-3 font-medium">Service</th>
                  {!hideTenant && <th className="px-4 py-3 font-medium">Tenant</th>}
                  <th className="px-4 py-3 font-medium">Mode</th>
                  <th className="px-4 py-3 text-right font-medium">Replicas</th>
                  <th className="px-4 py-3 font-medium" title="Tall tick marks the 90% SLA threshold">Peak CPU (last 5 min)</th>
                  <th className="px-4 py-3 text-right font-medium">Requests/s</th>
                </tr>
              </thead>
              <tbody className="divide-y divide-line">
                {rows.map((s) => {
                  const u = s.cpu_util_peak ?? null;
                  return (
                    <tr key={s.id} className="hover:bg-canvas/60">
                      <td className="px-4 py-3">
                        <Link to={`/services/${s.id}`} className="font-medium hover:text-brand-600">{s.name}</Link>
                        <div className="text-xs text-muted">{triggerLabel[s.trigger] ?? s.trigger} · {s.replica_vcpu} vCPU</div>
                      </td>
                      {!hideTenant && <td className="px-4 py-3 text-ink-2"><Link to={`/tenants/${s.tenant_id}`} className="hover:text-brand-600">{s.tenant_name}</Link></td>}
                      <td className="px-4 py-3"><ModeBadge mode={s.mode} /></td>
                      <td className="num px-4 py-3 text-right font-medium">{s.replicas}
                        {s.baseline_replicas !== s.replicas && <div className="text-xs font-normal text-faint">was {s.baseline_replicas}</div>}</td>
                      <td className="w-56 px-4 py-3">
                        {u == null ? <span className="text-faint">—</span> : (
                          <div className="flex items-center gap-3"><TickMeter value={Math.min(u, 1)} marker={0.9} ticks={30} tone={s.sla_violation ? "bad" : "brand"} label={`${s.name} peak utilisation`} /><span className={`num w-12 text-right ${s.sla_violation ? "font-semibold text-bad" : ""}`}>{pct(u)}</span></div>
                        )}
                      </td>
                      <td className="num px-4 py-3 text-right">{num(s.rps ?? null, 1)}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </>)}
      </QueryView>
    </>
  );
}

export function ServicesPage() {
  const { user } = useAuth();
  const tenants = useTenants();
  const [tenant, setTenant] = useState("all");
  const tenantId = user?.tenant_id ?? (tenant === "all" ? null : tenant);
  return (
    <Page>
      <PageHeader title="Services" subtitle="Every workload CloudOpt is sizing, with live utilisation"
        filters={!user?.tenant_id ? <FilterSelect label="Tenant" value={tenant} onChange={setTenant}
          options={[{ value: "all", label: "All tenants" }, ...(tenants.data ?? []).map((t) => ({ value: t.id, label: t.name }))]} /> : undefined} />
      <Card><ServicesTable tenantId={tenantId} /></Card>
    </Page>
  );
}

const WINDOWS = [{ value: "3", label: "3 hours" }, { value: "6", label: "6 hours" }, { value: "24", label: "24 hours" }, { value: "72", label: "3 days" }];

export function ServiceDetail() {
  const { id = "" } = useParams();
  const { isAdmin } = useAuth();
  const [hours, setHours] = useState("6");
  const svc = useService(id);
  const ts = useTimeseries(id, Number(hours));
  const recs = useRecommendations({ service_id: id, status: "pending", limit: 5 });
  if (svc.error) return <Page><Card><ErrorState error={svc.error} onRetry={() => svc.refetch()} /></Card></Page>;
  const s = svc.data;
  const pts = ts.data?.points ?? [];
  const last = pts[pts.length - 1];
  const next = ts.data?.forecast.find((f) => f.horizon_minutes === 60) ?? ts.data?.forecast[ts.data.forecast.length - 1];
  const windowCost = pts.reduce((a, p) => a + p.cost, 0);
  const windowBase = pts.reduce((a, p) => a + p.baseline_cost, 0);
  const violations = pts.filter((p) => p.sla_violation).length;
  return (
    <Page>
      <Link to={s ? `/tenants/${s.tenant_id}` : "/services"} className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink"><ArrowLeftIcon size={15} /> {s?.tenant_name ?? "Services"}</Link>
      <PageHeader title={s?.name ?? "…"}
        subtitle={s ? <span className="inline-flex flex-wrap items-center gap-2">{triggerLabel[s.trigger] ?? s.trigger} · {s.replica_vcpu} vCPU / {s.replica_mem_gb} GB replicas <ModeBadge mode={s.mode} /></span> : undefined}
        filters={<FilterSelect label="Window" value={hours} onChange={setHours} options={WINDOWS} />} />
      <div className="space-y-5">
        <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
          <KpiTile loading={!last} label="Replicas now" value={last?.replicas ?? "—"} deltaLabel={s ? `${s.baseline_replicas} before CloudOpt` : undefined} />
          <KpiTile loading={!last} label="Peak CPU (last 5 min)" value={pct(last?.cpu_util_peak)} deltaLabel={ts.data ? `SLA threshold ${pct(ts.data.sla_capacity_factor)}` : undefined} />
          <KpiTile loading={!next} label="Forecast in 1 hour" value={next ? num(next.p50, 2) : "—"} suffix="workers"
            deltaLabel={next ? `80% band ${num(next.p10, 2)}–${num(next.p90, 2)}` : "Forecast warming up"} />
          <KpiTile loading={ts.isLoading} label={`Cost (${hours} h window)`} value={money(windowCost, { cents: true })}
            deltaLabel={windowBase ? `${pct((windowBase - windowCost) / windowBase)} below static · ${violations} violations` : undefined} />
        </div>

        {recs.data && recs.data.items.length > 0 && <div className="space-y-3">{recs.data.items.map((r) => <RecCard key={r.id} rec={r} compact />)}</div>}

        <Card>
          <CardHeader title="Demand vs. capacity" subtitle="Busiest minute per 5-minute slot, the 5-minute forecast band, and the next-hour forecast" />
          <div className="px-3 pb-4 pt-2">
            {ts.isLoading ? <ChartGhost height={320} label="Loading telemetry" /> : ts.error && !ts.data ? <ErrorState error={ts.error} onRetry={() => ts.refetch()} compact /> :
              pts.length === 0 ? <EmptyState compact title="No telemetry in this window yet" /> :
                <DemandChart points={pts} future={ts.data?.forecast ?? []} slaFactor={ts.data?.sla_capacity_factor ?? 0.9} hours={Number(hours)} />}
          </div>
        </Card>
        <Card>
          <CardHeader title="Replicas and utilisation" />
          <div className="px-3 pb-4 pt-2">{pts.length ? <ReplicaChart points={pts} hours={Number(hours)} /> : <ChartGhost height={200} label="Loading replicas" />}</div>
        </Card>
        {isAdmin && s && <ServiceSettings service={s} />}
      </div>
    </Page>
  );
}

function ServiceSettings({ service }: { service: Service }) {
  const toast = useToast();
  const rlAvailable = useReplay().data?.rl_available ?? false;
  const init = { mode: service.mode, engine: service.decision_engine, min: service.min_replicas, max: service.max_replicas, step: service.autopilot_max_step_pct };
  const [f, setF] = useState(init);
  const dirty = JSON.stringify(f) !== JSON.stringify(init);
  const err = f.min < 1 ? "Minimum must be at least 1" : f.max < f.min ? "Maximum must be ≥ minimum" : f.step <= 0 || f.step > 500 ? "Guardrail must be 1–500%" : null;
  const save = useAction((b: object) => api(`/api/services/${service.id}`, { method: "PATCH", json: b }), [["service", service.id]]);
  return (
    <Card>
      <CardHeader title="Scaling policy" subtitle="How much CloudOpt may change on its own" />
      <form className="grid grid-cols-1 gap-4 p-4 sm:grid-cols-2 sm:p-5 lg:grid-cols-5" onSubmit={(e) => {
        e.preventDefault();
        if (err) return;
        save.mutate({ mode: f.mode, decision_engine: f.engine, min_replicas: f.min, max_replicas: f.max, autopilot_max_step_pct: f.step }, {
          onSuccess: () => toast("good", "Scaling policy saved"), onError: (e) => toast("bad", (e as Error).message),
        });
      }}>
        <Field label="Mode" htmlFor="mode" hint={MODE_LABEL[f.mode].help}>
          <select id="mode" className={inputClass} value={f.mode} onChange={(e) => setF({ ...f, mode: e.target.value as ServiceMode })}>
            <option value="autopilot">Autopilot</option><option value="recommend">Approval required</option><option value="manual">Manual (observe only)</option>
          </select>
        </Field>
        <Field label="Decision engine" htmlFor="engine"
          hint={f.engine === "rl" ? "PPO agent trained in simulation; same guardrails apply" : "XGBoost p90 forecast with anomaly guard"}>
          <select id="engine" className={inputClass} value={f.engine} disabled={f.mode === "manual"}
            onChange={(e) => setF({ ...f, engine: e.target.value as "forecast" | "rl" })}>
            <option value="forecast">Forecast policy (recommended)</option>
            <option value="rl" disabled={!rlAvailable}>RL agent (PPO){rlAvailable ? "" : " (not deployed)"}</option>
          </select>
        </Field>
        <Field label="Autopilot guardrail (% per step)" htmlFor="step" hint="Bigger changes are sent for approval">
          <input id="step" type="number" min={1} max={500} className={inputClass} disabled={f.mode !== "autopilot"} value={f.step} onChange={(e) => setF({ ...f, step: Number(e.target.value) })} />
        </Field>
        <Field label="Minimum replicas" htmlFor="min">
          <input id="min" type="number" min={1} className={inputClass} value={f.min} onChange={(e) => setF({ ...f, min: Number(e.target.value) })} />
        </Field>
        <Field label="Maximum replicas" htmlFor="max" error={err}>
          <input id="max" type="number" min={1} className={inputClass} value={f.max} onChange={(e) => setF({ ...f, max: Number(e.target.value) })} />
        </Field>
        <div className="flex justify-end gap-2 sm:col-span-2 lg:col-span-5">
          <Button type="button" disabled={!dirty || save.isPending} onClick={() => setF(init)}>Discard</Button>
          <Button type="submit" variant="primary" disabled={!dirty || !!err} loading={save.isPending}>Save policy</Button>
        </div>
      </form>
    </Card>
  );
}
