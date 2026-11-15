import { useEffect, useRef, useState } from 'react';
import { ApiError, downloadIncidentsCsv } from '../api/client';
import type { IncidentStatus, Severity } from '../api/types';
import { SEVERITY_LABELS, STATUS_LABELS } from '../format';
import { Alert } from './ui';

type ExportState =
  | { kind: 'idle' }
  | { kind: 'working' }
  // The browser has been handed the file; the app cannot know whether saving finished.
  | { kind: 'done'; rowCount: number | null; filename: string }
  | { kind: 'error'; message: string; tooLarge: boolean };

/** Hands a downloaded file to the browser, then releases the object URL. */
export function saveFile(blob: Blob, filename: string) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = filename;
  link.rel = 'noopener';
  link.style.display = 'none';
  document.body.appendChild(link);
  try {
    link.click();
  } finally {
    link.remove();
    // Revoke after the click has been handled; some browsers read the URL asynchronously.
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

/** "Export CSV" for the incident list, using the list's current filters. */
export function ExportCsvButton({ severity, status }: { severity?: Severity; status?: IncidentStatus }) {
  const [state, setState] = useState<ExportState>({ kind: 'idle' });
  const controller = useRef<AbortController | null>(null);

  // A filter change makes any earlier result stale; leaving the page cancels a running export.
  useEffect(() => setState({ kind: 'idle' }), [severity, status]);
  useEffect(() => () => controller.current?.abort(), []);

  const scope = [severity && SEVERITY_LABELS[severity], status && STATUS_LABELS[status]].filter(Boolean).join(', ');
  const label = scope ? `Export CSV (${scope})` : 'Export CSV (all visible incidents)';

  async function handleClick() {
    controller.current?.abort();
    controller.current = new AbortController();
    setState({ kind: 'working' });
    try {
      const file = await downloadIncidentsCsv({ severity, status }, controller.current.signal);
      saveFile(file.blob, file.filename);
      setState({ kind: 'done', rowCount: file.rowCount, filename: file.filename });
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') return;
      if (error instanceof ApiError && error.status === 401) return; // the session handler shows sign-in
      setState({
        kind: 'error',
        tooLarge: error instanceof ApiError && error.status === 413,
        message: error instanceof ApiError ? error.message : 'The export could not be created.',
      });
    }
  }

  const working = state.kind === 'working';
  return (
    <div className="export-csv">
      <button type="button" className="button button-secondary" onClick={() => void handleClick()} disabled={working} aria-label={working ? 'Preparing export…' : label}>
        {working ? 'Preparing export…' : 'Export CSV'}
      </button>
      <p className="visually-hidden" role="status" aria-live="polite">
        {working ? 'Preparing export…' : state.kind === 'done' ? `CSV download started: ${state.rowCount === null ? 'incidents' : `${state.rowCount} incident${state.rowCount === 1 ? '' : 's'}`}, file ${state.filename}.` : ''}
      </p>
      {state.kind === 'done' && (
        <p className="muted small export-result" aria-hidden="true">
          CSV download started — {state.rowCount === null ? '' : `${state.rowCount.toLocaleString()} `}incident{state.rowCount === 1 ? '' : 's'} ({state.filename})
        </p>
      )}
      {state.kind === 'error' && (
        <Alert
          tone="error"
          title={state.tooLarge ? 'Too many incidents to export' : 'Export failed'}
          action={state.tooLarge ? undefined : <button type="button" className="button button-secondary button-small" onClick={() => void handleClick()}>Retry</button>}
          onDismiss={() => setState({ kind: 'idle' })}
        >
          {state.message}
        </Alert>
      )}
    </div>
  );
}
