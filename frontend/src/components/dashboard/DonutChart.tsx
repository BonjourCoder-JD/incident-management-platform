import { useState, type KeyboardEvent } from 'react';
import { formatCount, percent } from './format';

export interface DonutSegment {
  key: string;
  label: string;
  value: number;
  /** CSS class that sets the segment colour (validated palette slots in style.css). */
  tone: string;
}

const SIZE = 168;
const RADIUS = 62;
const STROKE = 22;
const CIRCUMFERENCE = 2 * Math.PI * RADIUS;
const GAP = 2.5; // surface gap between segments, along the ring

/**
 * Part-to-whole for a few categories. The legend beside the ring is the primary reading: every
 * name with its exact count and share, always visible. The ring itself is keyboard-readable
 * (arrow keys move between segments, the centre shows the one in focus).
 */
export function DonutChart({ segments, label, unit }: { segments: DonutSegment[]; label: string; unit: string }) {
  const [active, setActive] = useState<number | null>(null);
  const total = segments.reduce((sum, s) => sum + s.value, 0);
  const shown = segments.map((s, index) => ({ ...s, index })).filter(s => s.value > 0);
  const summary = `${label}: ${segments.map(s => `${s.label} ${formatCount(s.value)} (${percent(s.value, total)})`).join(', ')}. ${formatCount(total)} ${unit} in total.`;

  let offset = 0;
  const arcs = shown.map(s => {
    const length = (s.value / total) * CIRCUMFERENCE;
    const gap = shown.length > 1 ? Math.min(GAP, length / 2) : 0;
    const arc = { ...s, dash: `${Math.max(length - gap, 0.01)} ${CIRCUMFERENCE}`, start: offset };
    offset += length;
    return arc;
  });

  function handleKey(event: KeyboardEvent<SVGSVGElement>) {
    if (shown.length === 0) return;
    const position = shown.findIndex(s => s.index === active);
    const moves: Record<string, number> = { ArrowLeft: -1, ArrowUp: -1, ArrowRight: 1, ArrowDown: 1 };
    if (event.key in moves) {
      event.preventDefault();
      const next = position === -1 ? 0 : (position + moves[event.key] + shown.length) % shown.length;
      setActive(shown[next].index);
    } else if (event.key === 'Escape') {
      setActive(null);
    }
  }

  const focus = active !== null ? segments[active] : null;
  return (
    <div className="donut">
      <svg
        className="donut-ring"
        viewBox={`0 0 ${SIZE} ${SIZE}`}
        width={SIZE}
        height={SIZE}
        role="img"
        aria-label={`${summary} Use the arrow keys to read each part.`}
        tabIndex={0}
        onKeyDown={handleKey}
        onFocus={() => setActive(current => current ?? shown[0]?.index ?? null)}
        onBlur={() => setActive(null)}
      >
        <circle className="donut-track" cx={SIZE / 2} cy={SIZE / 2} r={RADIUS} strokeWidth={STROKE} fill="none" />
        <g transform={`rotate(-90 ${SIZE / 2} ${SIZE / 2})`}>
          {arcs.map(arc => (
            <circle
              key={arc.key}
              className={`donut-segment ${arc.tone}${active === arc.index ? ' is-active' : ''}`}
              cx={SIZE / 2}
              cy={SIZE / 2}
              r={RADIUS}
              fill="none"
              strokeWidth={STROKE}
              strokeDasharray={arc.dash}
              strokeDashoffset={-arc.start}
              onPointerEnter={() => setActive(arc.index)}
              onPointerLeave={() => setActive(null)}
            />
          ))}
        </g>
        <text className="donut-value" x="50%" y="47%" textAnchor="middle" dominantBaseline="middle">
          {formatCount(focus ? focus.value : total)}
        </text>
        <text className="donut-caption" x="50%" y="62%" textAnchor="middle" dominantBaseline="middle">
          {focus ? `${focus.label} · ${percent(focus.value, total)}` : total === 0 ? `No ${unit}` : `${unit} in total`}
        </text>
      </svg>
      <ul className="donut-legend" aria-label={label}>
        {segments.map((s, index) => (
          <li key={s.key} className={active === index ? 'is-active' : undefined}>
            <span className={`donut-key ${s.tone}`} aria-hidden="true" />
            <span className="donut-name">{s.label}</span>
            <span className="donut-figure"><strong>{formatCount(s.value)}</strong><span className="muted"> · {percent(s.value, total)}</span></span>
          </li>
        ))}
      </ul>
      <p className="visually-hidden" role="status">{focus ? `${focus.label}: ${formatCount(focus.value)} ${unit}, ${percent(focus.value, total)}.` : ''}</p>
    </div>
  );
}
