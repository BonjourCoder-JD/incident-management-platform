import { useEffect, useState, type FormEvent } from 'react';
import { ApiError, createUser, listUsers, updateUser, type FieldErrors } from '../api/client';
import { PASSWORD_MAX_LENGTH, PASSWORD_MIN_LENGTH, ROLES, type User, type UserRole, type UserUpdate } from '../api/types';
import { ROLE_LABELS, useCurrentUser } from '../auth';
import { Alert, Email, LoadingState } from '../components/ui';
import { formatDateTime } from '../format';

type LoadState = { kind: 'loading' } | { kind: 'error'; message: string } | { kind: 'ready'; users: User[] };
type Notice = { tone: 'success' | 'error'; text: string } | null;

const EMAIL_PATTERN = /^[^@\s]+@[^@\s]+\.[^@\s]+$/;

interface NewUserValues {
  email: string;
  password: string;
  role: UserRole;
}

const EMPTY_USER: NewUserValues = { email: '', password: '', role: 'reporter' };

export function validateNewUser(values: NewUserValues): FieldErrors {
  const errors: FieldErrors = {};
  if (!EMAIL_PATTERN.test(values.email.trim())) errors.email = 'Enter a valid email address.';
  if (values.password.length < PASSWORD_MIN_LENGTH) errors.password = `Password must be at least ${PASSWORD_MIN_LENGTH} characters.`;
  else if (values.password.length > PASSWORD_MAX_LENGTH) errors.password = `Password must be at most ${PASSWORD_MAX_LENGTH} characters.`;
  else if (!values.password.trim()) errors.password = 'Password cannot be only spaces.';
  return errors;
}

