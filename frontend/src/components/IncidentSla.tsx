import { useEffect, useState } from 'react';
import { ApiError, getIncidentSla } from '../api/client';
import type { IncidentSla as IncidentSlaData, SlaCycle, SlaTargetStatus } from '../api/types';
import { permissions, useCurrentUser } from '../auth';
import { formatDateTime, formatDuration, formatMinutes, SEVERITY_LABELS, SLA_STATE_LABELS, SLA_TARGET_LABELS } from '../format';
import { Alert, LoadingState, SlaBadge } from './ui';

/** States come from the server; refetched this often so they stay current. */
export const SLA_REFRESH_MS = 60_000;
/** Relative times ("due in 25 min") are re-rendered this often between fetches. */
const TICK_MS = 30_000;

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; data: IncidentSlaData; serverNow: number; fetchedAt: number };

const OUTSTANDING = new Set(['on_track', 'approaching', 'overdue']);

/**
 * The server's clock at this moment: its `now` at fetch time plus monotonic time elapsed since.
 * The browser's wall clock is never used, so a wrong device clock cannot change what is shown.
 */
function serverTime(state: Extract<LoadState, { kind: 'ready' }>): number {
  return state.serverNow + (performance.now() - state.fetchedAt);
}

function detail(status: SlaTargetStatus, now: number): string {
  if (status.state === 'not_tracked' || !status.due_at) return 'Not tracked';
  const due = Date.parse(status.due_at);
  const target = status.target_minutes === null ? '' : ` · target ${formatMinutes(status.target_minutes)}${status.severity ? ` (${SEVERITY_LABELS[status.severity]})` : ''}`;
  if (OUTSTANDING.has(status.state)) {
    const relative = due > now ? `in ${formatDuration(due - now)}` : `${formatDuration(now - due)} ago`;
    return `Due ${formatDateTime(status.due_at)}, ${relative}${target}`;
  }
  const satisfied = status.satisfied_at ? Date.parse(status.satisfied_at) : due;
  if (status.state === 'met') return `Met ${formatDateTime(status.satisfied_at ?? status.due_at)}, ${formatDuration(due - satisfied)} before the deadline${target}`;
  return `Breached by ${formatDuration(satisfied - due)} (done ${formatDateTime(status.satisfied_at ?? status.due_at)})${target}`;
}

function Target({ label, status, now }: { label: string; status: SlaTargetStatus; now: number }) {
  return (
    <div className="sla-target">
      <dt>{label}</dt>
      <dd>
        <SlaBadge state={status.state} />
        <span className="sla-detail small">{detail(status, now)}</span>
      </dd>
    </div>
  );
}

function untrackedReason(data: IncidentSlaData): string {
  return data.cycles.length === 0
    ? 'This incident was created before its history was recorded, so it has no SLA targets.'
    : 'SLA targets apply to incidents reported or reopened after SLA tracking began.';
}

function EarlierCycles({ cycles }: { cycles: SlaCycle[] }) {
  return (
    <details className="sla-history">
      <summary>Earlier periods ({cycles.length})</summary>
      <ul>
        {cycles.map(cycle => (
          <li key={cycle.cycle}>
            {cycle.cycle === 0 ? 'Original report' : `Reopened ${formatDateTime(cycle.started_at)}`}: resolution{' '}
            {SLA_STATE_LABELS[cycle.resolution.state].toLowerCase()}
            {cycle.response && <>, response {SLA_STATE_LABELS[cycle.response.state].toLowerCase()}</>}
          </li>
        ))}
      </ul>
    </details>
  );
}

/** Response and resolution targets for one incident. `refreshKey` reloads it after edits. */
export function IncidentSla({ incidentId, refreshKey }: { incidentId: number; refreshKey: number }) {
  const staff = permissions.canTriage(useCurrentUser());
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [retryKey, setRetryKey] = useState(0);
  const [pollKey, setPollKey] = useState(0);
  const [, setTick] = useState(0);

  useEffect(() => {
    const controller = new AbortController();
    setState(current => (current.kind === 'ready' ? current : { kind: 'loading' }));
    getIncidentSla(incidentId, controller.signal)
      .then(data => setState({ kind: 'ready', data, serverNow: Date.parse(data.now), fetchedAt: performance.now() }))
      .catch(error => {
        if (controller.signal.aborted) return;
        setState({ kind: 'error', message: error instanceof ApiError ? error.message : 'Unable to load the SLA.' });
      });
    return () => controller.abort();
  }, [incidentId, refreshKey, retryKey, pollKey]);

  useEffect(() => {
    const poll = window.setInterval(() => setPollKey(key => key + 1), SLA_REFRESH_MS);
    const tick = window.setInterval(() => setTick(key => key + 1), TICK_MS);
    return () => {
      window.clearInterval(poll);
      window.clearInterval(tick);
    };
  }, []);

  return (
    <section className="card sla-card" aria-labelledby="sla-heading">
      <h2 id="sla-heading">SLA</h2>
      {state.kind === 'loading' && <LoadingState label="Loading SLA…" />}
      {state.kind === 'error' && (
        <Alert
          tone="error"
          title="The SLA could not be loaded"
          action={<button type="button" className="button button-secondary button-small" onClick={() => setRetryKey(key => key + 1)}>Retry</button>}
        >
          {state.message}
        </Alert>
      )}
      {state.kind === 'ready' && <SlaContent state={state} staff={staff} />}
    </section>
  );
}

function SlaContent({ state, staff }: { state: Extract<LoadState, { kind: 'ready' }>; staff: boolean }) {
  const { data } = state;
  const now = serverTime(state);
  if (!data.tracked || data.cycles.length === 0) {
    return (
      <>
        <p><SlaBadge state="not_tracked" /></p>
        <p className="muted small">{untrackedReason(data)}</p>
      </>
    );
  }
  const first = data.cycles[0];
  const current = data.cycles[data.cycles.length - 1];
  const escalations = data.escalations ?? [];
  return (
    <>
      {current.cycle > 0 && (
        <p className="muted small sla-note">Reopened {formatDateTime(current.started_at)}: the resolution target restarted then.</p>
      )}
      <dl className="sla-targets">
        {first.response && <Target label={current.cycle === 0 ? 'Response' : 'First response'} status={first.response} now={now} />}
        <Target label="Resolution" status={current.resolution} now={now} />
      </dl>
      {staff && escalations.length > 0 && (
        <div className="sla-escalations">
          <h3>Escalations</h3>
          <ul>
            {escalations.map(escalation => (
              <li key={escalation.id}>
                {SLA_TARGET_LABELS[escalation.target]} target overdue: escalated automatically by the system{' '}
                <time dateTime={escalation.created_at}>{formatDateTime(escalation.created_at)}</time>
                <span className="muted"> (deadline {formatDateTime(escalation.deadline_at)})</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {data.cycles.length > 1 && <EarlierCycles cycles={data.cycles.slice(0, -1)} />}
      <p className="muted small sla-footnote">Times from the server; updated every minute.</p>
    </>
  );
}
