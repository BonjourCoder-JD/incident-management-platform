import { useEffect, useState, type FormEvent } from 'react';
import { ApiError, getAnalyticsSummary } from '../api/client';
import type { AnalyticsParams, AnalyticsSummary } from '../api/types';
import { MeterList } from '../components/dashboard/MeterList';
import { SlaSummary } from '../components/dashboard/SlaSummary';
import { StatTile } from '../components/dashboard/StatTile';
import { TrendChart } from '../components/dashboard/TrendChart';
import { formatCount, formatDay, formatHours } from '../components/dashboard/format';
import { permissions, useCurrentUser } from '../auth';
import { Alert, LoadingState } from '../components/ui';
import { SEVERITY_LABELS, STATUS_LABELS } from '../format';
import { navigate } from '../router';

export const PRESETS = [
  { id: '7d', label: 'Last 7 days', days: 7 },
  { id: '30d', label: 'Last 30 days', days: 30 },
  { id: '90d', label: 'Last 90 days', days: 90 },
  { id: '12m', label: 'Last 12 months', days: 365 },
] as const;
type PresetId = (typeof PRESETS)[number]['id'];
const DEFAULT_PRESET: PresetId = '30d';
const ISO_DATE = /^\d{4}-\d{2}-\d{2}$/;

export function browserTimeZone(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
  } catch {
    return 'UTC';
  }
}

/** Today's calendar date in the viewer's time zone, as YYYY-MM-DD. */
function today(tz: string): string {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
}

function addDays(isoDate: string, days: number): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

type Selection = { kind: 'preset'; preset: PresetId } | { kind: 'custom'; from: string; to: string };

function readSelection(search: URLSearchParams): Selection {
  const from = search.get('from');
  const to = search.get('to');
  if (from && to && ISO_DATE.test(from) && ISO_DATE.test(to)) return { kind: 'custom', from, to };
  const preset = PRESETS.find(p => p.id === search.get('range'));
  return { kind: 'preset', preset: preset?.id ?? DEFAULT_PRESET };
}

function toParams(selection: Selection, tz: string): AnalyticsParams {
  if (selection.kind === 'custom') return { from: selection.from, to: selection.to, tz };
  const days = PRESETS.find(p => p.id === selection.preset)!.days;
  const end = today(tz);
  return { from: addDays(end, -(days - 1)), to: end, tz };
}

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string; fieldErrors: Partial<Record<string, string>> }
  | { kind: 'ready'; data: AnalyticsSummary; refreshing: boolean };

