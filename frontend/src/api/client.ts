import type {
  AnalyticsParams,
  IncidentSla,
  SlaPolicy,
  SlaPolicyCreate,
  CommentCreate,
  CommentPage,
  IncidentComment,
  IncidentStatus,
  Severity,
  AnalyticsSummary,
  Incident,
  IncidentCreate,
  IncidentEventPage,
  IncidentListParams,
  IncidentPage,
  IncidentUpdate,
  SessionInfo,
  TriageDecisionCreate,
  TriageStatus,
  TriageSuggestion,
  TriageSuggestionPage,
  User,
  UserCreate,
  UserSummary,
  UserUpdate,
} from './types';

// Requests go through the Vite dev proxy, which strips the /api prefix.
const API_BASE = '/api';
const UNSAFE_METHODS = new Set(['POST', 'PUT', 'PATCH', 'DELETE']);

export type FieldErrors = Partial<Record<string, string>>;

export class ApiError extends Error {
  readonly status: number;
  readonly fieldErrors: FieldErrors;
  readonly retryAfterSeconds: number | null;

  constructor(status: number, message: string, fieldErrors: FieldErrors = {}, retryAfterSeconds: number | null = null) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.fieldErrors = fieldErrors;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

// ---- Session plumbing ---------------------------------------------------------------
// The session itself is an HttpOnly cookie that JavaScript cannot read. The CSRF token is
// kept only in memory (never localStorage) and sent on state-changing requests.

let csrfToken: string | null = null;
let onUnauthorized: (() => void) | null = null;

export function setCsrfToken(token: string | null) {
  csrfToken = token;
}

/** Called when an authenticated request returns 401 (session expired or revoked). */
export function setUnauthorizedHandler(handler: (() => void) | null) {
  onUnauthorized = handler;
}

interface ValidationIssue {
  loc?: unknown[];
  msg?: string;
}

function messageForStatus(status: number): string {
  if (status === 401) return 'Your session has ended. Sign in again.';
  if (status === 403) return 'You do not have permission to do this.';
  if (status === 404) return 'The requested incident was not found.';
  if (status === 409) return 'This conflicts with existing data.';
  if (status === 422) return 'Some fields are invalid. Check the highlighted fields and try again.';
  if (status === 429) return 'Too many attempts. Try again later.';
  if (status === 503) return 'The service is temporarily unavailable. Try again shortly.';
  if (status >= 500) return 'The server encountered an error. Try again shortly.';
  return `Request failed (HTTP ${status}).`;
}

/** Converts a FastAPI error body into a readable message and per-field messages. */
export function parseErrorBody(status: number, body: unknown, retryAfterSeconds: number | null = null): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') {
    // e.g. "title cannot be null" from PATCH, or "Incident not found".
    const field = /^(\w+) cannot be null$/.exec(detail)?.[1];
    const message = status === 404 ? messageForStatus(404) : detail;
    return new ApiError(status, message, field ? { [field]: 'This field is required.' } : {}, retryAfterSeconds);
  }
  if (Array.isArray(detail)) {
    const fieldErrors: FieldErrors = {};
    for (const issue of detail as ValidationIssue[]) {
      const loc = Array.isArray(issue.loc) ? issue.loc : [];
      const field = loc[0] === 'body' || loc[0] === 'query' ? loc[1] : loc[loc.length - 1];
      if (typeof field === 'string' && issue.msg && !fieldErrors[field]) {
        fieldErrors[field] = issue.msg.replace(/^Value error, /, '');
      }
    }
    return new ApiError(status, messageForStatus(status), fieldErrors, retryAfterSeconds);
  }
  return new ApiError(status, messageForStatus(status), {}, retryAfterSeconds);
}

interface RequestOptions extends RequestInit {
  /** Do not treat 401 as an expired session (login and session checks). */
  allowUnauthorized?: boolean;
}

