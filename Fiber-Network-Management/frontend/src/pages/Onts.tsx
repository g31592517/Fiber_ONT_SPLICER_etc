import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { get, patch, post } from "../api";
import { useAuth } from "../auth";
import { OpticalChart, OpticalValue, TracePath } from "../components/domain";
import { useTerms } from "../components/Layout";
import { Badge, Card, ConfirmDialog, ErrorBox, Form, KV, Loading, Modal, PageHeader, Pager, SimTag, Table, Tabs, Unsupported, useJobRunner, useToast } from "../components/ui";
import { ago, dbm, fmtTime, label, num } from "../format";
import { useGet, useInvalidate } from "../hooks";
import { OntTable } from "./Olts";

// ================================================================= inventory
export function OntList() {
  const [sp, setSp] = useSearchParams();
  const olts = useGet<any[]>("/olts");
  const [offset, setOffset] = useState(0);
  const f = {
    q: sp.get("q") || "", status: sp.get("status") || "", olt_id: sp.get("olt_id") || "", optical_status: sp.get("optical_status") || "",
    registration: sp.get("registration") || "registered", sort: sp.get("sort") || "serial",
  };
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n); setOffset(0); };
  const q = useGet<any>("/onts", { ...f, limit: 100, offset }, 30000);
  const nav = useNavigate();
  return (
    <>
      <PageHeader title="ONT inventory" subtitle="Every ONT/ONU record with its OLT, PON port, FAT port, subscriber and latest optical reading." />
      <div className="filters">
        <input placeholder="Serial, MAC, description, subscriber, service ref…" value={f.q} onChange={(e) => set("q", e.target.value)} style={{ minWidth: 300 }} />
        <select value={f.status} onChange={(e) => set("status", e.target.value)}>
          <option value="">Any status</option>
          {["online", "offline", "los", "dying_gasp", "registered_inactive", "disabled", "faulty", "unreachable", "unknown"].map((s) => <option key={s} value={s}>{label(s)}</option>)}
          <option value="offline,los,dying_gasp,unreachable">All not online</option>
        </select>
        <select value={f.olt_id} onChange={(e) => set("olt_id", e.target.value)}><option value="">All OLTs</option>{olts.data?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select>
        <select value={f.optical_status} onChange={(e) => set("optical_status", e.target.value)}><option value="">Any optical</option><option value="warning,critical">Warning + critical</option><option value="critical">Critical</option><option value="ok">OK</option></select>
        <select value={f.registration} onChange={(e) => set("registration", e.target.value)}><option value="registered">Registered</option><option value="deregistered">Deregistered</option><option value="all">All records</option></select>
        <select value={f.sort} onChange={(e) => set("sort", e.target.value)}><option value="serial">Sort: serial</option><option value="rx_asc">Sort: weakest Rx first</option><option value="status_changed">Sort: recent status change</option></select>
      </div>
      <Card>{q.data ? <><OntTable rows={q.data.items} onOpen={(o) => nav(`/onts/${o.id}`)} /><Pager {...q.data} onChange={setOffset} /></> : <Loading />}</Card>
    </>
  );
}

