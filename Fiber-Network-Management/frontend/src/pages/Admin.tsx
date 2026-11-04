import { useState } from "react";
import { Link } from "react-router-dom";
import { del, patch, post, put } from "../api";
import { useAuth } from "../auth";
import { Badge, Card, ErrorBox, Form, Loading, Modal, PageHeader, Pager, SimTag, Table, useToast } from "../components/ui";
import { ago, fmtTime, label } from "../format";
import { useGet, useInvalidate } from "../hooks";

// ================================================================= integrations
export function Integrations() {
  const q = useGet<any>("/adapters");
  if (!q.data) return <Loading />;
  const { adapters, capabilities } = q.data;
  const cell = (c: any) => !c.supported ? <span className="unsupported">—</span>
    : <Badge value={c.verification === "hardware_verified" ? "HW ✓" : c.verification === "simulated" ? "sim" : c.verification === "fixture_tested" ? "fixtures" : "unverified"}
      tone={c.verification === "hardware_verified" ? "ok" : c.verification === "simulated" ? "info" : "warn"} title={c.notes} />;
  return (
    <>
      <PageHeader title="Vendor integrations" subtitle="Each adapter declares exactly what it implements and how it was verified. Unsupported operations return a structured 'unsupported' result — never a fake success." />
      <div className="note">Legend: <Badge value="HW ✓" tone="ok" /> verified against hardware · <Badge value="sim" tone="info" /> verified only against the simulator · <Badge value="unverified" tone="warn" /> implemented but not verified · — not implemented. No adapter in this build is verified against physical equipment.</div>
      <div className="grid c2">
        {adapters.map((a: any) => (
          <Card key={a.key} title={<>{a.name} {a.planned && <Badge value="planned" tone="muted" />} {a.experimental && <Badge value="experimental" tone="warn" />}</>}>
            <div className="vendor">{a.description}</div>
            <div style={{ marginTop: 6 }}>Key <code>{a.key}</code> · protocols {a.protocols.join(", ")} · models {a.models.join(", ")}</div>
            <div>Selectable: {a.selectable ? "yes" : "no"} · write operations: {a.writes_enabled ? "enabled" : "disabled"}</div>
          </Card>
        ))}
      </div>
      <Card title="Capability matrix">
        <div className="table-wrap">
          <table>
            <thead><tr><th>Capability</th>{adapters.map((a: any) => <th key={a.key}>{a.vendor}<div className="vendor" style={{ textTransform: "none" }}>{a.key}</div></th>)}</tr></thead>
            <tbody>{capabilities.map((c: string) => <tr key={c}><td>{label(c)}</td>{adapters.map((a: any) => <td key={a.key}>{cell(a.capabilities[c])}</td>)}</tr>)}</tbody>
          </table>
        </div>
      </Card>
    </>
  );
}

export function IntegrationHealth() {
  const q = useGet<any[]>("/integrations/health", undefined, 15000);
  return (
    <>
      <PageHeader title="Integration health" subtitle="Connectivity and polling status of each OLT integration." />
      <Card>{!q.data ? <Loading /> : <Table rows={q.data} rowKey="olt_id" cols={[
        { key: "olt", title: "OLT", render: (r: any) => <><Link to={`/olts/${r.olt_id}`}>{r.olt}</Link> <SimTag on={r.simulated} /></> },
        { key: "adapter", title: "Adapter / protocol", render: (r: any) => `${r.adapter} / ${r.protocol}` }, { key: "host", title: "Host", render: (r: any) => <span className="mono">{r.host}</span> },
        { key: "connection_status", title: "Status", render: (r: any) => <Badge value={r.connection_status} /> },
        { key: "last_contact_at", title: "Last contact", render: (r: any) => <span title={fmtTime(r.last_contact_at)}>{ago(r.last_contact_at)}</span> },
        { key: "next_poll_at", title: "Next poll", render: (r: any) => (r.monitoring_enabled ? ago(r.next_poll_at) : "disabled") },
        { key: "poll_interval_seconds", title: "Interval", render: (r: any) => `${r.poll_interval_seconds}s` },
        { key: "creds", title: "Credentials / SNMP", render: (r: any) => <>{r.has_password ? "password ✓" : "no password"} · {r.snmp_version || "no SNMP"} {r.has_snmp_credentials ? "✓" : ""}</> },
        { key: "last_poll_job", title: "Last poll job", render: (r: any) => (r.last_poll_job ? <Badge value={r.last_poll_job.status} /> : "—") },
        { key: "last_poll_error", title: "Last error" },
      ]} />}</Card>
    </>
  );
}

