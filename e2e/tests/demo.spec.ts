import AxeBuilder from '@axe-core/playwright';
import type { Page } from '@playwright/test';
import { bootstrapAdmin, createIncident, expect, pageApi, signIn, test } from '../support/fixtures';

// Runs only in the public-demo mode of e2e/run.sh (--demo): production images, DEMO_MODE, and the
// synthetic data created by the real daily reset (backend/app/demo_reset.py).
test.skip(!process.env.E2E_DEMO, 'public-demo run only (e2e/run.sh --demo)');

const REPORTER = { email: 'demo.reporter@example.com', password: 'demo-reporter-2026' };
const RESPONDER = { email: 'demo.responder@example.com', password: 'demo-responder-2026' };
const MY_INCIDENTS = [
  'Phishing email reported by the finance team', 'Suspicious VPN sign-in from a new country', 'Unusual outbound traffic from the build server',
  'Lost laptop with an encrypted disk', 'Cloned access badge used at reception', 'USB stick found in the car park',
];
const OTHERS = ['Ransomware note on the shared engineering drive', 'Domain admin credentials found on a paste site'];

async function expectNoBlockingViolations(page: Page, name: string) {
  const results = await new AxeBuilder({ page }).withTags(['wcag2a', 'wcag2aa', 'wcag21a', 'wcag21aa']).analyze();
  const blocking = results.violations.filter(v => v.impact === 'serious' || v.impact === 'critical').map(v => `${v.id}: ${v.help}`);
  expect(blocking, `accessibility violations on ${name}`).toEqual([]);
}

async function useDemoAccount(page: Page, role: 'reporter' | 'responder') {
  await page.goto('/');
  await page.getByRole('button', { name: `Use the ${role} account` }).click();
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
}

const banner = (page: Page) => page.getByRole('note', { name: 'Public demo' });

test.describe('Public demo', () => {
  test('the sign-in page publishes both demo accounts and explains the demo', async ({ page }) => {
    await page.goto('/');
    await expect(banner(page)).toContainText('All data is synthetic. AI triage is simulated');
    await expect(banner(page)).toContainText('resets daily at 04:00 UTC');
    const accounts = page.getByRole('region', { name: 'Public demo accounts' });
    await expect(accounts).toContainText(REPORTER.email);
    await expect(accounts).toContainText(RESPONDER.password);
    await expectNoBlockingViolations(page, 'demo sign-in page');
  });

  test('the demo reporter sees only their own synthetic incidents and can report one', async ({ page }) => {
    await useDemoAccount(page, 'reporter');
    await expect(banner(page)).toBeVisible();
    for (const title of MY_INCIDENTS) await expect(page.getByRole('link', { name: title })).toBeVisible();
    for (const title of OTHERS) await expect(page.getByRole('link', { name: title })).toHaveCount(0);

    const id = await createIncident(page, { title: 'Recruiter test: suspicious attachment', severity: 'medium' });
    await page.goto(`/incidents/${id}`);
    await expect(page.getByRole('heading', { name: 'Recruiter test: suspicious attachment' })).toBeVisible();
    await expect(page.getByRole('group', { name: 'Change status' })).toHaveCount(0); // responders only
    await expect(page.getByRole('region', { name: 'Triage assistant' })).toHaveCount(0);
    await page.goto('/incidents/1'); // another reporter's synthetic incident (the oldest)
    await expect(page.getByText('Incident #1 was not found')).toBeVisible();

    // The published password cannot be changed by a visitor.
    const change = await pageApi(page, 'PUT', '/auth/password', { current_password: REPORTER.password, new_password: 'another-long-passphrase' });
    expect(change.status).toBe(403);
  });

  test('the demo responder triages every incident with SLA states and simulated AI', async ({ page }) => {
    await useDemoAccount(page, 'responder');
    // The newest incidents of both reporters on the first page (everything is visible to staff).
    for (const title of [...MY_INCIDENTS.slice(0, 3), ...OTHERS]) await expect(page.getByRole('link', { name: title })).toBeVisible();
    await page.goto('/?sla=overdue');
    await expect(page.getByRole('link', { name: 'Suspicious VPN sign-in from a new country' })).toBeVisible();

    await page.goto('/');
    await page.getByRole('link', { name: 'Phishing email reported by the finance team' }).click();
    const triage = page.getByRole('region', { name: 'Triage assistant' });
    await expect(triage.getByText('Test suggestion (not AI)').first()).toBeVisible();
    await expect(triage.getByText(/fixed keyword rules for testing and demos, not AI/)).toBeVisible();
    await page.getByRole('group', { name: 'Change status' }).getByRole('button', { name: 'Investigating' }).click();
    await expect(page.getByText('Status changed to Investigating.')).toBeVisible();
    const discussion = page.getByRole('region', { name: 'Discussion' });
    await discussion.getByLabel('Message').fill('Recruiter test note: checked the sender domain.');
    await discussion.getByRole('button', { name: 'Add internal note' }).click();
    await expect(discussion.getByText('Recruiter test note: checked the sender domain.')).toBeVisible();

    await page.goto('/dashboard');
    await expect(page.locator('.kpi-row .stat-tile')).toHaveCount(5);
    await expect(page.locator('.stat-tile', { hasText: 'SLA overdue now' }).locator('.stat-value')).not.toHaveText('0');
    await expectNoBlockingViolations(page, 'demo dashboard');
  });

  test('demo accounts survive lockout attempts and cannot be changed by an administrator', async ({ page, browser }) => {
    await page.goto('/');
    for (let attempt = 0; attempt < 8; attempt += 1) {
      const failed = await pageApi(page, 'POST', '/auth/login', { email: REPORTER.email, password: 'wrong-password-123' }, { csrf: false });
      expect(failed.status).toBe(401);
    }
    await useDemoAccount(page, 'reporter'); // still works after more than five failures

    const adminContext = await browser.newContext();
    const admin = await adminContext.newPage();
    await signIn(admin, bootstrapAdmin());
    const users = await pageApi<{ id: number; email: string }[]>(admin, 'GET', '/users');
    const responder = users.json.find(user => user.email === RESPONDER.email)!;
    expect((await pageApi(admin, 'PATCH', `/users/${responder.id}`, { is_active: false })).status).toBe(403);
    expect((await pageApi(admin, 'PATCH', `/users/${responder.id}`, { role: 'admin' })).status).toBe(403);
    await adminContext.close();
  });
});