// ================================================================= detail
export function OntDetail() {
  const { id } = useParams();
  const q = useGet<any>(`/onts/${id}`, undefined, 30000);
  const [tab, setTab] = useState("overview");
  const { can } = useAuth();
  const { run, running } = useJobRunner();
  const inv = useInvalidate();
  const [op, setOp] = useState<string | null>(null);
  const [edit, setEdit] = useState(false);
  const [err, setErr] = useState<any>(null);
  const terms = useTerms();
  const nav = useNavigate();
  if (!q.data) return q.error ? <ErrorBox error={q.error} /> : <Loading />;
  const o = q.data;
  const caps = o.capabilities || {};
  const capOk = (k: string) => caps[k]?.supported;
  return (
    <>
      <PageHeader crumbs={[["ONTs", "/onts"], [o.serial_number]]}
        title={<><span className="mono">{o.serial_number}</span> <SimTag on={o.is_simulated} /></>}
        subtitle={<><Badge value={o.status} /> <span className="vendor">vendor-reported: {o.vendor_status || "—"}</span> · {o.registration_status} · status since {fmtTime(o.status_changed_at)}</>}
        actions={
          <>
            {can("ont.operate") && <button disabled={!!running} onClick={() => run("Refresh ONT", () => post(`/onts/${o.id}/refresh`), () => inv())}>Refresh status & optics</button>}
            {can("ont.reboot") && <button disabled={!capOk("ont_reboot") || o.status !== "online"} title={!capOk("ont_reboot") ? "Not supported by adapter" : ""} onClick={() => setOp("reboot")}>Reboot</button>}
            {can("ont.disable") && (o.admin_status === "disabled"
              ? <button disabled={!capOk("ont_enable_disable")} onClick={() => setOp("enable")}>Enable</button>
              : <button disabled={!capOk("ont_enable_disable")} onClick={() => setOp("disable")}>Disable</button>)}
            {can("ont.deregister") && o.registration_status === "registered" && <button className="danger" onClick={async () => {
              try { const j = await post("/provisioning/jobs", { operation: "deregister_ont", ont_id: o.id }); nav(`/provisioning/jobs/${j.id}`); } catch (e) { setErr(e); }
            }}>Deregister…</button>}
            {can("ont.write") && <button onClick={() => setEdit(true)}>Edit</button>}
          </>
        } />
      <ErrorBox error={err} />
      {running && <div className="note">{running}…</div>}
      <div className="grid c3">
        <Card title="Where it is connected">
          <KV rows={[
            ["OLT", o.olt_id ? <Link to={`/olts/${o.olt_id}`}>{o.olt_name}</Link> : "—"],
            ["PON port", o.pon_port_id ? <Link to={`/pon-ports/${o.pon_port_id}`}>{o.pon_port}</Link> : "—"],
            ["ONT ID", o.ont_index], [`${terms.fat}`, o.fat ? <Link to={`/fats/${o.fat.fat_id}`}>{o.fat.fat_name}</Link> : <span className="unsupported">not recorded</span>],
            [`${terms.fat} port`, o.fat?.fat_port_number], ["Distance", o.distance_m ? `${o.distance_m} m` : "—"],
          ]} />
        </Card>
        <Card title="Subscriber & service">
          {o.services.length === 0 ? <div className="vendor">No subscriber/service reference is associated with this ONT.</div> : o.services.map((s: any) => (
            <KV key={s.id} rows={[["Subscriber", <Link to={`/subscribers/${s.subscriber.id}`}>{s.subscriber.display_name}</Link>], ["Customer ref", s.subscriber.customer_ref],
              ["Service", s.service_ref], ["Service status", <Badge value={s.status} />], ["Template / profile", s.template], ["External service id", s.external_service_id]]} />
          ))}
        </Card>
        <Card title="Latest optical reading">
          <KV rows={[
            ["ONT Rx", <OpticalValue v={o.rx_power_dbm} status={o.optical_status} />], ["ONT Tx", o.tx_power_dbm != null ? dbm(o.tx_power_dbm) : <Unsupported text="not reported" />],
            ["OLT Rx (this ONT)", o.olt_rx_power_dbm != null ? dbm(o.olt_rx_power_dbm) : <Unsupported text="not reported" />],
            ["Temperature", o.temperature_c != null ? num(o.temperature_c, " °C") : <Unsupported text="not reported" />],
            ["Voltage", o.voltage_v != null ? num(o.voltage_v, " V", 3) : <Unsupported text="not reported" />],
            ["Bias current", o.bias_current_ma != null ? num(o.bias_current_ma, " mA", 2) : <Unsupported text="not reported" />],
            ["Optical status", o.optical_status ? <Badge value={o.optical_status} /> : "no reading"],
            ["Threshold", o.threshold?.name],
          ]} />
          {o.status !== "online" && <div className="vendor" style={{ marginTop: 6 }}>The ONT is not online, so no current optical values are available. Previous readings are in the history.</div>}
        </Card>
      </div>
      <Tabs active={tab} onChange={setTab} tabs={[["overview", "Device"], ["path", "Fiber path"], ["optical", "Optical history"], ["status", "Status history"], ["alarms", "Alarms"], ["jobs", "Provisioning"]]} />
      {tab === "overview" && (
        <Card title="Device record">
          <KV rows={[
            ["Vendor", o.vendor], ["Model", o.model], ["Equipment ID", o.equipment_id], ["Hardware version", o.hardware_version], ["Firmware", o.firmware_version],
            ["MAC", o.mac_address], ["Auth method", o.auth_method], ["LOID", o.loid], ["Auth password stored", o.has_auth_password ? "yes (encrypted)" : "no"],
            ["Line profile", o.line_profile], ["Service profile", o.service_profile], ["Admin state", <Badge value={o.admin_status} />],
            ["Last online", fmtTime(o.last_online_at)], ["Last offline", fmtTime(o.last_offline_at)], ["Last down cause", o.last_down_cause],
            ["Last polled", fmtTime(o.last_polled_at)], ["Installed", o.installation_date], ["Description", o.description], ["Notes", o.notes],
            ["GEM / T-CONT / VLAN config", o.vendor_config ? <span className="mono">{JSON.stringify(o.vendor_config)}</span> : "as defined by line/service profile"],
            ["Data source", o.data_source === "simulated" ? <SimTag /> : "device"],
          ]} />
          {o.optical_violations?.length > 0 && <div className="error-box">Threshold violations: {o.optical_violations.map((v: any) => `${label(v.metric)} ${v.value} (${v.level} ${v.direction}, limit ${v.limit})`).join("; ")}</div>}
        </Card>
      )}
      {tab === "path" && (
        <Card title="Physical path: ONT → OLT">
          {o.trace.warnings.map((w: string) => <div key={w} className="note warn">{w}</div>)}
          <TracePath trace={o.trace.physical} />
          {o.trace.logical && <div className="vendor" style={{ marginTop: 10 }}>Logical registration: {o.trace.logical.olt} PON {o.trace.logical.pon_port} ONT ID {o.trace.logical.ont_index}</div>}
        </Card>
      )}
      {tab === "optical" && <OntOptical id={o.id} threshold={o.threshold} />}
      {tab === "status" && <OntStatusHistory id={o.id} />}
      {tab === "alarms" && <OntAlarms id={o.id} />}
      {tab === "jobs" && (
        <Card title="Provisioning history">
          <Table rows={o.recent_jobs} onRow={(j: any) => nav(`/provisioning/jobs/${j.id}`)} cols={[
            { key: "created_at", title: "Created", render: (j: any) => fmtTime(j.created_at) }, { key: "operation", title: "Operation", render: (j: any) => label(j.operation) },
            { key: "state", title: "State", render: (j: any) => <Badge value={j.state} /> },
          ]} />
        </Card>
      )}
      {op && (
        <ConfirmDialog title={`${label(op)} ONT`} destructive={op !== "enable"} token={o.serial_number}
          message={op === "reboot" ? "The ONT will restart and the subscriber's service will be interrupted for a few minutes."
            : op === "disable" ? "Disabling the ONT stops all service on it until it is enabled again." : "Enable the ONT on the OLT."}
          onClose={() => setOp(null)}
          onConfirm={(typed) => { const which = op; setOp(null); run(`ONT ${which}`, () => post(`/onts/${o.id}/operations`, { operation: which, confirm: typed }), () => inv()); }} />
      )}
      {edit && (
        <Modal title="Edit ONT record" onClose={() => setEdit(false)}>
          <Form fields={[{ name: "description", label: "Description" }, { name: "installation_date", label: "Installation date", type: "date" },
            { name: "vendor", label: "Vendor" }, { name: "model", label: "Model" }, { name: "notes", label: "Notes", type: "textarea" }]}
            initial={o} onCancel={() => setEdit(false)} onSubmit={async (v) => { await patch(`/onts/${o.id}`, v); setEdit(false); inv(); }} />
        </Modal>
      )}
    </>
  );
}

