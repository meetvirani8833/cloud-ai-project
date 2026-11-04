import { FlaskIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis } from "recharts";
import { Page, PageHeader } from "../components/layout/AppShell";
import { Badge, Card, CardHeader, Loading } from "../components/ui/primitives";
import { EmptyState, QueryView } from "../components/ui/states";
import { money, num, pct } from "../lib/format";
import { useEvaluation } from "../lib/queries";
import type { AnomalyReport, Evaluation as Ev, ForecastMetrics, PolicyResult } from "../lib/types";

const POLICY_LABEL: Record<string, string> = {
  static_peak: "Static (sized for peak)",
  static_observed: "Static (Borg-observed over-provisioning)",
  reactive_hpa: "Reactive threshold autoscaler (HPA-style)",
  naive_persistence: "Naive forecast: last value",
  naive_moving_max_30m: "Naive forecast: 30-min max",
  predictive_xgb: "Forecast only (XGBoost p90, no stability layers)",
  predictive_xgb_anomaly: "CloudOpt production policy (forecast + anomaly guard + look-ahead scale-in)",
  rl_ppo: "CloudOpt: RL policy (PPO)",
  oracle: "Oracle (perfect foresight)",
};
const isOurs = (p: string) => p === "cloudopt" || p === "predictive_xgb_anomaly" || p.startsWith("rl_");

function Section({ title, subtitle, children, badge }: { title: string; subtitle?: string; children: ReactNode; badge?: ReactNode }) {
  return <Card><CardHeader title={<span className="inline-flex items-center gap-2">{title}{badge}</span>} subtitle={subtitle} /><div className="p-5 pt-4">{children}</div></Card>;
}

export default function Evaluation() {
  const q = useEvaluation();
  return (
    <Page>
      <PageHeader title="Evaluation" subtitle="Offline results on held-out trace days 13–14, the same workload for every strategy. Produced by the training pipeline (cloudopt.eval)." />
      <QueryView query={q} loading={<Card><Loading className="h-72" label="Loading evaluation results" /></Card>}>
        {(d) => (
          <div className="space-y-5">
            <Frontier d={d} />
            <PolicyTable d={d} />
            <ForecastTable d={d} />
            <LstmTable r={d.forecast_lstm as unknown as LstmReport | null} />
            <AnomalyTable r={d.anomaly} />
            {d.rl ? <RLTable r={d.rl as unknown as RLReport} /> : <Section title="Reinforcement-learning policy" badge={<Badge tone="warn">Pending</Badge>}><EmptyState compact icon={<FlaskIcon size={30} weight="light" />} title="RL results not generated yet" body="Run python -m cloudopt.rl.train to train and evaluate the PPO scaling policy." /></Section>}
            <ExplainTable r={d.explainability as unknown as ExplainReport | null} />
          </div>
        )}
      </QueryView>
    </Page>
  );
}

