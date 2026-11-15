import { useCallback, useEffect, useState } from 'react';
import { ApiError, listIncidents } from '../api/client';
import { SEVERITIES, STATUSES, isSeverity, isStatus, type IncidentPage, type IncidentStatus, type Severity } from '../api/types';
import { Alert, EmptyState, LoadingState, SeverityBadge, StatusBadge } from '../components/ui';
import { formatDateTime, SEVERITY_LABELS, STATUS_LABELS } from '../format';
import { Link, navigate } from '../router';

export const PAGE_SIZE = 10;

interface Filters {
  severity?: Severity;
  status?: IncidentStatus;
  page: number;
}

function readFilters(search: URLSearchParams): Filters {
  const severity = search.get('severity');
  const status = search.get('status');
  const page = Number(search.get('page'));
  return {
    severity: isSeverity(severity) ? severity : undefined,
    status: isStatus(status) ? status : undefined,
    page: Number.isInteger(page) && page >= 1 ? page : 1,
  };
}

function toSearch(filters: Filters): string {
  const query = new URLSearchParams();
  if (filters.severity) query.set('severity', filters.severity);
  if (filters.status) query.set('status', filters.status);
  if (filters.page > 1) query.set('page', String(filters.page));
  const text = query.toString();
  return text ? `/?${text}` : '/';
}

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; page: IncidentPage };

export function IncidentListPage({ search }: { search: URLSearchParams }) {
  const filters = readFilters(search);
  const { severity, status, page } = filters;
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: 'loading' });
    listIncidents({ limit: PAGE_SIZE, offset: (page - 1) * PAGE_SIZE, severity, status }, controller.signal)
      .then(result => setState({ kind: 'ready', page: result }))
      .catch(error => {
        if (controller.signal.aborted) return;
        setState({ kind: 'error', message: error instanceof ApiError ? error.message : 'Unable to load incidents.' });
      });
    return () => controller.abort();
  }, [severity, status, page, reloadKey]);

  const update = useCallback((next: Partial<Filters>) => {
    // Changing a filter returns to the first page; the URL is the source of truth.
    navigate(toSearch({ ...filters, page: 1, ...next }), { replace: !('page' in next) });
  }, [filters]);

  const hasFilters = Boolean(severity || status);

  return (
    <section aria-labelledby="incidents-heading">
      <div className="page-header">
        <div>
          <h1 id="incidents-heading">Incidents</h1>
          <p className="muted">Track, triage and resolve reported security incidents.</p>
        </div>
        <Link to="/incidents/new" className="button button-primary">New incident</Link>
      </div>

      <div className="card">
        <form className="filters" role="search" aria-label="Filter incidents" onSubmit={event => event.preventDefault()}>
          <div className="field field-inline">
            <label htmlFor="filter-severity">Severity</label>
            <select id="filter-severity" value={severity ?? ''} onChange={event => update({ severity: isSeverity(event.target.value) ? event.target.value : undefined })}>
              <option value="">All severities</option>
              {SEVERITIES.map(value => <option key={value} value={value}>{SEVERITY_LABELS[value]}</option>)}
            </select>
          </div>
          <div className="field field-inline">
            <label htmlFor="filter-status">Status</label>
            <select id="filter-status" value={status ?? ''} onChange={event => update({ status: isStatus(event.target.value) ? event.target.value : undefined })}>
              <option value="">All statuses</option>
              {STATUSES.map(value => <option key={value} value={value}>{STATUS_LABELS[value]}</option>)}
            </select>
          </div>
          {hasFilters && (
            <button type="button" className="button button-ghost" onClick={() => update({ severity: undefined, status: undefined })}>
              Clear filters
            </button>
          )}
        </form>

        {state.kind === 'loading' && <LoadingState label="Loading incidents…" />}

        {state.kind === 'error' && (
          <Alert
            tone="error"
            title="Incidents could not be loaded"
            action={<button type="button" className="button button-secondary button-small" onClick={() => setReloadKey(key => key + 1)}>Retry</button>}
          >
            {state.message}
          </Alert>
        )}

        {state.kind === 'ready' && <Results page={state.page} pageNumber={page} hasFilters={hasFilters} update={update} />}
      </div>
    </section>
  );
}

function Results({ page, pageNumber, hasFilters, update }: {
  page: IncidentPage;
  pageNumber: number;
  hasFilters: boolean;
  update: (next: Partial<Filters>) => void;
}) {
  const totalPages = Math.max(1, Math.ceil(page.total / PAGE_SIZE));

  if (page.items.length === 0) {
    if (page.total > 0) {
      return (
        <EmptyState title="This page is empty" action={<button type="button" className="button button-secondary" onClick={() => update({ page: 1 })}>Go to first page</button>}>
          There are {page.total} matching incidents, but none on page {pageNumber}.
        </EmptyState>
      );
    }
    return hasFilters ? (
      <EmptyState title="No incidents match these filters" action={<button type="button" className="button button-secondary" onClick={() => update({ severity: undefined, status: undefined })}>Clear filters</button>}>
        Try a different severity or status.
      </EmptyState>
    ) : (
      <EmptyState title="No incidents reported yet" action={<Link to="/incidents/new" className="button button-primary">Report the first incident</Link>}>
        New incidents will appear here.
      </EmptyState>
    );
  }

  const first = page.offset + 1;
  const last = page.offset + page.items.length;

  return (
    <>
      <div className="table-wrap">
        <table className="incident-table">
          <caption className="visually-hidden">Incidents, newest first</caption>
          <thead>
            <tr>
              <th scope="col">ID</th>
              <th scope="col">Title</th>
              <th scope="col">Severity</th>
              <th scope="col">Status</th>
              <th scope="col">Assignee</th>
              <th scope="col">Last updated</th>
            </tr>
          </thead>
          <tbody>
            {page.items.map(incident => (
              <tr key={incident.id}>
                <td data-label="ID" className="cell-id">#{incident.id}</td>
                <td data-label="Title" className="cell-title">
                  <Link to={`/incidents/${incident.id}`}>{incident.title}</Link>
                </td>
                <td data-label="Severity"><SeverityBadge severity={incident.severity} /></td>
                <td data-label="Status"><StatusBadge status={incident.status} /></td>
                <td data-label="Assignee">{incident.assignee_id ?? <span className="muted">Unassigned</span>}</td>
                <td data-label="Last updated"><time dateTime={incident.updated_at}>{formatDateTime(incident.updated_at)}</time></td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <nav className="pagination" aria-label="Pagination">
        <p className="muted" aria-live="polite">Showing {first}–{last} of {page.total}</p>
        <div className="pagination-buttons">
          <button type="button" className="button button-secondary" disabled={pageNumber <= 1} onClick={() => update({ page: pageNumber - 1 })}>
            Previous
          </button>
          <span className="pagination-page">Page {pageNumber} of {totalPages}</span>
          <button type="button" className="button button-secondary" disabled={pageNumber >= totalPages} onClick={() => update({ page: pageNumber + 1 })}>
            Next
          </button>
        </div>
      </nav>
    </>
  );
}