/** Sends a request and returns the successful response; every failure becomes an ApiError. */
async function send(path: string, { allowUnauthorized, ...init }: RequestOptions = {}, accept = 'application/json'): Promise<Response> {
  const method = (init.method ?? 'GET').toUpperCase();
  const headers: Record<string, string> = { Accept: accept };
  if (init.body) headers['Content-Type'] = 'application/json';
  if (UNSAFE_METHODS.has(method) && csrfToken) headers['X-CSRF-Token'] = csrfToken;

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, { ...init, credentials: 'same-origin', headers: { ...headers, ...(init.headers as Record<string, string> | undefined) } });
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    throw new ApiError(0, 'Unable to reach the API. Check that the backend is running.');
  }

  if (!response.ok) {
    let body: unknown = null;
    try {
      body = await response.json();
    } catch {
      // Non-JSON error body; fall back to a status-based message.
    }
    const retryHeader = Number(response.headers.get('Retry-After'));
    const error = parseErrorBody(response.status, body, Number.isFinite(retryHeader) && retryHeader > 0 ? retryHeader : null);
    if (response.status === 401 && !allowUnauthorized) onUnauthorized?.();
    throw error;
  }
  return response;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const response = await send(path, options);
  if (response.status === 204) return undefined as T;
  return (await response.json()) as T;
}

// ---- Authentication -------------------------------------------------------------------

export function login(email: string, password: string): Promise<SessionInfo> {
  return request<SessionInfo>('/auth/login', { method: 'POST', body: JSON.stringify({ email, password }), allowUnauthorized: true });
}

export function logout(): Promise<void> {
  return request<void>('/auth/logout', { method: 'POST', allowUnauthorized: true });
}

/** The current session, or null when signed out. */
export async function getSession(signal?: AbortSignal): Promise<SessionInfo | null> {
  try {
    return await request<SessionInfo>('/auth/me', { signal, allowUnauthorized: true });
  } catch (error) {
    if (error instanceof ApiError && error.status === 401) return null;
    throw error;
  }
}

export function changePassword(currentPassword: string, newPassword: string): Promise<void> {
  return request<void>('/auth/password', { method: 'PUT', body: JSON.stringify({ current_password: currentPassword, new_password: newPassword }) });
}

// ---- Users ---------------------------------------------------------------------------

export function listUsers(signal?: AbortSignal): Promise<User[]> {
  return request<User[]>('/users', { signal });
}

export function createUser(payload: UserCreate): Promise<User> {
  return request<User>('/users', { method: 'POST', body: JSON.stringify(payload) });
}

export function updateUser(id: number, changes: UserUpdate): Promise<User> {
  return request<User>(`/users/${encodeURIComponent(String(id))}`, { method: 'PATCH', body: JSON.stringify(changes) });
}

export function listAssignees(signal?: AbortSignal): Promise<UserSummary[]> {
  return request<UserSummary[]>('/users/assignees', { signal });
}

// ---- Incidents ---------------------------------------------------------------------------

export function listIncidents(params: IncidentListParams, signal?: AbortSignal): Promise<IncidentPage> {
  const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
  if (params.severity) query.set('severity', params.severity);
  if (params.status) query.set('status', params.status);
  if (params.sla) query.set('sla', params.sla);
  return request<IncidentPage>(`/incidents?${query.toString()}`, { signal });
}

export function getIncident(id: number, signal?: AbortSignal): Promise<Incident> {
  return request<Incident>(`/incidents/${encodeURIComponent(String(id))}`, { signal });
}

export function createIncident(payload: IncidentCreate): Promise<Incident> {
  return request<Incident>('/incidents', { method: 'POST', body: JSON.stringify(payload) });
}

export function updateIncident(id: number, changes: IncidentUpdate): Promise<Incident> {
  return request<Incident>(`/incidents/${encodeURIComponent(String(id))}`, { method: 'PATCH', body: JSON.stringify(changes) });
}

export function listIncidentEvents(id: number, params: { limit: number; offset: number }, signal?: AbortSignal): Promise<IncidentEventPage> {
  const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
  return request<IncidentEventPage>(`/incidents/${encodeURIComponent(String(id))}/events?${query.toString()}`, { signal });
}

export function listComments(id: number, params: { limit: number; offset: number }, signal?: AbortSignal): Promise<CommentPage> {
  const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
  return request<CommentPage>(`/incidents/${encodeURIComponent(String(id))}/comments?${query.toString()}`, { signal });
}

export function createComment(id: number, payload: CommentCreate): Promise<IncidentComment> {
  return request<IncidentComment>(`/incidents/${encodeURIComponent(String(id))}/comments`, { method: 'POST', body: JSON.stringify(payload) });
}