function Frontier({ d }: { d: Ev }) {
  const f = d.frontier;
  if (!f) {
    const rows = d.comparison?.results ?? [];
    if (!rows.length) return null;
    const pts = rows.filter((r) => r.policy !== "static_peak").map((r) => ({ x: 100 * r.violation_rate, y: r.total_cost_usd, name: POLICY_LABEL[r.policy] ?? r.policy, ours: isOurs(r.policy) }));
    return (
      <Section title="Cost vs. SLA violations" subtitle="Lower-left is better. Each point is one strategy on the same replayed workload.">
        <ResponsiveContainer width="100%" height={320}>
          <ScatterChart margin={{ top: 10, right: 20, bottom: 20, left: 10 }}>
            <CartesianGrid stroke="#eef0f4" />
            <XAxis type="number" dataKey="x" name="SLA violations" unit="%" tick={{ fontSize: 11 }} label={{ value: "SLA violations (% of app-slots)", position: "insideBottom", offset: -10, fontSize: 12 }} />
            <YAxis type="number" dataKey="y" name="Cost" tick={{ fontSize: 11 }} tickFormatter={(v) => money(v)} width={70} />
            <ZAxis range={[90, 90]} />
            <Tooltip formatter={(v, n) => (n === "Cost" ? money(Number(v)) : `${Number(v).toFixed(2)}%`)} labelFormatter={() => ""} />
            <Scatter data={pts} fill="#3b6ff6" />
          </ScatterChart>
        </ResponsiveContainer>
      </Section>
    );
  }
  const colors = ["#9aa1b1", "#f08c00", "#3b6ff6", "#16a34a", "#e5484d", "#8b5cf6"];
  return (
    <Section title="Cost vs. SLA violations trade-off" subtitle="Each curve sweeps one strategy's tuning knob. Lower-left is better: for any violation budget, the lowest curve is the cheapest strategy.">
      {f.headline && (
        <div className="mb-4 grid grid-cols-1 gap-3 sm:grid-cols-2 xl:grid-cols-4">
          {Object.entries(f.headline).map(([k, v]) => (
            <div key={k} className="rounded-xl border border-line p-3"><div className="text-xs text-muted">{k}</div><div className="num mt-1 text-xl font-bold">{typeof v === "number" ? `${v.toFixed(1)}%` : v}</div></div>
          ))}
        </div>
      )}
      <ResponsiveContainer width="100%" height={360}>
        <LineChart margin={{ top: 10, right: 20, bottom: 24, left: 10 }}>
          <CartesianGrid stroke="#eef0f4" />
          <XAxis type="number" dataKey="violation_rate" tickFormatter={(v) => `${(100 * v).toFixed(1)}%`} tick={{ fontSize: 11 }} domain={[0, "auto"]}
            label={{ value: "SLA violations (% of app-slots)", position: "insideBottom", offset: -12, fontSize: 12 }} />
          <YAxis type="number" dataKey="cost" tickFormatter={(v) => money(v)} tick={{ fontSize: 11 }} width={72} />
          <Tooltip formatter={(v, n) => [money(Number(v)), String(n)]} labelFormatter={(v) => `SLA violations ${pct(Number(v), 2)}`} />
          <Legend verticalAlign="top" height={32} wrapperStyle={{ fontSize: 12 }} />
          {f.curves.map((c, i) => (
            <Line key={c.policy} data={c.points} dataKey="cost" name={c.label} stroke={colors[i % colors.length]} strokeWidth={isOurs(c.policy) ? 2.5 : 1.6}
              dot={{ r: 3 }} isAnimationActive={false} />
          ))}
        </LineChart>
      </ResponsiveContainer>
    </Section>
  );
}

