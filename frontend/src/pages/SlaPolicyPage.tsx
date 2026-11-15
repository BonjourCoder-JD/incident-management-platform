import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, createSlaPolicyVersion, getSlaPolicy, listSlaPolicyVersions, type FieldErrors } from '../api/client';
import { SLA_REASON_MAX_LENGTH, type Severity, type SlaPolicy } from '../api/types';
import { Alert, Email, LoadingState } from '../components/ui';
import { formatDateTime, formatMinutes, SEVERITY_LABELS } from '../format';
import { SLA_SEVERITY_ORDER, toSlaPolicyPayload, validateSlaPolicy, type SlaPolicyFormValues } from '../validation';

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; current: SlaPolicy; versions: SlaPolicy[] };

function formFrom(policy: SlaPolicy): SlaPolicyFormValues {
  const targets = {} as SlaPolicyFormValues['targets'];
  for (const target of policy.targets) {
    targets[target.severity] = { response: String(target.response_minutes), resolution: String(target.resolution_minutes) };
  }
  return { reason: '', targets };
}

function targetOf(policy: SlaPolicy, severity: Severity) {
  return policy.targets.find(target => target.severity === severity)!;
}

function TargetsTable({ policy, caption }: { policy: SlaPolicy; caption: string }) {
  return (
    <div className="table-wrap">
      <table className="sla-table">
        <caption className="visually-hidden">{caption}</caption>
        <thead>
          <tr><th scope="col">Severity</th><th scope="col">Response</th><th scope="col">Resolution</th></tr>
        </thead>
        <tbody>
          {SLA_SEVERITY_ORDER.map(severity => {
            const target = targetOf(policy, severity);
            return (
              <tr key={severity}>
                <th scope="row">{SEVERITY_LABELS[severity]}</th>
                <td>{formatMinutes(target.response_minutes)}</td>
                <td>{formatMinutes(target.resolution_minutes)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function versionLabel(policy: SlaPolicy) {
  return policy.source === 'default' ? 'Default policy' : <>Changed by <Email value={policy.author?.email ?? 'unknown'} /></>;
}

function MinutesField({ id, label, value, error, disabled, onChange }: {
  id: string; label: string; value: string; error?: string; disabled: boolean; onChange: (value: string) => void;
}) {
  const minutes = /^\d+$/.test(value.trim()) ? Number(value.trim()) : null;
  return (
    <div className="field field-inline">
      <label htmlFor={id}>{label}</label>
      <input
        id={id}
        inputMode="numeric"
        value={value}
        disabled={disabled}
        aria-invalid={Boolean(error)}
        aria-describedby={`${id}-hint${error ? ` ${id}-error` : ''}`}
        onChange={event => onChange(event.target.value)}
      />
      <p className="field-hint" id={`${id}-hint`}>{minutes ? `Minutes (${formatMinutes(minutes)})` : 'Minutes'}</p>
      {error && <p className="field-error" id={`${id}-error`}>{error}</p>}
    </div>
  );
}

/** Admin-only: the current SLA targets, a form for a new version and the version history. */
export function SlaPolicyPage() {
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [reloadKey, setReloadKey] = useState(0);
  const [values, setValues] = useState<SlaPolicyFormValues | null>(null);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [reviewing, setReviewing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const reviewRef = useRef<HTMLHeadingElement>(null);
  const formRef = useRef<HTMLFormElement>(null);

  useEffect(() => {
    const controller = new AbortController();
    Promise.all([getSlaPolicy(controller.signal), listSlaPolicyVersions(controller.signal)])
      .then(([current, versions]) => {
        setState({ kind: 'ready', current, versions });
        setValues(formFrom(current));
      })
      .catch(error => {
        if (controller.signal.aborted) return;
        setState({ kind: 'error', message: error instanceof ApiError ? error.message : 'Unable to load the SLA policy.' });
      });
    return () => controller.abort();
  }, [reloadKey]);

  useEffect(() => {
    if (reviewing) reviewRef.current?.focus();
  }, [reviewing]);

  if (state.kind === 'loading') return <LoadingState label="Loading SLA policy…" />;
  if (state.kind === 'error') {
    return (
      <Alert tone="error" title="The SLA policy could not be loaded"
        action={<button type="button" className="button button-secondary button-small" onClick={() => setReloadKey(k => k + 1)}>Retry</button>}>
        {state.message}
      </Alert>
    );
  }
  const { current, versions } = state;
  const form = values ?? formFrom(current);
  const setTarget = (severity: Severity, field: 'response' | 'resolution', value: string) =>
    setValues({ ...form, targets: { ...form.targets, [severity]: { ...form.targets[severity], [field]: value } } });

  function review(event: FormEvent) {
    event.preventDefault();
    setNotice(null);
    const problems = validateSlaPolicy(form);
    setErrors(problems);
    if (Object.keys(problems).length > 0) {
      const first = Object.keys(problems).find(key => key !== 'form');
      formRef.current?.querySelector<HTMLElement>(first ? `#sla-${first}` : 'button[type="submit"]')?.focus();
      return;
    }
    setReviewing(true);
  }

  async function save() {
    setSaving(true);
    try {
      const created = await createSlaPolicyVersion(toSlaPolicyPayload(form));
      setNotice({ tone: 'success', text: `Policy version #${created.id} is now in force for newly started SLA periods.` });
      setReviewing(false);
      setErrors({});
      setReloadKey(key => key + 1);
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof ApiError ? error.message : 'The policy could not be saved.' });
      if (error instanceof ApiError) setErrors(error.fieldErrors);
      setReviewing(false);
    } finally {
      setSaving(false);
    }
  }

  const payload = toSlaPolicyPayload(form);
  const changes = payload.targets.filter(t => {
    const before = targetOf(current, t.severity);
    return before.response_minutes !== t.response_minutes || before.resolution_minutes !== t.resolution_minutes;
  });

  return (
    <section aria-labelledby="sla-policy-heading" className="sla-policy">
      <div className="page-header">
        <div>
          <h1 id="sla-policy-heading">SLA policy</h1>
          <p className="muted">Response and resolution targets per severity. Changes create a new version and apply only to SLA periods that start afterwards.</p>
        </div>
      </div>
      {notice && <Alert tone={notice.tone}>{notice.text}</Alert>}

      <section className="card" aria-labelledby="sla-current-heading">
        <h2 id="sla-current-heading">Current targets</h2>
        <p className="muted small">Version #{current.id} · {versionLabel(current)} · {formatDateTime(current.created_at)}</p>
        <TargetsTable policy={current} caption="Current SLA targets" />
      </section>

      <section className="card" aria-labelledby="sla-new-heading">
        <h2 id="sla-new-heading">New policy version</h2>
        <form ref={formRef} className="form" noValidate onSubmit={review} aria-labelledby="sla-new-heading">
          {errors.form && <p className="field-error" role="alert">{errors.form}</p>}
          <div className="sla-form-grid">
            {SLA_SEVERITY_ORDER.map(severity => (
              <fieldset key={severity} className="sla-severity" disabled={saving || reviewing}>
                <legend>{SEVERITY_LABELS[severity]}</legend>
                <MinutesField id={`sla-${severity}-response`} label="Response" value={form.targets[severity].response}
                  error={errors[`${severity}-response`]} disabled={saving || reviewing} onChange={value => setTarget(severity, 'response', value)} />
                <MinutesField id={`sla-${severity}-resolution`} label="Resolution" value={form.targets[severity].resolution}
                  error={errors[`${severity}-resolution`]} disabled={saving || reviewing} onChange={value => setTarget(severity, 'resolution', value)} />
              </fieldset>
            ))}
          </div>
          <div className="field">
            <label htmlFor="sla-reason">Reason for the change</label>
            <textarea id="sla-reason" rows={2} value={form.reason} disabled={saving || reviewing}
              aria-invalid={Boolean(errors.reason)} aria-describedby={`sla-reason-hint${errors.reason ? ' sla-reason-error' : ''}`}
              onChange={event => setValues({ ...form, reason: event.target.value })} />
            <p className="field-hint" id="sla-reason-hint">Recorded in the version history. Up to {SLA_REASON_MAX_LENGTH} characters.</p>
            {errors.reason && <p className="field-error" id="sla-reason-error">{errors.reason}</p>}
          </div>
          {!reviewing && (
            <div className="form-actions">
              <button type="submit" className="button button-primary">Review change</button>
            </div>
          )}
        </form>

        {reviewing && (
          <div className="sla-review" role="group" aria-labelledby="sla-review-heading">
            <h3 id="sla-review-heading" tabIndex={-1} ref={reviewRef}>Confirm the new version</h3>
            {changes.length === 0 ? (
              <p>The targets are unchanged; a new version will still be recorded with your reason.</p>
            ) : (
              <ul>
                {changes.map(t => {
                  const before = targetOf(current, t.severity);
                  return (
                    <li key={t.severity}>
                      {SEVERITY_LABELS[t.severity]}: response {formatMinutes(before.response_minutes)} → {formatMinutes(t.response_minutes)},
                      resolution {formatMinutes(before.resolution_minutes)} → {formatMinutes(t.resolution_minutes)}
                    </li>
                  );
                })}
              </ul>
            )}
            <p className="muted small">Existing deadlines do not change. Incidents reported or reopened from now on use the new targets.</p>
            <div className="form-actions">
              <button type="button" className="button button-ghost" disabled={saving} onClick={() => setReviewing(false)}>Back</button>
              <button type="button" className="button button-primary" disabled={saving} onClick={save}>{saving ? 'Saving…' : 'Create version'}</button>
            </div>
          </div>
        )}
      </section>

      <section className="card" aria-labelledby="sla-history-heading">
        <h2 id="sla-history-heading">Version history</h2>
        <ol className="sla-versions" aria-label="Policy versions, newest first">
          {versions.map(version => (
            <li key={version.id}>
              <details>
                <summary>
                  Version #{version.id} · {versionLabel(version)} · <time dateTime={version.created_at}>{formatDateTime(version.created_at)}</time>
                </summary>
                <p className="sla-reason-text">{version.reason}</p>
                <TargetsTable policy={version} caption={`Targets of version ${version.id}`} />
              </details>
            </li>
          ))}
        </ol>
      </section>
    </section>
  );
}
