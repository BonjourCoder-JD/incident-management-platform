import { useEffect, useId, useRef, useState, type FormEvent, type Ref } from 'react';
import { ApiError, decideTriageSuggestion, getTriageStatus, listTriageSuggestions, requestTriageSuggestion } from '../api/client';
import { COMMENT_MAX_LENGTH, SEVERITIES, type Severity, type TriageStatus, type TriageSuggestion } from '../api/types';
import { formatDateTime, SEVERITY_LABELS } from '../format';
import { Link } from '../router';
import { commentLength, validateComment } from '../validation';
import { Alert, Email, LoadingState, SeverityBadge } from './ui';

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; status: TriageStatus; items: TriageSuggestion[] };

const DECISION_LABELS = { accepted: 'Accepted', accepted_with_edits: 'Accepted with edits', rejected: 'Rejected' } as const;

/** What the server stores as the note when the reviewer does not edit it (see app/triage.py default_note). */
export function defaultNote(suggestion: TriageSuggestion): string {
  const steps = suggestion.next_steps.map(step => `- ${step}`).join('\n');
  return `AI triage suggestion #${suggestion.id} (reviewed): ${suggestion.rationale ?? ''}${steps ? `\nNext steps:\n${steps}` : ''}`;
}

function formatUsd(value: number): string {
  return value < 0.01 ? `under $0.01` : `$${value.toFixed(2)}`;
}

function retryText(error: ApiError): string {
  if (error.status !== 429 || error.retryAfterSeconds === null) return error.message;
  const seconds = error.retryAfterSeconds;
  const wait = seconds < 120 ? `${seconds} seconds` : seconds < 7200 ? `${Math.ceil(seconds / 60)} minutes` : `${Math.ceil(seconds / 3600)} hours`;
  return `${error.message} You can try again in about ${wait}.`;
}

/** The label shown on every suggestion: the deterministic test provider is never called AI. */
function sourceLabel(suggestion: TriageSuggestion): string {
  return suggestion.provider === 'fake' ? 'Test suggestion (not AI)' : 'AI-generated suggestion';
}

function SentDisclosure({ status }: { status: TriageStatus }) {
  return (
    <details className="triage-disclosure">
      <summary>What is sent{status.is_ai ? ' to the AI service' : ''}</summary>
      <ul className="small">
        {status.sent_fields.map(field => <li key={field}>{field}</li>)}
      </ul>
      <p className="muted small">Comments, internal notes, people and email addresses are never sent. Use synthetic data only.</p>
      {status.typical_cost_usd !== null && status.is_ai && (
        <p className="muted small">Typical cost per suggestion: {formatUsd(status.typical_cost_usd)}.</p>
      )}
      {status.limits && (
        <p className="muted small">
          Your remaining allowance: {status.limits.user_hourly_remaining} of {status.limits.user_hourly_requests} requests this hour
          {status.is_ai && <>, {formatUsd(status.limits.user_daily_remaining_usd)} of {formatUsd(status.limits.user_daily_usd)} today</>}.
        </p>
      )}
    </details>
  );
}

function DecisionSummary({ suggestion, focus }: { suggestion: TriageSuggestion; focus: boolean }) {
  const decision = suggestion.decision!;
  const ref = useRef<HTMLParagraphElement>(null);
  useEffect(() => {
    if (focus) ref.current?.focus();
  }, [focus]);
  return (
    <p className="triage-decision small" ref={ref} tabIndex={-1}>
      <strong>{DECISION_LABELS[decision.decision]}</strong> by <Email value={decision.decider.email} /> on{' '}
      <time dateTime={decision.created_at}>{formatDateTime(decision.created_at)}</time>
      {decision.applied_severity && <> · severity set to {SEVERITY_LABELS[decision.applied_severity]}</>}
      {decision.decision !== 'rejected' && !decision.applied_severity && <> · severity already matched; nothing changed</>}
      {decision.note_comment_id !== null && <> · internal note added</>}
      {decision.reason && <> · reason: {decision.reason}</>}
    </p>
  );
}

