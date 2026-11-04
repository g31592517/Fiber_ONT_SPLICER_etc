export function fmtTime(s?: string | null): string {
  if (!s) return "—";
  const d = new Date(s);
  if (isNaN(d.getTime())) return s;
  return d.toLocaleString(undefined, { year: "numeric", month: "short", day: "2-digit", hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

export function ago(s?: string | null): string {
  if (!s) return "never";
  const sec = Math.round((Date.now() - new Date(s).getTime()) / 1000);
  if (sec < 0) {
    const f = -sec;
    return f < 60 ? `in ${f}s` : f < 3600 ? `in ${Math.floor(f / 60)}m` : `in ${Math.floor(f / 3600)}h`;
  }
  if (sec < 60) return `${sec}s ago`;
  if (sec < 3600) return `${Math.floor(sec / 60)}m ago`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}h ago`;
  return `${Math.floor(sec / 86400)}d ago`;
}

export const dbm = (v?: number | null) => (v === null || v === undefined ? "—" : `${v.toFixed(2)} dBm`);
export const num = (v?: number | null, unit = "", digits = 1) =>
  v === null || v === undefined ? "unsupported / n.a." : `${Number(v).toFixed(digits)}${unit}`;
export const mbps = (kbps?: number) => (kbps ? `${Math.round(kbps / 1024)} Mbps` : "—");

export function uptime(sec?: number | null) {
  if (sec === null || sec === undefined) return "—";
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600), m = Math.floor((sec % 3600) / 60);
  return `${d}d ${h}h ${m}m`;
}

export const label = (s?: string | null) => (s ? s.replace(/_/g, " ") : "—");
