import { useFundHolders } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import { ErrorBanner, LoadingSpinner, formatMoney } from '../common';

const STATUS_TEXT: Record<string, string> = {
  new: 'new position',
  added: 'added',
  trimmed: 'trimmed',
  sold_out: 'sold out',
  unchanged: 'unchanged',
  no_comparison: 'one quarter stored',
};

/** Which of the followed funds hold this symbol, from their latest 13F: long only, quarter-end, weeks late.
 *  Used on the Analysis page's research tab; the Smart Money page has the full per-fund view. Read-only. */
export function FundHoldersPanel({ symbol }: { symbol: string }) {
  const holders = useFundHolders(symbol);
  if (holders.isLoading) return <LoadingSpinner label="Loading fund holdings…" />;
  if (holders.error) return <ErrorBanner message={(holders.error as ApiError).message} onRetry={() => holders.refetch()} />;
  const d = holders.data;
  if (!d) return null;

  if (d.funds_stored === 0) {
    return (
      <div className="card" data-testid="fund-holders">
        <h3 style={{ fontSize: 15, marginBottom: 6 }}>Held by followed funds</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          No 13F filings are stored yet, so this cannot say whether funds hold {symbol}. Load them from the Funds tab of the
          Smart Money page.
        </div>
      </div>
    );
  }
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }} data-testid="fund-holders">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: 15 }}>Held by followed funds</h3>
        <span className="text-muted" style={{ fontSize: 12 }}>
          13F: long only, as of the quarter end, filed up to 45 days later
        </span>
      </div>
      {d.holders.length === 0 ? (
        <div className="text-muted" style={{ fontSize: 13 }}>
          None of the {d.funds_stored} followed funds with stored filings reported {symbol} at their latest quarter end.
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>Fund</th>
                <th>Quarter ended</th>
                <th style={{ textAlign: 'right' }}>Shares</th>
                <th style={{ textAlign: 'right' }}>Value</th>
                <th style={{ textAlign: 'right' }}>Weight</th>
                <th>Versus last quarter</th>
              </tr>
            </thead>
            <tbody>
              {d.holders.map((h) => (
                <tr key={h.cik}>
                  <td style={{ fontWeight: 600 }}>{h.manager ?? `CIK ${h.cik}`}</td>
                  <td className="tabular-nums">{h.period}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{h.shares.toLocaleString()}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatMoney(h.value, { compact: true })}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{h.weight_pct.toFixed(2)}%</td>
                  <td>
                    {STATUS_TEXT[h.status] ?? h.status}
                    {h.change_pct !== null && h.status !== 'sold_out' && ` (${h.change_pct >= 0 ? '+' : ''}${h.change_pct.toFixed(0)}%)`}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
