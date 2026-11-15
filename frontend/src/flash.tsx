import { createContext, useCallback, useContext, useEffect, useMemo, useRef, useState, type ReactNode } from 'react';
import { navigate, useLocation } from './router';

interface Flash {
  message: string;
  pathname: string;
}

interface FlashContextValue {
  flash: string | null;
  /** Navigates to `to` and shows `message` on that page until the user leaves it. */
  navigateWithFlash: (to: string, message: string) => void;
  dismiss: () => void;
}

const FlashContext = createContext<FlashContextValue | null>(null);

export function FlashProvider({ children }: { children: ReactNode }) {
  const [flash, setFlash] = useState<Flash | null>(null);
  const seen = useRef(false);
  const { pathname } = useLocation();

  const navigateWithFlash = useCallback((to: string, message: string) => {
    seen.current = false;
    setFlash({ message, pathname: new URL(to, window.location.origin).pathname });
    navigate(to);
  }, []);
  const dismiss = useCallback(() => setFlash(null), []);

  // Once the target page has shown the message, leaving that page clears it.
  useEffect(() => {
    if (!flash) return;
    if (pathname === flash.pathname) seen.current = true;
    else if (seen.current) setFlash(null);
  }, [pathname, flash]);

  const visible = flash && flash.pathname === pathname ? flash.message : null;
  const value = useMemo(() => ({ flash: visible, navigateWithFlash, dismiss }), [visible, navigateWithFlash, dismiss]);
  return <FlashContext.Provider value={value}>{children}</FlashContext.Provider>;
}

export function useFlash(): FlashContextValue {
  const value = useContext(FlashContext);
  if (!value) throw new Error('useFlash must be used inside FlashProvider');
  return value;
}
