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

export type IncidentEventAction = 'created' | 'updated' | 'status_changed' | 'assignment_changed';

/** One audited field change. Values are plain JSON (strings, numbers or null). */
export interface FieldChange {
  from: unknown;
  to: unknown;
}

export interface IncidentEvent {
  id: number;
  action: IncidentEventAction;
  changes: Record<string, FieldChange>;
  actor: UserSummary;
  created_at: string;
}

export interface IncidentEventPage {
  items: IncidentEvent[];
  total: number;
  limit: number;
  offset: number;
  /** People referenced by assignee changes on this page. */
  users: UserSummary[];
}

// ---- Analytics (GET /analytics/summary) ----

export interface AnalyticsParams {
  /** Inclusive calendar dates, YYYY-MM-DD. */
  from: string;
  to: string;
  /** IANA time zone used for day boundaries and buckets. */
  tz: string;
}

export interface TrendPoint {
  period_start: string;
  period_end: string;
  created: number;
  resolved: number;
}

export interface AnalyticsSummary {
  scope: 'own' | 'all';
  range: { start_date: string; end_date: string; timezone: string; bucket: 'day' | 'week' };
  totals: { incidents: number; open: number; resolved_or_closed: number };
  by_status: { status: IncidentStatus; count: number }[];
  by_severity: { severity: Severity; count: number }[];
  open_backlog_by_severity: { severity: Severity; count: number }[];
  trend: TrendPoint[];
  resolution: {
    resolved_count: number;
    median_hours: number | null;
    p90_hours: number | null;
    reopened_count: number;
    excluded_legacy: number;
    by_severity: { severity: Severity; resolved_count: number; median_hours: number | null }[];
  };
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
