// 啟動流程：GET /v1/iam/me → 有 session 就進 AppShell；401 由 client 導向 /login。登出：POST /auth/logout → 若回 idp_logout 則整頁跳 IdP 結束 session。
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, ApiError } from '@/api/client';
import type { Me } from '@/api/types';
import { can } from '@/lib/policy';
import type { Action, Module } from '@/api/types';

interface AuthState {
  me: Me | null;
  loading: boolean;
  error: string | null;
  reload: () => Promise<void>;
  logout: () => Promise<void>;
  can: (module: Module, action: Action) => boolean;
}

const Ctx = createContext<AuthState | null>(null);

export function AuthProvider({ children, bootstrap }: { children: ReactNode; bootstrap?: Me | null }) {
  const [me, setMe] = useState<Me | null>(bootstrap ?? null);
  const [loading, setLoading] = useState(bootstrap === undefined);
  const [error, setError] = useState<string | null>(null);

  const reload = useCallback(async () => {
    setLoading(true);
    try { setMe(await api.get<Me>('/v1/iam/me')); setError(null); }
    catch (e) { if (!(e instanceof ApiError && e.status === 401)) setError((e as Error).message); }
    finally { setLoading(false); }
  }, []);

  const logout = useCallback(async () => {
    const r = await api.post<{ ok: boolean; idp_logout: string | null }>('/auth/logout');
    setMe(null);
    location.assign(r.idp_logout ?? '/login');
  }, []);

  useEffect(() => { if (bootstrap === undefined) void reload(); }, [bootstrap, reload]);

  const value = useMemo<AuthState>(() => ({ me, loading, error, reload, logout, can: (m, a) => can(me, m, a) }), [me, loading, error, reload, logout]);
  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAuth(): AuthState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useAuth 必須在 AuthProvider 內使用');
  return v;
}
