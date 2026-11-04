import { Background, Controls, Handle, MiniMap, Position, ReactFlow, type Edge, type Node, type NodeProps } from "@xyflow/react";
import L from "leaflet";
import { useMemo, useState } from "react";
import { CircleMarker, MapContainer, Polyline, Popup, TileLayer, Tooltip as LTooltip } from "react-leaflet";
import { Link, useSearchParams } from "react-router-dom";
import { elementLink } from "../components/domain";
import { useTerms } from "../components/Layout";
import { Badge, Card, KV, Loading, PageHeader, SimTag, Table } from "../components/ui";
import { label } from "../format";
import { useGet } from "../hooks";

const BAD = new Set(["los", "offline", "dying_gasp", "fault", "faulty", "down", "unreachable", "failed"]);
const WARN = new Set(["degraded", "unknown", "registered_inactive", "disabled", "maintenance"]);
const COLOR: Record<string, string> = { olt: "#0b5cab", pon_port: "#1f6feb", splitter: "#8250df", fat: "#bf8700", odf: "#57606a", closure: "#57606a", odb: "#57606a", splice_point: "#57606a", termination: "#57606a", ont: "#1a7f37" };

function NetNode({ data, selected }: NodeProps) {
  const d = data as any;
  const tone = BAD.has(d.status) ? "bad" : WARN.has(d.status) || d.optical_status === "warning" || d.optical_status === "critical" ? "warn" : "";
  return (
    <div className={`flow-node ${tone} ${selected || d.highlight ? "sel" : ""}`} style={{ borderLeft: `4px solid ${COLOR[d.type] || "#999"}` }}>
      <Handle type="target" position={Position.Left} style={{ opacity: 0 }} />
      <div className="t">{d.typeLabel}</div>
      <div><b>{d.label}</b></div>
      {d.subscriber && <div style={{ color: "#57606a" }}>{d.subscriber}</div>}
      {d.status && <div><Badge value={d.status} /></div>}
      {d.capacity && <div style={{ color: "#57606a" }}>{d.capacity.occupied}/{d.capacity.total} ports used</div>}
      <Handle type="source" position={Position.Right} style={{ opacity: 0 }} />
    </div>
  );
}
const nodeTypes = { net: NetNode };

function layout(nodes: any[], edges: any[]) {
  const children: Record<string, string[]> = {};
  const indeg: Record<string, number> = {};
  nodes.forEach((n) => (indeg[n.id] = 0));
  edges.forEach((e) => { (children[e.source] ||= []).push(e.target); indeg[e.target] = (indeg[e.target] || 0) + 1; });
  const depth: Record<string, number> = {};
  const roots = nodes.filter((n) => !indeg[n.id]).map((n) => n.id);
  const q = [...roots];
  roots.forEach((r) => (depth[r] = 0));
  while (q.length) {
    const n = q.shift()!;
    for (const c of children[n] || []) {
      if (depth[c] === undefined || depth[c] < depth[n] + 1) { depth[c] = depth[n] + 1; q.push(c); }
    }
  }
  // order: DFS from roots so subtrees stay together
  const order: string[] = [];
  const seen = new Set<string>();
  const dfs = (n: string) => { if (seen.has(n)) return; seen.add(n); order.push(n); (children[n] || []).forEach(dfs); };
  roots.forEach(dfs);
  nodes.forEach((n) => dfs(n.id));
  const rowAt: Record<number, number> = {};
  const pos: Record<string, { x: number; y: number }> = {};
  const leafY: Record<string, number> = {};
  let nextLeaf = 0;
  // leaves get sequential rows; parents centered on children
  const place = (n: string, visiting = new Set<string>()): number => {
    if (leafY[n] !== undefined) return leafY[n];
    if (visiting.has(n)) return nextLeaf;
    visiting.add(n);
    const kids = (children[n] || []).filter((k) => depth[k] > (depth[n] ?? 0));
    let y: number;
    if (!kids.length) y = nextLeaf++;
    else { const ys = kids.map((k) => place(k, visiting)); y = (Math.min(...ys) + Math.max(...ys)) / 2; }
    leafY[n] = y;
    return y;
  };
  order.forEach((n) => place(n));
  nodes.forEach((n) => { const d = depth[n.id] ?? 0; rowAt[d] = (rowAt[d] || 0) + 1; pos[n.id] = { x: d * 250, y: leafY[n.id] * 78 }; });
  return pos;
}

