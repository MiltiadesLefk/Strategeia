import { useSmartMoneyInsiders, useSmartMoneySummary } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import { ErrorBanner, LoadingSpinner, formatMoney } from '../common';
import { InsiderTradesTable } from './InsiderTradesTable';

const PANEL_DAYS = 90;
const PANEL_MAX_ROWS = 8;

/** One symbol's insider activity: the numbers the scorer reads, what it would do with them
 *  (information only), and the most recent open-market trades. Used on the Analysis page's
 *  research tabs; the Smart Money page has its own full table. Read-only. */
export function InsiderPanel({ symbol }: { symbol: string }) {
  const summary = useSmartMoneySummary(symbol);
  const trades = useSmartMoneyInsiders({ days: PANEL_DAYS, side: 'all', symbol, minValue: 0 });

  if (summary.isLoading || trades.isLoading) return <LoadingSpinner label="Loading insider activity…" />;
  if (summary.error) return <ErrorBanner message={(summary.error as ApiError).message} onRetry={() => summary.refetch()} />;
  const s = summary.data;
  if (!s) return null;

  if (!s.data_loaded) {
    return (
      <div className="card">
        <h3 style={{ fontSize: 15, marginBottom: 6 }}>Insider activity</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          No insider filings are stored for {symbol} yet. That is not the same as insiders being quiet: nothing has
          been loaded. Load them from the Smart Money page.
        </div>
      </div>
    );
  }

  const shown = (trades.data?.trades ?? []).slice(0, PANEL_MAX_ROWS);
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: 15 }}>Insider activity · last {s.window_days} days</h3>
        <span className="text-muted" style={{ fontSize: 12 }}>
          SEC Form 4, dated by SEC acceptance time
        </span>
      </div>
      <div className="tabular-nums" style={{ display: 'flex', gap: 24, flexWrap: 'wrap', fontSize: 14 }}>
        <span>
          <span className="text-green" style={{ fontWeight: 700 }}>
            {s.buy_count} buys
          </span>{' '}
          {formatMoney(s.buy_value, { compact: true })}
        </span>
        <span>
          <span className="text-red" style={{ fontWeight: 700 }}>
            {s.sell_count} sells
          </span>{' '}
          {formatMoney(s.sell_value, { compact: true })}
        </span>
        <span>
          {s.cluster_count} cluster{s.cluster_count === 1 ? '' : 's'}
        </span>
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        {s.would_score_long > 0
          ? `For information: today's rules would add ${s.would_score_long} point to a long (${s.score_reasons[0]}).`
          : 'For information: today\'s rules would add nothing for insiders (they need meaningful net open-market buying).'}{' '}
        Only open-market buys can ever score; sells are context only.
      </div>
      {shown.length > 0 && <InsiderTradesTable trades={shown} showSymbol={false} />}
    </div>
  );
}
