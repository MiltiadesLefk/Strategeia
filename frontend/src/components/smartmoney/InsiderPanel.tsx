import { useState } from 'react';
import { useSmartMoneyInsiders, useSmartMoneySummary } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import { ErrorBanner, LoadingSpinner, formatMoney } from '../common';
import type { SmartMoneySide } from '../../api/types';
import { InsiderTradesTable } from './InsiderTradesTable';

const PANEL_MAX_ROWS = 25;
const DAY_OPTIONS = [30, 90, 180, 365];
const MIN_VALUE_OPTIONS = [
  { value: 0, label: 'Any value' },
  { value: 50_000, label: '$50k +' },
  { value: 100_000, label: '$100k +' },
  { value: 500_000, label: '$500k +' },
  { value: 1_000_000, label: '$1M +' },
];
const SIDE_OPTIONS: { value: SmartMoneySide; label: string }[] = [
  { value: 'all', label: 'Buys and sells' },
  { value: 'buys', label: 'Buys only' },
  { value: 'sells', label: 'Sells only' },
];

/** One symbol's insider activity: the numbers the scorer reads, what it would do with them
 *  (information only), and the most recent open-market trades. Used on the Analysis page's
 *  research tabs; the Smart Money page has its own full table. Read-only. */
export function InsiderPanel({ symbol }: { symbol: string }) {
  const [side, setSide] = useState<SmartMoneySide>('all');
  const [minValue, setMinValue] = useState(0);
  const [days, setDays] = useState(90);
  const summary = useSmartMoneySummary(symbol, days);
  const trades = useSmartMoneyInsiders({ days, side, symbol, minValue });

  if (summary.isLoading) return <LoadingSpinner label="Loading insider activity…" />;
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
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        <select aria-label="Side" style={{ width: 'auto' }} value={side} onChange={(e) => setSide(e.target.value as SmartMoneySide)}>
          {SIDE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <select aria-label="Minimum value" style={{ width: 'auto' }} value={minValue} onChange={(e) => setMinValue(Number(e.target.value))}>
          {MIN_VALUE_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
        <select aria-label="Days" style={{ width: 'auto' }} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {DAY_OPTIONS.map((d) => (
            <option key={d} value={d}>
              Last {d} days
            </option>
          ))}
        </select>
      </div>
      {trades.isLoading && <LoadingSpinner label="Loading trades…" />}
      {shown.length > 0 && <InsiderTradesTable trades={shown} showSymbol={false} />}
      {!trades.isLoading && shown.length === 0 && (
        <div className="text-muted" style={{ fontSize: 13 }}>
          No trades match these filters.
        </div>
      )}
    </div>
  );
}
