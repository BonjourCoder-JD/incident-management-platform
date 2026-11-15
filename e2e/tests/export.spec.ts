import type { Page } from '@playwright/test';
import { readCsvDownload } from '../support/csv';
import { createIncident, expect, signIn, test } from '../support/fixtures';

const COLUMNS = ['id', 'title', 'description', 'severity', 'status', 'reporter_email', 'assignee_email', 'created_at_utc', 'updated_at_utc'];

async function exportCsv(page: Page, query = '') {
  await page.goto(`/${query}`);
  await expect(page.getByRole('table')).toBeVisible();
  const response = page.waitForResponse(r => r.url().includes('/api/exports/incidents.csv'));
  const download = page.waitForEvent('download');
  await page.getByRole('button', { name: /^Export CSV/ }).click();
  return { response: await response, csv: await readCsvDownload(await download) };
}

test.describe('CSV export', () => {
  test('reporters download only their own incidents, safely encoded', async ({ page, browser, makeUser }) => {
    const reporter = await makeUser('reporter', 'exporter');
    const other = await makeUser('reporter', 'other');
    const otherPage = await browser.newPage();
    await signIn(otherPage, other);
    await createIncident(otherPage, { title: 'Other reporter only' });
    await otherPage.close();

    await signIn(page, reporter);
    const formula = '=HYPERLINK("http://example.com","click me")';
    const ids = [
      await createIncident(page, { title: formula, severity: 'high' }),
      await createIncident(page, { title: 'Lost badge, "reception" desk', description: 'Line one\nLine two', severity: 'low' }),
      await createIncident(page, { title: 'Café Wi-Fi phishing 🔐', severity: 'high' }),
    ];
    const { response, csv } = await exportCsv(page);

    expect([...csv.bytes.subarray(0, 3)]).toEqual([0xef, 0xbb, 0xbf]);
    expect(csv.header).toEqual(COLUMNS);
    expect(csv.filename).toMatch(/^incidents-\d{4}-\d{2}-\d{2}\.csv$/);
    expect(response.headers()).toMatchObject({ 'cache-control': 'no-store', 'x-content-type-options': 'nosniff', 'content-type': 'text/csv; charset=utf-8' });
    expect(csv.rows.map(r => Number(r.id)).sort()).toEqual([...ids].sort());
    const byId = Object.fromEntries(csv.rows.map(r => [Number(r.id), r]));
    expect(byId[ids[0]].title).toBe("'" + formula);
    expect(byId[ids[1]]).toMatchObject({ title: 'Lost badge, "reception" desk', description: 'Line one\nLine two' });
    expect(byId[ids[2]].title).toBe('Café Wi-Fi phishing 🔐');
    expect(csv.rows.every(r => r.reporter_email === reporter.email && r.created_at_utc.endsWith('Z'))).toBe(true);
    expect(csv.text).not.toContain(other.email);
    expect(csv.text).not.toContain('Other reporter only');
    await expect(page.getByText(/CSV download started — 3 incidents \(/)).toBeVisible();
  });

  test('the export uses the list filters and names the file after them', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('reporter'));
    await createIncident(page, { title: 'Filtered high', severity: 'high' });
    await createIncident(page, { title: 'Filtered low', severity: 'low' });
    await page.goto('/?severity=high');
    await expect(page.getByRole('button', { name: 'Export CSV (High)' })).toBeVisible();
    const { csv } = await exportCsv(page, '?severity=high');
    expect(csv.rows.map(r => r.title)).toEqual(['Filtered high']);
    expect(csv.filename).toMatch(/-high\.csv$/);
  });

  test('an export at the limit succeeds and one above it is refused', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('reporter', 'limit'));
    for (let i = 1; i <= 5; i += 1) await createIncident(page, { title: `Limit incident ${i}` });
    const { csv } = await exportCsv(page); // this stack's limit is 5
    expect(csv.rows).toHaveLength(5);

    await createIncident(page, { title: 'Limit incident 6' });
    await page.goto('/');
    await expect(page.getByText('Showing 1–6 of 6')).toBeVisible();
    let downloaded = false;
    page.on('download', () => { downloaded = true; });
    await page.getByRole('button', { name: 'Export CSV (all visible incidents)' }).click();
    const alert = page.getByRole('alert');
    await expect(alert).toContainText('Too many incidents to export');
    await expect(alert).toContainText('6 incidents; the limit is 5. Narrow the filters and try again.');
    expect(downloaded).toBe(false);
  });
});
