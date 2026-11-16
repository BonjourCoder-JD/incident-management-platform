import { useEffect, useState } from 'react';

/**
 * Light and dark themes. Until the viewer picks one, the operating-system setting applies (CSS
 * `color-scheme: light dark` with `light-dark()` tokens). A choice is stored in this browser only
 * and applied as `data-theme` on <html>. Storage can be unavailable (private windows, blocked
 * site data); the theme then still works for the current page.
 */
export type Theme = 'light' | 'dark';

export const THEME_STORAGE_KEY = 'imp-theme';
const DARK_QUERY = '(prefers-color-scheme: dark)';

export function storedTheme(): Theme | null {
  try {
    const value = window.localStorage.getItem(THEME_STORAGE_KEY);
    return value === 'light' || value === 'dark' ? value : null;
  } catch {
    return null;
  }
}

function saveTheme(theme: Theme) {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Not persisted; the choice still applies until the page is reloaded.
  }
}

/** Sets or clears the explicit theme on <html>. Called once at start-up, before the first render. */
export function applyTheme(theme: Theme | null) {
  const root = document.documentElement;
  if (theme) root.dataset.theme = theme;
  else delete root.dataset.theme;
}

function systemTheme(): Theme {
  try {
    return window.matchMedia?.(DARK_QUERY).matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

/** The theme in effect, whether it was chosen, and a setter that stores the choice. */
export function useTheme() {
  const [chosen, setChosen] = useState<Theme | null>(storedTheme);
  const [system, setSystem] = useState<Theme>(systemTheme);

  useEffect(() => {
    const query = typeof window.matchMedia === 'function' ? window.matchMedia(DARK_QUERY) : null;
    if (!query) return;
    const update = () => setSystem(query.matches ? 'dark' : 'light');
    query.addEventListener('change', update);
    return () => query.removeEventListener('change', update);
  }, []);

  function setTheme(theme: Theme) {
    saveTheme(theme);
    applyTheme(theme);
    setChosen(theme);
  }

  return { theme: chosen ?? system, chosen: chosen !== null, setTheme };
}
