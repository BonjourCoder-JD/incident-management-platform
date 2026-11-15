import { randomBytes } from 'node:crypto';
import { test as base, expect, request, type APIRequestContext, type Page } from '@playwright/test';

export { expect };

export type Role = 'reporter' | 'responder' | 'admin';

export interface TestUser {
  id: number;
  email: string;
  /** Random per run; never written to disk. */
  password: string;
  role: Role;
}

export interface ApiResult<T = unknown> {
  status: number;
  json: T;
  text: string;
}

function required(name: string): string {
  const value = process.env[name];
  if (!value) throw new Error(`${name} is not set. Run the suite through e2e/run.sh.`);
  return value;
}

const RUN_ID = process.env.E2E_RUN_ID ?? randomBytes(3).toString('hex');

/**
 * Synthetic, unique address on the reserved example.com domain. Random per call: Playwright loads this
 * module once per test file, so a module-level counter would repeat across files.
 */
export function syntheticEmail(label: string): string {
  return `e2e.${label}.${RUN_ID}.${randomBytes(5).toString('hex')}@example.com`;
}

export function randomPassword(): string {
  return randomBytes(18).toString('base64url');
}

export const legacyIncidentIds = {
  get readOnly() { return Number(required('E2E_LEGACY_READONLY_ID')); },
  get resolvable() { return Number(required('E2E_LEGACY_RESOLVE_ID')); },
};

export const bootstrapAdmin = () => ({ email: required('E2E_ADMIN_EMAIL'), password: required('E2E_ADMIN_PASSWORD') });

/**
 * API client signed in as the bootstrap admin. The session cookie and CSRF token are sent explicitly,
 * so this works over plain-HTTP localhost regardless of the cookie's Secure flag.
 */
export class AdminApi {
  private constructor(private readonly ctx: APIRequestContext, private readonly cookie: string, private readonly csrf: string) {}

  static async signIn(baseURL: string): Promise<AdminApi> {
    const ctx = await request.newContext({ baseURL });
    const response = await ctx.post('/api/auth/login', { data: bootstrapAdmin() });
    expect(response.status(), 'bootstrap admin sign-in').toBe(200);
    const setCookie = response.headersArray().find(h => h.name.toLowerCase() === 'set-cookie')?.value ?? '';
    const cookie = setCookie.split(';')[0];
    expect(cookie).toMatch(/^imp_session=/);
    return new AdminApi(ctx, cookie, (await response.json()).csrf_token);
  }

  async call<T = unknown>(method: string, path: string, data?: unknown): Promise<ApiResult<T>> {
    const response = await this.ctx.fetch(`/api${path}`, {
      method,
      data,
      headers: { Cookie: this.cookie, 'X-CSRF-Token': this.csrf },
    });
    const text = await response.text();
    let json: unknown = null;
    try { json = JSON.parse(text); } catch { /* not JSON */ }
    return { status: response.status(), json: json as T, text };
  }

  async createUser(role: Role, label: string = role): Promise<TestUser> {
    const password = randomPassword();
    const email = syntheticEmail(label);
    const result = await this.call<{ id: number }>('POST', '/users', { email, password, role });
    expect(result.status, `create ${role}`).toBe(201);
    return { id: result.json.id, email, password, role };
  }

  async updateUser(id: number, changes: { role?: Role; is_active?: boolean }): Promise<void> {
    expect((await this.call('PATCH', `/users/${id}`, changes)).status).toBe(200);
  }

  async dispose(): Promise<void> {
    await this.ctx.dispose();
  }
}

// Expected failed requests (the tests provoke these deliberately); anything else fails the test.
const EXPECTED_CONSOLE = /Failed to load resource: the server responded with a status of (401|403|404|409|413|422|429)/;

export const test = base.extend<{ makeUser: (role: Role, label?: string) => Promise<TestUser>; consoleGuard: void }, { admin: AdminApi }>({
  admin: [async ({}, use, workerInfo) => {
    const admin = await AdminApi.signIn(String(workerInfo.project.use.baseURL));
    await use(admin);
    await admin.dispose();
  }, { scope: 'worker' }],

  makeUser: async ({ admin }, use) => {
    await use((role, label) => admin.createUser(role, label));
  },

  consoleGuard: [async ({ page }, use) => {
    const errors: string[] = [];
    page.on('console', message => { if (message.type() === 'error' && !EXPECTED_CONSOLE.test(message.text())) errors.push(message.text()); });
    page.on('pageerror', error => errors.push(String(error)));
    await use();
    expect(errors, 'unexpected browser console errors').toEqual([]);
  }, { auto: true }],
});

// ---- Browser helpers -------------------------------------------------------------------------

export async function signIn(page: Page, user: { email: string; password: string }): Promise<void> {
  await page.goto('/');
  await page.getByLabel('Email').fill(user.email);
  await page.getByLabel('Password').fill(user.password);
  await page.getByRole('button', { name: 'Sign in' }).click();
  await expect(page.getByRole('button', { name: 'Sign out' })).toBeVisible();
}

export async function signOut(page: Page): Promise<void> {
  await page.getByRole('button', { name: 'Sign out' }).click();
  await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
}

/** Calls the API from the signed-in page, with its cookie and CSRF token, as the app itself does. */
export async function pageApi<T = unknown>(page: Page, method: string, path: string, body?: unknown, { csrf = true } = {}): Promise<ApiResult<T>> {
  return page.evaluate(async ({ method, path, body, csrf }) => {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    if (csrf) {
      const me = await fetch('/api/auth/me');
      if (me.ok) headers['X-CSRF-Token'] = (await me.json()).csrf_token;
    }
    const response = await fetch(`/api${path}`, { method, headers, body: body === undefined ? undefined : JSON.stringify(body) });
    const text = await response.text();
    let json: unknown = null;
    try { json = JSON.parse(text); } catch { /* not JSON */ }
    return { status: response.status, json, text };
  }, { method, path, body, csrf }) as Promise<ApiResult<T>>;
}

export interface NewIncident {
  title: string;
  description?: string;
  severity?: 'low' | 'medium' | 'high' | 'critical';
}

export async function createIncident(page: Page, incident: NewIncident): Promise<number> {
  const result = await pageApi<{ id: number }>(page, 'POST', '/incidents', {
    description: 'Synthetic end-to-end test incident.',
    severity: 'medium',
    ...incident,
  });
  expect(result.status, `create incident "${incident.title}"`).toBe(201);
  return result.json.id;
}

export async function hasNoHorizontalScroll(page: Page): Promise<boolean> {
  return page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth);
}
