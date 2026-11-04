import { ArrowRightIcon, CheckCircleIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { CostTrendChart } from "../components/charts/charts";
import { FilterSelect, Page, PageHeader } from "../components/layout/AppShell";
import { KpiTile } from "../components/ui/KpiTile";
import { CompareBars, HeatLegend, HeatStrip, TickMeter } from "../components/ui/meters";
import { Badge, Card, CardHeader, ChartGhost, Loading } from "../components/ui/primitives";
import { EmptyState, QueryView } from "../components/ui/states";
import { useAuth } from "../lib/auth";
import { ago, money, pct } from "../lib/format";
import { useAnomalies, useOverview, useRecommendations, useReplay, useServices, useTenants } from "../lib/queries";

const TIMEFRAMES = [
  { value: "6", label: "Last 6 hours" },
  { value: "24", label: "Last 24 hours" },
  { value: "72", label: "Last 3 days" },
  { value: "168", label: "Last 7 days" },
];

export default function Overview() {
  const { user } = useAuth();
  const scoped = user?.tenant_id ?? null;
  const [hours, setHours] = useState("24");
  const [tenant, setTenant] = useState<string>(scoped ?? "all");
  const tenantId = scoped ?? (tenant === "all" ? null : tenant);
  const ov = useOverview(tenantId, Number(hours));
  const tenants = useTenants();
  const replay = useReplay();
  const d = ov.data;
  const loading = ov.isLoading;

  const rateDelta = d && d.baseline_hourly_run_rate > 0 ? (d.hourly_run_rate - d.baseline_hourly_run_rate) / d.baseline_hourly_run_rate : null;
  const slaDelta = d && d.sla_violation_rate != null && d.pre_onboarding_sla_violation_rate != null ? d.sla_violation_rate - d.pre_onboarding_sla_violation_rate : null;
  const utilDelta = d && d.avg_util != null && d.pre_onboarding_avg_util != null ? d.avg_util - d.pre_onboarding_avg_util : null;
  const spark = d?.trend.slice(-24);

  return (
    <Page>
      <PageHeader
        title="Overview"
        subtitle={scoped ? `Cost and reliability for ${tenants.data?.[0]?.name ?? "your company"}` : "Cost and reliability across every tenant CloudOpt manages"}
        filters={<>
          <FilterSelect label="Timeframe" value={hours} onChange={setHours} options={TIMEFRAMES} />
          {!scoped && (
            <FilterSelect label="Tenant" value={tenant} onChange={setTenant}
              options={[{ value: "all", label: "All tenants" }, ...(tenants.data ?? []).map((t) => ({ value: t.id, label: t.name }))]} />
          )}
        </>}
      />

      {ov.error && !d ? (
        <Card><QueryView query={ov} loading={null}>{() => null}</QueryView></Card>
      ) : (
        <div className="space-y-5">
          <div className="grid grid-cols-2 gap-3 sm:gap-4 xl:grid-cols-4">
            <KpiTile loading={loading} label="Current run-rate" value={money(d?.hourly_run_rate, { cents: true })} suffix="/hr"
              delta={rateDelta} upIsGood={false} deltaLabel={rateDelta == null ? undefined : `${pct(rateDelta, 0, { signed: true })} vs before CloudOpt`}
              spark={spark?.map((p) => p.cost)} hint="What the managed services cost per hour at their current replica counts" />
            <KpiTile loading={loading} label="Projected monthly savings" value={money(d?.projected_monthly_savings)}
              deltaLabel={d ? `${money(d.savings_since_onboarding)} saved since onboarding` : undefined}
              hint="(run-rate before CloudOpt − current run-rate) × 730 hours" />
            <KpiTile loading={loading} label="SLA violation rate" value={pct(d?.sla_violation_rate, 2)}
              delta={slaDelta} upIsGood={false}
              deltaLabel={slaDelta == null ? "No data in this window yet" : `${pct(slaDelta, 2, { signed: true })} vs before CloudOpt`}
              spark={spark?.map((p) => p.violation_rate)}
              hint="Share of 5-minute slots where the busiest minute exceeded the SLA utilisation threshold" />
            <KpiTile loading={loading} label="Avg CPU utilisation" value={pct(d?.avg_util, 0)}
              delta={utilDelta} upIsGood deltaLabel={utilDelta == null ? undefined : `${pct(utilDelta, 0, { signed: true })} pts vs before CloudOpt`}
              spark={spark?.map((p) => p.avg_util)} hint="Higher means less idle capacity you pay for" />
          </div>

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-3">
            <Card className="xl:col-span-2">
              <CardHeader title="Hourly cost" subtitle="Spend rate with CloudOpt vs. the static provisioning each tenant had before onboarding" />
              <div className="px-3 pb-4 pt-2">
                {loading ? <ChartGhost height={280} label="Loading cost history" /> : d && d.trend.length ? (
                  <CostTrendChart data={d.trend} hours={Number(hours)} />
                ) : (
                  <EmptyState compact title="No usage recorded in this window" body="The replay may have just been reset. Data appears within a few seconds." />
                )}
              </div>
            </Card>
            <SavingsCard loading={loading} pending={d?.pending_recommendations ?? 0} anomalies={d?.anomalies ?? 0}
              savedPct={d?.savings_since_onboarding_pct ?? null} saved={d?.savings_since_onboarding ?? 0} onboardedAt={replay.data?.onboarded_at}
              before={d?.baseline_hourly_run_rate} now={d?.hourly_run_rate} />
          </div>

          {!scoped && !tenantId && <TenantBoards />}
          {tenantId && <ServiceBoard tenantId={tenantId} />}

          <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
            <RecentRecommendations tenantId={tenantId} />
            <RecentAnomalies tenantId={tenantId} />
          </div>
        </div>
      )}
    </Page>
  );
}

function SavingsCard({ loading, pending, anomalies, savedPct, saved, onboardedAt, before, now }: {
  loading: boolean; pending: number; anomalies: number; savedPct: number | null; saved: number; onboardedAt?: string;
  before?: number; now?: number;
}) {
  return (
    <Card className="flex flex-col">
      <CardHeader title="Since onboarding" subtitle={onboardedAt ? `Managed by CloudOpt from ${new Date(onboardedAt).toUTCString().slice(0, 16)}` : undefined} />
      <div className="flex flex-1 flex-col gap-5 px-5 pb-5 pt-4">
        {loading ? <Loading className="h-20" /> : (
          <div>
            <div className="num text-4xl font-bold tracking-tight text-good">{savedPct == null ? "—" : pct(savedPct, 0, { ratio: false })}</div>
            <div className="mt-1 text-sm text-muted">lower spend · <span className="num font-medium text-ink">{money(saved)}</span> saved so far</div>
          </div>
        )}
        {!loading && before != null && now != null && (
          <div>
            <div className="mb-2.5 text-[11px] font-semibold uppercase tracking-wider text-faint">Hourly run-rate</div>
            <CompareBars before={before} now={now} />
            <div className="mt-3 flex items-baseline justify-between border-t border-dashed border-line pt-3 text-sm">
              <span className="text-muted">At this rate, per month</span>
              <span className="num font-semibold text-good">{money((before - now) * 730)} less</span>
            </div>
          </div>
        )}
        <div className="mt-auto grid grid-cols-2 gap-3">
          <Link to="/recommendations" className="rounded-xl border border-line p-3 hover:border-brand-200 hover:bg-brand-50/40">
            <div className="text-xs text-muted">Awaiting approval</div>
            <div className="num mt-1 text-xl font-bold">{pending}</div>
          </Link>
          <Link to="/anomalies" className="rounded-xl border border-line p-3 hover:border-brand-200 hover:bg-brand-50/40">
            <div className="text-xs text-muted">Anomalies in window</div>
            <div className="num mt-1 text-xl font-bold">{anomalies}</div>
          </Link>
        </div>
      </div>
    </Card>
  );
}

function TenantBoards() {
  const q = useTenants();
  const rows = q.data ?? [];
  const bySavings = [...rows].sort((a, b) => (b.savings_pct ?? -1e9) - (a.savings_pct ?? -1e9)).slice(0, 4);
  const byRisk = [...rows].sort((a, b) => (b.sla_violation_rate_24h ?? 0) - (a.sla_violation_rate_24h ?? 0)).slice(0, 4);
  const skel = <Loading className="h-44" label="Loading tenants" />;
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
      <Card>
        <CardHeader title="Top savings" subtitle="Share of the pre-onboarding run-rate no longer spent" action={<Link to="/tenants" className="text-xs font-medium text-brand-600 hover:underline">All tenants</Link>} />
        <QueryView query={q} loading={skel} compact isEmpty={(d) => d.length === 0} empty={<EmptyState compact title="No tenants yet" />}>
          {() => (
            <ul className="divide-y divide-line px-5 pb-2 pt-1">
              {bySavings.map((t) => (
                <li key={t.id}>
                  <Link to={`/tenants/${t.id}`} className="group block py-3.5">
                    <div className="flex items-baseline justify-between gap-4">
                      <span className="truncate text-sm font-medium group-hover:text-brand-600" title={t.name}>{t.name}</span>
                      <span className="num shrink-0 text-sm font-semibold">{pct(t.savings_pct, 0, { ratio: false })}</span>
                    </div>
                    <TickMeter className="mt-2" value={(t.savings_pct ?? 0) / 100} tone="good" label={`${t.name} savings`} />
                    <div className="num mt-1.5 text-xs text-muted">
                      <span className="line-through decoration-faint">{money(t.baseline_hourly_run_rate, { cents: true })}</span>
                      <span className="mx-1.5 text-faint">→</span>{money(t.hourly_run_rate, { cents: true })}/hr
                    </div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </QueryView>
      </Card>
      <Card>
        <CardHeader title="Needs attention" subtitle="SLA violations by hour, last 24 hours" action={<HeatLegend />} />
        <QueryView query={q} loading={skel} compact isEmpty={(d) => d.length === 0} empty={<EmptyState compact title="No tenants yet" />}>
          {() => byRisk.every((t) => !t.sla_violation_rate_24h) ? (
            <EmptyState compact icon={<CheckCircleIcon size={30} weight="light" className="text-good" />} title="No SLA violations in the last 24 hours" body="Every tenant stayed within its utilisation threshold." />
          ) : (
            <ul className="divide-y divide-line px-5 pb-2 pt-1">
              {byRisk.map((t) => (
                <li key={t.id}>
                  <Link to={`/tenants/${t.id}`} className="group block py-3.5">
                    <div className="flex items-baseline justify-between gap-4">
                      <span className="truncate text-sm font-medium group-hover:text-brand-600" title={t.name}>{t.name}</span>
                      <span className="num shrink-0 text-sm">
                        <span className="font-semibold">{pct(t.sla_violation_rate_24h, 2)}</span>
                        <span className="text-faint"> of slots{t.pending_recommendations ? ` · ${t.pending_recommendations} pending` : ""}</span>
                      </span>
                    </div>
                    <div className="mt-2"><HeatStrip values={t.violations_by_hour} start={t.hours_start} label={`${t.name}: SLA violations by hour`} /></div>
                  </Link>
                </li>
              ))}
            </ul>
          )}
        </QueryView>
      </Card>
    </div>
  );
}

function ServiceBoard({ tenantId }: { tenantId: string }) {
  const q = useServices(tenantId);
  return (
    <Card>
      <CardHeader title="Services" subtitle="Live utilisation of the busiest minute in the last slot" action={<Link to="/services" className="text-xs font-medium text-brand-600 hover:underline">All services</Link>} />
      <QueryView query={q} compact loading={<Loading className="h-40" label="Loading services" />} isEmpty={(d) => d.length === 0} empty={<EmptyState compact title="This tenant has no services" />}>
        {(rows) => (
          <ul className="grid grid-cols-1 gap-x-8 gap-y-4 px-5 pb-5 pt-4 md:grid-cols-2">
            {rows.map((s) => {
              const u = s.cpu_util_peak ?? 0;
              return (
                <li key={s.id}>
                  <Link to={`/services/${s.id}`} className="group block">
                    <div className="flex items-baseline justify-between gap-4">
                      <span className="truncate text-sm font-medium group-hover:text-brand-600">{s.name}</span>
                      <span className="num shrink-0 text-sm font-semibold">{pct(u)} <span className="font-normal text-faint">· {s.replicas} replicas</span></span>
                    </div>
                    <TickMeter className="mt-2" value={Math.min(u, 1)} marker={0.9} tone={s.sla_violation ? "bad" : "brand"} label={`${s.name} utilisation; marker at SLA threshold`} />
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </QueryView>
    </Card>
  );
}

const viewAll = (to: string) => (
  <Link to={to} className="inline-flex items-center gap-1 text-xs font-medium text-brand-600 hover:underline">
    View all <ArrowRightIcon size={12} weight="bold" />
  </Link>
);

function RecentRecommendations({ tenantId }: { tenantId: string | null }) {
  const q = useRecommendations({ status: "pending,approved,rejected,auto_applied", tenant_id: tenantId, limit: 6 });
  const now = useReplay().data?.sim_time;
  return (
    <Card>
      <CardHeader title="Latest scaling decisions" action={viewAll("/recommendations")} />
      <QueryView query={q} compact loading={<Loading className="h-44" label="Loading decisions" />}
        isEmpty={(d) => d.items.length === 0} empty={<EmptyState compact title="No scaling decisions yet" body="They appear as the replay runs." />}>
        {(d) => (
          <ul className="divide-y divide-line px-5 pb-2 pt-1">
            {d.items.map((r) => {
              const up = r.action === "scale_out";
              return (
                <li key={r.id}>
                  <Link to={`/services/${r.service_id}`} className="grid grid-cols-[auto_minmax(0,1fr)] items-center gap-x-4 py-3 hover:opacity-80 sm:grid-cols-[auto_minmax(0,1fr)_auto]">
                    <span className={`num w-[74px] text-[15px] font-semibold tracking-tight ${up ? "text-warn" : "text-good"}`}>
                      {r.current_replicas}<span className="mx-1 font-normal text-faint">→</span>{r.recommended_replicas}
                    </span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">{r.facts.service_name} <span className="font-normal text-muted">· {r.facts.tenant_name}</span></span>
                      <span className="block text-xs text-muted">{money(r.facts.hourly_cost_delta, { signed: true, cents: true })}/hr · {ago(r.created_at, now)}<span className="sm:hidden"> · {STATUS_LABEL[r.status] ?? r.status}</span></span>
                    </span>
                    <span className="hidden sm:block"><StatusBadge status={r.status} /></span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </QueryView>
    </Card>
  );
}

const STATUS_LABEL: Record<string, string> = {
  pending: "Awaiting approval", approved: "Approved", auto_applied: "Autopilot", rejected: "Rejected", expired: "Expired", superseded: "Superseded",
};

export function StatusBadge({ status }: { status: string }) {
  const map: Record<string, { tone: "warn" | "good" | "bad" | "neutral" | "brand"; label: string }> = {
    pending: { tone: "warn", label: "Awaiting approval" },
    approved: { tone: "good", label: "Approved" },
    auto_applied: { tone: "brand", label: "Autopilot" },
    rejected: { tone: "bad", label: "Rejected" },
    expired: { tone: "neutral", label: "Expired" },
    superseded: { tone: "neutral", label: "Superseded" },
  };
  const m = map[status] ?? { tone: "neutral", label: status };
  return <Badge tone={m.tone}>{m.label}</Badge>;
}

function RecentAnomalies({ tenantId }: { tenantId: string | null }) {
  const q = useAnomalies({ tenant_id: tenantId, limit: 6 });
  const services = useServices(tenantId);
  const now = useReplay().data?.sim_time;
  const name = (id: string) => services.data?.find((s) => s.id === id)?.name ?? id;
  return (
    <Card>
      <CardHeader title="Recent anomalies" subtitle="Deviations the forecast did not expect" action={viewAll("/anomalies")} />
      <QueryView query={q} compact loading={<Loading className="h-44" label="Loading anomalies" />}
        isEmpty={(d) => d.length === 0} empty={<EmptyState compact icon={<CheckCircleIcon size={30} weight="light" className="text-good" />} title="No anomalies detected" body="Traffic is tracking the forecast." />}>
        {(rows) => (
          <ul className="divide-y divide-line px-5 pb-2 pt-1">
            {rows.map((a) => {
              const ratio = a.kind === "spike" ? a.observed / Math.max(a.expected, 1e-6) : a.expected / Math.max(a.observed, 1e-6);
              return (
                <li key={a.id}>
                  <Link to={`/services/${a.service_id}`} className="grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-x-4 py-3 hover:opacity-80">
                    <span className="num w-[74px] text-xs text-muted">{ago(a.time, now)}</span>
                    <span className="min-w-0">
                      <span className="block truncate text-sm font-medium">{name(a.service_id)}</span>
                      <span className="block text-xs text-muted">{a.observed.toFixed(1)} workers vs {a.expected.toFixed(1)} expected{a.injected ? " · injected scenario" : ""}</span>
                    </span>
                    <span className={`num rounded-md px-2 py-1 text-xs font-semibold ${a.kind === "spike" ? "bg-bad-soft text-bad" : "bg-warn-soft text-warn"}`}>
                      {a.kind === "spike" ? "▲" : "▼"} {ratio.toFixed(1)}×
                    </span>
                  </Link>
                </li>
              );
            })}
          </ul>
        )}
      </QueryView>
    </Card>
  );
}
