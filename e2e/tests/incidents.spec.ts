import { createIncident, expect, pageApi, signIn, signOut, test } from '../support/fixtures';

test.describe('Incidents', () => {
  test('the create form validates input, then creates and opens the incident', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('reporter'));
    await page.getByRole('link', { name: 'New incident' }).click();
    await page.getByRole('button', { name: 'Create incident' }).click();
    for (const message of ['Enter a title.', 'Enter a description.', 'Select a severity.']) {
      await expect(page.getByText(message)).toBeVisible();
    }
    await expect(page.getByLabel('Title')).toBeFocused();
    await expect(page.getByLabel(/Assignee/)).toHaveCount(0); // reporters cannot assign

    await page.getByLabel('Title').fill('Phishing email to finance');
    await page.getByLabel('Description').fill('Asked to change supplier bank details.\nNobody replied.');
    await page.getByLabel('Severity').selectOption('medium');
    await page.getByRole('button', { name: 'Create incident' }).click();
    await expect(page).toHaveURL(/\/incidents\/\d+$/);
    const id = new URL(page.url()).pathname.split('/').pop();
    await expect(page.getByText(`Incident #${id} was created.`)).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Phishing email to finance' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'History' }).getByText('reported this incident')).toBeVisible();
  });

  test('filters and pagination live in the URL', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('reporter', 'paging'));
    for (let i = 1; i <= 12; i += 1) {
      await createIncident(page, { title: `Paging incident ${i}`, severity: i <= 4 ? 'high' : 'low' });
    }
    await page.goto('/');
    await expect(page.getByText('Showing 1–10 of 12')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Paging incident 12' })).toBeVisible();

    await page.getByRole('button', { name: 'Next' }).click();
    await expect(page).toHaveURL(/\?page=2$/);
    await expect(page.getByText('Showing 11–12 of 12')).toBeVisible();
    await expect(page.getByRole('link', { name: 'Paging incident 1', exact: true })).toBeVisible();
    await page.goBack();
    await expect(page.getByText('Showing 1–10 of 12')).toBeVisible();

    await page.getByLabel('Severity').selectOption('high');
    await expect(page).toHaveURL(/\?severity=high$/);
    await expect(page.getByText('Showing 1–4 of 4')).toBeVisible();
    await page.getByLabel('Status').selectOption('closed');
    await expect(page.getByText('No incidents match these filters')).toBeVisible();
    await page.getByRole('button', { name: 'Clear filters' }).first().click();
    await expect(page.getByText('Showing 1–10 of 12')).toBeVisible();
  });

  test('responders change status, edit and assign; the reporter sees the result', async ({ page, makeUser }) => {
    const reporter = await makeUser('reporter');
    const responder = await makeUser('responder');
    await signIn(page, reporter);
    const id = await createIncident(page, { title: 'Laptop stolen from car', severity: 'high' });
    await signOut(page);

    await signIn(page, responder);
    await page.goto(`/incidents/${id}`);
    await page.getByRole('button', { name: 'Investigating' }).click();
    await expect(page.getByText('Status changed to Investigating.')).toBeVisible();
    await expect(page.getByRole('button', { name: 'Investigating' })).toHaveAttribute('aria-pressed', 'true');

    await page.getByRole('button', { name: 'Edit details' }).click();
    await page.getByLabel('Title').fill('Laptop stolen from car (police report filed)');
    await page.getByLabel(/Assignee/).selectOption({ label: responder.email });
    await page.getByRole('button', { name: 'Save changes' }).click();
    await expect(page.getByText('Incident details saved.')).toBeVisible();
    await expect(page.getByRole('heading', { name: 'Laptop stolen from car (police report filed)' })).toBeVisible();
    await signOut(page);

    await signIn(page, reporter);
    await page.goto(`/incidents/${id}`);
    await expect(page.locator('.badge-row').getByText('Investigating')).toBeVisible();
    await expect(page.getByRole('definition').filter({ hasText: responder.email.split('@')[0] })).toBeVisible();
  });

  test('unknown and malformed incident URLs show not-found states', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    await page.goto('/incidents/999999');
    await expect(page.getByText('Incident #999999 was not found')).toBeVisible();
    await page.goto('/incidents/abc');
    await expect(page.getByText('Page not found')).toBeVisible();
  });

  test('HTML typed into an incident is shown as text, never run', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('reporter'));
    const payload = '<img src=x onerror="window.__pwned=1">';
    const id = await createIncident(page, { title: payload, description: payload });
    await page.goto(`/incidents/${id}`);
    await expect(page.getByRole('heading', { name: payload })).toBeVisible();
    await page.goto('/');
    await expect(page.getByRole('link', { name: payload })).toBeVisible();
    expect(await page.evaluate(() => (window as unknown as { __pwned?: number }).__pwned)).toBeUndefined();
    await expect(page.locator('main img')).toHaveCount(0);
  });

  test('the first Tab stop is the skip link', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('reporter'));
    await page.goto('/incidents/new');
    await page.keyboard.press('Tab');
    await expect(page.getByRole('link', { name: 'Skip to content' })).toBeFocused();
  });

  test('saving nothing changes nothing', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    const id = await createIncident(page, { title: 'No-op probe' });
    const before = await pageApi<{ updated_at: string }>(page, 'GET', `/incidents/${id}`);
    const after = await pageApi<{ updated_at: string }>(page, 'PATCH', `/incidents/${id}`, { title: 'No-op probe' });
    expect(after.status).toBe(200);
    expect(after.json.updated_at).toBe(before.json.updated_at);
  });
});
