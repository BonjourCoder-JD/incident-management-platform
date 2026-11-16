import { screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import type { IncidentEventPage, TriageStatus, TriageSuggestion } from '../api/types';
import { deferred, headerOf, jsonResponse, makeIncident, makeUser, mockApi, renderAt, TEST_CSRF, TRIAGE_OFF, triageCalls } from '../test/helpers';

const incident = makeIncident({ id: 7, severity: 'medium' });

const FAKE_STATUS: TriageStatus = {
  enabled: true, reason: null, provider: 'fake', provider_label: 'Test provider (not AI)', is_ai: false, model: 'fake-triage-v1',
  prompt_version: 'triage-v1', sent_fields: ['title', 'description', 'severity', 'status', 'duplicate candidate titles (up to 5)'],
  typical_cost_usd: 0.0005,
  limits: { user_daily_usd: 0.05, user_daily_remaining_usd: 0.05, user_hourly_requests: 10, user_hourly_remaining: 9, incident_cooldown_seconds: 60 },
};
const AI_STATUS: TriageStatus = { ...FAKE_STATUS, provider: 'anthropic', provider_label: 'Claude (claude-haiku-5-5)', is_ai: true, model: 'claude-haiku-5-5' };

function suggestion(overrides: Partial<TriageSuggestion> = {}): TriageSuggestion {
  return {
    id: 31, incident_id: 7, outcome: 'valid', message: null, severity: 'high',
    rationale: 'Credential theft affecting a privileged account.', next_steps: ['Reset the password.', 'Review sign-in logs.'],
    duplicates: [{ id: 3, title: 'Earlier credential phishing' }], candidates_sent: [{ id: 3, title: 'Earlier credential phishing' }],
    provider: 'fake', model: 'fake-triage-v1', prompt_version: 'triage-v1', requested_by: { id: 201, email: 'responder@example.com' },
    created_at: '2026-11-15T12:00:00Z', cost_usd: 0.000165, stale: false, decision: null, ...overrides,
  };
}

type TriageHandler = (url: URL, init: RequestInit) => Response | Promise<Response>;

function open({ status = FAKE_STATUS, items = [] as TriageSuggestion[], triage, user = makeUser('responder'), events }: {
  status?: TriageStatus; items?: TriageSuggestion[]; triage?: TriageHandler; user?: ReturnType<typeof makeUser>; events?: IncidentEventPage;
} = {}) {
  let current = incident;
  let listed = items;
  const fetchMock = mockApi(
    url => (url.pathname === '/api/incidents/7' ? jsonResponse(200, current) : jsonResponse(404, {})),
    {
      user,
      events,
      triage: async (url, init) => {
        if (url.pathname === '/api/triage/status') return jsonResponse(200, status);
        if (url.pathname === '/api/incidents/7/triage-suggestions' && (init.method ?? 'GET') === 'GET') return jsonResponse(200, { items: listed });
        const response = triage ? await triage(url, init) : jsonResponse(500, {});
        if (response.ok) {
          // Like the server: later list requests include new suggestions and recorded decisions.
          const saved = (await response.clone().json()) as TriageSuggestion;
          listed = [saved, ...listed.filter(item => item.id !== saved.id)];
          if (url.pathname.endsWith('/decision') && saved.decision?.applied_severity) current = { ...incident, severity: saved.decision.applied_severity };
        }
        return response;
      },
    },
  );
  renderAt('/incidents/7');
  return fetchMock;
}

async function panel() {
  const region = await screen.findByRole('region', { name: 'Triage assistant' });
  await within(region).findByText(/Suggestions come from|turned off|could not be loaded/);
  return region;
}

describe('Triage assistant', () => {
  it('is not shown to reporters, who never call the triage API', async () => {
    const fetchMock = open({ user: makeUser('reporter') });
    await screen.findByRole('heading', { name: incident.title });
    await screen.findByRole('region', { name: 'Discussion' });
    expect(screen.queryByRole('region', { name: 'Triage assistant' })).not.toBeInTheDocument();
    expect(triageCalls(fetchMock)).toEqual([]);
  });

  it('explains when triage is turned off and offers no request button', async () => {
    open({ status: TRIAGE_OFF });
    const region = await panel();
    expect(within(region).getByText('AI triage is turned off.')).toBeInTheDocument();
    expect(within(region).queryByRole('button', { name: 'Suggest triage' })).not.toBeInTheDocument();
  });

  it('labels the test provider as not AI and discloses exactly what is sent', async () => {
    open();
    const region = await panel();
    expect(within(region).getByText(/Test provider \(not AI\).*not AI\./)).toBeInTheDocument();
    await userEvent.click(within(region).getByText('What is sent'));
    for (const field of FAKE_STATUS.sent_fields) expect(within(region).getByText(field)).toBeInTheDocument();
    expect(within(region).getByText(/Comments, internal notes, people and email addresses are never sent/)).toBeInTheDocument();
    expect(within(region).getByText(/9 of 10 requests this hour/)).toBeInTheDocument();
  });

  it('labels real suggestions as AI-generated and warns that they can be wrong', async () => {
    open({ status: AI_STATUS, items: [suggestion({ provider: 'anthropic', model: 'claude-haiku-5-5' })] });
    const region = await panel();
    expect(within(region).getByText(/They can be wrong: review every suggestion/)).toBeInTheDocument();
    expect(within(region).getByText('AI-generated suggestion')).toBeInTheDocument();
    expect(within(region).getByText(/claude-haiku-5-5 · prompt triage-v1/)).toBeInTheDocument();
    await userEvent.click(within(region).getByText('What is sent to the AI service'));
    expect(within(region).getByText(/Typical cost per suggestion: under \$0\.01/)).toBeInTheDocument();
  });

  it('requests a suggestion with the CSRF token, then shows it for review without changing the incident', async () => {
    const pending = deferred();
    const fetchMock = open({
      triage: async (url, init) => {
        if (init.method === 'POST' && url.pathname === '/api/incidents/7/triage-suggestions') {
          await pending.promise;
          return jsonResponse(201, suggestion({ provider: 'fake' }));
        }
        return jsonResponse(500, {});
      },
    });
    const region = await panel();
    await userEvent.click(within(region).getByRole('button', { name: 'Suggest triage' }));
    expect(within(region).getByRole('button', { name: 'Requesting…' })).toBeDisabled();
    pending.release();
    const heading = await within(region).findByRole('heading', { name: /Suggestion #31/ });
    await waitFor(() => expect(heading).toHaveFocus());
    expect(within(region).getByText('Test suggestion (not AI)')).toBeInTheDocument();
    expect(within(region).getByText('Credential theft affecting a privileged account.')).toBeInTheDocument();
    expect(within(region).getByRole('link', { name: '#3 Earlier credential phishing' })).toHaveAttribute('href', '/incidents/3');
    expect(within(region).getByText(/Nothing changes until you accept/)).toBeInTheDocument();
    const posts = triageCalls(fetchMock).filter(call => call.init.method === 'POST');
    // Only the request itself: nothing is decided or applied without the reviewer.
    expect(posts.map(call => call.url.pathname)).toEqual(['/api/incidents/7/triage-suggestions']);
    expect(headerOf(posts[0].init, 'X-CSRF-Token')).toBe(TEST_CSRF);
    // The incident itself is unchanged: still medium.
    expect(screen.getAllByText('Medium').length).toBeGreaterThan(0);
  });

  it('renders model output as plain text, never as HTML', async () => {
    const hostile = '<img src=x onerror="alert(1)"><a href="javascript:alert(1)">click</a>';
    open({ items: [suggestion({ rationale: hostile, next_steps: [hostile] })] });
    const region = await panel();
    expect(within(region).getAllByText(hostile)).toHaveLength(2);
    expect(region.querySelector('img')).toBeNull();
    expect(within(region).queryByRole('link', { name: 'click' })).not.toBeInTheDocument();
  });

  it('accepts with an edited severity and an internal note, then reloads the incident and history', async () => {
    let body: unknown = null;
    const fetchMock = open({
      items: [suggestion()],
      triage: (url, init) => {
        body = JSON.parse(String(init.body));
        return jsonResponse(200, suggestion({
          decision: { decision: 'accepted_with_edits', applied_severity: 'critical', resulting_event_id: 90, note_comment_id: 12, reason: null,
            decider: { id: 201, email: 'responder@example.com' }, created_at: '2026-11-15T12:05:00Z' },
        }));
      },
    });
    const region = await panel();
    await userEvent.selectOptions(within(region).getByLabelText('Severity to apply'), 'critical');
    await userEvent.click(within(region).getByLabelText(/Add the rationale and next steps as an internal note/));
    expect(within(region).getByLabelText('Internal note')).toHaveValue(
      'AI triage suggestion #31 (reviewed): Credential theft affecting a privileged account.\nNext steps:\n- Reset the password.\n- Review sign-in logs.');
    const before = fetchMock.mock.calls.length;
    await userEvent.click(within(region).getByRole('button', { name: 'Accept and apply' }));
    expect(await within(region).findByText('Accepted with edits')).toBeInTheDocument();
    expect(within(region).getByText(/severity set to Critical · internal note added/)).toBeInTheDocument();
    await waitFor(() => expect(within(region).getByText('Accepted with edits').closest('p')).toHaveFocus());
    // The unedited note is left to the server's default, so only the choices are sent.
    expect(body).toEqual({ decision: 'accept', severity: 'critical', add_internal_note: true });
    await waitFor(() => {
      const later = fetchMock.mock.calls.slice(before).map(([input]) => new URL(String(input), 'http://localhost').pathname);
      expect(later).toEqual(expect.arrayContaining(['/api/incidents/7', '/api/incidents/7/events', '/api/incidents/7/comments']));
    });
  });

  it('sends an edited note and a trimmed reason', async () => {
    let body: unknown = null;
    open({ items: [suggestion()], triage: (_url, init) => { body = JSON.parse(String(init.body)); return jsonResponse(200, suggestion()); } });
    const region = await panel();
    await userEvent.click(within(region).getByLabelText(/Add the rationale/));
    const note = within(region).getByLabelText('Internal note');
    await userEvent.clear(note);
    await userEvent.type(note, 'Reviewed and agreed.');
    await userEvent.type(within(region).getByLabelText(/Reason/), '  Matches the playbook.  ');
    await userEvent.click(within(region).getByRole('button', { name: 'Accept and apply' }));
    await waitFor(() => expect(body).toEqual({ decision: 'accept', severity: 'high', add_internal_note: true, note: 'Reviewed and agreed.', reason: 'Matches the playbook.' }));
  });

  it('checks an edited note like a comment before sending anything', async () => {
    const fetchMock = open({ items: [suggestion()] });
    const region = await panel();
    await userEvent.click(within(region).getByLabelText(/Add the rationale/));
    const note = within(region).getByLabelText('Internal note');
    expect(within(region).getByText(/characters · visible to responders and admins only/)).toBeInTheDocument();
    await userEvent.clear(note);
    await userEvent.click(within(region).getByRole('button', { name: 'Accept and apply' }));
    expect(within(region).getByText('Enter a comment.')).toBeInTheDocument();
    expect(note).toHaveFocus();
    expect(note).toHaveAttribute('aria-invalid', 'true');
    expect(triageCalls(fetchMock).filter(call => call.init.method === 'POST')).toEqual([]);
  });

  it('rejects without applying anything', async () => {
    let body: unknown = null;
    const fetchMock = open({
      items: [suggestion()],
      triage: (_url, init) => {
        body = JSON.parse(String(init.body));
        return jsonResponse(200, suggestion({ decision: { decision: 'rejected', applied_severity: null, resulting_event_id: null, note_comment_id: null,
          reason: 'Not credential related.', decider: { id: 201, email: 'responder@example.com' }, created_at: '2026-11-15T12:05:00Z' } }));
      },
    });
    const region = await panel();
    await userEvent.type(within(region).getByLabelText(/Reason/), 'Not credential related.');
    const before = fetchMock.mock.calls.length;
    await userEvent.click(within(region).getByRole('button', { name: 'Reject' }));
    expect(await within(region).findByText('Rejected')).toBeInTheDocument();
    expect(within(region).getByText(/reason: Not credential related\./)).toBeInTheDocument();
    expect(body).toEqual({ decision: 'reject', reason: 'Not credential related.' });
    const later = fetchMock.mock.calls.slice(before).map(([input]) => new URL(String(input), 'http://localhost').pathname);
    expect(later).not.toContain('/api/incidents/7');
  });

  it('does not offer accepting a stale suggestion', async () => {
    open({ items: [suggestion({ stale: true })] });
    const region = await panel();
    expect(within(region).getByText(/changed after this suggestion was made/)).toBeInTheDocument();
    expect(within(region).queryByRole('button', { name: 'Accept and apply' })).not.toBeInTheDocument();
    expect(within(region).getByRole('button', { name: 'Reject' })).toBeEnabled();
  });

  it('shows the server message when the decision conflicts', async () => {
    open({ items: [suggestion()], triage: () => jsonResponse(409, { detail: 'The incident changed after this suggestion was made. Request a new suggestion.' }) });
    const region = await panel();
    await userEvent.click(within(region).getByRole('button', { name: 'Accept and apply' }));
    expect(await within(region).findByText('The incident changed after this suggestion was made. Request a new suggestion.')).toBeInTheDocument();
  });

  it('shows rate limits with the wait time', async () => {
    open({ triage: () => jsonResponse(429, { detail: 'A suggestion was just requested for this incident. Try again shortly.' }, { 'Retry-After': '42' }) });
    const region = await panel();
    await userEvent.click(within(region).getByRole('button', { name: 'Suggest triage' }));
    expect(await within(region).findByText('A suggestion was just requested for this incident. Try again shortly. You can try again in about 42 seconds.')).toBeInTheDocument();
    expect(within(region).getByRole('button', { name: 'Suggest triage' })).toBeEnabled();
  });

  it('explains unusable results and offers no review', async () => {
    open({ items: [suggestion({ outcome: 'timeout', severity: null, rationale: null, next_steps: [], duplicates: [],
      message: 'The AI service did not respond in time. Continue triage manually.' })] });
    const region = await panel();
    expect(within(region).getByText('The AI service did not respond in time. Continue triage manually.')).toBeInTheDocument();
    expect(within(region).queryByRole('button', { name: 'Accept and apply' })).not.toBeInTheDocument();
  });

  it('keeps earlier suggestions in a collapsed list', async () => {
    open({ items: [suggestion({ id: 33 }), suggestion({ id: 32 }), suggestion({ id: 31 })] });
    const region = await panel();
    expect(within(region).getByRole('heading', { name: /Suggestion #33/ })).toBeVisible();
    expect(within(region).getByText('Earlier suggestions (2)')).toBeInTheDocument();
  });

  it('shows a retry when triage cannot be loaded', async () => {
    let fail = true;
    mockApi(url => (url.pathname === '/api/incidents/7' ? jsonResponse(200, incident) : jsonResponse(404, {})), {
      triage: url => {
        if (fail) return jsonResponse(500, {});
        return url.pathname === '/api/triage/status' ? jsonResponse(200, FAKE_STATUS) : jsonResponse(200, { items: [] });
      },
    });
    renderAt('/incidents/7');
    const region = await panel();
    fail = false;
    await userEvent.click(within(region).getByRole('button', { name: 'Retry' }));
    expect(await within(region).findByRole('button', { name: 'Suggest triage' })).toBeInTheDocument();
  });
});

describe('History marker', () => {
  it('marks changes applied from a triage suggestion', async () => {
    open({
      events: {
        items: [{ id: 90, action: 'updated', changes: { severity: { from: 'medium', to: 'high' } }, actor: { id: 201, email: 'responder@example.com' },
          created_at: '2026-11-15T12:05:00Z', triage_suggestion_id: 31 }],
        total: 1, limit: 10, offset: 0, users: [],
      },
    });
    const history = await screen.findByRole('region', { name: 'History' });
    expect(await within(history).findByText('Applied from triage suggestion #31 after review')).toBeInTheDocument();
  });
});