function OntOptical({ id, threshold }: { id: number; threshold: any }) {
  const [hours, setHours] = useState(168);
  const q = useGet<any>(`/onts/${id}/history`, { hours });
  return (
    <Card title="Optical history" actions={
      <select value={hours} onChange={(e) => setHours(Number(e.target.value))}><option value={24}>24 h</option><option value={168}>7 days</option><option value={720}>30 days</option></select>
    }>
      {!q.data ? <Loading /> : <>
        <OpticalChart readings={q.data.optical} threshold={threshold} />
        <Table rows={[...q.data.optical].reverse().slice(0, 50)} cols={[
          { key: "ts", title: "Time", render: (r: any) => fmtTime(r.ts) }, { key: "rx_power_dbm", title: "ONT Rx", render: (r: any) => dbm(r.rx_power_dbm) },
          { key: "tx_power_dbm", title: "ONT Tx", render: (r: any) => dbm(r.tx_power_dbm) }, { key: "olt_rx_power_dbm", title: "OLT Rx", render: (r: any) => dbm(r.olt_rx_power_dbm) },
          { key: "temperature_c", title: "Temp" }, { key: "voltage_v", title: "Voltage" }, { key: "bias_current_ma", title: "Bias mA" },
          { key: "source", title: "Source", render: (r: any) => (r.source.startsWith("simulator") ? <SimTag /> : r.source) },
        ]} />
      </>}
    </Card>
  );
}

