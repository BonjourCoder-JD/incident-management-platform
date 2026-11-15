import type { IncidentStatus, Severity, SlaState, SlaTargetName } from './api/types';

export const SEVERITY_LABELS: Record<Severity, string> = {
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  critical: 'Critical',
};

export const STATUS_LABELS: Record<IncidentStatus, string> = {
  open: 'Open',
  investigating: 'Investigating',
  resolved: 'Resolved',
  closed: 'Closed',
};

const dateTimeFormat = new Intl.DateTimeFormat(undefined, { dateStyle: 'medium', timeStyle: 'short' });

export function formatDateTime(value: string): string {
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : dateTimeFormat.format(date);
}

export const SLA_STATE_LABELS: Record<SlaState, string> = {
  on_track: 'On track',
  approaching: 'Approaching',
  overdue: 'Overdue',
  met: 'Met',
  breached: 'Breached',
  not_tracked: 'Not tracked',
};

export const SLA_TARGET_LABELS: Record<SlaTargetName, string> = { response: 'Response', resolution: 'Resolution' };

/** A whole-minute duration as days, hours and minutes, largest two units: "1 day 4 h", "45 min". */
export function formatMinutes(total: number): string {
  const minutes = Math.max(0, Math.round(total));
  if (minutes < 1) return 'under 1 min';
  const days = Math.floor(minutes / 1440);
  const hours = Math.floor((minutes % 1440) / 60);
  const rest = minutes % 60;
  const parts = [days ? `${days} ${days === 1 ? 'day' : 'days'}` : '', hours ? `${hours} h` : '', rest ? `${rest} min` : ''].filter(Boolean);
  return parts.slice(0, 2).join(' ');
}

/** Milliseconds as a duration, for "due in ..." and "... ago". */
export function formatDuration(ms: number): string {
  return formatMinutes(Math.abs(ms) / 60000);
}
