import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { bootstrapAdmin, createIncident, expect, hasNoHorizontalScroll, signIn, test } from '../support/fixtures';

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
    const pages: [string, () => Promise<void>][] = [
      ['/', () => expect(page.getByRole('button', { name: /Export CSV/ })).toBeVisible()],
      [`/incidents/${id}`, () => expect(page.getByRole('region', { name: 'History' }).getByText('reported this incident')).toBeVisible()],
      ['/incidents/new', () => expect(page.getByLabel('Title')).toBeVisible()],
      ['/dashboard', () => expect(page.locator('.stat-tile').first()).toBeVisible()],
      ['/account', () => expect(page.getByRole('heading', { name: 'Your account' })).toBeVisible()],
    ];
    for (const [path, ready] of pages) {
      await page.goto(path);
      await ready();
      expect(await hasNoHorizontalScroll(page), `${path} scrolls horizontally`).toBe(true);
    }
  });
});
