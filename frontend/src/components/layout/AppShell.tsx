import {
  BuildingsIcon, ChartBarIcon, DotsThreeIcon, FlaskIcon, GearSixIcon, HardDrivesIcon, LightningIcon, ListChecksIcon,
  SignOutIcon, SquaresFourIcon, type Icon,
} from "@phosphor-icons/react";
import { useIsFetching } from "@tanstack/react-query";
import clsx from "clsx";
import { Suspense, useEffect, useState, type ReactNode } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { ApiError } from "../../lib/api";
import { useAuth } from "../../lib/auth";
import { clock, simTime } from "../../lib/format";
import { useOverview, useReplay } from "../../lib/queries";
import { Logo, LogoMark } from "../ui/Logo";
import { Loading } from "../ui/primitives";

interface NavDef { to: string; label: string; icon: Icon; end?: boolean; badge?: "pending"; adminOnly?: boolean; hideForTenantUser?: boolean }

const MAIN: NavDef[] = [
  { to: "/", label: "Overview", icon: SquaresFourIcon, end: true },
  { to: "/recommendations", label: "Recommendations", icon: ListChecksIcon, badge: "pending" },
  { to: "/tenants", label: "Tenants", icon: BuildingsIcon, hideForTenantUser: true },
  { to: "/services", label: "Services", icon: HardDrivesIcon },
  { to: "/anomalies", label: "Anomalies", icon: LightningIcon },
  { to: "/evaluation", label: "Evaluation", icon: FlaskIcon },
];
const WORKSPACE: NavDef[] = [
  { to: "/reports", label: "Savings report", icon: ChartBarIcon },
  { to: "/settings", label: "Settings", icon: GearSixIcon, adminOnly: true },
];

function usePending() {
  return useOverview(null, 24).data?.pending_recommendations ?? 0;
}

function visible(items: NavDef[], isAdmin: boolean, tenantScoped: boolean) {
  return items.filter((i) => (!i.adminOnly || isAdmin) && (!i.hideForTenantUser || !tenantScoped));
}

function NavItem({ item, pending }: { item: NavDef; pending: number }) {
  const I = item.icon;
  const badge = item.badge === "pending" ? pending : 0;
  return (
    <NavLink to={item.to} end={item.end}
      className={({ isActive }) => clsx(
        "group flex items-center gap-3 rounded-lg px-3 py-2 text-sm transition-colors",
        isActive ? "bg-brand-50 font-semibold text-brand-700" : "font-medium text-ink-2 hover:bg-canvas hover:text-ink",
      )}>
      {({ isActive }) => (
        <>
          <I size={19} weight={isActive ? "fill" : "regular"} className="shrink-0" />
          <span className="flex-1 truncate">{item.label}</span>
          {badge > 0 && <span className="num rounded-md bg-ink px-1.5 py-px text-[11px] font-semibold text-white">{badge > 99 ? "99+" : badge}</span>}
        </>
      )}
    </NavLink>
  );
}

function UserCard() {
  const { user, isAdmin, signOut } = useAuth();
  const initials = user?.full_name?.split(" ").map((p) => p[0]).slice(0, 2).join("") || "?";
  return (
    <div className="flex items-center gap-3 px-2">
      <div className="grid size-9 shrink-0 place-items-center rounded-full bg-ink text-[13px] font-semibold text-white">{initials}</div>
      <div className="min-w-0 flex-1">
        <div className="truncate text-sm font-medium" title={user?.full_name}>{user?.full_name}</div>
        <div className="truncate text-xs text-muted">{isAdmin ? "Admin" : user?.tenant_id ? "Tenant viewer" : "Viewer"}</div>
      </div>
      <button onClick={() => signOut()} className="grid size-9 place-items-center rounded-lg text-muted hover:bg-canvas hover:text-ink" title="Sign out" aria-label="Sign out">
        <SignOutIcon size={18} />
      </button>
    </div>
  );
}