// ================================================================= simulator
const EVENT_FORMS: Record<string, { fields: [string, string, string?][] }> = {
  ont_state: { fields: [["serial", "ONT serial"], ["state", "State (online|offline|los|dying_gasp)"]] },
  signal_degrade: { fields: [["serial", "ONT serial"], ["delta_db", "Delta dB (e.g. -3)", "number"]] },
  set_rx_power: { fields: [["serial", "ONT serial"], ["rx_dbm", "Rx dBm (e.g. -27.5)", "number"]] },
  pon_port_fault: { fields: [["fsp", "PON port F/S/P (e.g. 0/1/0)"], ["fault", "Fault (true/false)", "bool"]] },
  fiber_cut: { fields: [["serials", "ONT serials (comma separated)", "list"], ["restore", "Restore instead (true/false)", "bool"]] },
  olt_unreachable: { fields: [["unreachable", "Unreachable (true/false)", "bool"]] },
  board_fail: { fields: [["slot", "Slot", "number"], ["failed", "Failed (true/false)", "bool"]] },
  new_ont: { fields: [["fsp", "PON port F/S/P"], ["serial", "Serial (optional)"], ["rx_dbm", "Rx dBm", "number"]] },
  temperature: { fields: [["value", "Temperature °C", "number"]] },
  fail_next: { fields: [["operation", "Operation (register_ont, create_service_port, …)"], ["count", "Count", "number"]] },
  restore_all: { fields: [] },
};

