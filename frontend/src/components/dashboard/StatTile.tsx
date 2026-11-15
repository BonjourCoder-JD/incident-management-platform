import type { ReactNode } from 'react';

/** A single headline figure: label, value, optional supporting line. */
export function StatTile({ label, value, detail }: { label: string; value: string; detail?: ReactNode }) {
  return (
    <div className="stat-tile">
      <p className="stat-label">{label}</p>
      <p className="stat-value">{value}</p>
      {detail && <p className="stat-detail muted small">{detail}</p>}
    </div>
  );
}
