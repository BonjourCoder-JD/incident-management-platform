import type { Page } from '@playwright/test';
import { createIncident, expect, legacyIncidentIds, pageApi, signIn, test } from '../support/fixtures';

/** The figure shown in the stat tile with this label. */
const tile = (page: Page, label: string) => page.locator('.stat-tile', { hasText: label }).locator('.stat-value');

async function openDashboard(page: Page, query = '') {
  await page.goto(`/dashboard${query}`);
  // Stat tiles exist only once the figures have loaded (headings are role-based and appear earlier).
  await expect(page.locator('.stat-tile').first()).toBeVisible();
  await expect(page.locator('.dashboard-content')).toHaveAttribute('aria-busy', 'false');
}

test.describe('Analytics dashboard', () => {
  test('a reporter sees exact figures for their own incidents only', async ({ page, browser, makeUser }) => {
    const reporterA = await makeUser('reporter', 'a');
    const reporterB = await makeUser('reporter', 'b');
    const responder = await makeUser('responder');

    const pageB = await browser.newPage();
    await signIn(pageB, reporterB);
    await createIncident(pageB, { title: 'B one', severity: 'low' });
    await createIncident(pageB, { title: 'B two', severity: 'low' });

    await signIn(page, reporterA);
    const ids = [
      await createIncident(page, { title: 'A high', severity: 'high' }),
      await createIncident(page, { title: 'A low', severity: 'low' }),
      await createIncident(page, { title: 'A critical', severity: 'critical' }),
    ];
    const staff = await browser.newPage();
    await signIn(staff, responder);
    expect((await pageApi(staff, 'PATCH', `/incidents/${ids[0]}`, { status: 'resolved' })).status).toBe(200);
    // A resolution recorded inside a multi-field update must count too.
    expect((await pageApi(staff, 'PATCH', `/incidents/${ids[1]}`, { status: 'closed', title: 'A low (closed)' })).status).toBe(200);
    await staff.close();

    await openDashboard(page);
    await expect(page.getByRole('heading', { name: 'My dashboard' })).toBeVisible();
    await expect(page.getByText('Figures cover only the incidents you reported.')).toBeVisible();
    await expect(tile(page, 'Incidents in range')).toHaveText('3');
    await expect(tile(page, 'Open backlog now')).toHaveText('1');
    await expect(page.locator('.stat-tile', { hasText: 'Open backlog now' })).toContainText('Right now');
    await expect(page.locator('.stat-tile', { hasText: 'Incidents in range' })).toContainText('Selected dates');
    await expect(tile(page, 'Resolved or closed')).toHaveText('2');
    await expect(page.locator('.stat-tile', { hasText: 'Median time to resolve' })).toContainText('2 first resolved in range');
    const backlog = page.getByRole('region', { name: 'Current open backlog' });
    await expect(backlog).toContainText('1 in total');
    await expect(backlog.getByRole('listitem').filter({ hasText: 'Critical' })).toContainText('1');
    const status = page.getByRole('list', { name: 'Incidents by status' });
    await expect(status.getByRole('listitem')).toHaveText(['Open1 · 33%', 'Investigating0 · 0%', 'Resolved1 · 33%', 'Closed1 · 33%']);
    // Escalations are staff-only, in the API and on the page.
    await expect(page.getByRole('region', { name: 'SLA' }).getByText('Escalations')).toHaveCount(0);

    // "Right now" figures ignore the dates: a range before any incident keeps the current backlog.
    await openDashboard(page, '?from=2001-01-01&to=2001-01-31');
    await expect(tile(page, 'Incidents in range')).toHaveText('0');
    await expect(tile(page, 'Open backlog now')).toHaveText('1');
    await expect(page.locator('.stat-tile', { hasText: 'Open backlog now' })).toContainText('0 of them created in range');

    await openDashboard(pageB);
    await expect(tile(pageB, 'Incidents in range')).toHaveText('2');
    await expect(tile(pageB, 'Resolved or closed')).toHaveText('0');
    await pageB.close();
  });

  test('every headline figure and chart entry matches the analytics API for staff', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    // Compare with the exact response the page rendered (other tests add incidents in parallel).
    const response = page.waitForResponse(r => new URL(r.url()).pathname === '/api/analytics/summary' && r.ok());
    await openDashboard(page);
    const summary = await (await response).json();
    const sum = (rows: { count: number }[]) => rows.reduce((total, row) => total + row.count, 0);
    const n = (value: number) => value.toLocaleString();
    await expect(tile(page, 'Incidents in range')).toHaveText(n(summary.totals.incidents));
    await expect(tile(page, 'Open backlog now')).toHaveText(n(sum(summary.open_backlog_by_severity)));
    await expect(tile(page, 'Resolved or closed')).toHaveText(n(summary.totals.resolved_or_closed));
    await expect(tile(page, 'SLA overdue now')).toHaveText(n(sum(summary.sla.overdue_by_severity)));
    const status = page.getByRole('list', { name: 'Incidents by status' });
    for (const row of summary.by_status as { status: string; count: number }[]) {
      const label = row.status[0].toUpperCase() + row.status.slice(1);
      await expect(status.getByRole('listitem').filter({ hasText: label })).toContainText(n(row.count));
    }
    const severity = page.getByRole('list', { name: 'Incidents by severity' });
    for (const row of summary.by_severity as { severity: string; count: number }[]) {
      const label = row.severity[0].toUpperCase() + row.severity.slice(1);
      await expect(severity.getByRole('listitem').filter({ hasText: label })).toContainText(n(row.count));
    }
    await expect(page.getByRole('region', { name: 'SLA' }).locator('.stat-tile', { hasText: 'Escalations' }).locator('.stat-value'))
      .toHaveText(n(summary.sla.escalations));

    // The donut is keyboard-readable.
    const ring = page.getByRole('img', { name: /^Incidents by status:/ });
    await ring.focus();
    await expect(ring).toContainText(/(Open|Investigating|Resolved|Closed) · \d+%/);
    const first = await ring.textContent();
    await page.keyboard.press('ArrowRight');
    if (summary.by_status.filter((row: { count: number }) => row.count > 0).length > 1) await expect(ring).not.toHaveText(first!);
    await page.keyboard.press('Escape');
    await expect(ring).toContainText('incidents in total');
  });

  test('staff dashboards leave legacy incidents out of resolution times and say so', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    expect((await pageApi(page, 'PATCH', `/incidents/${legacyIncidentIds.resolvable}`, { status: 'resolved' })).status).toBe(200);
    await openDashboard(page);
    await expect(page.getByRole('heading', { name: 'Dashboard' })).toBeVisible();
    await expect(page.getByRole('region', { name: 'Time to first resolution' }))
      .toContainText(/\d+ incidents? (was|were) resolved in this range but created before audit logging began/);
  });

  test('presets, custom ranges and range errors are kept in the URL', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('reporter'));
    await openDashboard(page);
    await expect(page.getByRole('button', { name: 'Last 30 days' })).toHaveAttribute('aria-pressed', 'true');
    await page.getByRole('button', { name: 'Last 7 days' }).click();
    await expect(page).toHaveURL(/\/dashboard\?range=7d$/);

    await page.getByLabel('From', { exact: true }).fill('2001-01-01');
    await page.getByLabel('To', { exact: true }).fill('2001-01-31');
    await page.getByRole('button', { name: 'Apply' }).click();
    await expect(page).toHaveURL(/\?from=2001-01-01&to=2001-01-31$/);
    await expect(page.getByText('No incidents in this range')).toBeVisible();

    await page.goto('/dashboard?from=2020-01-01&to=2026-01-01');
    await expect(page.getByText('The range can be at most 366 days.')).toBeVisible();
    await expect(page.getByLabel('From', { exact: true })).toHaveAttribute('aria-invalid', 'true');
  });

  test('the trend chart offers a keyboard readout and a table', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('reporter'));
    await createIncident(page, { title: 'Trend one' });
    await createIncident(page, { title: 'Trend two' });
    await openDashboard(page);
    const chart = page.getByRole('img', { name: /Incidents created and first resolved per day: 2 created/ });
    await chart.focus();
    await page.keyboard.press('End');
    await expect(page.locator('.chart-tooltip')).toContainText('2 created');
    await page.getByRole('button', { name: 'Show as table' }).click();
    const createdTotal = await page.locator('table.data-table tbody tr').evaluateAll(rows =>
      rows.reduce((sum, row) => sum + Number(row.children[1].textContent), 0));
    expect(createdTotal).toBe(2);
  });
});