export function UsersPage() {
  const me = useCurrentUser();
  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [reloadKey, setReloadKey] = useState(0);
  const [notice, setNotice] = useState<Notice>(null);
  const [busyId, setBusyId] = useState<number | null>(null);

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: 'loading' });
    listUsers(controller.signal)
      .then(users => setState({ kind: 'ready', users }))
      .catch(error => {
        if (!controller.signal.aborted) setState({ kind: 'error', message: error instanceof ApiError ? error.message : 'Unable to load users.' });
      });
    return () => controller.abort();
  }, [reloadKey]);

  function replaceUser(updated: User) {
    setState(current => (current.kind === 'ready' ? { kind: 'ready', users: current.users.map(user => (user.id === updated.id ? updated : user)) } : current));
  }

  async function change(user: User, changes: UserUpdate, success: string) {
    setBusyId(user.id);
    setNotice(null);
    try {
      replaceUser(await updateUser(user.id, changes));
      setNotice({ tone: 'success', text: success });
    } catch (error) {
      setNotice({ tone: 'error', text: error instanceof ApiError ? error.message : 'The change could not be saved.' });
    } finally {
      setBusyId(null);
    }
  }

  return (
    <section aria-labelledby="users-heading">
      <div className="page-header">
        <div>
          <h1 id="users-heading">Users</h1>
          <p className="muted">Create accounts and manage roles. Changing a role or deactivating a user signs them out.</p>
        </div>
      </div>

      {notice && <Alert tone={notice.tone} onDismiss={() => setNotice(null)}>{notice.text}</Alert>}

      <div className="users-layout">
        <div className="card">
          <h2>Accounts</h2>
          {state.kind === 'loading' && <LoadingState label="Loading users…" />}
          {state.kind === 'error' && (
            <Alert tone="error" title="Users could not be loaded" action={<button type="button" className="button button-secondary button-small" onClick={() => setReloadKey(key => key + 1)}>Retry</button>}>
              {state.message}
            </Alert>
          )}
          {state.kind === 'ready' && (
            <div className="table-wrap">
              <table className="incident-table users-table">
                <caption className="visually-hidden">User accounts</caption>
                <thead>
                  <tr>
                    <th scope="col">Email</th>
                    <th scope="col">Role</th>
                    <th scope="col">Status</th>
                    <th scope="col">Created</th>
                  </tr>
                </thead>
                <tbody>
                  {state.users.map(user => {
                    const isMe = user.id === me.id;
                    const busy = busyId === user.id;
                    return (
                      <tr key={user.id}>
                        <td data-label="Email" className="cell-email">
                          <Email value={user.email} /> {isMe && <span className="muted">(you)</span>}
                        </td>
                        <td data-label="Role">
                          <select
                            aria-label={`Role for ${user.email}`}
                            value={user.role}
                            disabled={isMe || busy}
                            onChange={event => {
                              const role = event.target.value as UserRole;
                              void change(user, { role }, `Role for ${user.email} changed to ${ROLE_LABELS[role]}.`);
                            }}
                          >
                            {ROLES.map(role => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}
                          </select>
                        </td>
                        <td data-label="Status">
                          <span className={`badge ${user.is_active ? 'status-resolved' : 'status-closed'}`}>{user.is_active ? 'Active' : 'Inactive'}</span>{' '}
                          {!isMe && (
                            <button
                              type="button"
                              className="button button-ghost button-small"
                              disabled={busy}
                              aria-label={`${user.is_active ? 'Deactivate' : 'Reactivate'} ${user.email}`}
                              onClick={() => void change(user, { is_active: !user.is_active }, `${user.email} was ${user.is_active ? 'deactivated' : 'reactivated'}.`)}
                            >
                              {user.is_active ? 'Deactivate' : 'Reactivate'}
                            </button>
                          )}
                        </td>
                        <td data-label="Created"><time dateTime={user.created_at}>{formatDateTime(user.created_at)}</time></td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        <CreateUserForm
          onCreated={user => {
            setState(current => (current.kind === 'ready' ? { kind: 'ready', users: [...current.users, user] } : current));
            setNotice({ tone: 'success', text: `Account created for ${user.email}. Share the temporary password with them securely.` });
          }}
        />
      </div>
    </section>
  );
}

function CreateUserForm({ onCreated }: { onCreated: (user: User) => void }) {
  const [values, setValues] = useState<NewUserValues>(EMPTY_USER);
  const [errors, setErrors] = useState<FieldErrors>({});
  const [formError, setFormError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const validation = validateNewUser(values);
    setErrors(validation);
    setFormError(null);
    if (Object.keys(validation).length > 0) return;
    setSubmitting(true);
    try {
      const user = await createUser({ ...values, email: values.email.trim() });
      setValues(EMPTY_USER);
      onCreated(user);
    } catch (error) {
      if (error instanceof ApiError) {
        setErrors(error.status === 409 ? { email: error.message } : error.fieldErrors);
        setFormError(error.status === 409 ? null : error.message);
      } else setFormError('The account could not be created.');
    } finally {
      setSubmitting(false);
    }
  }

  const field = (name: keyof NewUserValues) => ({
    id: `new-user-${name}`,
    disabled: submitting,
    'aria-invalid': Boolean(errors[name]),
    'aria-describedby': errors[name] ? `new-user-${name}-error` : undefined,
  });

  return (
    <form className="card form" noValidate onSubmit={handleSubmit} aria-labelledby="create-user-heading">
      <h2 id="create-user-heading">Create account</h2>
      {formError && <Alert tone="error">{formError}</Alert>}
      <div className="field">
        <label htmlFor="new-user-email">Email</label>
        <input type="email" autoComplete="off" value={values.email} onChange={event => setValues({ ...values, email: event.target.value })} {...field('email')} />
        {errors.email && <p className="field-error" id="new-user-email-error">{errors.email}</p>}
      </div>
      <div className="field">
        <label htmlFor="new-user-password">Temporary password</label>
        <input type="password" autoComplete="new-password" value={values.password} onChange={event => setValues({ ...values, password: event.target.value })} {...field('password')} />
        <p className="field-hint">At least {PASSWORD_MIN_LENGTH} characters. Ask the user to change it after signing in.</p>
        {errors.password && <p className="field-error" id="new-user-password-error">{errors.password}</p>}
      </div>
      <div className="field">
        <label htmlFor="new-user-role">Role</label>
        <select value={values.role} onChange={event => setValues({ ...values, role: event.target.value as UserRole })} {...field('role')}>
          {ROLES.map(role => <option key={role} value={role}>{ROLE_LABELS[role]}</option>)}
        </select>
      </div>
      <div className="form-actions">
        <button type="submit" className="button button-primary" disabled={submitting}>{submitting ? 'Creating…' : 'Create account'}</button>
      </div>
    </form>
  );
}
