import { bootstrapAdmin, expect, pageApi, randomPassword, signIn, signOut, test } from '../support/fixtures';

test.describe('Authentication and sessions', () => {
  test('signed-out visitors see sign-in, keep the URL and get no data', async ({ page }) => {
    await page.goto('/incidents/123');
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    expect(new URL(page.url()).pathname).toBe('/incidents/123');
    expect((await pageApi(page, 'GET', '/incidents', undefined, { csrf: false })).status).toBe(401);
  });

  test('wrong passwords get a generic error; sign-in and sign-out work', async ({ page, makeUser }) => {
    const user = await makeUser('reporter');
    await page.goto('/');
    await page.getByLabel('Email').fill(user.email);
    await page.getByLabel('Password').fill('not-the-right-password');
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('alert')).toHaveText('Invalid email or password.');
    await expect(page.getByLabel('Password')).toHaveValue('');

    await signIn(page, user);
    await expect(page.getByRole('heading', { name: 'My incidents' })).toBeVisible();
    await signOut(page);
    await expect(page.getByText('You have signed out.')).toBeVisible();
    expect((await pageApi(page, 'GET', '/auth/me', undefined, { csrf: false })).status).toBe(401);
  });

  test('the session cookie is hardened and nothing is kept in web storage', async ({ page, context, makeUser }) => {
    await signIn(page, await makeUser('reporter'));
    const session = (await context.cookies()).find(cookie => cookie.name === 'imp_session');
    expect(session).toMatchObject({ httpOnly: true, secure: true, sameSite: 'Strict' });
    const exposed = await page.evaluate(() => ({ cookie: document.cookie, local: localStorage.length, session: sessionStorage.length }));
    expect(exposed).toEqual({ cookie: '', local: 0, session: 0 });
  });

  test('state-changing requests without the CSRF token are refused', async ({ page, makeUser }) => {
    await signIn(page, await makeUser('responder'));
    const result = await pageApi(page, 'POST', '/incidents', { title: 'CSRF probe', description: 'x', severity: 'low' }, { csrf: false });
    expect(result.status).toBe(403);
    expect(result.text).toContain('CSRF token missing or invalid.');
  });

  test('a role change or deactivation ends live sessions', async ({ page, admin, makeUser }) => {
    const user = await makeUser('reporter');
    await signIn(page, user);
    await expect(page.getByRole('heading', { name: 'My incidents' })).toBeVisible();

    await admin.updateUser(user.id, { role: 'responder' });
    await page.getByLabel('Severity').selectOption('high');
    await expect(page.getByText('Your session has ended. Sign in again to continue.')).toBeVisible();
    await signIn(page, user);
    await expect(page.getByRole('heading', { name: 'Incidents', exact: true })).toBeVisible();

    await admin.updateUser(user.id, { is_active: false });
    await page.reload();
    await expect(page.getByRole('heading', { name: 'Sign in' })).toBeVisible();
    await page.getByLabel('Email').fill(user.email);
    await page.getByLabel('Password').fill(user.password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('alert')).toHaveText('Invalid email or password.');
  });

  test('changing the password signs out other sessions and retires the old one', async ({ browser, page, makeUser }) => {
    const user = await makeUser('reporter');
    const otherDevice = await browser.newPage();
    await signIn(page, user);
    await signIn(otherDevice, user);

    const newPassword = randomPassword();
    await page.getByRole('link', { name: `Account settings for ${user.email}` }).click();
    await page.getByLabel('Current password').fill(user.password);
    await page.getByLabel('New password', { exact: true }).fill(newPassword);
    await page.getByLabel('Confirm new password').fill(newPassword);
    await page.getByRole('button', { name: 'Change password' }).click();
    await expect(page.getByText('Password changed. Your other devices have been signed out.')).toBeVisible();

    await otherDevice.getByLabel('Severity').selectOption('low');
    await expect(otherDevice.getByText('Your session has ended. Sign in again to continue.')).toBeVisible();
    await otherDevice.getByLabel('Email').fill(user.email);
    await otherDevice.getByLabel('Password').fill(user.password);
    await otherDevice.getByRole('button', { name: 'Sign in' }).click();
    await expect(otherDevice.getByRole('alert')).toHaveText('Invalid email or password.');
    await signIn(otherDevice, { email: user.email, password: newPassword });
    await otherDevice.close();
  });

  test('repeated failed sign-ins are rate limited, even for the right password', async ({ page, makeUser }) => {
    const user = await makeUser('reporter', 'ratelimited');
    await page.goto('/');
    for (let attempt = 1; attempt <= 5; attempt += 1) {
      await page.getByLabel('Email').fill(user.email);
      await page.getByLabel('Password').fill(`wrong-password-${attempt}`);
      await page.getByRole('button', { name: 'Sign in' }).click();
      await expect(page.getByRole('alert')).toHaveText('Invalid email or password.');
    }
    await page.getByLabel('Email').fill(user.email);
    await page.getByLabel('Password').fill(user.password);
    await page.getByRole('button', { name: 'Sign in' }).click();
    await expect(page.getByRole('alert')).toContainText('Too many failed sign-in attempts');
  });

  test('admins manage accounts but cannot remove their own access', async ({ page, admin }) => {
    const me = bootstrapAdmin();
    await signIn(page, me);
    await page.getByRole('link', { name: 'Users' }).click();
    const email = `e2e.created.${Date.now()}@example.com`;
    await page.getByLabel('Email').fill(email);
    await page.getByLabel('Temporary password').fill(randomPassword());
    await page.getByLabel('Role', { exact: true }).selectOption('responder');
    await page.getByRole('button', { name: 'Create account' }).click();
    await expect(page.getByText(`Account created for ${email}.`, { exact: false })).toBeVisible();
    await expect(page.getByRole('cell', { name: email, exact: true })).toBeVisible();

    await expect(page.getByLabel(`Role for ${me.email}`)).toBeDisabled();
    const meId = (await admin.call<{ user: { id: number } }>('GET', '/auth/me')).json.user.id;
    expect((await pageApi(page, 'PATCH', `/users/${meId}`, { role: 'reporter' })).status).toBe(409);
  });
});
