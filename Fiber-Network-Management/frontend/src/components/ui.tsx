import { createContext, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, waitForJob } from "../api";
import { label } from "../format";

// ---------------------------------------------------------------- status badges
const TONE: Record<string, string> = {
  online: "ok", up: "ok", normal: "ok", active: "ok", succeeded: "ok", ok: "ok", verified: "ok", available: "ok", resolved: "muted",
  degraded: "warn", warning: "warn", minor: "warn", reserved: "info", acknowledged: "info", investigating: "info", queued: "info",
  running: "info", awaiting_approval: "info", validation_pending: "info", pending: "info", planned: "info", unverified: "warn",
  registered_inactive: "warn", suspended: "warn", partially_succeeded: "warn", rolled_back: "warn", dying_gasp: "warn",
  inferred: "warn", maintenance: "warn",
  offline: "bad", los: "bad", unreachable: "bad", fault: "bad", faulty: "bad", failed: "bad", critical: "bad", major: "bad",
  down: "bad", new: "bad", reopened: "bad", validation_failed: "bad", confirmed: "bad", open: "bad", disconnected: "muted",
  disabled: "muted", unknown: "muted", empty: "muted", cancelled: "muted", info: "muted", occupied: "info", deregistered: "muted",
  decommissioned: "muted", skipped: "muted", compensated: "warn", compensation_failed: "bad", unregistered: "info",
  ignored: "muted", gone: "muted", registered: "ok",
};

const ABBR: Record<string, string> = { los: "LOS", ok: "OK", olt_unreachable: "OLT unreachable", ont_los: "ONT LOS", ont_dying_gasp: "ONT dying gasp" };

export function Badge({ value, title, tone }: { value?: string | null; title?: string; tone?: string }) {
  const v = value || "unknown";
  const text = ABBR[v] || label(v);
  return (
    <span className={`badge ${tone || TONE[v] || "muted"} ${ABBR[v] ? "abbr" : ""}`} title={title}>
      {text}
    </span>
  );
}

export function SimTag({ on = true }: { on?: boolean }) {
  return on ? <span className="badge sim" title="Data produced by the built-in simulator — not live network data">SIMULATED</span> : null;
}

export function Unsupported({ text = "unsupported / not reported" }) {
  return <span className="unsupported">{text}</span>;
}

// ---------------------------------------------------------------- layout primitives
export function PageHeader({ title, subtitle, actions, crumbs }: { title: ReactNode; subtitle?: ReactNode; actions?: ReactNode; crumbs?: [string, string?][] }) {
  return (
    <div className="page-header">
      <div>
        {crumbs && (
          <div className="crumbs">
            {crumbs.map(([t, to], i) => (
              <span key={i}>
                {to ? <Link to={to}>{t}</Link> : t}
                {i < crumbs.length - 1 && " / "}
              </span>
            ))}
          </div>
        )}
        <h1>{title}</h1>
        {subtitle && <div className="subtitle">{subtitle}</div>}
      </div>
      {actions && <div className="actions">{actions}</div>}
    </div>
  );
}

export function Card({ title, children, actions, className }: { title?: ReactNode; children: ReactNode; actions?: ReactNode; className?: string }) {
  return (
    <section className={`card ${className || ""}`}>
      {(title || actions) && (
        <div className="card-head">
          <h3>{title}</h3>
          {actions && <div className="actions">{actions}</div>}
        </div>
      )}
      <div className="card-body">{children}</div>
    </section>
  );
}

export function Stat({ label: l, value, tone, to, hint }: { label: string; value: ReactNode; tone?: string; to?: string; hint?: string }) {
  const inner = (
    <div className={`stat ${tone || ""}`} title={hint}>
      <div className="stat-value">{value}</div>
      <div className="stat-label">{l}</div>
    </div>
  );
  return to ? <Link to={to} className="stat-link">{inner}</Link> : inner;
}

