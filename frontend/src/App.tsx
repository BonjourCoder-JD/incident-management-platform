import { useEffect, useState, type ReactNode } from 'react';
import { checkHealth } from './api/client';
import { EmptyState } from './components/ui';
import { FlashProvider } from './flash';
import { CreateIncidentPage } from './pages/CreateIncidentPage';
import { IncidentDetailPage } from './pages/IncidentDetailPage';
import { IncidentListPage } from './pages/IncidentListPage';
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

function Layout({ children }: { children: ReactNode }) {
  return (
    <div className="app">
      <a className="skip-link" href="#main">Skip to content</a>
      <header className="app-header">
        <div className="app-header-inner">
          <Link to="/" className="brand">
            <span className="brand-mark" aria-hidden="true">◆</span>
            <span>Security Incident Management</span>
          </Link>
          <ApiStatusIndicator />
        </div>
      </header>
      <main id="main" className="app-main" tabIndex={-1}>{children}</main>
    </div>
  );
}

function RouteView({ route }: { route: Route }) {
  switch (route.name) {
    case 'list':
      return <IncidentListPage search={route.search} />;
    case 'new':
      return <CreateIncidentPage />;
    case 'detail':
      return <IncidentDetailPage key={route.id} id={route.id} />;
    case 'notFound':
      return <EmptyState title="Page not found" action={<Link to="/" className="button button-secondary">Back to incidents</Link>}>This page does not exist.</EmptyState>;
  }
}

export default function App() {
  const { route } = useLocation();
  return (
    <FlashProvider>
      <Layout>
        <RouteView route={route} />
      </Layout>
    </FlashProvider>
  );
}