export function DashboardPage({ search }: { search: URLSearchParams }) {
  const ownScope = !permissions.canTriage(useCurrentUser());
  const tz = browserTimeZone();
  const selection = readSelection(search);
  const params = toParams(selection, tz);
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [reloadKey, setReloadKey] = useState(0);
  const [custom, setCustom] = useState({ from: params.from, to: params.to });
  const [customError, setCustomError] = useState<string | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    // Keep the previous figures on screen (dimmed) while a new range loads.
    setState(current => (current.kind === 'ready' ? { ...current, refreshing: true } : { kind: 'loading' }));
    getAnalyticsSummary({ from: params.from, to: params.to, tz: params.tz }, controller.signal)
      .then(data => setState({ kind: 'ready', data, refreshing: false }))
      .catch(error => {
        if (controller.signal.aborted) return;
        setState({
          kind: 'error',
          message: error instanceof ApiError ? error.message : 'Unable to load the dashboard.',
          fieldErrors: error instanceof ApiError ? error.fieldErrors : {},
        });
      });
    return () => controller.abort();
  }, [params.from, params.to, params.tz, reloadKey]);

  useEffect(() => setCustom({ from: params.from, to: params.to }), [params.from, params.to]);

  function choosePreset(preset: PresetId) {
    navigate(preset === DEFAULT_PRESET ? '/dashboard' : `/dashboard?range=${preset}`, { replace: true });
  }

  function applyCustom(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!ISO_DATE.test(custom.from) || !ISO_DATE.test(custom.to)) return setCustomError('Enter both dates.');
    if (custom.from > custom.to) return setCustomError('The start date must be on or before the end date.');
    setCustomError(null);
    navigate(`/dashboard?from=${custom.from}&to=${custom.to}`, { replace: true });
  }

  const rangeError = state.kind === 'error' ? state.fieldErrors.from ?? state.fieldErrors.to ?? state.fieldErrors.tz : undefined;

  return (
    <section aria-labelledby="dashboard-heading" className="dashboard">
      <div className="page-header">
        <div>
          <h1 id="dashboard-heading">{ownScope ? 'My dashboard' : 'Dashboard'}</h1>
          <p className="muted">
            {ownScope
              ? 'Figures cover only the incidents you reported.'
              : 'Incident volume, status and resolution times across all incidents.'}
          </p>
        </div>
      </div>

      <div className="dashboard-filters card">
        <div className="segmented" role="group" aria-label="Date range">
          {PRESETS.map(preset => (
            <button
              key={preset.id}
              type="button"
              className="segmented-option"
              aria-pressed={selection.kind === 'preset' && selection.preset === preset.id}
              onClick={() => choosePreset(preset.id)}
            >
              {preset.label}
            </button>
          ))}
        </div>
        <form className="custom-range" onSubmit={applyCustom} noValidate aria-label="Custom date range">
          <div className="field field-inline">
            <label htmlFor="range-from">From</label>
            <input id="range-from" type="date" value={custom.from} onChange={e => setCustom({ ...custom, from: e.target.value })}
              aria-invalid={Boolean(customError || rangeError)} aria-describedby={customError || rangeError ? 'range-error' : undefined} />
          </div>
          <div className="field field-inline">
            <label htmlFor="range-to">To</label>
            <input id="range-to" type="date" value={custom.to} onChange={e => setCustom({ ...custom, to: e.target.value })}
              aria-invalid={Boolean(customError || rangeError)} aria-describedby={customError || rangeError ? 'range-error' : undefined} />
          </div>
          <button type="submit" className="button button-secondary">Apply</button>
          {(customError || rangeError) && <p className="field-error range-error" id="range-error">{customError ?? rangeError}</p>}
        </form>
        <p className="muted small range-caption">
          {formatDay(params.from, true)} – {formatDay(params.to, true)} · days in {tz}
        </p>
      </div>

      {state.kind === 'loading' && <LoadingState label="Loading dashboard…" />}
      {state.kind === 'error' && !rangeError && (
        <Alert tone="error" title="The dashboard could not be loaded"
          action={<button type="button" className="button button-secondary button-small" onClick={() => setReloadKey(k => k + 1)}>Retry</button>}>
          {state.message}
        </Alert>
      )}
      {state.kind === 'ready' && <DashboardContent data={state.data} refreshing={state.refreshing} onWiden={() => choosePreset('12m')} />}
    </section>
  );
}

