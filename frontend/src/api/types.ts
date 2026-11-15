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

// ---- SLA (Milestone 5B) -----------------------------------------------------------------

export const SLA_STATES = ['on_track', 'approaching', 'overdue', 'met', 'breached', 'not_tracked'] as const;
export type SlaState = (typeof SLA_STATES)[number];
export type SlaTargetName = 'response' | 'resolution';
/** List filter values; `escalated` is for responders and admins only. */
export const SLA_FILTERS = ['overdue', 'approaching', 'breached', 'escalated'] as const;
export type SlaFilter = (typeof SLA_FILTERS)[number];

export function isSlaFilter(value: unknown): value is SlaFilter {
  return typeof value === 'string' && (SLA_FILTERS as readonly string[]).includes(value);
}

export interface SlaSummary {
  state: Exclude<SlaState, 'not_tracked'>;
  target: SlaTargetName;
  due_at: string;
}

export interface IncidentListItem extends Incident {
  /** null when the incident is not tracked. */
  sla: SlaSummary | null;
  /** Staff only; always null for reporters. */
  escalated: boolean | null;
}

export interface SlaTargetStatus {
  target_minutes: number | null;
  severity: Severity | null;
  due_at: string | null;
  warning_at: string | null;
  satisfied_at: string | null;
  state: SlaState;
}

export interface SlaCycle {
  cycle: number;
  started_at: string;
  policy_version_id: number | null;
  tracked: boolean;
  response: SlaTargetStatus | null;
  resolution: SlaTargetStatus;
}

export interface SlaEscalation {
  id: number;
  cycle: number;
  target: SlaTargetName;
  deadline_at: string;
  created_at: string;
  triggered_by: 'system';
}

export interface IncidentSla {
  /** Server time used for every state; the browser clock is never authoritative. */
  now: string;
  tracked: boolean;
  current_cycle: number;
  cycles: SlaCycle[];
  /** Staff only; null for reporters. */
  escalations: SlaEscalation[] | null;
}

export interface SlaPolicyTarget {
  severity: Severity;
  response_minutes: number;
  resolution_minutes: number;
}

export interface SlaPolicy {
  id: number;
  source: 'default' | 'admin';
  reason: string;
  author: UserSummary | null;
  created_at: string;
  targets: SlaPolicyTarget[];
}

export interface SlaPolicyCreate {
  reason: string;
  targets: SlaPolicyTarget[];
}

export const SLA_RESPONSE_MAX_MINUTES = 30 * 24 * 60;
export const SLA_RESOLUTION_MAX_MINUTES = 365 * 24 * 60;
export const SLA_REASON_MAX_LENGTH = 500;

export interface SlaComplianceCounts {
  met: number;
  breached: number;
  outstanding: number;
  not_tracked: number;
}

export interface SlaAnalytics {
  response: SlaComplianceCounts;
  resolution: SlaComplianceCounts;
  overdue_by_severity: { severity: Severity; count: number }[];
  approaching_by_severity: { severity: Severity; count: number }[];
  /** Staff only; null for reporters. */
  escalations: number | null;
}

export interface IncidentPage {
  items: IncidentListItem[];
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

export const COMMENT_VISIBILITIES = ['public', 'internal'] as const;
/** `public`: the reporter and staff can read it. `internal`: responders and admins only. */
export type CommentVisibility = (typeof COMMENT_VISIBILITIES)[number];

export interface IncidentComment {
  id: number;
  visibility: CommentVisibility;
  body: string;
  author: UserSummary;
  created_at: string;
}

export interface CommentPage {
  items: IncidentComment[];
  /** Only the comments the signed-in user may read. */
  total: number;
  limit: number;
  offset: number;
}

export interface CommentCreate {
  body: string;
  visibility: CommentVisibility;
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
  sla: SlaAnalytics;
}

export interface IncidentListParams {
  limit: number;
  offset: number;
  severity?: Severity;
  status?: IncidentStatus;
  sla?: SlaFilter;
}

export const TITLE_MAX_LENGTH = 200;
/** Matches the API and the database (migration 0006); counted in Unicode code points. */
export const COMMENT_MAX_LENGTH = 5000;
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;
export const MAX_PAGE_SIZE = 100;

export function isSeverity(value: unknown): value is Severity {
  return typeof value === 'string' && (SEVERITIES as readonly string[]).includes(value);
}

export function isStatus(value: unknown): value is IncidentStatus {
  return typeof value === 'string' && (STATUSES as readonly string[]).includes(value);
}