function OntStatusHistory({ id }: { id: number }) {
  const q = useGet<any>(`/onts/${id}/history`, { hours: 24 * 90 });
  if (!q.data) return <Loading />;
  return (
    <Card title="Status changes">
      <Table rows={q.data.status_events} cols={[
        { key: "ts", title: "Time", render: (e: any) => fmtTime(e.ts) }, { key: "from_status", title: "From", render: (e: any) => <Badge value={e.from_status} /> },
        { key: "to_status", title: "To", render: (e: any) => <Badge value={e.to_status} /> }, { key: "vendor_status", title: "Vendor-reported" }, { key: "reason", title: "Reason" },
      ]} />
    </Card>
  );
}

function OntAlarms({ id }: { id: number }) {
  const q = useGet<any>(`/onts/${id}/history`, { hours: 24 });
  if (!q.data) return <Loading />;
  return (
    <Card title="Alarms for this ONT">
      <Table rows={q.data.alarms} cols={[
        { key: "severity", title: "Severity", render: (a: any) => <Badge value={a.severity} /> }, { key: "category", title: "Category", render: (a: any) => label(a.category) },
        { key: "description", title: "Description" }, { key: "state", title: "State", render: (a: any) => <Badge value={a.state} /> },
        { key: "first_occurred_at", title: "First", render: (a: any) => fmtTime(a.first_occurred_at) }, { key: "resolved_at", title: "Resolved", render: (a: any) => fmtTime(a.resolved_at) },
      ]} />
    </Card>
  );
}