export function Simulator() {
  const olts = useGet<any[]>("/olts");
  const sims = (olts.data || []).filter((o) => o.is_simulated);
  const [olt, setOlt] = useState<string>("");
  const oltId = olt || (sims[0] ? String(sims[0].id) : "");
  const events = useGet<any[]>("/simulator/events");
  const state = useGet<any>(oltId ? `/simulator/olts/${oltId}/state` : null, undefined, 10000);
  const [ev, setEv] = useState("ont_state");
  const [vals, setVals] = useState<Record<string, string>>({});
  const [err, setErr] = useState<any>(null);
  const { push } = useToast();
  const inv = useInvalidate();
  const send = async (event: string, params: any) => {
    setErr(null);
    try {
      const r = await post(`/simulator/olts/${oltId}/events`, { event, params, poll_after: true });
      push(`[SIMULATION] ${r.message}. A poll was queued so monitoring picks it up.`, "ok");
      setTimeout(inv, 2500);
    } catch (e) {
      setErr(e);
    }
  };
  const build = () => {
    const p: any = {};
    for (const [k, , t] of EVENT_FORMS[ev].fields) {
      const v = vals[k];
      if (v === undefined || v === "") continue;
      p[k] = t === "number" ? Number(v) : t === "bool" ? v === "true" : t === "list" ? v.split(",").map((s) => s.trim()).filter(Boolean) : v;
    }
    return p;
  };
  return (
    <>
      <PageHeader title="Simulator control" subtitle="Inject network conditions into SIMULATED OLTs. The monitoring engine discovers them through the normal adapter path — exactly as with real equipment." />
      <div className="note sim"><SimTag /> Everything on this page affects simulated equipment only. Real OLTs cannot be targeted.</div>
      {sims.length === 0 ? <Card><div className="empty">No simulated OLTs. Run <code>python -m app.seed --demo</code> or register an OLT with the simulator adapter (a matching simulated device must exist).</div></Card> : (
        <div className="grid c2">
          <Card title="Inject event">
            <div className="form-grid">
              <label className="field"><span>Simulated OLT</span><select value={oltId} onChange={(e) => setOlt(e.target.value)}>{sims.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select></label>
              <label className="field"><span>Event</span><select value={ev} onChange={(e) => { setEv(e.target.value); setVals({}); }}>{events.data?.map((x: any) => <option key={x.event} value={x.event}>{label(x.event)}</option>)}</select></label>
            </div>
            <div className="vendor" style={{ margin: "6px 0" }}>{events.data?.find((x: any) => x.event === ev)?.description}</div>
            <div className="form-grid">
              {EVENT_FORMS[ev]?.fields.map(([k, l]) => <label key={k} className="field"><span>{l}</span><input value={vals[k] || ""} onChange={(e) => setVals({ ...vals, [k]: e.target.value })} /></label>)}
            </div>
            <ErrorBox error={err} />
            <div className="form-actions"><button className="primary" onClick={() => send(ev, build())}>Inject</button></div>
          </Card>
          <Card title="Simulated device state">
            {!state.data ? <Loading /> : <>
              <div>Reachable: <Badge value={state.data.reachable ? "online" : "unreachable"} /> · temperature {state.data.temperature} °C · pending failures {JSON.stringify(state.data.fail_next)}</div>
              <div style={{ margin: "6px 0" }}>Boards: {state.data.boards.map((b: any) => <span key={b.slot} style={{ marginRight: 8 }}>slot {b.slot} <Badge value={b.installed ? b.status : "empty"} /></span>)}</div>
              <div>Unconfigured ONTs (autofind): {state.data.autofind.map((a: any) => <code key={a.serial} style={{ marginRight: 4 }}>{a.serial}@{a.fsp}</code>)}</div>
              <Table searchable rows={state.data.onts} rowKey="serial" cols={[
                { key: "serial", title: "ONT", render: (o: any) => <span className="mono">{o.serial}</span> }, { key: "fsp", title: "Port", render: (o: any) => `${o.fsp} #${o.ont_index}` },
                { key: "state", title: "Physical state", render: (o: any) => <Badge value={o.state} /> }, { key: "rx_base", title: "Rx base" },
                { key: "x", title: "", render: (o: any) => <span style={{ display: "flex", gap: 4 }}>
                  {o.state === "online" ? <><button className="small" onClick={() => send("ont_state", { serial: o.serial, state: "los" })}>LOS</button><button className="small" onClick={() => send("ont_state", { serial: o.serial, state: "dying_gasp" })}>Dying gasp</button><button className="small" onClick={() => send("signal_degrade", { serial: o.serial, delta_db: -3 })}>−3 dB</button></>
                    : <button className="small" onClick={() => send("ont_state", { serial: o.serial, state: "online" })}>Bring online</button>}
                </span> },
              ]} />
              <div className="form-actions"><button onClick={() => send("restore_all", {})}>Restore all simulated faults</button></div>
            </>}
          </Card>
        </div>
      )}
    </>
  );
}

// ================================================================= thresholds & jobs
export function Thresholds() {
  const q = useGet<any[]>("/optical-thresholds");
  const [edit, setEdit] = useState<any>(null);
  const { can } = useAuth();
  const inv = useInvalidate();
  return (
    <>
      <PageHeader title="Optical thresholds" subtitle="Configurable per vendor / ONT model. The most specific match is used: vendor+model > model > vendor > default."
        actions={can("monitoring.configure") && <button className="primary" onClick={() => setEdit({})}>Add threshold</button>} />
      <div className="note warn">Do not rely on the default values blindly: set thresholds from the receiver sensitivity and overload figures in the datasheets of the optics you deploy.</div>
      <Card><Table rows={q.data || []} onRow={can("monitoring.configure") ? setEdit : undefined} cols={[
        { key: "name", title: "Name" }, { key: "scope", title: "Scope", render: (t: any) => (t.model ? `model ${t.model}` : t.vendor ? `vendor ${t.vendor}` : "default") },
        { key: "rx", title: "ONT Rx warn / crit low", render: (t: any) => `${t.rx_warning_low} / ${t.rx_critical_low} dBm` },
        { key: "rxh", title: "ONT Rx warn / crit high", render: (t: any) => `${t.rx_warning_high ?? "—"} / ${t.rx_critical_high ?? "—"}` },
        { key: "olt", title: "OLT Rx warn / crit low", render: (t: any) => `${t.olt_rx_warning_low ?? "—"} / ${t.olt_rx_critical_low ?? "—"}` }, { key: "notes", title: "Notes" },
      ]} /></Card>
      {edit && <Modal title={edit.id ? "Edit threshold" : "Add threshold"} onClose={() => setEdit(null)} wide>
        <Form fields={[{ name: "name", label: "Name", required: true }, { name: "vendor", label: "Vendor (blank = any)" }, { name: "model", label: "ONT model (blank = any)" },
          { name: "rx_warning_low", label: "Rx warning low (dBm)", type: "number", step: "any", required: true }, { name: "rx_critical_low", label: "Rx critical low (dBm)", type: "number", step: "any", required: true },
          { name: "rx_warning_high", label: "Rx warning high (overload)", type: "number", step: "any" }, { name: "rx_critical_high", label: "Rx critical high", type: "number", step: "any" },
          { name: "olt_rx_warning_low", label: "OLT Rx warning low", type: "number", step: "any" }, { name: "olt_rx_critical_low", label: "OLT Rx critical low", type: "number", step: "any" },
          { name: "notes", label: "Notes / datasheet reference", type: "textarea" }]} initial={edit} onCancel={() => setEdit(null)}
          onSubmit={async (v) => { const b = { ...v, vendor: v.vendor || null, model: v.model || null }; edit.id ? await put(`/optical-thresholds/${edit.id}`, b) : await post("/optical-thresholds", b); setEdit(null); inv(); }} />
        {edit.id && can("monitoring.configure") && <button className="small" onClick={async () => { await del(`/optical-thresholds/${edit.id}`); setEdit(null); inv(); }}>Delete threshold</button>}
      </Modal>}
    </>
  );
}

export function BackgroundJobs() {
  const [status, setStatus] = useState("");
  const [offset, setOffset] = useState(0);
  const q = useGet<any>("/jobs", { status, limit: 50, offset }, 5000);
  const [show, setShow] = useState<any>(null);
  return (
    <>
      <PageHeader title="Background jobs" subtitle="Monitoring polls, discovery, provisioning runs and device operations executed by the worker." actions={
        <select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}><option value="">All</option>{["queued", "running", "succeeded", "failed"].map((s) => <option key={s}>{s}</option>)}</select>} />
      {q.data && q.data.items.some((j: any) => j.status === "queued" && Date.now() - new Date(j.created_at).getTime() > 60000) && <div className="note warn">Some jobs have been queued for over a minute — check that the worker process is running.</div>}
      <Card>{!q.data ? <Loading /> : <>
        <Table rows={q.data.items} onRow={setShow} cols={[
          { key: "created_at", title: "Queued", render: (j: any) => fmtTime(j.created_at) }, { key: "job_type", title: "Type" }, { key: "status", title: "Status", render: (j: any) => <Badge value={j.status} /> },
          { key: "attempts", title: "Attempts" }, { key: "created_by", title: "By" }, { key: "finished_at", title: "Finished", render: (j: any) => fmtTime(j.finished_at) }, { key: "error", title: "Error" },
        ]} />
        <Pager {...q.data} onChange={setOffset} />
      </>}</Card>
      {show && <Modal title={`${show.job_type} — ${show.id}`} onClose={() => setShow(null)} wide><pre className="code">{JSON.stringify({ payload: show.payload, result: show.result, error: show.error }, null, 2)}</pre></Modal>}
    </>
  );
}

