import { useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { post, patch } from "../api";
import { useAuth } from "../auth";
import { elementLink } from "../components/domain";
import { useTerms } from "../components/Layout";
import { Badge, Card, ErrorBox, KV, Loading, Modal, PageHeader, Pager, SimTag, Stat, Table, useToast } from "../components/ui";
import { ago, fmtTime, label } from "../format";
import { useGet, useInvalidate } from "../hooks";

export function Dashboard() {
  const [site, setSite] = useState("");
  const [olt, setOlt] = useState("");
  const sites = useGet<any[]>("/sites");
  const olts = useGet<any[]>("/olts");
  const d = useGet<any>("/dashboard", { site_id: site, olt_id: olt }, 20000);
  const t = useTerms();
  if (d.isLoading) return <Loading />;
  if (d.error) return <ErrorBox error={d.error} />;
  const x = d.data;
  return (
    <>
      <PageHeader title="Network dashboard" subtitle={`Generated ${fmtTime(x.generated_at)} · auto-refreshes every 20 s`}
        actions={
          <>
            <select value={site} onChange={(e) => setSite(e.target.value)}>
              <option value="">All sites</option>
              {sites.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
            </select>
            <select value={olt} onChange={(e) => setOlt(e.target.value)}>
              <option value="">All OLTs</option>
              {olts.data?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
            </select>
          </>
        } />
      {x.olts.simulated > 0 && <div className="note sim">{x.olts.simulated} OLT(s) in this view are <b>simulated</b>. Their devices, statuses, optical levels and alarms are produced by the built-in simulator, not by live equipment.</div>}
      <h3 style={{ margin: "4px 0 8px" }}>OLTs</h3>
      <div className="stats">
        <Stat label="Total OLTs" value={x.olts.total} to="/olts" />
        <Stat label="Online" value={x.olts.online} tone="ok" to="/olts" />
        <Stat label="Degraded" value={x.olts.degraded} tone={x.olts.degraded ? "warn" : ""} />
        <Stat label="Unreachable" value={x.olts.unreachable} tone={x.olts.unreachable ? "bad" : ""} hint="Communication failure — ONT state unknown, not necessarily down" />
        <Stat label="PON ports up / fault" value={`${x.pon_ports.up} / ${x.pon_ports.fault}`} tone={x.pon_ports.fault ? "bad" : ""} to="/pon-ports" />
      </div>
      <h3 style={{ margin: "4px 0 8px" }}>ONTs</h3>
      <div className="stats">
        <Stat label="Registered" value={x.onts.registered} to="/onts" />
        <Stat label="Online" value={x.onts.online} tone="ok" to="/onts?status=online" />
        <Stat label="Offline" value={x.onts.offline} tone={x.onts.offline ? "bad" : ""} to="/onts?status=offline" />
        <Stat label="LOS" value={x.onts.los} tone={x.onts.los ? "bad" : ""} to="/onts?status=los" />
        <Stat label="Dying gasp" value={x.onts.dying_gasp} tone={x.onts.dying_gasp ? "warn" : ""} to="/onts?status=dying_gasp" />
        <Stat label="Unreachable (OLT down)" value={x.onts.unreachable} tone={x.onts.unreachable ? "warn" : ""} to="/onts?status=unreachable" />
        <Stat label="Discovered, unregistered" value={x.onts.discovered_unregistered} tone={x.onts.discovered_unregistered ? "info" : ""} to="/discovered" />
        <Stat label="Optical warning / critical" value={`${x.onts.optical_warning} / ${x.onts.optical_critical}`} tone={x.onts.optical_critical ? "bad" : x.onts.optical_warning ? "warn" : ""} to="/optical" />
      </div>
      <h3 style={{ margin: "4px 0 8px" }}>Distribution & faults</h3>
      <div className="stats">
        <Stat label={`Total ${t.fats}`} value={x.fats.total} to="/fats" />
        <Stat label={`${t.fat} port utilization`} value={`${x.fats.utilization_percent}%`} hint={`${x.fats.ports_occupied}/${x.fats.ports_total} ports occupied`} />
        <Stat label={`${t.fats} near full`} value={x.fats.near_full} tone={x.fats.near_full ? "warn" : ""} to="/fats?near_full=1" />
        <Stat label="Active alarms" value={x.alarms.active} tone={x.alarms.critical ? "bad" : x.alarms.active ? "warn" : "ok"} to="/alarms" />
        <Stat label="Critical / major" value={`${x.alarms.critical} / ${x.alarms.major}`} tone={x.alarms.critical ? "bad" : ""} to="/alarms?severity=critical,major" />
        <Stat label="Active incidents" value={x.incidents.active} tone={x.incidents.active ? "bad" : "ok"} to="/incidents" />
      </div>
      <div className="grid c32">
        <Card title="Active network incidents" actions={<Link to="/incidents">All incidents</Link>}>
          <IncidentTable rows={x.incidents.items} />
        </Card>
        <Card title="OLT status">
          <Table rows={x.olt_list} cols={[
            { key: "name", title: "OLT", render: (o: any) => <><Link to={`/olts/${o.id}`}>{o.name}</Link> <SimTag on={o.simulated} /></> },
            { key: "status", title: "Status", render: (o: any) => <Badge value={o.status} /> },
            { key: "last_contact_at", title: "Last contact", render: (o: any) => <span title={fmtTime(o.last_contact_at)}>{ago(o.last_contact_at)}</span> },
          ]} />
        </Card>
      </div>
    </>
  );
}

function IncidentTable({ rows }: { rows: any[] }) {
  const nav = useNavigate();
  return (
    <Table rows={rows} empty="No active incidents" onRow={(i: any) => nav(`/incidents/${i.id}`)} cols={[
      { key: "severity", title: "Sev", render: (i: any) => <Badge value={i.severity} /> },
      { key: "title", title: "Incident", render: (i: any) => <><b>{i.title}</b> <SimTag on={i.is_simulated} /><div className="vendor">{i.suspected_cause}</div></> },
      { key: "affected_onts", title: "ONTs (confirmed down)", render: (i: any) => `${i.affected_onts} (${i.confirmed_down_onts})` },
      { key: "affected_subscribers", title: "Subscribers" },
      { key: "detection", title: "Detection", render: (i: any) => label(i.detection) },
      { key: "started_at", title: "Started", render: (i: any) => <span title={fmtTime(i.started_at)}>{ago(i.started_at)}</span> },
      { key: "status", title: "Status", render: (i: any) => <Badge value={i.status} /> },
    ]} />
  );
}

export function Incidents() {
  const [status, setStatus] = useState("active");
  const [offset, setOffset] = useState(0);
  const q = useGet<any>("/incidents", { status, limit: 50, offset }, 20000);
  return (
    <>
      <PageHeader title="Network incidents" subtitle="Outages correlated from device alarms and the fiber topology. Impacts distinguish confirmed outages from inferred dependencies."
        actions={
          <select value={status} onChange={(e) => { setStatus(e.target.value); setOffset(0); }}>
            <option value="active">Active</option><option value="resolved">Resolved</option><option value="all">All</option>
          </select>
        } />
      <Card>{q.data ? <><IncidentTable rows={q.data.items} /><Pager {...q.data} onChange={setOffset} /></> : <Loading />}</Card>
    </>
  );
}

export function IncidentDetail() {
  const { id } = useParams();
  const q = useGet<any>(`/incidents/${id}`, { live: true }, 20000);
  const { can } = useAuth();
  const inv = useInvalidate();
  const [note, setNote] = useState("");
  const [err, setErr] = useState<any>(null);
  if (!q.data) return q.error ? <ErrorBox error={q.error} /> : <Loading />;
  const i = q.data;
  const im = i.impact || { summary: {}, onts: [], subscribers: [], infrastructure: [] };
  const update = async (body: any) => {
    setErr(null);
    try {
      await patch(`/incidents/${i.id}`, body);
      setNote("");
      inv();
    } catch (e) {
      setErr(e);
    }
  };
  return (
    <>
      <PageHeader crumbs={[["Incidents", "/incidents"], [`#${i.id}`]]} title={<>{i.title} <SimTag on={i.is_simulated} /></>}
        subtitle={<><Badge value={i.status} /> <Badge value={i.severity} /> · started {fmtTime(i.started_at)} {i.resolved_at && `· resolved ${fmtTime(i.resolved_at)} by ${i.resolved_by}`}</>}
        actions={can("incident.manage") && (
          <>
            {i.status === "open" && <button onClick={() => update({ status: "investigating" })}>Mark investigating</button>}
            {i.status !== "resolved" ? <button className="primary" onClick={() => update({ status: "resolved", note })}>Resolve</button>
              : <button onClick={() => update({ status: "open", note: "Reopened" })}>Reopen</button>}
          </>
        )} />
      <ErrorBox error={err} />
      <div className="grid c32">
        <Card title="Incident">
          <KV rows={[
            ["Source element", elementLink(i.source_type === "board" ? undefined : i.source_type, i.source_id, i.source_label)],
            ["Suspected cause", i.suspected_cause],
            ["Detection", i.detection === "automatic" ? "Automatic correlation" : "Opened manually"],
            ["Affected ONTs", im.summary.onts], ["Confirmed down (device status)", im.summary.confirmed_down],
            ["Inferred (dependency only)", im.summary.inferred], ["Unknown (no status available)", im.summary.unknown],
            ["Affected subscribers", im.summary.subscribers],
          ]} />
          <div className="note" style={{ marginTop: 12 }}>
            <b>Confirmed</b> = the ONT's own status reported by the OLT shows it down. <b>Inferred</b> = depends on the failed element per the recorded topology, but its status does not confirm an outage. <b>Unknown</b> = status cannot be retrieved (e.g. OLT unreachable). Subscribers are never marked disconnected on inference alone.
          </div>
        </Card>
        <Card title="Notes & timeline">
          <pre className="code" style={{ whiteSpace: "pre-wrap", background: "#f6f8fa", color: "#1b2430" }}>{i.notes || "No notes"}</pre>
          {can("incident.manage") && (
            <div style={{ display: "flex", gap: 6 }}>
              <input placeholder="Add a note…" value={note} onChange={(e) => setNote(e.target.value)} />
              <button disabled={!note} onClick={() => update({ note })}>Add</button>
            </div>
          )}
        </Card>
      </div>
      <Card title={`Affected ONTs (${im.onts.length})`}>
        <Table searchable rows={im.onts} cols={[
          { key: "serial_number", title: "ONT", render: (o: any) => <Link to={`/onts/${o.id}`}>{o.serial_number}</Link> },
          { key: "evidence", title: "Evidence", render: (o: any) => <Badge value={o.evidence} /> },
          { key: "status", title: "Status", render: (o: any) => <><Badge value={o.status} /> <span className="vendor">{o.vendor_status}</span></> },
          { key: "dependency", title: "Dependency" },
          { key: "olt", title: "OLT / PON", render: (o: any) => `${o.olt || "—"} ${o.pon_port || ""}` },
          { key: "services", title: "Services", render: (o: any) => o.services.join(", ") || "—" },
        ]} />
      </Card>
      <div className="grid c2">
        <Card title={`Affected subscribers (${im.subscribers.length})`}>
          <Table rows={im.subscribers} cols={[
            { key: "customer_ref", title: "Customer", render: (s: any) => <Link to={`/subscribers/${s.id}`}>{s.customer_ref}</Link> },
            { key: "display_name", title: "Name" }, { key: "service_ref", title: "Service" },
            { key: "evidence", title: "Evidence", render: (s: any) => <Badge value={s.evidence} /> },
          ]} />
        </Card>
        <Card title="Affected infrastructure">
          <Table rows={im.infrastructure} rowKey="label" cols={[
            { key: "type", title: "Type" }, { key: "label", title: "Element", render: (x: any) => elementLink(x.type, x.id, x.label) },
            { key: "status", title: "Status", render: (x: any) => <Badge value={x.status} /> },
          ]} />
        </Card>
      </div>
      <Card title="Correlated alarms">
        <AlarmTable rows={i.alarms} onOpen={() => {}} />
      </Card>
    </>
  );
}

function AlarmTable({ rows, onOpen, selectable, selected, setSelected }: { rows: any[]; onOpen: (a: any) => void; selectable?: boolean; selected?: Set<number>; setSelected?: (s: Set<number>) => void }) {
  return (
    <Table rows={rows} empty="No alarms" onRow={onOpen} cols={[
      ...(selectable ? [{ key: "sel", title: "", render: (a: any) => (
        <input type="checkbox" checked={selected!.has(a.id)} onClick={(e) => e.stopPropagation()} onChange={(e) => {
          const s = new Set(selected); e.target.checked ? s.add(a.id) : s.delete(a.id); setSelected!(s);
        }} />
      ) }] : []),
      { key: "severity", title: "Severity", render: (a: any) => <Badge value={a.severity} />, sort: (a: any) => ({ critical: 0, major: 1, minor: 2, warning: 3, info: 4 } as any)[a.severity] },
      { key: "category", title: "Category", render: (a: any) => label(a.category) },
      { key: "source_label", title: "Source", render: (a: any) => <>{elementLink(a.source_type === "board" ? undefined : a.source_type, a.source_id, a.source_label)} <SimTag on={a.is_simulated} /></> },
      { key: "description", title: "Description", render: (a: any) => <>{a.description}{a.vendor_alarm_id && <div className="vendor">vendor id {a.vendor_alarm_id} · vendor severity {a.vendor_severity || "—"}</div>}</> },
      { key: "origin", title: "Origin", render: (a: any) => <span title={a.origin === "device" ? "Reported by the equipment" : "Derived by this system"}>{a.origin}</span> },
      { key: "first_occurred_at", title: "First / last", render: (a: any) => <span title={`${fmtTime(a.first_occurred_at)} / ${fmtTime(a.last_occurred_at)}`}>{ago(a.first_occurred_at)}<div className="vendor">last {ago(a.last_occurred_at)} · ×{a.occurrence_count}</div></span> },
      { key: "state", title: "State", render: (a: any) => <><Badge value={a.state} />{a.acknowledged_by && <div className="vendor">ack {a.acknowledged_by}</div>}</> },
    ]} />
  );
}

export function Alarms() {
  const [sp] = useSearchParams();
  const [f, setF] = useState({ state: "open", severity: sp.get("severity") || "", category: "", olt_id: sp.get("olt_id") || "", q: "", origin: "" });
  const [offset, setOffset] = useState(0);
  const [open, setOpen] = useState<any>(null);
  const [selected, setSelected] = useState<Set<number>>(new Set());
  const olts = useGet<any[]>("/olts");
  const q = useGet<any>("/alarms", { ...f, limit: 50, offset }, 20000);
  const { can } = useAuth();
  const inv = useInvalidate();
  const { push } = useToast();
  const set = (k: string, v: string) => { setF((s) => ({ ...s, [k]: v })); setOffset(0); };
  return (
    <>
      <PageHeader title="Alarm management" subtitle="Alarms retrieved from equipment (vendor id/severity preserved) and alarms derived by this system. Cleared alarms are kept as history."
        actions={can("alarm.manage") && selected.size > 0 && (
          <button className="primary" onClick={async () => {
            await post("/alarms/acknowledge", { alarm_ids: [...selected] });
            push(`${selected.size} alarm(s) acknowledged`, "ok"); setSelected(new Set()); inv();
          }}>Acknowledge selected ({selected.size})</button>
        )} />
      <div className="filters">
        <select value={f.state} onChange={(e) => set("state", e.target.value)}>
          <option value="open">Open (new/ack/investigating/reopened)</option><option value="new">New</option><option value="acknowledged">Acknowledged</option>
          <option value="investigating">Investigating</option><option value="reopened">Reopened</option><option value="resolved">Resolved</option><option value="all">All</option>
        </select>
        <select value={f.severity} onChange={(e) => set("severity", e.target.value)}>
          <option value="">All severities</option>{["critical", "major", "minor", "warning", "info"].map((s) => <option key={s}>{s}</option>)}
          <option value="critical,major">critical + major</option>
        </select>
        <select value={f.category} onChange={(e) => set("category", e.target.value)}>
          <option value="">All categories</option>
          {["olt_unreachable", "board_failure", "pon_port_failure", "ont_los", "ont_dying_gasp", "low_optical_signal", "high_optical_signal", "power_failure", "temperature", "uplink_failure", "ont_registration_failure", "other"].map((c) => <option key={c} value={c}>{label(c)}</option>)}
        </select>
        <select value={f.olt_id} onChange={(e) => set("olt_id", e.target.value)}>
          <option value="">All OLTs</option>{olts.data?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
        </select>
        <select value={f.origin} onChange={(e) => set("origin", e.target.value)}>
          <option value="">Any origin</option><option value="device">Device-reported</option><option value="nms">NMS-derived</option>
        </select>
        <input placeholder="Search description/source" value={f.q} onChange={(e) => set("q", e.target.value)} />
      </div>
      <Card>
        {q.data ? <><AlarmTable rows={q.data.items} onOpen={setOpen} selectable={can("alarm.manage")} selected={selected} setSelected={setSelected} /><Pager {...q.data} onChange={setOffset} /></> : <Loading />}
      </Card>
      {open && <AlarmModal id={open.id} onClose={() => setOpen(null)} />}
    </>
  );
}

function AlarmModal({ id, onClose }: { id: number; onClose: () => void }) {
  const q = useGet<any>(`/alarms/${id}`);
  const { can } = useAuth();
  const inv = useInvalidate();
  const [note, setNote] = useState("");
  const [err, setErr] = useState<any>(null);
  const act = async (action: string) => {
    setErr(null);
    try {
      await post(`/alarms/${id}/actions`, { action, note });
      setNote("");
      inv();
    } catch (e) {
      setErr(e);
    }
  };
  const a = q.data;
  return (
    <Modal title={`Alarm #${id}`} onClose={onClose} wide>
      {!a ? <Loading /> : (
        <>
          <KV rows={[
            ["Severity", <Badge value={a.severity} />], ["State", <Badge value={a.state} />], ["Category", label(a.category)],
            ["Source", elementLink(a.source_type === "board" ? undefined : a.source_type, a.source_id, a.source_label)], ["OLT", a.olt_name],
            ["Origin", a.origin === "device" ? "Reported by equipment" : a.origin === "nms" ? "Derived by NMS" : a.origin],
            ["Vendor alarm id", a.vendor_alarm_id], ["Vendor severity", a.vendor_severity], ["First occurrence", fmtTime(a.first_occurred_at)],
            ["Last occurrence", fmtTime(a.last_occurred_at)], ["Occurrences", a.occurrence_count], ["Acknowledged", a.acknowledged ? `${a.acknowledged_by} at ${fmtTime(a.acknowledged_at)}` : "no"],
            ["Cleared on device", fmtTime(a.cleared_on_device_at)], ["Resolved", a.resolved_at ? `${fmtTime(a.resolved_at)} by ${a.resolved_by}` : "—"],
            ["Incident", a.incident_id ? <Link to={`/incidents/${a.incident_id}`}>#{a.incident_id}</Link> : "—"], ["Description", a.description],
          ]} />
          <h3 style={{ margin: "16px 0 8px" }}>History</h3>
          <Table rows={a.events} cols={[
            { key: "ts", title: "Time", render: (e: any) => fmtTime(e.ts) }, { key: "action", title: "Action", render: (e: any) => <Badge value={e.action} tone="muted" /> },
            { key: "actor", title: "By" }, { key: "note", title: "Note" },
          ]} />
          <ErrorBox error={err} />
          {can("alarm.manage") && (
            <div className="form-actions" style={{ flexWrap: "wrap" }}>
              <input placeholder="Note (optional)" value={note} onChange={(e) => setNote(e.target.value)} style={{ flex: 1, minWidth: 200 }} />
              {["new", "reopened"].includes(a.state) && <button onClick={() => act("acknowledge")}>Acknowledge</button>}
              {["new", "reopened", "acknowledged"].includes(a.state) && <button onClick={() => act("investigate")}>Investigating</button>}
              {a.state !== "resolved" && <button className="primary" onClick={() => act("resolve")}>Resolve</button>}
              {a.state === "resolved" && <button onClick={() => act("reopen")}>Reopen</button>}
              <button disabled={!note} onClick={() => act("note")}>Add note</button>
            </div>
          )}
        </>
      )}
    </Modal>
  );
}
