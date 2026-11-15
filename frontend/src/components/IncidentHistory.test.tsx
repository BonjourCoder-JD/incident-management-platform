import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { IncidentEvent, IncidentEventPage } from '../api/types';
import { deferred, eventCalls, jsonResponse, makeIncident, makeUser, mockApi, renderAt } from '../test/helpers';

const alex = { id: 201, email: 'responder@example.com' };
const sam = { id: 202, email: 'second.responder@example.com' };
const riley = { id: 101, email: 'reporter@example.com' };

function event(id: number, action: IncidentEvent['action'], changes: IncidentEvent['changes'], actor = alex): IncidentEvent {
  return { id, action, changes, actor, created_at: `2026-11-15T10:${String(id).padStart(2, '0')}:00Z` };
}

function page(items: IncidentEvent[], extra: Partial<IncidentEventPage> = {}): IncidentEventPage {
  return { items, total: items.length, limit: 10, offset: 0, users: [alex, sam], ...extra };
}

const incident = makeIncident({ id: 5 });
const serveIncident = () => jsonResponse(200, incident);

async function openHistory() {
  renderAt('/incidents/5');
  return screen.findByRole('region', { name: 'History' });
}

describe('Incident history', () => {
  it('describes each kind of event in plain language, newest first', async () => {
    mockApi(serveIncident, {
      events: page([
        event(6, 'updated', { title: { from: 'Suspicious login', to: 'Confirmed takeover' }, severity: { from: 'high', to: 'critical' } }),
        event(5, 'assignment_changed', { assignee_id: { from: 202, to: 201 } }),
        event(4, 'assignment_changed', { assignee_id: { from: 201, to: null } }),
        event(3, 'assignment_changed', { assignee_id: { from: null, to: 201 } }),
        event(2, 'status_changed', { status: { from: 'open', to: 'investigating' } }),
        event(1, 'created', { title: { from: null, to: 'Suspicious login' }, severity: { from: null, to: 'high' }, status: { from: null, to: 'open' } }, riley),
      ]),
    });
    await openHistory();
    const items = within(await screen.findByRole('list', { name: 'Incident history, newest first' })).getAllByRole('listitem', { name: undefined });
    const entries = items.filter(item => item.classList.contains('timeline-item')).map(item => item.textContent?.replace(/\s+/g, ' ').trim());

    expect(entries[0]).toMatch(/updated 2 fields.*Title Suspicious login → to Confirmed takeover.*Severity High → to Critical/);
    expect(entries[1]).toMatch(/reassigned this incident from second\.responder@example\.com to responder@example\.com/);
    expect(entries[2]).toMatch(/unassigned responder@example\.com/);
    expect(entries[3]).toMatch(/assigned this incident to responder@example\.com/);
    expect(entries[4]).toMatch(/changed status from Open to Investigating/);
    expect(entries[5]).toMatch(/^reporter@example\.com.*reported this incident.*Severity High · Status Open/);
    expect(screen.getAllByText(/Nov 15, 2026/)).toHaveLength(6);
  });

  it('collapses description changes and shows the full before and after on request', async () => {
    const before = 'Original line one\nOriginal line two';
    mockApi(serveIncident, { events: page([event(2, 'updated', { description: { from: before, to: 'Rewritten' } })]) });
    await openHistory();
    const summary = await screen.findByText('Show description before and after');
    const details = summary.closest('details')!;
    expect(details).not.toHaveAttribute('open');
    await userEvent.click(summary);
    expect(details).toHaveAttribute('open');
    expect(within(details).getByText(/Original line one\s+Original line two/)).toBeInTheDocument();
    expect(within(details).getByText('Rewritten')).toBeInTheDocument();
  });

  it('renders HTML in audited values as text', async () => {
    const payload = '<img src=x onerror="window.__pwned=1"><b>bold</b>';
    mockApi(serveIncident, {
      events: page([event(2, 'updated', { title: { from: 'Old', to: payload }, description: { from: payload, to: 'x' } })]),
    });
    const history = await openHistory();
    await screen.findByText('updated 2 fields');
    // Once as the new title, once as the (collapsed) previous description.
    expect(within(history).getAllByText(payload)).toHaveLength(2);
    expect(history.querySelector('img, b')).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
  });

  it('shows loading, then an empty state for incidents with no recorded history', async () => {
    const response = deferred();
    mockApi(serveIncident, {
      events: async () => {
        await response.promise;
        return jsonResponse(200, page([], { users: [] }));
      },
    });
    const history = await openHistory();
    expect(within(history).getByText('Loading history…')).toBeInTheDocument();
    response.release();
    expect(await within(history).findByText('No history recorded yet.')).toBeInTheDocument();
    expect(within(history).getByText(/Earlier changes to this incident were not tracked/)).toBeInTheDocument();
  });

  it('shows an error with a working retry', async () => {
    let attempts = 0;
    mockApi(serveIncident, {
      events: () => (++attempts === 1 ? jsonResponse(500, { detail: 'boom' }) : jsonResponse(200, page([event(1, 'created', {})]))),
    });
    const history = await openHistory();
    expect(await within(history).findByRole('alert')).toHaveTextContent('History could not be loaded');
    await userEvent.click(within(history).getByRole('button', { name: 'Retry' }));
    expect(await within(history).findByText('reported this incident')).toBeInTheDocument();
  });

  it('loads older events page by page', async () => {
    const all = Array.from({ length: 13 }, (_, i) => event(13 - i, 'status_changed', { status: { from: 'open', to: 'investigating' } }));
    const fetchMock = mockApi(serveIncident, {
      events: url => {
        const offset = Number(url.searchParams.get('offset'));
        return jsonResponse(200, page(all.slice(offset, offset + 10), { total: 13, offset }));
      },
    });
    const history = await openHistory();
    expect(await within(history).findByText('Showing 10 of 13')).toBeInTheDocument();
    await userEvent.click(within(history).getByRole('button', { name: 'Show older' }));
    await within(history).findAllByText(/changed status/).then(found => expect(found).toHaveLength(13));
    expect(within(history).queryByRole('button', { name: 'Show older' })).not.toBeInTheDocument();
    expect(eventCalls(fetchMock).map(url => url.searchParams.get('offset'))).toEqual(['0', '10']);
    expect(eventCalls(fetchMock).every(url => url.pathname === '/api/incidents/5/events' && url.searchParams.get('limit') === '10')).toBe(true);
  });

  it('reloads after a change is saved on the page', async () => {
    let saved = false;
    const fetchMock = mockApi((_url, init) => {
      if (init.method === 'PATCH') { saved = true; return jsonResponse(200, { ...incident, status: 'resolved' }); }
      return serveIncident();
    }, {
      events: () => jsonResponse(200, page(saved
        ? [event(2, 'status_changed', { status: { from: 'open', to: 'resolved' } }), event(1, 'created', {})]
        : [event(1, 'created', {})])),
    });
    const history = await openHistory();
    await within(history).findByText('reported this incident');
    await userEvent.click(screen.getByRole('button', { name: 'Resolved' }));
    expect(await within(history).findByText(/changed status from/)).toHaveTextContent('changed status from Open to Resolved');
    expect(eventCalls(fetchMock)).toHaveLength(2);
  });

  it('is shown to reporters on their own incidents', async () => {
    mockApi(serveIncident, { user: makeUser('reporter'), events: page([event(1, 'created', {}, riley)]) });
    const history = await openHistory();
    expect(await within(history).findByText('reported this incident')).toBeInTheDocument();
  });
});