// ================================================================= users, roles, audit, settings
export function Users() {
  const q = useGet<any[]>("/users");
  const roles = useGet<any[]>("/roles");
  const [edit, setEdit] = useState<any>(null);
  const inv = useInvalidate();
  const roleOpts: [any, string][] = (roles.data || []).map((r) => [r.id, r.name]);
  return (
    <>
      <PageHeader title="Users" actions={<button className="primary" onClick={() => setEdit({ is_active: true })}>Add user</button>} />
      <Card><Table rows={q.data || []} onRow={setEdit} cols={[
        { key: "username", title: "Username" }, { key: "full_name", title: "Name" }, { key: "email", title: "Email" }, { key: "role", title: "Role" },
        { key: "is_active", title: "Status", render: (u: any) => <Badge value={u.is_active ? "active" : "disabled"} /> },
        { key: "last_login_at", title: "Last login", render: (u: any) => fmtTime(u.last_login_at) }, { key: "locked_until", title: "Locked until", render: (u: any) => fmtTime(u.locked_until) },
      ]} /></Card>
      {edit && <Modal title={edit.id ? `Edit ${edit.username}` : "Add user"} onClose={() => setEdit(null)}>
        <Form fields={[...(edit.id ? [] : [{ name: "username", label: "Username", required: true }]), { name: "full_name", label: "Full name" }, { name: "email", label: "Email" },
          { name: "role_id", label: "Role", type: "select" as const, required: true, options: roleOpts },
          { name: "password", label: edit.id ? "New password (blank = unchanged)" : "Password", type: "password" as const, required: !edit.id, help: "At least 10 characters with letters and digits" },
          { name: "is_active", label: "Active", type: "checkbox" as const }]}
          initial={{ ...edit, role_id: edit.role_id }} onCancel={() => setEdit(null)}
          onSubmit={async (v) => {
            const body: any = { ...v, role_id: Number(v.role_id) };
            if (!body.password) delete body.password;
            edit.id ? await patch(`/users/${edit.id}`, body) : await post("/users", body);
            setEdit(null); inv();
          }} />
      </Modal>}
    </>
  );
}

