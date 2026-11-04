// Thin API client for the FNMS backend. All data shown in the UI comes from here — no mock data.
export const API = "/api/v1";

export class ApiError extends Error {
  status: number;
  code: string;
  details: any;
  constructor(status: number, code: string, message: string, details?: any) {
    super(message);
    this.status = status;
    this.code = code;
    this.details = details;
  }
  get list(): string[] {
    const d = this.details;
    if (d && Array.isArray(d.errors)) return d.errors;
    if (Array.isArray(d)) return d.map((e: any) => `${(e.loc || []).slice(1).join(".")}: ${e.msg}`);
    return [];
  }
}

let token: string | null = sessionStorage.getItem("fnms_token");
let onUnauthorized: () => void = () => {};

export function setToken(t: string | null) {
  token = t;
  if (t) sessionStorage.setItem("fnms_token", t);
  else sessionStorage.removeItem("fnms_token");
}
export const getToken = () => token;
export function setUnauthorizedHandler(fn: () => void) {
  onUnauthorized = fn;
}

export async function api<T = any>(path: string, opts: { method?: string; body?: any; query?: Record<string, any> } = {}): Promise<T> {
  let url = path.startsWith("/api") ? path : API + path;
  if (opts.query) {
    const q = new URLSearchParams();
    Object.entries(opts.query).forEach(([k, v]) => {
      if (v !== undefined && v !== null && v !== "") q.set(k, String(v));
    });
    const s = q.toString();
    if (s) url += (url.includes("?") ? "&" : "?") + s;
  }
  const res = await fetch(url, {
    method: opts.method || (opts.body !== undefined ? "POST" : "GET"),
    headers: { "Content-Type": "application/json", ...(token ? { Authorization: `Bearer ${token}` } : {}) },
    body: opts.body !== undefined ? JSON.stringify(opts.body) : undefined,
  });
  if (res.status === 204) return undefined as T;
  let data: any = null;
  try {
    data = await res.json();
  } catch {
    /* empty */
  }
  if (!res.ok) {
    if (res.status === 401 && !path.includes("/auth/login")) onUnauthorized();
    const e = data?.error || {};
    throw new ApiError(res.status, e.code || "error", e.message || res.statusText, e.details);
  }
  return data as T;
}

export const get = <T = any>(p: string, query?: Record<string, any>) => api<T>(p, { query });
export const post = <T = any>(p: string, body: any = {}) => api<T>(p, { method: "POST", body });
export const put = <T = any>(p: string, body: any) => api<T>(p, { method: "PUT", body });
export const patch = <T = any>(p: string, body: any) => api<T>(p, { method: "PATCH", body });
export const del = <T = any>(p: string) => api<T>(p, { method: "DELETE" });

/** Poll a background job until it finishes. */
export async function waitForJob(jobId: string, timeoutMs = 120000): Promise<any> {
  const start = Date.now();
  let delay = 600;
  while (Date.now() - start < timeoutMs) {
    const j = await get(`/jobs/${jobId}`);
    if (["succeeded", "failed", "cancelled"].includes(j.status)) return j;
    await new Promise((r) => setTimeout(r, delay));
    delay = Math.min(3000, delay * 1.4);
  }
  throw new Error("Timed out waiting for background job — is the worker running?");
}
