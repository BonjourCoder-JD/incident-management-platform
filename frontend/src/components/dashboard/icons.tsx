/** Small decorative icons for the headline figures. Always paired with a text label; hidden from assistive technology. */
const PATHS = {
  incidents: 'M4 4h16v12H5.2L4 17.2V4z M8 8h8 M8 12h5',
  backlog: 'M4 7h16 M4 12h16 M4 17h10',
  resolved: 'M5 12.5l4.5 4.5L19 7.5',
  duration: 'M12 7v5l3 2 M12 3a9 9 0 1 0 0 18a9 9 0 0 0 0-18z',
  overdue: 'M12 4l9 16H3L12 4z M12 10v4 M12 17h.01',
} as const;

export type IconName = keyof typeof PATHS;

export function DashboardIcon({ name }: { name: IconName }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" aria-hidden="true" focusable="false">
      <path d={PATHS[name]} fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}
