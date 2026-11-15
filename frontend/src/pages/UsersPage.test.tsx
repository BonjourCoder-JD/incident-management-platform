import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { User } from '../api/types';
import { apiCalls, jsonResponse, makeUser, mockApi, renderAt } from '../test/helpers';
import { validateNewUser } from './UsersPage';

const admin = makeUser('admin');
const reporter = makeUser('reporter', { id: 102, email: 'jordan.reporter@example.com' });

function serveUsers(initial: User[]) {
  let users = initial;
  return mockApi((url, init) => {
    if (url.pathname === '/api/users' && init.method === 'POST') {
      const body = JSON.parse(String(init.body));
      if (users.some(user => user.email === body.email)) return jsonResponse(409, { detail: 'A user with this email already exists.' });
      const created = makeUser(body.role, { id: 500, email: body.email });
      users = [...users, created];
      return jsonResponse(201, created);
    }
    const match = /^\/api\/users\/(\d+)$/.exec(url.pathname);
    if (match && init.method === 'PATCH') {
      users = users.map(user => (user.id === Number(match[1]) ? { ...user, ...JSON.parse(String(init.body)) } : user));
      return jsonResponse(200, users.find(user => user.id === Number(match[1])));
    }
    if (url.pathname === '/api/users') return jsonResponse(200, users);
    return jsonResponse(404, {});
  }, { user: admin });
}

describe('Users page', () => {
  it('lists accounts and protects the admin’s own row', async () => {
    serveUsers([admin, reporter]);
    renderAt('/users');
    const ownRow = (await screen.findByRole('cell', { name: /admin@example\.com/ })).closest('tr')!;
    expect(within(ownRow).getByText('(you)')).toBeInTheDocument();
    expect(within(ownRow).getByRole('combobox')).toBeDisabled();
    expect(within(ownRow).queryByRole('button', { name: /Deactivate/ })).not.toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Users' })).toHaveAttribute('aria-current', 'page');
  });

  it('changes a role and deactivates a user', async () => {
    const fetchMock = serveUsers([admin, reporter]);
    renderAt('/users');
    await userEvent.selectOptions(await screen.findByLabelText('Role for jordan.reporter@example.com'), 'responder');
    expect(await screen.findByText('Role for jordan.reporter@example.com changed to Responder.')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Deactivate jordan.reporter@example.com' }));
    expect(await screen.findByText('jordan.reporter@example.com was deactivated.')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reactivate jordan.reporter@example.com' })).toBeInTheDocument();

    const patches = apiCalls(fetchMock).filter(call => call.init.method === 'PATCH').map(call => [call.url.pathname, JSON.parse(String(call.init.body))]);
    expect(patches).toEqual([['/api/users/102', { role: 'responder' }], ['/api/users/102', { is_active: false }]]);
  });

  it('creates an account and reports duplicates on the email field', async () => {
    serveUsers([admin, reporter]);
    renderAt('/users');
    await screen.findByRole('cell', { name: /jordan\.reporter@example\.com/ });

    await userEvent.type(screen.getByLabelText('Email'), 'new.responder@example.com');
    await userEvent.type(screen.getByLabelText('Temporary password'), 'a-long-synthetic-passphrase');
    await userEvent.selectOptions(screen.getByLabelText('Role'), 'responder');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByText(/Account created for new.responder@example.com/)).toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toHaveValue('');

    await userEvent.type(screen.getByLabelText('Email'), 'jordan.reporter@example.com');
    await userEvent.type(screen.getByLabelText('Temporary password'), 'a-long-synthetic-passphrase');
    await userEvent.click(screen.getByRole('button', { name: 'Create account' }));
    expect(await screen.findByText('A user with this email already exists.')).toBeInTheDocument();
    expect(screen.getByLabelText('Email')).toHaveAttribute('aria-invalid', 'true');
  });

  it('validates new accounts before sending', () => {
    expect(validateNewUser({ email: 'bad', password: 'short', role: 'reporter' })).toEqual({
      email: 'Enter a valid email address.',
      password: 'Password must be at least 12 characters.',
    });
    expect(validateNewUser({ email: 'ok@example.com', password: 'twelve chars', role: 'admin' })).toEqual({});
  });
});
