import type { IncidentStatus, Severity } from '../../api/types';

/** Bar colours per severity: the same tones as the severity badges, so a severity looks the same everywhere. */
export const SEVERITY_TONES: Record<Severity, string> = {
  low: 'tone-sev-low',
  medium: 'tone-sev-medium',
  high: 'tone-sev-high',
  critical: 'tone-sev-critical',
};

/** Donut colours per status: validated chart palette slots (see --viz-* in style.css), not the badge tones. */
export const STATUS_TONES: Record<IncidentStatus, string> = {
  open: 'tone-viz-open',
  investigating: 'tone-viz-investigating',
  resolved: 'tone-viz-resolved',
  closed: 'tone-viz-closed',
};
