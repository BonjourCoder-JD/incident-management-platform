import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { bootstrapAdmin, createIncident, expect, hasNoHorizontalScroll, pageApi, signIn, signOut, slaScenario, test } from '../support/fixtures';

const BLOCKING = new Set(['serious', 'critical']);

/** Fails on serious or critical WCAG 2.1 A/AA violations; lists each rule and the affected elements. */
async function expectNoBlockingViolations(page: Page, name: string) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const blocking = results.violations
    .filter(v => v.impact && BLOCKING.has(v.impact))
    .map(v => `${v.impact}: ${v.id} — ${v.help} (${v.nodes.map(n => n.target.join(' ')).slice(0, 3).join('; ')})`);
  expect(blocking, `accessibility violations on ${name}`).toEqual([]);
}

async function seededIncident(page: Page) {
  return createIncident(page, { title: 'Accessibility check incident', severity: 'high' });
}

for (const colorScheme of ['light', 'dark'] as const) {
  test.describe(`Accessibility (${colorScheme})`, () => {
    test.use({ colorScheme });

    test('sign-in page', async ({ page }) => {
      await page.goto('/');
      await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
      await expectNoBlockingViolations(page, 'sign-in');
    });

    test('incident list, detail and create pages', async ({ page, makeUser }) => {
      await signIn(page, await makeUser('responder'));
      const id = await seededIncident(page);
      await page.goto('/');
      await expect(page.getByRole('table')).toBeVisible();
      await expectNoBlockingViolations(page, 'incident list');
      await page.goto(`/incidents/${id}`);
      await expect(page.getByRole('region', { name: 'History' }).getByText('reported this incident')).toBeVisible();
      await expectNoBlockingViolations(page, 'incident detail');
      await page.goto('/incidents/new');
      await page.getByRole('button', { name: 'Create incident' }).click(); // include validation messages
      await expect(page.getByText('Enter a title.')).toBeVisible();
      await expectNoBlockingViolations(page, 'create form with errors');
    });

    test('discussion with comments and internal notes, for staff and the reporter', async ({ page, browser, makeUser }) => {
      const reporter = await makeUser('reporter');
      await signIn(page, reporter);
      const id = await seededIncident(page);
      expect((await pageApi(page, 'POST', `/incidents/${id}/comments`, { body: 'Reporter follow-up question', visibility: 'public' })).status).toBe(201);
      // AxeBuilder needs a page from a context it can open; browser.newPage() is not supported.
      const staffContext = await browser.newContext({ colorScheme });
      const staffPage = await staffContext.newPage();
      await signIn(staffPage, await makeUser('responder'));
      for (const visibility of ['public', 'internal']) {
        expect((await pageApi(staffPage, 'POST', `/incidents/${id}/comments`, { body: `Synthetic ${visibility} entry`, visibility })).status).toBe(201);
      }
      await staffPage.goto(`/incidents/${id}`);
      const staffDiscussion = staffPage.getByRole('region', { name: 'Discussion' });
      await expect(staffDiscussion.getByText('Not visible to the reporter')).toBeVisible();
      await staffDiscussion.getByRole('radio', { name: /Public reply/ }).check();
      await staffDiscussion.getByRole('button', { name: 'Post public reply' }).click(); // empty: shows the field error
      await expect(staffDiscussion.getByText('Enter a comment.')).toBeVisible();
      await expectNoBlockingViolations(staffPage, 'discussion (staff)');
      await staffContext.close();

      await page.goto(`/incidents/${id}`);
      await expect(page.getByRole('region', { name: 'Discussion' }).getByText('Synthetic public entry')).toBeVisible();
      await expectNoBlockingViolations(page, 'discussion (reporter)');
      await signOut(page);
    });

    test('SLA card, SLA list filter and SLA policy page', async ({ page }) => {
      await signIn(page, bootstrapAdmin());
      await page.goto(`/incidents/${slaScenario.overdueId}`);
      await expect(page.getByRole('region', { name: 'SLA' }).getByRole('heading', { name: 'Escalations' })).toBeVisible();
      await expectNoBlockingViolations(page, 'SLA card with an escalation');
      await page.goto('/?sla=overdue');
      await expect(page.getByRole('link', { name: 'E2E SLA response overdue' })).toBeVisible();
      await expectNoBlockingViolations(page, 'SLA-filtered list');
      await page.goto('/sla');
      await page.getByRole('button', { name: 'Review change' }).click(); // empty reason: field error
      await expect(page.getByText('Enter a reason for the change.')).toBeVisible();
      await expectNoBlockingViolations(page, 'SLA policy page with an error');
      await page.getByLabel('Reason for the change').fill('Accessibility check only');
      await page.getByRole('button', { name: 'Review change' }).click();
      await expect(page.getByRole('group', { name: 'Confirm the new version' })).toBeVisible();
      await expectNoBlockingViolations(page, 'SLA policy review');
    });

    test('dashboard, users and account pages', async ({ page }) => {
      await signIn(page, bootstrapAdmin());
      await page.goto('/dashboard');
      await expect(page.locator('.stat-tile').first()).toBeVisible();
      await expectNoBlockingViolations(page, 'dashboard');
      await page.goto('/users');
      await expect(page.getByRole('heading', { name: 'Users' })).toBeVisible();
      await expectNoBlockingViolations(page, 'users');
      await page.goto('/account');
      await expect(page.getByRole('heading', { name: 'Your account' })).toBeVisible();
      await expectNoBlockingViolations(page, 'account');
    });
  });
}

test.describe('Phone width (390 px)', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test('main pages fit without horizontal scrolling', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    const id = await seededIncident(page);
    // Long unbroken text and an internal note must wrap inside the discussion.
    expect((await pageApi(page, 'POST', `/incidents/${id}/comments`, { body: `See ${'x'.repeat(300)} and https://example.com/${'path/'.repeat(40)}`, visibility: 'internal' })).status).toBe(201);
    const pages: [string, () => Promise<void>][] = [
      ['/', () => expect(page.getByRole('button', { name: /Export CSV/ })).toBeVisible()],
      [`/incidents/${id}`, () => expect(page.getByRole('region', { name: 'Discussion' }).getByText('Not visible to the reporter')).toBeVisible()],
      ['/incidents/new', () => expect(page.getByLabel('Title')).toBeVisible()],
      ['/dashboard', () => expect(page.locator('.stat-tile').first()).toBeVisible()],
      ['/?sla=overdue', () => expect(page.getByRole('link', { name: 'E2E SLA response overdue' })).toBeVisible()],
      [`/incidents/${slaScenario.reopenedId}`, () => expect(page.getByRole('region', { name: 'SLA' }).getByText('Earlier periods (1)')).toBeVisible()],
      ['/account', () => expect(page.getByRole('heading', { name: 'Your account' })).toBeVisible()],
    ];
    for (const [path, ready] of pages) {
      await page.goto(path);
      await ready();
      expect(await hasNoHorizontalScroll(page), `${path} scrolls horizontally`).toBe(true);
    }
  });
});

test.describe('Phone width (390 px), admin pages', () => {
  test.use({ viewport: { width: 390, height: 844 }, isMobile: true, hasTouch: true });

  test('the SLA policy page fits without horizontal scrolling', async ({ page }) => {
    await signIn(page, bootstrapAdmin());
    await page.goto('/sla');
    await expect(page.getByRole('table', { name: 'Current SLA targets' })).toBeVisible();
    expect(await hasNoHorizontalScroll(page), '/sla scrolls horizontally').toBe(true);
  });
});