function ReviewForm({ suggestion, onDecided }: { suggestion: TriageSuggestion; onDecided: (updated: TriageSuggestion, applied: boolean) => void }) {
  const id = useId();
  const [severity, setSeverity] = useState<Severity>(suggestion.severity!);
  const [addNote, setAddNote] = useState(false);
  const [note, setNote] = useState(() => defaultNote(suggestion));
  const [reason, setReason] = useState('');
  const [busy, setBusy] = useState<'accept' | 'reject' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [fieldErrors, setFieldErrors] = useState<Record<string, string | undefined>>({});

  async function submit(decision: 'accept' | 'reject') {
    const noteError = decision === 'accept' && addNote ? validateComment(note) : undefined;
    if (noteError) {
      setFieldErrors({ note: noteError });
      document.getElementById(`${id}-note`)?.focus();
      return;
    }
    setBusy(decision);
    setError(null);
    setFieldErrors({});
    try {
      const payload = decision === 'reject'
        ? { decision, ...(reason.trim() ? { reason: reason.trim() } : {}) }
        : {
            decision,
            severity,
            add_internal_note: addNote,
            ...(addNote && note !== defaultNote(suggestion) ? { note } : {}),
            ...(reason.trim() ? { reason: reason.trim() } : {}),
          };
      const updated = await decideTriageSuggestion(suggestion.id, payload);
      onDecided(updated, decision === 'accept');
    } catch (caught) {
      if (caught instanceof ApiError) {
        setError(caught.message);
        setFieldErrors(caught.fieldErrors);
      } else {
        setError('The decision could not be saved. Try again.');
      }
    } finally {
      setBusy(null);
    }
  }

  function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    void submit('accept');
  }

  return (
    <form className="form triage-review" noValidate onSubmit={handleSubmit} aria-label={`Review suggestion #${suggestion.id}`}>
      <p className="small triage-review-intro">Nothing changes until you accept. Review the suggestion, adjust it if needed, then accept or reject it.</p>
      {error && <Alert tone="error">{error}</Alert>}
      {!suggestion.stale && (
        <>
          <div className="field">
            <label htmlFor={`${id}-severity`}>Severity to apply</label>
            <select id={`${id}-severity`} value={severity} disabled={busy !== null} onChange={event => setSeverity(event.target.value as Severity)}>
              {SEVERITIES.map(value => <option key={value} value={value}>{SEVERITY_LABELS[value]}{value === suggestion.severity ? ' (suggested)' : ''}</option>)}
            </select>
          </div>
          <label className="checkbox-field">
            <input type="checkbox" checked={addNote} disabled={busy !== null} onChange={event => setAddNote(event.target.checked)} />
            <span>Add the rationale and next steps as an internal note <span className="muted small">(responders and admins only)</span></span>
          </label>
          {addNote && (
            <div className="field">
              <label htmlFor={`${id}-note`}>Internal note</label>
              <textarea
                id={`${id}-note`}
                rows={5}
                value={note}
                disabled={busy !== null}
                aria-invalid={Boolean(fieldErrors.note)}
                aria-describedby={`${id}-note-hint${fieldErrors.note ? ` ${id}-note-error` : ''}`}
                onChange={event => setNote(event.target.value)}
              />
              <p className={`field-hint${commentLength(note) > COMMENT_MAX_LENGTH ? ' field-hint-over' : ''}`} id={`${id}-note-hint`}>
                {commentLength(note).toLocaleString('en-US')}/{COMMENT_MAX_LENGTH.toLocaleString('en-US')} characters · visible to responders and admins only
              </p>
              {fieldErrors.note && <p className="field-error" id={`${id}-note-error`}>{fieldErrors.note}</p>}
            </div>
          )}
        </>
      )}
      <div className="field">
        <label htmlFor={`${id}-reason`}>Reason <span className="muted small">(optional)</span></label>
        <input
          id={`${id}-reason`}
          type="text"
          maxLength={500}
          value={reason}
          disabled={busy !== null}
          aria-invalid={Boolean(fieldErrors.reason)}
          onChange={event => setReason(event.target.value)}
        />
        {fieldErrors.reason && <p className="field-error">{fieldErrors.reason}</p>}
      </div>
      <div className="form-actions">
        <button type="button" className="button button-secondary" disabled={busy !== null} onClick={() => void submit('reject')}>
          {busy === 'reject' ? 'Rejecting…' : 'Reject'}
        </button>
        {!suggestion.stale && (
          <button type="submit" className="button button-primary" disabled={busy !== null}>
            {busy === 'accept' ? 'Applying…' : 'Accept and apply'}
          </button>
        )}
      </div>
    </form>
  );
}

