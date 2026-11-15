import { fireEvent, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { CommentVisibility, IncidentComment, User } from '../api/types';
import { commentCalls, deferred, headerOf, jsonResponse, makeIncident, makeUser, mockApi, renderAt, TEST_CSRF } from '../test/helpers';

const reporterUser = makeUser('reporter');
const responderUser = makeUser('responder');
const incident = makeIncident({ id: 5 });

function comment(id: number, body: string, author: User = responderUser, visibility: CommentVisibility = 'public'): IncidentComment {
  return { id, body, visibility, author: { id: author.id, email: author.email }, created_at: `2026-11-15T10:${String(id).padStart(2, '0')}:00Z` };
}

/** A discussion that GET pages through and POST appends to, as the API does. */
function serveDiscussion(initial: IncidentComment[], author: User) {
  const items = [...initial];
  return (url: URL, init: RequestInit) => {
    if (init.method === 'POST') {
      const { body, visibility } = JSON.parse(String(init.body));
      const created = comment(100 + items.length, body, author, visibility);
      items.push(created);
      return jsonResponse(201, created);
    }
    const limit = Number(url.searchParams.get('limit'));
    const offset = Number(url.searchParams.get('offset'));
    return jsonResponse(200, { items: items.slice(offset, offset + limit), total: items.length, limit, offset });
  };
}

type Handler = (url: URL, init: RequestInit) => Response | Promise<Response>;

function open(user: User, comments: Handler) {
  const fetchMock = mockApi(url => (url.pathname === '/api/incidents/5' ? jsonResponse(200, incident) : jsonResponse(404, { detail: 'Incident not found' })), { user, comments });
  renderAt('/incidents/5');
  return fetchMock;
}

const region = () => screen.findByRole('region', { name: 'Discussion' });
const postBodies = (fetchMock: ReturnType<typeof mockApi>) =>
  commentCalls(fetchMock).filter(call => call.init.method === 'POST').map(call => JSON.parse(String(call.init.body)));

describe('Incident discussion', () => {
  it('shows comments oldest first as plain text and marks the reporter', async () => {
    const payload = '<img src=x onerror="window.__pwned=1"><b>bold</b>';
    open(reporterUser, serveDiscussion([comment(1, 'Line one\nLine two', reporterUser), comment(2, payload)], reporterUser));
    const discussion = await region();
    const items = await within(discussion).findAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(within(items[0]).getByText('reporter@example.com')).toBeInTheDocument();
    expect(within(items[0]).getByText('Reporter')).toBeInTheDocument();
    expect(within(items[1]).queryByText('Reporter')).toBeNull();
    expect(within(items[0]).getByText(/Line one\s+Line two/)).toHaveClass('comment-body');
    expect(within(items[1]).getByText(payload)).toBeInTheDocument();
    expect(discussion.querySelector('.comment-list img, .comment-list b')).toBeNull();
    expect((window as unknown as { __pwned?: number }).__pwned).toBeUndefined();
    // Reporters have no visibility choice.
    expect(within(discussion).queryByRole('radio')).toBeNull();
    expect(within(discussion).getByLabelText('Comment')).toBeInTheDocument();
    expect(within(discussion).getByRole('button', { name: 'Post comment' })).toBeInTheDocument();
    // The discussion sits above the history.
    const history = screen.getByRole('region', { name: 'History' });
    expect(discussion.compareDocumentPosition(history) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('marks internal notes for staff with text, not colour alone', async () => {
    open(responderUser, serveDiscussion([comment(1, 'Public update'), comment(2, 'Check the VPN logs', responderUser, 'internal')], responderUser));
    const discussion = await region();
    const [publicItem, internalItem] = await within(discussion).findAllByRole('listitem');
    expect(within(discussion).getByText('Internal notes are visible only to responders and admins.')).toBeInTheDocument();
    expect(internalItem).toHaveClass('comment-internal');
    expect(within(internalItem).getByText('Internal note')).toBeInTheDocument();
    expect(within(internalItem).getByText('Not visible to the reporter')).toBeInTheDocument();
    expect(within(publicItem).queryByText('Internal note')).toBeNull();
    expect(within(publicItem).queryByText('Not visible to the reporter')).toBeNull();
  });

  it('defaults staff to an internal note and names the choice on the button', async () => {
    open(responderUser, serveDiscussion([], responderUser));
    const discussion = await region();
    const group = within(discussion).getByRole('group', { name: 'Visibility' });
    expect(within(group).getByRole('radio', { name: /Internal note/ })).toBeChecked();
    expect(within(discussion).getByRole('button', { name: 'Add internal note' })).toBeInTheDocument();
    const textarea = within(discussion).getByLabelText('Message');
    expect(textarea).not.toHaveAccessibleDescription(/reporter will see/);

    await userEvent.click(within(group).getByRole('radio', { name: /Public reply/ }));
    expect(within(discussion).getByRole('button', { name: 'Post public reply' })).toBeInTheDocument();
    expect(within(discussion).getByText('The reporter will see this reply.')).toBeInTheDocument();
    expect(textarea).toHaveAccessibleDescription(/The reporter will see this reply\./);
  });

  it('posts with the CSRF token, appends, clears, announces and resets to internal', async () => {
    const fetchMock = open(responderUser, serveDiscussion([comment(1, 'First')], responderUser));
    const discussion = await region();
    await within(discussion).findByText('First');
    await userEvent.click(within(discussion).getByRole('radio', { name: /Public reply/ }));
    const textarea = within(discussion).getByLabelText('Message');
    await userEvent.type(textarea, 'We have reset your password.');
    await userEvent.click(within(discussion).getByRole('button', { name: 'Post public reply' }));

    const items = await within(discussion).findAllByRole('listitem');
    expect(items).toHaveLength(2);
    expect(within(items[1]).getByText('We have reset your password.')).toBeInTheDocument();
    expect(postBodies(fetchMock)).toEqual([{ body: 'We have reset your password.', visibility: 'public' }]);
    const post = commentCalls(fetchMock).find(call => call.init.method === 'POST')!;
    expect(headerOf(post.init, 'X-CSRF-Token')).toBe(TEST_CSRF);
    expect(textarea).toHaveValue('');
    expect(textarea).toHaveFocus();
    expect(within(discussion).getByText('Comment posted.')).toBeInTheDocument();
    expect(within(discussion).getByRole('radio', { name: /Internal note/ })).toBeChecked();
  });

  it('announces internal notes separately', async () => {
    const fetchMock = open(responderUser, serveDiscussion([], responderUser));
    const discussion = await region();
    await userEvent.type(within(discussion).getByLabelText('Message'), 'Possible insider');
    await userEvent.click(within(discussion).getByRole('button', { name: 'Add internal note' }));
    expect(await within(discussion).findByText('Internal note added.')).toBeInTheDocument();
    expect(postBodies(fetchMock)).toEqual([{ body: 'Possible insider', visibility: 'internal' }]);
    expect(within(discussion).getByText('Not visible to the reporter')).toBeInTheDocument();
  });

  it('reporters always post public comments', async () => {
    const fetchMock = open(reporterUser, serveDiscussion([], reporterUser));
    const discussion = await region();
    await userEvent.type(within(discussion).getByLabelText('Comment'), 'Any update?');
    await userEvent.click(within(discussion).getByRole('button', { name: 'Post comment' }));
    expect(await within(discussion).findByText('Comment posted.')).toBeInTheDocument();
    expect(postBodies(fetchMock)).toEqual([{ body: 'Any update?', visibility: 'public' }]);
  });

  it('validates locally: blank and over-limit comments are not sent', async () => {
    const fetchMock = open(reporterUser, serveDiscussion([], reporterUser));
    const discussion = await region();
    const textarea = within(discussion).getByLabelText('Comment');
    await userEvent.type(textarea, '   ');
    await userEvent.click(within(discussion).getByRole('button', { name: 'Post comment' }));
    expect(within(discussion).getByText('Enter a comment.')).toBeInTheDocument();
    expect(textarea).toHaveAttribute('aria-invalid', 'true');
    expect(textarea).toHaveAccessibleDescription(/Enter a comment\./);
    expect(textarea).toHaveFocus();

    fireEvent.change(textarea, { target: { value: 'x'.repeat(5001) } });
    expect(within(discussion).getByText('5,001/5,000 characters')).toHaveClass('field-hint-over');
    await userEvent.click(within(discussion).getByRole('button', { name: 'Post comment' }));
    expect(within(discussion).getByText('Comments can be at most 5,000 characters.')).toBeInTheDocument();
    expect(postBodies(fetchMock)).toEqual([]);
  });

  it('counts characters as the server does: code points, after trimming', async () => {
    open(reporterUser, serveDiscussion([], reporterUser));
    const discussion = await region();
    const textarea = within(discussion).getByLabelText('Comment');
    const siren = String.fromCodePoint(0x1f6a8);
    fireEvent.change(textarea, { target: { value: `   ${siren}${siren}ab\r\nc   ` } });
    expect(within(discussion).getByText('6/5,000 characters')).toBeInTheDocument();
  });

  it('announces the remaining characters only near the limit', async () => {
    open(reporterUser, serveDiscussion([], reporterUser));
    const discussion = await region();
    const textarea = within(discussion).getByLabelText('Comment');
    const live = discussion.querySelector('.comment-length-status')!;
    fireEvent.change(textarea, { target: { value: 'x'.repeat(100) } });
    expect(live).toHaveTextContent(/^$/);
    fireEvent.change(textarea, { target: { value: 'x'.repeat(4850) } });
    expect(live).toHaveTextContent('Fewer than 200 characters left.');
    fireEvent.change(textarea, { target: { value: 'x'.repeat(5000) } });
    expect(live).toHaveTextContent('Character limit reached.');
  });

  it('shows server validation on the field and keeps the text', async () => {
    open(responderUser, (url, init) => init.method === 'POST'
      ? jsonResponse(422, { detail: [{ loc: ['body', 'body'], msg: 'Value error, Comments cannot contain control characters.', type: 'value_error' }] })
      : jsonResponse(200, { items: [], total: 0, limit: 50, offset: 0 }));
    const discussion = await region();
    const textarea = within(discussion).getByLabelText('Message');
    await userEvent.type(textarea, 'Pasted text');
    await userEvent.click(within(discussion).getByRole('button', { name: 'Add internal note' }));
    expect(await within(discussion).findByText('Comments cannot contain control characters.')).toBeInTheDocument();
    expect(textarea).toHaveValue('Pasted text');
    expect(textarea).toHaveAttribute('aria-invalid', 'true');
  });

  it('shows refusals as a form error and keeps the text', async () => {
    open(reporterUser, (url, init) => init.method === 'POST'
      ? jsonResponse(403, { detail: 'Reporters can only post public comments.' })
      : jsonResponse(200, { items: [], total: 0, limit: 50, offset: 0 }));
    const discussion = await region();
    const textarea = within(discussion).getByLabelText('Comment');
    await userEvent.type(textarea, 'Hello');
    await userEvent.click(within(discussion).getByRole('button', { name: 'Post comment' }));
    const alert = await within(discussion).findByRole('alert');
    expect(alert).toHaveTextContent('Your comment was not posted');
    expect(alert).toHaveTextContent('Reporters can only post public comments.');
    expect(textarea).toHaveValue('Hello');
  });

  it('disables the form while posting', async () => {
    const response = deferred();
    open(reporterUser, async (url, init) => {
      if (init.method !== 'POST') return jsonResponse(200, { items: [], total: 0, limit: 50, offset: 0 });
      await response.promise;
      return jsonResponse(201, comment(9, 'Hello', reporterUser));
    });
    const discussion = await region();
    const textarea = within(discussion).getByLabelText('Comment');
    await userEvent.type(textarea, 'Hello');
    await userEvent.click(within(discussion).getByRole('button', { name: 'Post comment' }));
    expect(within(discussion).getByRole('button', { name: 'Posting…' })).toBeDisabled();
    expect(textarea).toBeDisabled();
    response.release();
    expect(await within(discussion).findByRole('button', { name: 'Post comment' })).toBeEnabled();
    expect(textarea).toBeEnabled();
  });

  it('shows loading, then an empty state worded for the reader', async () => {
    const response = deferred();
    open(reporterUser, async () => {
      await response.promise;
      return jsonResponse(200, { items: [], total: 0, limit: 50, offset: 0 });
    });
    const discussion = await region();
    expect(within(discussion).getByText('Loading discussion…')).toBeInTheDocument();
    response.release();
    expect(await within(discussion).findByText('No comments yet. Ask a question or add details for the response team.')).toBeInTheDocument();
  });

  it('staff see their own empty state', async () => {
    open(responderUser, serveDiscussion([], responderUser));
    expect(await within(await region()).findByText('No comments or internal notes yet.')).toBeInTheDocument();
  });

  it('offers Retry when the discussion cannot be loaded', async () => {
    let calls = 0;
    open(reporterUser, () => {
      calls += 1;
      return calls === 1 ? jsonResponse(500, {}) : jsonResponse(200, { items: [comment(1, 'Recovered')], total: 1, limit: 50, offset: 0 });
    });
    const discussion = await region();
    expect(await within(discussion).findByText('The discussion could not be loaded')).toBeInTheDocument();
    await userEvent.click(within(discussion).getByRole('button', { name: 'Retry' }));
    expect(await within(discussion).findByText('Recovered')).toBeInTheDocument();
  });

  it('shows more comments without duplicates', async () => {
    const fetchMock = open(responderUser, url => {
      const offset = Number(url.searchParams.get('offset'));
      // The second page overlaps the first, as if the page boundary moved.
      const items = offset === 0 ? [comment(1, 'One'), comment(2, 'Two')] : [comment(2, 'Two'), comment(3, 'Three')];
      return jsonResponse(200, { items, total: 3, limit: 50, offset });
    });
    const discussion = await region();
    expect(await within(discussion).findByText('Showing 2 of 3')).toBeInTheDocument();
    await userEvent.click(within(discussion).getByRole('button', { name: 'Show more comments' }));
    await within(discussion).findByText('Three');
    expect(within(discussion).getAllByRole('listitem').map(item => within(item).getByText(/One|Two|Three/).textContent)).toEqual(['One', 'Two', 'Three']);
    expect(within(discussion).queryByRole('button', { name: 'Show more comments' })).toBeNull();
    expect(commentCalls(fetchMock).map(call => call.url.searchParams.get('offset'))).toEqual(['0', '2']);
  });

  it('counts a new comment without showing it out of order while earlier pages are unloaded', async () => {
    open(responderUser, (url, init) => init.method === 'POST'
      ? jsonResponse(201, comment(9, 'Newest', responderUser, 'internal'))
      : jsonResponse(200, { items: [comment(1, 'One'), comment(2, 'Two')], total: 3, limit: 50, offset: 0 }));
    const discussion = await region();
    await within(discussion).findByText('Showing 2 of 3');
    await userEvent.type(within(discussion).getByLabelText('Message'), 'Newest');
    await userEvent.click(within(discussion).getByRole('button', { name: 'Add internal note' }));
    expect(await within(discussion).findByText('Showing 2 of 4')).toBeInTheDocument();
    await waitFor(() => expect(within(discussion).queryByText('Newest')).toBeNull());
  });
});
