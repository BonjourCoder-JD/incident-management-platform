import type { Page } from '@playwright/test';
import { bootstrapAdmin, expect, pageApi, signIn, slaScenario, test } from '../support/fixtures';

const slaCard = (page: Page) => page.getByRole('region', { name: 'SLA' });

async function openSla(page: Page, id: number) {
  await page.goto(`/incidents/${id}`);
  const card = slaCard(page);
  await expect(card.getByText('Times from the server; updated every minute.')).toBeVisible();
  return card;
}

const row = (page: Page, title: string) => page.getByRole('row').filter({ has: page.getByRole('link', { name: title, exact: true }) });

test.describe('SLA tracking and escalation', () => {
  test('the real escalation command recorded exactly one escalation, then none', async () => {
    expect(slaScenario.escalationRuns).toEqual([1, 0]);
  });

  test('staff see SLA states, filter by them and see the escalation, attributed to the system', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    await page.goto('/?sla=overdue');
    const overdue = row(page, 'E2E SLA response overdue');
    await expect(overdue.getByText('Overdue', { exact: true })).toBeVisible();
    await expect(overdue.getByText('Escalated', { exact: true })).toBeVisible();
    await expect(overdue.getByText(/Response due/)).toBeVisible();
    await expect(row(page, 'E2E SLA response approaching')).toHaveCount(0);

    await page.getByLabel('SLA').selectOption('approaching');
    await expect(page).toHaveURL(/\?sla=approaching$/);
    await expect(row(page, 'E2E SLA response approaching').getByText('Approaching', { exact: true })).toBeVisible();

    await page.getByLabel('SLA').selectOption('escalated');
    await expect(row(page, 'E2E SLA response overdue')).toBeVisible();
    await expect(page.getByText('Showing 1–1 of 1')).toBeVisible();

    const card = await openSla(page, slaScenario.overdueId);
    await expect(card.getByRole('definition').first()).toContainText('Overdue');
    await expect(card.getByRole('heading', { name: 'Escalations' })).toBeVisible();
    await expect(card.getByRole('listitem').filter({ hasText: 'escalated automatically by the system' })).toHaveCount(1);
  });

  test('the reporter sees their SLA but never escalation details', async ({ page }) => {
    await signIn(page, slaScenario.reporter);
    await page.goto('/?sla=overdue');
    const overdue = row(page, 'E2E SLA response overdue');
    await expect(overdue.getByText('Overdue', { exact: true })).toBeVisible();
    await expect(overdue.getByText('Escalated', { exact: true })).toHaveCount(0);
    await expect(page.getByLabel('SLA').getByRole('option', { name: 'Escalated' })).toHaveCount(0);

    const card = await openSla(page, slaScenario.overdueId);
    await expect(card.getByRole('definition').first()).toContainText('Overdue');
    await expect(card.getByText(/Escalat/)).toHaveCount(0);

    const api = await pageApi<{ escalations: unknown; cycles: unknown[] }>(page, 'GET', `/incidents/${slaScenario.overdueId}/sla`);
    expect(api.status).toBe(200);
    expect(api.json.escalations).toBeNull();
    expect((await pageApi(page, 'GET', '/incidents?sla=escalated')).status).toBe(403);
    const list = await pageApi<{ items: { id: number; escalated: unknown }[] }>(page, 'GET', '/incidents?sla=overdue');
    expect(list.json.items.map(i => i.escalated)).toEqual(list.json.items.map(() => null));
  });

  test('met, breached and reopened incidents keep their outcomes', async ({ page }) => {
    await signIn(page, slaScenario.reporter);
    let card = await openSla(page, slaScenario.metId);
    await expect(card.getByRole('definition').nth(0)).toContainText('Met');
    await expect(card.getByRole('definition').nth(1)).toContainText('Met');

    card = await openSla(page, slaScenario.breachedId);
    await expect(card.getByRole('definition').nth(0)).toContainText(/Breached.*Breached by 1 h/);

    card = await openSla(page, slaScenario.reopenedId);
    await expect(card.getByText(/^Reopened .*: the resolution target restarted then\.$/)).toBeVisible();
    await expect(card.getByText('First response')).toBeVisible();
    await expect(card.getByRole('definition').nth(1)).toContainText('On track');
    await card.getByText('Earlier periods (1)').click();
    await expect(card.getByText(/Original report: resolution met, response met/)).toBeVisible();
  });

  test('other reporters get the same 404 for SLA data as for a missing incident', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('reporter'));
    const missing = await pageApi(page, 'GET', '/incidents/2000000000/sla');
    const theirs = await pageApi(page, 'GET', `/incidents/${slaScenario.overdueId}/sla`);
    expect([theirs.status, theirs.json]).toEqual([404, missing.json]);
    const overdue = await pageApi<{ items: { id: number }[] }>(page, 'GET', '/incidents?sla=overdue');
    expect(overdue.json.items.map(i => i.id)).not.toContain(slaScenario.overdueId);
  });

  test('admins change the policy with a reason and see it in the history; others are refused', async ({ page, browser, makeUser }) => {
    await signIn(page, bootstrapAdmin());
    await page.getByRole('link', { name: 'SLA policy' }).click();
    await expect(page.getByRole('heading', { name: 'SLA policy', level: 1 })).toBeVisible();
    const critical = page.getByRole('group', { name: 'Critical' });
    await critical.getByLabel('Response').fill('10');
    await page.getByRole('button', { name: 'Review change' }).click();
    await expect(page.getByLabel('Reason for the change')).toHaveAccessibleDescription(/Enter a reason for the change\./);
    await page.getByLabel('Reason for the change').fill('Synthetic end-to-end policy change');
    await page.getByRole('button', { name: 'Review change' }).click();
    const review = page.getByRole('group', { name: 'Confirm the new version' });
    await expect(review).toContainText('Critical: response 15 min → 10 min');
    await review.getByRole('button', { name: 'Create version' }).click();
    await expect(page.getByText(/Policy version #\d+ is now in force for newly started SLA periods\./)).toBeVisible();
    const current = page.getByRole('table', { name: 'Current SLA targets' });
    await expect(current.getByRole('row', { name: /Critical 10 min 4 h/ })).toBeVisible();
    await expect(page.getByRole('list', { name: 'Policy versions, newest first' }).getByText(/Changed by/).first()).toBeVisible();

    const other = await browser.newPage();
    await signIn(other, await makeUser('responder'));
    await other.goto('/sla');
    await expect(other.getByText('You do not have access to this page')).toBeVisible();
    await expect(other.getByRole('link', { name: 'SLA policy' })).toHaveCount(0);
    const refused = await pageApi(other, 'POST', '/sla/policy/versions', { reason: 'Not allowed', targets: [] });
    expect(refused.status).toBe(403);
    await other.close();
  });
});