function SuggestionCard({ suggestion, headingRef, onDecided, justDecided = false }: {
  suggestion: TriageSuggestion;
  headingRef?: Ref<HTMLHeadingElement>;
  justDecided?: boolean;
  onDecided: (updated: TriageSuggestion, applied: boolean) => void;
}) {
  const valid = suggestion.outcome === 'valid';
  return (
    <div className={`triage-suggestion triage-${suggestion.outcome}`}>
      <h3 className="triage-suggestion-heading" ref={headingRef} tabIndex={-1}>
        <span className={`badge ${suggestion.provider === 'fake' ? 'triage-badge-test' : 'triage-badge-ai'}`}>{sourceLabel(suggestion)}</span>{' '}
        Suggestion #{suggestion.id}
      </h3>
      <p className="muted small">
        {suggestion.model} · prompt {suggestion.prompt_version} · requested by <Email value={suggestion.requested_by.email} /> on{' '}
        <time dateTime={suggestion.created_at}>{formatDateTime(suggestion.created_at)}</time>
      </p>
      {!valid && <Alert tone="info">{suggestion.message ?? 'No suggestion is available. Continue triage manually.'}</Alert>}
      {valid && suggestion.severity && (
        <>
          <p className="triage-severity">Suggested severity: <SeverityBadge severity={suggestion.severity} /></p>
          {/* Model output is always rendered as plain text, never as HTML or links. */}
          <p className="triage-rationale">{suggestion.rationale}</p>
          {suggestion.next_steps.length > 0 && (
            <>
              <h4 className="triage-subheading">Suggested next steps</h4>
              <ol className="triage-steps">
                {suggestion.next_steps.map((step, index) => <li key={index}>{step}</li>)}
              </ol>
            </>
          )}
          <h4 className="triage-subheading">Possible duplicates</h4>
          {suggestion.duplicates.length > 0 ? (
            <ul className="triage-duplicates">
              {suggestion.duplicates.map(candidate => (
                <li key={candidate.id}><Link to={`/incidents/${candidate.id}`}>#{candidate.id} {candidate.title}</Link></li>
              ))}
            </ul>
          ) : (
            <p className="muted small">None identified{suggestion.candidates_sent.length ? ` among ${suggestion.candidates_sent.length} similar incidents checked` : ''}.</p>
          )}
          {suggestion.decision ? (
            <DecisionSummary suggestion={suggestion} focus={justDecided} />
          ) : (
            <>
              {suggestion.stale && (
                <Alert tone="info">The incident changed after this suggestion was made, so it can no longer be accepted. Request a new suggestion.</Alert>
              )}
              <ReviewForm suggestion={suggestion} onDecided={onDecided} />
            </>
          )}
        </>
      )}
    </div>
  );
}

/**
 * Optional triage suggestions for responders and admins. A suggestion never changes the incident;
 * accepting one applies the chosen severity through the normal audited update.
 * `refreshKey` reloads it after other edits (which can make a suggestion stale).
 */
