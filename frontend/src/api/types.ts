// Mirrors backend/app/models.py and backend/app/schemas.py. Keep in sync with the API.

export const SEVERITIES = ['low', 'medium', 'high', 'critical'] as const;
export const STATUSES = ['open', 'investigating', 'resolved', 'closed'] as const;

export type Severity = (typeof SEVERITIES)[number];
export type IncidentStatus = (typeof STATUSES)[number];

export interface Incident {
  id: number;
  title: string;
  description: string;
  severity: Severity;
  status: IncidentStatus;
  assignee_id: number | null;
  created_at: string;
  updated_at: string;
}

export interface IncidentPage {
  items: Incident[];
  total: number;
  limit: number;
  offset: number;
}

export interface IncidentCreate {
  title: string;
  description: string;
  severity: Severity;
  assignee_id?: number | null;
}

export interface IncidentUpdate {
  title?: string;
  description?: string;
  severity?: Severity;
  status?: IncidentStatus;
  assignee_id?: number | null;
}

export interface IncidentListParams {
  limit: number;
  offset: number;
  severity?: Severity;
  status?: IncidentStatus;
}

export const TITLE_MAX_LENGTH = 200;
export const MAX_PAGE_SIZE = 100;

export function isSeverity(value: unknown): value is Severity {
  return typeof value === 'string' && (SEVERITIES as readonly string[]).includes(value);
}

export function isStatus(value: unknown): value is IncidentStatus {
  return typeof value === 'string' && (STATUSES as readonly string[]).includes(value);
}
