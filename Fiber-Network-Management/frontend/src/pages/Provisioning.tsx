import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { del, post, put } from "../api";
import { useAuth } from "../auth";
import { useTerms } from "../components/Layout";
import { Badge, Card, ConfirmDialog, ErrorBox, Form, KV, Loading, Modal, PageHeader, Pager, SimTag, Table, useToast } from "../components/ui";
import { ago, fmtTime, label, mbps } from "../format";
import { useGet, useInvalidate } from "../hooks";

// ================================================================= provisioning jobs
const STATES = ["draft", "validation_pending", "validation_failed", "awaiting_approval", "queued", "running", "succeeded", "failed", "partially_succeeded", "rolled_back", "cancelled"];

export function Jobs() {
  const [state, setState] = useState("");
  const [op, setOp] = useState("");
  const [offset, setOffset] = useState(0);
  const q = useGet<any>("/provisioning/jobs", { state, operation: op, limit: 50, offset }, 10000);
  const nav = useNavigate();
  return (
    <>
      <PageHeader title="Provisioning jobs" subtitle="Every provisioning request has a job id, a reviewed plan, an execution log and a verification result." actions={
        <>
          <select value={state} onChange={(e) => { setState(e.target.value); setOffset(0); }}><option value="">All states</option>{STATES.map((s) => <option key={s} value={s}>{label(s)}</option>)}</select>
          <select value={op} onChange={(e) => { setOp(e.target.value); setOffset(0); }}><option value="">All operations</option>{["register_ont", "activate_service", "modify_service", "suspend_service", "restore_service", "disconnect_service", "deregister_ont"].map((s) => <option key={s} value={s}>{label(s)}</option>)}</select>
        </>
      } />
      <Card>{!q.data ? <Loading /> : <>
        <Table rows={q.data.items} onRow={(j: any) => nav(`/provisioning/jobs/${j.id}`)} cols={[
          { key: "id", title: "Job", render: (j: any) => <><span className="mono">{j.id.slice(0, 8)}</span> <SimTag on={j.is_simulated} /></> },
          { key: "operation", title: "Operation", render: (j: any) => label(j.operation) },
          { key: "state", title: "State", render: (j: any) => <Badge value={j.state} /> },
          { key: "requested_by", title: "Requested by" }, { key: "approved_by", title: "Approved by" },
          { key: "created_at", title: "Created", render: (j: any) => <span title={fmtTime(j.created_at)}>{ago(j.created_at)}</span> },
          { key: "error", title: "Problem", render: (j: any) => j.error || j.errors?.[0] || "" },
        ]} />
        <Pager {...q.data} onChange={setOffset} />
      </>}</Card>
    </>
  );
}

