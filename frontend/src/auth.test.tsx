import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { apiCalls, headerOf, jsonResponse, makeIncident, makeUser, mockApi, renderAt, TEST_CSRF } from './test/helpers';

const page = (items = [makeIncident()]) => ({ items, total: items.length, limit: 10, offset: 0 });

describe('Authentication', () => {
  it('shows the sign-in form when there is no session, and keeps the requested URL', async () => {
    const fetchMock = mockApi(() => jsonResponse(500, {}), { user: null });
    renderAt('/incidents/7');

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(window.location.pathname).toBe('/incidents/7');
    expect(screen.queryByRole('navigation', { name: 'Main' })).not.toBeInTheDocument();
    expect(apiCalls(fetchMock)).toHaveLength(0); // no incident data requested while signed out
  });

  it('signs in and opens the page that was requested', async () => {
    const reporter = makeUser('reporter');
    const fetchMock = mockApi((url, init) => {
      if (url.pathname === '/api/auth/login') return jsonResponse(200, { user: reporter, csrf_token: 'fresh-token' });
      if (url.pathname === '/api/incidents/7') return jsonResponse(200, makeIncident({ id: 7, title: 'My laptop was stolen' }));
      if (init.method === 'PATCH') return jsonResponse(200, makeIncident({ id: 7, title: 'My laptop was stolen (updated)' }));
      return jsonResponse(404, {});
    }, { user: null });
    renderAt('/incidents/7');

    await userEvent.type(await screen.findByLabelText('Email'), 'reporter@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'a-long-synthetic-passphrase');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('heading', { name: 'My laptop was stolen' })).toBeInTheDocument();
    const loginCall = apiCalls(fetchMock).find(call => call.url.pathname === '/api/auth/login')!;
    expect(JSON.parse(String(loginCall.init.body))).toEqual({ email: 'reporter@example.com', password: 'a-long-synthetic-passphrase' });
    expect(loginCall.init.credentials).toBe('same-origin');

    // The CSRF token from login is sent on later state-changing requests.
    await userEvent.click(screen.getByRole('button', { name: 'Edit details' }));
    await userEvent.type(screen.getByLabelText('Title'), ' (updated)');
    await userEvent.click(screen.getByRole('button', { name: 'Save changes' }));
    await screen.findByText('Incident details saved.');
    const patch = apiCalls(fetchMock).find(call => call.init.method === 'PATCH')!;
    expect(headerOf(patch.init, 'X-CSRF-Token')).toBe('fresh-token');
  });

  it('shows a generic error for wrong credentials and clears the password', async () => {
    mockApi(() => jsonResponse(401, { detail: 'Invalid email or password.' }), { user: null });
    renderAt('/');

    await userEvent.type(await screen.findByLabelText('Email'), 'someone@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'wrong-password-123');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Invalid email or password.');
    expect(screen.getByLabelText('Password')).toHaveValue('');
    expect(screen.getByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
  });

  it('explains rate limiting using Retry-After', async () => {
    mockApi(() => jsonResponse(429, { detail: 'Too many failed sign-in attempts. Try again later.' }, { 'Retry-After': '600' }), { user: null });
    renderAt('/');
    await userEvent.type(await screen.findByLabelText('Email'), 'someone@example.com');
    await userEvent.type(screen.getByLabelText('Password'), 'whatever-password');
    await userEvent.click(screen.getByRole('button', { name: 'Sign in' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Try again in 10 minutes.');
  });

  it('requires both fields before calling the API', async () => {
    const fetchMock = mockApi(undefined, { user: null });
    renderAt('/');
    await userEvent.click(await screen.findByRole('button', { name: 'Sign in' }));
    expect(screen.getByRole('alert')).toHaveTextContent('Enter your email and password.');
    expect(apiCalls(fetchMock)).toHaveLength(0);
  });

  it('signs out with the CSRF token and returns to the sign-in form', async () => {
    const fetchMock = mockApi(() => jsonResponse(200, page()));
    renderAt('/');
    await screen.findByRole('link', { name: 'Suspicious login' });

    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.getByText('You have signed out.')).toBeInTheDocument();
    expect(screen.queryByText('Suspicious login')).not.toBeInTheDocument();
    const logoutCall = apiCalls(fetchMock).find(call => call.url.pathname === '/api/auth/logout')!;
    expect(logoutCall.init.method).toBe('POST');
    expect(headerOf(logoutCall.init, 'X-CSRF-Token')).toBe(TEST_CSRF);
  });

  it('returns to sign-in when the session expires mid-use', async () => {
    let calls = 0;
    mockApi(() => (++calls === 1 ? jsonResponse(200, page()) : jsonResponse(401, { detail: 'Authentication required.' })));
    renderAt('/');
    await screen.findByRole('link', { name: 'Suspicious login' });

    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'high');

    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(screen.getByText('Your session has ended. Sign in again to continue.')).toBeInTheDocument();
  });

  it('shows the signed-in user and role in the header', async () => {
    mockApi(() => jsonResponse(200, page()), { user: makeUser('admin') });
    renderAt('/');
    const account = await screen.findByRole('link', { name: 'Account settings for admin@example.com' });
    expect(within(account).getByText('Admin')).toBeInTheDocument();
  });
});