export function TopologyView() {
  const [sp, setSp] = useSearchParams();
  const t = useTerms();
  const sites = useGet<any[]>("/sites");
  const olts = useGet<any[]>("/olts");
  const f = { site_id: sp.get("site_id") || "", olt_id: sp.get("olt_id") || "", pon_port_id: sp.get("pon_port_id") || "", fat_id: sp.get("fat_id") || "",
    splitter_id: sp.get("splitter_id") || "", status: sp.get("status") || "", q: sp.get("q") || "" };
  const set = (k: string, v: string) => { const n = new URLSearchParams(sp); v ? n.set(k, v) : n.delete(k); setSp(n); };
  const g = useGet<any>("/topology/graph", f, 60000);
  const [sel, setSel] = useState<any>(null);
  const impactType = sel ? ({ olt: "olt", pon_port: "olt_port", splitter: "splitter", fat: "fat" } as any)[sel.type] || (["odf", "odb", "closure", "splice_point", "termination"].includes(sel.type) ? "dpoint" : null) : null;
  const impact = useGet<any>(impactType ? `/impact/${impactType}/${sel.ref_id}` : null);

  const { nodes, edges } = useMemo(() => {
    if (!g.data) return { nodes: [] as Node[], edges: [] as Edge[] };
    const pos = layout(g.data.nodes, g.data.edges);
    const typeLabel: Record<string, string> = { olt: "OLT", pon_port: "PON port", splitter: "Splitter", fat: t.fat, ont: "ONT", odf: "ODF", odb: "ODB", closure: "Closure", splice_point: "Splice", termination: "Termination" };
    const nodes: Node[] = g.data.nodes.map((n: any) => ({ id: n.id, type: "net", position: pos[n.id] || { x: 0, y: 0 }, data: { ...n, typeLabel: typeLabel[n.type] || n.type } }));
    const edges: Edge[] = g.data.edges.map((e: any) => ({
      id: e.id, source: e.source, target: e.target, animated: e.faulty,
      style: { stroke: e.faulty ? "#c1272d" : e.kind === "logical" ? "#9aa5b1" : e.kind === "contains" ? "#0b5cab" : e.verification === "verified" ? "#1a7f37" : e.verification === "planned" ? "#bf8700" : "#d4a72c",
        strokeDasharray: e.kind === "logical" ? "4 4" : e.verification === "planned" ? "8 4" : e.verification === "unverified" ? "2 3" : undefined, strokeWidth: 1.6 },
      label: e.links?.[0]?.cable ? `${e.links[0].cable}${e.links[0].core ? ` #${e.links[0].core}` : ""}` : undefined, labelStyle: { fontSize: 9, fill: "#57606a" },
    }));
    return { nodes, edges };
  }, [g.data, t.fat]);

  return (
    <>
      <PageHeader title="Fiber topology (schematic)" subtitle="OLT → PON port → feeder → splitter → distribution → FAT → drop → ONT → subscriber. Built from recorded connections; independent of any map provider."
        actions={<Link to="/map"><button>Geographic view</button></Link>} />
      <div className="filters">
        <select value={f.site_id} onChange={(e) => set("site_id", e.target.value)}><option value="">All sites</option>{sites.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}</select>
        <select value={f.olt_id} onChange={(e) => set("olt_id", e.target.value)}><option value="">All OLTs</option>{olts.data?.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}</select>
        <select value={f.status} onChange={(e) => set("status", e.target.value)}><option value="">Any ONT status</option><option value="los,offline,dying_gasp">ONTs down</option><option value="unreachable">Unreachable</option><option value="online">Online</option></select>
        <input placeholder="Search element / subscriber" value={f.q} onChange={(e) => set("q", e.target.value)} />
        {(f.pon_port_id || f.fat_id || f.splitter_id) && <button onClick={() => { set("pon_port_id", ""); set("fat_id", ""); set("splitter_id", ""); }}>Clear element focus</button>}
      </div>
      <div className="legend">
        <span><i style={{ background: "#1a7f37" }} />verified</span><span><i style={{ background: "#d4a72c" }} />unverified</span><span><i style={{ background: "#bf8700" }} />planned</span>
        <span><i style={{ background: "#9aa5b1" }} />logical only (no physical path recorded)</span><span><i style={{ background: "#c1272d" }} />faulty</span>
      </div>
      <div className="grid c32">
        <div className="topology">
          {g.isLoading ? <Loading /> : (
            <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView minZoom={0.05} onNodeClick={(_, n) => setSel(n.data)} proOptions={{ hideAttribution: true }}>
              <Background /><Controls /><MiniMap pannable zoomable nodeColor={(n: any) => (BAD.has(n.data.status) ? "#c1272d" : COLOR[n.data.type] || "#999")} />
            </ReactFlow>
          )}
        </div>
        <Card title={sel ? sel.label : "Select an element"}>
          {!sel ? <div className="vendor">Click any node to see its details and which subscribers depend on it.</div> : (
            <>
              <KV rows={[["Type", sel.typeLabel], ["Status", <Badge value={sel.status} />], ...(sel.subscriber ? [["Subscriber", sel.subscriber] as [string, any]] : []),
                ...(sel.rx_power_dbm != null ? [["Rx", `${sel.rx_power_dbm} dBm`] as [string, any]] : []),
                ["Open", elementLink(sel.type === "pon_port" ? "olt_port" : ["odf", "odb", "closure", "splice_point", "termination"].includes(sel.type) ? "dpoint" : sel.type, sel.ref_id, "details →")]]} />
              {impact.data && (
                <>
                  <h3 style={{ margin: "12px 0 6px" }}>Downstream impact</h3>
                  <div className="vendor">{impact.data.summary.onts} ONT(s), {impact.data.summary.subscribers} subscriber(s), {impact.data.summary.confirmed_down} currently down</div>
                  <Table rows={impact.data.subscribers.slice(0, 30)} cols={[{ key: "display_name", title: "Subscriber", render: (s: any) => <Link to={`/subscribers/${s.id}`}>{s.display_name}</Link> }, { key: "evidence", title: "", render: (s: any) => <Badge value={s.evidence} /> }]} />
                </>
              )}
              <div style={{ marginTop: 8 }}>
                {sel.type === "fat" && <button onClick={() => set("fat_id", String(sel.ref_id))}>Focus on this {t.fat}</button>}
                {sel.type === "splitter" && <button onClick={() => set("splitter_id", String(sel.ref_id))}>Focus on this splitter</button>}
                {sel.type === "pon_port" && <button onClick={() => set("pon_port_id", String(sel.ref_id))}>Focus on this port</button>}
              </div>
            </>
          )}
        </Card>
      </div>
    </>
  );
}

