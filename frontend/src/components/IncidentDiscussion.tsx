import { useEffect, useRef, useState, type FormEvent } from 'react';
import { ApiError, createComment, listComments } from '../api/client';
import { COMMENT_MAX_LENGTH, type CommentVisibility, type Incident, type IncidentComment } from '../api/types';
import { permissions, useCurrentUser } from '../auth';
import { formatDateTime } from '../format';
import { commentLength, validateComment } from '../validation';
import { Alert, Email, LoadingState } from './ui';

export const COMMENTS_PAGE_SIZE = 50;

type LoadState =
  | { kind: 'loading' }
  | { kind: 'error'; message: string }
  | { kind: 'ready'; items: IncidentComment[]; total: number };

const LIMIT = COMMENT_MAX_LENGTH.toLocaleString('en-US');

/** Announced politely only when the remaining-characters band changes, not on every keystroke. */
function lengthBand(length: number): string {
  const remaining = COMMENT_MAX_LENGTH - length;
  if (remaining < 0) return 'Over the character limit.';
  if (remaining === 0) return 'Character limit reached.';
  if (remaining <= 50) return 'Fewer than 50 characters left.';
  if (remaining <= 200) return 'Fewer than 200 characters left.';
  return '';
}

function Comment({ comment, reporterId }: { comment: IncidentComment; reporterId: number | null }) {
  const internal = comment.visibility === 'internal';
  return (
    <li className={`comment comment-${comment.visibility}`}>
      <div className="comment-meta">
        <span className="comment-author"><Email value={comment.author.email} /></span>
        {reporterId !== null && comment.author.id === reporterId && <span className="badge role-reporter">Reporter</span>}
        {internal && <span className="badge comment-internal-badge">Internal note</span>}
        <time className="muted small" dateTime={comment.created_at}>{formatDateTime(comment.created_at)}</time>
      </div>
      {internal && <p className="comment-internal-hint small">Not visible to the reporter</p>}
      {/* Plain text only: React escapes it, and line breaks are kept by CSS. */}
      <p className="comment-body">{comment.body}</p>
    </li>
  );
}

