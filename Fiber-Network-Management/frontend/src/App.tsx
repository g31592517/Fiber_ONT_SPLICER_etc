import { useState } from "react";
import { Route, Routes } from "react-router-dom";
import { useAuth } from "./auth";
import Layout from "./components/Layout";
import { ErrorBox, Loading } from "./components/ui";
import * as Admin from "./pages/Admin";
import * as Infra from "./pages/Infra";
import * as Olts from "./pages/Olts";
import * as Onts from "./pages/Onts";
import * as Overview from "./pages/Overview";
import * as Prov from "./pages/Provisioning";
import * as Topo from "./pages/Topology";

function Login() {
  const { login } = useAuth();
  const [u, setU] = useState("");
  const [p, setP] = useState("");
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  return (
    <div className="login">
      <form
        onSubmit={async (e) => {
          e.preventDefault();
          setBusy(true);
          setErr(null);
          try {
            await login(u, p);
          } catch (ex) {
            setErr(ex);
          } finally {
            setBusy(false);
          }
        }}
      >
        <h1>Fiber Network Management</h1>
        <div className="subtitle">OLT · ONT · FAT · fiber topology · monitoring · provisioning</div>
        <label className="field">
          <span>Username</span>
          <input value={u} onChange={(e) => setU(e.target.value)} autoFocus autoComplete="username" />
        </label>
        <label className="field">
          <span>Password</span>
          <input type="password" value={p} onChange={(e) => setP(e.target.value)} autoComplete="current-password" />
        </label>
        <ErrorBox error={err} />
        <button className="primary" disabled={busy || !u || !p}>{busy ? "Signing in…" : "Sign in"}</button>
      </form>
    </div>
  );
}

export default function App() {
  const { user, ready } = useAuth();
  if (!ready) return <Loading />;
  if (!user) return <Login />;
  return (
    <Layout>
      <Routes>
        <Route path="/" element={<Overview.Dashboard />} />
        <Route path="/incidents" element={<Overview.Incidents />} />
        <Route path="/incidents/:id" element={<Overview.IncidentDetail />} />
        <Route path="/alarms" element={<Overview.Alarms />} />
        <Route path="/sites" element={<Olts.Sites />} />
        <Route path="/olts" element={<Olts.OltList />} />
        <Route path="/olts/:id" element={<Olts.OltDetail />} />
        <Route path="/pon-ports" element={<Olts.PonPorts />} />
        <Route path="/pon-ports/:id" element={<Olts.PonPortDetail />} />
        <Route path="/onts" element={<Onts.OntList />} />
        <Route path="/onts/:id" element={<Onts.OntDetail />} />
        <Route path="/discovered" element={<Onts.Discovered />} />
        <Route path="/optical" element={<Onts.Optical />} />
        <Route path="/fats" element={<Infra.Fats />} />
        <Route path="/fats/:id" element={<Infra.FatDetail />} />
        <Route path="/splitters" element={<Infra.Splitters />} />
        <Route path="/splitters/:id" element={<Infra.SplitterDetail />} />
        <Route path="/cables" element={<Infra.Cables />} />
        <Route path="/cables/:id" element={<Infra.CableDetail />} />
        <Route path="/distribution-points" element={<Infra.DPoints />} />
        <Route path="/distribution-points/:id" element={<Infra.DPointDetail />} />
        <Route path="/links" element={<Infra.Links />} />
        <Route path="/topology" element={<Topo.TopologyView />} />
        <Route path="/map" element={<Topo.MapView />} />
        <Route path="/subscribers" element={<Prov.Subscribers />} />
        <Route path="/subscribers/:id" element={<Prov.SubscriberDetail />} />
        <Route path="/provisioning/jobs" element={<Prov.Jobs />} />
        <Route path="/provisioning/jobs/:id" element={<Prov.JobDetail />} />
        <Route path="/provisioning/templates" element={<Prov.Templates />} />
        <Route path="/provisioning/bandwidth" element={<Prov.Bandwidth />} />
        <Route path="/provisioning/vlans" element={<Prov.Vlans />} />
        <Route path="/provisioning/vendor-profiles" element={<Prov.VendorProfiles />} />
        <Route path="/monitoring/thresholds" element={<Admin.Thresholds />} />
        <Route path="/monitoring/jobs" element={<Admin.BackgroundJobs />} />
        <Route path="/integrations" element={<Admin.Integrations />} />
        <Route path="/integrations/health" element={<Admin.IntegrationHealth />} />
        <Route path="/simulator" element={<Admin.Simulator />} />
        <Route path="/admin/users" element={<Admin.Users />} />
        <Route path="/admin/roles" element={<Admin.Roles />} />
        <Route path="/admin/audit" element={<Admin.AuditLogs />} />
        <Route path="/admin/settings" element={<Admin.Settings />} />
        <Route path="*" element={<div className="empty">Page not found</div>} />
      </Routes>
    </Layout>
  );
}
