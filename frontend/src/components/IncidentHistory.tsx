import { useEffect, useState, type ReactNode } from 'react';
import { ApiError, listIncidentEvents } from '../api/client';
import { isSeverity, isStatus, type FieldChange, type IncidentEvent, type UserSummary } from '../api/types';
import { formatDateTime, SEVERITY_LABELS, STATUS_LABELS } from '../format';
import { Alert, Email, LoadingState } from './ui';

export const HISTORY_PAGE_SIZE = 10;

type People = Map<number, string>;

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; items: IncidentEvent[]; total: number; people: People };

const FIELD_LABELS: Record<string, string> = {
  title: 'Title',
  description: 'Description',
  severity: 'Severity',
  status: 'Status',
  assignee_id: 'Assignee',
};

function addPeople(people: People, users: UserSummary[]): People {
  const next = new Map(people);
  for (const user of users) next.set(user.id, user.email);
  return next;
}

// Every value is rendered as React text, never as HTML, whatever it contains.
function plain(value: unknown): string {
  if (value === null || value === undefined) return '—';
  return typeof value === 'string' ? value : JSON.stringify(value);
}

function Person({ id, people }: { id: unknown; people: People }) {
  if (id === null || id === undefined) return <span className="muted">Unassigned</span>;
  const email = typeof id === 'number' ? people.get(id) : undefined;
  return email ? <Email value={email} /> : <span>user #{plain(id)}</span>;
}

function fieldValue(field: string, value: unknown, people: People): ReactNode {
  if (field === 'severity' && isSeverity(value)) return SEVERITY_LABELS[value];
  if (field === 'status' && isStatus(value)) return STATUS_LABELS[value];
  if (field === 'assignee_id') return <Person id={value} people={people} />;
  return plain(value);
}

function DescriptionChange({ change }: { change: FieldChange }) {
  return (
    <details className="history-details">
      <summary>Show description before and after</summary>
      <div className="history-compare">
        <div>
          <p className="history-compare-label">Before</p>
          <p className="history-text">{plain(change.from)}</p>
        </div>
        <div>
          <p className="history-compare-label">After</p>
          <p className="history-text">{plain(change.to)}</p>
        </div>
      </div>
    </details>
  );
}

function FieldChanges({ changes, people }: { changes: Record<string, FieldChange>; people: People }) {
  return (
    <ul className="history-changes">
      {Object.entries(changes).map(([field, change]) => (
        <li key={field}>
          <span className="history-field">{FIELD_LABELS[field] ?? field}</span>{' '}
          {field === 'description' ? (
            <>changed <DescriptionChange change={change} /></>
          ) : (
            <>
              <span className="history-from">{fieldValue(field, change.from, people)}</span>
              <span aria-hidden="true"> → </span>
              <span className="visually-hidden"> to </span>
              <span className="history-to">{fieldValue(field, change.to, people)}</span>
            </>
          )}
        </li>
      ))}
    </ul>
  );
}

function Summary({ event, people }: { event: IncidentEvent; people: People }) {
  const { changes } = event;
  switch (event.action) {
    case 'created': {
      const initial = [
        changes.severity && `Severity ${fieldValue('severity', changes.severity.to, people)}`,
        changes.status && `Status ${fieldValue('status', changes.status.to, people)}`,
      ].filter(Boolean).join(' · ');
      return (
        <>
          <p>reported this incident</p>
          {(initial || changes.assignee_id) && (
            <p className="muted small">
              {initial}
              {changes.assignee_id && <>{initial && ' · '}Assigned to <Person id={changes.assignee_id.to} people={people} /></>}
            </p>
          )}
        </>
      );
    }
    case 'status_changed':
      return (
        <p>
          changed status from <strong>{fieldValue('status', changes.status?.from, people)}</strong> to{' '}
          <strong>{fieldValue('status', changes.status?.to, people)}</strong>
        </p>
      );
    case 'assignment_changed': {
      const { from, to } = changes.assignee_id ?? { from: null, to: null };
      if (from === null) return <p>assigned this incident to <Person id={to} people={people} /></p>;
      if (to === null) return <p>unassigned <Person id={from} people={people} /></p>;
      return <p>reassigned this incident from <Person id={from} people={people} /> to <Person id={to} people={people} /></p>;
    }
    default: {
      const count = Object.keys(changes).length;
      return (
        <>
          <p>updated {count === 1 ? '1 field' : `${count} fields`}</p>
          <FieldChanges changes={changes} people={people} />
        </>
      );
    }
  }
}

