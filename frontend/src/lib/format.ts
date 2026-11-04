// Formatting helpers. Simulated trace time is shown in UTC to match the server clock.

const usd0 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const usd2 = new Intl.NumberFormat("en-US", { style: "currency", currency: "USD", minimumFractionDigits: 2, maximumFractionDigits: 2 });
const compact = new Intl.NumberFormat("en-US", { notation: "compact", maximumFractionDigits: 1 });

export function money(x: number | null | undefined, opts: { cents?: boolean; signed?: boolean } = {}): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const abs = Math.abs(x);
  const cents = opts.cents ?? abs < 100;
  const s = (cents ? usd2 : usd0).format(abs);
  if (opts.signed) return x > 0 ? `+${s}` : x < 0 ? `−${s}` : s;
  return x < 0 ? `−${s}` : s;
}

export function pct(x: number | null | undefined, digits = 0, opts: { signed?: boolean; ratio?: boolean } = {}): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  const v = opts.ratio === false ? x : x * 100;
  const s = `${Math.abs(v).toFixed(digits)}%`;
  if (opts.signed) return v > 0 ? `+${s}` : v < 0 ? `−${s}` : s;
  return v < 0 ? `−${s}` : s;
}

export function num(x: number | null | undefined, digits = 1): string {
  if (x === null || x === undefined || Number.isNaN(x)) return "—";
  if (Math.abs(x) >= 10000) return compact.format(x);
  return x.toLocaleString("en-US", { maximumFractionDigits: digits });
}

export function simTime(iso: string, withDay = true): string {
  const d = new Date(iso);
  return d.toLocaleString("en-GB", {
    timeZone: "UTC",
    weekday: withDay ? "short" : undefined,
    day: withDay ? "numeric" : undefined,
    month: withDay ? "short" : undefined,
    hour: "2-digit",
    minute: "2-digit",
  });
}

export function clock(iso: string): string {
  return new Date(iso).toLocaleTimeString("en-GB", { timeZone: "UTC", hour: "2-digit", minute: "2-digit" });
}

export function dayLabel(iso: string): string {
  return new Date(iso).toLocaleDateString("en-GB", { timeZone: "UTC", weekday: "short", day: "numeric", month: "short" });
}

/** "12 min ago" relative to the simulated now (both ISO strings). */
export function ago(iso: string, nowIso?: string): string {
  const now = nowIso ? new Date(nowIso).getTime() : Date.now();
  const diff = Math.max(0, now - new Date(iso).getTime());
  const m = Math.round(diff / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 48) return `${h} h ago`;
  return `${Math.round(h / 24)} d ago`;
}

export const triggerLabel: Record<string, string> = {
  http: "HTTP API",
  queue: "Queue worker",
  event: "Event stream",
  timer: "Scheduled job",
  orchestration: "Workflow",
  storage: "Storage trigger",
  others: "Other",
};
