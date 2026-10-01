// Helpers for the Analysis chart's intraday ranges (1D, 1W). Pure and import-free
// so `node --test` can run them without a bundler (see tests/).

/** Ranges served from intraday bars (5-minute and 15-minute). Mirrors INTRADAY_PARAMS in the backend. */
export const INTRADAY_RANGES = ['1d', '1w'];

export function isIntradayRange(range: string): boolean {
  return INTRADAY_RANGES.includes(range);
}

/** The daily view an unavailable intraday range falls back to. */
export const INTRADAY_FALLBACK_RANGE = '1mo';

export const INTRADAY_UNAVAILABLE_MESSAGE =
  'Intraday data is unavailable right now (the provider is rate-limited); showing daily bars.';

/** US markets trade in Eastern time. lightweight-charts treats every timestamp as UTC, which put the
 *  9:30 am open at 1:30 pm on the axis, so intraday ranges are formatted in this zone explicitly. */
export const MARKET_TIME_ZONE = 'America/New_York';

// Built once: constructing an Intl formatter is far more expensive than calling one, and the chart
// asks for a label per tick and per crosshair move. DST is handled by the zone, not by an offset.
const timeFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: MARKET_TIME_ZONE,
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});
const dayFormat = new Intl.DateTimeFormat('en-US', { timeZone: MARKET_TIME_ZONE, month: 'short', day: 'numeric' });
const monthFormat = new Intl.DateTimeFormat('en-US', { timeZone: MARKET_TIME_ZONE, month: 'short' });
const yearFormat = new Intl.DateTimeFormat('en-US', { timeZone: MARKET_TIME_ZONE, year: 'numeric' });
const fullFormat = new Intl.DateTimeFormat('en-US', {
  timeZone: MARKET_TIME_ZONE,
  month: 'short',
  day: 'numeric',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
});

// "24:05" is what hour12:false can produce at midnight in some engines; the market never trades then,
// but normalise so a label can never read 24:xx.
const fixMidnight = (s: string) => s.replace(/^24:/, '00:').replace(/ 24:/, ' 00:');

/** An axis tick label in Eastern time. `kind` is which granularity the chart wants for this tick. */
export function formatEtTick(seconds: number, kind: 'year' | 'month' | 'day' | 'time'): string {
  const date = new Date(seconds * 1000);
  if (kind === 'year') return yearFormat.format(date);
  if (kind === 'month') return monthFormat.format(date);
  if (kind === 'day') return dayFormat.format(date);
  return fixMidnight(timeFormat.format(date));
}

/** The label under the crosshair and in the hover legend: date and time, with the zone named. */
export function formatEtCrosshair(seconds: number): string {
  return `${fixMidnight(fullFormat.format(new Date(seconds * 1000)))} ET`;
}
