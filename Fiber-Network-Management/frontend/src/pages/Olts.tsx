import { useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { del, post, put } from "../api";
import { useAuth } from "../auth";
import { OpticalValue, SeriesChart, UtilBar } from "../components/domain";
import { Badge, Card, ErrorBox, Form, KV, Loading, Modal, PageHeader, SimTag, Table, Tabs, Unsupported, useJobRunner, type FieldSpec } from "../components/ui";
import { ago, fmtTime, label, num, uptime } from "../format";
import { useGet, useInvalidate } from "../hooks";

// ================================================================= sites
const SITE_FIELDS: FieldSpec[] = [
  { name: "name", label: "Name", required: true }, { name: "code", label: "Code" },
  { name: "site_type", label: "Type", type: "select", required: true, options: [["pop", "POP"], ["central_office", "Central office"], ["cabinet", "Cabinet"], ["street", "Street"], ["other", "Other"]] },
  { name: "address", label: "Address" }, { name: "latitude", label: "Latitude", type: "number", step: "any" }, { name: "longitude", label: "Longitude", type: "number", step: "any" },
  { name: "description", label: "Description", type: "textarea" },
];

export function Sites() {
  const q = useGet<any[]>("/sites");
  const [edit, setEdit] = useState<any>(null);
  const { can } = useAuth();
  const inv = useInvalidate();
  return (
    <>
      <PageHeader title="Network sites" subtitle="Points of presence, central offices and cabinets that house active equipment."
        actions={can("infrastructure.write") && <button className="primary" onClick={() => setEdit({ site_type: "pop" })}>Add site</button>} />
      <Card>
        <Table searchable rows={q.data || []} onRow={can("infrastructure.write") ? setEdit : undefined} cols={[
          { key: "name", title: "Site", render: (s: any) => <><b>{s.name}</b> <SimTag on={s.is_simulated} /></> },
          { key: "code", title: "Code" }, { key: "site_type", title: "Type", render: (s: any) => label(s.site_type) },
          { key: "address", title: "Address" }, { key: "olt_count", title: "OLTs" },
          { key: "lat", title: "GPS", render: (s: any) => (s.latitude != null ? `${s.latitude.toFixed(5)}, ${s.longitude.toFixed(5)}` : "—") },
        ]} />
      </Card>
      {edit && (
        <Modal title={edit.id ? `Edit ${edit.name}` : "Add site"} onClose={() => setEdit(null)}>
          <Form fields={SITE_FIELDS} initial={edit} onCancel={() => setEdit(null)} onSubmit={async (v) => {
            edit.id ? await put(`/sites/${edit.id}`, v) : await post("/sites", v);
            setEdit(null); inv();
          }} />
        </Modal>
      )}
    </>
  );
}

// ================================================================= OLT list
function oltFields(adapters: any[], sites: any[]): FieldSpec[] {
  return [
    { name: "name", label: "OLT name", required: true },
    { name: "adapter", label: "Vendor adapter", type: "select", required: true,
      options: adapters.filter((a) => a.selectable).map((a) => [a.key, `${a.name}${a.experimental ? " (experimental)" : ""}`]),
      help: "Determines how the OLT is managed. Planned vendors are not selectable until implemented." },
    { name: "site_id", label: "Site", type: "select", options: sites.map((s) => [s.id, s.name]) },
    { name: "manufacturer", label: "Manufacturer" }, { name: "model", label: "Model" }, { name: "serial_number", label: "Serial number" },
    { name: "firmware_version", label: "Firmware version" },
    { name: "host", label: "Management IP / hostname", required: true },
    { name: "management_protocol", label: "Management protocol", type: "select", required: true, options: [["ssh", "SSH"], ["telnet", "Telnet (insecure)"], ["api", "Vendor API"], ["snmp", "SNMP only"], ["simulator", "Simulator"]] },
    { name: "management_port", label: "Management port", type: "number" },
    { name: "snmp_version", label: "SNMP version", type: "select", options: [["v3", "SNMPv3 (recommended)"], ["v2c", "SNMPv2c (community — insecure)"]] },
    { name: "snmp_port", label: "SNMP port", type: "number" },
    { name: "snmp_v3_username", label: "SNMPv3 user" },
    { name: "snmp_v3_auth_protocol", label: "SNMPv3 auth", type: "select", options: [["SHA", "SHA"], ["SHA256", "SHA-256"], ["SHA512", "SHA-512"], ["MD5", "MD5 (weak)"]] },
    { name: "snmp_v3_priv_protocol", label: "SNMPv3 privacy", type: "select", options: [["AES", "AES-128"], ["AES256", "AES-256"], ["DES", "DES (weak)"]] },
    { name: "rack_location", label: "Rack / cabinet location" },
    { name: "latitude", label: "Latitude", type: "number", step: "any" }, { name: "longitude", label: "Longitude", type: "number", step: "any" },
    { name: "slot_count", label: "Supported slots", type: "number" }, { name: "pon_port_count", label: "Supported PON ports", type: "number" },
    { name: "admin_status", label: "Administrative status", type: "select", required: true, options: [["enabled", "Enabled"], ["maintenance", "Maintenance"], ["disabled", "Disabled"]] },
    { name: "poll_interval_seconds", label: "Polling interval (s)", type: "number", required: true, help: "Minimum 60 s. Avoid aggressive polling." },
    { name: "monitoring_enabled", label: "Monitoring enabled", type: "checkbox" },
    { name: "notes", label: "Notes", type: "textarea" },
  ];
}

export function OltForm({ olt, onDone }: { olt?: any; onDone: (o: any) => void }) {
  const adapters = useGet<any>("/adapters");
  const sites = useGet<any[]>("/sites");
  if (!adapters.data || !sites.data) return <Loading />;
  const init = olt || { adapter: "simulator", management_protocol: "simulator", admin_status: "enabled", poll_interval_seconds: 300, monitoring_enabled: true, snmp_port: 161, snmp_version: "v3" };
  return (
    <>
      <div className="note">Credentials are set separately (write-only) after the OLT is created and are encrypted at rest. They are never displayed again.</div>
      <Form fields={oltFields(adapters.data.adapters, sites.data)} initial={init} onSubmit={async (v) => {
        const body = { ...v, site_id: v.site_id ? Number(v.site_id) : null };
        onDone(olt ? await put(`/olts/${olt.id}`, body) : await post("/olts", body));
      }} />
    </>
  );
}

export function OltList() {
  const q = useGet<any[]>("/olts", undefined, 30000);
  const [create, setCreate] = useState(false);
  const nav = useNavigate();
  const { can } = useAuth();
  return (
    <>
      <PageHeader title="OLTs" subtitle="Optical line terminals and their management connectivity."
        actions={can("olt.write") && <button className="primary" onClick={() => setCreate(true)}>Register OLT</button>} />
      <Card>
        {!q.data ? <Loading /> : (
          <Table searchable rows={q.data} onRow={(o: any) => nav(`/olts/${o.id}`)} cols={[
            { key: "name", title: "OLT", render: (o: any) => <><b>{o.name}</b> <SimTag on={o.is_simulated} /><div className="vendor">{o.manufacturer} {o.model}</div></> },
            { key: "site_name", title: "Site" },
            { key: "host", title: "Management", render: (o: any) => <span className="mono">{o.management_protocol}://{o.host}{o.management_port ? `:${o.management_port}` : ""}</span> },
            { key: "adapter_name", title: "Adapter", render: (o: any) => <>{o.adapter_name}{o.adapter_experimental && <div><Badge value="unverified" /></div>}</> },
            { key: "connection_status", title: "Connection", render: (o: any) => <><Badge value={o.connection_status} /><div className="vendor">contact {ago(o.last_contact_at)}</div></> },
            { key: "onts_registered", title: "ONTs on / reg", render: (o: any) => `${o.onts_online} / ${o.onts_registered}` },
            { key: "onts_offline", title: "Down", render: (o: any) => (o.onts_offline ? <Badge value={`${o.onts_offline} down`} tone="bad" /> : 0) },
            { key: "onts_discovered", title: "Discovered", render: (o: any) => (o.onts_discovered ? <Badge value={`${o.onts_discovered} new`} tone="info" /> : 0) },
            { key: "active_alarms", title: "Alarms" },
            { key: "admin_status", title: "Admin", render: (o: any) => <Badge value={o.admin_status} /> },
          ]} />
        )}
      </Card>
      {create && (
        <Modal title="Register OLT" onClose={() => setCreate(false)} wide>
          <OltForm onDone={(o) => { setCreate(false); nav(`/olts/${o.id}`); }} />
        </Modal>
      )}
    </>
  );
}

// ================================================================= OLT detail
export function OltDetail() {
  const { id } = useParams();
  const [tab, setTab] = useState("overview");
  const q = useGet<any>(`/olts/${id}`, undefined, 30000);
  const { can } = useAuth();
  const { run, running } = useJobRunner();
  const inv = useInvalidate();
  const [edit, setEdit] = useState(false);
  const [creds, setCreds] = useState(false);
  const nav = useNavigate();
  if (!q.data) return q.error ? <ErrorBox error={q.error} /> : <Loading />;
  const o = q.data;
  const action = (a: string, name: string) => run(name, () => post(`/olts/${o.id}/actions/${a}`), () => inv());
  return (
    <>
      <PageHeader crumbs={[["OLTs", "/olts"], [o.name]]} title={<>{o.name} <SimTag on={o.is_simulated} /></>}
        subtitle={<><Badge value={o.connection_status} /> · {o.manufacturer} {o.model} · {o.adapter_name} · last successful communication {fmtTime(o.last_contact_at)}</>}
        actions={
          <>
            {can("olt.operate") && <button disabled={!!running} onClick={() => action("test-connection", "Connection test")}>Test connection</button>}
            {can("olt.operate") && <button disabled={!!running} onClick={() => action("poll", "Poll")}>Poll now</button>}
            {can("ont.discover") && <button disabled={!!running} onClick={() => action("discover", "ONT discovery")}>Discover ONTs</button>}
            {can("olt.operate") && <button disabled={!!running} onClick={() => action("sync-profiles", "Profile sync")}>Sync profiles</button>}
            {can("olt.write") && <button onClick={() => setEdit(true)}>Edit</button>}
            {can("olt.credentials") && <button onClick={() => setCreds(true)}>Credentials</button>}
          </>
        } />
      {running && <div className="note">{running} running in the background…</div>}
      {o.adapter_experimental && <div className="note warn">This OLT uses an <b>experimental, unverified</b> adapter. Monitoring reads are attempted; write operations are disabled unless explicitly enabled by the administrator after validating the adapter against this equipment.</div>}
      {o.last_poll_error && <div className="error-box">Last poll problem: {o.last_poll_error}</div>}
      <Tabs active={tab} onChange={setTab} tabs={[["overview", "Overview"], ["hardware", "Hardware"], ["ports", "PON ports"], ["onts", "ONTs"], ["profiles", "Profiles"], ["history", "History"], ["capabilities", "Capabilities"], ["jobs", "Operations"]]} />
      {tab === "overview" && <OltOverview o={o} />}
      {tab === "hardware" && <OltHardware id={o.id} />}
      {tab === "ports" && <PonPortTable query={{ olt_id: o.id }} />}
      {tab === "onts" && <OltOnts id={o.id} />}
      {tab === "profiles" && <OltProfiles id={o.id} />}
      {tab === "history" && <OltHistory id={o.id} />}
      {tab === "capabilities" && <CapabilityTable caps={o.capabilities} />}
      {tab === "jobs" && <OltJobs id={o.id} />}
      {edit && <Modal title={`Edit ${o.name}`} onClose={() => setEdit(false)} wide><OltForm olt={o} onDone={() => { setEdit(false); inv(); }} /></Modal>}
      {creds && <CredentialsModal olt={o} onClose={() => { setCreds(false); inv(); }} />}
      {can("olt.write") && tab === "overview" && (
        <div style={{ textAlign: "right" }}>
          <button className="small" onClick={async () => { if (confirm(`Delete OLT ${o.name}? Only possible when no ONTs are registered.`)) { try { await del(`/olts/${o.id}`); nav("/olts"); } catch (e: any) { alert(e.message); } } }}>Delete OLT</button>
        </div>
      )}
    </>
  );
}

function OltOverview({ o }: { o: any }) {
  return (
    <div className="grid c2">
      <Card title="Identity & management">
        <KV rows={[
          ["Manufacturer", o.manufacturer], ["Model", o.model], ["Serial number", o.serial_number], ["Firmware", o.firmware_version],
          ["Site", o.site_name], ["Rack / cabinet", o.rack_location],
          ["GPS", o.latitude != null ? `${o.latitude}, ${o.longitude}` : null],
          ["Management", <span className="mono">{o.management_protocol}://{o.host}{o.management_port ? `:${o.management_port}` : ""}</span>],
          ["Username", o.username], ["Password stored", o.has_password ? "yes (encrypted)" : "no"],
          ["SNMP", o.snmp_version ? o.snmp_security : "not configured"],
          ["SNMP credentials stored", o.has_snmp_community || o.has_snmp_v3_auth_key ? "yes (encrypted)" : "no"],
          ["Supported slots / PON ports", `${o.slot_count ?? "—"} / ${o.pon_port_count ?? "—"}`],
          ["Administrative status", <Badge value={o.admin_status} />],
        ]} />
      </Card>
      <Card title="Health & ONTs">
        <KV rows={[
          ["Connection", <Badge value={o.connection_status} />], ["Last successful communication", fmtTime(o.last_contact_at)],
          ["Last poll", fmtTime(o.last_poll_at)], ["Next poll", `${fmtTime(o.next_poll_at)} (${ago(o.next_poll_at)})`],
          ["Polling interval", `${o.poll_interval_seconds} s ${o.monitoring_enabled ? "" : "(monitoring disabled)"}`],
          ["CPU", o.cpu_percent != null ? num(o.cpu_percent, " %") : <Unsupported />],
          ["Memory", o.memory_percent != null ? num(o.memory_percent, " %") : <Unsupported />],
          ["Temperature", o.temperature_c != null ? num(o.temperature_c, " °C") : <Unsupported />],
          ["Uptime", o.uptime_seconds != null ? uptime(o.uptime_seconds) : <Unsupported />],
          ["Registered ONTs", o.onts_registered], ["Online ONTs", o.onts_online], ["Offline / LOS / dying-gasp", o.onts_offline],
          ["Unreachable (state unknown)", o.onts_unreachable], ["Discovered, unregistered", <Link to={`/discovered`}>{o.onts_discovered}</Link>],
          ["Active alarms", <Link to={`/alarms?olt_id=${o.id}`}>{o.active_alarms}</Link>],
        ]} />
      </Card>
    </div>
  );
}

function OltHardware({ id }: { id: number }) {
  const q = useGet<any>(`/olts/${id}/hardware`);
  const nav = useNavigate();
  if (!q.data) return <Loading />;
  if (!q.data.frames.length) return <Card><div className="empty">No hardware recorded. {q.data.supports_hardware_discovery ? "Poll the OLT to discover it." : "This adapter cannot discover hardware; add boards manually."}</div></Card>;
  return (
    <>
      <div className="note">{q.data.source_note}</div>
      {q.data.frames.map((f: any) => (
        <Card key={f.frame} title={`Frame / shelf ${f.frame}`}>
          <div className="chassis">
            {f.boards.map((b: any) => (
              <div key={b.id} className={`slot ${b.installed ? "" : "empty"} ${b.status === "failed" ? "failed" : ""}`}>
                <div style={{ display: "flex", justifyContent: "space-between" }}>
                  <b>Slot {b.slot}</b> <Badge value={b.installed ? b.status : "empty"} />
                </div>
                {b.installed ? (
                  <>
                    <div className="mono">{b.board_type}</div>
                    <div className="vendor">{b.role} · vendor: {b.vendor_status || "—"} · {b.source}</div>
                    <div className="vendor">SN {b.serial_number || "—"} · HW {b.hardware_version || "—"} · SW {b.software_version || "—"}</div>
                    {b.ports.length > 0 && (
                      <div className="ponports">
                        {b.ports.map((p: any) => (
                          <div key={p.id} className={`ponport ${p.oper_status}`} title={`${p.port_type} ${p.fsp}: ${p.oper_status} (${p.vendor_status || ""}) ${p.registered_onts ?? 0} ONTs`}
                            onClick={() => p.port_type === "pon" && nav(`/pon-ports/${p.id}`)}>
                            {p.port}{p.port_type === "pon" ? <><br />{p.registered_onts ?? 0}</> : <><br />UP</>}
                          </div>
                        ))}
                      </div>
                    )}
                  </>
                ) : <div className="vendor">Available slot</div>}
              </div>
            ))}
          </div>
        </Card>
      ))}
    </>
  );
}

export function PonPortTable({ query }: { query: Record<string, any> }) {
  const q = useGet<any[]>("/pon-ports", query, 30000);
  const nav = useNavigate();
  if (!q.data) return <Loading />;
  return (
    <Card>
      <Table searchable rows={q.data} onRow={(p: any) => nav(`/pon-ports/${p.id}`)} cols={[
        { key: "olt_name", title: "OLT" }, { key: "fsp", title: "Port (F/S/P)", render: (p: any) => <b className="mono">{p.fsp}</b> },
        { key: "technology", title: "Tech" }, { key: "label", title: "Label" },
        { key: "oper_status", title: "Status", render: (p: any) => <><Badge value={p.oper_status} /> <span className="vendor">{p.vendor_status}</span></> },
        { key: "admin_status", title: "Admin", render: (p: any) => <Badge value={p.admin_status} /> },
        { key: "registered_onts", title: "ONTs (online)", render: (p: any) => `${p.registered_onts} (${p.online_onts ?? "—"})` },
        { key: "onts_down", title: "Down", render: (p: any) => (p.onts_down ? <Badge value={`${p.onts_down}`} tone="bad" /> : 0) },
        { key: "utilization_percent", title: "Utilization", render: (p: any) => (p.utilization_percent != null ? <UtilBar pct={p.utilization_percent} /> : "—") },
        { key: "tx_power_dbm", title: "Tx power", render: (p: any) => (p.tx_power_dbm != null ? `${p.tx_power_dbm} dBm` : <Unsupported text="n/a" />) },
      ]} />
    </Card>
  );
}

function OltOnts({ id }: { id: number }) {
  const q = useGet<any>("/onts", { olt_id: id, limit: 500 });
  const nav = useNavigate();
  if (!q.data) return <Loading />;
  return <Card><OntTable rows={q.data.items} onOpen={(o) => nav(`/onts/${o.id}`)} /></Card>;
}

export function OntTable({ rows, onOpen }: { rows: any[]; onOpen: (o: any) => void }) {
  return (
    <Table searchable rows={rows} onRow={onOpen} cols={[
      { key: "serial_number", title: "Serial", render: (o: any) => <><b className="mono">{o.serial_number}</b> <SimTag on={o.is_simulated} /><div className="vendor">{o.model || o.equipment_id}</div></> },
      { key: "status", title: "Status", render: (o: any) => <><Badge value={o.status} /><div className="vendor" title="vendor-reported">{o.vendor_status}</div></> },
      { key: "pon_port", title: "OLT / PON / ID", render: (o: any) => <span className="mono">{o.olt_name} {o.pon_port} #{o.ont_index ?? "—"}</span> },
      { key: "fat", title: "FAT port", render: (o: any) => (o.fat ? `${o.fat.fat_name} P${o.fat.fat_port_number}` : <span className="unsupported">not recorded</span>) },
      { key: "subscriber", title: "Subscriber", render: (o: any) => (o.subscriber ? <>{o.subscriber.display_name}<div className="vendor">{o.subscriber.customer_ref} · {o.subscriber.service_status}</div></> : "—") },
      { key: "rx_power_dbm", title: "Rx", render: (o: any) => <OpticalValue v={o.rx_power_dbm} status={o.optical_status} />, sort: (o: any) => o.rx_power_dbm },
      { key: "status_changed_at", title: "Status since", render: (o: any) => <span title={fmtTime(o.status_changed_at)}>{ago(o.status_changed_at)}</span> },
    ]} />
  );
}

function OltProfiles({ id }: { id: number }) {
  const q = useGet<any[]>(`/olts/${id}/profiles`);
  if (!q.data) return <Loading />;
  return (
    <Card title="Vendor profiles present on the OLT" actions={<Link to="/provisioning/vendor-profiles">Manage</Link>}>
      <Table searchable rows={q.data} cols={[
        { key: "kind", title: "Kind", render: (p: any) => label(p.kind) }, { key: "vendor_ref", title: "Device ID / index", render: (p: any) => <span className="mono">{p.vendor_ref}</span> },
        { key: "name", title: "Name" }, { key: "parameters", title: "Parameters", render: (p: any) => <span className="mono">{p.parameters ? JSON.stringify(p.parameters) : "—"}</span> },
        { key: "source", title: "Source", render: (p: any) => <Badge value={p.source === "device" ? "synced from device" : "manual"} tone={p.source === "device" ? "ok" : "muted"} /> },
        { key: "last_synced_at", title: "Synced", render: (p: any) => fmtTime(p.last_synced_at) },
      ]} />
    </Card>
  );
}

function OltHistory({ id }: { id: number }) {
  const [hours, setHours] = useState(24);
  const q = useGet<any[]>(`/olts/${id}/metrics`, { hours });
  return (
    <Card title="Historical monitoring" actions={
      <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
        <option value={6}>6 h</option><option value={24}>24 h</option><option value={168}>7 days</option><option value={720}>30 days</option>
      </select>
    }>
      {!q.data ? <Loading /> : (
        <>
          <SeriesChart data={q.data.filter((m) => m.reachable)} lines={[["cpu_percent", "CPU %", "#1f6feb"], ["memory_percent", "Memory %", "#8250df"], ["temperature_c", "Temp °C", "#d4a72c"]]} />
          <SeriesChart data={q.data} lines={[["onts_online", "ONTs online", "#1a7f37"], ["onts_total", "ONTs registered", "#7a8699"]]} />
          <div className="vendor">{q.data.filter((m) => !m.reachable).length} failed poll(s) in this window. Values the adapter does not support are absent rather than estimated.</div>
        </>
      )}
    </Card>
  );
}

export function CapabilityTable({ caps }: { caps: Record<string, any> }) {
  return (
    <Card title="Adapter capabilities" actions={<Link to="/integrations">All adapters</Link>}>
      <Table rows={Object.entries(caps).map(([k, v]) => ({ id: k, ...v }))} cols={[
        { key: "id", title: "Capability", render: (c: any) => label(c.id) },
        { key: "supported", title: "Supported", render: (c: any) => <Badge value={c.supported ? "supported" : "unsupported"} tone={c.supported ? "ok" : "muted"} /> },
        { key: "verification", title: "Verification", render: (c: any) => (c.verification ? <Badge value={c.verification} tone={c.verification === "hardware_verified" ? "ok" : c.verification === "simulated" ? "info" : "warn"} /> : "—") },
        { key: "notes", title: "Notes" },
      ]} />
    </Card>
  );
}

function OltJobs({ id }: { id: number }) {
  const q = useGet<any[]>(`/olts/${id}/jobs`, { limit: 50 }, 10000);
  const [show, setShow] = useState<any>(null);
  if (!q.data) return <Loading />;
  return (
    <Card title="Recent background operations">
      <Table rows={q.data} onRow={setShow} cols={[
        { key: "created_at", title: "Queued", render: (j: any) => fmtTime(j.created_at) }, { key: "job_type", title: "Operation" },
        { key: "status", title: "Status", render: (j: any) => <Badge value={j.status} /> }, { key: "created_by", title: "By" },
        { key: "finished_at", title: "Finished", render: (j: any) => fmtTime(j.finished_at) }, { key: "error", title: "Error" },
      ]} />
      {show && (
        <Modal title={`${show.job_type} ${show.id.slice(0, 8)}`} onClose={() => setShow(null)} wide>
          {show.result?.config ? <pre className="code">{show.result.config}</pre> : <pre className="code">{JSON.stringify(show.result || show.error, null, 2)}</pre>}
        </Modal>
      )}
    </Card>
  );
}

function CredentialsModal({ olt, onClose }: { olt: any; onClose: () => void }) {
  return (
    <Modal title={`Credentials — ${olt.name}`} onClose={onClose}>
      <div className="note">Write-only. Leave a field empty to keep the stored value. Stored values are encrypted and are never shown. Changes are audited (without the secret values).</div>
      <Form fields={[
        { name: "username", label: "Username" }, { name: "password", label: "Password", type: "password" },
        { name: "enable_password", label: "Enable password", type: "password" }, { name: "api_token", label: "API token", type: "password" },
        { name: "snmp_community", label: "SNMPv2c community", type: "password", help: "Only if SNMPv3 is unavailable" },
        { name: "snmp_v3_auth_key", label: "SNMPv3 auth key", type: "password" }, { name: "snmp_v3_priv_key", label: "SNMPv3 privacy key", type: "password" },
      ]} initial={{ username: olt.username || "" }} onCancel={onClose} onSubmit={async (v) => {
        const body: any = {};
        Object.entries(v).forEach(([k, val]) => { if (val) body[k] = val; });
        if (v.username !== (olt.username || "")) body.username = v.username;
        await put(`/olts/${olt.id}/credentials`, body);
        onClose();
      }} submitLabel="Save credentials" />
    </Modal>
  );
}

// ================================================================= PON ports
export function PonPorts() {
  const olts = useGet<any[]>("/olts");
  const [olt, setOlt] = useState("");
  const [status, setStatus] = useState("");
  return (
    <>
      <PageHeader title="PON ports" subtitle="Port status and ONT utilization across all OLTs." actions={
        <>
          <select value={olt} onChange={(e) => setOlt(e.target.value)}><option value="">All OLTs</option>{olts.data?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select>
          <select value={status} onChange={(e) => setStatus(e.target.value)}><option value="">Any status</option><option value="up">Up</option><option value="fault">Fault</option><option value="down">Down</option></select>
        </>
      } />
      <PonPortTable query={{ olt_id: olt, status }} />
    </>
  );
}

export function PonPortDetail() {
  const { id } = useParams();
  const q = useGet<any>(`/pon-ports/${id}`, undefined, 30000);
  const metrics = useGet<any[]>(`/pon-ports/${id}/metrics`, { hours: 24 });
  const impact = useGet<any>(`/impact/olt_port/${id}`);
  const nav = useNavigate();
  if (!q.data) return q.error ? <ErrorBox error={q.error} /> : <Loading />;
  const p = q.data;
  return (
    <>
      <PageHeader crumbs={[["OLTs", "/olts"], [p.olt_name, `/olts/${p.olt_id}`], [`PON ${p.fsp}`]]} title={`PON ${p.fsp} — ${p.olt_name}`}
        subtitle={<><Badge value={p.oper_status} /> vendor: {p.vendor_status || "—"} · {p.technology || ""} · last seen {fmtTime(p.last_seen_at)}</>}
        actions={<Link to={`/topology?pon_port_id=${p.id}`}><button>Show in topology</button></Link>} />
      <div className="grid c3">
        <Card title="Port"><KV rows={[["Admin", <Badge value={p.admin_status} />], ["Registered ONTs", p.registered_onts], ["Online (last poll)", p.online_onts ?? "—"],
          ["Max ONTs", p.max_onts ?? "—"], ["Utilization", p.utilization_percent != null ? <UtilBar pct={p.utilization_percent} /> : "—"], ["Tx power", p.tx_power_dbm != null ? `${p.tx_power_dbm} dBm` : <Unsupported />]]} /></Card>
        <Card title="Downstream dependency">{impact.data ? <KV rows={[["ONTs", impact.data.summary.onts], ["Physically traced", impact.data.summary.physical_onts], ["Subscribers", impact.data.summary.subscribers],
          ["Infrastructure elements", impact.data.infrastructure.length], ["Currently down", impact.data.summary.confirmed_down]]} /> : <Loading />}</Card>
        <Card title="Discovered here">{p.discovered.length ? p.discovered.map((d: any) => <div key={d.id}><Link to="/discovered">{d.serial_number}</Link> {d.rx_power_dbm} dBm</div>) : <div className="vendor">None</div>}</Card>
      </div>
      <Card title="ONT history on this port (24 h)"><SeriesChart data={metrics.data || []} lines={[["onts_online", "Online", "#1a7f37"], ["onts_total", "Configured", "#7a8699"]]} /></Card>
      <Card title={`ONTs (${p.onts.length})`}><OntTable rows={p.onts} onOpen={(o) => nav(`/onts/${o.id}`)} /></Card>
    </>
  );
}
