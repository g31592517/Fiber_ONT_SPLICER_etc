// Domain-specific widgets shared across pages.
import { Link } from "react-router-dom";
import { CartesianGrid, Legend, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { fmtTime } from "../format";
import { Badge } from "./ui";

export function elementLink(type?: string, id?: number, text?: string) {
  if (!type || !id) return text || "—";
  const to = { olt: `/olts/${id}`, olt_port: `/pon-ports/${id}`, ont: `/onts/${id}`, fat: `/fats/${id}`, splitter: `/splitters/${id}`,
    dpoint: `/distribution-points/${id}`, cable: `/cables/${id}` }[type];
  return to ? <Link to={to}>{text || `${type} ${id}`}</Link> : text || `${type} ${id}`;
}

/** Renders an upstream trace (ONT → … → OLT) with per-link verification. */
export function TracePath({ trace }: { trace: any }) {
  if (!trace) return null;
  const hops = [...trace.hops].reverse();
  const links = [...trace.links].reverse();
  return (
    <div>
      <div className="path">
        {hops.map((h: any, i: number) => {
          const link = i > 0 ? links[i - 1] : null;
          return (
            <span key={i} style={{ display: "contents" }}>
              {i > 0 && (
                <span className={`arrow ${link?.verification || ""}`} title={link ? `${link.link_type} ${link.verification}` : "internal"}>
                  {link ? <>→<small>{link.cable ? `${link.cable}${link.core ? ` #${link.core}` : ""}` : link.link_type}</small></> : <>⇢<small>internal</small></>}
                </span>
              )}
              <span className="hop">
                {elementLink(h.element_type === "olt" ? "olt_port" : h.element_type, h.element_type === "olt" ? h.id : h.element_id, h.label)}
                {h.status && <small>{h.status}</small>}
              </span>
            </span>
          );
        })}
      </div>
      <div style={{ marginTop: 8, display: "flex", gap: 8 }}>
        {trace.complete ? <Badge value="complete path" tone="ok" /> : <Badge value="incomplete path" tone="warn" />}
        {trace.complete && (trace.all_verified ? <Badge value="all connections verified" tone="ok" /> : <Badge value="contains unverified / planned links" tone="warn" />)}
      </div>
    </div>
  );
}

export function UtilBar({ pct, warnAt = 80 }: { pct: number; warnAt?: number }) {
  const cls = pct >= 95 ? "bad" : pct >= warnAt ? "warn" : "";
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "center" }}>
      <div className={`util ${cls}`} style={{ flex: 1 }}>
        <div style={{ width: `${Math.min(100, pct)}%` }} />
      </div>
      <span style={{ fontSize: 12, minWidth: 40 }}>{pct}%</span>
    </div>
  );
}

export function OpticalChart({ readings, threshold }: { readings: any[]; threshold?: any }) {
  if (!readings?.length) return <div className="empty">No optical readings recorded.</div>;
  const data = readings.map((r) => ({ t: new Date(r.ts).getTime(), rx: r.rx_power_dbm, olt_rx: r.olt_rx_power_dbm }));
  return (
    <ResponsiveContainer width="100%" height={260}>
      <LineChart data={data} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eef1f6" />
        <XAxis dataKey="t" type="number" domain={["dataMin", "dataMax"]} scale="time"
          tickFormatter={(v) => new Date(v).toLocaleDateString(undefined, { month: "short", day: "2-digit" })} fontSize={11} />
        <YAxis domain={["auto", "auto"]} fontSize={11} unit=" dBm" width={70} />
        <Tooltip labelFormatter={(v) => fmtTime(new Date(v as number).toISOString())} formatter={(v: any) => `${v} dBm`} />
        <Legend />
        {threshold && <ReferenceLine y={threshold.rx_warning_low} stroke="#d4a72c" strokeDasharray="4 4" label={{ value: "warn", fontSize: 10 }} />}
        {threshold && <ReferenceLine y={threshold.rx_critical_low} stroke="#c1272d" strokeDasharray="4 4" label={{ value: "crit", fontSize: 10 }} />}
        <Line type="monotone" dataKey="rx" name="ONT Rx" stroke="#1f6feb" dot={false} strokeWidth={2} connectNulls={false} />
        <Line type="monotone" dataKey="olt_rx" name="OLT Rx (from ONT)" stroke="#8250df" dot={false} strokeWidth={1.5} connectNulls={false} />
      </LineChart>
    </ResponsiveContainer>
  );
}

export function SeriesChart({ data, lines, height = 220 }: { data: any[]; lines: [string, string, string][]; height?: number }) {
  if (!data?.length) return <div className="empty">No historical samples yet.</div>;
  const d = data.map((r) => ({ ...r, t: new Date(r.ts).getTime() }));
  return (
    <ResponsiveContainer width="100%" height={height}>
      <LineChart data={d} margin={{ top: 10, right: 20, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="#eef1f6" />
        <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} fontSize={11}
          tickFormatter={(v) => new Date(v).toLocaleTimeString(undefined, { hour: "2-digit", minute: "2-digit" })} />
        <YAxis fontSize={11} width={50} />
        <Tooltip labelFormatter={(v) => fmtTime(new Date(v as number).toISOString())} />
        <Legend />
        {lines.map(([k, n, c]) => (
          <Line key={k} type="monotone" dataKey={k} name={n} stroke={c} dot={false} strokeWidth={2} />
        ))}
      </LineChart>
    </ResponsiveContainer>
  );
}

export function OpticalValue({ v, status }: { v?: number | null; status?: string | null }) {
  if (v === null || v === undefined) return <span className="unsupported">no reading</span>;
  const tone = status === "critical" ? "bad" : status === "warning" ? "warn" : "";
  return <span className={`mono ${tone ? `badge ${tone}` : ""}`}>{v.toFixed(2)} dBm</span>;
}
