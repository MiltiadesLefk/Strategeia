import { useEffect, useState } from 'react';

/**
 * The current time, re-rendering every `intervalMs`. For countdowns ("opens
 * in 2h 15m") that should keep ticking between data fetches — the market
 * session is fetched rarely, but the minutes until the bell change constantly.
 */
export function useNow(intervalMs = 30_000): Date {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), intervalMs);
    return () => clearInterval(t);
  }, [intervalMs]);
  return now;
}
