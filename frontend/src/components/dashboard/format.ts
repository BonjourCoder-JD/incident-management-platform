// Calendar dates from the API are plain YYYY-MM-DD values; format them in UTC so they never
// shift by a day in the viewer's time zone.
const shortDate = new Intl.DateTimeFormat(undefined, { month: 'short', day: 'numeric', timeZone: 'UTC' });
const longDate = new Intl.DateTimeFormat(undefined, { year: 'numeric', month: 'short', day: 'numeric', timeZone: 'UTC' });
const integer = new Intl.NumberFormat();

export function formatCount(value: number): string {
  return integer.format(value);
}

export function formatDay(isoDate: string, long = false): string {
  const date = new Date(`${isoDate}T00:00:00Z`);
  return Number.isNaN(date.getTime()) ? isoDate : (long ? longDate : shortDate).format(date);
}

export function formatPeriod(start: string, end: string): string {
  return start === end ? formatDay(start) : `${formatDay(start)} – ${formatDay(end)}`;
}

/** Durations from the API are hours; show the unit a person would use. */
export function formatHours(hours: number | null): string {
  if (hours === null) return '—';
  if (hours * 60 < 1) return '< 1 min';
  if (hours < 1) return `${Math.round(hours * 60)} min`;
  if (hours < 48) return `${hours.toFixed(1)} h`;
  return `${(hours / 24).toFixed(1)} days`;
}

export function percent(part: number, whole: number): string {
  return whole > 0 ? `${Math.round((part / whole) * 100)}%` : '0%';
}
