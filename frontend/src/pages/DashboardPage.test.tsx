import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { AnalyticsSummary } from '../api/types';
import { browserTimeZone } from './DashboardPage';
import { deferred, jsonResponse, makeUser, mockApi, renderAt } from '../test/helpers';

function summary(overrides: Partial<AnalyticsSummary> = {}): AnalyticsSummary {
  return {
    scope: 'all',
    range: { start_date: '2026-10-17', end_date: '2026-11-15', timezone: 'UTC', bucket: 'day' },
    totals: { incidents: 12, open: 5, resolved_or_closed: 7 },
    by_status: [
      { status: 'open', count: 3 }, { status: 'investigating', count: 2 }, { status: 'resolved', count: 4 }, { status: 'closed', count: 3 },
    ],
    by_severity: [
      { severity: 'low', count: 2 }, { severity: 'medium', count: 4 }, { severity: 'high', count: 5 }, { severity: 'critical', count: 1 },
    ],
    open_backlog_by_severity: [
      { severity: 'low', count: 1 }, { severity: 'medium', count: 0 }, { severity: 'high', count: 6 }, { severity: 'critical', count: 2 },
    ],
    trend: [
      { period_start: '2026-11-13', period_end: '2026-11-13', created: 4, resolved: 1 },
      { period_start: '2026-11-14', period_end: '2026-11-14', created: 0, resolved: 3 },
      { period_start: '2026-11-15', period_end: '2026-11-15', created: 8, resolved: 3 },
    ],
    resolution: {
      resolved_count: 7, median_hours: 18.5, p90_hours: 70, reopened_count: 1, excluded_legacy: 2,
      by_severity: [
        { severity: 'low', resolved_count: 2, median_hours: 3 }, { severity: 'medium', resolved_count: 0, median_hours: null },
        { severity: 'high', resolved_count: 4, median_hours: 20 }, { severity: 'critical', resolved_count: 1, median_hours: 0.25 },
      ],
    },
    sla: {
      response: { met: 6, breached: 2, outstanding: 3, not_tracked: 1 },
      resolution: { met: 5, breached: 1, outstanding: 5, not_tracked: 1 },
      overdue_by_severity: [
        { severity: 'low', count: 0 }, { severity: 'medium', count: 1 }, { severity: 'high', count: 2 }, { severity: 'critical', count: 1 },
      ],
      approaching_by_severity: [
        { severity: 'low', count: 1 }, { severity: 'medium', count: 0 }, { severity: 'high', count: 1 }, { severity: 'critical', count: 0 },
      ],
      escalations: 3,
    },
    ...overrides,
  };
}

/** The value shown in the stat tile with this label. */
const tileValue = (label: string) => (screen.getByText(label).closest('.stat-tile') as HTMLElement).querySelector('.stat-value')!.textContent;

const analyticsCalls = (fetchMock: ReturnType<typeof mockApi>) =>
  fetchMock.mock.calls.map(([input]) => new URL(String(input), 'http://localhost')).filter(url => url.pathname === '/api/analytics/summary');

function isoToday(tz: string) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date());
}
const daysBetween = (from: string, to: string) => (Date.parse(`${to}T00:00:00Z`) - Date.parse(`${from}T00:00:00Z`)) / 86_400_000;

