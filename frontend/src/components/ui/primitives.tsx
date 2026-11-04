import clsx from "clsx";
import type { ButtonHTMLAttributes, ReactNode } from "react";

export function Card({ className, children, ...rest }: { className?: string; children: ReactNode } & React.HTMLAttributes<HTMLDivElement>) {
  return (
    <div className={clsx("rounded-2xl border border-line bg-surface shadow-card", className)} {...rest}>
      {children}
    </div>
  );
}

export function CardHeader({ title, subtitle, action, className }: { title: ReactNode; subtitle?: ReactNode; action?: ReactNode; className?: string }) {
  return (
    <div className={clsx("flex items-start justify-between gap-3 px-5 pt-4", className)}>
      <div className="min-w-0">
        <h3 className="truncate text-[15px] font-semibold text-ink">{title}</h3>
        {subtitle && <p className="mt-0.5 text-xs text-muted">{subtitle}</p>}
      </div>
      {action && <div className="shrink-0">{action}</div>}
    </div>
  );
}

type Variant = "primary" | "secondary" | "ghost" | "danger" | "success";
const variants: Record<Variant, string> = {
  primary: "bg-brand-500 text-white hover:bg-brand-600 active:bg-brand-700 disabled:bg-brand-200",
  secondary: "bg-surface text-ink-2 border border-line-strong hover:bg-canvas disabled:text-faint",
  ghost: "text-ink-2 hover:bg-canvas disabled:text-faint",
  danger: "bg-surface text-bad border border-line-strong hover:bg-bad-soft hover:border-bad/40 disabled:text-faint",
  success: "bg-good text-white hover:bg-good/90 disabled:bg-good/40",
};

export function Button({
  variant = "secondary",
  size = "md",
  loading,
  icon,
  className,
  children,
  disabled,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: Variant; size?: "sm" | "md"; loading?: boolean; icon?: ReactNode }) {
  return (
    <button
      className={clsx(
        "inline-flex items-center justify-center gap-1.5 rounded-lg font-medium transition-colors disabled:cursor-not-allowed",
        size === "sm" ? "h-8 px-3 text-[13px]" : "h-9 px-3.5 text-sm",
        variants[variant],
        className,
      )}
      disabled={disabled || loading}
      aria-busy={loading || undefined}
      {...rest}
    >
      {loading ? <Dots /> : icon}
      {children}
    </button>
  );
}

type Tone = "neutral" | "brand" | "good" | "bad" | "warn";
const tones: Record<Tone, string> = {
  neutral: "bg-canvas text-ink-2 border-line",
  brand: "bg-brand-50 text-brand-700 border-brand-100",
  good: "bg-good-soft text-good border-good/15",
  bad: "bg-bad-soft text-bad border-bad/15",
  warn: "bg-warn-soft text-warn border-warn/20",
};

export function Badge({ tone = "neutral", children, className, title }: { tone?: Tone; children: ReactNode; className?: string; title?: string }) {
  return (
    <span title={title} className={clsx("inline-flex items-center gap-1 whitespace-nowrap rounded-full border px-2 py-0.5 text-[11.5px] font-medium", tones[tone], className)}>
      {children}
    </span>
  );
}

/** Three dots that breathe in sequence: the inline loading indicator. */
export function Dots({ className }: { className?: string }) {
  return (
    <span className={clsx("dots", className)} role="status" aria-label="Loading">
      <i /><i /><i />
    </span>
  );
}

/** Block-level loading state: quiet dots with an optional caption, centred in the space the content will take. */
export function Loading({ label, className }: { label?: string; className?: string }) {
  return (
    <div className={clsx("flex flex-col items-center justify-center gap-2.5 text-[13px] text-faint", className ?? "h-40")}>
      <Dots className="text-brand-500" />
      {label && <span>{label}</span>}
    </div>
  );
}

/** Placeholder for a chart: the empty axes frame with a slowly tracing baseline. */
export function ChartGhost({ height = 280, label = "Loading chart" }: { height?: number; label?: string }) {
  return (
    <div className="relative mx-2" style={{ height }} role="status" aria-label={label}>
      <svg className="absolute inset-0 h-full w-full" preserveAspectRatio="none" viewBox="0 0 100 100" aria-hidden>
        {[20, 40, 60, 80].map((y) => <line key={y} x1="0" x2="100" y1={y} y2={y} stroke="var(--color-line)" strokeWidth="0.4" vectorEffect="non-scaling-stroke" />)}
        <path className="ghost-trace" d="M0 70 C 12 64, 20 72, 32 60 S 52 48, 64 55 S 86 40, 100 46" fill="none" stroke="var(--color-brand-200)" strokeWidth="2" vectorEffect="non-scaling-stroke" />
      </svg>
      <span className="absolute bottom-3 left-1/2 -translate-x-1/2 text-xs text-faint">{label}…</span>
    </div>
  );
}

export function Field({ label, hint, error, children, htmlFor }: { label: string; hint?: ReactNode; error?: string | null; children: ReactNode; htmlFor?: string }) {
  return (
    <div className="space-y-1.5">
      <label htmlFor={htmlFor} className="block text-[13px] font-medium text-ink-2">{label}</label>
      {children}
      {error ? <p className="text-xs text-bad" role="alert">{error}</p> : hint ? <p className="text-xs text-muted">{hint}</p> : null}
    </div>
  );
}

export const inputClass =
  "h-9 w-full rounded-lg border border-line-strong bg-surface px-3 text-sm text-ink placeholder:text-faint focus:border-brand-500 focus:outline-none focus:ring-3 focus:ring-brand-100 disabled:bg-canvas disabled:text-muted";