/** Audit history for one incident, newest first. `refreshKey` reloads it after edits. */
export function IncidentHistory({ incidentId, refreshKey }: { incidentId: number; refreshKey: number }) {
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [retryKey, setRetryKey] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setState(current => (current.kind === 'ready' ? current : { kind: 'loading' }));
    setMoreError(null);
    listIncidentEvents(incidentId, { limit: HISTORY_PAGE_SIZE, offset: 0 }, controller.signal)
      .then(page => setState({ kind: 'ready', items: page.items, total: page.total, people: addPeople(new Map(), page.users) }))
      .catch(error => {
        if (controller.signal.aborted) return;
        setState({ kind: 'error', message: error instanceof ApiError ? error.message : 'Unable to load the history.' });
      });
    return () => controller.abort();
  }, [incidentId, refreshKey, retryKey]);

  async function loadMore() {
    if (state.kind !== 'ready') return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await listIncidentEvents(incidentId, { limit: HISTORY_PAGE_SIZE, offset: state.items.length });
      setState(current => current.kind === 'ready'
        ? {
            kind: 'ready',
            // Skip anything already shown, in case new events shifted the page boundaries.
            items: [...current.items, ...page.items.filter(item => !current.items.some(existing => existing.id === item.id))],
            total: page.total,
            people: addPeople(current.people, page.users),
          }
        : current);
    } catch (error) {
      setMoreError(error instanceof ApiError ? error.message : 'Unable to load older history.');
    } finally {
      setLoadingMore(false);
    }
  }

  return (
    <section className="card history" aria-labelledby="history-heading">
      <h2 id="history-heading">History</h2>
      {state.kind === 'loading' && <LoadingState label="Loading history…" />}
      {state.kind === 'error' && (
        <Alert
          tone="error"
          title="History could not be loaded"
          action={<button type="button" className="button button-secondary button-small" onClick={() => setRetryKey(key => key + 1)}>Retry</button>}
        >
          {state.message}
        </Alert>
      )}
      {state.kind === 'ready' && state.items.length === 0 && (
        <div className="history-empty">
          <p>No history recorded yet.</p>
          <p className="muted small">Changes are recorded from the moment audit logging was enabled. Earlier changes to this incident were not tracked.</p>
        </div>
      )}
      {state.kind === 'ready' && state.items.length > 0 && (
        <>
          <ol className="timeline" aria-label="Incident history, newest first">
            {state.items.map(event => (
              <li key={event.id} className={`timeline-item timeline-${event.action}`}>
                <div className="timeline-body">
                  <p className="timeline-actor"><Email value={event.actor.email} /></p>
                  <Summary event={event} people={state.people} />
                  {event.triage_suggestion_id != null && (
                    <p className="muted small timeline-triage">Applied from triage suggestion #{event.triage_suggestion_id} after review</p>
                  )}
                </div>
                <time className="timeline-time muted small" dateTime={event.created_at}>{formatDateTime(event.created_at)}</time>
              </li>
            ))}
          </ol>
          {moreError && <Alert tone="error">{moreError}</Alert>}
          {state.items.length < state.total && (
            <div className="history-more">
              <button type="button" className="button button-secondary button-small" disabled={loadingMore} onClick={() => void loadMore()}>
                {loadingMore ? 'Loading…' : 'Show older'}
              </button>
              <span className="muted small">Showing {state.items.length} of {state.total}</span>
            </div>
          )}
        </>
      )}
    </section>
  );
}
