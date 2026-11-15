import type { ReactNode } from 'react';
import type { IncidentStatus, Severity } from '../api/types';
import { SEVERITY_LABELS, STATUS_LABELS } from '../format';

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`badge severity-${severity}`}>
      <span className="badge-dot" aria-hidden="true" />
      {SEVERITY_LABELS[severity]}
    </span>
  );
}

/** An email address that wraps after the @ rather than mid-word on narrow screens. */
export function Email({ value }: { value: string }) {
  const at = value.indexOf('@');
  if (at < 1) return <>{value}</>;
  return <span className="email">{value.slice(0, at)}<wbr />{value.slice(at)}</span>;
}

export function StatusBadge({ status }: { status: IncidentStatus }) {
  return <span className={`badge status-${status}`}>{STATUS_LABELS[status]}</span>;
}

type AlertTone = 'error' | 'success' | 'info';

export function Alert({ tone, title, children, action, onDismiss }: {
  tone: AlertTone;
  title?: string;
  children?: ReactNode;
  action?: ReactNode;
  onDismiss?: () => void;
}) {
  // Errors interrupt screen readers; success and info messages wait politely.
  return (
    <div className={`alert alert-${tone}`} role={tone === 'error' ? 'alert' : 'status'}>
      <div className="alert-body">
        {title && <p className="alert-title">{title}</p>}
        {children && <div className="alert-text">{children}</div>}
      </div>
      {action}
      {onDismiss && (
        <button type="button" className="button button-ghost button-small" onClick={onDismiss} aria-label="Dismiss message">
          ✕
        </button>
      )}
    </div>
  );
}

export function LoadingState({ label }: { label: string }) {
  return (
    <div className="state" role="status" aria-live="polite">
      <span className="spinner" aria-hidden="true" />
      <p>{label}</p>
    </div>
  );
}

export function EmptyState({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="state state-empty">
      <p className="state-title">{title}</p>
      {children && <p className="muted">{children}</p>}
      {action}
    </div>
  );
}