export function TriagePanel({ incidentId, refreshKey, onApplied }: { incidentId: number; refreshKey: number; onApplied: (noteAdded: boolean) => void }) {
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [retryKey, setRetryKey] = useState(0);
  const [requesting, setRequesting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [announcement, setAnnouncement] = useState('');
  const [justDecided, setJustDecided] = useState<number | null>(null);
  const latestHeading = useRef<HTMLHeadingElement>(null);
  const focusLatest = useRef(false);

  useEffect(() => {
    const controller = new AbortController();
    setState(current => (current.kind === 'ready' ? current : { kind: 'loading' }));
    Promise.all([getTriageStatus(controller.signal), listTriageSuggestions(incidentId, controller.signal)])
      .then(([status, page]) => setState({ kind: 'ready', status, items: page.items }))
      .catch(caught => {
        if (controller.signal.aborted) return;
        setState({ kind: 'error', message: caught instanceof ApiError ? caught.message : 'Unable to load triage suggestions.' });
      });
    return () => controller.abort();
  }, [incidentId, refreshKey, retryKey]);

  useEffect(() => {
    if (focusLatest.current && latestHeading.current) {
      focusLatest.current = false;
      latestHeading.current.focus();
    }
  });

  async function requestSuggestion() {
    setRequesting(true);
    setError(null);
    setAnnouncement('Requesting a suggestion…');
    try {
      const suggestion = await requestTriageSuggestion(incidentId);
      setState(current => (current.kind === 'ready' ? { ...current, items: [suggestion, ...current.items] } : current));
      setJustDecided(null);
      setAnnouncement(suggestion.outcome === 'valid' ? `Suggestion #${suggestion.id} is ready for review.` : (suggestion.message ?? 'No suggestion is available.'));
      focusLatest.current = true;
      // Refresh the remaining allowance; a failure here leaves the earlier figures in place.
      getTriageStatus()
        .then(status => setState(current => (current.kind === 'ready' ? { ...current, status } : current)))
        .catch(() => undefined);
    } catch (caught) {
      const message = caught instanceof ApiError ? retryText(caught) : 'The suggestion could not be requested. Try again.';
      setError(message);
      setAnnouncement('');
    } finally {
      setRequesting(false);
    }
  }

  function handleDecided(updated: TriageSuggestion, applied: boolean) {
    setState(current => (current.kind === 'ready' ? { ...current, items: current.items.map(item => (item.id === updated.id ? updated : item)) } : current));
    setAnnouncement(applied ? `Suggestion #${updated.id} accepted.` : `Suggestion #${updated.id} rejected.`);
    setJustDecided(updated.id);
    if (applied) onApplied(updated.decision?.note_comment_id != null);
  }

  return (
    <section className="card triage" aria-labelledby="triage-heading">
      <h2 id="triage-heading">Triage assistant</h2>
      {state.kind === 'loading' && <LoadingState label="Loading triage…" />}
      {state.kind === 'error' && (
        <Alert
          tone="error"
          title="Triage could not be loaded"
          action={<button type="button" className="button button-secondary button-small" onClick={() => setRetryKey(key => key + 1)}>Retry</button>}
        >
          {state.message}
        </Alert>
      )}
      {state.kind === 'ready' && (
        <>
          {state.status.enabled ? (
            <>
              <p className="muted small">
                {state.status.is_ai
                  ? `Suggestions come from ${state.status.provider_label ?? 'an AI model'}. They can be wrong: review every suggestion before accepting it.`
                  : `Suggestions come from ${state.status.provider_label ?? 'a test provider'}: fixed keyword rules for testing and demos, not AI.`}
              </p>
              <SentDisclosure status={state.status} />
              {error && <Alert tone="error" onDismiss={() => setError(null)}>{error}</Alert>}
              <div className="form-actions triage-actions">
                <button type="button" className="button button-secondary" disabled={requesting} onClick={() => void requestSuggestion()}>
                  {requesting ? 'Requesting…' : 'Suggest triage'}
                </button>
              </div>
            </>
          ) : (
            <p className="muted small">{state.status.reason ?? 'Triage suggestions are unavailable.'}</p>
          )}
          {state.items.length > 0 && (
            <>
              <SuggestionCard suggestion={state.items[0]} headingRef={latestHeading} onDecided={handleDecided} justDecided={justDecided === state.items[0].id} />
              {state.items.length > 1 && (
                <details className="triage-earlier">
                  <summary>Earlier suggestions ({state.items.length - 1})</summary>
                  {state.items.slice(1).map(item => <SuggestionCard key={item.id} suggestion={item} onDecided={handleDecided} justDecided={justDecided === item.id} />)}
                </details>
              )}
            </>
          )}
        </>
      )}
      <p className="visually-hidden" role="status">{announcement}</p>
    </section>
  );
}
