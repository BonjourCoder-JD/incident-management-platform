import { createContext, useContext, useEffect, useState, type ReactNode } from 'react';
import { getDemoInfo } from './api/client';
import type { DemoInfo } from './api/types';

const DEMO_OFF: DemoInfo = { enabled: false, reset_time_utc: null, accounts: [] };
const DemoContext = createContext<DemoInfo>(DEMO_OFF);

/** Whether this is the public demo (asked once). Any failure means "not the demo": nothing is shown. */
export function DemoProvider({ children }: { children: ReactNode }) {
  const [info, setInfo] = useState<DemoInfo>(DEMO_OFF);
  useEffect(() => {
    const controller = new AbortController();
    getDemoInfo(controller.signal).then(setInfo).catch(() => { /* not the demo, or aborted */ });
    return () => controller.abort();
  }, []);
  return <DemoContext.Provider value={info}>{children}</DemoContext.Provider>;
}

export function useDemo(): DemoInfo {
  return useContext(DemoContext);
}

/** Shown on every page of the public demo. */
export function DemoBanner() {
  const demo = useDemo();
  if (!demo.enabled) return null;
  return (
    <div className="demo-banner" role="note" aria-label="Public demo">
      <p>
        <strong>Public demo.</strong> All data is synthetic. AI triage is simulated by rule-based test suggestions; no AI service is
        called. Everything resets daily at {demo.reset_time_utc ?? '04:00'} UTC.
      </p>
    </div>
  );
}
