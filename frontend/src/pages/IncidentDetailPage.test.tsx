import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { Incident } from '../api/types';
import { apiCalls, jsonResponse, makeIncident, mockApi, renderAt } from '../test/helpers';

/** In-memory incident that GET returns and PATCH updates. */
function serveIncident(initial: Incident) {
  let current = initial;
  const fetchMock = mockApi((url, init) => {
    if (url.pathname !== `/api/incidents/${initial.id}`) return jsonResponse(404, { detail: 'Incident not found' });
    if (init.method === 'PATCH') {
      current = { ...current, ...JSON.parse(String(init.body)), updated_at: '2026-11-14T12:00:00Z' };
    }
    return jsonResponse(200, current);
  });
  return fetchMock;
}

function patchBodies(fetchMock: ReturnType<typeof mockApi>) {
  return apiCalls(fetchMock).filter(call => call.init.method === 'PATCH').map(call => JSON.parse(String(call.init.body)));
}

describe('Incident detail', () => {
  it('shows the incident', async () => {
    serveIncident(makeIncident({ id: 5, title: 'Data exfiltration', description: 'Line one\nLine two', severity: 'critical', assignee_id: 3 }));
    renderAt('/incidents/5');

    expect(screen.getByText('Loading incident…')).toBeInTheDocument();
    expect(await screen.findByRole('heading', { name: 'Data exfiltration' })).toBeInTheDocument();
    expect(screen.getByText('Incident #5')).toBeInTheDocument();
    expect(screen.getByText('Critical')).toBeInTheDocument();
    expect(screen.getByText(/Line one\s+Line two/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Open' })).toHaveAttribute('aria-pressed', 'true');
  });

  it('shows a not-found state for unknown incidents', async () => {
    mockApi(() => jsonResponse(404, { detail: 'Incident not found' }));
    renderAt('/incidents/999');
    expect(await screen.findByText('Incident #999 was not found')).toBeInTheDocument();
  });

  it('treats malformed incident URLs as page not found without calling the API', async () => {
    const fetchMock = mockApi(() => jsonResponse(500, {}));
    renderAt('/incidents/abc');
    expect(screen.getByText('Page not found')).toBeInTheDocument();
    expect(apiCalls(fetchMock)).toHaveLength(0);
  });

  it('changes the status', async () => {
    const fetchMock = serveIncident(makeIncident({ id: 5 }));
    renderAt('/incidents/5');
    await screen.findByRole('heading', { name: 'Suspicious login' });

    await userEvent.click(screen.getByRole('button', { name: 'Resolved' }));

    expect(await screen.findByText('Status changed to Resolved.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Resolved' })).toHaveAttribute('aria-pressed', 'true');
    expect(patchBodies(fetchMock)).toEqual([{ status: 'resolved' }]);
  });

  it('saves only the changed fields and can clear the assignee', async () => {
    const fetchMock = serveIncident(makeIncident({ id: 5, assignee_id: 12 }));
    renderAt('/incidents/5');
    await screen.findByRole('heading', { name: 'Suspicious login' });

    await userEvent.click(screen.getByRole('button', { name: 'Edit details' }));
    const title = screen.getByLabelText('Title');
    await userEvent.clear(title);
    await userEvent.type(title, 'Confirmed account takeover');
    await userEvent.clear(screen.getByLabelText(/Assignee ID/));
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(await screen.findByText('Incident details saved.')).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Confirmed account takeover' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Save changes' })).not.toBeInTheDocument();
    expect(patchBodies(fetchMock)).toEqual([{ title: 'Confirmed account takeover', assignee_id: null }]);
  });

  it('validates edits before saving', async () => {
    const fetchMock = serveIncident(makeIncident({ id: 5 }));
    renderAt('/incidents/5');
    await screen.findByRole('heading', { name: 'Suspicious login' });

    await userEvent.click(screen.getByRole('button', { name: 'Edit details' }));
    await userEvent.clear(screen.getByLabelText('Description'));
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(screen.getByText('Enter a description.')).toBeInTheDocument();
    expect(patchBodies(fetchMock)).toHaveLength(0);
  });

  it('does not send a request when nothing changed', async () => {
    const fetchMock = serveIncident(makeIncident({ id: 5 }));
    renderAt('/incidents/5');
    await screen.findByRole('heading', { name: 'Suspicious login' });

    await userEvent.click(screen.getByRole('button', { name: 'Edit details' }));
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(await screen.findByText('No changes to save.')).toBeInTheDocument();
    expect(patchBodies(fetchMock)).toHaveLength(0);
  });

  it('shows an error when saving fails and keeps the form open', async () => {
    mockApi((_url, init) => (init.method === 'PATCH' ? jsonResponse(503, { detail: 'Database unavailable' }) : jsonResponse(200, makeIncident({ id: 5 }))));
    renderAt('/incidents/5');
    await screen.findByRole('heading', { name: 'Suspicious login' });

    await userEvent.click(screen.getByRole('button', { name: 'Edit details' }));
    await userEvent.type(screen.getByLabelText('Title'), ' (updated)');
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Database unavailable');
    expect(screen.getByRole('button', { name: 'Save changes' })).toBeEnabled();
  });
});
