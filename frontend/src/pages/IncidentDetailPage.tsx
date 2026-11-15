import { useEffect, useState, type FormEvent } from 'react';
import { ApiError, getIncident, updateIncident, type FieldErrors } from '../api/client';
import { STATUSES, type Incident, type IncidentStatus, type IncidentUpdate, type Severity } from '../api/types';
import { IncidentFields } from '../components/IncidentFields';
import { Alert, EmptyState, LoadingState, SeverityBadge, StatusBadge } from '../components/ui';
import { useFlash } from '../flash';
import { formatDateTime, STATUS_LABELS } from '../format';
import { Link } from '../router';
import { parseAssignee, validateIncidentForm, type IncidentFormValues } from '../validation';
import { focusFirstInvalid } from './CreateIncidentPage';

type LoadState =
  | { kind: 'loading' }
  | { kind: 'notFound' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; incident: Incident };

type Notice = { tone: 'success' | 'error' | 'info'; text: string } | null;

function toFormValues(incident: Incident): IncidentFormValues {
  return {
    title: incident.title,
    description: incident.description,
    severity: incident.severity,
    assigneeId: incident.assignee_id === null ? '' : String(incident.assignee_id),
  };
}

/** Returns only the fields that differ from the saved incident. */
function changedFields(incident: Incident, values: IncidentFormValues): IncidentUpdate {
  const changes: IncidentUpdate = {};
  const title = values.title.trim();
  const description = values.description.trim();
  const assignee = parseAssignee(values.assigneeId);
  if (title !== incident.title) changes.title = title;
  if (description !== incident.description) changes.description = description;
  if (values.severity !== incident.severity) changes.severity = values.severity as Severity;
  if (assignee !== incident.assignee_id) changes.assignee_id = assignee;
  return changes;
}

export function IncidentDetailPage({ id }: { id: number }) {
  const { flash, dismiss } = useFlash();
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [reloadKey, setReloadKey] = useState(0);
  const [notice, setNotice] = useState<Notice>(null);
  const [editing, setEditing] = useState(false);
  const [values, setValues] = useState<IncidentFormValues | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [saving, setSaving] = useState<'status' | 'details' | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: 'loading' });
    setEditing(false);
    setNotice(null);
    getIncident(id, controller.signal)
      .then(incident => setState({ kind: 'ready', incident }))
      .catch(error => {
        if (controller.signal.aborted) return;
        if (error instanceof ApiError && error.status === 404) setState({ kind: 'notFound' });
        else setState({ kind: 'error', message: error instanceof ApiError ? error.message : 'Unable to load the incident.' });
      });
    return () => controller.abort();
  }, [id, reloadKey]);

  if (state.kind === 'loading') return <LoadingState label="Loading incident…" />;

  if (state.kind === 'notFound') {
    return (
      <EmptyState title={`Incident #${id} was not found`} action={<Link to="/" className="button button-secondary">Back to incidents</Link>}>
        It may have been entered incorrectly.
      </EmptyState>
    );
  }

  if (state.kind === 'error') {
    return (
      <Alert
        tone="error"
        title="The incident could not be loaded"
        action={<button type="button" className="button button-secondary button-small" onClick={() => setReloadKey(key => key + 1)}>Retry</button>}
      >
        {state.message}
      </Alert>
    );
  }

  const incident = state.incident;

  async function save(kind: 'status' | 'details', changes: IncidentUpdate, success: string) {
    setSaving(kind);
    setNotice(null);
    dismiss();
    try {
      const updated = await updateIncident(incident.id, changes);
      setState({ kind: 'ready', incident: updated });
      setNotice({ tone: 'success', text: success });
      return true;
    } catch (error) {
      if (error instanceof ApiError) {
        setErrors(error.fieldErrors);
        setNotice({ tone: 'error', text: error.message });
      } else {
        setNotice({ tone: 'error', text: 'The change could not be saved. Try again.' });
      }
      return false;
    } finally {
      setSaving(null);
    }
  }

  function changeStatus(status: IncidentStatus) {
    if (status === incident.status || saving) return;
    void save('status', { status }, `Status changed to ${STATUS_LABELS[status]}.`);
  }

  function startEditing() {
    setValues(toFormValues(incident));
    setErrors({});
    setNotice(null);
    dismiss();
    setEditing(true);
  }

  async function handleEditSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!values) return;
    const form = event.currentTarget;
    const validation = validateIncidentForm(values);
    setErrors(validation);
    if (Object.keys(validation).length > 0) {
      focusFirstInvalid(form);
      return;
    }
    const changes = changedFields(incident, values);
    if (Object.keys(changes).length === 0) {
      setEditing(false);
      setNotice({ tone: 'info', text: 'No changes to save.' });
      return;
    }
    if (await save('details', changes, 'Incident details saved.')) setEditing(false);
    else focusFirstInvalid(form);
  }

  return (
    <article aria-labelledby="incident-heading">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/">Incidents</Link> <span aria-hidden="true">/</span> <span>#{incident.id}</span>
      </nav>

      {flash && <Alert tone="success" onDismiss={dismiss}>{flash}</Alert>}
      {notice && <Alert tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</Alert>}

      <div className="page-header">
        <div>
          <p className="eyebrow">Incident #{incident.id}</p>
          <h1 id="incident-heading" className="incident-title">{incident.title}</h1>
          <div className="badge-row">
            <SeverityBadge severity={incident.severity} />
            <StatusBadge status={incident.status} />
          </div>
        </div>
        {!editing && (
          <button type="button" className="button button-secondary" onClick={startEditing}>Edit details</button>
        )}
      </div>

      <div className="detail-layout">
        <div className="card">
          {editing && values ? (
            <form className="form" noValidate onSubmit={handleEditSubmit} aria-label="Edit incident details">
              <h2>Edit details</h2>
              <IncidentFields idPrefix="edit" values={values} errors={errors} disabled={saving === 'details'} onChange={setValues} />
              <div className="form-actions">
                <button type="button" className="button button-ghost" disabled={saving === 'details'} onClick={() => { setEditing(false); setErrors({}); }}>
                  Cancel
                </button>
                <button type="submit" className="button button-primary" disabled={saving === 'details'}>
                  {saving === 'details' ? 'Saving…' : 'Save changes'}
                </button>
              </div>
            </form>
          ) : (
            <>
              <h2>Description</h2>
              <p className="description">{incident.description}</p>
            </>
          )}
        </div>

        <aside className="detail-sidebar">
          <section className="card" aria-labelledby="status-heading">
            <h2 id="status-heading">Status</h2>
            <div className="status-options" role="group" aria-label="Change status">
              {STATUSES.map(status => (
                <button
                  key={status}
                  type="button"
                  className={`status-option status-${status}`}
                  aria-pressed={incident.status === status}
                  disabled={saving !== null}
                  onClick={() => changeStatus(status)}
                >
                  {STATUS_LABELS[status]}
                </button>
              ))}
            </div>
            {saving === 'status' && <p className="muted small" role="status">Updating status…</p>}
          </section>

          <section className="card" aria-labelledby="meta-heading">
            <h2 id="meta-heading">Details</h2>
            <dl className="meta">
              <dt>Assignee</dt>
              <dd>{incident.assignee_id ?? <span className="muted">Unassigned</span>}</dd>
              <dt>Created</dt>
              <dd><time dateTime={incident.created_at}>{formatDateTime(incident.created_at)}</time></dd>
              <dt>Last updated</dt>
              <dd><time dateTime={incident.updated_at}>{formatDateTime(incident.updated_at)}</time></dd>
            </dl>
          </section>
        </aside>
      </div>
    </article>
  );
}
