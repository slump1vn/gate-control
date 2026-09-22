'use client';

import { createContext, useCallback, useContext, useEffect, useMemo, useState, ReactNode } from 'react';
import * as gateApi from '@/lib/gate-api';
import type { Role, Session } from '@/lib/gate-api';

export interface AuthState {
  loading: boolean;
  authenticated: boolean;
  username: string | null;
  roles: Role[];
  hasRole: (role: Role) => boolean;
  login: (username: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  refresh: () => Promise<void>;
}

const ANONYMOUS: AuthState = {
  loading: false,
  authenticated: false,
  username: null,
  roles: [],
  hasRole: () => false,
  login: async () => {},
  logout: async () => {},
  refresh: async () => {},
};

const AuthContext = createContext<AuthState>(ANONYMOUS);

export function useAuth(): AuthState {
  return useContext(AuthContext);
}

/** A fixed session for Storybook and tests. */
export function MockAuthProvider({ roles = [], username = 'demo', children }: { roles?: Role[]; username?: string; children: ReactNode }) {
  const value = useMemo<AuthState>(() => ({
    ...ANONYMOUS,
    authenticated: roles.length > 0,
    username: roles.length > 0 ? username : null,
    roles,
    hasRole: (role) => roles.includes(role),
  }), [roles, username]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(() => gateApi.getSession()
    .then(setSession)
    // Backend unreachable: behave as logged out; the pages show their own errors.
    .catch(() => setSession(null))
    .finally(() => setLoading(false)), []);

  useEffect(() => {
    gateApi.getSession().then(setSession).catch(() => setSession(null)).finally(() => setLoading(false));
  }, []);

  // A 401/403 may mean the session expired server-side: re-read it.
  useEffect(() => gateApi.onAuthFailure(() => { gateApi.getSession().then(setSession).catch(() => {}); }), []);

  const login = useCallback(async (username: string, password: string) => {
    setSession(await gateApi.login(username, password));
  }, []);

  const logout = useCallback(async () => {
    try {
      setSession(await gateApi.logout());
    } catch {
      setSession(null);
    }
  }, []);

  const value = useMemo<AuthState>(() => {
    const roles = session?.roles ?? [];
    return {
      loading,
      authenticated: !!session?.authenticated,
      username: session?.username ?? null,
      roles,
      hasRole: (role) => roles.includes(role),
      login,
      logout,
      refresh,
    };
  }, [session, loading, login, logout, refresh]);

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}