function PolicyTable({ d }: { d: Ev }) {
  const rows: PolicyResult[] = d.comparison?.results ?? [];
  if (!rows.length) return null;
  const meta = d.comparison?.meta ?? {};
  return (
    <Section title="Scaling strategies at default settings" subtitle={`${meta.apps ?? "?"} apps across ${meta.tenants ?? "?"} tenants · savings relative to static peak provisioning`}>
      <div className="-mx-5 overflow-x-auto">
        <table className="w-full min-w-[820px] text-sm">
          <thead className="text-xs text-muted"><tr className="border-b border-line text-left">
            <th className="px-5 py-2.5 font-medium">Strategy</th><th className="px-3 py-2.5 text-right font-medium">Cost (2 days)</th>
            <th className="px-3 py-2.5 text-right font-medium">Savings</th><th className="px-3 py-2.5 text-right font-medium">Median app savings</th>
            <th className="px-3 py-2.5 text-right font-medium">SLA violations</th><th className="px-3 py-2.5 text-right font-medium">Median util.</th>
            <th className="px-5 py-2.5 text-right font-medium">Changes / app / day</th>
          </tr></thead>
          <tbody className="divide-y divide-line">
            {rows.map((r) => (
              <tr key={r.policy} className={isOurs(r.policy) ? "bg-brand-50/50" : r.policy === "oracle" ? "text-muted" : ""}>
                <td className="px-5 py-2.5 font-medium">{POLICY_LABEL[r.policy] ?? r.policy}</td>
                <td className="num px-3 py-2.5 text-right">{money(r.total_cost_usd)}</td>
                <td className="num px-3 py-2.5 text-right">{pct(r.savings_vs_baseline_pct ?? 0, 1, { ratio: false })}</td>
                <td className="num px-3 py-2.5 text-right">{r.median_app_savings_pct == null ? "—" : pct(r.median_app_savings_pct, 1, { ratio: false })}</td>
                <td className="num px-3 py-2.5 text-right">{pct(r.violation_rate, 2)}</td>
                <td className="num px-3 py-2.5 text-right">{pct(r.median_util)}</td>
                <td className="num px-5 py-2.5 text-right">{num(r.scaling_actions_per_app_day, 1)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function ForecastTable({ d }: { d: Ev }) {
  const f = d.forecast;
  if (!f) return null;
  const models = ["xgboost_p50", "persistence", "seasonal_naive_1d"];
  const label: Record<string, string> = { xgboost_p50: "XGBoost (median)", persistence: "Persistence", seasonal_naive_1d: "Same slot yesterday" };
  return (
    <Section title="Forecast accuracy" subtitle="Peak demand per 5-minute slot, scaling-study apps, test days. WAPE and nMAE are the primary metrics; MAPE is unstable near zero demand.">
      <div className="-mx-5 overflow-x-auto">
        <table className="w-full min-w-[760px] text-sm">
          <thead className="text-xs text-muted"><tr className="border-b border-line text-left">
            <th className="px-5 py-2.5 font-medium">Horizon</th><th className="px-3 py-2.5 font-medium">Model</th>
            <th className="px-3 py-2.5 text-right font-medium">WAPE</th><th className="px-3 py-2.5 text-right font-medium">nMAE</th>
            <th className="px-3 py-2.5 text-right font-medium">MAE</th><th className="px-3 py-2.5 text-right font-medium">RMSE</th>
            <th className="px-3 py-2.5 text-right font-medium">MAPE</th><th className="px-5 py-2.5 text-right font-medium">80% band coverage</th>
          </tr></thead>
          <tbody>
            {Object.entries(f).map(([h, rep]) => models.map((m, i) => {
              const r = rep.scaling_study_apps[m] as ForecastMetrics | undefined;
              if (!r) return null;
              return (
                <tr key={h + m} className={i === 0 ? "border-t border-line bg-brand-50/40" : ""}>
                  <td className="px-5 py-2 font-medium">{i === 0 ? h.replace(/^h\d+ \(|\)$/g, "") : ""}</td>
                  <td className="px-3 py-2">{label[m]}</td>
                  <td className="num px-3 py-2 text-right">{r.wape_pct.toFixed(1)}%</td>
                  <td className="num px-3 py-2 text-right">{r.nmae.toFixed(3)}</td>
                  <td className="num px-3 py-2 text-right">{r.mae.toFixed(2)}</td>
                  <td className="num px-3 py-2 text-right">{r.rmse.toFixed(1)}</td>
                  <td className="num px-3 py-2 text-right">{r.mape_pct.toFixed(1)}%</td>
                  <td className="num px-5 py-2 text-right">{i === 0 ? pct(rep.scaling_study_apps.xgboost_p10_p90_coverage as number, 1) : ""}</td>
                </tr>
              );
            }))}
          </tbody>
        </table>
      </div>
    </Section>
  );
}

function AnomalyTable({ r }: { r: AnomalyReport | null }) {
  if (!r) return null;
  const rows: [string, keyof AnomalyReport, boolean][] = [
    ["CloudOpt: forecast residual + Isolation Forest", "residual_iforest", true],
    ["Ablation: Isolation Forest without band check", "ablation_iforest_without_band_check", false],
    ["Ablation: forecast band only", "ablation_forecast_band_only", false],
    ["Baseline: raw threshold (tuned)", "raw_threshold_baseline", false],
  ];
  return (
    <Section title="Anomaly detection" subtitle={`Synthetic events injected into test days (${Object.entries(r.test_events).map(([k, v]) => `${v} ${k}s`).join(", ")}). Surges are benign and should not alert. ${r.method}.`}>
      <div className="-mx-5 overflow-x-auto">
        <table className="w-full min-w-[720px] text-sm">
          <thead className="text-xs text-muted"><tr className="border-b border-line text-left">
            <th className="px-5 py-2.5 font-medium">Detector</th><th className="px-3 py-2.5 text-right font-medium">Precision</th>
            <th className="px-3 py-2.5 text-right font-medium">Recall</th><th className="px-3 py-2.5 text-right font-medium">F1</th>
            <th className="px-3 py-2.5 text-right font-medium">False alarms on surges</th><th className="px-5 py-2.5 text-right font-medium">Alerts raised</th>
          </tr></thead>
          <tbody className="divide-y divide-line">
            {rows.map(([name, key, ours]) => {
              const m = r[key] as { precision: number; recall: number; f1: number; surge_false_alarm: number; alerts: number };
              return (
                <tr key={key} className={ours ? "bg-brand-50/50" : ""}>
                  <td className="px-5 py-2.5 font-medium">{name}</td>
                  <td className="num px-3 py-2.5 text-right">{pct(m.precision)}</td><td className="num px-3 py-2.5 text-right">{pct(m.recall)}</td>
                  <td className="num px-3 py-2.5 text-right">{m.f1.toFixed(2)}</td><td className="num px-3 py-2.5 text-right">{pct(m.surge_false_alarm)}</td>
                  <td className="num px-5 py-2.5 text-right">{m.alerts.toLocaleString()}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-muted">Precision is a lower bound: real traffic contains genuine anomalies that are not in the injected labels.</p>
    </Section>
  );
}

interface RLRow extends PolicyResult { violation_penalty: number; cumulative_reward: number; baseline_reward: Record<string, number>; train_seconds: number }
interface RLReport { timesteps: number; churn_penalty?: number; train_window: string; eval_window: string; policies: RLRow[]; baselines: Record<string, PolicyResult> }

function RLTable({ r }: { r: RLReport }) {
  return (
    <Section title="Reinforcement-learning policy (PPO)"
      subtitle={`Custom Gymnasium environment, Stable-Baselines3 PPO, ${r.timesteps.toLocaleString()} steps per policy; trained on ${r.train_window}, evaluated on ${r.eval_window}. Reward = −cost − λ·violation − ${r.churn_penalty ?? 0.01}·change. Higher reward is better.`}>
      <div className="-mx-5 overflow-x-auto">
        <table className="w-full min-w-[820px] text-sm">
          <thead className="text-xs text-muted"><tr className="border-b border-line text-left">
            <th className="px-5 py-2.5 font-medium">Violation penalty λ</th><th className="px-3 py-2.5 text-right font-medium">Cost</th>
            <th className="px-3 py-2.5 text-right font-medium">SLA violations</th><th className="px-3 py-2.5 text-right font-medium">Changes / app / day</th>
            <th className="px-3 py-2.5 text-right font-medium">Reward: PPO</th><th className="px-3 py-2.5 text-right font-medium">CloudOpt rules</th>
            <th className="px-5 py-2.5 text-right font-medium">Reactive</th>
          </tr></thead>
          <tbody className="divide-y divide-line">
            {r.policies.map((p) => {
              const best = Math.max(p.cumulative_reward, ...Object.values(p.baseline_reward));
              const cell = (v: number) => <td className={`num px-3 py-2.5 text-right ${v === best ? "font-semibold text-good" : ""}`}>{v.toFixed(1)}</td>;
              return (
                <tr key={p.violation_penalty}>
                  <td className="px-5 py-2.5 font-medium">{p.violation_penalty}</td>
                  <td className="num px-3 py-2.5 text-right">{money(p.total_cost_usd)}</td>
                  <td className="num px-3 py-2.5 text-right">{pct(p.violation_rate, 2)}</td>
                  <td className="num px-3 py-2.5 text-right">{num(p.scaling_actions_per_app_day, 1)}</td>
                  {cell(p.cumulative_reward)}{cell(p.baseline_reward.cloudopt_rules)}
                  <td className={`num px-5 py-2.5 text-right ${p.baseline_reward.reactive_hpa === best ? "font-semibold text-good" : ""}`}>{p.baseline_reward.reactive_hpa.toFixed(1)}</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <p className="mt-3 text-xs text-muted">Mean per-app cumulative reward over the test window under each λ's objective; the best policy per row is highlighted.</p>
    </Section>
  );
}

interface ExplainSummary { n: number; all_checks_passed_pct?: number; per_check_pass_pct?: Record<string, number> }
interface ExplainReport { recommendations: number; with_anomaly: number; template: ExplainSummary; openai: (ExplainSummary & { model: string; sources?: Record<string, number> }) | null; openai_verified?: ExplainSummary | null }

const CHECK_LABEL: Record<string, string> = {
  has_all_fields: "All brief fields present", replica_counts: "Correct replica counts", direction: "Correct direction",
  cites_forecast: "Cites the triggering forecast", anomaly_consistent: "Mentions anomaly iff one triggered it",
  cost_sign: "Correct cost direction", risk_cited: "Cites risk before/after", threshold_claims_true: "Threshold claims are true",
  no_invented_numbers: "No invented numbers", confidence_label: "Correct confidence label",
  no_field_names: "No internal field names",
};

function ExplainTable({ r }: { r: ExplainReport | null }) {
  if (!r) return null;
  const cols: [string, ExplainSummary | null][] = [
    ["Template", r.template],
    [r.openai ? `${r.openai.model}: first drafts` : "LLM", r.openai],
    ["LLM + fact-check (shown to users)", r.openai_verified ?? null],
  ];
  const src = r.openai?.sources;
  return (
    <Section title="Explanation quality (automatic rubric)"
      subtitle={`${r.recommendations} recommendations generated by the live engine; each explanation is checked against the structured facts it was generated from.${src ? ` LLM sample: ${src.openai ?? 0} passed first time, ${src.openai_retry ?? 0} fixed on retry, ${src.fallback ?? 0} fell back to the template.` : ""}`}>
      <div className="-mx-5 overflow-x-auto">
        <table className="w-full min-w-[560px] text-sm">
          <thead className="text-xs text-muted"><tr className="border-b border-line text-left">
            <th className="px-5 py-2.5 font-medium">Check</th>
            {cols.map(([n]) => <th key={n} className="px-5 py-2.5 text-right font-medium">{n}</th>)}
          </tr></thead>
          <tbody className="divide-y divide-line">
            {Object.keys(r.template.per_check_pass_pct ?? {}).map((k) => (
              <tr key={k}>
                <td className="px-5 py-2.5">{CHECK_LABEL[k] ?? k}</td>
                {cols.map(([n, c]) => <td key={n} className="num px-5 py-2.5 text-right">{c?.per_check_pass_pct ? `${c.per_check_pass_pct[k]?.toFixed(1)}%` : <span className="text-faint">needs API key</span>}</td>)}
              </tr>
            ))}
            <tr className="bg-brand-50/50 font-semibold">
              <td className="px-5 py-2.5">All checks passed</td>
              {cols.map(([n, c]) => <td key={n} className="num px-5 py-2.5 text-right">{c?.all_checks_passed_pct != null ? `${c.all_checks_passed_pct.toFixed(1)}%` : "—"}</td>)}
            </tr>
          </tbody>
        </table>
      </div>
    </Section>
  );
}

type LstmReport = Record<string, Record<string, Record<string, ForecastMetrics | number> | number[] | number>>;

function LstmTable({ r }: { r: LstmReport | null }) {
  if (!r) return null;
  const label: Record<string, string> = {
    lstm_p50: "LSTM (median)", xgboost_p50: "XGBoost (median)", xgboost_p50_hourly_mean: "XGBoost 24 h (hourly mean)",
    persistence: "Persistence", seasonal_naive_1d: "Same hour yesterday",
  };
  return (
    <Section title="LSTM vs. XGBoost at longer horizons" subtitle="PyTorch LSTM with quantile (pinball) loss: 1 hour ahead at 5-minute resolution, and 24 hours ahead at hourly resolution. Scaling-study apps, test days.">
      <div className="-mx-5 overflow-x-auto">
        <table className="w-full min-w-[640px] text-sm">
          <thead className="text-xs text-muted"><tr className="border-b border-line text-left">
            <th className="px-5 py-2.5 font-medium">Horizon</th><th className="px-3 py-2.5 font-medium">Model</th>
            <th className="px-3 py-2.5 text-right font-medium">WAPE</th><th className="px-3 py-2.5 text-right font-medium">nMAE</th>
            <th className="px-5 py-2.5 text-right font-medium">RMSE</th>
          </tr></thead>
          <tbody>
            {Object.entries(r).map(([h, rep]) => {
              const s = (rep as Record<string, unknown>).scaling_study_apps as Record<string, ForecastMetrics | number>;
              const models = Object.keys(s).filter((k) => typeof s[k] === "object");
              const best = Math.min(...models.map((m) => (s[m] as ForecastMetrics).nmae));
              return models.map((m, i) => {
                const x = s[m] as ForecastMetrics;
                return (
                  <tr key={h + m} className={i === 0 ? "border-t border-line" : ""}>
                    <td className="px-5 py-2 font-medium">{i === 0 ? h.replace(/^h\w+ \(|\)$/g, "") : ""}</td>
                    <td className="px-3 py-2">{label[m] ?? m}</td>
                    <td className="num px-3 py-2 text-right">{x.wape_pct.toFixed(1)}%</td>
                    <td className={`num px-3 py-2 text-right ${x.nmae === best ? "font-semibold text-good" : ""}`}>{x.nmae.toFixed(3)}</td>
                    <td className="num px-5 py-2 text-right">{x.rmse.toFixed(1)}</td>
                  </tr>
                );
              });
            })}
          </tbody>
        </table>
      </div>
    </Section>
  );
}
