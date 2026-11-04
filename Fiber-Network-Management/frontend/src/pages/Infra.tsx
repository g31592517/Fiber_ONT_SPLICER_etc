import { useEffect, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { del, get, patch, post, put } from "../api";
import { useAuth } from "../auth";
import { elementLink, TracePath, UtilBar } from "../components/domain";
import { useTerms } from "../components/Layout";
import { Badge, Card, ErrorBox, Form, KV, Loading, Modal, PageHeader, SimTag, Table, useToast, type FieldSpec } from "../components/ui";
import { fmtTime, label } from "../format";
import { useGet, useInvalidate } from "../hooks";

const STATUS_OPTS: [string, string][] = [["planned", "Planned"], ["active", "Active"], ["faulty", "Faulty"], ["maintenance", "Maintenance"], ["decommissioned", "Decommissioned"]];

function useSiteOptions(): [any, string][] {
  const s = useGet<any[]>("/sites");
  return (s.data || []).map((x) => [x.id, x.name]);
}

function ImpactCard({ type, id }: { type: string; id: number }) {
  const q = useGet<any>(`/impact/${type}/${id}`);
  const nav = useNavigate();
  if (!q.data) return <Card title="Downstream dependency"><Loading /></Card>;
  const s = q.data.summary;
  return (
    <Card title="Downstream dependency (if this element fails)">
      <KV rows={[["ONTs depending on it", s.onts], ["Currently down", s.confirmed_down], ["Subscribers", s.subscribers], ["Downstream infrastructure", q.data.infrastructure.length]]} />
      {q.data.subscribers.length > 0 && (
        <details style={{ marginTop: 8 }}><summary>Subscribers ({q.data.subscribers.length})</summary>
          <Table rows={q.data.subscribers} onRow={(x: any) => nav(`/subscribers/${x.id}`)} cols={[{ key: "customer_ref", title: "Customer" }, { key: "display_name", title: "Name" }, { key: "service_ref", title: "Service" }, { key: "evidence", title: "Now", render: (x: any) => <Badge value={x.evidence === "confirmed" ? "down" : "not confirmed down"} tone={x.evidence === "confirmed" ? "bad" : "muted"} /> }]} />
        </details>
      )}
    </Card>
  );
}

function FaultButtons({ type, id, status }: { type: string; id: number; status: string }) {
  const { can } = useAuth();
  const inv = useInvalidate();
  const { push } = useToast();
  const nav = useNavigate();
  if (!can("infrastructure.maintenance")) return null;
  return status === "faulty" ? (
    <button onClick={async () => { await post("/infrastructure/fault", { element_type: type, element_id: id, faulty: false, create_incident: false }); push("Marked active", "ok"); inv(); }}>Mark repaired</button>
  ) : (
    <button className="danger" onClick={async () => {
      const note = prompt("Describe the fault (an incident with impact analysis will be opened):");
      if (note === null) return;
      const r = await post("/infrastructure/fault", { element_type: type, element_id: id, note });
      push(`Marked faulty — ${r.impact.summary.onts} dependent ONT(s), ${r.impact.summary.confirmed_down} confirmed down`, "bad");
      if (r.incident_id) nav(`/incidents/${r.incident_id}`);
    }}>Mark faulty…</button>
  );
}

// ================================================================= FATs
export function Fats() {
  const t = useTerms();
  const [sp] = useSearchParams();
  const [nearFull, setNearFull] = useState(!!sp.get("near_full"));
  const q = useGet<any[]>("/fats", { near_full: nearFull || undefined });
  const [create, setCreate] = useState(false);
  const nav = useNavigate();
  const { can } = useAuth();
  const sites = useSiteOptions();
  return (
    <>
      <PageHeader title={t.fats} subtitle={`Passive distribution terminals. ${t.fats} are tracked physically (ports, capacity, connections) — they are never polled.`}
        actions={<>
          <label className="field check" style={{ flexDirection: "row" }}><input type="checkbox" checked={nearFull} onChange={(e) => setNearFull(e.target.checked)} /> Near capacity only</label>
          {can("infrastructure.write") && <button className="primary" onClick={() => setCreate(true)}>Add {t.fat}</button>}
        </>} />
      <Card>
        {!q.data ? <Loading /> : (
          <Table searchable rows={q.data} onRow={(f: any) => nav(`/fats/${f.id}`)} cols={[
            { key: "name", title: t.fat, render: (f: any) => <><b>{f.name}</b> <SimTag on={f.is_simulated} /><div className="vendor">{f.code} · {f.fat_type}</div></> },
            { key: "address", title: "Location", render: (f: any) => <>{f.address}<div className="vendor">{f.latitude != null ? `${f.latitude.toFixed(5)}, ${f.longitude.toFixed(5)}` : "no GPS"}</div></> },
            { key: "capacity", title: "Ports", render: (f: any) => f.capacity_summary.total },
            { key: "occ", title: "Occ / free / res / faulty", render: (f: any) => `${f.capacity_summary.occupied} / ${f.capacity_summary.available} / ${f.capacity_summary.reserved} / ${f.capacity_summary.faulty}` },
            { key: "util", title: "Utilization", sort: (f: any) => f.capacity_summary.utilization_percent, render: (f: any) => <UtilBar pct={f.capacity_summary.utilization_percent} /> },
            { key: "capacity_warning", title: "", render: (f: any) => (f.capacity_warning ? <Badge value="near full" tone="warn" /> : null) },
            { key: "status", title: "Status", render: (f: any) => <Badge value={f.status} /> },
          ]} />
        )}
      </Card>
      {create && (
        <Modal title={`Add ${t.fat}`} onClose={() => setCreate(false)} wide>
          <Form fields={fatFields(sites, t.fat)} initial={{ capacity: 8, fat_type: "pole", status: "active" }} onCancel={() => setCreate(false)}
            onSubmit={async (v) => { const f = await post("/fats", { ...v, site_id: v.site_id ? Number(v.site_id) : null }); nav(`/fats/${f.id}`); }} />
        </Modal>
      )}
    </>
  );
}

function fatFields(sites: [any, string][], term: string): FieldSpec[] {
  return [
    { name: "name", label: `${term} name / reference`, required: true }, { name: "code", label: "Identification number" },
    { name: "fat_type", label: "Type", type: "select", required: true, options: [["pole", "Pole-mounted"], ["wall", "Wall-mounted"], ["pedestal", "Pedestal"], ["underground", "Underground"], ["indoor", "Indoor"], ["other", "Other"]] },
    { name: "capacity", label: "Number of output ports", type: "number", required: true, help: "Configurable per terminal" },
    { name: "site_id", label: "Site", type: "select", options: sites }, { name: "status", label: "Status", type: "select", required: true, options: STATUS_OPTS },
    { name: "address", label: "Physical address" }, { name: "manufacturer", label: "Manufacturer" },
    { name: "latitude", label: "Latitude", type: "number", step: "any" }, { name: "longitude", label: "Longitude", type: "number", step: "any" },
    { name: "installation_date", label: "Installation date", type: "date" }, { name: "description", label: "Description", type: "textarea" }, { name: "notes", label: "Notes", type: "textarea" },
  ];
}

export function FatDetail() {
  const { id } = useParams();
  const t = useTerms();
  const q = useGet<any>(`/fats/${id}`);
  const [edit, setEdit] = useState(false);
  const [port, setPort] = useState<any>(null);
  const { can } = useAuth();
  const inv = useInvalidate();
  const sites = useSiteOptions();
  const nav = useNavigate();
  if (!q.data) return q.error ? <ErrorBox error={q.error} /> : <Loading />;
  const f = q.data;
  const c = f.capacity_summary;
  return (
    <>
      <PageHeader crumbs={[[t.fats, "/fats"], [f.name]]} title={<>{f.name} <SimTag on={f.is_simulated} /></>}
        subtitle={<><Badge value={f.status} /> · {f.fat_type} · {f.address}</>}
        actions={<>
          <Link to={`/topology?fat_id=${f.id}`}><button>Show in topology</button></Link>
          <FaultButtons type="fat" id={f.id} status={f.status} />
          {can("infrastructure.write") && <button onClick={() => setEdit(true)}>Edit</button>}
        </>} />
      {f.capacity_warning && <div className="note warn">This {t.fat} is above the capacity warning level ({c.utilization_percent}% utilized).</div>}
      <div className="stats">
        <div className="stat"><div className="stat-value">{c.total}</div><div className="stat-label">Total ports</div></div>
        <div className="stat info"><div className="stat-value">{c.occupied}</div><div className="stat-label">Occupied</div></div>
        <div className="stat ok"><div className="stat-value">{c.available}</div><div className="stat-label">Available</div></div>
        <div className="stat warn"><div className="stat-value">{c.reserved}</div><div className="stat-label">Reserved</div></div>
        <div className="stat bad"><div className="stat-value">{c.faulty}</div><div className="stat-label">Faulty</div></div>
        <div className="stat"><div className="stat-value">{c.utilization_percent}%</div><div className="stat-label">Utilization</div></div>
      </div>
      <Card title="Ports" actions={<span className="vendor">Click a port to manage it</span>}>
        <div className="port-grid">
          {f.ports.map((p: any) => (
            <div key={p.id} className={`port ${p.effective_status} clickable`} onClick={() => setPort(p)} style={{ cursor: "pointer" }}>
              <div className="num">Port {p.number} <Badge value={p.effective_status} /></div>
              {p.ont ? <><div className="mono">{p.ont.serial_number}</div><div>{p.ont.subscriber || "no subscriber"}</div><Badge value={p.ont.status} /></>
                : p.effective_status === "reserved" ? <div className="vendor">{p.reserved_for}</div> : p.upstream ? <div className="vendor">fed by {p.upstream.a.label}</div> : <div className="vendor">no feed recorded</div>}
            </div>
          ))}
        </div>
      </Card>
      <div className="grid c2">
        <Card title={`Network path (${t.fat} → OLT)`}>
          {f.served_by ? <div style={{ marginBottom: 8 }}>Served by <b>{f.served_by.label}</b></div> : <div className="note warn">No complete upstream path recorded.</div>}
          {f.network_path && <TracePath trace={f.network_path} />}
          {f.splitters.length > 0 && <div style={{ marginTop: 10 }}>Splitters housed here: {f.splitters.map((s: any) => <Link key={s.id} to={`/splitters/${s.id}`} style={{ marginRight: 8 }}>{s.name} ({s.ratio})</Link>)}</div>}
        </Card>
        <ImpactCard type="fat" id={f.id} />
      </div>
      <Card title="Details">
        <KV rows={[["Identification", f.code], ["Manufacturer", f.manufacturer], ["Installed", f.installation_date], ["GPS", f.latitude != null ? `${f.latitude}, ${f.longitude}` : null],
          ["Description", f.description], ["Notes", f.notes], ["Photos", f.photos?.length ? f.photos.map((p: any, i: number) => <a key={i} href={p.url} target="_blank" rel="noreferrer">{p.caption || `photo ${i + 1}`} </a>) : "none"]]} />
      </Card>
      {edit && <Modal title={`Edit ${f.name}`} onClose={() => setEdit(false)} wide>
        <Form fields={[...fatFields(sites, t.fat), { name: "photo_urls", label: "Photo URLs (one per line)", type: "textarea", help: "Links to installation photographs" }]}
          initial={{ ...f, photo_urls: (f.photos || []).map((p: any) => p.url).join("\n") }} onCancel={() => setEdit(false)}
          onSubmit={async (v) => {
            const photos = String(v.photo_urls || "").split("\n").map((s) => s.trim()).filter(Boolean).map((url) => ({ url }));
            const { photo_urls, ...rest } = v;
            await put(`/fats/${f.id}`, { ...rest, site_id: v.site_id ? Number(v.site_id) : null, photos });
            setEdit(false); inv();
          }} /></Modal>}
      {port && <PortModal port={port} onClose={() => { setPort(null); inv(); }} onOnt={(oid) => nav(`/onts/${oid}`)} />}
    </>
  );
}

function PortModal({ port, onClose, onOnt }: { port: any; onClose: () => void; onOnt: (id: number) => void }) {
  const { can } = useAuth();
  const cables = useGet<any[]>("/cables", { cable_type: "drop" });
  return (
    <Modal title={`Port ${port.number}`} onClose={onClose}>
      <KV rows={[["Effective status", <Badge value={port.effective_status} />], ["Administrative status", port.status],
        ["Upstream feed", port.upstream ? `${port.upstream.a.label} (${port.upstream.verification})` : "not recorded"],
        ["Connected device", port.ont ? <a onClick={() => onOnt(port.ont.id)} style={{ cursor: "pointer" }}>{port.ont.serial_number}</a> : "—"],
        ["Subscriber", port.ont?.subscriber], ["Connected since", port.connected_since], ["Reserved for", port.reserved_for], ["Reserved until", port.reserved_until]]} />
      {can("infrastructure.maintenance") && (
        <div style={{ marginTop: 14 }}>
          <Form fields={[
            { name: "status", label: "Administrative status", type: "select", required: true, options: [["available", "Available"], ["reserved", "Reserved"], ["faulty", "Faulty"]], help: "Occupied is derived from live connections" },
            { name: "reserved_for", label: "Reserved for" }, { name: "reserved_until", label: "Reserved until", type: "date" },
            { name: "drop_cable_id", label: "Drop cable", type: "select", options: (cables.data || []).map((c) => [c.id, c.name]) },
            { name: "label", label: "Label" }, { name: "notes", label: "Notes", type: "textarea" },
          ]} initial={{ ...port, status: port.status === "occupied" ? "available" : port.status }} onCancel={onClose}
            onSubmit={async (v) => { await patch(`/infra-ports/${port.id}`, { ...v, drop_cable_id: v.drop_cable_id ? Number(v.drop_cable_id) : null }); onClose(); }} />
        </div>
      )}
    </Modal>
  );
}

// ================================================================= splitters
export function Splitters() {
  const q = useGet<any[]>("/splitters");
  const types = useGet<any[]>("/splitter-types");
  const fats = useGet<any[]>("/fats");
  const sites = useSiteOptions();
  const [create, setCreate] = useState(false);
  const [newType, setNewType] = useState(false);
  const nav = useNavigate();
  const { can } = useAuth();
  const inv = useInvalidate();
  const t = useTerms();
  return (
    <>
      <PageHeader title="Splitters" subtitle={`Passive optical splitters, including cascaded hierarchies. Ratios: ${(types.data || []).map((x) => x.name).join(", ")}`}
        actions={can("infrastructure.write") && <><button onClick={() => setNewType(true)}>Add ratio</button><button className="primary" onClick={() => setCreate(true)}>Add splitter</button></>} />
      <Card>
        {!q.data ? <Loading /> : <Table searchable rows={q.data} onRow={(s: any) => nav(`/splitters/${s.id}`)} cols={[
          { key: "name", title: "Splitter", render: (s: any) => <><b>{s.name}</b> <SimTag on={s.is_simulated} /></> }, { key: "ratio", title: "Ratio" },
          { key: "fat_name", title: `Housed in`, render: (s: any) => s.fat_name || s.location || "—" },
          { key: "outputs", title: "Outputs used", render: (s: any) => `${s.capacity.occupied} / ${s.capacity.total}` },
          { key: "estimated_loss_db", title: "Est. loss", render: (s: any) => (s.estimated_loss_db != null ? `${s.estimated_loss_db} dB` : "—") },
          { key: "status", title: "Status", render: (s: any) => <Badge value={s.status} /> },
        ]} />}
      </Card>
      {create && <Modal title="Add splitter" onClose={() => setCreate(false)} wide>
        <Form fields={[{ name: "name", label: "Reference", required: true }, { name: "splitter_type_id", label: "Ratio", type: "select", required: true, options: (types.data || []).map((x) => [x.id, x.name]) },
          { name: "site_id", label: "Site", type: "select", options: sites }, { name: "fat_id", label: `Housed in ${t.fat}`, type: "select", options: (fats.data || []).map((f) => [f.id, f.name]) },
          { name: "location", label: "Installation location" }, { name: "status", label: "Condition", type: "select", required: true, options: STATUS_OPTS },
          { name: "latitude", label: "Latitude", type: "number", step: "any" }, { name: "longitude", label: "Longitude", type: "number", step: "any" },
          { name: "estimated_loss_db", label: "Estimated loss (dB)", type: "number", step: "any", help: "Defaults to the ratio's nominal loss" }, { name: "notes", label: "Notes", type: "textarea" }]}
          initial={{ status: "active" }} onCancel={() => setCreate(false)}
          onSubmit={async (v) => { const s = await post("/splitters", { ...v, splitter_type_id: Number(v.splitter_type_id), site_id: v.site_id ? Number(v.site_id) : null, fat_id: v.fat_id ? Number(v.fat_id) : null }); nav(`/splitters/${s.id}`); }} />
      </Modal>}
      {newType && <Modal title="Add splitter ratio" onClose={() => setNewType(false)}>
        <Form fields={[{ name: "name", label: "Ratio (e.g. 2:32)", required: true }, { name: "input_ports", label: "Inputs", type: "number", required: true },
          { name: "output_ports", label: "Outputs", type: "number", required: true }, { name: "nominal_loss_db", label: "Nominal loss dB", type: "number", step: "any" },
          { name: "splitter_kind", label: "Kind", type: "select", required: true, options: [["plc", "PLC"], ["fbt", "FBT"]] }]}
          initial={{ input_ports: 1, splitter_kind: "plc" }} onCancel={() => setNewType(false)} onSubmit={async (v) => { await post("/splitter-types", v); setNewType(false); inv(); }} />
      </Modal>}
    </>
  );
}

function PortTable({ ports }: { ports: any[] }) {
  return (
    <Table rows={ports} cols={[
      { key: "direction", title: "Dir" }, { key: "number", title: "#" },
      { key: "effective_status", title: "Status", render: (p: any) => <Badge value={p.effective_status} /> },
      { key: "upstream", title: "Upstream", render: (p: any) => (p.upstream ? <>{p.upstream.a.label} <Badge value={p.upstream.verification} /></> : "—") },
      { key: "downstream", title: "Downstream", render: (p: any) => p.downstream.map((l: any) => <div key={l.id}>{l.b.label} <Badge value={l.verification} /></div>) },
      { key: "cable", title: "Cable / core", render: (p: any) => { const l = p.upstream || p.downstream[0]; return l?.cable ? `${l.cable} #${l.core ?? ""}` : "—"; } },
    ]} />
  );
}

export function SplitterDetail() {
  const { id } = useParams();
  const q = useGet<any>(`/splitters/${id}`);
  if (!q.data) return q.error ? <ErrorBox error={q.error} /> : <Loading />;
  const s = q.data;
  return (
    <>
      <PageHeader crumbs={[["Splitters", "/splitters"], [s.name]]} title={<>{s.name} <SimTag on={s.is_simulated} /></>}
        subtitle={<><Badge value={s.status} /> · ratio {s.ratio} · est. loss {s.estimated_loss_db ?? "—"} dB {s.fat_name && `· in ${s.fat_name}`}</>}
        actions={<><Link to={`/topology?splitter_id=${s.id}`}><button>Show in topology</button></Link><FaultButtons type="splitter" id={s.id} status={s.status} /></>} />
      <div className="grid c2">
        <Card title="Upstream path">{s.upstream_trace ? <TracePath trace={s.upstream_trace} /> : "—"}</Card>
        <ImpactCard type="splitter" id={s.id} />
      </div>
      <Card title="Ports & connections"><PortTable ports={s.ports} /></Card>
    </>
  );
}

// ================================================================= distribution points
export function DPoints() {
  const q = useGet<any[]>("/distribution-points");
  const sites = useSiteOptions();
  const olts = useGet<any[]>("/olts");
  const [create, setCreate] = useState(false);
  const nav = useNavigate();
  const { can } = useAuth();
  return (
    <>
      <PageHeader title="ODF / ODB / closures / termination points" subtitle="Patch, splice and termination points in the fiber path."
        actions={can("infrastructure.write") && <button className="primary" onClick={() => setCreate(true)}>Add</button>} />
      <Card>{!q.data ? <Loading /> : <Table searchable rows={q.data} onRow={(d: any) => nav(`/distribution-points/${d.id}`)} cols={[
        { key: "name", title: "Name", render: (d: any) => <><b>{d.name}</b> <SimTag on={d.is_simulated} /></> }, { key: "point_type", title: "Type", render: (d: any) => d.point_type.toUpperCase() },
        { key: "address", title: "Location" }, { key: "cap", title: "Positions used", render: (d: any) => `${d.capacity.occupied} / ${d.capacity.total}` },
        { key: "status", title: "Status", render: (d: any) => <Badge value={d.status} /> },
      ]} />}</Card>
      {create && <Modal title="Add distribution point" onClose={() => setCreate(false)} wide>
        <Form fields={[{ name: "name", label: "Name", required: true }, { name: "point_type", label: "Type", type: "select", required: true, options: [["odf", "ODF"], ["odb", "ODB"], ["closure", "Splice closure"], ["splice_point", "Splice point"], ["termination", "Termination point"]] },
          { name: "port_count", label: "Positions / ports", type: "number", required: true }, { name: "site_id", label: "Site", type: "select", options: sites },
          { name: "olt_id", label: "Associated OLT", type: "select", options: (olts.data || []).map((o) => [o.id, o.name]) }, { name: "status", label: "Status", type: "select", required: true, options: STATUS_OPTS },
          { name: "address", label: "Address" }, { name: "latitude", label: "Latitude", type: "number", step: "any" }, { name: "longitude", label: "Longitude", type: "number", step: "any" }, { name: "notes", label: "Notes", type: "textarea" }]}
          initial={{ point_type: "odf", port_count: 24, status: "active" }} onCancel={() => setCreate(false)}
          onSubmit={async (v) => { const d = await post("/distribution-points", { ...v, site_id: v.site_id ? Number(v.site_id) : null, olt_id: v.olt_id ? Number(v.olt_id) : null }); nav(`/distribution-points/${d.id}`); }} />
      </Modal>}
    </>
  );
}

export function DPointDetail() {
  const { id } = useParams();
  const q = useGet<any>(`/distribution-points/${id}`);
  if (!q.data) return q.error ? <ErrorBox error={q.error} /> : <Loading />;
  const d = q.data;
  return (
    <>
      <PageHeader crumbs={[["ODF / termination", "/distribution-points"], [d.name]]} title={<>{d.name} <SimTag on={d.is_simulated} /></>}
        subtitle={<><Badge value={d.status} /> · {d.point_type.toUpperCase()} · {d.address}</>} actions={<FaultButtons type="dpoint" id={d.id} status={d.status} />} />
      <ImpactCard type="dpoint" id={d.id} />
      <Card title="Positions"><PortTable ports={d.ports.filter((p: any) => p.upstream || p.downstream.length)} /><div className="vendor">{d.ports.length} positions total; unused positions hidden.</div></Card>
    </>
  );
}

// ================================================================= cables
export function Cables() {
  const q = useGet<any[]>("/cables");
  const sites = useSiteOptions();
  const [create, setCreate] = useState(false);
  const nav = useNavigate();
  const { can } = useAuth();
  return (
    <>
      <PageHeader title="Fiber cables" subtitle="Feeder, distribution, drop and backbone cables with per-core usage."
        actions={can("infrastructure.write") && <button className="primary" onClick={() => setCreate(true)}>Add cable</button>} />
      <Card>{!q.data ? <Loading /> : <Table searchable rows={q.data} onRow={(c: any) => nav(`/cables/${c.id}`)} cols={[
        { key: "name", title: "Cable", render: (c: any) => <><b>{c.name}</b> <SimTag on={c.is_simulated} /></> }, { key: "cable_type", title: "Type" },
        { key: "fiber_count", title: "Fibers" }, { key: "route", title: "From → to", render: (c: any) => `${c.from_description || "?"} → ${c.to_description || "?"}` },
        { key: "length_m", title: "Length", render: (c: any) => (c.length_m ? `${c.length_m} m` : "—") },
        { key: "util", title: "Cores used", sort: (c: any) => c.utilization_percent, render: (c: any) => <UtilBar pct={c.utilization_percent} /> },
        { key: "status", title: "Status", render: (c: any) => <Badge value={c.status} /> },
      ]} />}</Card>
      {create && <Modal title="Add cable" onClose={() => setCreate(false)} wide>
        <Form fields={[{ name: "name", label: "Name", required: true }, { name: "cable_type", label: "Type", type: "select", required: true, options: [["feeder", "Main feeder"], ["distribution", "Distribution"], ["drop", "Drop"], ["backbone", "Backbone"], ["patch", "Patch"]] },
          { name: "fiber_count", label: "Fiber count", type: "number", required: true }, { name: "site_id", label: "Site", type: "select", options: sites },
          { name: "from_description", label: "From" }, { name: "to_description", label: "To" }, { name: "length_m", label: "Length (m)", type: "number" },
          { name: "parent_cable_id", label: "Segment of cable", type: "select", options: (q.data || []).map((c) => [c.id, c.name]) },
          { name: "status", label: "Status", type: "select", required: true, options: STATUS_OPTS }, { name: "notes", label: "Notes", type: "textarea" }]}
          initial={{ cable_type: "distribution", fiber_count: 12, status: "active" }} onCancel={() => setCreate(false)}
          onSubmit={async (v) => { const c = await post("/cables", { ...v, site_id: v.site_id ? Number(v.site_id) : null, parent_cable_id: v.parent_cable_id ? Number(v.parent_cable_id) : null }); nav(`/cables/${c.id}`); }} />
      </Modal>}
    </>
  );
}

export function CableDetail() {
  const { id } = useParams();
  const q = useGet<any>(`/cables/${id}`);
  const { can } = useAuth();
  const inv = useInvalidate();
  if (!q.data) return q.error ? <ErrorBox error={q.error} /> : <Loading />;
  const c = q.data;
  return (
    <>
      <PageHeader crumbs={[["Cables", "/cables"], [c.name]]} title={<>{c.name} <SimTag on={c.is_simulated} /></>}
        subtitle={<><Badge value={c.status} /> · {c.cable_type} · {c.fiber_count}F · {c.length_m ?? "?"} m · {c.from_description} → {c.to_description}</>}
        actions={<FaultButtons type="cable" id={c.id} status={c.status} />} />
      <div className="grid c2">
        <Card title="Core usage"><KV rows={Object.entries(c.core_usage).map(([k, v]) => [label(k), String(v)])} />
          {c.route && <div className="vendor" style={{ marginTop: 8 }}>Recorded GIS route with {c.route.length} points.</div>}
          {c.segments.length > 0 && <div>Segments: {c.segments.map((s: any) => <Link key={s.id} to={`/cables/${s.id}`}>{s.name} </Link>)}</div>}</Card>
        <ImpactCard type="cable" id={c.id} />
      </div>
      <Card title="Fiber cores">
        <Table rows={c.cores} cols={[
          { key: "number", title: "Core" }, { key: "tube", title: "Tube" }, { key: "color", title: "Color", render: (x: any) => <><span style={{ display: "inline-block", width: 10, height: 10, borderRadius: 5, background: x.color === "slate" ? "#708090" : x.color === "rose" ? "#ff66cc" : x.color, border: "1px solid #ccc" }} /> {x.color}</> },
          { key: "effective_status", title: "Status", render: (x: any) => <Badge value={x.effective_status} tone={x.effective_status === "used" ? "info" : x.effective_status === "damaged" ? "bad" : undefined} /> },
          { key: "link", title: "Carries", render: (x: any) => (x.link ? `${x.link.a.label} → ${x.link.b.label}` : "—") },
          { key: "act", title: "", render: (x: any) => can("infrastructure.maintenance") && x.effective_status !== "used" ? (
            <select value="" onChange={async (e) => { await patch(`/fiber-cores/${x.id}`, { status: e.target.value }); inv(); }}>
              <option value="">set…</option><option value="free">free</option><option value="reserved">reserved</option><option value="damaged">damaged</option>
            </select>) : null },
        ]} />
      </Card>
    </>
  );
}

// ================================================================= connections
type EP = { type: string; id: number | null };

function EndpointPicker({ side, value, onChange }: { side: "a" | "b"; value: EP; onChange: (e: EP) => void }) {
  const t = useTerms();
  const [kind, setKind] = useState(side === "a" ? "olt" : "splitter");
  const [elem, setElem] = useState<string>("");
  const [ports, setPorts] = useState<any[]>([]);
  const [ontQ, setOntQ] = useState("");
  const [onts, setOnts] = useState<any[]>([]);
  const olts = useGet<any[]>("/olts");
  const splitters = useGet<any[]>("/splitters");
  const fats = useGet<any[]>("/fats");
  const dps = useGet<any[]>("/distribution-points");
  useEffect(() => {
    setPorts([]);
    onChange({ type: "", id: null });
    if (!elem) return;
    if (kind === "olt") get("/pon-ports", { olt_id: elem }).then((ps) => setPorts(ps.map((p: any) => ({ id: p.id, label: `PON ${p.fsp}`, type: "olt_port" }))));
    else {
      const url = { splitter: `/splitters/${elem}`, fat: `/fats/${elem}`, dpoint: `/distribution-points/${elem}` }[kind as "splitter"];
      get(url).then((x) => setPorts(x.ports.filter((p: any) => (side === "a" ? p.direction !== "in" : p.direction !== "out"))
        .map((p: any) => ({ id: p.id, type: "infra_port", label: `${p.direction.toUpperCase()} ${p.number} — ${p.effective_status}` }))));
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [kind, elem]);
  useEffect(() => {
    if (kind === "ont" && ontQ.length >= 3) get("/onts", { q: ontQ, limit: 10, registration: "all" }).then((r) => setOnts(r.items));
  }, [kind, ontQ]);
  const kinds: [string, string][] = side === "a" ? [["olt", "OLT PON port"], ["splitter", "Splitter output"], ["fat", `${t.fat} port`], ["dpoint", "ODF/closure position"]]
    : [["splitter", "Splitter input"], ["fat", `${t.fat} port`], ["dpoint", "ODF/closure position"], ["ont", "ONT"]];
  const elems = kind === "olt" ? olts.data : kind === "splitter" ? splitters.data : kind === "fat" ? fats.data : dps.data;
  return (
    <div className="form-grid">
      <label className="field"><span>{side === "a" ? "Upstream (A) type" : "Downstream (B) type"}</span>
        <select value={kind} onChange={(e) => { setKind(e.target.value); setElem(""); }}>{kinds.map(([k, l]) => <option key={k} value={k}>{l}</option>)}</select></label>
      {kind === "ont" ? (
        <label className="field"><span>ONT serial</span><input value={ontQ} onChange={(e) => setOntQ(e.target.value)} placeholder="type ≥3 chars" />
          {onts.length > 0 && <select value={value.id ?? ""} onChange={(e) => onChange({ type: "ont", id: Number(e.target.value) })}><option value="">—</option>{onts.map((o) => <option key={o.id} value={o.id}>{o.serial_number} {o.fat ? "(already connected)" : ""}</option>)}</select>}</label>
      ) : (
        <>
          <label className="field"><span>Element</span><select value={elem} onChange={(e) => setElem(e.target.value)}><option value="">—</option>{elems?.map((x: any) => <option key={x.id} value={x.id}>{x.name}</option>)}</select></label>
          <label className="field"><span>Port</span><select value={value.id ?? ""} onChange={(e) => { const p = ports.find((x) => String(x.id) === e.target.value); onChange({ type: p?.type || "", id: p ? p.id : null }); }}><option value="">—</option>{ports.map((p) => <option key={p.id} value={p.id}>{p.label}</option>)}</select></label>
        </>
      )}
    </div>
  );
}

export function Links() {
  const [verification, setVerification] = useState("");
  const q = useGet<any[]>("/links", { verification });
  const cables = useGet<any[]>("/cables");
  const { can } = useAuth();
  const inv = useInvalidate();
  const [create, setCreate] = useState(false);
  const [a, setA] = useState<EP>({ type: "", id: null });
  const [b, setB] = useState<EP>({ type: "", id: null });
  const [meta, setMeta] = useState<any>({ link_type: "distribution", verification: "unverified" });
  const [cores, setCores] = useState<any[]>([]);
  const [check, setCheck] = useState<any>(null);
  const [err, setErr] = useState<any>(null);
  useEffect(() => { if (meta.cable_id) get(`/cables/${meta.cable_id}`).then((c) => setCores(c.cores)); else setCores([]); }, [meta.cable_id]);
  const body = () => ({ a_type: a.type, a_id: a.id, b_type: b.type, b_id: b.id, ...meta, cable_id: meta.cable_id ? Number(meta.cable_id) : null,
    core_id: meta.core_id ? Number(meta.core_id) : null, length_m: meta.length_m ? Number(meta.length_m) : null, loss_db: meta.loss_db ? Number(meta.loss_db) : null });
  return (
    <>
      <PageHeader title="Fiber connections" subtitle="Directed physical links (upstream → downstream). Each link records whether it is verified, unverified or only planned."
        actions={<>
          <select value={verification} onChange={(e) => setVerification(e.target.value)}><option value="">All</option><option value="verified">Verified</option><option value="unverified">Unverified</option><option value="planned">Planned</option></select>
          {can("infrastructure.write") && <button className="primary" onClick={() => setCreate(true)}>Add connection</button>}
        </>} />
      <Card>{!q.data ? <Loading /> : <Table searchable rows={q.data} cols={[
        { key: "a", title: "Upstream (A)", render: (l: any) => elementLink(l.a.element_type === "olt" ? "olt_port" : l.a.element_type, l.a.element_type === "olt" ? l.a.id : l.a.element_id, l.a.label) },
        { key: "b", title: "Downstream (B)", render: (l: any) => elementLink(l.b.element_type, l.b.element_id, l.b.label) },
        { key: "link_type", title: "Type" }, { key: "cable", title: "Cable / core", render: (l: any) => (l.cable ? `${l.cable} #${l.core ?? "—"}` : "—") },
        { key: "verification", title: "Verification", render: (l: any) => <Badge value={l.verification} /> }, { key: "status", title: "Status", render: (l: any) => <Badge value={l.status} /> },
        { key: "length_m", title: "Length", render: (l: any) => (l.length_m ? `${l.length_m} m` : "—") },
        { key: "act", title: "", render: (l: any) => can("infrastructure.maintenance") && (
          <span style={{ display: "flex", gap: 4 }}>
            {l.verification !== "verified" && <button className="small" onClick={async () => { await patch(`/links/${l.id}`, { verification: "verified" }); inv(); }}>Verify</button>}
            {can("infrastructure.write") && <button className="small" onClick={async () => { if (confirm("Remove this connection?")) { await del(`/links/${l.id}`); inv(); } }}>Remove</button>}
          </span>) },
      ]} />}</Card>
      {create && <Modal title="Add fiber connection" onClose={() => setCreate(false)} wide>
        <h3>Upstream end (towards the OLT)</h3><EndpointPicker side="a" value={a} onChange={setA} />
        <h3 style={{ marginTop: 12 }}>Downstream end</h3><EndpointPicker side="b" value={b} onChange={setB} />
        <div className="form-grid" style={{ marginTop: 12 }}>
          <label className="field"><span>Link type</span><select value={meta.link_type} onChange={(e) => setMeta({ ...meta, link_type: e.target.value })}>{["feeder", "distribution", "drop", "patch", "splice"].map((x) => <option key={x}>{x}</option>)}</select></label>
          <label className="field"><span>Verification</span><select value={meta.verification} onChange={(e) => setMeta({ ...meta, verification: e.target.value })}><option value="verified">Verified in the field</option><option value="unverified">Recorded, not verified</option><option value="planned">Planned only</option></select></label>
          <label className="field"><span>Cable</span><select value={meta.cable_id || ""} onChange={(e) => setMeta({ ...meta, cable_id: e.target.value, core_id: "" })}><option value="">—</option>{cables.data?.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}</select></label>
          <label className="field"><span>Core</span><select value={meta.core_id || ""} onChange={(e) => setMeta({ ...meta, core_id: e.target.value })} disabled={!cores.length}><option value="">—</option>{cores.map((c) => <option key={c.id} value={c.id} disabled={c.effective_status !== "free"}>#{c.number} {c.color} {c.effective_status !== "free" ? `(${c.effective_status})` : ""}</option>)}</select></label>
          <label className="field"><span>Length (m)</span><input type="number" value={meta.length_m || ""} onChange={(e) => setMeta({ ...meta, length_m: e.target.value })} /></label>
          <label className="field"><span>Measured loss (dB)</span><input type="number" step="any" value={meta.loss_db || ""} onChange={(e) => setMeta({ ...meta, loss_db: e.target.value })} /></label>
          <label className="field check"><span>Allow use of a reserved port</span><input type="checkbox" checked={!!meta.allow_reserved} onChange={(e) => setMeta({ ...meta, allow_reserved: e.target.checked })} /></label>
        </div>
        {check && (check.valid ? <div className="note">Connection is valid.</div> : <div className="error-box">Invalid: <ul>{check.errors.map((x: string) => <li key={x}>{x}</li>)}</ul></div>)}
        <ErrorBox error={err} />
        <div className="form-actions">
          <button disabled={!a.id || !b.id} onClick={async () => setCheck(await post("/links/validate", body()))}>Validate</button>
          <button className="primary" disabled={!a.id || !b.id} onClick={async () => { setErr(null); try { await post("/links", body()); setCreate(false); setCheck(null); inv(); } catch (e) { setErr(e); } }}>Create connection</button>
        </div>
      </Modal>}
    </>
  );
}
