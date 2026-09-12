import { useEffect, useState } from 'react';
import { marketState, marketStateLabel, timeUntilNextTransition, type MarketState } from '../lib/marketHours';
import { formatRelativeTime } from './common';

const TONE: Record<MarketState, { color: string; bg: string }> = {
  open: { color: 'var(--green)', bg: 'var(--green-bg)' },
  pre: { color: 'var(--amber)', bg: 'var(--amber-bg)' },
  after: { color: 'var(--amber)', bg: 'var(--amber-bg)' },
  weekend: { color: 'var(--text-muted)', bg: 'rgba(255,255,255,0.05)' },
};

/**
 * Replaces the old always-green "Live Market Data" badge, which was
 * unconditional markup — it read "Live" with the backend on fire, and the
 * data behind it is the last daily close (cached up to 15 minutes), not a
 * live tick.
 *
 * `asOf` is when the client last actually received this data (react-query's
 * dataUpdatedAt). With provider TTLs between 15 minutes and a day, "when is
 * this from?" is the question the dashboard most needed to answer and never
 * did.
 */
export function MarketStatus({ asOf }: { asOf?: number }) {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const t = setInterval(() => setNow(new Date()), 30_000);
    return () => clearInterval(t);
  }, []);

  const state = marketState(now);
  const tone = TONE[state];

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
      <span className="badge" style={{ background: tone.bg, color: tone.color }}>
        <span
          style={{
            width: 6,
            height: 6,
            borderRadius: '50%',
            background: tone.color,
            display: 'inline-block',
          }}
        />
        {marketStateLabel(state)}
      </span>
      <span className="text-muted" style={{ fontSize: 12 }}>
        {timeUntilNextTransition(now)}
        {asOf ? ` · data ${formatRelativeTime(new Date(asOf).toISOString())}` : ''}
      </span>
    </div>
  );
}
