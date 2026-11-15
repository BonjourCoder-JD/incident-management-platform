import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { getSession, login as apiLogin, logout as apiLogout, setCsrfToken, setUnauthorizedHandler } from './api/client';
import type { SessionInfo, User, UserRole } from './api/types';

type AuthState =
  | { status: 'loading' }
  | { status: 'error'; message: string }
  | { status: 'anonymous'; notice: string | null }
  | { status: 'authenticated'; user: User };

interface AuthContextValue {
  state: AuthState;
  login: (email: string, password: string) => Promise<void>;
  logout: () => Promise<void>;
  retry: () => void;
}

const AuthContext = createContext<AuthContextValue | null>(null);

export function AuthProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<AuthState>({ status: 'loading' });
  const [attempt, setAttempt] = useState(0);

  const applySession = useCallback((session: SessionInfo | null, notice: string | null = null) => {
    setCsrfToken(session?.csrf_token ?? null);
    setState(session ? { status: 'authenticated', user: session.user } : { status: 'anonymous', notice });
  }, []);

  useEffect(() => {
    const controller = new AbortController();
    setState({ status: 'loading' });
    getSession(controller.signal)
      .then(session => applySession(session))
      .catch(error => {
        if (controller.signal.aborted) return;
        setState({ status: 'error', message: error instanceof Error ? error.message : 'Unable to check your session.' });
      });
    return () => controller.abort();
  }, [applySession, attempt]);

  // Any 401 from the API means the session expired or was revoked (e.g. role changed).
  useEffect(() => {
    setUnauthorizedHandler(() => applySession(null, 'Your session has ended. Sign in again to continue.'));
    return () => setUnauthorizedHandler(null);
  }, [applySession]);

  const login = useCallback(async (email: string, password: string) => {
    applySession(await apiLogin(email, password));
  }, [applySession]);

  const logout = useCallback(async () => {
    try {
      await apiLogout();
    } finally {
      applySession(null, 'You have signed out.');
    }
  }, [applySession]);

  const retry = useCallback(() => setAttempt(value => value + 1), []);
  const value = useMemo(() => ({ state, login, logout, retry }), [state, login, logout, retry]);
  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>;
}

export function useAuth(): AuthContextValue {
  const value = useContext(AuthContext);
  if (!value) throw new Error('useAuth must be used inside AuthProvider');
  return value;
}

/** The signed-in user. Only call inside screens rendered for authenticated users. */
export function useCurrentUser(): User {
  const { state } = useAuth();
  if (state.status !== 'authenticated') throw new Error('useCurrentUser requires a signed-in user');
  return state.user;
}

// UI hints only: the API enforces every one of these rules independently.
const STAFF: readonly UserRole[] = ['responder', 'admin'];

export const permissions = {
  /** Change status and assignment, see every incident. */
  canTriage: (user: User) => STAFF.includes(user.role),
  canManageUsers: (user: User) => user.role === 'admin',
};

export const ROLE_LABELS: Record<UserRole, string> = {
  reporter: 'Reporter',
  responder: 'Responder',
  admin: 'Admin',
};
