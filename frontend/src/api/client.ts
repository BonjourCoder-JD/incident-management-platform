import type { Incident, IncidentCreate, IncidentListParams, IncidentPage, IncidentUpdate } from './types';

// Requests go through the Vite dev proxy, which strips the /api prefix.
const API_BASE = '/api';

export type FieldErrors = Partial<Record<string, string>>;

export class ApiError extends Error {
  readonly status: number;
  readonly fieldErrors: FieldErrors;

  constructor(status: number, message: string, fieldErrors: FieldErrors = {}) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.fieldErrors = fieldErrors;
  }
}

interface ValidationIssue {
  loc?: unknown[];
  msg?: string;
}

function messageForStatus(status: number): string {
  if (status === 404) return 'The requested incident was not found.';
  if (status === 422) return 'Some fields are invalid. Check the highlighted fields and try again.';
  if (status === 503) return 'The service is temporarily unavailable. Try again shortly.';
  if (status >= 500) return 'The server encountered an error. Try again shortly.';
  return `Request failed (HTTP ${status}).`;
}

/** Converts a FastAPI error body into a readable message and per-field messages. */
export function parseErrorBody(status: number, body: unknown): ApiError {
  const detail = (body as { detail?: unknown } | null)?.detail;
  if (typeof detail === 'string') {
    // e.g. "title cannot be null" from PATCH, or "Incident not found".
    const field = /^(\w+) cannot be null$/.exec(detail)?.[1];
    const message = status === 404 ? messageForStatus(404) : detail;
    return new ApiError(status, message, field ? { [field]: 'This field is required.' } : {});
  }
  if (Array.isArray(detail)) {
    const fieldErrors: FieldErrors = {};
    for (const issue of detail as ValidationIssue[]) {
      const loc = Array.isArray(issue.loc) ? issue.loc : [];
      const field = loc[0] === 'body' || loc[0] === 'query' ? loc[1] : loc[loc.length - 1];
      if (typeof field === 'string' && issue.msg && !fieldErrors[field]) {
        fieldErrors[field] = issue.msg;
      }
    }
    return new ApiError(status, messageForStatus(status), fieldErrors);
  }
  return new ApiError(status, messageForStatus(status));
}

async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      ...init,
      headers: { Accept: 'application/json', ...(init.body ? { 'Content-Type': 'application/json' } : {}), ...init.headers },
    });
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
    throw parseErrorBody(response.status, body);
  }
  return (await response.json()) as T;
}

export function listIncidents(params: IncidentListParams, signal?: AbortSignal): Promise<IncidentPage> {
  const query = new URLSearchParams({ limit: String(params.limit), offset: String(params.offset) });
  if (params.severity) query.set('severity', params.severity);
  if (params.status) query.set('status', params.status);
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

export async function checkHealth(signal?: AbortSignal): Promise<boolean> {
  try {
    const data = await request<{ status?: string }>('/health', { signal });
    return data.status === 'ok';
  } catch (error) {
    if (error instanceof DOMException && error.name === 'AbortError') throw error;
    return false;
  }
}
