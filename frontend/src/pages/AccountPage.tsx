import { useState, type FormEvent } from 'react';
import { ApiError, changePassword, type FieldErrors } from '../api/client';
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH } from '../api/types';
import { ROLE_LABELS, useCurrentUser } from '../auth';
import { Alert } from '../components/ui';

interface Values {
  current: string;
  next: string;
  confirm: string;
}

const EMPTY: Values = { current: '', next: '', confirm: '' };

export function AccountPage() {
  const user = useCurrentUser();
  const [values, setValues] = useState<Values>(EMPTY);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [notice, setNotice] = useState<{ tone: 'success' | 'error'; text: string } | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const found: FieldErrors = {};
    if (!values.current) found.current_password = 'Enter your current password.';
    if (values.next.length < PASSWORD_MIN_LENGTH) found.new_password = `Use at least ${PASSWORD_MIN_LENGTH} characters.`;
    else if (values.next.length > PASSWORD_MAX_LENGTH) found.new_password = `Use at most ${PASSWORD_MAX_LENGTH} characters.`;
    else if (!values.next.trim()) found.new_password = 'Password cannot be only spaces.';
    if (values.confirm !== values.next) found.confirm = 'Passwords do not match.';
    setErrors(found);
    setNotice(null);
    if (Object.keys(found).length > 0) return;

    setSubmitting(true);
    try {
      await changePassword(values.current, values.next);
      setValues(EMPTY);
      setNotice({ tone: 'success', text: 'Password changed. Your other devices have been signed out.' });
    } catch (error) {
      if (error instanceof ApiError) {
        setErrors(error.fieldErrors);
        setNotice({ tone: 'error', text: error.fieldErrors.current_password ?? error.message });
      } else setNotice({ tone: 'error', text: 'The password could not be changed.' });
    } finally {
      setSubmitting(false);
    }
  }

  const input = (key: keyof Values, errorKey: string, label: string, autoComplete: string) => (
    <div className="field">
      <label htmlFor={`account-${key}`}>{label}</label>
      <input
        id={`account-${key}`}
        type="password"
        autoComplete={autoComplete}
        value={values[key]}
        disabled={submitting}
        aria-invalid={Boolean(errors[errorKey])}
        aria-describedby={errors[errorKey] ? `account-${key}-error` : undefined}
        onChange={event => setValues({ ...values, [key]: event.target.value })}
      />
      {errors[errorKey] && <p className="field-error" id={`account-${key}-error`}>{errors[errorKey]}</p>}
    </div>
  );

  return (
    <section aria-labelledby="account-heading" className="narrow">
      <div className="page-header">
        <div>
          <h1 id="account-heading">Your account</h1>
          <p className="muted">Signed in as <strong>{user.email}</strong> ({ROLE_LABELS[user.role]}).</p>
        </div>
      </div>
      {notice && <Alert tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</Alert>}
      <form className="card form" noValidate onSubmit={handleSubmit} aria-labelledby="password-heading">
        <h2 id="password-heading">Change password</h2>
        {input('current', 'current_password', 'Current password', 'current-password')}
        {input('next', 'new_password', 'New password', 'new-password')}
        {input('confirm', 'confirm', 'Confirm new password', 'new-password')}
        <div className="form-actions">
          <button type="submit" className="button button-primary" disabled={submitting}>{submitting ? 'Saving…' : 'Change password'}</button>
        </div>
      </form>
    </section>
  );
}