function Sidebar() {
  const { user, isAdmin } = useAuth();
  const pending = usePending();
  const scoped = !!user?.tenant_id;
  return (
    <aside className="flex h-full w-64 flex-col border-r border-line bg-surface px-3 py-5">
      <Logo className="px-2" />
      <nav className="mt-8 space-y-0.5" aria-label="Main">
        {visible(MAIN, isAdmin, scoped).map((i) => <NavItem key={i.to} item={i} pending={pending} />)}
      </nav>
      <div className="mt-7 px-3 text-[11px] font-semibold uppercase tracking-wider text-faint">Workspace</div>
      <nav className="mt-2 space-y-0.5" aria-label="Workspace">
        {visible(WORKSPACE, isAdmin, scoped).map((i) => <NavItem key={i.to} item={i} pending={pending} />)}
      </nav>
      <div className="mt-auto border-t border-line pt-4"><UserCard /></div>
    </aside>
  );
}

/** Mobile: four primary destinations in a bottom bar, everything else under "More". */
function TabBar({ onMore, moreOpen }: { onMore: () => void; moreOpen: boolean }) {
  const pending = usePending();
  const tabs: NavDef[] = [MAIN[0], { ...MAIN[1], label: "Approvals" }, MAIN[3], MAIN[4]];
  return (
    <nav className="pb-safe fixed inset-x-0 bottom-0 z-40 border-t border-line bg-surface/95 backdrop-blur lg:hidden" aria-label="Primary">
      <div className="grid h-16 grid-cols-5">
        {tabs.map((t) => {
          const I = t.icon;
          return (
            <NavLink key={t.to} to={t.to} end={t.end}
              className={({ isActive }) => clsx("relative flex flex-col items-center justify-center gap-1 text-[11px]",
                isActive && !moreOpen ? "font-semibold text-brand-600" : "font-medium text-muted")}>
              {({ isActive }) => (
                <>
                  <I size={22} weight={isActive && !moreOpen ? "fill" : "regular"} />
                  {t.label}
                  {t.badge && pending > 0 && (
                    <span className="num absolute left-1/2 top-2 ml-2 min-w-4 rounded-full bg-ink px-1 text-center text-[10px] font-semibold leading-4 text-white">{pending > 9 ? "9+" : pending}</span>
                  )}
                </>
              )}
            </NavLink>
          );
        })}
        <button onClick={onMore} className={clsx("flex flex-col items-center justify-center gap-1 text-[11px]", moreOpen ? "font-semibold text-brand-600" : "font-medium text-muted")}
          aria-expanded={moreOpen} aria-label="More pages">
          <DotsThreeIcon size={22} weight="bold" />More
        </button>
      </div>
    </nav>
  );
}

function MoreSheet({ open, onClose }: { open: boolean; onClose: () => void }) {
  const { user, isAdmin } = useAuth();
  const pending = usePending();
  if (!open) return null;
  const items = visible([...MAIN.slice(2, 3), MAIN[5], ...WORKSPACE], isAdmin, !!user?.tenant_id);
  return (
    <div className="fixed inset-0 z-30 lg:hidden">
      <div className="absolute inset-0 bg-ink/25" onClick={onClose} />
      <div className="pb-safe absolute inset-x-0 bottom-16 rounded-t-2xl border-t border-line bg-surface px-3 pb-3 pt-2 shadow-pop">
        <div className="mx-auto mb-2 h-1 w-10 rounded-full bg-line-strong" />
        <nav className="space-y-0.5" onClick={(e) => (e.target as HTMLElement).closest("a") && onClose()}>
          {items.map((i) => <NavItem key={i.to} item={i} pending={pending} />)}
        </nav>
        <div className="mt-3 border-t border-line pt-3"><UserCard /></div>
      </div>
    </div>
  );
}

export function SimClock({ compact }: { compact?: boolean }) {
  const r = useReplay();
  if (!r.data) return <span className="text-xs text-faint">Connecting…</span>;
  const d = r.data;
  const live = d.running && !d.finished;
  const state = d.finished ? "Replay finished" : live ? `Live · ${d.speedup}×` : "Paused";
  return (
    <div className={clsx("flex items-center gap-2 rounded-full border border-line bg-surface text-xs text-ink-2", compact ? "px-2.5 py-1" : "px-3 py-1.5 shadow-card")}
      title="Telemetry replayed from the Azure Functions 2019 trace at accelerated speed. Times are simulated (UTC).">
      <span className="relative flex size-2">
        {live && <span className="absolute inset-0 animate-ping rounded-full bg-good/60" />}
        <span className={clsx("relative size-2 rounded-full", live ? "bg-good" : "bg-faint")} />
      </span>
      <span className="font-medium">{state}</span>
      <span className="text-line-strong">|</span>
      <span className="num">{compact ? clock(d.sim_time) : `${simTime(d.sim_time)} UTC`}</span>
    </div>
  );
}

