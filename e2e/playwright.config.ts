import { defineConfig, devices } from '@playwright/test';

const CI = Boolean(process.env.CI);

export default defineConfig({
  testDir: './tests',
  // One worker, no retries: tests share one disposable stack and flakiness must fail visibly.
  workers: 1,
  fullyParallel: false,
  retries: 0,
  forbidOnly: CI,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  // CI prints results to the job log only. The HTML report is local: its step titles can include typed values.
  reporter: CI ? [['list'], ['github']] : [['list'], ['html', { open: 'never' }]],
  outputDir: 'test-results',
  use: {
    baseURL: process.env.E2E_BASE_URL ?? 'http://localhost:15173',
    timezoneId: 'Europe/London',
    locale: 'en-US',
    acceptDownloads: true,
    screenshot: 'only-on-failure',
    // Traces record request bodies (including sign-in requests), so they are kept locally only.
    trace: CI ? 'off' : 'retain-on-failure',
    video: 'off',
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
});
