import { SEVERITIES, TITLE_MAX_LENGTH, type Severity, type UserSummary } from '../api/types';
import type { FieldErrors } from '../api/client';
import { SEVERITY_LABELS } from '../format';
import type { IncidentFormValues } from '../validation';

interface Props {
  idPrefix: string;
  values: IncidentFormValues;
  errors: FieldErrors;
  disabled?: boolean;
  /**
   * People the incident can be assigned to. `null` hides the field entirely (reporters
   * may not assign); `'loading'` and `'error'` describe the directory request.
   */
  assignees: UserSummary[] | 'loading' | 'error' | null;
  /** The saved assignee, shown even if they are no longer in the directory. */
  currentAssignee?: UserSummary | null;
  onChange: (values: IncidentFormValues) => void;
}

function describedBy(id: string, error?: string, hint?: boolean) {
  const ids = [hint ? `${id}-hint` : '', error ? `${id}-error` : ''].filter(Boolean).join(' ');
  return ids || undefined;
}

function FieldError({ id, message }: { id: string; message?: string }) {
  return message ? <p className="field-error" id={`${id}-error`}>{message}</p> : null;
}

/** Title, description, severity and assignee inputs shared by the create and edit forms. */
export function IncidentFields({ idPrefix, values, errors, disabled, assignees, currentAssignee, onChange }: Props) {
  const ids = {
    title: `${idPrefix}-title`,
    description: `${idPrefix}-description`,
    severity: `${idPrefix}-severity`,
    assignee: `${idPrefix}-assignee`,
  };
  const set = <K extends keyof IncidentFormValues>(key: K, value: IncidentFormValues[K]) => onChange({ ...values, [key]: value });
  const titleLength = values.title.trim().length;

  return (
    <div className="field-grid">
      <div className="field field-full">
        <label htmlFor={ids.title}>Title</label>
        <input
          id={ids.title}
          type="text"
          value={values.title}
          maxLength={TITLE_MAX_LENGTH + 50}
          disabled={disabled}
          aria-invalid={Boolean(errors.title)}
          aria-describedby={describedBy(ids.title, errors.title, true)}
          onChange={event => set('title', event.target.value)}
        />
        <p className={`field-hint${titleLength > TITLE_MAX_LENGTH ? ' field-hint-over' : ''}`} id={`${ids.title}-hint`}>
          {titleLength}/{TITLE_MAX_LENGTH} characters
        </p>
        <FieldError id={ids.title} message={errors.title} />
      </div>

      <div className="field field-full">
        <label htmlFor={ids.description}>Description</label>
        <textarea
          id={ids.description}
          rows={6}
          value={values.description}
          disabled={disabled}
          aria-invalid={Boolean(errors.description)}
          aria-describedby={describedBy(ids.description, errors.description)}
          onChange={event => set('description', event.target.value)}
        />
        <FieldError id={ids.description} message={errors.description} />
      </div>

      <div className="field">
        <label htmlFor={ids.severity}>Severity</label>
        <select
          id={ids.severity}
          value={values.severity}
          disabled={disabled}
          aria-invalid={Boolean(errors.severity)}
          aria-describedby={describedBy(ids.severity, errors.severity)}
          onChange={event => set('severity', event.target.value as Severity | '')}
        >
          <option value="" disabled>Select severity</option>
          {SEVERITIES.map(severity => <option key={severity} value={severity}>{SEVERITY_LABELS[severity]}</option>)}
        </select>
        <FieldError id={ids.severity} message={errors.severity} />
      </div>

      {assignees !== null && (
        <div className="field">
          <label htmlFor={ids.assignee}>Assignee <span className="optional">(optional)</span></label>
          <select
            id={ids.assignee}
            value={values.assigneeId}
            disabled={disabled || assignees === 'loading'}
            aria-invalid={Boolean(errors.assignee_id)}
            aria-describedby={describedBy(ids.assignee, errors.assignee_id, true)}
            onChange={event => set('assigneeId', event.target.value)}
          >
            <option value="">Unassigned</option>
            {currentAssignee && (assignees === 'loading' || assignees === 'error' || !assignees.some(user => user.id === currentAssignee.id)) && (
              <option value={String(currentAssignee.id)}>{currentAssignee.email}</option>
            )}
            {Array.isArray(assignees) && assignees.map(user => <option key={user.id} value={String(user.id)}>{user.email}</option>)}
          </select>
          <p className="field-hint" id={`${ids.assignee}-hint`}>
            {assignees === 'loading' ? 'Loading responders…' : assignees === 'error' ? 'Could not load responders. Try again later.' : 'Active responders and admins.'}
          </p>
          <FieldError id={ids.assignee} message={errors.assignee_id} />
        </div>
      )}
    </div>
  );
}
