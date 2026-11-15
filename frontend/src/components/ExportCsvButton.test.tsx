import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { filenameFrom } from '../api/client';
import { deferred, jsonResponse, makeIncident, makeUser, mockApi, renderAt } from '../test/helpers';

const EXPORT_PATH = '/api/exports/incidents.csv';
const listPage = { items: [makeIncident()], total: 1, limit: 10, offset: 0 };

function csvResponse(filename = 'incidents-2026-11-15-high-open.csv', rows = 2) {
  return new Response('﻿id,title\r\n1,A\r\n2,B\r\n', {
    status: 200,
    headers: {
      'Content-Type': 'text/csv; charset=utf-8',
      'Content-Disposition': `attachment; filename="${filename}"`,
      'X-Export-Row-Count': String(rows),
    },
  });
}

let clicked: { href: string; download: string }[];
let revoked: string[];

beforeEach(() => {
  clicked = [];
  revoked = [];
  // jsdom has no object URLs; record what the component does with them.
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:mock-export') });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn((url: string) => revoked.push(url)) });
  vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(function (this: HTMLAnchorElement) {
    clicked.push({ href: this.getAttribute('href') ?? '', download: this.download });
  });
});

afterEach(() => {
  delete (URL as unknown as Record<string, unknown>).createObjectURL;
  delete (URL as unknown as Record<string, unknown>).revokeObjectURL;
});

function exportCalls(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls
    .map(([input, init]) => ({ url: new URL(String(input), 'http://localhost'), init: (init ?? {}) as RequestInit }))
    .filter(call => call.url.pathname === EXPORT_PATH);
}

function serve(exportHandler: () => Response | Promise<Response>, options = {}) {
  return mockApi(url => (url.pathname === EXPORT_PATH ? exportHandler() : jsonResponse(200, listPage)), options);
}

describe('Export CSV', () => {
  it('exports with the current filters and saves the server-named file', async () => {
    const fetchMock = serve(() => csvResponse());
    renderAt('/?severity=high&status=open');
    const button = await screen.findByRole('button', { name: 'Export CSV (High, Open)' });

    await userEvent.click(button);

    expect(await screen.findByText(/CSV download started — 2 incidents \(incidents-2026-11-15-high-open\.csv\)/)).toBeInTheDocument();
    // Announced to screen readers through the live region.
    expect(screen.getByText('CSV download started: 2 incidents, file incidents-2026-11-15-high-open.csv.', { selector: '[role="status"]' })).toBeInTheDocument();
    expect(screen.queryByText(/Downloaded/)).not.toBeInTheDocument(); // never claims the download finished
    const [call] = exportCalls(fetchMock);
    expect(Object.fromEntries(call.url.searchParams)).toEqual({ severity: 'high', status: 'open' });
    expect(call.init.credentials).toBe('same-origin');
    expect(new Headers(call.init.headers).get('Accept')).toBe('text/csv');
    expect(clicked).toEqual([{ href: 'blob:mock-export', download: 'incidents-2026-11-15-high-open.csv' }]);
    await waitFor(() => expect(revoked).toEqual(['blob:mock-export']));
    expect(document.querySelector('a[download]')).toBeNull(); // temporary link removed
  });

  it('exports all visible incidents when no filter is set', async () => {
    const fetchMock = serve(() => csvResponse('incidents-2026-11-15.csv', 1));
    renderAt('/');
    await userEvent.click(await screen.findByRole('button', { name: 'Export CSV (all visible incidents)' }));
    await screen.findByText(/CSV download started — 1 incident \(/);
    expect(exportCalls(fetchMock)[0].url.search).toBe('');
  });

  it('shows a busy state while the export is prepared', async () => {
    const response = deferred();
    serve(async () => { await response.promise; return csvResponse(); });
    renderAt('/?severity=high&status=open');
    await userEvent.click(await screen.findByRole('button', { name: 'Export CSV (High, Open)' }));

    const busy = screen.getByRole('button', { name: 'Preparing export…' });
    expect(busy).toBeDisabled();
    expect(screen.getByText('Preparing export…', { selector: '[role="status"]' })).toBeInTheDocument();
    response.release();
    expect(await screen.findByRole('button', { name: 'Export CSV (High, Open)' })).toBeEnabled();
  });

  it('explains when the export is over the row limit, without downloading', async () => {
    serve(() => jsonResponse(413, { detail: 'This export would contain 12,000 incidents; the limit is 10,000. Narrow the filters and try again.' }, { 'X-Export-Row-Count': '12000' }));
    renderAt('/');
    await userEvent.click(await screen.findByRole('button', { name: /Export CSV/ }));
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Too many incidents to export');
    expect(alert).toHaveTextContent('12,000 incidents; the limit is 10,000. Narrow the filters');
    expect(screen.queryByRole('button', { name: 'Retry' })).not.toBeInTheDocument();
    expect(clicked).toEqual([]);
  });

  it('offers a retry after a failure', async () => {
    let calls = 0;
    serve(() => (++calls === 1 ? jsonResponse(500, {}) : csvResponse()));
    renderAt('/?severity=high&status=open');
    await userEvent.click(await screen.findByRole('button', { name: 'Export CSV (High, Open)' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('Export failed');
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    await screen.findByText(/CSV download started — 2 incidents \(/);
    expect(clicked).toHaveLength(1);
  });

  it('ignores an unsafe filename from the server', async () => {
    serve(() => csvResponse('../../evil.sh', 2));
    renderAt('/');
    await userEvent.click(await screen.findByRole('button', { name: /Export CSV/ }));
    await screen.findByText(/CSV download started — 2 incidents \(incidents\.csv\)/);
    expect(clicked[0].download).toBe('incidents.csv');
  });

  it('hands an expired session to the sign-in screen', async () => {
    serve(() => jsonResponse(401, { detail: 'Authentication required.' }));
    renderAt('/');
    await userEvent.click(await screen.findByRole('button', { name: /Export CSV/ }));
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(clicked).toEqual([]);
  });

  it('is available to reporters for their own incidents', async () => {
    serve(() => csvResponse(), { user: makeUser('reporter') });
    renderAt('/');
    await screen.findByRole('heading', { name: 'My incidents' });
    expect(screen.getByRole('button', { name: 'Export CSV (all visible incidents)' })).toBeEnabled();
  });
});

describe('filenameFrom', () => {
  it.each([
    ['attachment; filename="incidents-2026-11-15.csv"', 'incidents-2026-11-15.csv'],
    ['attachment; filename="../../etc/passwd.csv"', 'incidents.csv'],
    ['attachment; filename="report.exe"', 'incidents.csv'],
    ['attachment; filename="name with spaces.csv"', 'incidents.csv'],
    [null, 'incidents.csv'],
  ])('%s -> %s', (header, expected) => {
    expect(filenameFrom(header)).toBe(expected);
  });
});
