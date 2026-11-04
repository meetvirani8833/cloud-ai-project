import { EyeIcon, EyeSlashIcon, InfoIcon } from "@phosphor-icons/react";
import { useState, type FormEvent } from "react";
import { Navigate, useLocation } from "react-router-dom";
import { Logo } from "../components/ui/Logo";
import { Button, Field, inputClass } from "../components/ui/primitives";
import { ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";

const DEMO_ACCOUNTS = [
  { email: "admin@cloudopt.dev", role: "Admin: approve actions, configure pricing and replay" },
  { email: "viewer@cloudopt.dev", role: "Viewer: read-only across all tenants" },
  { email: "ops@northvale.cloudopt.dev", role: "Tenant viewer: Northvale Analytics only" },
];

export default function Login() {
  const { status, signIn, signedOutReason } = useAuth();
  const loc = useLocation() as { state?: { from?: string } };
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [show, setShow] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const showDemo = import.meta.env.VITE_SHOW_DEMO_ACCOUNTS !== "false";

  if (status === "authenticated") return <Navigate to={loc.state?.from ?? "/"} replace />;

  async function submit(e: FormEvent) {
    e.preventDefault();
    if (busy) return;
    if (!email.trim() || !password) {
      setError("Enter your email and password.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      await signIn(email.trim(), password);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Sign-in failed. Please try again.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="grid min-h-full lg:grid-cols-[1fr_1.05fr]">
      <div className="flex items-center justify-center px-6 py-12">
        <div className="w-full max-w-sm">
          <Logo className="mb-10" />
          <h1 className="text-2xl font-bold tracking-tight">Sign in</h1>
          <p className="mt-1.5 text-sm text-muted">Your team is too small to hire a FinOps engineer. CloudOpt acts like one.</p>

          {signedOutReason && !error && (
            <div className="mt-6 flex gap-2 rounded-lg border border-warn/30 bg-warn-soft px-3 py-2.5 text-[13px] text-warn" role="status">
              <InfoIcon size={16} className="mt-0.5 shrink-0" />{signedOutReason}
            </div>
          )}
          {error && (
            <div className="mt-6 rounded-lg border border-bad/25 bg-bad-soft px-3 py-2.5 text-[13px] text-bad" role="alert">{error}</div>
          )}

          <form onSubmit={submit} className="mt-6 space-y-4" noValidate>
            <Field label="Work email" htmlFor="email">
              <input id="email" type="email" autoComplete="username" autoFocus value={email}
                onChange={(e) => setEmail(e.target.value)} className={inputClass} placeholder="you@company.com" />
            </Field>
            <Field label="Password" htmlFor="password">
              <div className="relative">
                <input id="password" type={show ? "text" : "password"} autoComplete="current-password" value={password}
                  onChange={(e) => setPassword(e.target.value)} className={`${inputClass} pr-10`} />
                <button type="button" onClick={() => setShow((s) => !s)} className="absolute inset-y-0 right-0 grid w-10 place-items-center text-muted hover:text-ink"
                  aria-label={show ? "Hide password" : "Show password"}>
                  {show ? <EyeSlashIcon size={17} /> : <EyeIcon size={17} />}
                </button>
              </div>
            </Field>
            <Button type="submit" variant="primary" className="w-full" loading={busy}>{busy ? "Signing in…" : "Sign in"}</Button>
          </form>

          {showDemo && (
            <div className="mt-8 rounded-xl border border-line bg-surface p-4">
              <p className="text-xs font-semibold uppercase tracking-wider text-faint">Demo accounts</p>
              <ul className="mt-2 space-y-1.5">
                {DEMO_ACCOUNTS.map((a) => (
                  <li key={a.email}>
                    <button type="button" onClick={() => { setEmail(a.email); setError(null); }}
                      className="w-full rounded-lg px-2 py-1.5 text-left hover:bg-canvas">
                      <span className="block text-[13px] font-medium text-brand-600">{a.email}</span>
                      <span className="block text-xs text-muted">{a.role}</span>
                    </button>
                  </li>
                ))}
              </ul>
              <p className="mt-2 px-2 text-xs text-muted">Ask your evaluator pack for the shared demo password.</p>
            </div>
          )}
        </div>
      </div>

      <div className="relative hidden overflow-hidden bg-ink lg:block">
        <svg className="absolute -right-40 -top-40 size-[640px] text-white/[0.05]" viewBox="0 0 32 32" aria-hidden>
          <circle cx="16" cy="16" r="13.5" fill="none" stroke="currentColor" strokeWidth="0.6" />
          <circle cx="16" cy="16" r="9" fill="none" stroke="currentColor" strokeWidth="0.6" />
        </svg>
        <div className="relative flex h-full flex-col justify-center px-14 text-white">
          <p className="text-xs font-semibold uppercase tracking-[0.2em] text-brand-400">FinOps autopilot</p>
          <h2 className="mt-4 max-w-md text-[32px] font-semibold leading-[1.15] tracking-tight">Right-size every service before demand arrives.</h2>
          <ol className="mt-12 max-w-md divide-y divide-white/10 border-y border-white/10">
            {[
              { t: "Forecast demand", d: "Quantile forecasts from 5 minutes to 24 hours ahead, with calibrated confidence bands." },
              { t: "Protect the SLA", d: "Scales out ahead of load, holds capacity through anomalies, and scales in only when it is safe." },
              { t: "Explain every change", d: "Each recommendation states the forecast, the risk and the dollar impact, fact-checked before you see it." },
            ].map((x, i) => (
              <li key={x.t} className="flex gap-5 py-5">
                <span className="num pt-0.5 text-sm font-medium text-white/40">0{i + 1}</span>
                <div><div className="font-semibold">{x.t}</div><div className="mt-1 text-sm leading-relaxed text-white/60">{x.d}</div></div>
              </li>
            ))}
          </ol>
        </div>
      </div>
    </div>
  );
}