export function KV({ rows }: { rows: [string, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v]) => (
        <div key={k}>
          <dt>{k}</dt>
          <dd>{v === null || v === undefined || v === "" ? "—" : v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function Loading({ text = "Loading…" }) {
  return <div className="loading">{text}</div>;
}

export function ErrorBox({ error }: { error: any }) {
  if (!error) return null;
  const e = error as ApiError;
  return (
    <div className="error-box">
      <strong>{e.message || String(error)}</strong>
      {e.list?.length > 0 && (
        <ul>
          {e.list.map((x, i) => (
            <li key={i}>{x}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}

export function Tabs({ tabs, active, onChange }: { tabs: [string, string][]; active: string; onChange: (k: string) => void }) {
  return (
    <div className="tabs">
      {tabs.map(([k, t]) => (
        <button key={k} className={k === active ? "active" : ""} onClick={() => onChange(k)}>
          {t}
        </button>
      ))}
    </div>
  );
}

// ---------------------------------------------------------------- table
export type Col<T> = { key: string; title: string; render?: (row: T) => ReactNode; sort?: (row: T) => any; className?: string };

export function Table<T extends Record<string, any>>({ rows, cols, empty = "No records", searchable, rowKey = "id", onRow }: {
  rows: T[]; cols: Col<T>[]; empty?: string; searchable?: boolean; rowKey?: string; onRow?: (r: T) => void;
}) {
  const [q, setQ] = useState("");
  const [sort, setSort] = useState<{ k: string; dir: 1 | -1 } | null>(null);
  const shown = useMemo(() => {
    let r = rows || [];
    if (q) {
      const ql = q.toLowerCase();
      r = r.filter((x) => JSON.stringify(x).toLowerCase().includes(ql));
    }
    if (sort) {
      const c = cols.find((c) => c.key === sort.k);
      const f = c?.sort || ((x: T) => x[sort.k]);
      r = [...r].sort((a, b) => {
        const va = f(a), vb = f(b);
        if (va === vb) return 0;
        if (va === null || va === undefined) return 1;
        if (vb === null || vb === undefined) return -1;
        return (va > vb ? 1 : -1) * sort.dir;
      });
    }
    return r;
  }, [rows, q, sort, cols]);
  return (
    <div className="table-wrap">
      {searchable && <input className="table-search" placeholder="Filter rows…" value={q} onChange={(e) => setQ(e.target.value)} />}
      <table>
        <thead>
          <tr>
            {cols.map((c) => (
              <th key={c.key} className={c.className} onClick={() => setSort((s) => ({ k: c.key, dir: s?.k === c.key && s.dir === 1 ? -1 : 1 }))}>
                {c.title}
                {sort?.k === c.key ? (sort.dir === 1 ? " ▲" : " ▼") : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {shown.length === 0 && (
            <tr>
              <td colSpan={cols.length} className="empty-row">
                {empty}
              </td>
            </tr>
          )}
          {shown.map((r, i) => (
            <tr key={r[rowKey] ?? i} onClick={onRow ? () => onRow(r) : undefined} className={onRow ? "clickable" : ""}>
              {cols.map((c) => (
                <td key={c.key} className={c.className}>
                  {c.render ? c.render(r) : (r[c.key] ?? "—")}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Pager({ total, limit, offset, onChange }: { total: number; limit: number; offset: number; onChange: (o: number) => void }) {
  if (total <= limit) return <div className="pager">{total} record(s)</div>;
  return (
    <div className="pager">
      <button disabled={offset === 0} onClick={() => onChange(Math.max(0, offset - limit))}>‹ Prev</button>
      <span>
        {offset + 1}–{Math.min(total, offset + limit)} of {total}
      </span>
      <button disabled={offset + limit >= total} onClick={() => onChange(offset + limit)}>Next ›</button>
    </div>
  );
}

// ---------------------------------------------------------------- modal & confirmation
export function Modal({ title, onClose, children, wide }: { title: ReactNode; onClose: () => void; children: ReactNode; wide?: boolean }) {
  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", h);
    return () => window.removeEventListener("keydown", h);
  }, [onClose]);
  return (
    <div className="modal-backdrop" onMouseDown={onClose}>
      <div className={`modal ${wide ? "wide" : ""}`} onMouseDown={(e) => e.stopPropagation()} role="dialog" aria-modal>
        <div className="modal-head">
          <h3>{title}</h3>
          <button className="icon" onClick={onClose} aria-label="Close">×</button>
        </div>
        <div className="modal-body">{children}</div>
      </div>
    </div>
  );
}

export function ConfirmDialog({ title, message, token, destructive, onConfirm, onClose, busy }: {
  title: string; message: ReactNode; token?: string; destructive?: boolean; onConfirm: (typed: string) => void; onClose: () => void; busy?: boolean;
}) {
  const [typed, setTyped] = useState("");
  const ok = !token || typed.trim().toUpperCase() === token.toUpperCase();
  return (
    <Modal title={title} onClose={onClose}>
      <div className={destructive ? "confirm destructive" : "confirm"}>{message}</div>
      {token && (
        <label className="field">
          <span>
            Type <code>{token}</code> to confirm
          </span>
          <input autoFocus value={typed} onChange={(e) => setTyped(e.target.value)} />
        </label>
      )}
      <div className="form-actions">
        <button onClick={onClose}>Cancel</button>
        <button className={destructive ? "danger" : "primary"} disabled={!ok || busy} onClick={() => onConfirm(typed)}>
          {busy ? "Working…" : "Confirm"}
        </button>
      </div>
    </Modal>
  );
}

// ---------------------------------------------------------------- forms
export type FieldSpec = {
  name: string; label: string; type?: "text" | "number" | "select" | "textarea" | "checkbox" | "password" | "date";
  options?: [any, string][]; required?: boolean; help?: string; step?: string; placeholder?: string;
};

export function Form({ fields, initial, onSubmit, submitLabel = "Save", onCancel }: {
  fields: FieldSpec[]; initial?: Record<string, any>; onSubmit: (v: Record<string, any>) => Promise<any>; submitLabel?: string; onCancel?: () => void;
}) {
  const [v, setV] = useState<Record<string, any>>(() => ({ ...(initial || {}) }));
  const [err, setErr] = useState<any>(null);
  const [busy, setBusy] = useState(false);
  const set = (k: string, val: any) => setV((s) => ({ ...s, [k]: val }));
  return (
    <form
      className="form"
      onSubmit={async (e) => {
        e.preventDefault();
        setBusy(true);
        setErr(null);
        const out: Record<string, any> = {};
        for (const f of fields) {
          let val = v[f.name];
          if (f.type === "number") val = val === "" || val === undefined || val === null ? null : Number(val);
          if ((f.type === "select" || f.type === "date") && val === "") val = null;
          if (f.type === "checkbox") val = !!val;
          out[f.name] = val;
        }
        try {
          await onSubmit(out);
        } catch (ex) {
          setErr(ex);
        } finally {
          setBusy(false);
        }
      }}
    >
      <div className="form-grid">
        {fields.map((f) => (
          <label key={f.name} className={`field ${f.type === "textarea" ? "span2" : ""} ${f.type === "checkbox" ? "check" : ""}`}>
            <span>
              {f.label}
              {f.required && " *"}
            </span>
            {f.type === "select" ? (
              <select value={v[f.name] ?? ""} onChange={(e) => set(f.name, e.target.value)} required={f.required}>
                {!f.required && <option value="">—</option>}
                {f.options?.map(([val, t]) => (
                  <option key={String(val)} value={val}>
                    {t}
                  </option>
                ))}
              </select>
            ) : f.type === "textarea" ? (
              <textarea value={v[f.name] ?? ""} onChange={(e) => set(f.name, e.target.value)} rows={3} />
            ) : f.type === "checkbox" ? (
              <input type="checkbox" checked={!!v[f.name]} onChange={(e) => set(f.name, e.target.checked)} />
            ) : (
              <input type={f.type || "text"} step={f.step} placeholder={f.placeholder} value={v[f.name] ?? ""} required={f.required}
                onChange={(e) => set(f.name, e.target.value)} autoComplete={f.type === "password" ? "new-password" : undefined} />
            )}
            {f.help && <small>{f.help}</small>}
          </label>
        ))}
      </div>
      <ErrorBox error={err} />
      <div className="form-actions">
        {onCancel && <button type="button" onClick={onCancel}>Cancel</button>}
        <button className="primary" disabled={busy}>{busy ? "Saving…" : submitLabel}</button>
      </div>
    </form>
  );
}

// ---------------------------------------------------------------- toasts & background jobs
type Toast = { id: number; text: string; tone: "ok" | "bad" | "info" };
const ToastCtx = createContext<{ push: (t: string, tone?: Toast["tone"]) => void }>({ push: () => {} });

export function ToastProvider({ children }: { children: ReactNode }) {
  const [items, setItems] = useState<Toast[]>([]);
  const push = (text: string, tone: Toast["tone"] = "info") => {
    const id = Date.now() + Math.random();
    setItems((s) => [...s, { id, text, tone }]);
    setTimeout(() => setItems((s) => s.filter((x) => x.id !== id)), 6000);
  };
  return (
    <ToastCtx.Provider value={{ push }}>
      {children}
      <div className="toasts">
        {items.map((t) => (
          <div key={t.id} className={`toast ${t.tone}`}>{t.text}</div>
        ))}
      </div>
    </ToastCtx.Provider>
  );
}
export const useToast = () => useContext(ToastCtx);

/** Start a background job via `start`, then wait for it and report the outcome. */
export function useJobRunner() {
  const { push } = useToast();
  const [running, setRunning] = useState<string | null>(null);
  async function run(name: string, start: () => Promise<any>, onDone?: (job: any) => void) {
    setRunning(name);
    try {
      const ref = await start();
      push(`${name}: queued (job ${String(ref.job_id).slice(0, 8)})`, "info");
      const job = await waitForJob(ref.job_id);
      if (job.status === "succeeded") push(`${name}: completed`, "ok");
      else push(`${name}: ${job.status} — ${job.error || job.result?.reason || "see job details"}`, "bad");
      onDone?.(job);
      return job;
    } catch (e: any) {
      push(`${name}: ${e.message}`, "bad");
    } finally {
      setRunning(null);
    }
  }
  return { run, running };
}
