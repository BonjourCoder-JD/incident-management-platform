import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { DemoInfo } from './api/types';
import { jsonResponse, makeUser, mockApi, renderAt } from './test/helpers';

const DEMO: DemoInfo = {
  enabled: true,
  reset_time_utc: '04:00',
  accounts: [
    { role: 'reporter', email: 'demo.reporter@example.com', password: 'demo-reporter-2026' },
    { role: 'responder', email: 'demo.responder@example.com', password: 'demo-responder-2026' },
  ],
};

describe('Public demo', () => {
  it('shows nothing outside the demo', async () => {
    mockApi(undefined, { user: null });
    renderAt('/');
    await screen.findByRole('heading', { name: 'Sign in' });
    expect(screen.queryByRole('note', { name: 'Public demo' })).toBeNull();
    expect(screen.queryByRole('heading', { name: 'Public demo accounts' })).toBeNull();
    expect(screen.getByText('Use the account your administrator created for you.')).toBeInTheDocument();
  });

  it('explains the demo on the sign-in page and fills in a published account', async () => {
    const fetchMock = mockApi((url, init) => {
      if (url.pathname === '/api/auth/login') {
        return jsonResponse(200, { user: makeUser('responder', { email: 'demo.responder@example.com' }), csrf_token: 't' });
      }
      return jsonResponse(404, {});
    }, { user: null, demo: DEMO });
    renderAt('/');
    const banner = await screen.findByRole('note', { name: 'Public demo' });
    expect(banner).toHaveTextContent('All data is synthetic. AI triage is simulated by rule-based test suggestions; no AI service is called. Everything resets daily at 04:00 UTC.');
    const accounts = screen.getByRole('region', { name: 'Public demo accounts' });
    expect(within(accounts).getAllByRole('listitem').map(li => li.textContent)).toEqual([
      expect.stringContaining('ReporterEmail: demo.reporter@example.comPassword: demo-reporter-2026'),
      expect.stringContaining('ResponderEmail: demo.responder@example.comPassword: demo-responder-2026'),
    ]);
    await userEvent.click(within(accounts).getByRole('button', { name: 'Use the responder account' }));
    expect(screen.getByLabelText('Email')).toHaveValue('demo.responder@example.com');
    expect(screen.getByLabelText('Password')).toHaveValue('demo-responder-2026');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    await waitFor(() => {
      const login = fetchMock.mock.calls.find(([input]) => String(input).endsWith('/api/auth/login'));
      expect(JSON.parse(String(login![1]!.body))).toEqual({ email: 'demo.responder@example.com', password: 'demo-responder-2026' });
    });
  });

  it('keeps the banner on every page after signing in', async () => {
    mockApi(url => (url.pathname === '/api/incidents' ? jsonResponse(200, { items: [], total: 0, limit: 20, offset: 0 }) : jsonResponse(404, {})),
      { user: makeUser('reporter', { email: 'demo.reporter@example.com' }), demo: DEMO });
    renderAt('/');
    expect(await screen.findByRole('note', { name: 'Public demo' })).toBeInTheDocument();
  });

  it('shows nothing when the demo information cannot be loaded', async () => {
    mockApi(undefined, { user: null, demo: 'error' });
    renderAt('/');
    await screen.findByRole('heading', { name: 'Sign in' });
    expect(screen.queryByRole('note')).toBeNull();
  });

  it('shows the server refusal when a demo account tries to change its password', async () => {
    mockApi(url => (url.pathname === '/api/auth/password'
      ? jsonResponse(403, { detail: 'The password of a public demo account cannot be changed.' }) : jsonResponse(404, {})),
    { user: makeUser('reporter', { email: 'demo.reporter@example.com' }), demo: DEMO });
    renderAt('/account');
    await userEvent.type(await screen.findByLabelText('Current password'), 'demo-reporter-2026');
    await userEvent.type(screen.getByLabelText('New password'), 'another-long-passphrase');
    await userEvent.type(screen.getByLabelText('Confirm new password'), 'another-long-passphrase');
    await userEvent.click(screen.getByRole('button', { name: 'Change password' }));
    expect(await screen.findByText('The password of a public demo account cannot be changed.')).toBeInTheDocument();
  });
});
