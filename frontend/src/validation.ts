import {
  COMMENT_MAX_LENGTH, SLA_REASON_MAX_LENGTH, SLA_RESOLUTION_MAX_MINUTES, SLA_RESPONSE_MAX_MINUTES, TITLE_MAX_LENGTH,
  type IncidentCreate, type Severity, type SlaPolicyCreate,
} from './api/types';
import type { FieldErrors } from './api/client';

/** Form state for the editable incident fields. Strings so inputs stay controlled. */
export interface IncidentFormValues {
  title: string;
  description: string;
  severity: Severity | '';
  assigneeId: string;
}

export const EMPTY_FORM: IncidentFormValues = { title: '', description: '', severity: '', assigneeId: '' };

// assignee_id is a PostgreSQL INTEGER column.
const MAX_ASSIGNEE_ID = 2147483647;

export function validateIncidentForm(values: IncidentFormValues): FieldErrors {
  const errors: FieldErrors = {};
  const title = values.title.trim();
  if (!title) errors.title = 'Enter a title.';
  else if (title.length > TITLE_MAX_LENGTH) errors.title = `Title must be ${TITLE_MAX_LENGTH} characters or fewer.`;

  if (!values.description.trim()) errors.description = 'Enter a description.';
  if (!values.severity) errors.severity = 'Select a severity.';

  const assignee = values.assigneeId.trim();
  if (assignee) {
    const id = Number(assignee);
    if (!/^\d+$/.test(assignee) || id < 1 || id > MAX_ASSIGNEE_ID) {
      errors.assignee_id = 'Assignee ID must be a whole number greater than 0.';
    }
  }
  return errors;
}

export function parseAssignee(value: string): number | null {
  const trimmed = value.trim();
  return trimmed ? Number(trimmed) : null;
}

/** Builds the create payload. Call only after validateIncidentForm returns no errors. */
export function toCreatePayload(values: IncidentFormValues): IncidentCreate {
  const payload: IncidentCreate = {
    title: values.title.trim(),
    description: values.description.trim(),
    severity: values.severity as Severity,
  };
  const assignee = parseAssignee(values.assigneeId);
  if (assignee !== null) payload.assignee_id = assignee;
  return payload;
}

// ---- Comments -----------------------------------------------------------------------
// Same rules as the API (backend/app/schemas.py) and the database (migration 0006): line endings
// become LF, the same blank characters are trimmed from both ends, and length counts code points.

const COMMENT_BLANK = ' \t\n\u00a0\u1680\u2000-\u200b\u2028\u2029\u202f\u205f\u3000\ufeff';
const COMMENT_TRIM = new RegExp(`^[${COMMENT_BLANK}]+|[${COMMENT_BLANK}]+$`, 'g');
// C0 controls except tab and line feed, DEL and C1 controls.
const COMMENT_FORBIDDEN = /[\u0000-\u0008\u000b-\u001f\u007f-\u009f]/;

/** The comment exactly as the API will store it. */
export function normaliseComment(value: string): string {
  return value.replace(/\r\n?/g, '\n').replace(COMMENT_TRIM, '');
}

/** Length in Unicode code points after normalisation, as the API and PostgreSQL count it. */
export function commentLength(value: string): number {
  return [...normaliseComment(value)].length;
}

export function validateComment(value: string): string | undefined {
  const body = normaliseComment(value);
  if (!body) return 'Enter a comment.';
  if (COMMENT_FORBIDDEN.test(body)) return 'Comments cannot contain control characters.';
  if ([...body].length > COMMENT_MAX_LENGTH) return `Comments can be at most ${COMMENT_MAX_LENGTH.toLocaleString('en-US')} characters.`;
  return undefined;
}

// ---- SLA policy ------------------------------------------------------------------------
// Same rules as the API (backend/app/schemas.py, SlaPolicyCreate).

/** Strictest first, as the server compares them. */
export const SLA_SEVERITY_ORDER: Severity[] = ['critical', 'high', 'medium', 'low'];

export type SlaPolicyFormValues = {
  reason: string;
  targets: Record<Severity, { response: string; resolution: string }>;
};

function wholeMinutes(value: string, max: number): number | string {
  const trimmed = value.trim();
  if (!/^\d+$/.test(trimmed)) return 'Enter a whole number of minutes.';
  const minutes = Number(trimmed);
  if (minutes < 1) return 'Enter at least 1 minute.';
  if (minutes > max) return `Enter at most ${max.toLocaleString('en-US')} minutes.`;
  return minutes;
}

/** Field errors keyed `<severity>-response`, `<severity>-resolution`, `reason` and `form`. */
export function validateSlaPolicy(values: SlaPolicyFormValues): FieldErrors {
  const errors: FieldErrors = {};
  const parsed: Partial<Record<Severity, { response: number; resolution: number }>> = {};
  for (const severity of SLA_SEVERITY_ORDER) {
    const response = wholeMinutes(values.targets[severity].response, SLA_RESPONSE_MAX_MINUTES);
    const resolution = wholeMinutes(values.targets[severity].resolution, SLA_RESOLUTION_MAX_MINUTES);
    if (typeof response === 'string') errors[`${severity}-response`] = response;
    if (typeof resolution === 'string') errors[`${severity}-resolution`] = resolution;
    if (typeof response === 'number' && typeof resolution === 'number') {
      if (response > resolution) errors[`${severity}-response`] = 'The response target cannot be longer than the resolution target.';
      else parsed[severity] = { response, resolution };
    }
  }
  if (Object.keys(errors).length === 0) {
    for (let i = 0; i < SLA_SEVERITY_ORDER.length - 1; i += 1) {
      const stricter = parsed[SLA_SEVERITY_ORDER[i]]!;
      const lighter = parsed[SLA_SEVERITY_ORDER[i + 1]]!;
      if (stricter.response > lighter.response || stricter.resolution > lighter.resolution) {
        errors.form = 'A more severe incident cannot have a longer target than a less severe one.';
        break;
      }
    }
  }
  const reason = values.reason.trim();
  if (!reason) errors.reason = 'Enter a reason for the change.';
  else if (reason.length > SLA_REASON_MAX_LENGTH) errors.reason = `The reason can be at most ${SLA_REASON_MAX_LENGTH} characters.`;
  return errors;
}

export function toSlaPolicyPayload(values: SlaPolicyFormValues): SlaPolicyCreate {
  return {
    reason: values.reason.trim(),
    targets: SLA_SEVERITY_ORDER.map(severity => ({
      severity,
      response_minutes: Number(values.targets[severity].response.trim()),
      resolution_minutes: Number(values.targets[severity].resolution.trim()),
    })),
  };
}
