import clsx from "clsx";
import { ArrowDownRightIcon, ArrowUpRightIcon, InfoIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { Area, AreaChart, ResponsiveContainer } from "recharts";
import { Card, Dots } from "./primitives";

/** KPI tile: label, big number, optional delta (with whether "up" is good) and sparkline. */
export function KpiTile({ label, value, suffix, delta, deltaLabel, upIsGood = true, spark, hint, loading }: {
  label: string;
  value: ReactNode;
  suffix?: ReactNode;
  delta?: number | null;
  deltaLabel?: string;
  upIsGood?: boolean;
  spark?: number[];
  hint?: string;
  loading?: boolean;
}) {
  const gid = label.replace(/[^a-zA-Z0-9]/g, "");
  const good = delta == null ? null : delta === 0 ? null : (delta > 0) === upIsGood;
  return (
    <Card className="flex min-h-[112px] flex-col justify-between p-3.5 sm:min-h-[124px] sm:p-4">
      <div className="flex items-start gap-1 text-xs leading-snug text-muted sm:items-center sm:text-[13px]">
        <span className="sm:truncate">{label}</span>
        {hint && (
          <span title={hint} className="cursor-help text-faint" aria-label={hint}>
            <InfoIcon size={14} />
          </span>
        )}
      </div>
      {loading ? (
        <div className="mt-3 h-8"><Dots className="text-faint" /></div>
      ) : (
        <div className="mt-2 flex items-baseline gap-1">
          <span className="num text-[22px] font-bold leading-none tracking-tight text-ink sm:text-[26px]">{value}</span>
          {suffix && <span className="text-sm font-medium text-faint">{suffix}</span>}
        </div>
      )}
      <div className="mt-2 flex h-7 items-end justify-between gap-2">
        {delta != null && !loading ? (
          <span className={clsx("inline-flex items-start gap-0.5 text-[11.5px] font-medium leading-tight sm:text-xs", good === null ? "text-muted" : good ? "text-good" : "text-bad")}>
            {delta > 0 ? <ArrowUpRightIcon size={13} weight="bold" /> : delta < 0 ? <ArrowDownRightIcon size={13} weight="bold" /> : null}
            {deltaLabel}
          </span>
        ) : (
          <span className="text-xs text-faint">{loading ? "" : deltaLabel}</span>
        )}
        {spark && spark.length > 1 && !loading && (
          <div className="hidden h-7 w-24 shrink-0 sm:block" aria-hidden>
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={spark.map((v, i) => ({ i, v }))} margin={{ top: 2, bottom: 0, left: 0, right: 0 }}>
                <defs>
                  <linearGradient id={`spark-${gid}`} x1="0" y1="0" x2="0" y2="1">
                    <stop offset="0%" stopColor="#3b6ff6" stopOpacity={0.25} />
                    <stop offset="100%" stopColor="#3b6ff6" stopOpacity={0} />
                  </linearGradient>
                </defs>
                <Area type="monotone" dataKey="v" stroke="#3b6ff6" strokeWidth={1.6} fill={`url(#spark-${gid})`} isAnimationActive={false} />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </Card>
  );
}
