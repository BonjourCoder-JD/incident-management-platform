import { useEffect, useRef, useState, type KeyboardEvent, type PointerEvent } from 'react';
import type { TrendPoint } from '../../api/types';
import { formatCount, formatPeriod } from './format';

const SERIES = [
  { key: 'created', label: 'Created', className: 'series-1' },
  { key: 'resolved', label: 'Resolved', className: 'series-2' },
] as const;

const HEIGHT = 240;
const MARGIN = { top: 16, right: 96, bottom: 30, left: 40 };
const MIN_LABEL_GAP = 16; // px between end labels; closer than this, the legend and tooltip carry identity

function niceMax(value: number): number {
  if (value <= 4) return 4;
  const magnitude = 10 ** Math.floor(Math.log10(value));
  const step = [1, 2, 2.5, 5, 10].map(m => m * magnitude).find(m => m * 4 >= value) ?? magnitude * 10;
  return step * 4;
}

/** Width of the element in CSS pixels, so text is drawn at its real size (no SVG scaling). */
function useWidth(fallback: number) {
  const ref = useRef<HTMLDivElement>(null);
  const [width, setWidth] = useState(fallback);
  useEffect(() => {
    const element = ref.current;
    if (!element || typeof ResizeObserver === 'undefined') return;
    const observer = new ResizeObserver(([entry]) => setWidth(Math.max(280, Math.round(entry.contentRect.width))));
    observer.observe(element);
    return () => observer.disconnect();
  }, []);
  return [ref, width] as const;
}

