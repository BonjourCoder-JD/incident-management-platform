import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { SlaPolicy } from '../api/types';
import { apiCalls, headerOf, jsonResponse, makeUser, mockApi, renderAt, TEST_CSRF } from '../test/helpers';

const DEFAULT: SlaPolicy = {
  id: 1, source: 'default', reason: 'Default policy (migration 0007)', author: null, created_at: '2026-11-15T09:00:00Z',
  targets: [
    { severity: 'low', response_minutes: 1440, resolution_minutes: 10080 },
    { severity: 'medium', response_minutes: 240, resolution_minutes: 4320 },
    { severity: 'high', response_minutes: 60, resolution_minutes: 1440 },
    { severity: 'critical', response_minutes: 15, resolution_minutes: 240 },
  ],
};

function serve(versions: SlaPolicy[] = [DEFAULT], create?: (body: unknown) => Response) {
  const stored = [...versions];
  return mockApi((url, init) => {
    if (url.pathname === '/api/sla/policy') return jsonResponse(200, stored[0]);
    if (url.pathname === '/api/sla/policy/versions' && init.method === 'POST') {
      const body = JSON.parse(String(init.body));
      if (create) return create(body);
      const created: SlaPolicy = { ...body, id: stored.length + 1, source: 'admin', author: { id: 301, email: 'admin@example.com' }, created_at: '2026-11-15T13:00:00Z' };
      stored.unshift(created);
      return jsonResponse(201, created);
    }
    if (url.pathname === '/api/sla/policy/versions') return jsonResponse(200, stored);
    return jsonResponse(404, {});
  }, { user: makeUser('admin') });
}

async function openPage() {
  renderAt('/sla');
  return screen.findByRole('heading', { name: 'SLA policy', level: 1 });
}

const field = (severity: string, kind: 'Response' | 'Resolution') =>
  within(screen.getByRole('group', { name: severity })).getByLabelText(kind);

describe('SLA policy page', () => {
  it('shows the current targets and the version history', async () => {
    serve();
    await openPage();
    const current = screen.getByRole('table', { name: 'Current SLA targets' });
    expect(within(current).getByRole('row', { name: /Critical 15 min 4 h/ })).toBeInTheDocument();
    expect(within(current).getByRole('row', { name: /Low 1 day 7 days/ })).toBeInTheDocument();
    expect(screen.getByText(/Version #1 · Default policy/, { selector: 'p' })).toBeInTheDocument();
    expect(within(screen.getByRole('list', { name: 'Policy versions, newest first' })).getAllByRole('listitem')).toHaveLength(1);
  });

  it('validates like the server before anything is sent', async () => {
    const fetchMock = serve();
    await openPage();
    await userEvent.clear(field('High', 'Response'));
    await userEvent.type(field('High', 'Response'), '2000');
    await userEvent.clear(field('Medium', 'Resolution'));
    await userEvent.type(field('Medium', 'Resolution'), '1.5');
    await userEvent.click(screen.getByRole('button', { name: 'Review change' }));
    expect(field('High', 'Response')).toHaveAccessibleDescription(/The response target cannot be longer than the resolution target\./);
    expect(field('Medium', 'Resolution')).toHaveAccessibleDescription(/Enter a whole number of minutes\./);
    expect(screen.getByLabelText('Reason for the change')).toHaveAccessibleDescription(/Enter a reason for the change\./);
    expect(field('High', 'Response')).toHaveFocus();
    expect(apiCalls(fetchMock).filter(call => call.init.method === 'POST')).toEqual([]);
  });

  it('refuses a stricter severity with a longer target', async () => {
    serve();
    await openPage();
    await userEvent.clear(field('Critical', 'Response'));
    await userEvent.type(field('Critical', 'Response'), '90');
    await userEvent.type(screen.getByLabelText('Reason for the change'), 'Testing the ordering rule');
    await userEvent.click(screen.getByRole('button', { name: 'Review change' }));
    expect(screen.getByRole('alert')).toHaveTextContent('A more severe incident cannot have a longer target than a less severe one.');
  });

  it('asks for confirmation, then creates a version with the CSRF token', async () => {
    const fetchMock = serve();
    await openPage();
    await userEvent.clear(field('High', 'Response'));
    await userEvent.type(field('High', 'Response'), '30');
    await userEvent.type(screen.getByLabelText('Reason for the change'), '  Faster first response for high severity  ');
    await userEvent.click(screen.getByRole('button', { name: 'Review change' }));

    const review = screen.getByRole('group', { name: 'Confirm the new version' });
    expect(within(review).getByRole('heading', { name: 'Confirm the new version' })).toHaveFocus();
    expect(review).toHaveTextContent('High: response 1 h → 30 min, resolution 1 day → 1 day');
    expect(review).toHaveTextContent('Existing deadlines do not change.');
    expect(apiCalls(fetchMock).filter(call => call.init.method === 'POST')).toEqual([]);

    await userEvent.click(within(review).getByRole('button', { name: 'Create version' }));
    expect(await screen.findByText('Policy version #2 is now in force for newly started SLA periods.')).toBeInTheDocument();
    const [post] = apiCalls(fetchMock).filter(call => call.init.method === 'POST');
    expect(headerOf(post.init, 'X-CSRF-Token')).toBe(TEST_CSRF);
    const body = JSON.parse(String(post.init.body));
    expect(body.reason).toBe('Faster first response for high severity');
    expect(body.targets).toContainEqual({ severity: 'high', response_minutes: 30, resolution_minutes: 1440 });
    expect(body.targets).toHaveLength(4);
    expect(await screen.findByText(/Version #2 · Changed by/, { selector: 'p' })).toBeInTheDocument();
  });

  it('going back from the review keeps the values and sends nothing', async () => {
    const fetchMock = serve();
    await openPage();
    await userEvent.type(screen.getByLabelText('Reason for the change'), 'Second thoughts');
    await userEvent.click(screen.getByRole('button', { name: 'Review change' }));
    await userEvent.click(screen.getByRole('button', { name: 'Back' }));
    expect(screen.getByLabelText('Reason for the change')).toHaveValue('Second thoughts');
    expect(apiCalls(fetchMock).filter(call => call.init.method === 'POST')).toEqual([]);
  });

  it('shows a server refusal', async () => {
    serve([DEFAULT], () => jsonResponse(403, { detail: 'You do not have permission to do this.' }));
    await openPage();
    await userEvent.type(screen.getByLabelText('Reason for the change'), 'Will be refused');
    await userEvent.click(screen.getByRole('button', { name: 'Review change' }));
    await userEvent.click(screen.getByRole('button', { name: 'Create version' }));
    expect(await screen.findByText('You do not have permission to do this.')).toBeInTheDocument();
  });

  it('is an admin-only page with an admin-only link', async () => {
    mockApi(() => jsonResponse(404, {}), { user: makeUser('responder') });
    renderAt('/sla');
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'SLA policy' })).toBeNull();
  });

  it('admins see the navigation link', async () => {
    serve();
    await openPage();
    expect(screen.getByRole('link', { name: 'SLA policy' })).toHaveAttribute('aria-current', 'page');
  });
});
