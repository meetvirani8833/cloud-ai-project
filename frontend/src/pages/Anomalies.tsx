import { CheckCircleIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { FilterSelect, Page, PageHeader } from "../components/layout/AppShell";
import { Badge, Card, Loading } from "../components/ui/primitives";
import { EmptyState, QueryView } from "../components/ui/states";
import { useAuth } from "../lib/auth";
import { ago, num, simTime } from "../lib/format";
import { useAnomalies, useReplay, useServices, useTenants } from "../lib/queries";

export default function Anomalies() {
  const { user } = useAuth();
  const tenants = useTenants();
  const [tenant, setTenant] = useState("all");
  const tenantId = user?.tenant_id ?? (tenant === "all" ? null : tenant);
  const q = useAnomalies({ tenant_id: tenantId, limit: 200 });
  const services = useServices(tenantId);
  const now = useReplay().data?.sim_time;
  const svc = (id: string) => services.data?.find((s) => s.id === id);
  return (
    <Page>
      <PageHeader title="Anomalies"
        subtitle="Demand the 5-minute forecaster did not expect. Predictable surges (e.g. a morning ramp) are not flagged: they stay inside the forecast band."
        filters={!user?.tenant_id ? <FilterSelect label="Tenant" value={tenant} onChange={setTenant}
          options={[{ value: "all", label: "All tenants" }, ...(tenants.data ?? []).map((t) => ({ value: t.id, label: t.name }))]} /> : undefined} />
      <Card>
        <QueryView query={q} loading={<Loading className="h-56" label="Loading anomalies" />}
          isEmpty={(d) => d.length === 0}
          empty={<EmptyState icon={<CheckCircleIcon size={30} weight="light" className="text-good" />} title="No anomalies detected"
            body="Admins can inject a synthetic spike from Settings → Replay & scenarios to see detection in action." />}>
          {(rows) => (<>
            <ul className="divide-y divide-line md:hidden">
              {rows.map((a) => {
                const s = svc(a.service_id);
                const ratio = a.kind === "spike" ? a.observed / Math.max(a.expected, 1e-6) : a.expected / Math.max(a.observed, 1e-6);
                return (
                  <li key={a.id}>
                    <Link to={`/services/${a.service_id}`} className="flex items-start gap-3 px-4 py-3.5 active:bg-canvas">
                      <div className="min-w-0 flex-1">
                        <div className="truncate font-medium">{s?.name ?? a.service_id}</div>
                        <div className="truncate text-xs text-muted">{s?.tenant_name} · {ago(a.time, now)}{a.injected ? " · scenario" : ""}</div>
                        <div className="num mt-1 text-xs text-ink-2">{num(a.observed, 2)} workers vs {num(a.expected, 2)} expected</div>
                      </div>
                      <span className={`num shrink-0 rounded-md px-2 py-1 text-xs font-semibold ${a.kind === "spike" ? "bg-bad-soft text-bad" : "bg-warn-soft text-warn"}`}>
                        {a.kind === "spike" ? "▲" : "▼"} {ratio.toFixed(1)}×
                      </span>
                    </Link>
                  </li>
                );
              })}
            </ul>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full min-w-[760px] text-sm">
                <thead className="text-xs text-muted">
                  <tr className="border-b border-line text-left">
                    <th className="px-4 py-3 font-medium">Detected</th><th className="px-4 py-3 font-medium">Service</th>
                    <th className="px-4 py-3 font-medium">Type</th><th className="px-4 py-3 text-right font-medium">Observed peak</th>
                    <th className="px-4 py-3 text-right font-medium">Expected (p50 / p95)</th><th className="px-4 py-3 text-right font-medium">vs p95</th>
                    <th className="px-4 py-3 font-medium">Source</th>
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {rows.map((a) => {
                    const s = svc(a.service_id);
                    return (
                      <tr key={a.id} className="hover:bg-canvas/60">
                        <td className="px-4 py-3"><div className="num">{simTime(a.time)}</div><div className="text-xs text-muted">{ago(a.time, now)}</div></td>
                        <td className="px-4 py-3"><Link to={`/services/${a.service_id}`} className="font-medium hover:text-brand-600">{s?.name ?? a.service_id}</Link>
                          <div className="text-xs text-muted">{s?.tenant_name}</div></td>
                        <td className="px-4 py-3"><Badge tone={a.kind === "spike" ? "bad" : "warn"}>{a.kind === "spike" ? "Spike" : "Drop"}</Badge></td>
                        <td className="num px-4 py-3 text-right font-medium">{num(a.observed, 2)}</td>
                        <td className="num px-4 py-3 text-right text-ink-2">{num(a.expected, 2)} / {num(a.expected_high, 2)}</td>
                        <td className="num px-4 py-3 text-right">{a.score >= 1 ? `${num(a.score, 1)}×` : `${num(a.score, 2)}×`}</td>
                        <td className="px-4 py-3">{a.injected ? <Badge tone="brand">Injected scenario</Badge> : <Badge>Trace data</Badge>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>)}
        </QueryView>
      </Card>
    </Page>
  );
}
