import type { Page } from '@playwright/test';
import { readCsvDownload } from '../support/csv';
import { createIncident, expect, pageApi, signIn, signOut, test } from '../support/fixtures';

const discussion = (page: Page) => page.getByRole('region', { name: 'Discussion' });

async function openDiscussion(page: Page, id: number) {
  await page.goto(`/incidents/${id}`);
  const region = discussion(page);
  // Wait for the discussion to finish loading (list or empty state), not just the heading.
  await expect(region.getByText(/^No comments/).or(region.getByRole('list', { name: 'Comments, oldest first' }))).toBeVisible();
  return region;
}

/** A synthetic marker that must never reach a reporter. */
const canary = () => `CANARY-${Math.random().toString(36).slice(2, 10)}`;

test.describe('Discussion and internal notes', () => {
  test('a reporter and a responder hold a public conversation; internal notes stay with staff', async ({ page, makeUser }) => {
    const reporter = await makeUser('reporter');
    const responder = await makeUser('responder');
    const secret = canary();

    await signIn(page, reporter);
    const id = await createIncident(page, { title: 'Lost access badge', severity: 'medium' });
    let region = await openDiscussion(page, id);
    await expect(region.getByRole('radio')).toHaveCount(0);
    await region.getByLabel('Comment', { exact: true }).fill('I lost it near the car park.');
    await region.getByRole('button', { name: 'Post comment' }).click();
    await expect(region.getByText('Comment posted.')).toBeAttached();
    await expect(region.getByRole('listitem').filter({ hasText: 'I lost it near the car park.' }).getByText('Reporter', { exact: true })).toBeVisible();
    await signOut(page);

    await signIn(page, responder);
    region = await openDiscussion(page, id);
    await expect(region.getByText('I lost it near the car park.')).toBeVisible();
    // Internal is the default; the button names what will happen.
    await expect(region.getByRole('radio', { name: /Internal note/ })).toBeChecked();
    await region.getByLabel('Message').fill(`${secret} badge used at 02:00, check CCTV`);
    await region.getByRole('button', { name: 'Add internal note' }).click();
    const note = region.getByRole('listitem').filter({ hasText: secret });
    await expect(note.getByText('Internal note', { exact: true })).toBeVisible();
    await expect(note.getByText('Not visible to the reporter')).toBeVisible();
    await expect(region.getByLabel('Message')).toBeFocused();

    // Keyboard: arrow keys move between the visibility options.
    await region.getByRole('radio', { name: /Internal note/ }).focus();
    await page.keyboard.press('ArrowDown');
    await expect(region.getByRole('radio', { name: /Public reply/ })).toBeChecked();
    await expect(region.getByText('The reporter will see this reply.')).toBeVisible();
    await region.getByLabel('Message').fill('The badge has been disabled. Collect a new one at reception.');
    await region.getByRole('button', { name: 'Post public reply' }).click();
    await expect(region.getByText('Comment posted.')).toBeAttached();
    await signOut(page);

    await signIn(page, reporter);
    region = await openDiscussion(page, id);
    await expect(region.getByText('The badge has been disabled. Collect a new one at reception.')).toBeVisible();
    await expect(region.getByRole('listitem')).toHaveCount(2);
    await expect(page.locator('body')).not.toContainText(secret);
    await expect(region.getByText('Internal note')).toHaveCount(0);
  });

  test('internal notes never reach the reporter through the API, history, dashboard or CSV', async ({ page, browser, makeUser }) => {
    const reporter = await makeUser('reporter');
    const responder = await makeUser('responder');
    const secret = canary();

    await signIn(page, reporter);
    const id = await createIncident(page, { title: 'Unusual MFA prompts', severity: 'high' });

    const staffPage = await browser.newPage();
    await signIn(staffPage, responder);
    expect((await pageApi(staffPage, 'POST', `/incidents/${id}/comments`, { body: `${secret} possible insider`, visibility: 'internal' })).status).toBe(201);
    expect((await pageApi(staffPage, 'POST', `/incidents/${id}/comments`, { body: 'We are looking into this.', visibility: 'public' })).status).toBe(201);
    expect((await pageApi(staffPage, 'PATCH', `/incidents/${id}`, { status: 'investigating' })).status).toBe(200);
    const staffView = await pageApi<{ total: number }>(staffPage, 'GET', `/incidents/${id}/comments`);
    expect(staffView.json.total).toBe(2);
    expect(staffView.text).toContain(secret); // the marker is real, so its absence below means something
    await staffPage.close();

    const comments = await pageApi<{ total: number; items: { visibility: string }[] }>(page, 'GET', `/incidents/${id}/comments`);
    expect(comments.json.total).toBe(1);
    expect(comments.json.items.map(c => c.visibility)).toEqual(['public']);
    for (const path of [`/incidents/${id}/comments?limit=1&offset=1`, `/incidents/${id}`, `/incidents/${id}/events`, '/incidents', '/analytics/summary', '/auth/me']) {
      const result = await pageApi(page, 'GET', path);
      expect(result.status, path).toBe(200);
      expect(result.text, path).not.toContain(secret);
    }

    await page.goto(`/incidents/${id}`);
    await expect(discussion(page).getByText('We are looking into this.')).toBeVisible();
    await expect(page.getByRole('region', { name: 'History' }).getByText(/changed status/)).toBeVisible();
    await expect(page.locator('body')).not.toContainText(secret);

    await page.goto('/dashboard');
    await expect(page.locator('.stat-tile').first()).toBeVisible();
    await expect(page.locator('body')).not.toContainText(secret);

    await page.goto('/');
    await expect(page.getByRole('table')).toBeVisible();
    const download = page.waitForEvent('download');
    await page.getByRole('button', { name: /^Export CSV/ }).click();
    const csv = await readCsvDownload(await download);
    expect(csv.rows.map(row => row.title)).toEqual(['Unusual MFA prompts']);
    expect(csv.text).not.toContain(secret);
  });

  test('other reporters are refused through the UI and the API, exactly like a missing incident', async ({ page, browser, makeUser }) => {
    const owner = await makeUser('reporter', 'owner');
    const other = await makeUser('reporter', 'other');
    const ownerPage = await browser.newPage();
    await signIn(ownerPage, owner);
    const id = await createIncident(ownerPage, { title: 'Owner only' });
    expect((await pageApi(ownerPage, 'POST', `/incidents/${id}/comments`, { body: 'Private to my incident', visibility: 'public' })).status).toBe(201);
    await ownerPage.close();

    await signIn(page, other);
    await page.goto(`/incidents/${id}`);
    await expect(page.getByText(`Incident #${id} was not found`)).toBeVisible();
    await expect(discussion(page)).toHaveCount(0);

    const missing = await pageApi(page, 'GET', '/incidents/2000000000/comments');
    expect(missing.status).toBe(404);
    for (const [method, body] of [['GET', undefined], ['POST', { body: 'Let me in', visibility: 'public' }], ['POST', { body: 'Let me in', visibility: 'internal' }]] as const) {
      const result = await pageApi(page, method, `/incidents/${id}/comments`, body);
      expect([result.status, result.json], `${method} ${JSON.stringify(body)}`).toEqual([404, missing.json]);
    }
  });

  // The absence of edit and delete routes (405) is covered by backend/tests/test_comments.py.
  test('reporters cannot force an internal note, spoof the author or skip CSRF through the API', async ({ page, makeUser }) => {
    const reporter = await makeUser('reporter');
    const responder = await makeUser('responder');
    await signIn(page, reporter);
    const id = await createIncident(page, { title: 'API probing' });
    const secret = canary();

    const forced = await pageApi(page, 'POST', `/incidents/${id}/comments`, { body: secret, visibility: 'internal' });
    expect(forced.status).toBe(403);
    expect(forced.text).not.toContain(secret);
    expect((await pageApi(page, 'POST', `/incidents/${id}/comments`, { body: 'Spoof', visibility: 'public', author_id: responder.id })).status).toBe(422);
    expect((await pageApi(page, 'POST', `/incidents/${id}/comments`, { body: 'No token', visibility: 'public' }, { csrf: false })).status).toBe(403);
    const listed = await pageApi<{ total: number }>(page, 'GET', `/incidents/${id}/comments`);
    expect(listed.json.total).toBe(0);
  });

  test('comment text is shown as text, never run, with line breaks kept', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    const id = await createIncident(page, { title: 'Rendering check' });
    const payload = '<img src=x onerror="window.__pwned=1"><script>window.__pwned=2</script>';
    const region = await openDiscussion(page, id);
    await region.getByRole('radio', { name: /Public reply/ }).check();
    await region.getByLabel('Message').fill(`${payload}\nSecond line`);
    await region.getByRole('button', { name: 'Post public reply' }).click();
    const body = region.locator('.comment-body');
    await expect(body).toHaveText(`${payload}\nSecond line`);
    await page.reload();
    await expect(discussion(page).locator('.comment-body')).toHaveText(`${payload}\nSecond line`);
    await expect(discussion(page).locator('.comment-list img, .comment-list script')).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
  });
});
