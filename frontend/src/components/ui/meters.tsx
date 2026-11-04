import clsx from "clsx";
import { clock, money, pct } from "../../lib/format";

type Tone = "good" | "bad" | "brand" | "ink";
const FILL: Record<Tone, string> = { good: "bg-good", bad: "bg-bad", brand: "bg-brand-500", ink: "bg-ink" };

/**
 * Segmented level meter: a row of fine ticks, the filled ones showing the value. Reads like an
 * instrument rather than a progress bar. ``marker`` (0-1) draws a taller tick, e.g. an SLA threshold.
 */
export function TickMeter({ value, ticks = 40, tone = "brand", marker, label, className }: {
  value: number; ticks?: number; tone?: Tone; marker?: number; label?: string; className?: string;
}) {
  const v = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0));
  const filled = Math.round(v * ticks);
  const markAt = marker == null ? -1 : Math.min(ticks - 1, Math.round(marker * ticks) - 1);
  return (
    <div className={clsx("flex h-3.5 w-full items-center gap-[2px]", className)}
      role="meter" aria-valuenow={Math.round(v * 100)} aria-valuemin={0} aria-valuemax={100} aria-label={label}>
      {Array.from({ length: ticks }, (_, i) => (
        <span key={i} className={clsx(
          "min-w-[2px] flex-1 rounded-[1.5px] transition-colors duration-500",
          i === markAt ? "h-3.5" : "h-2",
          i < filled ? FILL[tone] : i === markAt ? "bg-ink/70" : "bg-line",
        )} />
      ))}
    </div>
  );
}

const HEAT = ["bg-bad/15", "bg-bad/35", "bg-bad/60", "bg-bad"];

/**
 * One cell per hour, shaded by that hour's SLA-violation rate. Shows when trouble happened,
 * not just how much; empty hours stay neutral so a clean day reads as calm.
 */
export function HeatStrip({ values, start, label }: { values: (number | null)[]; start?: string; label?: string }) {
  const level = (v: number) => (v < 0.005 ? 0 : v < 0.02 ? 1 : v < 0.05 ? 2 : 3);
  return (
    <div className="grid gap-[3px]" style={{ gridTemplateColumns: `repeat(${values.length}, minmax(0, 1fr))` }} role="img" aria-label={label}>
      {values.map((v, i) => {
        const when = start ? clock(new Date(new Date(start).getTime() + i * 3600_000).toISOString()) : `hour ${i + 1}`;
        return (
          <span key={i} title={v == null ? `${when}: no data` : `${when}: ${pct(v, 1)} of slots violated the SLA`}
            className={clsx("h-4 rounded-[3px]", v == null ? "bg-canvas" : v === 0 ? "bg-line" : HEAT[level(v)])} />
        );
      })}
    </div>
  );
}

export function HeatLegend() {
  return (
    <div className="flex items-center gap-1.5 text-[11px] text-faint">
      <span>0%</span>
      {["bg-line", ...HEAT].map((c) => <span key={c} className={clsx("size-2.5 rounded-[2px]", c)} />)}
      <span>5%+</span>
    </div>
  );
}

/** Before vs. now as two aligned bars on one scale: the gap between them is the saving. */
export function CompareBars({ before, now }: { before: number; now: number }) {
  const max = Math.max(before, now, 1e-9);
  const row = (label: string, value: number, cls: string, strong?: boolean) => (
    <div className="grid grid-cols-[64px_minmax(0,1fr)_auto] items-center gap-3">
      <span className="text-xs text-muted">{label}</span>
      <div className="h-2 rounded-sm bg-canvas">
        <div className={clsx("h-full rounded-sm transition-[width] duration-700", cls)} style={{ width: `${(100 * value) / max}%` }} />
      </div>
      <span className={clsx("num text-right text-sm", strong ? "font-semibold text-ink" : "text-muted")}>{money(value, { cents: true })}/hr</span>
    </div>
  );
  return (
    <div className="space-y-2.5">
      {row("Before", before, "bg-line-strong")}
      {row("Now", now, "bg-ink", true)}
    </div>
  );
}
