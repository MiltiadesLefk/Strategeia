import { useCongressTrades, useOwnershipFilings, useSmartMoneyScore } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import { ErrorBanner, LoadingSpinner } from '../common';
import { CongressTradesTable } from './CongressTab';
import { FilingRow } from './ActivistsTab';

const SOURCE_LABEL: Record<string, string> = {
  insider: 'Insiders', house: 'Congress', fund: 'Funds (13F)', ownership: '5% owners', filing_8k: '8-K filings',
};

/** Every smart-money source for this stock in one score: the number the morning scan compares with its minimum. */
export function SmartMoneyScoreCard({ symbol }: { symbol: string }) {
  const score = useSmartMoneyScore(symbol);
  if (score.isLoading) return <LoadingSpinner label="Adding up smart money…" />;
  if (score.error) return <ErrorBanner message={(score.error as ApiError).message} onRetry={() => score.refetch()} />;
  const d = score.data;
  if (!d) return null;
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }} data-testid="smart-money-score">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: 15 }}>
          Smart money, all sources: {d.total.toFixed(1)} points{' '}
          <span className={`badge ${d.total >= d.min_score ? 'badge-green' : 'badge-neutral'}`}>
            {d.total >= d.min_score ? 'would be flagged' : `below the minimum of ${d.min_score}`}
          </span>
        </h3>
        <span className="text-muted" style={{ fontSize: 12 }}>Last 30 days, as the morning scan scores it. The tables below show the detail.</span>
      </div>
      {d.components.length === 0 ? (
        <div className="text-muted" style={{ fontSize: 13 }}>No insider, Congress, fund, 5% owner or 8-K evidence in the last 30 days.</div>
      ) : (
        <ul style={{ margin: '0 0 0 18px', display: 'flex', flexDirection: 'column', gap: 3, fontSize: 13 }}>
          {d.components.map((c, i) => (
            <li key={i}>
              <strong>{SOURCE_LABEL[c.source] ?? c.source}</strong> {c.points >= 0 ? '+' : ''}{c.points.toFixed(1)}: {c.reason}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

/** House members' reported trades in this stock (last 365 days of filings). */
export function StockCongressPanel({ symbol }: { symbol: string }) {
  const trades = useCongressTrades({ days: 365, side: 'all', symbol, followedOnly: false });
  if (trades.isLoading) return <LoadingSpinner label="Loading Congress trades…" />;
  if (trades.error) return <ErrorBanner message={(trades.error as ApiError).message} onRetry={() => trades.refetch()} />;
  const d = trades.data;
  if (!d) return null;
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }} data-testid="stock-congress">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: 15 }}>Congress trades · last 365 days of filings</h3>
        <span className="text-muted" style={{ fontSize: 12 }}>House only; amounts are ranges; reported up to 45 days late</span>
      </div>
      {d.trades.length === 0 ? (
        <div className="text-muted" style={{ fontSize: 13 }}>
          No House member reported a trade in {symbol}. If nothing is stored at all, load the reports from the Congress tab of the Smart Money page.
        </div>
      ) : (
        <CongressTradesTable trades={d.trades} />
      )}
    </div>
  );
}

/** 13D / 13G filings: anyone who holds more than 5% of this company. */
export function StockOwnersPanel({ symbol }: { symbol: string }) {
  const filings = useOwnershipFilings({ days: 365, schedule: 'all', symbol });
  if (filings.isLoading) return <LoadingSpinner label="Loading 5% owners…" />;
  if (filings.error) return <ErrorBanner message={(filings.error as ApiError).message} onRetry={() => filings.refetch()} />;
  const rows = filings.data?.filings ?? [];
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }} data-testid="stock-owners">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: 15 }}>5% owners (13D / 13G) · last 365 days</h3>
        <span className="text-muted" style={{ fontSize: 12 }}>A filing says what the holder owns now, not whether it is buying</span>
      </div>
      {rows.length === 0 ? (
        <div className="text-muted" style={{ fontSize: 13 }}>No 13D or 13G filing stored for {symbol}.</div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr><th>Filed</th><th>Stock</th><th>Holder</th><th>Form</th><th style={{ textAlign: 'right' }}>Stake</th><th>Purpose</th><th>Filing</th></tr>
            </thead>
            <tbody>{rows.map((f) => <FilingRow key={f.filing_url ?? `${f.known_at}-${f.filer_name}`} f={f} />)}</tbody>
          </table>
        </div>
      )}
    </div>
  );
}