export function Roles() {
  const q = useGet<any[]>("/roles");
  const perms = useGet<any[]>("/permissions");
  const { can } = useAuth();
  const [edit, setEdit] = useState<any>(null);
  const inv = useInvalidate();
  const [err, setErr] = useState<any>(null);
  return (
    <>
      <PageHeader title="Roles & permissions" actions={can("roles.manage") && <button className="primary" onClick={() => setEdit({ name: "", description: "", permissions: [] })}>New role</button>} />
      <Card><Table rows={q.data || []} onRow={can("roles.manage") ? (r: any) => setEdit({ ...r, permissions: r.permissions }) : undefined} cols={[
        { key: "name", title: "Role", render: (r: any) => <><b>{r.name}</b> {r.builtin && <Badge value="built-in" tone="muted" />}</> }, { key: "description", title: "Description" },
        { key: "permissions", title: "Permissions", render: (r: any) => <span className="vendor">{r.permissions.join(", ")}</span> },
      ]} /></Card>
      {edit && perms.data && <Modal title={edit.id ? `Edit ${edit.name}` : "New role"} onClose={() => setEdit(null)} wide>
        <div className="form-grid">
          <label className="field"><span>Name</span><input value={edit.name} disabled={edit.builtin} onChange={(e) => setEdit({ ...edit, name: e.target.value })} /></label>
          <label className="field"><span>Description</span><input value={edit.description} onChange={(e) => setEdit({ ...edit, description: e.target.value })} /></label>
        </div>
        <div className="form-grid" style={{ marginTop: 10 }}>
          {perms.data.map((p: any) => (
            <label key={p.name} className="field check"><span><code>{p.name}</code> — {p.description}</span>
              <input type="checkbox" checked={edit.permissions.includes(p.name)} onChange={(e) => setEdit({ ...edit, permissions: e.target.checked ? [...edit.permissions, p.name] : edit.permissions.filter((x: string) => x !== p.name) })} /></label>
          ))}
        </div>
        <ErrorBox error={err} />
        <div className="form-actions"><button onClick={() => setEdit(null)}>Cancel</button><button className="primary" onClick={async () => {
          setErr(null);
          try { const b = { name: edit.name, description: edit.description, permissions: edit.permissions }; edit.id ? await put(`/roles/${edit.id}`, b) : await post("/roles", b); setEdit(null); inv(); } catch (e) { setErr(e); }
        }}>Save role</button></div>
      </Modal>}
    </>
  );
}

