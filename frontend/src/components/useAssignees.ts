import { useEffect, useState } from 'react';
import { listAssignees } from '../api/client';
import type { UserSummary } from '../api/types';

/** Loads the assignee directory when `enabled` (responders and admins); otherwise returns null. */
export function useAssignees(enabled: boolean): UserSummary[] | 'loading' | 'error' | null {
  const [assignees, setAssignees] = useState<UserSummary[] | 'loading' | 'error'>('loading');

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    listAssignees(controller.signal)
      .then(setAssignees)
      .catch(() => { if (!controller.signal.aborted) setAssignees('error'); });
    return () => controller.abort();
  }, [enabled]);

  return enabled ? assignees : null;
}
