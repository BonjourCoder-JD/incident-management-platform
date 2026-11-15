// Mirrors backend/app/models.py and backend/app/schemas.py. Keep in sync with the API.

export const SEVERITIES = ['low', 'medium', 'high', 'critical'] as const;
export const STATUSES = ['open', 'investigating', 'resolved', 'closed'] as const;
export const ROLES = ['reporter', 'responder', 'admin'] as const;

export type Severity = (typeof SEVERITIES)[number];
export type IncidentStatus = (typeof STATUSES)[number];
export type UserRole = (typeof ROLES)[number];

export interface UserSummary {
  id: number;
  email: string;
}

export interface User extends UserSummary {
  role: UserRole;
  is_active: boolean;
  created_at: string;
}

export interface SessionInfo {
  user: User;
  csrf_token: string;
}

export interface UserCreate {
  email: string;
  password: string;
  role: UserRole;
}

export interface UserUpdate {
  role?: UserRole;
  is_active?: boolean;
}

export interface Incident {
  id: number;
  title: string;
  description: string;
  severity: Severity;
  status: IncidentStatus;
  reporter_id: number | null;
  assignee_id: number | null;
  reporter: UserSummary | null;
  assignee: UserSummary | null;
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
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;
export const MAX_PAGE_SIZE = 100;

export function isSeverity(value: unknown): value is Severity {
  return typeof value === 'string' && (SEVERITIES as readonly string[]).includes(value);
}

export function isStatus(value: unknown): value is IncidentStatus {
  return typeof value === 'string' && (STATUSES as readonly string[]).includes(value);
}