// ================================================================= discovery
export function Discovered() {
  const [state, setState] = useState("unregistered");
  const olts = useGet<any[]>("/olts");
  const [olt, setOlt] = useState("");
  const q = useGet<any[]>("/discovered-onts", { state, olt_id: olt }, 30000);
  const [inspect, setInspect] = useState<any>(null);
  const { can } = useAuth();
  const { run, running } = useJobRunner();
  const inv = useInvalidate();
  return (
    <>
      <PageHeader title="Discovered ONTs" subtitle="ONTs physically connected to a PON port but not configured on the OLT. Discovery is not registration — select a device to inspect and register it."
        actions={
          <>
            <select value={state} onChange={(e) => setState(e.target.value)}><option value="unregistered">Unregistered</option><option value="registered">Registered</option><option value="ignored">Ignored</option><option value="gone">No longer seen</option><option value="all">All</option></select>
            <select value={olt} onChange={(e) => setOlt(e.target.value)}><option value="">All OLTs</option>{olts.data?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select>
            {can("ont.discover") && olt && <button disabled={!!running} onClick={() => run("ONT discovery", () => post(`/olts/${olt}/actions/discover`), () => inv())}>Run discovery</button>}
          </>
        } />
      <Card>
        {!q.data ? <Loading /> : (
          <Table searchable rows={q.data} onRow={setInspect} empty="No discovered ONTs. Run discovery on an OLT (the poll also refreshes this list)." cols={[
            { key: "serial_number", title: "Serial", render: (d: any) => <><b className="mono">{d.serial_number}</b> <SimTag on={d.is_simulated} /></> },
            { key: "model", title: "Vendor / model", render: (d: any) => `${d.vendor_id || "—"} ${d.model || d.equipment_id || ""}` },
            { key: "olt_name", title: "OLT" }, { key: "pon_port", title: "PON port", render: (d: any) => <span className="mono">{d.pon_port}</span> },
            { key: "rx_power_dbm", title: "Signal", render: (d: any) => (d.rx_power_dbm != null ? `${d.rx_power_dbm} dBm` : <Unsupported text="not reported" />) },
            { key: "loid", title: "LOID" },
            { key: "first_seen_at", title: "Discovered", render: (d: any) => <span title={fmtTime(d.first_seen_at)}>{ago(d.first_seen_at)}<div className="vendor">last seen {ago(d.last_seen_at)}</div></span> },
            { key: "state", title: "State", render: (d: any) => <><Badge value={d.state} />{d.inventory_match && <div className="vendor">inventory: {d.inventory_match.registration_status}</div>}</> },
          ]} />
        )}
      </Card>
      {inspect && <RegisterWizard discoveryId={inspect.id} onClose={() => { setInspect(null); inv(); }} />}
    </>
  );
}

function RegisterWizard({ discoveryId, onClose }: { discoveryId: number; onClose: () => void }) {
  const d = useGet<any>(`/discovered-onts/${discoveryId}`);
  const templates = useGet<any[]>("/service-templates");
  const services = useGet<any>("/services", { status: "pending", limit: 200 });
  const fats = useGet<any[]>("/fats");
  const terms = useTerms();
  const { can } = useAuth();
  const { push } = useToast();
  const nav = useNavigate();
  const [v, setV] = useState<any>({ mode: "register", auth_method: "sn" });
  const [fatPorts, setFatPorts] = useState<any[]>([]);
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (v.fat_id) get(`/fats/${v.fat_id}`).then((f) => setFatPorts(f.ports.filter((p: any) => p.effective_status === "available" || p.effective_status === "reserved")));
    else setFatPorts([]);
  }, [v.fat_id]);
  if (!d.data) return <Modal title="Discovered ONT" onClose={onClose}><Loading /></Modal>;
  const x = d.data;
  const prof = (k: string) => x.profiles.filter((p: any) => p.kind === k);
  const set = (k: string, val: any) => setV((s: any) => ({ ...s, [k]: val }));
  const tpl = templates.data?.find((t: any) => String(t.id) === String(v.template_id));
  const mapping = tpl?.mappings.find((m: any) => m.adapter === x.olt_adapter);
  const fatPort = fatPorts.find((p) => String(p.id) === String(v.fat_port_id));
  const submit = async () => {
    setBusy(true);
    setErr(null);
    try {
      const body: any = { discovery_id: x.id, description: v.description, auth_method: v.auth_method, loid: v.loid || undefined, auth_password: v.auth_password || undefined,
        fat_port_id: v.fat_port_id ? Number(v.fat_port_id) : undefined, use_reserved_port: fatPort?.effective_status === "reserved",
        ont_index: v.ont_index !== undefined && v.ont_index !== "" ? Number(v.ont_index) : undefined };
      if (v.mode === "register") Object.assign(body, { operation: "register_ont", line_profile_ref: v.line_profile_ref, service_profile_ref: v.service_profile_ref });
      else Object.assign(body, { operation: "activate_service", service_id: Number(v.service_id), template_id: Number(v.template_id) });
      const job = await post("/provisioning/jobs", body);
      push("Request created — validation (including a device dry run) runs in the background", "info");
      nav(`/provisioning/jobs/${job.id}`);
    } catch (e) {
      setErr(e);
    } finally {
      setBusy(false);
    }
  };
  return (
    <Modal title={<>Discovered ONT <span className="mono">{x.serial_number}</span> <SimTag on={x.is_simulated} /></>} onClose={onClose} wide>
      <KV rows={[["OLT", x.olt_name], ["PON port", x.pon_port], ["Port status", <Badge value={x.pon_port_status} />], ["Vendor ID", x.vendor_id], ["Model / equipment", x.model || x.equipment_id],
        ["Hardware", x.hardware_version], ["Software", x.software_version], ["LOID", x.loid], ["Signal", x.rx_power_dbm != null ? `${x.rx_power_dbm} dBm` : "not reported"],
        ["First seen", fmtTime(x.first_seen_at)], ["Last seen", fmtTime(x.last_seen_at)], ["State", <Badge value={x.state} />]]} />
      {x.vendor_reported && <details style={{ margin: "10px 0" }}><summary>Vendor-reported fields</summary><pre className="code">{JSON.stringify(x.vendor_reported, null, 2)}</pre></details>}
      {x.state !== "unregistered" ? <div className="note">This device is {x.state}.</div> : !x.registration_supported ? <div className="note warn">The OLT's adapter does not support ONT registration.</div>
        : !can("ont.register") ? <div className="note">You do not have permission to register ONTs.</div> : (
        <>
          <h3 style={{ margin: "14px 0 8px" }}>Registration</h3>
          <div className="tabs">
            <button className={v.mode === "register" ? "active" : ""} onClick={() => set("mode", "register")}>Register only (profiles)</button>
            <button className={v.mode === "activate" ? "active" : ""} onClick={() => set("mode", "activate")}>Register + activate subscriber service</button>
          </div>
          <div className="form-grid">
            {v.mode === "register" ? (
              <>
                <label className="field"><span>Line profile *</span><select value={v.line_profile_ref || ""} onChange={(e) => set("line_profile_ref", e.target.value)}><option value="">—</option>{prof("line").map((p: any) => <option key={p.id} value={p.vendor_ref}>{p.vendor_ref} — {p.name}</option>)}</select></label>
                <label className="field"><span>Service profile *</span><select value={v.service_profile_ref || ""} onChange={(e) => set("service_profile_ref", e.target.value)}><option value="">—</option>{prof("service").map((p: any) => <option key={p.id} value={p.vendor_ref}>{p.vendor_ref} — {p.name}</option>)}</select></label>
              </>
            ) : (
              <>
                <label className="field"><span>Pending service (subscriber) *</span><select value={v.service_id || ""} onChange={(e) => set("service_id", e.target.value)}><option value="">—</option>{services.data?.items.map((s: any) => <option key={s.id} value={s.id}>{s.service_ref} — {s.subscriber.display_name}</option>)}</select><small>Create the subscriber/service reference first if missing.</small></label>
                <label className="field"><span>Service template *</span><select value={v.template_id || ""} onChange={(e) => set("template_id", e.target.value)}><option value="">—</option>{templates.data?.filter((t: any) => t.is_active).map((t: any) => <option key={t.id} value={t.id}>{t.name}</option>)}</select>
                  {tpl && <small>{mapping ? `Maps to line ${mapping.line_profile_ref} / service ${mapping.service_profile_ref} / traffic ${mapping.traffic_table_up_ref}/${mapping.traffic_table_down_ref}, VLAN ${tpl.svlan}, GEM ${tpl.gem_port}` : "No mapping for this OLT's adapter — validation will fail."}</small>}</label>
              </>
            )}
            <label className="field"><span>ONT ID</span><input type="number" placeholder="auto (lowest free)" value={v.ont_index ?? ""} onChange={(e) => set("ont_index", e.target.value)} /></label>
            <label className="field"><span>Description</span><input value={v.description || ""} maxLength={64} onChange={(e) => set("description", e.target.value)} /></label>
            <label className="field"><span>Authentication</span><select value={v.auth_method} onChange={(e) => set("auth_method", e.target.value)}><option value="sn">Serial number</option><option value="loid">LOID</option><option value="password">Password</option></select></label>
            {v.auth_method === "loid" && <label className="field"><span>LOID</span><input value={v.loid ?? x.loid ?? ""} onChange={(e) => set("loid", e.target.value)} /></label>}
            {v.auth_method === "password" && <label className="field"><span>Auth password</span><input type="password" value={v.auth_password || ""} onChange={(e) => set("auth_password", e.target.value)} /></label>}
            <label className="field"><span>{terms.fat}</span><select value={v.fat_id || ""} onChange={(e) => { set("fat_id", e.target.value); set("fat_port_id", ""); }}><option value="">— not recorded —</option>{fats.data?.map((f: any) => <option key={f.id} value={f.id}>{f.name} ({f.capacity_summary.available} free)</option>)}</select></label>
            <label className="field"><span>{terms.fat} port</span><select value={v.fat_port_id || ""} onChange={(e) => set("fat_port_id", e.target.value)} disabled={!v.fat_id}><option value="">—</option>{fatPorts.map((p) => <option key={p.id} value={p.id}>Port {p.number}{p.effective_status === "reserved" ? ` (reserved: ${p.reserved_for})` : ""}</option>)}</select></label>
          </div>
          <div className="note" style={{ marginTop: 12 }}>
            <b>Summary:</b> {v.mode === "register" ? "Register" : "Register and activate service for"} <span className="mono">{x.serial_number}</span> on {x.olt_name} PON {x.pon_port}
            {v.mode === "register" ? ` with line profile ${v.line_profile_ref || "?"} and service profile ${v.service_profile_ref || "?"}` : ` using template ${tpl?.name || "?"}`}
            {fatPort ? `, connected at ${terms.fat} port ${fatPort.number}` : ""}. The request is validated (database checks + device dry run) and then waits for approval. After execution the result is read back from the OLT.
          </div>
          <ErrorBox error={err} />
          <div className="form-actions">
            <button onClick={async () => { await post(`/discovered-onts/${x.id}/ignore`); onClose(); }}>Ignore</button>
            <button className="primary" disabled={busy || (v.mode === "register" ? !v.line_profile_ref || !v.service_profile_ref : !v.service_id || !v.template_id)} onClick={submit}>
              {busy ? "Submitting…" : "Validate & create plan"}
            </button>
          </div>
        </>
      )}
    </Modal>
  );
}

