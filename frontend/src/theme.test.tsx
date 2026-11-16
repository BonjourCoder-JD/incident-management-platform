import { act, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mockApi, renderAt } from './test/helpers';
import { applyTheme, storedTheme, THEME_STORAGE_KEY } from './theme';

type Listener = (event: { matches: boolean }) => void;

/** A controllable prefers-color-scheme media query (jsdom has none). */
function mockSystemTheme(dark: boolean) {
  const listeners = new Set<Listener>();
  const query = {
    matches: dark,
    media: '(prefers-color-scheme: dark)',
    addEventListener: (_: string, listener: Listener) => listeners.add(listener),
    removeEventListener: (_: string, listener: Listener) => listeners.delete(listener),
  };
  vi.stubGlobal('matchMedia', vi.fn(() => query));
  return {
    change(next: boolean) {
      query.matches = next;
      act(() => listeners.forEach(listener => listener({ matches: next })));
    },
  };
}

const toggle = () => screen.getByRole('button', { name: 'Dark theme' });

beforeEach(() => {
  window.localStorage.clear();
  applyTheme(null);
});

afterEach(() => {
  vi.unstubAllGlobals();
  window.localStorage.clear();
  applyTheme(null);
});

describe('Theme toggle', () => {
  it('follows the operating system until the viewer chooses', async () => {
    const system = mockSystemTheme(true);
    mockApi(undefined, { user: null });
    renderAt('/');
    expect(await screen.findByRole('heading', { name: 'Sign in' })).toBeInTheDocument();
    expect(toggle()).toHaveAttribute('aria-pressed', 'true');
    expect(toggle()).toHaveAccessibleDescription('Following your system setting.');
    expect(document.documentElement.dataset.theme).toBeUndefined();
    system.change(false);
    expect(toggle()).toHaveAttribute('aria-pressed', 'false');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBeNull();
  });

  it('stores and applies an explicit choice, which then ignores the operating system', async () => {
    const system = mockSystemTheme(false);
    mockApi();
    renderAt('/');
    await screen.findByRole('button', { name: 'Sign out' });
    await userEvent.click(toggle());
    expect(document.documentElement.dataset.theme).toBe('dark');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('dark');
    expect(toggle()).toHaveAttribute('aria-pressed', 'true');
    expect(toggle()).toHaveAccessibleDescription('Your choice is saved in this browser.');
    system.change(false);
    expect(toggle()).toHaveAttribute('aria-pressed', 'true');
    await userEvent.click(toggle());
    expect(document.documentElement.dataset.theme).toBe('light');
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe('light');
  });

  it('restores a saved choice on the next visit', async () => {
    mockSystemTheme(false);
    window.localStorage.setItem(THEME_STORAGE_KEY, 'dark');
    applyTheme(storedTheme()); // what main.tsx does before the first render
    expect(document.documentElement.dataset.theme).toBe('dark');
    mockApi(undefined, { user: null });
    renderAt('/');
    await screen.findByRole('heading', { name: 'Sign in' });
    expect(toggle()).toHaveAttribute('aria-pressed', 'true');
  });

  it('ignores unknown stored values and works when storage is unavailable', async () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, 'purple');
    expect(storedTheme()).toBeNull();
    mockSystemTheme(false);
    const getItem = vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => { throw new Error('blocked'); });
    const setItem = vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => { throw new Error('blocked'); });
    expect(storedTheme()).toBeNull();
    mockApi(undefined, { user: null });
    renderAt('/');
    await screen.findByRole('heading', { name: 'Sign in' });
    await userEvent.click(toggle());
    expect(document.documentElement.dataset.theme).toBe('dark'); // still applied for this page
    getItem.mockRestore();
    setItem.mockRestore();
  });

  it('works without matchMedia', async () => {
    vi.stubGlobal('matchMedia', undefined);
    mockApi(undefined, { user: null });
    renderAt('/');
    await screen.findByRole('heading', { name: 'Sign in' });
    expect(toggle()).toHaveAttribute('aria-pressed', 'false');
  });
});