function DashboardContent({ data, refreshing, onWiden }: { data: AnalyticsSummary; refreshing: boolean; onWiden: () => void }) {
  const { totals, resolution } = data;
  const backlogTotal = data.open_backlog_by_severity.reduce((sum, row) => sum + row.count, 0);
  const empty = totals.incidents === 0 && resolution.resolved_count === 0 && resolution.excluded_legacy === 0;

  return (
    <div className={`dashboard-content${refreshing ? ' refreshing' : ''}`} aria-busy={refreshing}>
      {refreshing && <p className="visually-hidden" role="status">Updating figures…</p>}

      <div className="stat-row">
        <StatTile label="Incidents in range" value={formatCount(totals.incidents)} detail="Created in the selected dates" />
        <StatTile label="Still open" value={formatCount(totals.open)} detail="Of those, open or investigating" />
        <StatTile label="Resolved or closed" value={formatCount(totals.resolved_or_closed)} detail="Of those, by current status" />
        <StatTile
          label="Median time to resolve"
          value={resolution.median_hours === null ? '—' : formatHours(resolution.median_hours)}
          detail={resolution.resolved_count === 0 ? 'No incidents first resolved in range' : `${formatCount(resolution.resolved_count)} first resolved in range`}
        />
      </div>

      {empty ? (
        <div className="card state state-empty">
          <p className="state-title">No incidents in this range</p>
          <p className="muted">Try a longer period to see trends.</p>
          <button type="button" className="button button-secondary" onClick={onWiden}>Show the last 12 months</button>
        </div>
      ) : (
        <>
          <section className="card" aria-labelledby="trend-heading">
            <h2 id="trend-heading">Created and resolved per {data.range.bucket === 'day' ? 'day' : 'week'}</h2>
            <TrendChart points={data.trend} bucket={data.range.bucket} />
          </section>

          <div className="dashboard-grid">
            <section className="card" aria-labelledby="status-breakdown-heading">
              <h2 id="status-breakdown-heading">By current status</h2>
              <p className="muted small card-caption">Incidents created in the selected dates</p>
              <MeterList label="Incidents by status" total={totals.incidents}
                rows={data.by_status.map(row => ({ label: STATUS_LABELS[row.status], value: row.count }))} />
            </section>
            <section className="card" aria-labelledby="severity-breakdown-heading">
              <h2 id="severity-breakdown-heading">By severity</h2>
              <p className="muted small card-caption">Incidents created in the selected dates</p>
              <MeterList label="Incidents by severity" total={totals.incidents}
                rows={data.by_severity.map(row => ({ label: SEVERITY_LABELS[row.severity], value: row.count }))} />
            </section>
          </div>
        </>
      )}

      <div className="dashboard-grid">
        <section className="card" aria-labelledby="backlog-heading">
          <h2 id="backlog-heading">Current open backlog</h2>
          <p className="muted small card-caption">Open or investigating right now, whatever the dates above · {formatCount(backlogTotal)} in total</p>
          <MeterList label="Current open backlog by severity" total={backlogTotal}
            rows={data.open_backlog_by_severity.map(row => ({ label: SEVERITY_LABELS[row.severity], value: row.count }))} />
        </section>

        <section className="card" aria-labelledby="resolution-heading">
          <h2 id="resolution-heading">Time to first resolution</h2>
          <p className="muted small card-caption">Incidents first resolved or closed in the selected dates, measured from the audit trail</p>
          <dl className="resolution-figures">
            <div><dt>Median</dt><dd>{formatHours(resolution.median_hours)}</dd></div>
            <div><dt>90th percentile</dt><dd>{formatHours(resolution.p90_hours)}</dd></div>
            <div><dt>Resolved</dt><dd>{formatCount(resolution.resolved_count)}</dd></div>
            <div><dt>Later reopened</dt><dd>{formatCount(resolution.reopened_count)}</dd></div>
          </dl>
          <MeterList label="Median time to resolve by severity"
            rows={resolution.by_severity.map(row => ({
              label: SEVERITY_LABELS[row.severity],
              value: row.median_hours ?? 0,
              display: formatHours(row.median_hours),
              note: `${formatCount(row.resolved_count)} resolved`,
            }))} />
          {resolution.excluded_legacy > 0 && (
            <p className="coverage-note muted small">
              {formatCount(resolution.excluded_legacy)} incident{resolution.excluded_legacy === 1 ? ' was' : 's were'} resolved in this range
              but created before audit logging began, so {resolution.excluded_legacy === 1 ? 'it is' : 'they are'} not included in resolution times.
            </p>
          )}
        </section>
      </div>

      <SlaSummary sla={data.sla} staff={data.scope === 'all'} />
    </div>
  );
}
