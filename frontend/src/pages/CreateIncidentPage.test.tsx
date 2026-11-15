import { screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { apiCalls, jsonResponse, makeIncident, mockApi, renderAt } from '../test/helpers';

describe('Create incident', () => {
  it('validates required fields without calling the API', async () => {
    const fetchMock = mockApi(() => jsonResponse(500, {}));
    renderAt('/incidents/new');

    await userEvent.click(screen.getByRole('button', { name: 'Create incident' }));

    expect(screen.getByText('Enter a title.')).toBeInTheDocument();
    expect(screen.getByText('Enter a description.')).toBeInTheDocument();
    expect(screen.getByText('Select a severity.')).toBeInTheDocument();
    expect(screen.getByLabelText('Title')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByLabelText('Title')).toHaveAccessibleDescription(/Enter a title\./);
    expect(apiCalls(fetchMock)).toHaveLength(0);
  });

  it('creates an incident, then opens it with a success message', async () => {
    const created = makeIncident({ id: 42, title: 'Lost laptop', description: 'Left on a train.', severity: 'medium', assignee_id: 9 });
    const fetchMock = mockApi((url, init) => {
      if (init.method === 'POST') return jsonResponse(201, created);
      if (url.pathname === '/api/incidents/42') return jsonResponse(200, created);
      return jsonResponse(404, { detail: 'Incident not found' });
    });
    renderAt('/incidents/new');

    await userEvent.type(screen.getByLabelText('Title'), '  Lost laptop ');
    await userEvent.type(screen.getByLabelText('Description'), 'Left on a train.');
    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'medium');
    await userEvent.type(screen.getByLabelText(/Assignee ID/), '9');
    await userEvent.click(screen.getByRole('button', { name: 'Create incident' }));

    expect(await screen.findByRole('heading', { name: 'Lost laptop' })).toBeInTheDocument();
    expect(screen.getByText('Incident #42 was created.')).toBeInTheDocument();
    expect(window.location.pathname).toBe('/incidents/42');

    const post = apiCalls(fetchMock).find(call => call.init.method === 'POST')!;
    expect(JSON.parse(String(post.init.body))).toEqual({ title: 'Lost laptop', description: 'Left on a train.', severity: 'medium', assignee_id: 9 });
  });

  it('shows server validation errors next to the matching field', async () => {
    mockApi(() => jsonResponse(422, { detail: [{ loc: ['body', 'description'], msg: 'Value error, description rejected', type: 'value_error' }] }));
    renderAt('/incidents/new');

    await userEvent.type(screen.getByLabelText('Title'), 'Test');
    await userEvent.type(screen.getByLabelText('Description'), 'Something');
    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'low');
    await userEvent.click(screen.getByRole('button', { name: 'Create incident' }));

    expect(await screen.findByText('Value error, description rejected')).toBeInTheDocument();
    expect(screen.getByRole('alert')).toHaveTextContent('The incident was not created');
    expect(screen.getByLabelText('Description')).toHaveAttribute('aria-invalid', 'true');
    expect(screen.getByRole('button', { name: 'Create incident' })).toBeEnabled();
  });

  it('explains when the API cannot be reached', async () => {
    mockApi(() => { throw new TypeError('Failed to fetch'); });
    renderAt('/incidents/new');

    await userEvent.type(screen.getByLabelText('Title'), 'Test');
    await userEvent.type(screen.getByLabelText('Description'), 'Something');
    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'low');
    await userEvent.click(screen.getByRole('button', { name: 'Create incident' }));

    expect(await screen.findByText(/Unable to reach the API/)).toBeInTheDocument();
  });
});
