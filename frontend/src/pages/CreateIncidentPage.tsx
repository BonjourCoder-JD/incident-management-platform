import { useState, type FormEvent } from 'react';
import { ApiError, createIncident, type FieldErrors } from '../api/client';
import { permissions, useCurrentUser } from '../auth';
import { IncidentFields } from '../components/IncidentFields';
import { useAssignees } from '../components/useAssignees';
import { Alert } from '../components/ui';
import { useFlash } from '../flash';
import { Link } from '../router';
import { EMPTY_FORM, toCreatePayload, validateIncidentForm, type IncidentFormValues } from '../validation';

export function CreateIncidentPage() {
  const user = useCurrentUser();
  const canTriage = permissions.canTriage(user);
  const assignees = useAssignees(canTriage);
  const { navigateWithFlash } = useFlash();
  const [values, setValues] = useState<IncidentFormValues>(EMPTY_FORM);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validation = validateIncidentForm(values);
    setErrors(validation);
    setFormError(null);
    if (Object.keys(validation).length > 0) {
      focusFirstInvalid(event.currentTarget);
      return;
    }

    setSubmitting(true);
    try {
      const incident = await createIncident(toCreatePayload(canTriage ? values : { ...values, assigneeId: '' }));
      navigateWithFlash(`/incidents/${incident.id}`, `Incident #${incident.id} was created.`);
    } catch (error) {
      if (error instanceof ApiError) {
        setErrors(error.fieldErrors);
        setFormError(error.message);
      } else {
        setFormError('The incident could not be created. Try again.');
      }
      setSubmitting(false);
    }
  }

  return (
    <section aria-labelledby="create-heading">
      <nav className="breadcrumb" aria-label="Breadcrumb">
        <Link to="/">Incidents</Link> <span aria-hidden="true">/</span> <span>New incident</span>
      </nav>
      <div className="page-header">
        <div>
          <h1 id="create-heading">Report an incident</h1>
          <p className="muted">
            {canTriage ? 'New incidents start with the status Open.' : 'New incidents start with the status Open. A responder will triage and assign it.'}
          </p>
        </div>
      </div>

      <form className="card form" noValidate onSubmit={handleSubmit} aria-describedby={formError ? 'create-form-error' : undefined}>
        {formError && (
          <div id="create-form-error">
            <Alert tone="error" title="The incident was not created">{formError}</Alert>
          </div>
        )}
        <IncidentFields idPrefix="create" values={values} errors={errors} disabled={submitting} assignees={assignees} onChange={setValues} />
        <div className="form-actions">
          <Link to="/" className="button button-ghost">Cancel</Link>
          <button type="submit" className="button button-primary" disabled={submitting}>
            {submitting ? 'Creating…' : 'Create incident'}
          </button>
        </div>
      </form>
    </section>
  );
}

export function focusFirstInvalid(form: HTMLFormElement) {
  // Wait for React to render aria-invalid before moving focus.
  requestAnimationFrame(() => form.querySelector<HTMLElement>('[aria-invalid="true"]')?.focus());
}