/** Public comments and, for responders and admins, internal notes. Oldest first. `refreshKey` reloads it. */
export function IncidentDiscussion({ incident, refreshKey = 0 }: { incident: Incident; refreshKey?: number }) {
  const user = useCurrentUser();
  const staff = permissions.canTriage(user);
  const defaultVisibility: CommentVisibility = staff ? 'internal' : 'public';

  const [state, setState] = useState<LoadState>({ kind: 'loading' });
  const [retryKey, setRetryKey] = useState(0);
  const [loadingMore, setLoadingMore] = useState(false);
  const [moreError, setMoreError] = useState<string | null>(null);

  const [body, setBody] = useState('');
  const [visibility, setVisibility] = useState<CommentVisibility>(defaultVisibility);
  const [fieldError, setFieldError] = useState<string | undefined>();
  const [formError, setFormError] = useState<string | null>(null);
  const [posting, setPosting] = useState(false);
  const [announcement, setAnnouncement] = useState('');
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  // Bumped after each attempt; focus moves back once the re-enabled textarea has rendered.
  const [refocusKey, setRefocusKey] = useState(0);

  useEffect(() => {
    if (refocusKey > 0) textareaRef.current?.focus();
  }, [refocusKey]);

  useEffect(() => {
    const controller = new AbortController();
    setState({ kind: 'loading' });
    setMoreError(null);
    listComments(incident.id, { limit: COMMENTS_PAGE_SIZE, offset: 0 }, controller.signal)
      .then(page => setState({ kind: 'ready', items: page.items, total: page.total }))
      .catch(error => {
        if (controller.signal.aborted) return;
        setState({ kind: 'error', message: error instanceof ApiError ? error.message : 'Unable to load the discussion.' });
      });
    return () => controller.abort();
  }, [incident.id, retryKey, refreshKey]);

  async function loadMore() {
    if (state.kind !== 'ready') return;
    setLoadingMore(true);
    setMoreError(null);
    try {
      const page = await listComments(incident.id, { limit: COMMENTS_PAGE_SIZE, offset: state.items.length });
      setState(current => current.kind === 'ready'
        ? {
            kind: 'ready',
            items: [...current.items, ...page.items.filter(item => !current.items.some(existing => existing.id === item.id))],
            total: page.total,
          }
        : current);
    } catch (error) {
      setMoreError(error instanceof ApiError ? error.message : 'Unable to load more comments.');
    } finally {
      setLoadingMore(false);
    }
  }

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (posting) return;
    setFormError(null);
    setAnnouncement('');
    const problem = validateComment(body);
    setFieldError(problem);
    if (problem) {
      textareaRef.current?.focus();
      return;
    }
    setPosting(true);
    try {
      const posted = await createComment(incident.id, { body, visibility });
      setState(current => {
        if (current.kind !== 'ready') return current;
        // Append only when every earlier comment is already shown; otherwise just count it.
        const complete = current.items.length >= current.total;
        return { kind: 'ready', items: complete ? [...current.items, posted] : current.items, total: current.total + 1 };
      });
      setBody('');
      setVisibility(defaultVisibility);
      setAnnouncement(posted.visibility === 'internal' ? 'Internal note added.' : 'Comment posted.');
    } catch (error) {
      if (error instanceof ApiError && error.fieldErrors.body) {
        setFieldError(error.fieldErrors.body);
      } else {
        setFormError(error instanceof ApiError ? error.message : 'Your comment could not be posted.');
      }
    } finally {
      setPosting(false);
      setRefocusKey(key => key + 1);
    }
  }

  const length = commentLength(body);
  const describedBy = ['comment-body-hint', fieldError ? 'comment-body-error' : '', staff && visibility === 'public' ? 'comment-public-warning' : '']
    .filter(Boolean)
    .join(' ');
  const submitLabel = !staff ? 'Post comment' : visibility === 'internal' ? 'Add internal note' : 'Post public reply';

  return (
    <section className="card discussion" aria-labelledby="discussion-heading">
      <h2 id="discussion-heading">Discussion</h2>
      {staff && <p className="muted small discussion-intro">Internal notes are visible only to responders and admins.</p>}

      {state.kind === 'loading' && <LoadingState label="Loading discussion…" />}
      {state.kind === 'error' && (
        <Alert
          tone="error"
          title="The discussion could not be loaded"
          action={<button type="button" className="button button-secondary button-small" onClick={() => setRetryKey(key => key + 1)}>Retry</button>}
        >
          {state.message}
        </Alert>
      )}
      {state.kind === 'ready' && state.items.length === 0 && (
        <p className="muted discussion-empty">
          {staff ? 'No comments or internal notes yet.' : 'No comments yet. Ask a question or add details for the response team.'}
        </p>
      )}
      {state.kind === 'ready' && state.items.length > 0 && (
        <ol className="comment-list" aria-label="Comments, oldest first">
          {state.items.map(comment => <Comment key={comment.id} comment={comment} reporterId={incident.reporter_id} />)}
        </ol>
      )}
      {state.kind === 'ready' && state.items.length < state.total && (
        <div className="history-more">
          <button type="button" className="button button-secondary button-small" disabled={loadingMore} onClick={loadMore}>
            {loadingMore ? 'Loading…' : 'Show more comments'}
          </button>
          <span className="muted small">Showing {state.items.length} of {state.total}</span>
          {moreError && <p className="field-error" role="alert">{moreError}</p>}
        </div>
      )}

      <form className="form comment-form" noValidate onSubmit={handleSubmit} aria-labelledby="comment-form-heading">
        <h3 id="comment-form-heading" className="comment-form-heading">{staff ? 'Add a comment or internal note' : 'Add a comment'}</h3>
        {formError && <Alert tone="error" title="Your comment was not posted">{formError}</Alert>}
        {staff && (
          <fieldset className="comment-visibility" disabled={posting}>
            <legend>Visibility</legend>
            <label className="comment-visibility-option">
              <input type="radio" name="comment-visibility" value="internal" checked={visibility === 'internal'} onChange={() => setVisibility('internal')} />
              <span><strong>Internal note</strong> <span className="muted small">Responders and admins only</span></span>
            </label>
            <label className="comment-visibility-option">
              <input type="radio" name="comment-visibility" value="public" checked={visibility === 'public'} onChange={() => setVisibility('public')} />
              <span><strong>Public reply</strong> <span className="muted small">Visible to the reporter</span></span>
            </label>
          </fieldset>
        )}
        <div className="field">
          <label htmlFor="comment-body">{staff ? 'Message' : 'Comment'}</label>
          <textarea
            id="comment-body"
            ref={textareaRef}
            rows={4}
            value={body}
            disabled={posting}
            aria-invalid={Boolean(fieldError)}
            aria-describedby={describedBy}
            onChange={event => setBody(event.target.value)}
          />
          {staff && visibility === 'public' && (
            <p className="field-hint comment-public-warning" id="comment-public-warning">The reporter will see this reply.</p>
          )}
          <p className={`field-hint${length > COMMENT_MAX_LENGTH ? ' field-hint-over' : ''}`} id="comment-body-hint">
            {length.toLocaleString('en-US')}/{LIMIT} characters
          </p>
          <p className="visually-hidden comment-length-status" aria-live="polite">{lengthBand(length)}</p>
          {fieldError && <p className="field-error" id="comment-body-error">{fieldError}</p>}
        </div>
        <div className="form-actions">
          <button type="submit" className="button button-primary" disabled={posting}>{posting ? 'Posting…' : submitLabel}</button>
        </div>
        <p className="visually-hidden" role="status">{announcement}</p>
      </form>
    </section>
  );
}
