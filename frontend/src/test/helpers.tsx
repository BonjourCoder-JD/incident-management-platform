import { render } from '@testing-library/react';
import { vi } from 'vitest';
import App from '../App';
import type { CommentPage, Incident, IncidentEventPage, IncidentSla, TriageStatus, User, UserRole, UserSummary } from '../api/types';

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
export const EMPTY_COMMENTS: CommentPage = { items: [], total: 0, limit: 50, offset: 0 };
/** An incident that is not SLA-tracked: the default for tests that are not about SLA. */
export const UNTRACKED_SLA: IncidentSla = { now: '2026-11-15T12:00:00Z', tracked: false, current_cycle: 0, cycles: [], escalations: null };

interface MockOptions {
  /** Signed-in user; `null` means signed out. Defaults to a responder. */
  user?: User | null;
  assignees?: UserSummary[];
  /** Incident history responses. Defaults to an empty history. */
  events?: IncidentEventPage | ((url: URL) => Response | Promise<Response>);
  /** Discussion requests (GET and POST). Defaults to an empty discussion; POST then fails. */
  comments?: CommentPage | Handler;
  /** SLA responses (`/incidents/{id}/sla`). Defaults to not tracked. */
  sla?: IncidentSla | Handler;
  /** Triage requests (`/triage/...`, `/incidents/{id}/triage-suggestions`, `/triage-suggestions/...`). Defaults to triage turned off. */
  triage?: Handler;
}

const EVENTS_PATH = /^\/api\/incidents\/\d+\/events$/;
const COMMENTS_PATH = /^\/api\/incidents\/\d+\/comments$/;
const SLA_PATH = /^\/api\/incidents\/\d+\/sla$/;
const TRIAGE_PATH = /^\/api\/(triage\/status|incidents\/\d+\/triage-suggestions|triage-suggestions\/\d+\/decision)$/;

/** Triage turned off: the default for tests that are not about triage. */
export const TRIAGE_OFF: TriageStatus = {
  enabled: false, reason: 'AI triage is turned off.', provider: 'off', provider_label: null, is_ai: false, model: null,
  prompt_version: 'triage-v1', sent_fields: [], typical_cost_usd: null, limits: null,
};

function triageOff(url: URL): Response {
  if (url.pathname === '/api/triage/status') return jsonResponse(200, TRIAGE_OFF);
  if (url.pathname.endsWith('/triage-suggestions')) return jsonResponse(200, { items: [] });
  return jsonResponse(500, {});
}

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
    if (SLA_PATH.test(url.pathname)) {
      const sla = options.sla ?? UNTRACKED_SLA;
      return typeof sla === 'function' ? sla(url, init) : jsonResponse(200, sla);
    }
    if (TRIAGE_PATH.test(url.pathname)) return (options.triage ?? triageOff)(url, init);
    if (COMMENTS_PATH.test(url.pathname)) {
      const comments = options.comments ?? EMPTY_COMMENTS;
      if (typeof comments === 'function') return comments(url, init);
      return (init.method ?? 'GET') === 'GET' ? jsonResponse(200, comments) : jsonResponse(500, {});
    }
    return handler(url, init);
  });
  vi.stubGlobal('fetch', fetchMock);
  return fetchMock;
}

const PLUMBING = new Set(['/api/health', '/api/auth/me', '/api/users/assignees']);

/** Calls made to the API, excluding health, session, assignee-directory, history, discussion, SLA and triage requests. */
export function apiCalls(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls
    .map(([input, init]) => ({ url: new URL(String(input), 'http://localhost'), init: (init ?? {}) as RequestInit }))
    .filter(call => !PLUMBING.has(call.url.pathname) && !EVENTS_PATH.test(call.url.pathname) && !COMMENTS_PATH.test(call.url.pathname) && !SLA_PATH.test(call.url.pathname) && !TRIAGE_PATH.test(call.url.pathname));
}

/** Requests made to the SLA endpoint. */
export function slaCalls(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls
    .map(([input]) => new URL(String(input), 'http://localhost'))
    .filter(url => SLA_PATH.test(url.pathname));
}

/** Requests made to the discussion endpoint. */
export function commentCalls(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls
    .map(([input, init]) => ({ url: new URL(String(input), 'http://localhost'), init: (init ?? {}) as RequestInit }))
    .filter(call => COMMENTS_PATH.test(call.url.pathname));
}

/** Requests made to the triage endpoints. */
export function triageCalls(fetchMock: ReturnType<typeof mockApi>) {
  return fetchMock.mock.calls
    .map(([input, init]) => ({ url: new URL(String(input), 'http://localhost'), init: (init ?? {}) as RequestInit }))
    .filter(call => TRIAGE_PATH.test(call.url.pathname));
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
