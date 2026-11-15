import { act, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import type { IncidentSla, SlaCycle, SlaTargetStatus } from '../api/types';
import { deferred, jsonResponse, makeIncident, makeUser, mockApi, renderAt, slaCalls } from '../test/helpers';

const NOW = '2026-11-15T12:00:00Z';
const incident = makeIncident({ id: 5 });

function target(overrides: Partial<SlaTargetStatus> = {}): SlaTargetStatus {
  return { target_minutes: 60, severity: 'high', due_at: '2026-11-15T12:25:00Z', warning_at: '2026-11-15T12:10:00Z', satisfied_at: null, state: 'on_track', ...overrides };
}

function cycle(overrides: Partial<SlaCycle> = {}): SlaCycle {
  return {
    cycle: 0, started_at: '2026-11-15T11:25:00Z', policy_version_id: 1, tracked: true,
    response: target(), resolution: target({ target_minutes: 1440, due_at: '2026-11-16T11:25:00Z', state: 'on_track' }),
    ...overrides,
  };
}

function sla(overrides: Partial<IncidentSla> = {}): IncidentSla {
  return { now: NOW, tracked: true, current_cycle: 0, cycles: [cycle()], escalations: null, ...overrides };
}

function open(data: IncidentSla | ((url: URL, init: RequestInit) => Response | Promise<Response>), user = makeUser('reporter')) {
  const fetchMock = mockApi(url => (url.pathname === '/api/incidents/5' ? jsonResponse(200, incident) : jsonResponse(404, {})), { user, sla: data });
  renderAt('/incidents/5');
  return fetchMock;
}

/** The SLA card once its data has rendered (not just its loading state). */
async function card() {
  const region = await screen.findByRole('region', { name: 'SLA' });
  await within(region).findByText(/Times from the server|Not tracked|could not be loaded/);
  return region;
}

afterEach(() => {
  vi.useRealTimers();
});

describe('Incident SLA', () => {
  it('shows each target state as text with its due time relative to the server clock', async () => {
    open(sla());
    const region = await card();
    const [response, resolution] = within(region).getAllByRole('definition');
    expect(within(region).getByText('Response')).toBeInTheDocument();
    expect(response).toHaveTextContent('On track');
    expect(response).toHaveTextContent(/Due .*, in 25 min · target 1 h \(High\)$/);
    expect(resolution).toHaveTextContent(/On track.*in 23 h 25 min.*target 1 day \(High\)/);
    expect(within(region).getByText('Times from the server; updated every minute.')).toBeInTheDocument();
  });

  it('never uses the browser clock: a wrong device time changes nothing', async () => {
    vi.useFakeTimers({ toFake: ['Date'], now: new Date('2031-01-01T00:00:00Z') });
    open(sla());
    const region = await card();
    expect(within(region).getAllByRole('definition')[0]).toHaveTextContent('in 25 min');
  });

  it('describes overdue, met and breached targets', async () => {
    open(sla({
      cycles: [cycle({
        response: target({ state: 'breached', due_at: '2026-11-15T11:00:00Z', satisfied_at: '2026-11-15T11:20:00Z' }),
        resolution: target({ state: 'overdue', target_minutes: 240, due_at: '2026-11-15T11:30:00Z' }),
      })],
    }));
    const [response, resolution] = within(await card()).getAllByRole('definition');
    expect(response).toHaveTextContent(/Breached.*Breached by 20 min/);
    expect(resolution).toHaveTextContent(/Overdue.*30 min ago.*target 4 h/);
  });

  it('shows a met target with the time to spare', async () => {
    open(sla({ cycles: [cycle({ response: target({ state: 'met', satisfied_at: '2026-11-15T11:55:00Z' }) })] }));
    expect(within(await card()).getAllByRole('definition')[0]).toHaveTextContent(/Met.*30 min before the deadline/);
  });

  it('explains reopened periods and keeps earlier outcomes', async () => {
    open(sla({
      current_cycle: 1,
      cycles: [
        cycle({ response: target({ state: 'met', satisfied_at: '2026-11-14T10:05:00Z', due_at: '2026-11-14T11:00:00Z' }),
                resolution: target({ state: 'breached', due_at: '2026-11-14T12:00:00Z', satisfied_at: '2026-11-14T13:00:00Z' }) }),
        cycle({ cycle: 1, started_at: '2026-11-15T09:00:00Z', response: null, resolution: target({ state: 'approaching', due_at: '2026-11-15T13:00:00Z' }) }),
      ],
    }));
    const region = await card();
    expect(within(region).getByText(/Reopened .*: the resolution target restarted then\./)).toBeInTheDocument();
    expect(within(region).getByText('First response')).toBeInTheDocument();
    expect(within(region).getAllByRole('definition')[1]).toHaveTextContent('Approaching');
    await userEvent.click(within(region).getByText('Earlier periods (1)'));
    expect(within(region).getByText(/Original report: resolution breached, response met/)).toBeInTheDocument();
  });

  it('shows escalations to staff, attributed to the system', async () => {
    open(sla({ escalations: [{ id: 1, cycle: 0, target: 'response', deadline_at: '2026-11-15T11:00:00Z', created_at: '2026-11-15T11:01:00Z', triggered_by: 'system' }] }), makeUser('responder'));
    const region = await card();
    expect(within(region).getByRole('heading', { name: 'Escalations' })).toBeInTheDocument();
    expect(within(region).getByText(/Response target overdue: escalated automatically by the system/)).toBeInTheDocument();
  });

  it('never shows escalations to reporters', async () => {
    open(sla({ escalations: null }));
    const region = await card();
    expect(within(region).queryByText(/Escalation/)).toBeNull();
  });

  it('explains untracked incidents', async () => {
    open(sla({ tracked: false, cycles: [cycle({ tracked: false, response: target({ state: 'not_tracked' }), resolution: target({ state: 'not_tracked' }) })] }));
    const region = await card();
    expect(within(region).getByText('Not tracked')).toBeInTheDocument();
    expect(within(region).getByText('SLA targets apply to incidents reported or reopened after SLA tracking began.')).toBeInTheDocument();
  });

  it('shows loading, then an error with a working retry', async () => {
    const response = deferred();
    let calls = 0;
    open(async () => {
      calls += 1;
      if (calls === 1) {
        await response.promise;
        return jsonResponse(500, {});
      }
      return jsonResponse(200, sla());
    });
    const region = await screen.findByRole('region', { name: 'SLA' });
    expect(within(region).getByText('Loading SLA…')).toBeInTheDocument();
    response.release();
    expect(await within(region).findByText('The SLA could not be loaded')).toBeInTheDocument();
    await userEvent.click(within(region).getByRole('button', { name: 'Retry' }));
    expect(await within(region).findByText('Response')).toBeInTheDocument();
  });

  it('refetches every minute so states stay current', async () => {
    vi.useFakeTimers({ toFake: ['setInterval', 'clearInterval'] });
    const fetchMock = open(sla());
    await card();
    const before = slaCalls(fetchMock).length;
    await act(async () => {
      vi.advanceTimersByTime(60_000);
    });
    expect(slaCalls(fetchMock).length).toBe(before + 1);
  });
});
