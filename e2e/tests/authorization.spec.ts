import { createIncident, expect, legacyIncidentIds, pageApi, signIn, test } from '../support/fixtures';

test.describe('Authorization', () => {
  test('reporters see only their own incidents and no triage or admin controls', async ({ page, browser, makeUser }) => {
    const reporterA = await makeUser('reporter', 'a');
    const reporterB = await makeUser('reporter', 'b');
    const otherPage = await browser.newPage();
    await signIn(otherPage, reporterB);
    await createIncident(otherPage, { title: 'Belongs to reporter B' });
    await otherPage.close();

    await signIn(page, reporterA);
    const mine = await createIncident(page, { title: 'Belongs to reporter A' });
    await page.goto('/');
    await expect(page.getByRole('heading', { name: 'My incidents' })).toBeVisible();
    await expect(page.getByRole('link', { name: 'Belongs to reporter A' })).toBeVisible();
    await expect(page.getByText('Showing 1–1 of 1')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Belongs to reporter B' })).toHaveCount(0);
    await expect(page.getByRole('link', { name: 'Users' })).toHaveCount(0);

    await page.goto(`/incidents/${mine}`);
    await expect(page.getByRole('group', { name: 'Change status' })).toHaveCount(0);
    await expect(page.getByText('Responders update the status as they work on your incident.')).toBeVisible();
    await page.getByRole('button', { name: 'Edit details' }).click();
    await expect(page.getByLabel(/Assignee/)).toHaveCount(0);

    await page.goto('/users');
    await expect(page.getByText('You do not have access to this page')).toBeVisible();
  });

  test('the API refuses reporter triage, spoofed ownership and cross-reporter access', async ({ page, browser, makeUser }) => {
    const reporterA = await makeUser('reporter', 'a');
    const reporterB = await makeUser('reporter', 'b');
    const otherPage = await browser.newPage();
    await signIn(otherPage, reporterB);
    const theirs = await createIncident(otherPage, { title: 'Reporter B secret' });
    await otherPage.close();

    await signIn(page, reporterA);
    const mine = await createIncident(page, { title: 'Reporter A incident' });
    expect((await pageApi(page, 'PATCH', `/incidents/${mine}`, { status: 'closed' })).status).toBe(403);
    expect((await pageApi(page, 'POST', '/incidents', { title: 't', description: 'd', severity: 'low', reporter_id: reporterB.id })).status).toBe(422);

    for (const id of [theirs, legacyIncidentIds.readOnly]) {
      for (const [method, path, body] of [['GET', `/incidents/${id}`], ['PATCH', `/incidents/${id}`, { title: 'hijack' }], ['GET', `/incidents/${id}/events`]] as const) {
        const result = await pageApi(page, method, path, body);
        expect(result.status, `${method} ${path}`).toBe(404);
        expect(result.text).not.toContain('secret');
      }
    }
    await page.goto(`/incidents/${theirs}`);
    await expect(page.getByText(`Incident #${theirs} was not found`)).toBeVisible();
    await expect(page.getByRole('region', { name: 'History' })).toHaveCount(0);
  });

  for (const role of ['responder', 'admin'] as const) {
    test(`${role}s see every incident, including legacy ones`, async ({ page, browser, makeUser }) => {
      const reporter = await makeUser('reporter');
      const reporterPage = await browser.newPage();
      await signIn(reporterPage, reporter);
      const reported = await createIncident(reporterPage, { title: `Reported for ${role} view` });
      await reporterPage.close();

      await signIn(page, await makeUser(role));
      await page.goto(`/incidents/${reported}`);
      await expect(page.getByRole('heading', { name: `Reported for ${role} view` })).toBeVisible();
      await expect(page.getByRole('group', { name: 'Change status' })).toBeVisible();
      await page.goto(`/incidents/${legacyIncidentIds.readOnly}`);
      await expect(page.getByRole('heading', { name: 'E2E legacy incident (read-only)' })).toBeVisible();
      await expect(page.getByRole('link', { name: 'Users' })).toHaveCount(role === 'admin' ? 1 : 0);
    });
  }
});