// ================================================================= optical monitoring
export function Optical() {
  const olts = useGet<any[]>("/olts");
  const [olt, setOlt] = useState("");
  const q = useGet<any>("/optical/overview", { olt_id: olt }, 60000);
  const th = useGet<any[]>("/optical-thresholds");
  const nav = useNavigate();
  return (
    <>
      <PageHeader title="Optical signal monitoring" subtitle="Weakest receive levels now, and ONTs whose level has declined over the last 7 days."
        actions={<select value={olt} onChange={(e) => setOlt(e.target.value)}><option value="">All OLTs</option>{olts.data?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select>} />
      {th.data && <div className="note">Thresholds in use: {th.data.map((t: any) => `${t.name}${t.model ? ` [model ${t.model}]` : t.vendor ? ` [vendor ${t.vendor}]` : " [default]"} (warn ≤ ${t.rx_warning_low}, crit ≤ ${t.rx_critical_low} dBm)`).join("; ")}. <Link to="/monitoring/thresholds">Configure</Link></div>}
      {!q.data ? <Loading /> : (
        <>
          <div className="stats">
            <div className="stat ok"><div className="stat-value">{q.data.distribution.ok}</div><div className="stat-label">Within thresholds</div></div>
            <div className="stat warn"><div className="stat-value">{q.data.distribution.warning}</div><div className="stat-label">Warning</div></div>
            <div className="stat bad"><div className="stat-value">{q.data.distribution.critical}</div><div className="stat-label">Critical</div></div>
            <div className="stat"><div className="stat-value">{q.data.distribution.no_reading}</div><div className="stat-label">No current reading</div></div>
          </div>
          <div className="grid c2">
            <Card title="Declining signal (7 days)">
              <Table rows={q.data.declining} onRow={(o: any) => nav(`/onts/${o.id}`)} empty="No ONT declined by ≥ 1 dB" cols={[
                { key: "serial_number", title: "ONT", render: (o: any) => <span className="mono">{o.serial_number}</span> }, { key: "olt", title: "OLT / PON", render: (o: any) => `${o.olt} ${o.pon_port}` },
                { key: "rx_7d_ago", title: "7 d ago", render: (o: any) => dbm(o.rx_7d_ago) }, { key: "rx_now", title: "Now", render: (o: any) => <OpticalValue v={o.rx_now} status={o.optical_status} /> },
                { key: "delta_db", title: "Change", render: (o: any) => <b style={{ color: "var(--bad)" }}>{o.delta_db} dB</b> },
              ]} />
            </Card>
            <Card title="Weakest receive levels">
              <Table rows={q.data.weakest} onRow={(o: any) => nav(`/onts/${o.id}`)} cols={[
                { key: "serial_number", title: "ONT", render: (o: any) => <><span className="mono">{o.serial_number}</span> <SimTag on={o.simulated} /></> }, { key: "olt", title: "OLT / PON", render: (o: any) => `${o.olt} ${o.pon_port}` },
                { key: "rx_power_dbm", title: "ONT Rx", render: (o: any) => <OpticalValue v={o.rx_power_dbm} status={o.optical_status} /> }, { key: "olt_rx_power_dbm", title: "OLT Rx", render: (o: any) => dbm(o.olt_rx_power_dbm) },
                { key: "status", title: "Status", render: (o: any) => <Badge value={o.status} /> },
              ]} />
            </Card>
          </div>
        </>
      )}
    </>
  );
}
