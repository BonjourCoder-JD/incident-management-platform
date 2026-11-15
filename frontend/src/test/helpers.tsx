import { render } from '@testing-library/react';
import { vi } from 'vitest';
import App from '../App';
import type { Incident } from '../api/types';

export function makeIncident(overrides: Partial<Incident> = {}): Incident {
  return {
    id: 1,
    title: 'Suspicious login',
    description: 'Multiple failed sign-ins from an unknown IP.',
    severity: 'high',
    status: 'open',
    assignee_id: null,
    created_at: '2026-11-14T10:00:00Z',
    updated_at: '2026-11-14T10:30:00Z',
    ...overrides,
  };
}

export function jsonResponse(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
}

type Handler = (url: URL, init: RequestInit) => Response | Promise<Response>;

/**
 * Replaces global fetch. `/api/health` answers ok unless overridden; any other
 * unhandled request fails the test so unexpected API calls are visible.
 */
export function mockApi(handler: Handler) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname === '/api/health') return jsonResponse(200, { status: 'ok' });
    return handler(url, init);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

/** Calls made to the incidents API, excluding the health check. */
export function apiCalls(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls
    .map(([input, init]) => ({ url: new URL(String(input), 'http://localhost'), init: (init ?? {}) as RequestInit }))
    .filter(call => call.url.pathname !== '/api/health');
}

export function renderAt(path: string) {
  window.history.replaceState(null, '', path);
  return render(<App />);
}
