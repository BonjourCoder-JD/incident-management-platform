import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { apiCalls, jsonResponse, makeIncident, makeUser, mockApi, renderAt } from './test/helpers';

const page = (items = [makeIncident()]) => ({ items, total: items.length, limit: 10, offset: 0 });

describe('Role-aware interface', () => {
  it('reporters see "My incidents" and no Users link', async () => {
    mockApi(() => jsonResponse(200, page()), { user: makeUser('reporter') });
    renderAt('/');
    expect(await screen.findByRole('heading', { name: 'My incidents' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'My incidents' })).toHaveAttribute('aria-current', 'page');
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument();
  });

  it('reporters cannot change status, and the edit form has no assignee field', async () => {
    const fetchMock = mockApi(() => jsonResponse(200, makeIncident({ id: 3 })), { user: makeUser('reporter') });
    renderAt('/incidents/3');
    await screen.findByRole('heading', { name: 'Suspicious login' });

    expect(screen.queryByRole('group', { name: 'Change status' })).not.toBeInTheDocument();
    expect(screen.getByText('Responders update the status as they work on your incident.')).toBeInTheDocument();

    await userEvent.click(screen.getByRole('button', { name: 'Edit details' }));
    expect(screen.getByLabelText('Title')).toBeInTheDocument();
    expect(screen.queryByLabelText(/Assignee/)).not.toBeInTheDocument();
    // The assignee directory is staff-only, so it is never requested.
    expect(fetchMock.mock.calls.some(([input]) => String(input).includes('/users/assignees'))).toBe(false);
  });

  it('reporters create incidents without an assignee field', async () => {
    const fetchMock = mockApi((_url, init) => (init.method === 'POST' ? jsonResponse(201, makeIncident({ id: 9 })) : jsonResponse(200, makeIncident({ id: 9 }))), { user: makeUser('reporter') });
    renderAt('/incidents/new');
    await screen.findByRole('heading', { name: 'Report an incident' });
    expect(screen.queryByLabelText(/Assignee/)).not.toBeInTheDocument();

    await userEvent.type(screen.getByLabelText('Title'), 'Phishing email');
    await userEvent.type(screen.getByLabelText('Description'), 'Reported by finance.');
    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'low');
    await userEvent.click(screen.getByRole('button', { name: 'Create incident' }));
    await screen.findByText('Incident #9 was created.');

    const post = apiCalls(fetchMock).find(call => call.init.method === 'POST')!;
    expect(JSON.parse(String(post.init.body))).toEqual({ title: 'Phishing email', description: 'Reported by finance.', severity: 'low' });
  });

  it('responders see status controls and the assignee directory', async () => {
    mockApi(() => jsonResponse(200, makeIncident({ id: 3 })), { user: makeUser('responder') });
    renderAt('/incidents/3');
    await screen.findByRole('heading', { name: 'Suspicious login' });
    expect(screen.getByRole('group', { name: 'Change status' })).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Edit details' }));
    const assignee = await screen.findByLabelText(/Assignee/);
    expect(await screen.findByRole('option', { name: 'second.responder@example.com' })).toBeInTheDocument();
    expect(assignee).toHaveValue('');
  });

  it('only admins see the Users page', async () => {
    mockApi(() => jsonResponse(200, []), { user: makeUser('responder') });
    renderAt('/users');
    expect(await screen.findByText('You do not have access to this page')).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: 'Users' })).not.toBeInTheDocument();
  });

  it('shows the API message when an action is forbidden', async () => {
    mockApi((_url, init) => (init.method === 'PATCH'
      ? jsonResponse(403, { detail: 'Reporters cannot change status or assignment.' })
      : jsonResponse(200, makeIncident({ id: 3 }))), { user: makeUser('responder') });
    renderAt('/incidents/3');
    await screen.findByRole('heading', { name: 'Suspicious login' });
    await userEvent.click(screen.getByRole('button', { name: 'Resolved' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Reporters cannot change status or assignment.');
  });
});
