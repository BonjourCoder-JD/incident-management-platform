import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { AnalyticsSummary, IncidentListItem } from '../api/types';
import { apiCalls, jsonResponse, makeIncident, makeUser, mockApi, renderAt } from '../test/helpers';

function item(id: number, sla: IncidentListItem['sla'], escalated: boolean | null = null): IncidentListItem {
  return { ...makeIncident({ id, title: `Incident ${id}` }), sla, escalated };
}

function serveList(items: IncidentListItem[], user = makeUser('responder')) {
  return mockApi(url => {
    if (url.pathname === '/api/incidents') return jsonResponse(200, { items, total: items.length, limit: 10, offset: 0 });
    return jsonResponse(404, {});
  }, { user });
}

const listCalls = (fetchMock: ReturnType<typeof mockApi>) => apiCalls(fetchMock).filter(call => call.url.pathname === '/api/incidents');

describe('Incident list SLA', () => {
  it('shows each incident SLA state as text, with the due time when outstanding and an escalated badge for staff', async () => {
    serveList([
      item(3, { state: 'overdue', target: 'response', due_at: '2026-11-15T10:00:00Z' }, true),
      item(2, { state: 'met', target: 'resolution', due_at: '2026-11-15T10:00:00Z' }, false),
      item(1, null, false),
    ]);
    renderAt('/');
    const rows = (await screen.findAllByRole('row')).slice(1);
    expect(within(rows[0]).getByText('Overdue')).toBeInTheDocument();
    expect(within(rows[0]).getByText('Escalated')).toBeInTheDocument();
    expect(within(rows[0]).getByText(/Response due/)).toBeInTheDocument();
    expect(within(rows[1]).getByText('Met')).toBeInTheDocument();
    expect(within(rows[1]).queryByText(/due/)).toBeNull();
    expect(within(rows[2]).getByText('Not tracked')).toHaveClass('visually-hidden');
    expect(screen.getByRole('columnheader', { name: 'SLA' })).toBeInTheDocument();
  });

  it('keeps the SLA filter in the URL and sends it to the API', async () => {
    const fetchMock = serveList([]);
    renderAt('/');
    await userEvent.selectOptions(await screen.findByLabelText('SLA'), 'overdue');
    expect(window.location.search).toBe('?sla=overdue');
    expect(listCalls(fetchMock).at(-1)!.url.searchParams.get('sla')).toBe('overdue');
    await userEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0]);
    expect(window.location.search).toBe('');
  });

  it('offers the escalated filter to staff only, and ignores it in a reporter URL', async () => {
    const fetchMock = serveList([], makeUser('reporter'));
    renderAt('/?sla=escalated');
    const select = await screen.findByLabelText('SLA');
    expect(within(select).queryByRole('option', { name: 'Escalated' })).toBeNull();
    expect(listCalls(fetchMock).at(-1)!.url.searchParams.get('sla')).toBeNull();
  });

  it('staff can filter to escalated incidents', async () => {
    const fetchMock = serveList([]);
    renderAt('/?sla=escalated');
    expect(within(await screen.findByLabelText('SLA')).getByRole('option', { name: 'Escalated' })).toBeInTheDocument();
    expect(listCalls(fetchMock).at(-1)!.url.searchParams.get('sla')).toBe('escalated');
  });
});

describe('Dashboard SLA section', () => {
  function summary(scope: 'own' | 'all', escalations: number | null): AnalyticsSummary {
    const zero = (s: 'low' | 'medium' | 'high' | 'critical') => ({ severity: s, count: 0 });
    return {
      scope,
      range: { start_date: '2026-10-17', end_date: '2026-11-15', timezone: 'UTC', bucket: 'day' },
      totals: { incidents: 4, open: 2, resolved_or_closed: 2 },
      by_status: [{ status: 'open', count: 2 }, { status: 'investigating', count: 0 }, { status: 'resolved', count: 2 }, { status: 'closed', count: 0 }],
      by_severity: (['low', 'medium', 'high', 'critical'] as const).map(zero),
      open_backlog_by_severity: (['low', 'medium', 'high', 'critical'] as const).map(zero),
      trend: [{ period_start: '2026-11-15', period_end: '2026-11-15', created: 4, resolved: 2 }],
      resolution: { resolved_count: 0, median_hours: null, p90_hours: null, reopened_count: 0, excluded_legacy: 0, by_severity: [] },
      sla: {
        response: { met: 3, breached: 1, outstanding: 2, not_tracked: 0 },
        resolution: { met: 0, breached: 0, outstanding: 4, not_tracked: 2 },
        overdue_by_severity: [{ severity: 'low', count: 0 }, { severity: 'medium', count: 1 }, { severity: 'high', count: 2 }, { severity: 'critical', count: 0 }],
        approaching_by_severity: [{ severity: 'low', count: 1 }, { severity: 'medium', count: 0 }, { severity: 'high', count: 0 }, { severity: 'critical', count: 0 }],
        escalations,
      },
    };
  }

  const tile = (region: HTMLElement, label: string) =>
    (within(region).getByText(label).closest('.stat-tile') as HTMLElement);

  it('shows on-time rates, overdue counts by severity and staff escalations', async () => {
    mockApi(url => (url.pathname === '/api/analytics/summary' ? jsonResponse(200, summary('all', 5)) : jsonResponse(404, {})));
    renderAt('/dashboard');
    const region = await screen.findByRole('region', { name: 'SLA' });
    expect(tile(region, 'Responses on time')).toHaveTextContent(/75%.*3 of 4 on time · 2 outstanding/);
    expect(tile(region, 'Resolutions on time')).toHaveTextContent(/—.*0 of 0 on time/);
    expect(tile(region, 'Overdue now')).toHaveTextContent(/3.*1 approaching the deadline/);
    expect(tile(region, 'Escalations')).toHaveTextContent('5');
    expect(within(region).getByRole('list', { name: 'Overdue now by severity' })).toHaveTextContent(/High.*2/);
    expect(within(region).getByText('2 incidents in range started before SLA tracking and are not counted.')).toBeInTheDocument();
  });

  it('never shows escalations to reporters', async () => {
    mockApi(url => (url.pathname === '/api/analytics/summary' ? jsonResponse(200, summary('own', null)) : jsonResponse(404, {})), { user: makeUser('reporter') });
    renderAt('/dashboard');
    const region = await screen.findByRole('region', { name: 'SLA' });
    expect(within(region).queryByText('Escalations')).toBeNull();
  });
});
