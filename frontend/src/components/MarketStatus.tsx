import { useMarketSession } from '../api/hooks';
import type { MarketSessionState } from '../api/types';
import { marketStateLabel, timeUntilNextTransition } from '../lib/marketHours';
import { useNow } from '../lib/useNow';
import { formatRelativeTime } from './common';

const TONE: Record<MarketSessionState, { color: string; bg: string }> = {
  open: { color: 'var(--green)', bg: 'var(--green-bg)' },
  pre: { color: 'var(--amber)', bg: 'var(--amber-bg)' },
  after: { color: 'var(--amber)', bg: 'var(--amber-bg)' },
  closed: { color: 'var(--text-muted)', bg: 'rgba(255,255,255,0.05)' },
  holiday: { color: 'var(--text-muted)', bg: 'rgba(255,255,255,0.05)' },
};

/**
 * Replaces the old always-green "Live Market Data" badge, which was
 * unconditional markup — it read "Live" with the backend on fire, and the
 * data behind it is the last daily close (cached up to 15 minutes), not a
 * live tick.
 *
 * The session (holidays and 1:00 pm early closes included) comes from the
 * backend's calendar, the same one the paper engine uses to refuse off-hours
 * fills, so the badge can't say "open" on a day the engine won't trade. The
 * countdown ticks locally every 30 s between fetches.
 *
 * `asOf` is when the client last actually received this data (react-query's
 * dataUpdatedAt). With provider TTLs between 15 minutes and a day, "when is
 * this from?" is the question the dashboard most needed to answer and never
 * did.
 */
export function MarketStatus({ asOf }: { asOf?: number }) {
  const { data: session, isError } = useMarketSession();
  const now = useNow();

  const dataAge = asOf ? `data ${formatRelativeTime(new Date(asOf).toISOString())}` : '';

  if (!session) {
    return (
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
        <span className="badge badge-neutral">{isError ? 'Market status unavailable' : 'Market status…'}</span>
        {dataAge && (
          <span className="text-muted" style={{ fontSize: 12 }}>
            {dataAge}
          </span>
        )}
      </div>
    );
  }

  const tone = TONE[session.state];

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
        {marketStateLabel(session)}
      </span>
      <span className="text-muted" style={{ fontSize: 12 }}>
        {timeUntilNextTransition(session, now)}
        {dataAge ? ` · ${dataAge}` : ''}
      </span>
    </div>
  );
}
