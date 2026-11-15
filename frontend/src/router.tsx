import { useMemo, useSyncExternalStore, type AnchorHTMLAttributes, type MouseEvent } from 'react';

// A minimal History API router: a handful of routes do not justify a routing dependency.

export type Route =
  | { name: 'list'; search: URLSearchParams }
  | { name: 'new' }
  | { name: 'detail'; id: number }
  | { name: 'users' }
  | { name: 'account' }
  | { name: 'notFound' };

const NAVIGATE_EVENT = 'app:navigate';

export function parseRoute(pathname: string, search: string): Route {
  const path = pathname.replace(/\/+$/, '') || '/';
  if (path === '/' || path === '/incidents') return { name: 'list', search: new URLSearchParams(search) };
  if (path === '/incidents/new') return { name: 'new' };
  if (path === '/users') return { name: 'users' };
  if (path === '/account') return { name: 'account' };
  const match = /^\/incidents\/([1-9]\d{0,9})$/.exec(path);
  if (match) {
    const id = Number(match[1]);
    if (Number.isSafeInteger(id) && id <= 2147483647) return { name: 'detail', id };
  }
  return { name: 'notFound' };
}

function subscribe(onChange: () => void) {
  window.addEventListener('popstate', onChange);
  window.addEventListener(NAVIGATE_EVENT, onChange);
  return () => {
    window.removeEventListener('popstate', onChange);
    window.removeEventListener(NAVIGATE_EVENT, onChange);
  };
}

const getHref = () => window.location.pathname + window.location.search;

export function useLocation() {
  const href = useSyncExternalStore(subscribe, getHref);
  return useMemo(() => {
    const url = new URL(href, window.location.origin);
    return { pathname: url.pathname, search: url.search, route: parseRoute(url.pathname, url.search) };
  }, [href]);
}

export function navigate(to: string, options: { replace?: boolean } = {}) {
  if (options.replace) window.history.replaceState(null, '', to);
  else window.history.pushState(null, '', to);
  window.dispatchEvent(new Event(NAVIGATE_EVENT));
  if (!options.replace) window.scrollTo?.({ top: 0 });
}

type LinkProps = AnchorHTMLAttributes<HTMLAnchorElement> & { to: string };

export function Link({ to, onClick, ...props }: LinkProps) {
  function handleClick(event: MouseEvent<HTMLAnchorElement>) {
    onClick?.(event);
    // Let the browser handle new-tab and modified clicks.
    if (event.defaultPrevented || event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
    event.preventDefault();
    navigate(to);
  }
  return <a href={to} onClick={handleClick} {...props} />;
}