export function JobDetail() {
  const { id } = useParams();
  const q = useGet<any>(`/provisioning/jobs/${id}`, undefined, 3000);
  const { can } = useAuth();
  const inv = useInvalidate();
  const nav = useNavigate();
  const [confirm, setConfirm] = useState(false);
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  if (!q.data) return q.error ? <ErrorBox error={q.error} /> : <Loading />;
  const j = q.data;
  const v = j.validation || {};
  const act = async (fn: () => Promise<any>) => { setBusy(true); setErr(null); try { const r = await fn(); inv(); return r; } catch (e) { setErr(e); } finally { setBusy(false); } };
  const destructive = !!j.confirmation_token;
  return (
    <>
      <PageHeader crumbs={[["Provisioning jobs", "/provisioning/jobs"], [j.id.slice(0, 8)]]} title={<>{label(j.operation)} <SimTag on={j.is_simulated} /></>}
        subtitle={<><Badge value={j.state} /> · job <span className="mono">{j.id}</span> · requested by {j.requested_by} {fmtTime(j.created_at)}</>}
        actions={<>
          {j.state === "awaiting_approval" && can("network.read") && <button className={destructive ? "danger" : "primary"} disabled={busy} onClick={() => setConfirm(true)}>Approve & execute</button>}
          {j.cancellable && can("provisioning.request") && <button disabled={busy} onClick={() => act(() => post(`/provisioning/jobs/${j.id}/cancel`))}>Cancel</button>}
          {j.retryable && can("provisioning.request") && <button disabled={busy} onClick={async () => { const r = await act(() => post(`/provisioning/jobs/${j.id}/retry`)); if (r) nav(`/provisioning/jobs/${r.id}`); }}>Retry safely</button>}
        </>} />
      <ErrorBox error={err} />
      {["validation_pending", "queued", "running"].includes(j.state) && <div className="note">Working in the background… this page refreshes automatically. (Requires the worker process.)</div>}
      {j.retry_of && <div className="note">Retry of <Link to={`/provisioning/jobs/${j.retry_of}`}>{j.retry_of.slice(0, 8)}</Link>. Steps already applied on the device are detected and skipped rather than repeated.</div>}
      {j.retries?.length > 0 && <div className="note">Retried as {j.retries.map((r: string) => <Link key={r} to={`/provisioning/jobs/${r}`}>{r.slice(0, 8)} </Link>)}</div>}
      <div className="grid c2">
        <Card title="Request">
          <KV rows={[
            ["OLT", j.olt_id ? <Link to={`/olts/${j.olt_id}`}>OLT #{j.olt_id}</Link> : "—"], ["ONT", j.ont_id ? <Link to={`/onts/${j.ont_id}`}>ONT #{j.ont_id}</Link> : "—"],
            ["Service", j.service_id ? `Service #${j.service_id}` : "—"], ["Template", j.template_id ? `#${j.template_id}` : "—"],
            ["Rollback on failure", j.rollback_on_failure ? "yes" : "no"], ["External request id", j.external_request_id], ["Approved", j.approved_by ? `${j.approved_by} ${fmtTime(j.approved_at)}` : "—"],
            ["Started", fmtTime(j.started_at)], ["Finished", fmtTime(j.finished_at)],
          ]} />
          <details style={{ marginTop: 8 }}><summary>Request parameters</summary><pre className="code">{JSON.stringify(j.request, null, 2)}</pre></details>
        </Card>
        <Card title="Validation">
          {!j.validation ? <div className="vendor">Not validated yet.</div> : <>
            <div style={{ marginBottom: 8 }}>{v.ok ? <Badge value="validation passed" tone="ok" /> : <Badge value="validation failed" tone="bad" />} <span className="vendor">{fmtTime(v.validated_at)}</span></div>
            {v.errors?.length > 0 && <div className="error-box"><ul>{v.errors.map((e: string) => <li key={e}>{e}</li>)}</ul></div>}
            {v.warnings?.length > 0 && <div className="note warn"><ul style={{ margin: 0, paddingLeft: 18 }}>{v.warnings.map((e: string) => <li key={e}>{e}</li>)}</ul></div>}
            {v.dry_run?.length > 0 && <><h3 style={{ margin: "8px 0" }}>Device dry run</h3>{v.dry_run.map((d: any) => (
              <div key={d.seq} style={{ marginBottom: 6 }}><Badge value={d.status} tone={d.status === "ok" ? "ok" : d.status === "error" ? "bad" : "muted"} /> step {d.seq} {label(d.action)} — {d.message}
                {d.commands?.length > 0 && <pre className="code">{d.commands.join("\n")}</pre>}</div>))}</>}
          </>}
        </Card>
      </div>
      {j.verification && <Card title="Verification (read back from device)"><KV rows={[["Result", j.verification.ok ? <Badge value="verified" tone="ok" /> : <Badge value="not verified" tone="bad" />], ["ONT state after", j.verification.ont_state], ["Checked", fmtTime(j.verification.checked_at)]]} /></Card>}
      {j.error && <div className="error-box"><b>Error:</b> {j.error}</div>}
      <Card title="Provisioning plan & execution">
        <ol className="steps">
          {j.steps.map((s: any) => (
            <li key={s.id}>
              <span className="seq">{s.seq}</span>
              <div style={{ flex: 1 }}>
                <div><b>{s.description || label(s.action)}</b> <Badge value={s.state} /> {s.destructive && <Badge value="destructive" tone="bad" />}</div>
                <div className="vendor">{label(s.action)} · {s.params ? Object.entries(s.params).map(([k, val]) => `${k}=${val}`).join(", ") : ""}</div>
                {s.compensation && <div className="vendor">compensation: {s.compensation.action}</div>}
                {s.output?.message && <div>{s.output.message}</div>}
                {s.output?.commands?.length > 0 && <pre className="code">{s.output.commands.join("\n")}</pre>}
                {s.error && <div style={{ color: "var(--bad)" }}>{s.error}</div>}
              </div>
              <div className="vendor">{fmtTime(s.finished_at)}</div>
            </li>
          ))}
        </ol>
      </Card>
      <Card title="Execution history">
        <Table rows={j.events} cols={[{ key: "ts", title: "Time", render: (e: any) => fmtTime(e.ts) }, { key: "from_state", title: "From", render: (e: any) => (e.from_state ? <Badge value={e.from_state} /> : "") },
          { key: "to_state", title: "To", render: (e: any) => (e.to_state ? <Badge value={e.to_state} /> : "") }, { key: "actor", title: "By" }, { key: "message", title: "Message" }]} />
      </Card>
      {confirm && (
        <ConfirmDialog title="Approve provisioning plan" destructive={destructive} token={j.confirmation_token || undefined} busy={busy}
          message={<>Execute {j.steps.length} step(s) for <b>{label(j.operation)}</b> through the OLT adapter. {destructive && "This plan contains destructive steps that remove configuration from the device."}</>}
          onClose={() => setConfirm(false)}
          onConfirm={async (typed) => { await act(() => post(`/provisioning/jobs/${j.id}/approve`, { confirm: typed || undefined })); setConfirm(false); }} />
      )}
    </>
  );
}

// ================================================================= subscribers & services
const SUB_FIELDS = [
  { name: "customer_ref", label: "Customer reference", required: true }, { name: "display_name", label: "Customer name / label", required: true },
  { name: "account_ref", label: "Account reference" }, { name: "external_customer_id", label: "External customer ID", help: "Billing system id (optional, not a foreign key)" },
  { name: "external_account_id", label: "External account ID" }, { name: "contact_phone", label: "Contact phone" },
  { name: "installation_address", label: "Installation address" }, { name: "latitude", label: "Latitude", type: "number" as const, step: "any" },
  { name: "longitude", label: "Longitude", type: "number" as const, step: "any" }, { name: "notes", label: "Notes", type: "textarea" as const },
];

export function Subscribers() {
  const [q, setQ] = useState("");
  const [serial, setSerial] = useState("");
  const [fat, setFat] = useState("");
  const [offset, setOffset] = useState(0);
  const fats = useGet<any[]>("/fats");
  const r = useGet<any>("/subscribers", { q, ont_serial: serial, fat_id: fat, limit: 50, offset });
  const [create, setCreate] = useState(false);
  const nav = useNavigate();
  const { can } = useAuth();
  const t = useTerms();
  return (
    <>
      <PageHeader title="Subscribers & services" subtitle="Lightweight network-side references. The billing system remains authoritative for commercial customer data."
        actions={can("subscriber.write") && <button className="primary" onClick={() => setCreate(true)}>Add subscriber</button>} />
      <div className="filters">
        <input placeholder="Name, customer/account ref, address" value={q} onChange={(e) => { setQ(e.target.value); setOffset(0); }} />
        <input placeholder="ONT serial" value={serial} onChange={(e) => { setSerial(e.target.value); setOffset(0); }} />
        <select value={fat} onChange={(e) => { setFat(e.target.value); setOffset(0); }}><option value="">Any {t.fat}</option>{fats.data?.map((f) => <option key={f.id} value={f.id}>{f.name}</option>)}</select>
      </div>
      <Card>{!r.data ? <Loading /> : <>
        <Table rows={r.data.items} onRow={(s: any) => nav(`/subscribers/${s.id}`)} cols={[
          { key: "customer_ref", title: "Customer", render: (s: any) => <><b>{s.display_name}</b> <SimTag on={s.is_simulated} /><div className="vendor">{s.customer_ref} · {s.account_ref}</div></> },
          { key: "installation_address", title: "Address" },
          { key: "services", title: "Services", render: (s: any) => s.services.map((x: any) => <div key={x.id}>{x.service_ref} <Badge value={x.status} /> {x.template}</div>) },
          { key: "ont", title: "ONT / OLT / PON", render: (s: any) => s.services.filter((x: any) => x.ont).map((x: any) => <div key={x.id} className="mono">{x.ont.serial_number} <Badge value={x.ont.status} /> {x.ont.olt} {x.ont.pon_port}</div>) },
          { key: "fat", title: t.fat, render: (s: any) => s.services.filter((x: any) => x.fat).map((x: any) => <div key={x.id}>{x.fat.fat_name} P{x.fat.fat_port_number}</div>) },
        ]} />
        <Pager {...r.data} onChange={setOffset} />
      </>}</Card>
      {create && <Modal title="Add subscriber" onClose={() => setCreate(false)} wide>
        <Form fields={SUB_FIELDS} onCancel={() => setCreate(false)} onSubmit={async (v) => { const s = await post("/subscribers", v); nav(`/subscribers/${s.id}`); }} />
      </Modal>}
    </>
  );
}

export function SubscriberDetail() {
  const { id } = useParams();
  const q = useGet<any>(`/subscribers/${id}`);
  const templates = useGet<any[]>("/service-templates");
  const { can } = useAuth();
  const inv = useInvalidate();
  const nav = useNavigate();
  const t = useTerms();
  const { push } = useToast();
  const [edit, setEdit] = useState(false);
  const [newSvc, setNewSvc] = useState(false);
  const [op, setOp] = useState<{ svc: any; op: string } | null>(null);
  const [err, setErr] = useState<any>(null);
  if (!q.data) return q.error ? <ErrorBox error={q.error} /> : <Loading />;
  const s = q.data;
  const request = async (body: any) => {
    setErr(null);
    try {
      const j = await post("/provisioning/jobs", body);
      push("Provisioning request created — review the plan", "info");
      nav(`/provisioning/jobs/${j.id}`);
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <>
      <PageHeader crumbs={[["Subscribers", "/subscribers"], [s.customer_ref]]} title={<>{s.display_name} <SimTag on={s.is_simulated} /></>}
        subtitle={`${s.customer_ref} · ${s.installation_address || "no address"}`}
        actions={<>{can("subscriber.write") && <button onClick={() => setEdit(true)}>Edit</button>}{can("subscriber.write") && <button className="primary" onClick={() => setNewSvc(true)}>Add service</button>}</>} />
      <ErrorBox error={err} />
      <Card title="Subscriber reference">
        <KV rows={[["Customer ref", s.customer_ref], ["Account ref", s.account_ref], ["External customer ID", s.external_customer_id], ["External account ID", s.external_account_id],
          ["Phone", s.contact_phone], ["GPS", s.latitude != null ? `${s.latitude}, ${s.longitude}` : null], ["Notes", s.notes]]} />
      </Card>
      {s.services.map((x: any) => (
        <Card key={x.id} title={<>Service {x.service_ref} <Badge value={x.status} /></>} actions={can("provisioning.request") && (
          <>
            {["pending", "failed"].includes(x.status) && x.ont && <button className="primary" onClick={() => setOp({ svc: x, op: "activate_service" })}>Activate</button>}
            {["pending", "failed"].includes(x.status) && !x.ont && <Link to="/discovered"><button>Register an ONT for this service</button></Link>}
            {x.status === "active" && <button onClick={() => setOp({ svc: x, op: "modify_service" })}>Change profile</button>}
            {x.status === "active" && <button onClick={() => request({ operation: "suspend_service", service_id: x.id })}>Suspend</button>}
            {x.status === "suspended" && <button onClick={() => request({ operation: "restore_service", service_id: x.id })}>Restore</button>}
            {["active", "suspended"].includes(x.status) && <button className="danger" onClick={() => setOp({ svc: x, op: "disconnect_service" })}>Disconnect…</button>}
          </>
        )}>
          <KV rows={[
            ["Template / profile", x.template], ["Activated", x.activation_date], ["External service ID", x.external_service_id], ["External package ID", x.external_package_id],
            ["ONT", x.ont ? <Link to={`/onts/${x.ont.id}`}>{x.ont.serial_number}</Link> : "not assigned"], ["ONT status", x.ont ? <Badge value={x.ont.status} /> : "—"],
            ["Rx power", x.ont?.rx_power_dbm != null ? `${x.ont.rx_power_dbm} dBm` : "—"], ["OLT", x.ont?.olt], ["PON port", x.ont ? <Link to={`/pon-ports/${x.ont.pon_port_id}`}>{x.ont.pon_port}</Link> : "—"],
            [t.fat, x.fat ? <Link to={`/fats/${x.fat.fat_id}`}>{x.fat.fat_name} port {x.fat.fat_port_number}</Link> : "—"],
            ["Network config", x.network_config?.service_ports?.length ? x.network_config.service_ports.map((p: any) => `service-port ${p.index} VLAN ${p.vlan} GEM ${p.gem_port}`).join("; ") : "none"],
            ["Last job", x.last_job_id ? <Link to={`/provisioning/jobs/${x.last_job_id}`}>{x.last_job_id.slice(0, 8)}</Link> : "—"],
          ]} />
        </Card>
      ))}
      {edit && <Modal title="Edit subscriber" onClose={() => setEdit(false)} wide><Form fields={SUB_FIELDS} initial={s} onCancel={() => setEdit(false)} onSubmit={async (v) => { await put(`/subscribers/${s.id}`, v); setEdit(false); inv(); }} /></Modal>}
      {newSvc && <Modal title="Add service reference" onClose={() => setNewSvc(false)}>
        <Form fields={[{ name: "service_ref", label: "Service reference", required: true }, { name: "template_id", label: "Service template", type: "select", options: (templates.data || []).map((x) => [x.id, x.name]) },
          { name: "external_service_id", label: "External service ID" }, { name: "external_package_id", label: "External package ID" }, { name: "notes", label: "Notes", type: "textarea" }]}
          onCancel={() => setNewSvc(false)} onSubmit={async (v) => { await post("/services", { ...v, subscriber_id: s.id, template_id: v.template_id ? Number(v.template_id) : null }); setNewSvc(false); inv(); }} />
      </Modal>}
      {op && op.op !== "disconnect_service" && (
        <Modal title={label(op.op)} onClose={() => setOp(null)}>
          <Form fields={[{ name: "template_id", label: "Service template", type: "select", required: true, options: (templates.data || []).filter((x) => x.is_active).map((x) => [x.id, x.name]) }]}
            initial={{ template_id: op.svc.template_id }} onCancel={() => setOp(null)} submitLabel="Create request"
            onSubmit={async (v) => request({ operation: op.op, service_id: op.svc.id, template_id: Number(v.template_id) })} />
        </Modal>
      )}
      {op && op.op === "disconnect_service" && (
        <Modal title="Disconnect service" onClose={() => setOp(null)}>
          <div className="confirm destructive">Disconnecting removes the service configuration from the OLT. You will review the plan and confirm again before anything is executed.</div>
          <Form fields={[{ name: "deregister_ont", label: "Also deregister the ONT from the OLT", type: "checkbox" }]} onCancel={() => setOp(null)} submitLabel="Create disconnect request"
            onSubmit={async (v) => request({ operation: "disconnect_service", service_id: op.svc.id, deregister_ont: v.deregister_ont })} />
        </Modal>
      )}
    </>
  );
}

// ================================================================= templates & profiles
export function Templates() {
  const q = useGet<any[]>("/service-templates");
  const bw = useGet<any[]>("/bandwidth-profiles");
  const adapters = useGet<any>("/adapters");
  const [edit, setEdit] = useState<any>(null);
  const { can } = useAuth();
  const inv = useInvalidate();
  return (
    <>
      <PageHeader title="Service templates" subtitle="Vendor-neutral definitions of what an internet service needs (speed, VLANs, GEM/T-CONT). Each template is translated per adapter / OLT model through a vendor mapping."
        actions={can("provisioning.profiles") && <button className="primary" onClick={() => setEdit({ vlan_mode: "single", service_type: "internet", is_active: true, mappings: [] })}>New template</button>} />
      <Card>{!q.data ? <Loading /> : <Table rows={q.data} onRow={can("provisioning.profiles") ? setEdit : undefined} cols={[
        { key: "name", title: "Template", render: (t: any) => <><b>{t.name}</b><div className="vendor">{t.description}</div></> }, { key: "service_type", title: "Type" },
        { key: "bw", title: "Speed ↓/↑", render: (t: any) => (t.bandwidth_profile ? `${mbps(t.bandwidth_profile.download_kbps)} / ${mbps(t.bandwidth_profile.upload_kbps)}` : "—") },
        { key: "vlan", title: "VLAN", render: (t: any) => `${t.vlan_mode} S:${t.svlan ?? "—"} C:${t.cvlan ?? "—"} U:${t.user_vlan ?? "—"}` },
        { key: "gem", title: "GEM / T-CONT", render: (t: any) => `${t.gem_port ?? "—"} / ${t.tcont ?? "—"}` }, { key: "wan_mode", title: "WAN" },
        { key: "mappings", title: "Vendor mappings", render: (t: any) => t.mappings.map((m: any) => <div key={m.id} className="vendor">{m.adapter}{m.olt_model ? ` [${m.olt_model}]` : ""}: line {m.line_profile_ref}, srv {m.service_profile_ref}, TT {m.traffic_table_up_ref}/{m.traffic_table_down_ref}</div>) },
        { key: "is_active", title: "Active", render: (t: any) => <Badge value={t.is_active ? "active" : "disabled"} /> },
      ]} />}</Card>
      {edit && <TemplateEditor tpl={edit} bw={bw.data || []} adapters={(adapters.data?.adapters || []).filter((a: any) => !a.planned)} onClose={() => { setEdit(null); inv(); }} />}
    </>
  );
}

function TemplateEditor({ tpl, bw, adapters, onClose }: { tpl: any; bw: any[]; adapters: any[]; onClose: () => void }) {
  const [v, setV] = useState<any>({ ...tpl, mappings: tpl.mappings.map((m: any) => ({ ...m })) });
  const [err, setErr] = useState<any>(null);
  const set = (k: string, val: any) => setV((s: any) => ({ ...s, [k]: val }));
  const setM = (i: number, k: string, val: any) => setV((s: any) => ({ ...s, mappings: s.mappings.map((m: any, j: number) => (j === i ? { ...m, [k]: val } : m)) }));
  const n = (x: any) => (x === "" || x === null || x === undefined ? null : Number(x));
  const save = async () => {
    setErr(null);
    const body = { name: v.name, service_type: v.service_type, bandwidth_profile_id: n(v.bandwidth_profile_id), vlan_mode: v.vlan_mode, svlan: n(v.svlan), cvlan: n(v.cvlan),
      user_vlan: n(v.user_vlan), gem_port: n(v.gem_port), tcont: n(v.tcont), wan_mode: v.wan_mode || null, description: v.description || "", is_active: !!v.is_active,
      mappings: v.mappings.map((m: any) => ({ adapter: m.adapter, olt_model: m.olt_model || "", line_profile_ref: m.line_profile_ref || null, service_profile_ref: m.service_profile_ref || null,
        dba_profile_ref: m.dba_profile_ref || null, traffic_table_up_ref: m.traffic_table_up_ref || null, traffic_table_down_ref: m.traffic_table_down_ref || null })) };
    try { v.id ? await put(`/service-templates/${v.id}`, body) : await post("/service-templates", body); onClose(); } catch (e) { setErr(e); }
  };
  const inp = (k: string, l: string, type = "text") => <label className="field"><span>{l}</span><input type={type} value={v[k] ?? ""} onChange={(e) => set(k, e.target.value)} /></label>;
  return (
    <Modal title={v.id ? `Edit ${v.name}` : "New service template"} onClose={onClose} wide>
      <div className="form-grid">
        {inp("name", "Name *")}
        <label className="field"><span>Service type</span><select value={v.service_type} onChange={(e) => set("service_type", e.target.value)}>{["internet", "voip", "iptv", "management"].map((x) => <option key={x}>{x}</option>)}</select></label>
        <label className="field"><span>Bandwidth profile</span><select value={v.bandwidth_profile_id ?? ""} onChange={(e) => set("bandwidth_profile_id", e.target.value)}><option value="">—</option>{bw.map((b) => <option key={b.id} value={b.id}>{b.name}</option>)}</select></label>
        <label className="field"><span>VLAN mode</span><select value={v.vlan_mode} onChange={(e) => set("vlan_mode", e.target.value)}><option value="single">Single VLAN</option><option value="qinq">QinQ (S-VLAN + C-VLAN)</option><option value="transparent">Transparent</option></select></label>
        {inp("svlan", "Service / S-VLAN", "number")}{inp("cvlan", "C-VLAN (QinQ)", "number")}{inp("user_vlan", "User-side VLAN", "number")}
        {inp("gem_port", "GEM port", "number")}{inp("tcont", "T-CONT", "number")}
        <label className="field"><span>WAN mode</span><select value={v.wan_mode ?? ""} onChange={(e) => set("wan_mode", e.target.value)}><option value="">—</option>{["pppoe", "dhcp", "static", "bridge"].map((x) => <option key={x}>{x}</option>)}</select></label>
        <label className="field check"><span>Active</span><input type="checkbox" checked={!!v.is_active} onChange={(e) => set("is_active", e.target.checked)} /></label>
        <label className="field span2"><span>Description</span><textarea value={v.description ?? ""} onChange={(e) => set("description", e.target.value)} /></label>
      </div>
      <h3 style={{ margin: "14px 0 6px" }}>Vendor mappings</h3>
      <div className="note">The values refer to objects that must exist on the OLT (profile IDs / traffic-table indexes). They are interpreted only by the adapter.</div>
      {v.mappings.map((m: any, i: number) => (
        <div key={i} className="form-grid" style={{ borderTop: "1px solid var(--line-2)", paddingTop: 8, marginTop: 8 }}>
          <label className="field"><span>Adapter</span><select value={m.adapter} onChange={(e) => setM(i, "adapter", e.target.value)}>{adapters.map((a) => <option key={a.key} value={a.key}>{a.name}</option>)}</select></label>
          <label className="field"><span>OLT model (blank = any)</span><input value={m.olt_model ?? ""} onChange={(e) => setM(i, "olt_model", e.target.value)} /></label>
          {[["line_profile_ref", "Line profile"], ["service_profile_ref", "Service profile"], ["dba_profile_ref", "DBA profile"], ["traffic_table_up_ref", "Traffic table up"], ["traffic_table_down_ref", "Traffic table down"]].map(([k, l]) => (
            <label key={k} className="field"><span>{l}</span><input value={m[k] ?? ""} onChange={(e) => setM(i, k, e.target.value)} /></label>
          ))}
          <div className="field" style={{ justifyContent: "flex-end" }}><button onClick={() => setV((s: any) => ({ ...s, mappings: s.mappings.filter((_: any, j: number) => j !== i) }))}>Remove mapping</button></div>
        </div>
      ))}
      <button style={{ marginTop: 8 }} onClick={() => setV((s: any) => ({ ...s, mappings: [...s.mappings, { adapter: adapters[0]?.key || "simulator" }] }))}>Add vendor mapping</button>
      <ErrorBox error={err} />
      <div className="form-actions"><button onClick={onClose}>Cancel</button><button className="primary" onClick={save}>Save template</button></div>
    </Modal>
  );
}

export function Bandwidth() {
  const q = useGet<any[]>("/bandwidth-profiles");
  const [edit, setEdit] = useState<any>(null);
  const { can } = useAuth();
  const inv = useInvalidate();
  return (
    <>
      <PageHeader title="Bandwidth profiles" subtitle="Vendor-neutral speed definitions used by service templates. Sample profiles are provided; configure your own."
        actions={can("provisioning.profiles") && <button className="primary" onClick={() => setEdit({ is_active: true })}>New profile</button>} />
      <Card><Table rows={q.data || []} onRow={can("provisioning.profiles") ? setEdit : undefined} cols={[
        { key: "name", title: "Name" }, { key: "download_kbps", title: "Download", render: (b: any) => mbps(b.download_kbps) }, { key: "upload_kbps", title: "Upload", render: (b: any) => mbps(b.upload_kbps) },
        { key: "description", title: "Description" }, { key: "is_active", title: "Active", render: (b: any) => <Badge value={b.is_active ? "active" : "disabled"} /> },
      ]} /></Card>
      {edit && <Modal title={edit.id ? `Edit ${edit.name}` : "New bandwidth profile"} onClose={() => setEdit(null)}>
        <Form fields={[{ name: "name", label: "Name", required: true }, { name: "download_kbps", label: "Download (kbps)", type: "number", required: true }, { name: "upload_kbps", label: "Upload (kbps)", type: "number", required: true },
          { name: "description", label: "Description" }, { name: "is_active", label: "Active", type: "checkbox" }]} initial={edit} onCancel={() => setEdit(null)}
          onSubmit={async (v) => { edit.id ? await put(`/bandwidth-profiles/${edit.id}`, v) : await post("/bandwidth-profiles", v); setEdit(null); inv(); }} />
      </Modal>}
    </>
  );
}

export function Vlans() {
  const q = useGet<any[]>("/vlans");
  const olts = useGet<any[]>("/olts");
  const [create, setCreate] = useState(false);
  const { can } = useAuth();
  const inv = useInvalidate();
  return (
    <>
      <PageHeader title="VLAN configuration" subtitle="VLAN register used to validate service templates. Global VLANs apply to every OLT."
        actions={can("provisioning.profiles") && <button className="primary" onClick={() => setCreate(true)}>Add VLAN</button>} />
      <Card><Table rows={q.data || []} cols={[
        { key: "vlan_id", title: "VLAN" }, { key: "name", title: "Name" }, { key: "purpose", title: "Purpose" },
        { key: "olt_id", title: "Scope", render: (v: any) => (v.olt_id ? olts.data?.find((o) => o.id === v.olt_id)?.name : "global") }, { key: "description", title: "Description" },
        { key: "x", title: "", render: (v: any) => can("provisioning.profiles") && <button className="small" onClick={async () => { await del(`/vlans/${v.id}`); inv(); }}>Remove</button> },
      ]} /></Card>
      {create && <Modal title="Add VLAN" onClose={() => setCreate(false)}>
        <Form fields={[{ name: "vlan_id", label: "VLAN ID", type: "number", required: true }, { name: "name", label: "Name" },
          { name: "purpose", label: "Purpose", type: "select", required: true, options: [["internet", "Internet"], ["management", "Management"], ["voip", "VoIP"], ["iptv", "IPTV"], ["other", "Other"]] },
          { name: "olt_id", label: "OLT (blank = global)", type: "select", options: (olts.data || []).map((o) => [o.id, o.name]) }, { name: "description", label: "Description" }]}
          initial={{ purpose: "internet" }} onCancel={() => setCreate(false)} onSubmit={async (v) => { await post("/vlans", { ...v, olt_id: v.olt_id ? Number(v.olt_id) : null }); setCreate(false); inv(); }} />
      </Modal>}
    </>
  );
}

export function VendorProfiles() {
  const olts = useGet<any[]>("/olts");
  const [olt, setOlt] = useState<string>("");
  const oltId = olt || (olts.data?.[0]?.id ? String(olts.data[0].id) : "");
  const q = useGet<any[]>(oltId ? `/olts/${oltId}/profiles` : null);
  const [create, setCreate] = useState(false);
  const { can } = useAuth();
  const inv = useInvalidate();
  return (
    <>
      <PageHeader title="Line, service, DBA profiles & traffic tables" subtitle="Objects that exist on each OLT. Sync them from the device where the adapter supports it, or record them manually."
        actions={<>
          <select value={oltId} onChange={(e) => setOlt(e.target.value)}>{olts.data?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select>
          {can("olt.operate") && oltId && <button onClick={async () => { await post(`/olts/${oltId}/actions/sync-profiles`); setTimeout(inv, 2500); }}>Sync from device</button>}
          {can("provisioning.profiles") && oltId && <button className="primary" onClick={() => setCreate(true)}>Record manually</button>}
        </>} />
      <Card><Table searchable rows={q.data || []} cols={[
        { key: "kind", title: "Kind", render: (p: any) => label(p.kind) }, { key: "vendor_ref", title: "ID / index", render: (p: any) => <span className="mono">{p.vendor_ref}</span> }, { key: "name", title: "Name" },
        { key: "parameters", title: "Parameters", render: (p: any) => <span className="mono">{p.parameters ? JSON.stringify(p.parameters) : "—"}</span> },
        { key: "source", title: "Source", render: (p: any) => <Badge value={p.source === "device" ? "device" : "manual"} tone={p.source === "device" ? "ok" : "muted"} /> }, { key: "last_synced_at", title: "Synced", render: (p: any) => fmtTime(p.last_synced_at) },
        { key: "x", title: "", render: (p: any) => can("provisioning.profiles") && p.source === "manual" && <button className="small" onClick={async () => { await del(`/olt-profiles/${p.id}`); inv(); }}>Remove</button> },
      ]} /></Card>
      {create && <Modal title="Record profile" onClose={() => setCreate(false)}>
        <Form fields={[{ name: "kind", label: "Kind", type: "select", required: true, options: [["line", "Line profile"], ["service", "Service profile"], ["dba", "DBA profile"], ["traffic_table", "Traffic table"]] },
          { name: "vendor_ref", label: "ID / index on the OLT", required: true }, { name: "name", label: "Name" }]} initial={{ kind: "line" }} onCancel={() => setCreate(false)}
          onSubmit={async (v) => { await post(`/olts/${oltId}/profiles`, v); setCreate(false); inv(); }} />
      </Modal>}
    </>
  );
}