export function TrendChart({ points, bucket }: { points: TrendPoint[]; bucket: 'day' | 'week' }) {
  const [containerRef, width] = useWidth(720);
  const [active, setActive] = useState<number | null>(null);
  const [showTable, setShowTable] = useState(false);

  const innerWidth = width - MARGIN.left - MARGIN.right;
  const innerHeight = HEIGHT - MARGIN.top - MARGIN.bottom;
  const yMax = niceMax(Math.max(0, ...points.flatMap(p => [p.created, p.resolved])));
  const step = points.length > 1 ? innerWidth / (points.length - 1) : 0;
  const x = (i: number) => MARGIN.left + (points.length > 1 ? i * step : innerWidth / 2);
  const y = (v: number) => MARGIN.top + innerHeight - (v / yMax) * innerHeight;
  const ticks = [0, yMax / 4, yMax / 2, (3 * yMax) / 4, yMax];
  const last = points.length - 1;
  const totals = { created: points.reduce((s, p) => s + p.created, 0), resolved: points.reduce((s, p) => s + p.resolved, 0) };
  const endGap = last >= 0 ? Math.abs(y(points[last].created) - y(points[last].resolved)) : 0;
  const unit = bucket === 'day' ? 'day' : 'week';
  const summary = `Incidents created and first resolved per ${unit}: ${formatCount(totals.created)} created and ${formatCount(totals.resolved)} resolved in total. Use the left and right arrow keys to read each ${unit}, or show the data as a table.`;

  function indexFromPointer(event: PointerEvent<SVGRectElement>) {
    const box = event.currentTarget.ownerSVGElement!.getBoundingClientRect();
    const px = ((event.clientX - box.left) / box.width) * width;
    return Math.min(last, Math.max(0, Math.round(step ? (px - MARGIN.left) / step : 0)));
  }

  function handleKey(event: KeyboardEvent<SVGSVGElement>) {
    const moves: Record<string, number> = { ArrowLeft: -1, ArrowRight: 1 };
    if (event.key in moves) {
      event.preventDefault();
      setActive(current => Math.min(last, Math.max(0, (current ?? last) + moves[event.key])));
    } else if (event.key === 'Home' || event.key === 'End') {
      event.preventDefault();
      setActive(event.key === 'Home' ? 0 : last);
    } else if (event.key === 'Escape') {
      setActive(null);
    }
  }

  const tick = (i: number) => {
    const p = points[i];
    return bucket === 'day' ? formatPeriod(p.period_start, p.period_start) : formatPeriod(p.period_start, p.period_end);
  };
  const xTicks = points.length <= 1 ? [0] : [0, Math.floor(last / 2), last].filter((v, i, a) => a.indexOf(v) === i);
  const activePoint = active !== null ? points[active] : null;

  return (
    <div className="trend">
      <div className="trend-header">
        <ul className="legend" aria-label="Legend">
          {SERIES.map(series => (
            <li key={series.key}><span className={`legend-key ${series.className}`} aria-hidden="true" />{series.label}</li>
          ))}
        </ul>
        <button type="button" className="button button-ghost button-small" aria-pressed={showTable} onClick={() => setShowTable(v => !v)}>
          {showTable ? 'Show as chart' : 'Show as table'}
        </button>
      </div>

      {showTable ? (
        <div className="table-wrap">
          <table className="data-table">
            <caption className="visually-hidden">Incidents created and first resolved per {unit}</caption>
            <thead><tr><th scope="col">{bucket === 'day' ? 'Day' : 'Week'}</th><th scope="col">Created</th><th scope="col">Resolved</th></tr></thead>
            <tbody>
              {points.map(p => (
                <tr key={p.period_start}>
                  <th scope="row">{formatPeriod(p.period_start, p.period_end)}</th>
                  <td>{formatCount(p.created)}</td>
                  <td>{formatCount(p.resolved)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="trend-plot" ref={containerRef}>
          <svg
            width={width}
            height={HEIGHT}
            role="img"
            aria-label={summary}
            tabIndex={0}
            onKeyDown={handleKey}
            onFocus={() => setActive(current => current ?? last)}
            onBlur={() => setActive(null)}
          >
            {ticks.map(t => (
              <g key={t}>
                <line className="grid" x1={MARGIN.left} x2={MARGIN.left + innerWidth} y1={y(t)} y2={y(t)} />
                <text className="axis-text" x={MARGIN.left - 8} y={y(t)} textAnchor="end" dominantBaseline="middle">{formatCount(t)}</text>
              </g>
            ))}
            <line className="baseline" x1={MARGIN.left} x2={MARGIN.left + innerWidth} y1={y(0)} y2={y(0)} />
            {xTicks.map(i => (
              <text key={i} className="axis-text" x={x(i)} y={HEIGHT - 8} textAnchor={i === 0 ? 'start' : i === last ? 'end' : 'middle'}>{tick(i)}</text>
            ))}
            {SERIES.map(series => (
              <polyline
                key={series.key}
                className={`trend-line ${series.className}`}
                points={points.map((p, i) => `${x(i)},${y(p[series.key])}`).join(' ')}
              />
            ))}
            {last >= 0 && SERIES.map(series => (
              <circle key={series.key} className={`end-dot ${series.className}`} cx={x(last)} cy={y(points[last][series.key])} r={4} />
            ))}
            {last >= 0 && endGap >= MIN_LABEL_GAP && SERIES.map(series => (
              <text key={series.key} className="end-label" x={x(last) + 10} y={y(points[last][series.key])} dominantBaseline="middle">
                {series.label} {formatCount(points[last][series.key])}
              </text>
            ))}
            {active !== null && (
              <g aria-hidden="true">
                <line className="crosshair" x1={x(active)} x2={x(active)} y1={MARGIN.top} y2={MARGIN.top + innerHeight} />
                {SERIES.map(series => (
                  <circle key={series.key} className={`end-dot ${series.className}`} cx={x(active)} cy={y(points[active][series.key])} r={4} />
                ))}
              </g>
            )}
            <rect
              className="hit-area"
              x={MARGIN.left - step / 2}
              y={MARGIN.top}
              width={innerWidth + step}
              height={innerHeight}
              onPointerMove={event => setActive(indexFromPointer(event))}
              onPointerLeave={() => setActive(null)}
            />
          </svg>
          {activePoint && (
            <div
              className="chart-tooltip"
              role="status"
              style={{ left: Math.min(Math.max(x(active!) - 80, 0), width - 170), top: 4 }}
            >
              <p className="tooltip-title">{formatPeriod(activePoint.period_start, activePoint.period_end)}</p>
              {SERIES.map(series => (
                <p key={series.key} className="tooltip-row">
                  <span className={`tooltip-key ${series.className}`} aria-hidden="true" />
                  <strong>{formatCount(activePoint[series.key])}</strong> <span className="muted">{series.label.toLowerCase()}</span>
                </p>
              ))}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
