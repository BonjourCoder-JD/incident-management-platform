import { createIncident, expect, legacyIncidentIds, pageApi, signIn, signOut, test } from '../support/fixtures';

const history = (page: import('@playwright/test').Page) => page.getByRole('region', { name: 'History' });

test.describe('Incident history', () => {
  test('records creation, edits, status and assignment with the right actors', async ({ page, makeUser }) => {
    const reporter = await makeUser('reporter');
    const responder = await makeUser('responder');
    await signIn(page, reporter);
    const id = await createIncident(page, { title: 'Phishing email', description: 'First version.\nNo one replied.' });
    await page.goto(`/incidents/${id}`);
    await page.getByRole('button', { name: 'Edit details' }).click();
    await page.getByLabel('Title').fill('Phishing email (supplier impersonation)');
    await page.getByLabel('Description').fill('Second version.');
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(history(page).getByText('updated 2 fields')).toBeVisible();
    const details = history(page).locator('details');
    await expect(details).not.toHaveAttribute('open', '');
    await history(page).getByText('Show description before and after').click();
    await expect(history(page).getByText(/No one replied\./)).toBeVisible();
    await signOut(page);

    await signIn(page, responder);
    await page.goto(`/incidents/${id}`);
    await page.getByRole('button', { name: 'Investigating' }).click();
    await expect(history(page).getByText('changed status from Open to Investigating')).toBeVisible();
    await page.getByRole('button', { name: 'Edit details' }).click();
    await page.getByLabel(/Assignee/).selectOption({ label: responder.email });
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(history(page).getByText(/assigned this incident to/)).toBeVisible();

    const events = await pageApi<{ items: { action: string; actor: { email: string } }[] }>(page, 'GET', `/incidents/${id}/events`);
    expect(events.json.items.map(e => `${e.action}:${e.actor.email}`)).toEqual([
      `assignment_changed:${responder.email}`,
      `status_changed:${responder.email}`,
      `updated:${reporter.email}`,
      `created:${reporter.email}`,
    ]);
  });

  test('no-op saves add nothing and client-supplied actors are rejected', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    const id = await createIncident(page, { title: 'Audit probe' });
    expect((await pageApi(page, 'PATCH', `/incidents/${id}`, { title: 'Audit probe' })).status).toBe(200);
    expect((await pageApi(page, 'PATCH', `/incidents/${id}`, { title: 'x', actor_id: 1 })).status).toBe(422);
    const events = await pageApi<{ total: number }>(page, 'GET', `/incidents/${id}/events`);
    expect(events.json.total).toBe(1);
  });

  test('older entries load on demand', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    const id = await createIncident(page, { title: 'Busy incident' });
    const statuses = ['investigating', 'resolved', 'open'];
    for (let i = 0; i < 12; i += 1) {
      expect((await pageApi(page, 'PATCH', `/incidents/${id}`, { status: statuses[i % 3] })).status).toBe(200);
    }
    await page.goto(`/incidents/${id}`);
    await expect(history(page).getByText('Showing 10 of 13')).toBeVisible();
    await expect(history(page).getByText('reported this incident')).toHaveCount(0);
    await history(page).getByRole('button', { name: 'Show older' }).click();
    await expect(history(page).getByText('reported this incident')).toBeVisible();
    await expect(history(page).getByRole('button', { name: 'Show older' })).toHaveCount(0);
  });

  test('legacy incidents explain that history starts later', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    await page.goto(`/incidents/${legacyIncidentIds.readOnly}`);
    await expect(history(page).getByText('No history recorded yet.')).toBeVisible();
  });

  test('HTML in audited values is shown as text', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('reporter'));
    const payload = '<img src=x onerror="window.__pwned=1">';
    const id = await createIncident(page, { title: 'Before' });
    expect((await pageApi(page, 'PATCH', `/incidents/${id}`, { title: payload })).status).toBe(200);
    await page.goto(`/incidents/${id}`);
    await expect(history(page).getByText(payload)).toBeVisible();
    await expect(history(page).locator('img')).toHaveCount(0);
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
  });
});
