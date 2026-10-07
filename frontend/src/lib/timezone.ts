/** The zone clock times are shown in. A per-browser display preference (localStorage), not a server
 *  setting: it changes how an instant is written, never when anything runs. Market hours, the
 *  09:45 redo, scheduled notes and the intraday chart axis stay on New York time. */
const KEY = 'strategeia.displayTimeZone';

/** Stored IANA zone, or undefined = the browser's own zone (what `toLocale*` does by default). */
export function getDisplayTimeZone(): string | undefined {
  try {
    const v = localStorage.getItem(KEY);
    if (v && isValidTimeZone(v)) return v;
  } catch {
    /* storage can be blocked: fall back to the browser zone */
  }
  return undefined;
}

export function setDisplayTimeZone(zone: string | undefined): void {
  try {
    if (zone) localStorage.setItem(KEY, zone);
    else localStorage.removeItem(KEY);
  } catch {
    /* ignore */
  }
}

export function isValidTimeZone(zone: string): boolean {
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: zone });
    return true;
  } catch {
    return false;
  }
}

export function browserTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
}

const FALLBACK_ZONES = [
  'UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles', 'America/Sao_Paulo',
  'Europe/London', 'Europe/Paris', 'Europe/Athens', 'Europe/Moscow', 'Asia/Dubai', 'Asia/Kolkata',
  'Asia/Singapore', 'Asia/Tokyo', 'Australia/Sydney', 'Pacific/Auckland',
];

export function listTimeZones(): string[] {
  const fn = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf;
  const zones = fn ? fn('timeZone') : FALLBACK_ZONES;
  return zones.includes('UTC') ? zones : ['UTC', ...zones];
}
