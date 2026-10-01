import { useMarketSession } from '../api/hooks';
import { describeFreshness, parseServerTime } from '../lib/freshness';
import { useNow } from '../lib/useNow';

// Idea from OpenStock's DataFreshness component (AGPL-3.0; no code copied):
// say how old the numbers on screen are instead of implying they are live.

interface FreshnessProps {
  /** react-query's dataUpdatedAt (epoch ms; 0 while there is no data). */
  updatedAt: number | undefined;
  /** The server's own timestamp for this data, when the endpoint supplies one. */
  serverAsOf?: string | null;
  isFetching?: boolean;
  /** 24/7 instruments (-USD crypto): never say the US market is closed. */
  alwaysOpen?: boolean;
}

function FreshnessText({
  updatedAt,
  serverAsOf,
  isFetching = false,
  alwaysOpen = false,
  compact,
  marketState,
  holidayName,
}: FreshnessProps & { compact: boolean; marketState?: string; holidayName?: string | null }) {
  // Re-render every 30 s so the age keeps counting between fetches.
  const now = useNow();
  const server = parseServerTime(serverAsOf);
  const info = describeFreshness({
    asOf: server ?? updatedAt,
    now: now.getTime(),
    source: server !== null ? 'server' : 'client',
    marketState,
    holidayName,
    alwaysOpen,
  });
  if (!info) return null;

  const origin =
    info.source === 'server'
      ? 'Timestamp supplied by the server for this data.'
      : 'When this browser last received this data. The server does not stamp it, so this is not the exchange time of the last price.';
  const staleNote = info.stale ? ' Older than the 15-minute data cache: a refresh would normally have replaced it.' : '';

  return (
    <span className={`freshness${info.stale ? ' freshness-stale' : ''}`} data-freshness={info.stale ? 'stale' : 'fresh'} title={`${origin}${staleNote}`}>
      {compact ? (
        <span>{info.age}</span>
      ) : (
        <span>
          as of {info.clock} · {info.age}
        </span>
      )}
      {isFetching && <span> · refreshing…</span>}
      {!compact && info.marketNote && <span className="freshness-market"> · {info.marketNote}</span>}
    </span>
  );
}

function FreshnessWithMarket(props: FreshnessProps) {
  const { data: session } = useMarketSession();
  return <FreshnessText {...props} compact={false} marketState={session?.state} holidayName={session?.holiday_name} />;
}

/**
 * "as of 14:32 · 3 min ago", plus "US market closed" when the market is not
 * open (from the backend's session calendar, the same one the paper engine
 * uses).
 *
 * The time is the server's own data timestamp when the endpoint supplies one
 * (`serverAsOf`), and otherwise the moment this browser last received the data
 * (react-query's `dataUpdatedAt`): the tooltip says which. It is never a
 * guess. It turns amber once the data is older than the provider cache window
 * (15 minutes), the point where the age can no longer be explained by caching.
 *
 * `compact` shows only the age, without the clock or the market note, for
 * tight spots like a card; it does not subscribe to the market session.
 */
export function DataFreshness({ compact = false, ...props }: FreshnessProps & { compact?: boolean }) {
  if (compact) return <FreshnessText {...props} compact />;
  return <FreshnessWithMarket {...props} />;
}