// ================================================================= geographic
const KIND_COLOR: Record<string, string> = { site: "#0b5cab", olt: "#0b5cab", splitter: "#8250df", fat: "#bf8700", dpoint: "#57606a", ont: "#1a7f37" };

export function MapView() {
  const q = useGet<any>("/topology/map", undefined, 60000);
  const [offline, setOffline] = useState(false);
  const [kinds, setKinds] = useState<Record<string, boolean>>({ site: true, olt: true, splitter: true, fat: true, dpoint: true, ont: true, cable: true, link: true });
  const t = useTerms();
  if (!q.data) return <Loading />;
  const feats = q.data.features.filter((f: any) => kinds[f.kind]);
  const lines = q.data.lines.filter((l: any) => kinds[l.kind]);
  const useTiles = !!q.data.tile_url && !offline;
  const color = (f: any) => (BAD.has(f.status) ? "#c1272d" : KIND_COLOR[f.kind]);
  return (
    <>
      <PageHeader title="Network map" subtitle={q.data.note} actions={<>
        <Link to="/topology"><button>Schematic view</button></Link>
        <button onClick={() => setOffline((o) => !o)}>{useTiles ? "Use schematic (no map tiles)" : q.data.tile_url ? "Use map tiles" : "No tile server configured"}</button>
      </>} />
      <div className="filters">
        {Object.keys(kinds).map((k) => (
          <label key={k} className="field check" style={{ flexDirection: "row" }}>
            <input type="checkbox" checked={kinds[k]} onChange={(e) => setKinds({ ...kinds, [k]: e.target.checked })} /> {k === "fat" ? t.fats : k === "dpoint" ? "ODF/closures" : k === "link" ? "estimated links" : `${k}s`}
          </label>
        ))}
      </div>
      {feats.length === 0 ? <Card><div className="empty">No elements have GPS coordinates yet.</div></Card> : useTiles ? (
        <div className="map">
          <MapContainer bounds={L.latLngBounds(feats.map((f: any) => [f.lat, f.lng])).pad(0.2)} style={{ height: "100%" }}>
            <TileLayer url={q.data.tile_url} attribution={q.data.attribution} />
            {lines.map((l: any) => <Polyline key={l.id} positions={l.path} pathOptions={{ color: l.status === "faulty" ? "#c1272d" : l.kind === "cable" ? "#1f6feb" : "#8b949e", weight: l.kind === "cable" ? 4 : 2, dashArray: l.estimated ? "5 6" : undefined }}><LTooltip>{l.label} {l.estimated ? "(schematic, not a surveyed route)" : ""}</LTooltip></Polyline>)}
            {feats.map((f: any) => (
              <CircleMarker key={f.id} center={[f.lat, f.lng]} radius={f.kind === "ont" ? 4 : f.kind === "olt" || f.kind === "site" ? 9 : 7} pathOptions={{ color: color(f), fillColor: color(f), fillOpacity: 0.8 }}>
                <Popup><b>{f.label}</b><br />{f.kind} · {f.status || ""}{f.utilization_percent != null && <><br />{f.utilization_percent}% used, {f.available} free</>}<br />
                  {elementLink(f.kind === "dpoint" ? "dpoint" : f.kind, f.ref_id, "Open details")}</Popup>
              </CircleMarker>
            ))}
          </MapContainer>
        </div>
      ) : <SchematicMap feats={feats} lines={lines} color={color} />}
      <SimNote feats={q.data.features} />
    </>
  );
}

