import { ArrowCounterClockwiseIcon, LightningIcon, PauseIcon, PlayIcon, PlusIcon, TrashIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { Page, PageHeader } from "../components/layout/AppShell";
import { ConfirmDialog, Dialog, Tabs, useToast } from "../components/ui/overlay";
import { Badge, Button, Card, CardHeader, Field, Loading, inputClass } from "../components/ui/primitives";
import { EmptyState, QueryView } from "../components/ui/states";
import { api } from "../lib/api";
import { useAuth } from "../lib/auth";
import { simTime } from "../lib/format";
import { useAction, useAudit, usePricing, useReplay, useServices, useTenants, useUsers } from "../lib/queries";
import type { Pricing, Role, User } from "../lib/types";

type TabKey = "replay" | "pricing" | "users" | "audit";

export default function Settings() {
  const [tab, setTab] = useState<TabKey>("replay");
  return (
    <Page>
      <PageHeader title="Settings" subtitle="Platform configuration (admins only)" />
      <Tabs value={tab} onChange={setTab} items={[
        { value: "replay", label: "Replay & scenarios" }, { value: "pricing", label: "Pricing models" },
        { value: "users", label: "Users" }, { value: "audit", label: "Audit log" },
      ]} />
      <div className="mt-5">
        {tab === "replay" && <ReplayPanel />}
        {tab === "pricing" && <PricingPanel />}
        {tab === "users" && <UsersPanel />}
        {tab === "audit" && <AuditPanel />}
      </div>
    </Page>
  );
}

const SPEEDS = [{ s: 30, label: "10× (30 s per 5 min)" }, { s: 10, label: "30×" }, { s: 5, label: "60×" }, { s: 2, label: "150×" }, { s: 1, label: "300×" }];

function ReplayPanel() {
  const r = useReplay();
  const services = useServices();
  const toast = useToast();
  const [confirmReset, setConfirmReset] = useState(false);
  const [sc, setSc] = useState({ service_id: "", kind: "spike", magnitude: 4, duration_slots: 3 });
  const control = useAction((b: object) => api("/api/replay/control", { method: "POST", json: b }));
  const reset = useAction(() => api("/api/replay/reset", { method: "POST" }));
  const inject = useAction((b: object) => api("/api/replay/scenario", { method: "POST", json: b }), [["anomalies"]]);
  const d = r.data;
  const scErr = !sc.service_id ? "Choose a service" : sc.magnitude <= 1 && sc.kind !== "drop" ? "Magnitude must be above 1×" : null;
  const fail = (e: unknown) => toast("bad", (e as Error).message);
  return (
    <div className="grid grid-cols-1 gap-5 xl:grid-cols-2">
      <Card>
        <CardHeader title="Telemetry replay" subtitle="Azure Functions 2019 trace replayed through the ingestion pipeline at accelerated speed" />
        <div className="space-y-5 p-5">
          {!d ? <Loading className="h-24" /> : (
            <>
              <div className="grid grid-cols-1 gap-3 text-sm sm:grid-cols-3">
                <div className="rounded-xl border border-line p-3"><div className="text-xs text-muted">Status</div><div className="mt-1 font-semibold">{d.finished ? "Finished" : d.running ? "Running" : "Paused"}</div></div>
                <div className="rounded-xl border border-line p-3"><div className="text-xs text-muted">Simulated time</div><div className="num mt-1 font-semibold">{simTime(d.sim_time)}</div></div>
                <div className="rounded-xl border border-line p-3"><div className="text-xs text-muted">Progress</div><div className="num mt-1 font-semibold">{(d.progress * 100).toFixed(0)}% · day {d.trace_day}/14</div></div>
              </div>
              <div className="flex flex-wrap items-end gap-3">
                <Field label="Speed" htmlFor="speed">
                  <select id="speed" className={`${inputClass} w-48`} value={d.seconds_per_slot}
                    onChange={(e) => control.mutate({ seconds_per_slot: Number(e.target.value) }, { onError: fail })}>
                    {SPEEDS.map((x) => <option key={x.s} value={x.s}>{x.label}</option>)}
                    {!SPEEDS.some((x) => x.s === d.seconds_per_slot) && <option value={d.seconds_per_slot}>{d.speedup}×</option>}
                  </select>
                </Field>
                {d.running ? (
                  <Button icon={<PauseIcon size={16} weight="fill" />} loading={control.isPending} onClick={() => control.mutate({ running: false }, { onError: fail })}>Pause</Button>
                ) : (
                  <Button variant="primary" icon={<PlayIcon size={16} weight="fill" />} disabled={d.finished} loading={control.isPending}
                    title={d.finished ? "Reset the replay to start again" : undefined}
                    onClick={() => control.mutate({ running: true }, { onError: fail })}>Resume</Button>
                )}
                <Button variant="danger" icon={<ArrowCounterClockwiseIcon size={16} />} onClick={() => setConfirmReset(true)}>Reset to onboarding</Button>
              </div>
            </>
          )}
        </div>
        <ConfirmDialog open={confirmReset} onClose={() => setConfirmReset(false)} tone="danger" confirmLabel="Reset replay" loading={reset.isPending}
          title="Reset the replay?"
          body="All replayed telemetry, forecasts, anomalies and recommendations since onboarding are deleted, and every service returns to its pre-onboarding replica count. Users and settings are kept."
          onConfirm={() => reset.mutate(undefined, { onSuccess: () => { setConfirmReset(false); toast("good", "Replay reset to the onboarding day"); }, onError: fail })} />
      </Card>

      <Card>
        <CardHeader title="Inject a scenario" subtitle="Test how CloudOpt reacts. Spikes and drops should be flagged as anomalies; a gradual surge should be forecast, not flagged." />
        <form className="grid grid-cols-1 gap-4 p-5 sm:grid-cols-2" onSubmit={(e) => {
          e.preventDefault();
          if (scErr) return;
          inject.mutate(sc, { onSuccess: () => toast("good", "Scenario starts at the next replayed interval"), onError: fail });
        }}>
          <div className="sm:col-span-2">
            <Field label="Service" htmlFor="sc-svc">
              <select id="sc-svc" className={inputClass} value={sc.service_id} onChange={(e) => setSc({ ...sc, service_id: e.target.value })}>
                <option value="">Select a service…</option>
                {(services.data ?? []).map((s) => <option key={s.id} value={s.id}>{s.tenant_name} / {s.name}</option>)}
              </select>
            </Field>
          </div>
          <Field label="Type" htmlFor="sc-kind">
            <select id="sc-kind" className={inputClass} value={sc.kind} onChange={(e) => setSc({ ...sc, kind: e.target.value, duration_slots: e.target.value === "surge" ? 12 : 3 })}>
              <option value="spike">Sudden spike</option><option value="drop">Sudden drop (outage upstream)</option><option value="surge">Gradual surge (expected growth)</option>
            </select>
          </Field>
          <Field label={sc.kind === "drop" ? "Drop factor (÷)" : "Magnitude (×)"} htmlFor="sc-mag">
            <input id="sc-mag" type="number" step="0.5" min={1.5} max={50} className={inputClass} value={sc.magnitude} onChange={(e) => setSc({ ...sc, magnitude: Number(e.target.value) })} />
          </Field>
          <Field label="Duration (5-min intervals)" htmlFor="sc-dur" hint={`${sc.duration_slots * 5} simulated minutes`}>
            <input id="sc-dur" type="number" min={1} max={288} className={inputClass} value={sc.duration_slots} onChange={(e) => setSc({ ...sc, duration_slots: Number(e.target.value) })} />
          </Field>
          <div className="flex items-end justify-end sm:col-span-1">
            <Button type="submit" variant="primary" icon={<LightningIcon size={16} weight="fill" />} disabled={!!scErr || !d?.running || !!d?.scenario} loading={inject.isPending}
              title={!d?.running ? "Resume the replay first" : d?.scenario ? "A scenario is already running" : scErr ?? undefined}>Inject</Button>
          </div>
          {d?.scenario && (
            <div className="rounded-lg bg-brand-50 px-3 py-2 text-[13px] text-brand-700 sm:col-span-2" role="status">
              Running: {d.scenario.kind} on {d.scenario.service_name} ({d.scenario.magnitude}×), {d.scenario.slots_left} interval(s) left.
            </div>
          )}
        </form>
      </Card>
    </div>
  );
}

function PricingPanel() {
  const q = usePricing();
  const tenants = useTenants();
  const toast = useToast();
  const [edit, setEdit] = useState<Partial<Pricing> & { isNew?: boolean } | null>(null);
  const [del, setDel] = useState<Pricing | null>(null);
  const save = useAction((p: Pricing) => api(`/api/pricing/${p.key}`, { method: "PUT", json: { label: p.label, vcpu_hour: p.vcpu_hour, gb_hour: p.gb_hour } }), [["pricing"]]);
  const remove = useAction((key: string) => api(`/api/pricing/${key}`, { method: "DELETE" }), [["pricing"]]);
  const usage = (key: string) => (tenants.data ?? []).filter((t) => t.pricing_key === key).length;
  const keyErr = edit?.isNew && !/^[a-z0-9_]{2,40}$/.test(edit.key ?? "") ? "2–40 lowercase letters, digits or underscores" :
    edit?.isNew && q.data?.some((p) => p.key === edit.key) ? "A model with this key already exists" : null;
  const valErr = !edit?.label?.trim() ? "Name is required" : !(edit.vcpu_hour! > 0) ? "vCPU price must be above 0" : !(edit.gb_hour! >= 0) ? "Memory price can't be negative" : null;
  return (
    <Card>
      <CardHeader title="Pricing models" subtitle="Provider-agnostic: add your negotiated or reseller rates. Presets are approximate public on-demand prices."
        action={<Button size="sm" variant="primary" icon={<PlusIcon size={16} weight="bold" />} onClick={() => setEdit({ isNew: true, key: "", label: "", vcpu_hour: 0.04, gb_hour: 0.004 })}>Add model</Button>} />
      <QueryView query={q} loading={<Loading className="h-40" />}>
        {(rows) => (
          <div className="overflow-x-auto pt-3">
            <table className="w-full min-w-[640px] text-sm">
              <thead className="text-xs text-muted"><tr className="border-y border-line text-left">
                <th className="px-5 py-2.5 font-medium">Model</th><th className="px-3 py-2.5 text-right font-medium">per vCPU-hour</th>
                <th className="px-3 py-2.5 text-right font-medium">per GB-hour</th><th className="px-3 py-2.5 text-right font-medium">Tenants</th><th className="px-5 py-2.5" />
              </tr></thead>
              <tbody className="divide-y divide-line">
                {rows.map((p) => (
                  <tr key={p.key}>
                    <td className="px-5 py-2.5"><div className="font-medium">{p.label}</div><div className="text-xs text-muted">{p.key} {p.is_custom ? <Badge tone="brand" className="ml-1">Custom</Badge> : <Badge className="ml-1">Preset</Badge>}</div></td>
                    <td className="num px-3 py-2.5 text-right">${p.vcpu_hour.toFixed(5)}</td>
                    <td className="num px-3 py-2.5 text-right">${p.gb_hour.toFixed(5)}</td>
                    <td className="num px-3 py-2.5 text-right">{usage(p.key)}</td>
                    <td className="px-5 py-2.5 text-right">
                      <div className="flex justify-end gap-1">
                        <Button size="sm" variant="ghost" onClick={() => setEdit(p)}>Edit</Button>
                        {p.is_custom && <Button size="sm" variant="ghost" className="text-bad" icon={<TrashIcon size={15} />} onClick={() => setDel(p)} aria-label={`Delete ${p.label}`} />}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
      <Dialog open={!!edit} onClose={() => setEdit(null)} title={edit?.isNew ? "Add pricing model" : `Edit ${edit?.label ?? ""}`}
        footer={<>
          <Button onClick={() => setEdit(null)} disabled={save.isPending}>Cancel</Button>
          <Button variant="primary" disabled={!!keyErr || !!valErr} loading={save.isPending}
            onClick={() => save.mutate(edit as Pricing, { onSuccess: () => { setEdit(null); toast("good", "Pricing model saved. Costs use it from the next interval."); }, onError: (e) => toast("bad", (e as Error).message) })}>Save</Button>
        </>}>
        {edit && (
          <div className="space-y-4">
            {edit.isNew && <Field label="Key" htmlFor="pk" error={edit.key ? keyErr : null} hint="Used in the API, e.g. my_reseller">
              <input id="pk" className={inputClass} value={edit.key} onChange={(e) => setEdit({ ...edit, key: e.target.value.toLowerCase() })} /></Field>}
            <Field label="Display name" htmlFor="pl"><input id="pl" maxLength={120} className={inputClass} value={edit.label} onChange={(e) => setEdit({ ...edit, label: e.target.value })} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="USD per vCPU-hour" htmlFor="pv"><input id="pv" type="number" step="0.0001" min={0} className={inputClass} value={edit.vcpu_hour} onChange={(e) => setEdit({ ...edit, vcpu_hour: Number(e.target.value) })} /></Field>
              <Field label="USD per GB-hour" htmlFor="pg"><input id="pg" type="number" step="0.0001" min={0} className={inputClass} value={edit.gb_hour} onChange={(e) => setEdit({ ...edit, gb_hour: Number(e.target.value) })} /></Field>
            </div>
            {valErr && <p className="text-xs text-bad">{valErr}</p>}
            {!edit.isNew && usage(edit.key!) > 0 && <p className="text-xs text-warn">Used by {usage(edit.key!)} tenant(s). Their costs change from the next interval.</p>}
          </div>
        )}
      </Dialog>
      <ConfirmDialog open={!!del} onClose={() => setDel(null)} tone="danger" confirmLabel="Delete" loading={remove.isPending}
        title={`Delete ${del?.label}?`} body={del && usage(del.key) > 0 ? `This model is used by ${usage(del.key)} tenant(s). Switch them to another model first.` : "This can't be undone."}
        onConfirm={() => del && remove.mutate(del.key, { onSuccess: () => { setDel(null); toast("good", "Pricing model deleted"); }, onError: (e) => { setDel(null); toast("bad", (e as Error).message); } })} />
    </Card>
  );
}

function UsersPanel() {
  const q = useUsers();
  const { user: me } = useAuth();
  const tenants = useTenants();
  const toast = useToast();
  const [form, setForm] = useState<{ email: string; full_name: string; password: string; role: Role; tenant_id: string } | null>(null);
  const [toggle, setToggle] = useState<User | null>(null);
  const create = useAction((b: object) => api("/api/users", { method: "POST", json: b }), [["users"]]);
  const update = useAction((v: { id: number; body: object }) => api(`/api/users/${v.id}`, { method: "PATCH", json: v.body }), [["users"]]);
  const tName = (id: string | null) => (id ? tenants.data?.find((t) => t.id === id)?.name ?? id : "All tenants");
  const err = !form ? null : !/^\S+@\S+\.\S+$/.test(form.email) ? "Enter a valid email" : !form.full_name.trim() ? "Name is required" :
    form.password.length < 8 ? "Password must be at least 8 characters" : null;
  return (
    <Card>
      <CardHeader title="Users" subtitle="Admins can approve changes and configure the platform. Viewers are read-only and can be limited to one tenant."
        action={<Button size="sm" variant="primary" icon={<PlusIcon size={16} weight="bold" />} onClick={() => setForm({ email: "", full_name: "", password: "", role: "viewer", tenant_id: "" })}>Invite user</Button>} />
      <QueryView query={q} loading={<Loading className="h-40" />} isEmpty={(d) => d.length === 0} empty={<EmptyState compact title="No users" />}>
        {(rows) => (
          <div className="overflow-x-auto pt-3">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="text-xs text-muted"><tr className="border-y border-line text-left">
                <th className="px-5 py-2.5 font-medium">User</th><th className="px-3 py-2.5 font-medium">Role</th><th className="px-3 py-2.5 font-medium">Access</th>
                <th className="px-3 py-2.5 font-medium">Last sign-in</th><th className="px-5 py-2.5" />
              </tr></thead>
              <tbody className="divide-y divide-line">
                {rows.map((u) => (
                  <tr key={u.id} className={!u.is_active ? "text-muted" : ""}>
                    <td className="px-5 py-2.5"><div className="font-medium">{u.full_name}{u.id === me?.id && <span className="ml-1 text-xs text-muted">(you)</span>}</div><div className="text-xs text-muted">{u.email}</div></td>
                    <td className="px-3 py-2.5">
                      <select aria-label={`Role for ${u.email}`} className={`${inputClass} h-8 w-28`} value={u.role} disabled={u.id === me?.id || update.isPending}
                        onChange={(e) => update.mutate({ id: u.id, body: { role: e.target.value } }, { onSuccess: () => toast("good", "Role updated"), onError: (er) => toast("bad", (er as Error).message) })}>
                        <option value="admin">Admin</option><option value="viewer">Viewer</option>
                      </select>
                    </td>
                    <td className="px-3 py-2.5">{tName(u.tenant_id)}</td>
                    <td className="px-3 py-2.5 text-ink-2">{u.last_login_at ? new Date(u.last_login_at).toLocaleString() : "Never"}</td>
                    <td className="px-5 py-2.5 text-right">
                      {!u.is_active && <Badge className="mr-2">Disabled</Badge>}
                      <Button size="sm" variant={u.is_active ? "danger" : "secondary"} disabled={u.id === me?.id} title={u.id === me?.id ? "You can't disable your own account" : undefined}
                        onClick={() => setToggle(u)}>{u.is_active ? "Disable" : "Enable"}</Button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
      <Dialog open={!!form} onClose={() => setForm(null)} title="Invite user"
        footer={<><Button onClick={() => setForm(null)} disabled={create.isPending}>Cancel</Button>
          <Button variant="primary" disabled={!!err} loading={create.isPending}
            onClick={() => form && create.mutate({ ...form, tenant_id: form.role === "viewer" && form.tenant_id ? form.tenant_id : null },
              { onSuccess: () => { setForm(null); toast("good", `${form.email} can now sign in`); }, onError: (e) => toast("bad", (e as Error).message) })}>Create user</Button></>}>
        {form && (
          <div className="space-y-4">
            <Field label="Full name" htmlFor="un"><input id="un" className={inputClass} value={form.full_name} onChange={(e) => setForm({ ...form, full_name: e.target.value })} /></Field>
            <Field label="Email" htmlFor="ue"><input id="ue" type="email" className={inputClass} value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} /></Field>
            <Field label="Temporary password" htmlFor="up" hint="At least 8 characters. Share it securely; the user can change it after signing in.">
              <input id="up" type="text" autoComplete="new-password" className={inputClass} value={form.password} onChange={(e) => setForm({ ...form, password: e.target.value })} /></Field>
            <div className="grid grid-cols-2 gap-3">
              <Field label="Role" htmlFor="ur"><select id="ur" className={inputClass} value={form.role} onChange={(e) => setForm({ ...form, role: e.target.value as Role })}><option value="viewer">Viewer</option><option value="admin">Admin</option></select></Field>
              <Field label="Tenant access" htmlFor="ut" hint={form.role === "admin" ? "Admins always see every tenant" : undefined}>
                <select id="ut" className={inputClass} disabled={form.role === "admin"} value={form.role === "admin" ? "" : form.tenant_id} onChange={(e) => setForm({ ...form, tenant_id: e.target.value })}>
                  <option value="">All tenants</option>{(tenants.data ?? []).map((t) => <option key={t.id} value={t.id}>{t.name}</option>)}
                </select>
              </Field>
            </div>
            {err && (form.email || form.full_name || form.password) && <p className="text-xs text-bad">{err}</p>}
          </div>
        )}
      </Dialog>
      <ConfirmDialog open={!!toggle} onClose={() => setToggle(null)} tone={toggle?.is_active ? "danger" : "primary"} loading={update.isPending}
        confirmLabel={toggle?.is_active ? "Disable account" : "Enable account"} title={toggle?.is_active ? `Disable ${toggle?.email}?` : `Enable ${toggle?.email}?`}
        body={toggle?.is_active ? "They are signed out immediately and can't sign in until re-enabled." : "They will be able to sign in again."}
        onConfirm={() => toggle && update.mutate({ id: toggle.id, body: { is_active: !toggle.is_active } }, {
          onSuccess: () => { setToggle(null); toast("good", "Account updated"); }, onError: (e) => { setToggle(null); toast("bad", (e as Error).message); },
        })} />
    </Card>
  );
}

function AuditPanel() {
  const q = useAudit();
  return (
    <Card>
      <CardHeader title="Audit log" subtitle="Sign-ins, approvals, configuration changes and replay controls (most recent 200)" />
      <QueryView query={q} loading={<Loading className="h-48" />} isEmpty={(d) => d.length === 0} empty={<EmptyState compact title="Nothing logged yet" />}>
        {(rows) => (
          <div className="overflow-x-auto pt-3">
            <table className="w-full min-w-[720px] text-sm">
              <thead className="text-xs text-muted"><tr className="border-y border-line text-left">
                <th className="px-5 py-2.5 font-medium">When (real time)</th><th className="px-3 py-2.5 font-medium">Actor</th><th className="px-3 py-2.5 font-medium">Action</th><th className="px-5 py-2.5 font-medium">Details</th>
              </tr></thead>
              <tbody className="divide-y divide-line">
                {rows.map((a) => (
                  <tr key={a.id}>
                    <td className="num whitespace-nowrap px-5 py-2 text-ink-2">{new Date(a.at).toLocaleString()}</td>
                    <td className="px-3 py-2">{a.actor}</td>
                    <td className="px-3 py-2"><Badge>{a.action.replace(/_/g, " ")}</Badge> {a.target && <span className="text-xs text-muted">{a.target}</span>}</td>
                    <td className="max-w-[360px] truncate px-5 py-2 text-xs text-muted" title={JSON.stringify(a.details)}>{Object.keys(a.details ?? {}).length ? JSON.stringify(a.details) : "—"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </QueryView>
    </Card>
  );
}
