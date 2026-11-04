import { createContext, useContext, useState, type ReactNode } from "react";
import { NavLink, useLocation, useNavigate } from "react-router-dom";
import { get } from "../api";
import { useAuth } from "../auth";
import { useGet } from "../hooks";

type Terms = { fat: string; fats: string };
const TermsCtx = createContext<Terms>({ fat: "FAT", fats: "FATs" });
export const useTerms = () => useContext(TermsCtx);

const NAV: { group: string; items: [string, string, string?][] }[] = [
  { group: "Overview", items: [["/", "Network dashboard"], ["/incidents", "Active incidents"], ["/alarms", "Alarm management"]] },
  {
    group: "Infrastructure",
    items: [["/sites", "Network sites"], ["/olts", "OLTs"], ["/pon-ports", "PON ports"], ["/splitters", "Splitters"], ["/fats", "__FATS__"],
      ["/cables", "Fiber cables"], ["/distribution-points", "ODF / termination"], ["/links", "Fiber connections"], ["/topology", "Fiber topology"],
      ["/map", "Network map"]],
  },
  {
    group: "ONT management",
    items: [["/onts", "ONT inventory"], ["/discovered", "Discovered ONTs"], ["/onts?status=offline,los,dying_gasp,unreachable", "Offline ONTs"],
      ["/optical", "Optical monitoring"], ["/subscribers", "Subscribers & services"]],
  },
  {
    group: "Provisioning",
    items: [["/provisioning/jobs", "Provisioning jobs"], ["/provisioning/templates", "Service templates"],
      ["/provisioning/bandwidth", "Bandwidth profiles"], ["/provisioning/vlans", "VLAN configuration"], ["/provisioning/vendor-profiles", "Line / service / DBA profiles"]],
  },
  { group: "Monitoring", items: [["/monitoring/thresholds", "Optical thresholds"], ["/monitoring/jobs", "Background jobs"]] },
  { group: "Integrations", items: [["/integrations", "Vendor integrations"], ["/integrations/health", "Integration health"], ["/simulator", "Simulator control", "simulator.control"]] },
  {
    group: "Administration",
    items: [["/admin/users", "Users", "users.manage"], ["/admin/roles", "Roles & permissions", "users.manage"], ["/admin/audit", "Audit logs", "audit.read"],
      ["/admin/settings", "Application settings"]],
  },
];

export default function Layout({ children }: { children: ReactNode }) {
  const { user, logout, can } = useAuth();
  const [open, setOpen] = useState(false);
  const nav = useNavigate();
  const loc = useLocation();
  const settings = useGet("/settings");
  const fat = settings.data?.["terminology.fat"]?.value || "FAT";
  const terms = { fat, fats: fat.endsWith("s") ? fat : `${fat}s` };
  const dash = useGet<any>("/dashboard", undefined, 30000);
  const [q, setQ] = useState("");
  const [hits, setHits] = useState<any[]>([]);

  async function search(v: string) {
    setQ(v);
    if (v.length < 2) return setHits([]);
    try {
      setHits(await get("/topology/search", { q: v }));
    } catch {
      setHits([]);
    }
  }
  const go = (h: any) => {
    const path = { olt: `/olts/${h.id}`, ont: `/onts/${h.id}`, fat: `/fats/${h.id}`, splitter: `/splitters/${h.id}`, cable: `/cables/${h.id}`,
      subscriber: `/subscribers/${h.id}` }[h.type as string];
    setQ("");
    setHits([]);
    if (path) nav(path);
  };

  return (
    <TermsCtx.Provider value={terms}>
      <div className={`shell ${open ? "nav-open" : ""}`}>
        <aside className="sidebar">
          <div className="brand">
            <svg viewBox="0 0 32 32" width="26" height="26"><rect width="32" height="32" rx="7" fill="#2f81f7" /><circle cx="9" cy="16" r="4" fill="#fff" /><path d="M13 16h6M19 16l5-6M19 16h6M19 16l5 6" stroke="#fff" strokeWidth="2.2" strokeLinecap="round" /></svg>
            <div>
              <div className="brand-name">Fiber NMS</div>
              <div className="brand-sub">OLT · ONT · FAT · Topology</div>
            </div>
          </div>
          <nav onClick={() => setOpen(false)}>
            {NAV.map((g) => (
              <div key={g.group} className="nav-group">
                <div className="nav-title">{g.group}</div>
                {g.items.filter(([, , perm]) => !perm || can(perm)).map(([to, t]) => (
                  <NavLink key={to} to={to} end={to === "/"} className={({ isActive }) => {
                    const [p, q] = to.split("?");
                    const active = q ? loc.pathname === p && loc.search === `?${q}` : isActive && !(p === "/onts" && loc.search.includes("status="));
                    return active ? "active" : "";
                  }}>
                    {t === "__FATS__" ? `${terms.fats}` : t}
                    {to === "/incidents" && dash.data?.incidents.active > 0 && <span className="pill bad">{dash.data.incidents.active}</span>}
                    {to === "/alarms" && dash.data?.alarms.active > 0 && <span className="pill">{dash.data.alarms.active}</span>}
                    {to === "/discovered" && dash.data?.onts.discovered_unregistered > 0 && <span className="pill info">{dash.data.onts.discovered_unregistered}</span>}
                  </NavLink>
                ))}
              </div>
            ))}
          </nav>
        </aside>
        <div className="main">
          <header className="topbar">
            <button className="icon burger" onClick={() => setOpen((o) => !o)} aria-label="Menu">☰</button>
            <div className="global-search">
              <input placeholder="Search OLT, ONT serial, FAT, splitter, cable, subscriber…" value={q} onChange={(e) => search(e.target.value)} />
              {hits.length > 0 && (
                <div className="search-hits">
                  {hits.map((h) => (
                    <button key={`${h.type}${h.id}`} onClick={() => go(h)}>
                      <span className="badge muted">{h.type === "fat" ? terms.fat : h.type}</span> {h.label} <small>{h.sub}</small>
                    </button>
                  ))}
                </div>
              )}
            </div>
            {dash.data?.olts.simulated > 0 && <span className="badge sim" title="One or more OLTs are simulated">SIMULATION DATA PRESENT</span>}
            <div className="user">
              <span>
                {user?.username} <small>({user?.role})</small>
              </span>
              <button onClick={() => logout()}>Sign out</button>
            </div>
          </header>
          <main className="content">{children}</main>
        </div>
      </div>
    </TermsCtx.Provider>
  );
}
