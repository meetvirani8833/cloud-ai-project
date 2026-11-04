import clsx from "clsx";
import { useId } from "react";

/** The mark: a capacity ring filled to the level demand needs, the product's whole idea in one shape. */
export function LogoMark({ size = 28, className, inverted }: { size?: number; className?: string; inverted?: boolean }) {
  const id = useId();
  const ring = inverted ? "#ffffff" : "var(--color-ink)";
  const fill = inverted ? "rgba(255,255,255,0.9)" : "var(--color-brand-500)";
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" className={className} aria-hidden>
      <defs>
        <clipPath id={id}>
          <circle cx="16" cy="16" r="9" />
        </clipPath>
      </defs>
      <circle cx="16" cy="16" r="13.5" fill="none" stroke={ring} strokeWidth="2.6" />
      <rect x="0" y="17.5" width="32" height="16" fill={fill} clipPath={`url(#${id})`} />
      <line x1="7" x2="25" y1="17.5" y2="17.5" stroke={fill} strokeWidth="1.2" strokeLinecap="round" opacity="0.5" />
    </svg>
  );
}

export function Logo({ className, inverted, subtitle = true }: { className?: string; inverted?: boolean; subtitle?: boolean }) {
  return (
    <div className={clsx("flex items-center gap-2.5", className)}>
      <LogoMark inverted={inverted} />
      <div className="leading-none">
        <div className={clsx("text-[17px] font-semibold tracking-[-0.02em]", inverted ? "text-white" : "text-ink")}>
          cloud<span className={inverted ? "text-white/70" : "text-brand-600"}>opt</span>
        </div>
        {subtitle && <div className={clsx("mt-1 text-[11px] tracking-wide", inverted ? "text-white/60" : "text-faint")}>Capacity, right-sized</div>}
      </div>
    </div>
  );
}
