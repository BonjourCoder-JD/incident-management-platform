import { render } from '@testing-library/react';
import { vi } from 'vitest';
import App from '../App';
import type { Incident, IncidentEventPage, User, UserRole, UserSummary } from '../api/types';

export const TEST_CSRF = 'test-csrf-token';

export function makeUser(role: UserRole = 'responder', overrides: Partial<User> = {}): User {
  const ids: Record<UserRole, number> = { reporter: 101, responder: 201, admin: 301 };
  return { id: ids[role], email: `${role}@example.com`, role, is_active: true, created_at: '2026-11-01T09:00:00Z', ...overrides };
}

export const ASSIGNEES: UserSummary[] = [
  { id: 201, email: 'responder@example.com' },
  { id: 202, email: 'second.responder@example.com' },
];

export function makeIncident(overrides: Partial<Incident> = {}): Incident {
  return {
    id: 1,
    title: 'Suspicious login',
    description: 'Multiple failed sign-ins from an unknown IP.',
    severity: 'high',
    status: 'open',
    reporter_id: 101,
    assignee_id: null,
    reporter: { id: 101, email: 'reporter@example.com' },
    assignee: null,
    created_at: '2026-11-14T10:00:00Z',
    updated_at: '2026-11-14T10:30:00Z',
    ...overrides,
  };
}

/**
 * A promise the test resolves itself. Use it to hold a mocked response until the test has
 * checked the loading state, so loading assertions do not depend on timing.
 */
export function deferred() {
  let release!: () => void;
  const promise = new Promise<void>(resolve => { release = resolve; });
  return { promise, release };
}

export function jsonResponse(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json', ...headers } });
}

type Handler = (url: URL, init: RequestInit) => Response | Promise<Response>;

export const EMPTY_EVENTS: IncidentEventPage = { items: [], total: 0, limit: 10, offset: 0, users: [] };

interface MockOptions {
  /** Signed-in user; `null` means signed out. Defaults to a responder. */
  user?: User | null;
  assignees?: UserSummary[];
  /** Incident history responses. Defaults to an empty history. */
  events?: IncidentEventPage | ((url: URL) => Response | Promise<Response>);
}

const EVENTS_PATH = /^\/api\/incidents\/\d+\/events$/;

/**
 * Replaces global fetch. Health, session (`/auth/me`), logout and the assignee directory
 * have defaults; everything else goes to `handler`, so unexpected calls are visible.
 */
export function mockApi(handler: Handler = () => jsonResponse(500, {}), options: MockOptions = {}) {
  const user = options.user === undefined ? makeUser('responder') : options.user;
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init: RequestInit = {}) => {
    const url = new URL(String(input), 'http://localhost');
    if (url.pathname === '/api/health') return jsonResponse(200, { status: 'ok' });
    if (url.pathname === '/api/auth/me') return user ? jsonResponse(200, { user, csrf_token: TEST_CSRF }) : jsonResponse(401, { detail: 'Authentication required.' });
    if (url.pathname === '/api/auth/logout') return new Response(null, { status: 204 });
    if (url.pathname === '/api/users/assignees') return jsonResponse(200, options.assignees ?? ASSIGNEES);
    if (EVENTS_PATH.test(url.pathname)) {
      const events = options.events ?? EMPTY_EVENTS;
      return typeof events === 'function' ? events(url) : jsonResponse(200, events);
    }
    return handler(url, init);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const PLUMBING = new Set(['/api/health', '/api/auth/me', '/api/users/assignees']);

/** Calls made to the API, excluding health, session, assignee-directory and history requests. */
export function apiCalls(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls
    .map(([input, init]) => ({ url: new URL(String(input), 'http://localhost'), init: (init ?? {}) as RequestInit }))
    .filter(call => !PLUMBING.has(call.url.pathname) && !EVENTS_PATH.test(call.url.pathname));
}

/** Requests made to the incident history endpoint. */
export function eventCalls(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls
    .map(([input]) => new URL(String(input), 'http://localhost'))
    .filter(url => EVENTS_PATH.test(url.pathname));
}

export function headerOf(init: RequestInit, name: string): string | null {
  return new Headers(init.headers).get(name);
}

export function renderAt(path: string) {
  window.history.replaceState(null, '', path);
  return render(<App />);
}
