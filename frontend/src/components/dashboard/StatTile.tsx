import type { ReactNode } from 'react';
import { DashboardIcon, type IconName } from './icons';

export type Accent = 'blue' | 'green' | 'orange' | 'purple' | 'red';

/**
 * A single headline figure: label, value, optional supporting line. `scope` says in words whether
 * the figure covers the selected dates or the current state; `accent` and `icon` are decoration
 * next to that text, never the only cue.
 */
export function StatTile({ label, value, detail, accent, icon, scope }: {
  label: string;
  value: string;
  detail?: ReactNode;
  accent?: Accent;
  icon?: IconName;
  scope?: string;
}) {
  return (
    <div className={`stat-tile${accent ? ` stat-accent accent-${accent}` : ''}`}>
      {(icon || scope) && (
        <div className="stat-top">
          {icon && <span className="stat-icon"><DashboardIcon name={icon} /></span>}
          {scope && <span className="stat-scope">{scope}</span>}
        </div>
      )}
      <p className="stat-label">{label}</p>
      <p className="stat-value">{value}</p>
      {detail && <p className="stat-detail muted small">{detail}</p>}
    </div>
  );
}
