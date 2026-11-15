import { describe, expect, it, vi } from 'vitest';
import { afterEach } from 'vitest';
import { ApiError, createIncident, getSession, listIncidents, login, parseErrorBody, setCsrfToken, setUnauthorizedHandler, updateIncident } from './client';
import { jsonResponse } from '../test/helpers';

describe('parseErrorBody', () => {
  it('maps FastAPI validation errors to fields', () => {
    const error = parseErrorBody(422, {
      detail: [
        { loc: ['body', 'title'], msg: 'String should have at least 1 character', type: 'string_too_short' },
        { loc: ['body', 'severity'], msg: "Input should be 'low', 'medium', 'high' or 'critical'", type: 'enum' },
      ],
    });
    expect(error.status).toBe(422);
    expect(error.fieldErrors).toEqual({
      title: 'String should have at least 1 character',
      severity: "Input should be 'low', 'medium', 'high' or 'critical'",
    });
    expect(error.message).toMatch(/fields are invalid/i);
  });

  it('maps "<field> cannot be null" PATCH errors to that field', () => {
    const error = parseErrorBody(422, { detail: 'title cannot be null' });
    expect(error.fieldErrors).toEqual({ title: 'This field is required.' });
    expect(error.message).toBe('title cannot be null');
  });

  it('uses a friendly message for 404 and server errors', () => {
    expect(parseErrorBody(404, { detail: 'Incident not found' }).message).toMatch(/not found/i);
    expect(parseErrorBody(500, null).message).toMatch(/server encountered an error/i);
  });
});

describe('API requests', () => {
  it('builds list queries with only the provided filters', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { items: [], total: 0, limit: 10, offset: 20 }));
    vi.stubGlobal('fetch', fetchMock);
    await listIncidents({ limit: 10, offset: 20, severity: 'critical' });
    expect(fetchMock).toHaveBeenCalledWith('/api/incidents?limit=10&offset=20&severity=critical', expect.anything());
  });

  it('sends JSON bodies for create and update', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, {}));
    vi.stubGlobal('fetch', fetchMock);
    await createIncident({ title: 'A', description: 'B', severity: 'low' });
    await updateIncident(7, { status: 'closed' });
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(calls[0][0]).toBe('/api/incidents');
    expect(calls[0][1]).toMatchObject({ method: 'POST', body: '{"title":"A","description":"B","severity":"low"}' });
    expect(calls[1][0]).toBe('/api/incidents/7');
    expect(calls[1][1]).toMatchObject({ method: 'PATCH', body: '{"status":"closed"}' });
    expect(new Headers(calls[1][1].headers).get('Content-Type')).toBe('application/json');
  });

  it('reports network failures as an ApiError with status 0', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => { throw new TypeError('Failed to fetch'); }));
    const error = await listIncidents({ limit: 10, offset: 0 }).catch(e => e);
    expect(error).toBeInstanceOf(ApiError);
    expect(error.status).toBe(0);
    expect(error.message).toMatch(/unable to reach the api/i);
  });
});

describe('session handling', () => {
  afterEach(() => {
    setCsrfToken(null);
    setUnauthorizedHandler(null);
  });

  it('sends the CSRF token only on state-changing requests', async () => {
    const fetchMock = vi.fn(async () => jsonResponse(200, { items: [], total: 0, limit: 10, offset: 0 }));
    vi.stubGlobal('fetch', fetchMock);
    setCsrfToken('abc123');
    await listIncidents({ limit: 10, offset: 0 });
    await updateIncident(1, { title: 'x' });
    const calls = fetchMock.mock.calls as unknown as [string, RequestInit][];
    expect(new Headers(calls[0][1].headers).get('X-CSRF-Token')).toBeNull();
    expect(new Headers(calls[1][1].headers).get('X-CSRF-Token')).toBe('abc123');
    expect(calls.every(([, init]) => init.credentials === 'same-origin')).toBe(true);
  });

  it('reports 401s on data requests as an ended session, but not on login or session checks', async () => {
    const handler = vi.fn();
    setUnauthorizedHandler(handler);
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(401, { detail: 'Authentication required.' })));

    await expect(login('a@example.com', 'wrong')).rejects.toMatchObject({ status: 401 });
    await expect(getSession()).resolves.toBeNull();
    expect(handler).not.toHaveBeenCalled();

    await expect(listIncidents({ limit: 10, offset: 0 })).rejects.toMatchObject({ status: 401 });
    expect(handler).toHaveBeenCalledTimes(1);
  });

  it('exposes Retry-After on rate-limited responses', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => jsonResponse(429, { detail: 'Too many failed sign-in attempts.' }, { 'Retry-After': '120' })));
    const error = await login('a@example.com', 'x').catch(e => e);
    expect(error.retryAfterSeconds).toBe(120);
  });

  it('strips the "Value error, " prefix from Pydantic messages', () => {
    expect(parseErrorBody(422, { detail: [{ loc: ['body', 'email'], msg: 'Value error, Enter a valid email address.' }] }).fieldErrors)
      .toEqual({ email: 'Enter a valid email address.' });
  });
});
