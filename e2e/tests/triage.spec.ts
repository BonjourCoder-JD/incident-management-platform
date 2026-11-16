import type { Page } from '@playwright/test';
import { createIncident, expect, pageApi, signIn, signOut, test } from '../support/fixtures';

// The end-to-end stack runs the deterministic offline provider (TRIAGE_PROVIDER=fake): no AI, no network, no cost.

const triage = (page: Page) => page.getByRole('region', { name: 'Triage assistant' });

async function openTriage(page: Page, id: number) {
  await page.goto(`/incidents/${id}`);
  const region = triage(page);
  await expect(region.getByRole('button', { name: 'Suggest triage' })).toBeVisible();
  return region;
}

async function suggest(page: Page, id: number) {
  const region = await openTriage(page, id);
  await region.getByRole('button', { name: 'Suggest triage' }).click();
  const heading = region.getByRole('heading', { name: /Suggestion #\d+/ }).first();
  await expect(heading).toBeFocused();
  const suggestionId = Number(/#(\d+)/.exec((await heading.textContent()) ?? '')?.[1]);
  return { region, suggestionId };
}

const unique = () => Math.random().toString(36).slice(2, 8);

test.describe('AI-assisted triage (test provider)', () => {
  test('a responder reviews, edits and accepts a suggestion; the change is audited and the reporter sees no triage', async ({ page, makeUser }) => {
    const reporter = await makeUser('reporter');
    const responder = await makeUser('responder');
    const tag = unique();
    await signIn(page, reporter);
    const earlier = await createIncident(page, { title: `Phishing email credential reset ${tag}`, severity: 'low' });
    const id = await createIncident(page, { title: `Phishing email led to credential theft ${tag}`, severity: 'medium' });
    // Reporters have no triage panel, and the API refuses them.
    await page.goto(`/incidents/${id}`);
    await expect(page.getByRole('region', { name: 'Discussion' })).toBeVisible();
    await expect(triage(page)).toHaveCount(0);
    expect((await pageApi(page, 'POST', `/incidents/${id}/triage-suggestions`)).status).toBe(403);
    expect((await pageApi(page, 'GET', '/triage/status')).status).toBe(403);
    await signOut(page);

    await signIn(page, responder);
    const { region, suggestionId } = await suggest(page, id);
    await expect(region.getByText(/Suggestions come from Test provider \(not AI\)/)).toBeVisible();
    await expect(region.getByText('Test suggestion (not AI)')).toBeVisible();
    await expect(region.getByText(/Suggested severity:/).getByText('High')).toBeVisible();
    await expect(region.getByRole('link', { name: new RegExp(`#${earlier} Phishing email credential reset`) })).toBeVisible();
    // Requesting changed nothing: the incident is still medium and the history has only the report.
    await expect(page.locator('.page-header').getByText('Medium')).toBeVisible();
    await expect(page.getByRole('region', { name: 'History' }).getByRole('listitem')).toHaveCount(1);

    await region.getByLabel('Severity to apply').selectOption('critical');
    await region.getByLabel(/Add the rationale and next steps as an internal note/).check();
    await region.getByRole('button', { name: 'Accept and apply' }).click();
    await expect(region.getByText('Accepted with edits')).toBeVisible();
    await expect(page.locator('.page-header').getByText('Critical')).toBeVisible();
    const history = page.getByRole('region', { name: 'History' });
    await expect(history.getByText(`Applied from triage suggestion #${suggestionId} after review`)).toBeVisible();
    await expect(history.getByRole('listitem').first()).toContainText(responder.email);
    const discussion = page.getByRole('region', { name: 'Discussion' });
    await expect(discussion.getByRole('listitem').filter({ hasText: `AI triage suggestion #${suggestionId} (reviewed)` }).getByText('Internal note', { exact: true })).toBeVisible();
    await signOut(page);

    // The reporter sees the audited severity change, but no suggestion, marker or internal note.
    await signIn(page, reporter);
    await page.goto(`/incidents/${id}`);
    await expect(page.locator('.page-header').getByText('Critical')).toBeVisible();
    await expect(page.getByRole('region', { name: 'History' }).getByText(responder.email)).toBeVisible();
    await expect(page.locator('body')).not.toContainText('triage suggestion');
    await expect(page.locator('body')).not.toContainText('AI triage');
  });

  test('rejecting a suggestion changes nothing', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    const id = await createIncident(page, { title: `Ransomware note on file server ${unique()}`, severity: 'low' });
    const { region } = await suggest(page, id);
    await region.getByLabel(/Reason/).fill('Already contained by the endpoint agent.');
    await region.getByRole('button', { name: 'Reject' }).click();
    await expect(region.getByText('Rejected', { exact: true })).toBeVisible();
    await expect(region.getByText(/reason: Already contained by the endpoint agent\./)).toBeVisible();
    await page.reload();
    await expect(page.locator('.page-header').getByText('Low')).toBeVisible();
    await expect(page.getByRole('region', { name: 'History' }).getByRole('listitem')).toHaveCount(1);
  });

  test('a suggestion goes stale when the incident changes, and cannot then be accepted', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    const id = await createIncident(page, { title: `Suspicious sign-in from new device ${unique()}`, severity: 'low' });
    const { region, suggestionId } = await suggest(page, id);
    await page.getByRole('group', { name: 'Change status' }).getByRole('button', { name: 'Investigating' }).click();
    await expect(region.getByText(/changed after this suggestion was made/)).toBeVisible();
    await expect(region.getByRole('button', { name: 'Accept and apply' })).toHaveCount(0);
    // The API refuses it too, even if a client tries.
    const direct = await pageApi(page, 'POST', `/triage-suggestions/${suggestionId}/decision`, { decision: 'accept' });
    expect(direct.status).toBe(409);
    await expect(page.locator('.page-header').getByText('Low')).toBeVisible();
  });

  test('a second request for the same incident is rate limited with a clear message', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    const id = await createIncident(page, { title: `Malware alert on laptop ${unique()}`, severity: 'medium' });
    const { region } = await suggest(page, id);
    await region.getByRole('button', { name: 'Suggest triage' }).click();
    await expect(region.getByText(/A suggestion was just requested for this incident\. Try again shortly\. You can try again in about \d+ seconds\./)).toBeVisible();
    await expect(region.getByRole('heading', { name: /Suggestion #\d+/ })).toHaveCount(1);
  });

  test('state-changing triage requests need the CSRF token', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    const id = await createIncident(page, { title: `CSRF probe ${unique()}`, severity: 'low' });
    const result = await pageApi(page, 'POST', `/incidents/${id}/triage-suggestions`, undefined, { csrf: false });
    expect(result.status).toBe(403);
    expect((await pageApi(page, 'GET', `/incidents/${id}/triage-suggestions`)).json).toEqual({ items: [] });
  });
});