function SimNote({ feats }: { feats: any[] }) {
  return feats.some((f) => f.label?.startsWith("SIM")) ? <div className="note sim" style={{ marginTop: 8 }}><SimTag /> Coordinates of simulated elements are illustrative only.</div> : null;
}

/** Map fallback that needs no external service: plots coordinates on an SVG. */
function SchematicMap({ feats, lines, color }: { feats: any[]; lines: any[]; color: (f: any) => string }) {
  const lats = feats.map((f) => f.lat), lngs = feats.map((f) => f.lng);
  const [minLat, maxLat, minLng, maxLng] = [Math.min(...lats), Math.max(...lats), Math.min(...lngs), Math.max(...lngs)];
  const W = 1000, H = 640, pad = 30;
  const sx = (lng: number) => pad + ((lng - minLng) / (maxLng - minLng || 1)) * (W - 2 * pad);
  const sy = (lat: number) => H - pad - ((lat - minLat) / (maxLat - minLat || 1)) * (H - 2 * pad);
  const [hover, setHover] = useState<any>(null);
  return (
    <Card title="Coordinate plot (offline mode — no map provider required)">
      <svg viewBox={`0 0 ${W} ${H}`} style={{ width: "100%", background: "#f8fafc", borderRadius: 6 }}>
        {lines.map((l) => <polyline key={l.id} fill="none" points={l.path.map((p: number[]) => `${sx(p[1])},${sy(p[0])}`).join(" ")} stroke={l.kind === "cable" ? "#1f6feb" : "#b0b8c4"} strokeWidth={l.kind === "cable" ? 3 : 1.2} strokeDasharray={l.estimated ? "4 4" : undefined} />)}
        {feats.map((f) => <circle key={f.id} cx={sx(f.lng)} cy={sy(f.lat)} r={f.kind === "ont" ? 3.5 : 7} fill={color(f)} onMouseEnter={() => setHover(f)} onMouseLeave={() => setHover(null)} />)}
        {hover && <text x={sx(hover.lng) + 10} y={sy(hover.lat) - 8} fontSize="13" fill="#1b2430">{hover.label} ({hover.kind}{hover.status ? `, ${label(hover.status)}` : ""})</text>}
      </svg>
    </Card>
  );
}
