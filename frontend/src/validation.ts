import { TITLE_MAX_LENGTH, type IncidentCreate, type Severity } from './api/types';
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
