import { ArrowLeftIcon, CaretRightIcon, MagnifyingGlassIcon } from "@phosphor-icons/react";
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router-dom";
import { Page, PageHeader } from "../components/layout/AppShell";
import { KpiTile } from "../components/ui/KpiTile";
import { useToast } from "../components/ui/overlay";
import { HeatStrip, TickMeter } from "../components/ui/meters";
import { Button, Card, CardHeader, Field, Loading, inputClass } from "../components/ui/primitives";
import { EmptyState, ErrorState, QueryView } from "../components/ui/states";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { money, pct } from "../lib/format";
import { useAction, useOverview, usePricing, useTenant, useTenants } from "../lib/queries";
import type { Tenant } from "../lib/types";
import { ServicesTable } from "./Services";

type SortKey = "name" | "savings_pct" | "hourly_run_rate" | "sla_violation_rate_24h" | "pending_recommendations";

export function TenantsPage() {
  const q = useTenants();
  const [search, setSearch] = useState("");
  const [sort, setSort] = useState<{ key: SortKey; dir: 1 | -1 }>({ key: "savings_pct", dir: -1 });
  const rows = useMemo(() => {
    const r = (q.data ?? []).filter((t) => t.name.toLowerCase().includes(search.trim().toLowerCase()));
    return r.sort((a, b) => {
      const va = a[sort.key] ?? -Infinity, vb = b[sort.key] ?? -Infinity;
      return (typeof va === "string" ? va.localeCompare(vb as string) : (va as number) - (vb as number)) * sort.dir;
    });
  }, [q.data, search, sort]);
  const th = (key: SortKey, label: string, right = false) => (
    <th className={`px-4 py-3 font-medium ${right ? "text-right" : "text-left"}`} aria-sort={sort.key === key ? (sort.dir === 1 ? "ascending" : "descending") : "none"}>
      <button className="inline-flex items-center gap-1 hover:text-ink" onClick={() => setSort((s) => ({ key, dir: s.key === key ? (s.dir === 1 ? -1 : 1) : -1 }))}>
        {label}{sort.key === key && <span aria-hidden>{sort.dir === 1 ? "▲" : "▼"}</span>}
      </button>
    </th>
  );
  return (
    <Page>
      <PageHeader title="Tenants" subtitle="Each tenant is a customer company whose services CloudOpt right-sizes" />
      <Card>
        <div className="flex flex-wrap items-center justify-between gap-3 border-b border-line px-4 py-3">
          <div className="relative w-full max-w-xs">
            <MagnifyingGlassIcon size={16} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-faint" />
            <input value={search} onChange={(e) => setSearch(e.target.value)} placeholder="Search tenants" className={`${inputClass} pl-9`} aria-label="Search tenants" />
          </div>
          <span className="text-xs text-muted num">{rows.length} of {q.data?.length ?? 0} tenants</span>
        </div>
        <QueryView query={q} loading={<Loading className="h-56" label="Loading tenants" />}
          isEmpty={(d) => d.length === 0} empty={<EmptyState title="No tenants yet" />}>
          {() => rows.length === 0 ? <EmptyState compact title="No tenants match your search" body={`Nothing found for “${search}”.`} /> : (<>
            <ul className="divide-y divide-line md:hidden">
              {rows.map((t) => (
                <li key={t.id}>
                  <Link to={`/tenants/${t.id}`} className="flex items-center gap-3 px-4 py-3.5 active:bg-canvas">
                    <div className="min-w-0 flex-1 space-y-1.5">
                      <div className="flex items-baseline justify-between gap-3">
                        <span className="truncate font-medium">{t.name}</span>
                        <span className="num shrink-0 text-sm font-semibold text-good">{pct(t.savings_pct, 0, { ratio: false })} saved</span>
                      </div>
                      <TickMeter value={(t.savings_pct ?? 0) / 100} tone="good" label={`${t.name} savings`} />
                      <div className="num flex justify-between text-xs text-muted">
                        <span>{money(t.hourly_run_rate, { cents: true })}/hr · {t.services} services</span>
                        <span className={(t.sla_violation_rate_24h ?? 0) > 0.01 ? "text-bad" : ""}>{pct(t.sla_violation_rate_24h, 2)} SLA</span>
                      </div>
                      <HeatStrip values={t.violations_by_hour} start={t.hours_start} label={`${t.name}: SLA violations by hour`} />
                    </div>
                    <CaretRightIcon size={16} className="shrink-0 text-faint" />
                  </Link>
                </li>
              ))}
            </ul>
            <div className="hidden overflow-x-auto md:block">
              <table className="w-full min-w-[760px] text-sm">
                <thead className="text-xs text-muted">
                  <tr className="border-b border-line">
                    {th("name", "Tenant")}{th("savings_pct", "Savings")}{th("hourly_run_rate", "Run-rate", true)}
                    {th("sla_violation_rate_24h", "SLA violations (24 h)", true)}{th("pending_recommendations", "Pending", true)}
                  </tr>
                </thead>
                <tbody className="divide-y divide-line">
                  {rows.map((t) => (
                    <tr key={t.id} className="hover:bg-canvas/60">
                      <td className="px-4 py-3">
                        <Link to={`/tenants/${t.id}`} className="font-medium hover:text-brand-600">{t.name}</Link>
                        <div className="text-xs text-muted">{t.services} services</div>
                      </td>
                      <td className="w-56 px-4 py-3">
                        <div className="flex items-center gap-3"><TickMeter value={(t.savings_pct ?? 0) / 100} tone="good" ticks={30} label={`${t.name} savings`} /><span className="num w-12 text-right font-medium">{pct(t.savings_pct, 0, { ratio: false })}</span></div>
                      </td>
                      <td className="num px-4 py-3 text-right">{money(t.hourly_run_rate, { cents: true })}<span className="text-muted">/hr</span>
                        <div className="text-xs text-faint line-through">{money(t.baseline_hourly_run_rate, { cents: true })}</div></td>
                      <td className="px-4 py-3">
                        <div className="flex items-center justify-end gap-3">
                          <div className="w-36"><HeatStrip values={t.violations_by_hour} start={t.hours_start} label={`${t.name}: SLA violations by hour`} /></div>
                          <span className={`num w-14 text-right ${(t.sla_violation_rate_24h ?? 0) > 0.01 ? "font-semibold text-bad" : ""}`}>{pct(t.sla_violation_rate_24h, 2)}</span>
                        </div>
                      </td>
                      <td className="num px-4 py-3 text-right">{t.pending_recommendations || <span className="text-faint">0</span>}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>)}
        </QueryView>
      </Card>
    </Page>
  );
}

export function TenantDetail() {
  const { id = "" } = useParams();
  const { isAdmin } = useAuth();
  const tenant = useTenant(id);
  const ov = useOverview(id, 24);
  if (tenant.error) return <Page><Card><ErrorState error={tenant.error} onRetry={() => tenant.refetch()} /></Card></Page>;
  const d = ov.data;
  return (
    <Page>
      <Link to="/tenants" className="mb-3 inline-flex items-center gap-1.5 text-sm text-muted hover:text-ink"><ArrowLeftIcon size={15} /> Tenants</Link>
      <PageHeader title={tenant.data?.name ?? "…"} subtitle={tenant.data ? `Tenant ${tenant.data.id} · SLA threshold ${pct(tenant.data.sla_util)} · target ${pct(tenant.data.target_util)}` : undefined} />
      <div className="space-y-5">
        <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
          <KpiTile loading={!d} label="Current run-rate" value={money(d?.hourly_run_rate, { cents: true })} suffix="/hr"
            deltaLabel={d ? `was ${money(d.baseline_hourly_run_rate, { cents: true })}/hr` : undefined} />
          <KpiTile loading={!d} label="Projected monthly savings" value={money(d?.projected_monthly_savings)} />
          <KpiTile loading={!d} label="SLA violations (24 h)" value={pct(d?.sla_violation_rate, 2)} />
          <KpiTile loading={!d} label="Pending approvals" value={d?.pending_recommendations ?? "—"} />
        </div>
        <Card>
          <CardHeader title="Services" />
          <ServicesTable tenantId={id} hideTenant />
        </Card>
        {isAdmin && tenant.data && <TenantSettings tenant={tenant.data} />}
      </div>
    </Page>
  );
}

function TenantSettings({ tenant }: { tenant: Tenant }) {
  const pricing = usePricing();
  const toast = useToast();
  const [form, setForm] = useState({ pricing_key: tenant.pricing_key, sla: Math.round(tenant.sla_util * 100), target: Math.round(tenant.target_util * 100) });
  const err = form.target >= form.sla ? "Target must be below the SLA threshold" : form.sla > 100 || form.sla < 31 ? "SLA threshold must be 31–100%" : form.target < 21 ? "Target must be at least 21%" : null;
  const dirty = form.pricing_key !== tenant.pricing_key || form.sla !== Math.round(tenant.sla_util * 100) || form.target !== Math.round(tenant.target_util * 100);
  const save = useAction((body: object) => api(`/api/tenants/${tenant.id}`, { method: "PATCH", json: body }), [["tenant", tenant.id]]);
  return (
    <Card>
      <CardHeader title="Tenant settings" subtitle="Changes apply from the next replayed interval" />
      <form className="grid grid-cols-1 gap-4 p-4 sm:p-5 md:grid-cols-3" onSubmit={(e) => {
        e.preventDefault();
        if (err) return;
        save.mutate({ pricing_key: form.pricing_key, sla_util: form.sla / 100, target_util: form.target / 100 }, {
          onSuccess: () => toast("good", "Tenant settings saved"), onError: (e) => toast("bad", (e as Error).message),
        });
      }}>
        <Field label="Pricing model" htmlFor="pricing" hint="Used for every cost and savings figure">
          <select id="pricing" className={inputClass} value={form.pricing_key} onChange={(e) => setForm({ ...form, pricing_key: e.target.value })}>
            {(pricing.data ?? []).map((p) => <option key={p.key} value={p.key}>{p.label}</option>)}
          </select>
        </Field>
        <Field label="SLA threshold (peak CPU %)" htmlFor="sla" hint="Above this a slot counts as a violation">
          <input id="sla" type="number" min={31} max={100} className={inputClass} value={form.sla} onChange={(e) => setForm({ ...form, sla: Number(e.target.value) })} />
        </Field>
        <Field label="Target utilisation (%)" htmlFor="target" error={err} hint="What the autopilot sizes capacity for">
          <input id="target" type="number" min={21} max={99} className={inputClass} value={form.target} onChange={(e) => setForm({ ...form, target: Number(e.target.value) })} />
        </Field>
        <div className="flex justify-end gap-2 md:col-span-3">
          <Button type="button" disabled={!dirty || save.isPending} onClick={() => setForm({ pricing_key: tenant.pricing_key, sla: Math.round(tenant.sla_util * 100), target: Math.round(tenant.target_util * 100) })}>Discard</Button>
          <Button type="submit" variant="primary" disabled={!dirty || !!err} loading={save.isPending}>Save changes</Button>
        </div>
      </form>
    </Card>
  );
}
