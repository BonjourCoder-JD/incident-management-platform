import { screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { apiCalls, jsonResponse, makeIncident, mockApi, renderAt } from '../test/helpers';

function page(items = [makeIncident()], total = items.length, offset = 0) {
  return { items, total, limit: 10, offset };
}

describe('Incident list', () => {
  it('shows incidents returned by the API', async () => {
    mockApi(() => jsonResponse(200, page([
      makeIncident({ id: 2, title: 'Ransomware alert', severity: 'critical', status: 'investigating', assignee_id: 7 }),
      makeIncident({ id: 1, title: 'Suspicious login' }),
    ])));
    renderAt('/');

    expect(screen.getByText('Loading incidents…')).toBeInTheDocument();
    const link = await screen.findByRole('link', { name: 'Ransomware alert' });
    expect(link).toHaveAttribute('href', '/incidents/2');
    const row = link.closest('tr')!;
    expect(within(row).getByText('Critical')).toBeInTheDocument();
    expect(within(row).getByText('Investigating')).toBeInTheDocument();
    expect(within(row).getByText('7')).toBeInTheDocument();
    expect(screen.getByText('Showing 1–2 of 2')).toBeInTheDocument();
    expect(await screen.findByText('API connected')).toBeInTheDocument();
  });

  it('shows an empty state when there are no incidents', async () => {
    mockApi(() => jsonResponse(200, page([], 0)));
    renderAt('/');
    expect(await screen.findByText('No incidents reported yet')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Report the first incident' })).toHaveAttribute('href', '/incidents/new');
  });

  it('shows an error with a working retry', async () => {
    let attempts = 0;
    mockApi(() => (++attempts === 1 ? jsonResponse(500, { detail: 'boom' }) : jsonResponse(200, page())));
    renderAt('/');

    expect(await screen.findByRole('alert')).toHaveTextContent('Incidents could not be loaded');
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    expect(await screen.findByRole('link', { name: 'Suspicious login' })).toBeInTheDocument();
  });

  it('filters by severity and status through the API and the URL', async () => {
    const fetchMock = mockApi(() => jsonResponse(200, page([], 0)));
    renderAt('/');
    await screen.findByText('No incidents reported yet');

    await userEvent.selectOptions(screen.getByLabelText('Severity'), 'critical');
    await screen.findByText('No incidents match these filters');
    await userEvent.selectOptions(screen.getByLabelText('Status'), 'resolved');
    await screen.findByText('No incidents match these filters');

    const last = apiCalls(fetchMock).at(-1)!.url;
    expect(last.pathname).toBe('/api/incidents');
    expect(Object.fromEntries(last.searchParams)).toEqual({ limit: '10', offset: '0', severity: 'critical', status: 'resolved' });
    expect(window.location.search).toBe('?severity=critical&status=resolved');

    await userEvent.click(screen.getAllByRole('button', { name: 'Clear filters' })[0]);
    await screen.findByText('No incidents reported yet');
    expect(window.location.search).toBe('');
  });

  it('reads filters from the URL and ignores invalid values', async () => {
    const fetchMock = mockApi(() => jsonResponse(200, page([], 0)));
    renderAt('/?severity=low&status=bogus&page=-4');
    await screen.findByText('No incidents match these filters');
    const url = apiCalls(fetchMock)[0].url;
    expect(Object.fromEntries(url.searchParams)).toEqual({ limit: '10', offset: '0', severity: 'low' });
    expect(screen.getByLabelText('Severity')).toHaveValue('low');
    expect(screen.getByLabelText('Status')).toHaveValue('');
  });

  it('paginates with offset-based requests', async () => {
    const first = Array.from({ length: 10 }, (_, i) => makeIncident({ id: 25 - i, title: `Incident ${25 - i}` }));
    const second = Array.from({ length: 10 }, (_, i) => makeIncident({ id: 15 - i, title: `Incident ${15 - i}` }));
    const fetchMock = mockApi(url => jsonResponse(200, url.searchParams.get('offset') === '10' ? page(second, 25, 10) : page(first, 25, 0)));
    renderAt('/');

    await screen.findByText('Showing 1–10 of 25');
    expect(screen.getByRole('button', { name: 'Previous' })).toBeDisabled();
    await userEvent.click(screen.getByRole('button', { name: 'Next' }));

    expect(await screen.findByText('Showing 11–20 of 25')).toBeInTheDocument();
    expect(screen.getByText('Page 2 of 3')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Incident 15' })).toBeInTheDocument();
    expect(apiCalls(fetchMock).at(-1)!.url.searchParams.get('offset')).toBe('10');
    expect(window.location.search).toBe('?page=2');
  });
});