/** Administrators only. Irreversible: the text is withheld from every reader afterwards. */
export function redactComment(incidentId: number, commentId: number, reason: string): Promise<IncidentComment> {
  return request<IncidentComment>(
    `/incidents/${encodeURIComponent(String(incidentId))}/comments/${encodeURIComponent(String(commentId))}/redaction`,
    { method: 'POST', body: JSON.stringify({ reason }) },
  );
}

export function getIncidentSla(id: number, signal?: AbortSignal): Promise<IncidentSla> {
  return request<IncidentSla>(`/incidents/${encodeURIComponent(String(id))}/sla`, { signal });
}

export function getSlaPolicy(signal?: AbortSignal): Promise<SlaPolicy> {
  return request<SlaPolicy>('/sla/policy', { signal });
}

export function listSlaPolicyVersions(signal?: AbortSignal): Promise<SlaPolicy[]> {
  return request<SlaPolicy[]>('/sla/policy/versions', { signal });
}

export function createSlaPolicyVersion(payload: SlaPolicyCreate): Promise<SlaPolicy> {
  return request<SlaPolicy>('/sla/policy/versions', { method: 'POST', body: JSON.stringify(payload) });
}

// ---- AI-assisted triage -------------------------------------------------------------------

export function getTriageStatus(signal?: AbortSignal): Promise<TriageStatus> {
  return request<TriageStatus>('/triage/status', { signal });
}

export function listTriageSuggestions(incidentId: number, signal?: AbortSignal): Promise<TriageSuggestionPage> {
  return request<TriageSuggestionPage>(`/incidents/${incidentId}/triage-suggestions`, { signal });
}

/** Asks for a suggestion. Never changes the incident; a person must accept it first. */
export function requestTriageSuggestion(incidentId: number): Promise<TriageSuggestion> {
  return request<TriageSuggestion>(`/incidents/${incidentId}/triage-suggestions`, { method: 'POST' });
}

export function decideTriageSuggestion(suggestionId: number, payload: TriageDecisionCreate): Promise<TriageSuggestion> {
  return request<TriageSuggestion>(`/triage-suggestions/${suggestionId}/decision`, { method: 'POST', body: JSON.stringify(payload) });
}

export function getAnalyticsSummary(params: AnalyticsParams, signal?: AbortSignal): Promise<AnalyticsSummary> {
  const query = new URLSearchParams({ from: params.from, to: params.to, tz: params.tz });
  return request<AnalyticsSummary>(`/analytics/summary?${query.toString()}`, { signal });
}

// ---- CSV export ---------------------------------------------------------------------------

export interface CsvDownload {
  blob: Blob;
  filename: string;
  /** Rows in the file, from X-Export-Row-Count. */
  rowCount: number | null;
}

const SAFE_FILENAME = /^[A-Za-z0-9._-]{1,100}$/;

/** Filename from Content-Disposition, accepted only if it is a plain, safe name. */
export function filenameFrom(disposition: string | null, fallback = 'incidents.csv'): string {
  const name = disposition ? /filename="([^"]+)"/.exec(disposition)?.[1] : undefined;
  return name && SAFE_FILENAME.test(name) && name.endsWith('.csv') ? name : fallback;
}

/** Downloads the incidents the user may see, with the list's filters. Over the limit: ApiError 413. */
export async function downloadIncidentsCsv(filters: { severity?: Severity; status?: IncidentStatus }, signal?: AbortSignal): Promise<CsvDownload> {
  const query = new URLSearchParams();
  if (filters.severity) query.set('severity', filters.severity);
  if (filters.status) query.set('status', filters.status);
  const suffix = query.toString() ? `?${query.toString()}` : '';
  const response = await send(`/exports/incidents.csv${suffix}`, { signal }, 'text/csv');
  const count = Number(response.headers.get('X-Export-Row-Count'));
  return {
    blob: await response.blob(),
    filename: filenameFrom(response.headers.get('Content-Disposition')),
    rowCount: Number.isFinite(count) && response.headers.has('X-Export-Row-Count') ? count : null,
  };
}

export async function checkHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const data = await request<{ status?: string }>('/health', { signal });
    return data.status === 'ok';
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    return false;
  }
}