describe('Dashboard', () => {
  it('shows loading, then every figure with clear labels', async () => {
    const response = deferred();
    mockApi(async () => { await response.promise; return jsonResponse(200, summary()); });
    renderAt('/dashboard');

    expect(await screen.findByText('Loading dashboard…')).toBeInTheDocument();
    response.release();
    expect(screen.getByRole('heading', { name: 'Dashboard' })).toBeInTheDocument();
    // The heading comes from the user's role; wait for content that only exists once the data has arrived.
    await screen.findByText('Incidents in range');

    const tile = (label: string) => screen.getByText(label).closest('.stat-tile') as HTMLElement;
    expect(within(tile('Incidents in range')).getByText('12')).toBeInTheDocument();
    expect(within(tile('Open backlog now')).getByText('9')).toBeInTheDocument();
    expect(tile('Open backlog now')).toHaveTextContent('5 of them created in range');
    expect(within(tile('Resolved or closed')).getByText('7')).toBeInTheDocument();
    expect(within(tile('Median time to resolve')).getByText('18.5 h')).toBeInTheDocument();

    expect(within(tile('SLA overdue now')).getByText('4')).toBeInTheDocument();
    expect(tile('SLA overdue now')).toHaveTextContent('2 approaching');

    const status = screen.getByRole('list', { name: 'Incidents by status' });
    expect(within(status).getByText('Investigating').closest('li')).toHaveTextContent('2 · 17%');
    const backlog = screen.getByRole('region', { name: 'Current open backlog' });
    expect(within(backlog).getByText(/whatever the dates above · 9 in total/)).toBeInTheDocument();
    expect(within(backlog).getByText('High').closest('li')).toHaveTextContent('6 · 67%');

    const resolution = screen.getByRole('region', { name: 'Time to first resolution' });
    expect(within(resolution).getByText('90th percentile').nextSibling).toHaveTextContent('2.9 days');
    expect(within(resolution).getByText('Critical').closest('li')).toHaveTextContent('15 min');
    expect(within(resolution).getByText('Low').closest('li')).toHaveTextContent('3.0 h2 resolved');
    expect(within(resolution).getByText('Medium').closest('li')).toHaveTextContent('—');
    expect(within(resolution).getByText(/2 incidents were resolved in this range but created before audit logging began/)).toBeInTheDocument();
  });

  it('requests the last 30 days in the browser time zone by default', async () => {
    const fetchMock = mockApi(() => jsonResponse(200, summary()));
    renderAt('/dashboard');
    await screen.findByText('Incidents in range'); // rendered only after the response, so the request was made
    const [url] = analyticsCalls(fetchMock);
    const tz = browserTimeZone();
    expect(url.searchParams.get('tz')).toBe(tz);
    expect(url.searchParams.get('to')).toBe(isoToday(tz));
    expect(daysBetween(url.searchParams.get('from')!, url.searchParams.get('to')!)).toBe(29);
    expect(screen.getByRole('button', { name: 'Last 30 days' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('uses the browser time zone, not UTC, for the range and its days', async () => {
    const realOptions = Intl.DateTimeFormat.prototype.resolvedOptions;
    vi.spyOn(Intl.DateTimeFormat.prototype, 'resolvedOptions').mockImplementation(function (this: Intl.DateTimeFormat) {
      return { ...realOptions.call(this), timeZone: 'Asia/Tokyo' };
    });
    const fetchMock = mockApi(() => jsonResponse(200, summary()));
    renderAt('/dashboard');
    await screen.findByText('Incidents in range');
    const [url] = analyticsCalls(fetchMock);
    expect(url.searchParams.get('tz')).toBe('Asia/Tokyo');
    expect(url.searchParams.get('to')).toBe(isoToday('Asia/Tokyo'));
    expect(screen.getByText(/days in Asia\/Tokyo/)).toBeInTheDocument();
  });

  it('switches presets through the URL and keeps the previous figures while reloading', async () => {
    const second = deferred();
    let calls = 0;
    const fetchMock = mockApi(async () => {
      if (++calls === 2) { await second.promise; return jsonResponse(200, summary({ totals: { incidents: 3, open: 1, resolved_or_closed: 2 } })); }
      return jsonResponse(200, summary());
    });
    renderAt('/dashboard');
    await screen.findByText('Incidents in range');
    expect(tileValue('Incidents in range')).toBe('12');

    await userEvent.click(screen.getByRole('button', { name: 'Last 7 days' }));
    expect(window.location.search).toBe('?range=7d');
    const content = document.querySelector('.dashboard-content')!;
    expect(content).toHaveAttribute('aria-busy', 'true');
    expect(tileValue('Incidents in range')).toBe('12'); // previous figures stay visible
    second.release();
    await screen.findByText((_, element) => element?.classList.contains('stat-value') === true && element.textContent === '3');
    expect(tileValue('Incidents in range')).toBe('3');
    expect(content).toHaveAttribute('aria-busy', 'false');
    const latest = analyticsCalls(fetchMock).at(-1)!;
    expect(daysBetween(latest.searchParams.get('from')!, latest.searchParams.get('to')!)).toBe(6);
  });

  it('applies a custom range and validates it before sending', async () => {
    const fetchMock = mockApi(() => jsonResponse(200, summary()));
    renderAt('/dashboard');
    await screen.findByText('Incidents in range');

    const from = screen.getByLabelText('From');
    const to = screen.getByLabelText('To');
    await userEvent.clear(from); await userEvent.type(from, '2026-03-10');
    await userEvent.clear(to); await userEvent.type(to, '2026-03-01');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(screen.getByText('The start date must be on or before the end date.')).toBeInTheDocument();
    expect(analyticsCalls(fetchMock)).toHaveLength(1);

    await userEvent.clear(to); await userEvent.type(to, '2026-03-31');
    await userEvent.click(screen.getByRole('button', { name: 'Apply' }));
    expect(window.location.search).toBe('?from=2026-03-10&to=2026-03-31');
    await screen.findByText('Incidents in range');
    const latest = analyticsCalls(fetchMock).at(-1)!;
    expect([latest.searchParams.get('from'), latest.searchParams.get('to')]).toEqual(['2026-03-10', '2026-03-31']);
    expect(screen.getByRole('button', { name: 'Last 30 days' })).toHaveAttribute('aria-pressed', 'false');
  });

  it('shows a server range error next to the date fields', async () => {
    mockApi(() => jsonResponse(422, { detail: [{ loc: ['query', 'to'], msg: 'The range can be at most 366 days.', type: 'value_error' }] }));
    renderAt('/dashboard?from=2024-01-01&to=2026-01-01');
    expect(await screen.findByText('The range can be at most 366 days.')).toHaveAttribute('id', 'range-error');
    expect(screen.getByLabelText('From')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('shows an error with a working retry', async () => {
    let calls = 0;
    mockApi(() => (++calls === 1 ? jsonResponse(500, {}) : jsonResponse(200, summary())));
    renderAt('/dashboard');
    expect(await screen.findByRole('alert')).toHaveTextContent('The dashboard could not be loaded');
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText('Median time to resolve');
    expect(tileValue('Median time to resolve')).toBe('18.5 h');
  });

  it('shows an empty state that offers a longer range', async () => {
    const zero = summary({
      totals: { incidents: 0, open: 0, resolved_or_closed: 0 },
      trend: [],
      resolution: { ...summary().resolution, resolved_count: 0, median_hours: null, p90_hours: null, reopened_count: 0, excluded_legacy: 0 },
    });
    mockApi(() => jsonResponse(200, zero));
    renderAt('/dashboard');
    expect(await screen.findByText('No incidents in this range')).toBeInTheDocument();
    expect(screen.getByText('No incidents first resolved in range')).toBeInTheDocument();
    expect(screen.getByRole('region', { name: 'Current open backlog' })).toBeInTheDocument(); // still shown: ignores the range
    await userEvent.click(screen.getByRole('button', { name: 'Show the last 12 months' }));
    expect(window.location.search).toBe('?range=12m');
  });

  it('labels the reporter view as their own', async () => {
    mockApi(() => jsonResponse(200, summary({ scope: 'own' })), { user: makeUser('reporter') });
    renderAt('/dashboard');
    expect(await screen.findByRole('heading', { name: 'My dashboard' })).toBeInTheDocument();
    expect(screen.getByText('Figures cover only the incidents you reported.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'My dashboard' })).toHaveAttribute('aria-current', 'page');
  });

  it('shows five headline figures, each saying whether it follows the dates or is current', async () => {
    mockApi(() => jsonResponse(200, summary()));
    renderAt('/dashboard');
    await screen.findByText('Incidents in range');
    const tiles = [...document.querySelectorAll('.kpi-row .stat-tile')] as HTMLElement[];
    expect(tiles.map(t => [t.querySelector('.stat-label')!.textContent, t.querySelector('.stat-scope')!.textContent, t.querySelector('.stat-value')!.textContent])).toEqual([
      ['Incidents in range', 'Selected dates', '12'],
      ['Open backlog now', 'Right now', '9'],
      ['Resolved or closed', 'Selected dates', '7'],
      ['Median time to resolve', 'Selected dates', '18.5 h'],
      ['SLA overdue now', 'Right now', '4'],
    ]);
    // Accents decorate the labels; icons are hidden from assistive technology.
    expect(tiles.every(t => t.querySelector('svg')?.getAttribute('aria-hidden') === 'true')).toBe(true);
    expect(tiles.map(t => [...t.classList].find(c => c.startsWith('accent-')))).toEqual(['accent-blue', 'accent-orange', 'accent-green', 'accent-purple', 'accent-red']);
  });

  it('shows status as a donut whose legend carries exact counts and shares', async () => {
    mockApi(() => jsonResponse(200, summary()));
    renderAt('/dashboard');
    const ring = await screen.findByRole('img', { name: /^Incidents by status: Open 3 \(25%\), Investigating 2 \(17%\), Resolved 4 \(33%\), Closed 3 \(25%\)\. 12 incidents in total\./ });
    const legend = screen.getByRole('list', { name: 'Incidents by status' });
    expect(within(legend).getAllByRole('listitem').map(li => li.textContent)).toEqual(['Open3 · 25%', 'Investigating2 · 17%', 'Resolved4 · 33%', 'Closed3 · 25%']);
    expect(document.querySelectorAll('.donut-segment')).toHaveLength(4);

    act(() => ring.focus());
    expect(ring).toHaveTextContent('3Open · 25%');
    await userEvent.keyboard('{ArrowRight}');
    expect(ring).toHaveTextContent('2Investigating · 17%');
    expect(screen.getByText('Investigating: 2 incidents, 17%.')).toBeInTheDocument();
    await userEvent.keyboard('{ArrowLeft}{ArrowLeft}');
    expect(ring).toHaveTextContent('3Closed · 25%'); // wraps around
    await userEvent.keyboard('{Escape}');
    expect(ring).toHaveTextContent('12incidents in total');
  });

  it('leaves empty statuses out of the ring but keeps them in the legend', async () => {
    mockApi(() => jsonResponse(200, summary({
      by_status: [{ status: 'open', count: 5 }, { status: 'investigating', count: 0 }, { status: 'resolved', count: 0 }, { status: 'closed', count: 0 }],
    })));
    renderAt('/dashboard');
    await screen.findByRole('list', { name: 'Incidents by status' });
    expect(document.querySelectorAll('.donut-segment')).toHaveLength(1);
    expect(screen.getByRole('list', { name: 'Incidents by status' })).toHaveTextContent('Investigating0 · 0%');
  });

  it('colours severity bars by severity while each row is named', async () => {
    mockApi(() => jsonResponse(200, summary()));
    renderAt('/dashboard');
    const severity = await screen.findByRole('list', { name: 'Incidents by severity' });
    expect(within(severity).getByText('Critical').closest('li')).toHaveTextContent('1 · 8%');
    expect([...severity.querySelectorAll('.meter-fill')].map(f => f.className)).toEqual([
      'meter-fill tone-sev-low', 'meter-fill tone-sev-medium', 'meter-fill tone-sev-high', 'meter-fill tone-sev-critical',
    ]);
  });

  it('shows a reporter their own figures without staff-only escalations', async () => {
    const own = summary({ scope: 'own', totals: { incidents: 2, open: 1, resolved_or_closed: 1 }, sla: { ...summary().sla, escalations: null } });
    mockApi(() => jsonResponse(200, own), { user: makeUser('reporter') });
    renderAt('/dashboard');
    expect(await screen.findByRole('heading', { name: 'My dashboard' })).toBeInTheDocument();
    await screen.findByText('Incidents in range'); // the heading renders before the figures arrive
    expect(tileValue('Incidents in range')).toBe('2');
    expect(tileValue('Resolved or closed')).toBe('1');
    expect(within(screen.getByRole('region', { name: 'SLA' })).queryByText('Escalations')).toBeNull();
  });

  it('offers the trend as an accessible chart, keyboard readout and table', async () => {
    mockApi(() => jsonResponse(200, summary()));
    renderAt('/dashboard');
    const chart = await screen.findByRole('img', { name: /Incidents created and first resolved per day: 12 created and 7 resolved in total/ });
    expect(screen.getByRole('list', { name: 'Legend' })).toHaveTextContent('CreatedResolved');

    act(() => chart.focus());
    expect(document.querySelector('.chart-tooltip')).toHaveTextContent('Nov 15'); // focus starts on the latest period
    await userEvent.keyboard('{ArrowLeft}');
    const tooltip = document.querySelector('.chart-tooltip') as HTMLElement;
    expect(tooltip).toHaveTextContent('Nov 14');
    expect(tooltip).toHaveTextContent('0 created');
    expect(tooltip).toHaveTextContent('3 resolved');
    await userEvent.keyboard('{Home}');
    expect(document.querySelector('.chart-tooltip')).toHaveTextContent('4 created');

    await userEvent.click(screen.getByRole('button', { name: 'Show as table' }));
    const rows = within(screen.getByRole('table')).getAllByRole('row');
    expect(rows.map(row => row.textContent)).toEqual(['DayCreatedResolved', 'Nov 1341', 'Nov 1403', 'Nov 1583']);
  });
});
