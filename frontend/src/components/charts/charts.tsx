import {
  Area, Bar, CartesianGrid, ComposedChart, Legend, Line, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from "recharts";
import { clock, dayLabel, money, num, pct } from "../../lib/format";
import type { FuturePoint, SeriesPoint, TrendPoint } from "../../lib/types";
import { useIsPhone } from "../../lib/useMedia";

const C = { ink2: "#3d4556", brand: "#3b6ff6", brandSoft: "#dce6fe", grey: "#9aa1b1", greySoft: "#eceef3", bad: "#e5484d", good: "#16a34a", warn: "#f08c00" };
const axis = { stroke: "#9aa1b1", fontSize: 11, tickLine: false, axisLine: false } as const;

function TooltipBox({ title, rows }: { title: string; rows: { label: string; value: string; color?: string }[] }) {
  return (
    <div className="min-w-[180px] rounded-xl border border-line bg-surface px-3 py-2 text-xs shadow-pop">
      <div className="mb-1.5 font-semibold text-ink">{title}</div>
      {rows.map((r) => (
        <div key={r.label} className="flex items-center justify-between gap-4 py-0.5">
          <span className="flex items-center gap-1.5 text-muted">
            {r.color && <span className="size-2 rounded-full" style={{ background: r.color }} />}{r.label}
          </span>
          <span className="num font-medium text-ink">{r.value}</span>
        </div>
      ))}
    </div>
  );
}

const tickTime = (spanHours: number) => (v: string) => (spanHours > 30 ? dayLabel(v) : clock(v));

/** Hourly cost: what the tenant pays now vs. what static pre-onboarding provisioning would cost. */
export function CostTrendChart({ data, hours }: { data: TrendPoint[]; hours: number }) {
  const phone = useIsPhone();
  return (
    <ResponsiveContainer width="100%" height={phone ? 220 : 280}>
      <ComposedChart data={data} margin={{ top: 10, right: 8, left: 0, bottom: 0 }}>
        <defs>
          <linearGradient id="costFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor={C.brand} stopOpacity={0.22} />
            <stop offset="100%" stopColor={C.brand} stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid vertical={false} stroke="#eef0f4" />
        <XAxis dataKey="time" {...axis} tickFormatter={tickTime(hours)} minTickGap={phone ? 28 : 40} />
        <YAxis {...axis} width={phone ? 44 : 56} tickFormatter={(v) => (v === 0 ? "$0" : money(v, { cents: v < 10 }))} />
        <Tooltip content={({ active, payload }) => {
          if (!active || !payload?.length) return null;
          const p = payload[0].payload as TrendPoint;
          return <TooltipBox title={`${dayLabel(p.time)} ${clock(p.time)} UTC${p.partial ? " (partial hour)" : ""}`} rows={[
            { label: "With CloudOpt", value: `${money(p.cost, { cents: true })}/hr`, color: C.brand },
            { label: "Before CloudOpt", value: `${money(p.baseline_cost, { cents: true })}/hr`, color: C.grey },
            { label: "Saving", value: `${money(p.baseline_cost - p.cost, { cents: true })}/hr` },
            { label: "SLA violations", value: pct(p.violation_rate, 1) },
          ]} />;
        }} />
        {!phone && <Legend verticalAlign="top" align="right" height={28} iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />}
        <Line type="stepAfter" dataKey="baseline_cost" name="Before CloudOpt (static)" stroke={C.grey} strokeDasharray="5 4" dot={false} strokeWidth={1.6} isAnimationActive={false} />
        <Area type="monotone" dataKey="cost" name="With CloudOpt" stroke={C.brand} fill="url(#costFill)" strokeWidth={2} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}

/** Demand (busiest minute per slot) vs. provisioned capacity, with the forecast band and the
 *  next-hour forecast continuing past "now". */
export function DemandChart({ points, future, slaFactor, hours }: { points: SeriesPoint[]; future: FuturePoint[]; slaFactor: number; hours: number }) {
  const phone = useIsPhone();
  const rows: Record<string, unknown>[] = points.map((p) => ({
    time: p.time, demand: p.demand, capacity: p.capacity, sla: p.capacity * slaFactor,
    band: p.forecast_p10 != null && p.forecast_p90 != null ? [p.forecast_p10, p.forecast_p90] : null,
    p50: p.forecast_p50, violation: p.sla_violation ? p.demand : null, injected: p.injected,
  }));
  const last = points[points.length - 1];
  if (last) {
    for (const f of future) rows.push({ time: f.time, futureBand: [f.p10, f.p90], futureP50: f.p50, capacity: last.capacity });
    if (future.length) rows[points.length - 1] = { ...rows[points.length - 1], futureP50: last.demand, futureBand: [last.demand, last.demand] };
  }
  return (
    <ResponsiveContainer width="100%" height={phone ? 240 : 320}>
      <ComposedChart data={rows} margin={{ top: 10, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="#eef0f4" />
        <XAxis dataKey="time" {...axis} tickFormatter={tickTime(hours)} minTickGap={phone ? 28 : 40} />
        <YAxis {...axis} width={48} tickFormatter={(v) => num(v, 1)} />
        <Tooltip content={({ active, payload }) => {
          if (!active || !payload?.length) return null;
          const r = payload[0].payload as Record<string, number | number[] | string | null>;
          const band = (r.band ?? r.futureBand) as number[] | null;
          const rowsOut = [
            r.demand != null && { label: "Peak demand", value: `${num(r.demand as number, 2)} workers`, color: C.ink2 },
            (r.p50 ?? r.futureP50) != null && { label: "Forecast (median)", value: num((r.p50 ?? r.futureP50) as number, 2), color: C.brand },
            band && { label: "Forecast p10–p90", value: `${num(band[0], 2)} – ${num(band[1], 2)}` },
            { label: "Capacity", value: `${num(r.capacity as number, 2)} vCPU`, color: C.good },
          ].filter(Boolean) as { label: string; value: string; color?: string }[];
          return <TooltipBox title={`${dayLabel(r.time as string)} ${clock(r.time as string)} UTC${r.demand == null ? " (forecast)" : ""}`} rows={rowsOut} />;
        }} />
        {!phone && <Legend verticalAlign="top" align="right" height={28} iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />}
        <Area dataKey="band" name="Forecast p10–p90" stroke="none" fill={C.brandSoft} fillOpacity={0.7} isAnimationActive={false} connectNulls={false} />
        <Area dataKey="futureBand" name="Next-hour forecast" stroke="none" fill={C.brand} fillOpacity={0.14} isAnimationActive={false} legendType="none" />
        <Line type="stepAfter" dataKey="capacity" name="Provisioned capacity" stroke={C.good} strokeWidth={2} dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="demand" name="Peak demand" stroke="#3d4556" strokeWidth={1.5} dot={false} isAnimationActive={false} />
        <Line type="monotone" dataKey="futureP50" name="Forecast" stroke={C.brand} strokeWidth={2} strokeDasharray="4 3" dot={false} isAnimationActive={false} legendType="none" />
        <Line dataKey="violation" name="SLA violation" stroke="none" dot={{ r: 3, fill: C.bad, stroke: "white", strokeWidth: 1 }} isAnimationActive={false} />
        {last && <ReferenceLine x={last.time} stroke={C.grey} strokeDasharray="3 3" label={{ value: "now", position: "insideTopRight", fill: C.grey, fontSize: 11 }} />}
      </ComposedChart>
    </ResponsiveContainer>
  );
}

export function ReplicaChart({ points, hours }: { points: SeriesPoint[]; hours: number }) {
  const phone = useIsPhone();
  return (
    <ResponsiveContainer width="100%" height={200}>
      <ComposedChart data={points} margin={{ top: 10, right: 8, left: 0, bottom: 0 }}>
        <CartesianGrid vertical={false} stroke="#eef0f4" />
        <XAxis dataKey="time" {...axis} tickFormatter={tickTime(hours)} minTickGap={phone ? 28 : 40} />
        <YAxis yAxisId="r" {...axis} width={40} allowDecimals={false} />
        <YAxis yAxisId="u" orientation="right" {...axis} width={40} tickFormatter={(v) => pct(v)} domain={[0, (max: number) => Math.max(1, max)]} />
        <Tooltip content={({ active, payload }) => {
          if (!active || !payload?.length) return null;
          const p = payload[0].payload as SeriesPoint;
          return <TooltipBox title={`${dayLabel(p.time)} ${clock(p.time)} UTC`} rows={[
            { label: "Replicas", value: String(p.replicas), color: C.brand },
            { label: "Peak CPU utilisation", value: pct(p.cpu_util_peak), color: C.warn },
            { label: "Cost (5 min)", value: money(p.cost, { cents: true }) },
          ]} />;
        }} />
        {!phone && <Legend verticalAlign="top" align="right" height={28} iconType="circle" iconSize={8} wrapperStyle={{ fontSize: 12 }} />}
        <Bar yAxisId="r" dataKey="replicas" name="Replicas" fill={C.brandSoft} radius={[3, 3, 0, 0]} isAnimationActive={false} />
        <Line yAxisId="u" type="monotone" dataKey="cpu_util_peak" name="Peak CPU utilisation" stroke={C.warn} dot={false} strokeWidth={1.5} isAnimationActive={false} />
      </ComposedChart>
    </ResponsiveContainer>
  );
}
