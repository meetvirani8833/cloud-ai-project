import clsx from "clsx";
import { CaretLeftIcon, CaretRightIcon, CheckCircleIcon, CheckIcon, XIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { Link } from "react-router-dom";
import { FilterSelect, Page, PageHeader } from "../components/layout/AppShell";
import { Dialog, Tabs, useToast } from "../components/ui/overlay";
import { Badge, Button, Card, Loading, inputClass } from "../components/ui/primitives";
import { EmptyState, QueryView } from "../components/ui/states";
import { api, ApiError } from "../lib/api";
import { useAuth } from "../lib/auth";
import { ago, money, pct } from "../lib/format";
import { useAction, useRecommendations, useReplay, useTenants } from "../lib/queries";
import type { Recommendation } from "../lib/types";
import { StatusBadge } from "./Overview";

type TabKey = "pending" | "auto_applied" | "history";
const TAB_STATUS: Record<TabKey, string> = {
  pending: "pending",
  auto_applied: "auto_applied",
  history: "approved,rejected,expired,superseded",
};
const PAGE = 20;

export default function Recommendations() {
  const { user } = useAuth();
  const [tab, setTab] = useState<TabKey>("pending");
  const [tenant, setTenant] = useState("all");
  const [page, setPage] = useState(0);
  const tenantId = user?.tenant_id ?? (tenant === "all" ? null : tenant);
  const tenants = useTenants();
  const q = useRecommendations({ status: TAB_STATUS[tab], tenant_id: tenantId, limit: PAGE, offset: page * PAGE });
  const counts = {
    pending: useRecommendations({ status: "pending", tenant_id: tenantId, limit: 1 }).data?.total,
    auto: useRecommendations({ status: "auto_applied", tenant_id: tenantId, limit: 1 }).data?.total,
  };
  const total = q.data?.total ?? 0;
  const pages = Math.max(1, Math.ceil(total / PAGE));

  return (
    <Page>
      <PageHeader
        title="Recommendations"
        subtitle="Every scaling decision, the evidence behind it, and its cost impact"
        filters={!user?.tenant_id ? (
          <FilterSelect label="Tenant" value={tenant} onChange={(v) => { setTenant(v); setPage(0); }}
            options={[{ value: "all", label: "All tenants" }, ...(tenants.data ?? []).map((t) => ({ value: t.id, label: t.name }))]} />
        ) : undefined}
      />
      <Tabs value={tab} onChange={(v) => { setTab(v); setPage(0); }} items={[
        { value: "pending", label: "Needs approval", count: counts.pending },
        { value: "auto_applied", label: "Autopilot", count: counts.auto },
        { value: "history", label: "History" },
      ]} />
      <div className="mt-5">
        <QueryView query={q}
          loading={<Card><Loading className="h-56" label="Loading recommendations" /></Card>}
          isEmpty={(d) => d.items.length === 0}
          empty={<Card><EmptyState
            icon={tab === "pending" ? <CheckCircleIcon size={30} weight="light" className="text-good" /> : undefined}
            title={tab === "pending" ? "You're all caught up" : tab === "auto_applied" ? "No autopilot changes yet" : "No decisions yet"}
            body={tab === "pending"
              ? "Changes larger than a service's autopilot guardrail appear here for approval. Small adjustments are applied automatically."
              : "Decisions appear here as the replay runs."} /></Card>}>
          {(d) => (
            <div className="space-y-4">
              {d.items.map((r) => <RecCard key={r.id} rec={r} />)}
              {pages > 1 && (
                <div className="flex items-center justify-between pt-2 text-sm text-muted">
                  <span className="num">{page * PAGE + 1}–{Math.min(total, (page + 1) * PAGE)} of {total}</span>
                  <div className="flex gap-2">
                    <Button size="sm" icon={<CaretLeftIcon size={14} />} disabled={page === 0} onClick={() => setPage((p) => p - 1)}>Previous</Button>
                    <Button size="sm" disabled={page >= pages - 1} onClick={() => setPage((p) => p + 1)}>Next <CaretRightIcon size={14} /></Button>
                  </div>
                </div>
              )}
            </div>
          )}
        </QueryView>
      </div>
    </Page>
  );
}

const SOURCE: Record<string, { label: string; title: string }> = {
  openai: { label: "LLM explanation · fact-checked", title: "Written by the LLM from the structured facts and verified against them before display" },
  openai_retry: { label: "LLM explanation · fact-checked", title: "The LLM's first draft failed verification; the corrected draft passed" },
  fallback: { label: "Template (AI draft failed fact-check)", title: "The LLM's drafts did not match the facts, so the deterministic template is shown instead" },
  template: { label: "Template explanation", title: "Deterministic template generated from the structured facts" },
  pending: { label: "Writing explanation…", title: "The explanation is being generated" },
};

function parseExplanation(text: string) {
  const fields: Record<string, string> = {};
  for (const line of text.split("\n")) {
    const m = line.match(/^([A-Za-z ]+):\s*(.*)$/);
    if (m) fields[m[1].trim().toLowerCase()] = m[2].trim();
  }
  return fields;
}

export function RecCard({ rec, compact }: { rec: Recommendation; compact?: boolean }) {
  const { isAdmin } = useAuth();
  const toast = useToast();
  const replay = useReplay().data;
  const [rejecting, setRejecting] = useState(false);
  const [note, setNote] = useState("");
  const up = rec.action === "scale_out";
  const f = rec.facts;
  const ex = parseExplanation(rec.explanation);
  const slotsLeft = replay ? rec.expires_slot - replay.current_slot - 1 : null;
  const minutesLeft = slotsLeft == null ? null : Math.max(0, slotsLeft * 5);

  const decide = useAction(async (v: { approve: boolean; note?: string }) =>
    api<Recommendation>(`/api/recommendations/${rec.id}/${v.approve ? "approve" : "reject"}`, { method: "POST", json: { note: v.note || null } }));

  const run = (approve: boolean, n?: string) =>
    decide.mutate({ approve, note: n }, {
      onSuccess: () => {
        toast("good", approve ? `Approved: ${f.service_name} scales to ${rec.recommended_replicas} replicas at the next interval.` : `Rejected. CloudOpt won't suggest this again for an hour.`);
        setRejecting(false);
      },
      onError: (e) => toast("bad", e instanceof ApiError && e.isConflict ? `${e.message}. The list has been refreshed.` : (e as Error).message),
    });

  const pending = rec.status === "pending";
  return (
    <Card className={clsx("relative overflow-hidden", pending && "ring-1 ring-warn/25")}>
      {/* Direction as a quiet edge rule rather than an icon tile. */}
      <span className={clsx("absolute inset-y-0 left-0 w-[3px]", up ? "bg-warn" : "bg-good")} aria-hidden />
      <div className="flex flex-wrap items-start gap-4 p-4 sm:p-5">
        <div className="min-w-0 flex-1">
          <div className="flex flex-wrap items-center gap-2">
            <Link to={`/services/${rec.service_id}`} className="truncate text-[15px] font-semibold hover:text-brand-600">{f.service_name}</Link>
            <span className="text-sm text-muted">· {f.tenant_name}</span>
            <StatusBadge status={rec.status} />
            {f.anomaly && <Badge tone="bad">Anomaly: {f.anomaly.kind}</Badge>}
          </div>
          <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
            <span>{ago(rec.created_at, replay?.sim_time)}</span>
            <span>{f.replica_size} replicas</span>
            <span title={SOURCE[rec.explanation_source]?.title ?? SOURCE.template.title}>
              {SOURCE[rec.explanation_source]?.label ?? SOURCE.template.label}
            </span>
            {pending && minutesLeft != null && (
              <span className={clsx(minutesLeft <= 10 && "font-medium text-warn")}>
                {minutesLeft > 0 ? `Expires in ${minutesLeft} min (simulated)` : "Expiring now"}
              </span>
            )}
          </div>
        </div>
        <div className="grid w-full grid-cols-2 gap-x-6 gap-y-3 sm:flex sm:w-auto sm:flex-wrap sm:items-center sm:text-right">
          <Metric label="Replicas" value={<>{rec.current_replicas} <span className="text-faint">→</span> {rec.recommended_replicas}</>} />
          <Metric label="Cost impact" value={`${money(f.hourly_cost_delta, { signed: true, cents: true })}/hr`} tone={f.hourly_cost_delta > 0 ? "bad" : "good"}
            sub={`${money(f.monthly_cost_delta, { signed: true })}/mo`} />
          <Metric label="SLA risk" value={<>{pct(rec.risk_current)} <span className="text-faint">→</span> {pct(rec.risk_recommended)}</>}
            tone={rec.risk_recommended < rec.risk_current ? "good" : "neutral"} />
          {!compact && <Metric label="Confidence" value={pct(rec.confidence)} sub={rec.confidence >= 0.75 ? "High" : rec.confidence >= 0.5 ? "Medium" : "Low"} />}
        </div>
      </div>

      <div className="border-t border-line bg-canvas/60 px-4 py-4 sm:px-5">
        <p className="text-sm leading-relaxed text-ink-2"><span className="font-semibold text-ink">Why: </span>{ex["reason"] ?? rec.explanation}</p>
        {rec.decided_by && rec.status !== "auto_applied" && (
          <p className="mt-2 text-xs text-muted">
            {rec.status === "approved" ? "Approved" : rec.status === "rejected" ? "Rejected" : "Closed"} by {rec.decided_by}
            {rec.decision_note && <> · “{rec.decision_note}”</>}
          </p>
        )}
      </div>

      {pending && (
        <div className="grid grid-cols-2 gap-2 border-t border-line px-4 py-3 sm:flex sm:flex-wrap sm:items-center sm:justify-end sm:px-5">
          {!isAdmin && <span className="col-span-2 text-xs text-muted sm:mr-auto">Only admins can approve or reject changes.</span>}
          <Button variant="danger" className="h-11 sm:h-9" icon={<XIcon size={16} />} disabled={!isAdmin || decide.isPending} onClick={() => setRejecting(true)}
            title={!isAdmin ? "Admin role required" : undefined}>Reject</Button>
          <Button variant="primary" className="h-11 sm:h-9" icon={<CheckIcon size={16} weight="bold" />} disabled={!isAdmin} loading={decide.isPending && decide.variables?.approve}
            onClick={() => run(true)} title={!isAdmin ? "Admin role required" : undefined}>
            <span className="sm:hidden">Approve</span>
            <span className="hidden sm:inline">Approve {rec.current_replicas} → {rec.recommended_replicas}</span>
          </Button>
        </div>
      )}

      <Dialog open={rejecting} onClose={() => setRejecting(false)} title="Reject recommendation"
        footer={<>
          <Button onClick={() => setRejecting(false)} disabled={decide.isPending}>Cancel</Button>
          <Button variant="danger" loading={decide.isPending} onClick={() => run(false, note.trim())}>Reject</Button>
        </>}>
        <p className="text-sm text-ink-2">
          {f.service_name} stays at <b>{rec.current_replicas}</b> replicas. CloudOpt won't propose another {up ? "scale-out" : "scale-in"} for this service for one hour.
          {up && rec.risk_current > 0.2 && <span className="mt-2 block text-bad">Heads-up: the model estimates a {pct(rec.risk_current)} chance of an SLA violation at the current capacity.</span>}
        </p>
        <label className="mt-4 block text-[13px] font-medium text-ink-2" htmlFor="note">Reason (optional, saved to the audit log)</label>
        <textarea id="note" maxLength={500} value={note} onChange={(e) => setNote(e.target.value)} rows={3}
          className={`${inputClass} mt-1.5 h-auto py-2`} placeholder="e.g. Planned maintenance window" />
        <div className="mt-1 text-right text-xs text-faint num">{note.length}/500</div>
      </Dialog>
    </Card>
  );
}

function Metric({ label, value, sub, tone = "neutral" }: { label: string; value: React.ReactNode; sub?: string; tone?: "good" | "bad" | "neutral" }) {
  return (
    <div className="min-w-[78px]">
      <div className="text-[11px] font-medium uppercase tracking-wide text-faint">{label}</div>
      <div className={clsx("num mt-0.5 text-sm font-semibold", tone === "good" ? "text-good" : tone === "bad" ? "text-bad" : "text-ink")}>{value}</div>
      {sub && <div className="num text-xs text-muted">{sub}</div>}
    </div>
  );
}
