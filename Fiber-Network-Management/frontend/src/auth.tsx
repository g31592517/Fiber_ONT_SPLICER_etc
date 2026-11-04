import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { get, getToken, post, setToken, setUnauthorizedHandler } from "./api";

export type User = { id: number; username: string; full_name: string; role: string; permissions: string[] };

type AuthCtx = {
  user: User | null;
  ready: boolean;
  login: (u: string, p: string) => Promise<void>;
  logout: () => Promise<void>;
  can: (perm: string) => boolean;
};

const Ctx = createContext<AuthCtx>(null as any);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [user, setUser] = useState<User | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    setUnauthorizedHandler(() => {
      setToken(null);
      setUser(null);
    });
    if (!getToken()) {
      setReady(true);
      return;
    }
    get<User>("/auth/me")
      .then(setUser)
      .catch(() => setToken(null))
      .finally(() => setReady(true));
  }, []);

  const login = useCallback(async (username: string, password: string) => {
    const r = await post("/auth/login", { username, password });
    setToken(r.access_token);
    setUser(r.user);
  }, []);
  const logout = useCallback(async () => {
    try {
      await post("/auth/logout");
    } catch {
      /* ignore */
    }
    setToken(null);
    setUser(null);
  }, []);
  const can = useCallback((perm: string) => !!user?.permissions.includes(perm), [user]);
  const v = useMemo(() => ({ user, ready, login, logout, can }), [user, ready, login, logout, can]);
  return <Ctx.Provider value={v}>{children}</Ctx.Provider>;
}

export const useAuth = () => useContext(Ctx);
