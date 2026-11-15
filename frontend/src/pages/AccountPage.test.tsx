import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { apiCalls, headerOf, jsonResponse, makeUser, mockApi, renderAt, TEST_CSRF } from '../test/helpers';

async function fill(current: string, next: string, confirm = next) {
  if (current) await userEvent.type(screen.getByLabelText('Current password'), current);
  await userEvent.type(screen.getByLabelText('New password'), next);
  await userEvent.type(screen.getByLabelText('Confirm new password'), confirm);
  await userEvent.click(screen.getByRole('button', { name: 'Change password' }));
}

describe('Account page', () => {
  it('validates locally before sending', async () => {
    const fetchMock = mockApi(undefined, { user: makeUser('reporter') });
    renderAt('/account');
    await screen.findByRole('heading', { name: 'Your account' });
    await fill('', 'short', 'different');
    expect(screen.getByText('Enter your current password.')).toBeInTheDocument();
    expect(screen.getByText('Use at least 12 characters.')).toBeInTheDocument();
    expect(screen.getByText('Passwords do not match.')).toBeInTheDocument();
    expect(apiCalls(fetchMock)).toHaveLength(0);
  });

  it('changes the password with the CSRF token', async () => {
    const fetchMock = mockApi(() => new Response(null, { status: 204 }), { user: makeUser('reporter') });
    renderAt('/account');
    await screen.findByRole('heading', { name: 'Your account' });
    await fill('old-synthetic-passphrase', 'new-synthetic-passphrase');

    expect(await screen.findByText('Password changed. Your other devices have been signed out.')).toBeInTheDocument();
    const call = apiCalls(fetchMock)[0];
    expect(call.url.pathname).toBe('/api/auth/password');
    expect(call.init.method).toBe('PUT');
    expect(headerOf(call.init, 'X-CSRF-Token')).toBe(TEST_CSRF);
    expect(JSON.parse(String(call.init.body))).toEqual({ current_password: 'old-synthetic-passphrase', new_password: 'new-synthetic-passphrase' });
    expect(screen.getByLabelText('Current password')).toHaveValue('');
  });

  it('shows a wrong current password on that field', async () => {
    mockApi(() => jsonResponse(422, { detail: [{ loc: ['body', 'current_password'], msg: 'Current password is incorrect.', type: 'value_error' }] }), { user: makeUser('reporter') });
    renderAt('/account');
    await screen.findByRole('heading', { name: 'Your account' });
    await fill('wrong-synthetic-passphrase', 'new-synthetic-passphrase');
    expect(await screen.findByRole('alert')).toHaveTextContent('Current password is incorrect.');
    expect(screen.getByLabelText('Current password')).toHaveAttribute('aria-invalid', 'true');
  });
});
