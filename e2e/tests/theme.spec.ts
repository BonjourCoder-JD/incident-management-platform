import type { Page } from '@playwright/test';
import { expect, signIn, test } from '../support/fixtures';

const background = (page: Page) => page.evaluate(() => getComputedStyle(document.body).backgroundColor);
const LIGHT_BG = 'rgb(238, 242, 247)'; // blue-gray page (light)
const DARK_BG = 'rgb(11, 17, 32)';
const toggle = (page: Page) => page.getByRole('button', { name: 'Dark theme' });

test.describe('Theme', () => {
  test('follows the operating system until the viewer chooses', async ({ browser }) => {
    for (const [colorScheme, expected, pressed] of [['light', LIGHT_BG, 'false'], ['dark', DARK_BG, 'true']] as const) {
      const context = await browser.newContext({ colorScheme });
      const page = await context.newPage();
      await page.goto('/');
      await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
      expect(await background(page)).toBe(expected);
      await expect(toggle(page)).toHaveAttribute('aria-pressed', pressed);
      expect(await page.evaluate(() => document.documentElement.dataset.theme ?? null)).toBeNull();
      await context.close();
    }
  });

  test('a chosen theme persists across reloads and sign-in, and overrides the operating system', async ({ browser, makeUser }) => {
    const context = await browser.newContext({ colorScheme: 'light' });
    const page = await context.newPage();
    await page.goto('/');
    await toggle(page).focus();
    await page.keyboard.press('Enter'); // keyboard operable
    await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true');
    expect(await background(page)).toBe(DARK_BG);
    expect(await page.evaluate(() => [document.documentElement.dataset.theme, localStorage.getItem('imp-theme')])).toEqual(['dark', 'dark']);

    await page.reload();
    await expect(toggle(page)).toHaveAttribute('aria-pressed', 'true');
    expect(await background(page)).toBe(DARK_BG);
    await signIn(page, await makeUser('responder'));
    await page.goto('/dashboard');
    await expect(page.locator('.kpi-row .stat-tile').first()).toBeVisible();
    expect(await background(page)).toBe(DARK_BG);

    // Choosing light also wins over a dark operating system.
    await page.emulateMedia({ colorScheme: 'dark' });
    await toggle(page).click();
    expect(await background(page)).toBe(LIGHT_BG);
    await page.reload();
    await expect(toggle(page)).toHaveAttribute('aria-pressed', 'false');
    expect(await background(page)).toBe(LIGHT_BG);
    await context.close();

    // The choice belongs to that browser only.
    const fresh = await browser.newContext({ colorScheme: 'light' });
    const other = await fresh.newPage();
    await other.goto('/');
    await expect(toggle(other)).toHaveAttribute('aria-pressed', 'false');
    await fresh.close();
  });
});
