import { useEffect, useState, type ReactNode } from 'react';
import { checkHealth } from './api/client';
import type { User } from './api/types';
import { AuthProvider, permissions, ROLE_LABELS, useAuth } from './auth';
import { Alert, EmptyState, LoadingState } from './components/ui';
import { FlashProvider } from './flash';
import { AccountPage } from './pages/AccountPage';
import { CreateIncidentPage } from './pages/CreateIncidentPage';
import { DashboardPage } from './pages/DashboardPage';
import { IncidentDetailPage } from './pages/IncidentDetailPage';
import { IncidentListPage } from './pages/IncidentListPage';
import { LoginPage } from './pages/LoginPage';
import { UsersPage } from './pages/UsersPage';
import { Link, useLocation, type Route } from './router';

type ApiStatus = 'checking' | 'connected' | 'unavailable';

const API_STATUS_LABELS: Record<ApiStatus, string> = {
  checking: 'Checking API…',
  connected: 'API connected',
  unavailable: 'API unavailable',
};

function ApiStatusIndicator() {
  const [status, setStatus] = useState<ApiStatus>('checking');
  useEffect(() => {
    const controller = new AbortController();
    checkHealth(controller.signal)
      .then(ok => setStatus(ok ? 'connected' : 'unavailable'))
      .catch(() => { /* aborted on unmount */ });
    return () => controller.abort();
  }, []);
  return (
    <p className={`api-status api-status-${status}`} role="status">
      <span className="api-status-dot" aria-hidden="true" />
      {API_STATUS_LABELS[status]}
    </p>
  );
}

function UserMenu({ user }: { user: User }) {
  const { logout } = useAuth();
  const [signingOut, setSigningOut] = useState(false);
  return (
    <div className="user-menu">
      <Link to="/account" className="user-menu-account" aria-label={`Account settings for ${user.email}`}>
        <span className="user-menu-email">{user.email}</span>
        <span className={`badge role-badge role-${user.role}`}>{ROLE_LABELS[user.role]}</span>
      </Link>
      <button
        type="button"
        className="button button-secondary button-small"
        disabled={signingOut}
        onClick={() => {
          setSigningOut(true);
          void logout().catch(() => setSigningOut(false));
        }}
      >
        {signingOut ? 'Signing out…' : 'Sign out'}
      </button>
    </div>
  );
}

function Layout({ user, children }: { user?: User; children: ReactNode }) {
  const { pathname } = useLocation();
  const navItems = user
    ? [
        { to: '/', label: permissions.canTriage(user) ? 'Incidents' : 'My incidents', active: pathname === '/' || pathname.startsWith('/incidents') },
        { to: '/dashboard', label: permissions.canTriage(user) ? 'Dashboard' : 'My dashboard', active: pathname === '/dashboard' },
        ...(permissions.canManageUsers(user) ? [{ to: '/users', label: 'Users', active: pathname === '/users' }] : []),
      ]
    : [];
  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="app-header">
        <div className="app-header-inner">
          <div className="app-header-start">
            <Link to="/" className="brand">
              <span className="brand-mark" aria-hidden="true">◆</span>
              <span>Security Incident Management</span>
            </Link>
            {navItems.length > 0 && (
              <nav aria-label="Main" className="main-nav">
                {navItems.map(item => (
                  <Link key={item.to} to={item.to} className="main-nav-link" aria-current={item.active ? 'page' : undefined}>{item.label}</Link>
                ))}
              </nav>
            )}
          </div>
          <div className="app-header-end">
            <ApiStatusIndicator />
            {user && <UserMenu user={user} />}
          </div>
        </div>
      </header>
      <main id="main" className="app-main" tabIndex={-1}>{children}</main>
    </div>
  );
}

function Forbidden() {
  return (
    <EmptyState title="You do not have access to this page" action={<Link to="/" className="button button-secondary">Back to incidents</Link>}>
      Ask an administrator if you need this access.
    </EmptyState>
  );
}

function RouteView({ route, user }: { route: Route; user: User }) {
  switch (route.name) {
    case 'list':
      return <IncidentListPage search={route.search} />;
    case 'new':
      return <CreateIncidentPage />;
    case 'detail':
      return <IncidentDetailPage key={route.id} id={route.id} />;
    case 'users':
      return permissions.canManageUsers(user) ? <UsersPage /> : <Forbidden />;
    case 'account':
      return <AccountPage />;
    case 'dashboard':
      return <DashboardPage search={route.search} />;
    case 'notFound':
      return <EmptyState title="Page not found" action={<Link to="/" className="button button-secondary">Back to incidents</Link>}>This page does not exist.</EmptyState>;
  }
}

function Shell() {
  const { state, retry } = useAuth();
  const { route } = useLocation();

  if (state.status === 'loading') return <Layout><LoadingState label="Checking your session…" /></Layout>;
  if (state.status === 'error') {
    return (
      <Layout>
        <Alert tone="error" title="Unable to check your session" action={<button type="button" className="button button-secondary button-small" onClick={retry}>Retry</button>}>
          {state.message}
        </Alert>
      </Layout>
    );
  }
  // Signed out: show sign-in in place. The URL is kept, so the requested page opens after signing in.
  if (state.status === 'anonymous') return <Layout><LoginPage notice={state.notice} /></Layout>;

  return (
    <Layout user={state.user}>
      {/* Re-mount pages when the signed-in user changes so no data from a previous user lingers. */}
      <RouteView key={state.user.id} route={route} user={state.user} />
    </Layout>
  );
}

export default function App() {
  return (
    <AuthProvider>
      <FlashProvider>
        <Shell />
      </FlashProvider>
    </AuthProvider>
  );
}