export function AuditLogs() {
  const [f, setF] = useState({ action: "", username: "", outcome: "", q: "" });
  const [offset, setOffset] = useState(0);
  const q = useGet<any>("/audit-logs", { ...f, limit: 50, offset });
  const [show, setShow] = useState<any>(null);
  const set = (k: string, v: string) => { setF({ ...f, [k]: v }); setOffset(0); };
  return (
    <>
      <PageHeader title="Audit logs" subtitle="Every configuration change, device operation, provisioning action and authentication event. Secrets are redacted before storage." />
      <div className="filters">
        <input placeholder="Action prefix (e.g. ont., provisioning.)" value={f.action} onChange={(e) => set("action", e.target.value)} />
        <input placeholder="Username" value={f.username} onChange={(e) => set("username", e.target.value)} />
        <select value={f.outcome} onChange={(e) => set("outcome", e.target.value)}><option value="">Any outcome</option><option>success</option><option>failure</option><option>denied</option></select>
        <input placeholder="Search summary" value={f.q} onChange={(e) => set("q", e.target.value)} />
      </div>
      <Card>{!q.data ? <Loading /> : <>
        <Table rows={q.data.items} onRow={setShow} cols={[
          { key: "ts", title: "Time", render: (a: any) => fmtTime(a.ts) }, { key: "username", title: "User" }, { key: "action", title: "Action", render: (a: any) => <code>{a.action}</code> },
          { key: "entity", title: "Entity", render: (a: any) => (a.entity_type ? `${a.entity_type} ${a.entity_id ?? ""}` : "—") }, { key: "summary", title: "Summary" },
          { key: "outcome", title: "Outcome", render: (a: any) => <Badge value={a.outcome} tone={a.outcome === "success" ? "ok" : "bad"} /> }, { key: "ip_address", title: "IP" },
        ]} />
        <Pager {...q.data} onChange={setOffset} />
      </>}</Card>
      {show && <Modal title={`Audit #${show.id}`} onClose={() => setShow(null)} wide><pre className="code">{JSON.stringify(show, null, 2)}</pre></Modal>}
    </>
  );
}

export function Settings() {
  const q = useGet<any>("/settings");
  const { can } = useAuth();
  const inv = useInvalidate();
  const { push } = useToast();
  const [vals, setVals] = useState<Record<string, any>>({});
  if (!q.data) return <Loading />;
  return (
    <>
      <PageHeader title="Application settings" subtitle="Values marked read-only come from the deployment environment." />
      <Card>
        <Table rows={Object.entries(q.data).map(([k, v]: any) => ({ id: k, ...v }))} cols={[
          { key: "id", title: "Setting", render: (s: any) => <code>{s.id}</code> }, { key: "description", title: "Description" },
          { key: "value", title: "Value", render: (s: any) => s.readonly || !can("settings.manage") ? <span className="mono">{String(s.value)}</span> : (
            <span style={{ display: "flex", gap: 6 }}>
              {typeof s.value === "boolean" ? <select value={String(vals[s.id] ?? s.value)} onChange={(e) => setVals({ ...vals, [s.id]: e.target.value === "true" })}><option>true</option><option>false</option></select>
                : s.id === "terminology.fat" ? <select value={vals[s.id] ?? s.value} onChange={(e) => setVals({ ...vals, [s.id]: e.target.value })}>{["FAT", "FDB", "FDT", "Fiber Distribution Terminal", "NAP", "Distribution Box"].map((x) => <option key={x}>{x}</option>)}</select>
                : <input value={vals[s.id] ?? s.value} onChange={(e) => setVals({ ...vals, [s.id]: typeof s.value === "number" ? Number(e.target.value) : e.target.value })} />}
              <button className="small" disabled={vals[s.id] === undefined} onClick={async () => { await put(`/settings/${s.id}`, { value: vals[s.id] }); push("Setting saved", "ok"); setVals({ ...vals, [s.id]: undefined }); inv(); }}>Save</button>
            </span>) },
        ]} />
      </Card>
    </>
  );
}
