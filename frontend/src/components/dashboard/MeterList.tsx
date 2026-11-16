import { formatCount, percent } from './format';

export interface MeterRow {
  label: string;
  value: number;
  /** Text shown instead of the count (e.g. a duration). */
  display?: string;
  /** Optional second line, e.g. "3 resolved". */
  note?: string;
  /** CSS class for the bar colour (e.g. a severity tone); the row's name always says what it is. */
  tone?: string;
}

/**
 * A name, a bar and a figure per row, in the rows' natural order. One hue; the track is a
 * lighter step of the same ramp. With `total`, bars show share of the whole; otherwise they
 * are scaled to the largest row.
 */
export function MeterList({ rows, total, label }: { rows: MeterRow[]; total?: number; label: string }) {
  const scale = total ?? Math.max(1, ...rows.map(row => row.value));
  return (
    <ul className="meter-list" aria-label={label}>
      {rows.map(row => {
        const width = scale > 0 ? Math.min(100, (row.value / scale) * 100) : 0;
        const figure = row.display ?? formatCount(row.value);
        const share = total !== undefined ? percent(row.value, total) : null;
        return (
          <li key={row.label} className="meter-row">
            <span className="meter-name">{row.label}</span>
            <span className={`meter-track${row.tone ? ' meter-track-neutral' : ''}`} aria-hidden="true">
              {width > 0 && <span className={`meter-fill${row.tone ? ` ${row.tone}` : ''}`} style={{ width: `${Math.max(width, 1.5)}%` }} />}
            </span>
            <span className="meter-figure">
              <strong>{figure}</strong>
              {share && <span className="muted"> · {share}</span>}
              {row.note && <span className="meter-note muted small">{row.note}</span>}
            </span>
          </li>
        );
      })}
    </ul>
  );
}
