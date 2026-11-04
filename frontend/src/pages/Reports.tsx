import { DownloadSimpleIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { CostTrendChart } from "../components/charts/charts";
import { FilterSelect, Page, PageHeader } from "../components/layout/AppShell";
import { Button, Card, CardHeader, ChartGhost, Loading } from "../components/ui/primitives";
import { EmptyState, QueryView } from "../components/ui/states";
import { useAuth } from "../lib/auth";
import { money, pct } from "../lib/format";
import { useOverview, useTenants } from "../lib/queries";
import type { TenantRow } from "../lib/types";

function downloadCsv(rows: TenantRow[]) {
  const header = ["tenant_id", "tenant", "services", "hourly_run_rate_usd", "baseline_hourly_run_rate_usd", "savings_pct", "saved_since_onboarding_usd", "sla_violation_rate_24h"];
  const esc = (v: unknown) => `"${String(v ?? "").replace(/"/g, '""')}"`;
  const lines = [header.join(","), ...rows.map((t) => [t.id, t.name, t.services, t.hourly_run_rate, t.baseline_hourly_run_rate, t.savings_pct, t.savings_since_onboarding, t.sla_violation_rate_24h].map(esc).join(","))];
  const url = URL.createObjectURL(new Blob([lines.join("\n")], { type: "text/csv" }));
  const a = Object.assign(document.createElement("a"), { href: url, download: "cloudopt-savings-report.csv" });
  a.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export default function Reports() {
  const { user } = useAuth();
  const [hours, setHours] = useState("168");
  const tenants = useTenants();
  const ov = useOverview(user?.tenant_id ?? null, Number(hours));
  const rows = tenants.data ?? [];
  const totSaved = rows.reduce((a, t) => a + t.savings_since_onboarding, 0);
  return (
    <Page>
      <PageHeader title="Savings report" subtitle="Spend with CloudOpt vs. the static provisioning each tenant ran before onboarding"
        actions={<Button icon={<DownloadSimpleIcon size={16} />} disabled={!rows.length} onClick={() => downloadCsv(rows)}>Download CSV</Button>}
        filters={<FilterSelect label="Timeframe" value={hours} onChange={setHours} options={[{ value: "24", label: "Last 24 hours" }, { value: "72", label: "Last 3 days" }, { value: "168", label: "Last 7 days" }, { value: "336", label: "All 14 days" }]} />} />
      <div className="space-y-5">
        <Card>
          <CardHeader title="Hourly cost" subtitle="Before onboarding both lines coincide; from onboarding the gap is the saving" />
          <div className="px-3 pb-4 pt-2">
            {ov.isLoading ? <ChartGhost height={280} label="Loading cost history" /> : ov.data?.trend.length ? <CostTrendChart data={ov.data.trend} hours={Number(hours)} /> : <EmptyState compact title="No data in this window" />}
          </div>
        </Card>
        <Card>
          <CardHeader title="By tenant" subtitle={rows.length ? `${money(totSaved)} saved in total since onboarding` : undefined} />
          <QueryView query={tenants} loading={<Loading className="h-48" label="Loading tenants" />} isEmpty={(d) => d.length === 0} empty={<EmptyState compact title="No tenants" />}>
            {(list) => (
              <div className="overflow-x-auto pt-3">
                <table className="w-full min-w-[720px] text-sm">
                  <thead className="text-xs text-muted"><tr className="border-y border-line text-left">
                    <th className="px-5 py-2.5 font-medium">Tenant</th><th className="px-3 py-2.5 text-right font-medium">Before (per hour)</th>
                    <th className="px-3 py-2.5 text-right font-medium">Now (per hour)</th><th className="px-3 py-2.5 text-right font-medium">Savings</th>
                    <th className="px-3 py-2.5 text-right font-medium">Projected per month</th><th className="px-5 py-2.5 text-right font-medium">Saved so far</th>
                  </tr></thead>
                  <tbody className="divide-y divide-line">
                    {list.map((t) => (
                      <tr key={t.id}>
                        <td className="px-5 py-2.5 font-medium">{t.name}</td>
                        <td className="num px-3 py-2.5 text-right text-ink-2">{money(t.baseline_hourly_run_rate, { cents: true })}</td>
                        <td className="num px-3 py-2.5 text-right">{money(t.hourly_run_rate, { cents: true })}</td>
                        <td className="num px-3 py-2.5 text-right font-semibold text-good">{pct(t.savings_pct, 0, { ratio: false })}</td>
                        <td className="num px-3 py-2.5 text-right">{money((t.baseline_hourly_run_rate - t.hourly_run_rate) * 730)}</td>
                        <td className="num px-5 py-2.5 text-right">{money(t.savings_since_onboarding)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </QueryView>
        </Card>
        <p className="text-xs text-muted">Costs use each tenant's configured pricing model. “Before” is the replica count the service ran with before onboarding (static, sized for its historical peak). Telemetry is replayed from the Azure Functions 2019 public trace.</p>
      </div>
    </Page>
  );
}
