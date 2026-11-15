import { useState, type FormEvent } from 'react';
import { ApiError } from '../api/client';
import { useAuth } from '../auth';
import { Alert } from '../components/ui';

function minutes(seconds: number) {
  const value = Math.max(1, Math.ceil(seconds / 60));
  return `${value} minute${value === 1 ? '' : 's'}`;
}

export function LoginPage({ notice }: { notice: string | null }) {
  const { login } = useAuth();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!email.trim() || !password) {
      setError('Enter your email and password.');
      return;
    }
    setSubmitting(true);
    setError(null);
    try {
      await login(email, password);
    } catch (caught) {
      setPassword('');
      if (caught instanceof ApiError && caught.status === 401) setError('Invalid email or password.');
      else if (caught instanceof ApiError && caught.status === 429) {
        setError(`Too many failed sign-in attempts. Try again in ${minutes(caught.retryAfterSeconds ?? 900)}.`);
      } else setError(caught instanceof ApiError ? caught.message : 'Sign-in failed. Try again.');
      setSubmitting(false);
    }
  }

  return (
    <section className="login" aria-labelledby="login-heading">
      <div className="card login-card">
        <h1 id="login-heading">Sign in</h1>
        <p className="muted">Use the account your administrator created for you.</p>
        {notice && !error && <Alert tone="info">{notice}</Alert>}
        {error && <Alert tone="error">{error}</Alert>}
        <form className="form" noValidate onSubmit={handleSubmit}>
          <div className="field">
            <label htmlFor="login-email">Email</label>
            <input id="login-email" type="email" autoComplete="username" value={email} disabled={submitting} onChange={event => setEmail(event.target.value)} />
          </div>
          <div className="field">
            <label htmlFor="login-password">Password</label>
            <input id="login-password" type="password" autoComplete="current-password" value={password} disabled={submitting} onChange={event => setPassword(event.target.value)} />
          </div>
          <button type="submit" className="button button-primary" disabled={submitting}>
            {submitting ? 'Signing in…' : 'Sign in'}
          </button>
        </form>
      </div>
    </section>
  );
}