function ConnectionBanner() {
  const r = useReplay();
  if (!(r.error instanceof ApiError && r.error.isNetwork)) return null;
  return (
    <div className="border-b border-warn/30 bg-warn-soft px-4 py-2 text-center text-[13px] text-warn" role="status">
      Connection to the server was lost. Showing the last data received; retrying automatically.
    </div>
  );
}

/** Thin bar along the top while any view is fetching data it has never had. */
function TopProgress() {
  const first = useIsFetching({ predicate: (q) => q.state.data === undefined });
  return first > 0 ? <div className="top-progress" role="progressbar" aria-label="Loading" /> : null;
}

export function AppShell() {
  const [more, setMore] = useState(false);
  const loc = useLocation();
  useEffect(() => setMore(false), [loc.pathname]);
  return (
    <div className="flex h-full">
      <div className="hidden lg:block"><Sidebar /></div>
      <div className="relative flex min-w-0 flex-1 flex-col">
        <TopProgress />
        <ConnectionBanner />
        <header className="flex items-center justify-between border-b border-line bg-surface px-4 py-2.5 lg:hidden">
          <div className="flex items-center gap-2"><LogoMark size={24} /><span className="text-[15px] font-semibold tracking-[-0.02em]">cloud<span className="text-brand-600">opt</span></span></div>
          <SimClock compact />
        </header>
        <main className="min-w-0 flex-1 overflow-y-auto pb-20 lg:pb-0">
          <Suspense fallback={<Loading className="h-64" />}>
            <Outlet />
          </Suspense>
        </main>
      </div>
      <MoreSheet open={more} onClose={() => setMore(false)} />
      <TabBar onMore={() => setMore((m) => !m)} moreOpen={more} />
    </div>
  );
}

export function PageHeader({ title, subtitle, actions, filters }: { title: string; subtitle?: ReactNode; actions?: ReactNode; filters?: ReactNode }) {
  return (
    <div className="mb-5 sm:mb-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="min-w-0">
          <h1 className="text-xl font-bold tracking-tight sm:text-[22px]">{title}</h1>
          {subtitle && <p className="mt-1 text-[13px] text-muted sm:text-sm">{subtitle}</p>}
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <span className="hidden lg:block"><SimClock /></span>
          {actions}
        </div>
      </div>
      {filters && <div className="mt-4 flex flex-wrap gap-2 border-t border-line pt-4 sm:mt-5 sm:gap-3 sm:pt-5">{filters}</div>}
    </div>
  );
}

export function Page({ children }: { children: ReactNode }) {
  return <div className="mx-auto max-w-[1400px] px-4 py-5 sm:px-6 sm:py-6 lg:px-8">{children}</div>;
}

/** Filter dropdown styled like "Timeframe: All-time" pills. */
export function FilterSelect<T extends string>({ label, value, onChange, options }: {
  label: string; value: T; onChange: (v: T) => void; options: { value: T; label: string }[];
}) {
  return (
    <label className="relative flex h-11 w-full min-w-0 items-center rounded-xl border border-line bg-surface px-3 text-sm shadow-card sm:h-10 sm:w-auto sm:min-w-[200px]">
      <span className="shrink-0 text-muted">{label}:</span>
      <select value={value} onChange={(e) => onChange(e.target.value as T)}
        className="ml-1 h-full min-w-0 flex-1 cursor-pointer appearance-none truncate bg-transparent pr-6 font-semibold text-ink focus:outline-none">
        {options.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
      </select>
      <svg className="pointer-events-none absolute right-3 size-3 text-ink-2" viewBox="0 0 12 12" aria-hidden><path d="M2 4l4 4 4-4" stroke="currentColor" strokeWidth="1.6" fill="none" /></svg>
    </label>
  );
}
