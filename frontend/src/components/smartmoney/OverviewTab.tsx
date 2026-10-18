import { useState } from 'react';
import { useSmartMoneyOverview } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import { StatCard } from '../StatCard';
import { TickerLink } from '../TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatMoney } from '../common';

const DAY_OPTIONS = [30, 90, 180, 365];
const COLUMNS: { key: 'insider' | 'house' | 'fund' | 'ownership' | 'filing_8k'; label: string }[] = [
  { key: 'insider', label: 'Insiders' }, { key: 'house', label: 'Congress' }, { key: 'fund', label: 'Funds' },
  { key: 'ownership', label: '5% owners' }, { key: 'filing_8k', label: '8-Ks' },
];

/** Every smart-money source added up: totals per source, and the stocks ranked by their combined score. */
export function OverviewTab() {
  const [days, setDays] = useState(30);
  const overview = useSmartMoneyOverview(days);
  const d = overview.data;
  const t = d?.totals;
  return (
    <>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <select aria-label="Window" style={{ width: 'auto' }} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {DAY_OPTIONS.map((n) => <option key={n} value={n}>Totals for the last {n} days</option>)}
        </select>
        <span className="text-muted" style={{ fontSize: 12 }}>
          The ranking always uses the scan&apos;s own 30-day score; the totals follow the window.
        </span>
      </div>
      {overview.isLoading && <LoadingSpinner label="Adding up every source…" />}
      {overview.error && <ErrorBanner message={(overview.error as ApiError).message} onRetry={() => overview.refetch()} />}
      {d && t && (
        <>
          <div className="grid stat-grid">
            <StatCard label="Insider buys" value={String(t.insider.buys)} note={`${formatMoney(t.insider.buy_value, { compact: true })} bought, ${t.insider.sells} sells (${formatMoney(t.insider.sell_value, { compact: true })})`} />
            <StatCard label="Congress stock trades" value={String(t.congress.buys + t.congress.sells)} note={`${t.congress.buys} buys, ${t.congress.sells} sells, ${t.congress.members} members`} />
            <StatCard label="Fund moves (13F)" value={String(t.funds.new_positions + t.funds.added)} note={`${t.funds.new_positions} new, ${t.funds.added} added, ${t.funds.followed_with_filings} funds with filings`} />
            <StatCard label="5% owner filings" value={String(t.owners['13D'] + t.owners['13G'])} note={`${t.owners['13D']} 13D, ${t.owners['13G']} 13G`} />
            <StatCard label="Candidates" value={String(t.candidates.all)} note={`${t.candidates.new} new, ${t.candidates.needs_review} need your review`} />
            <StatCard label="Stocks at or above the minimum" value={String(d.passing)} note={`of ${d.scored_stocks} scored; minimum ${d.min_score}`} />
          </div>
          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <h3 style={{ fontSize: 16 }}>Stocks by combined smart-money score</h3>
            {d.ranked.length === 0 ? (
              <EmptyState>No smart-money evidence is stored for this window. Load insiders, Congress reports and fund filings from their tabs.</EmptyState>
            ) : (
              <div style={{ overflowX: 'auto' }}>
                <table>
                  <thead>
                    <tr>
                      <th>Stock</th>
                      <th style={{ textAlign: 'right' }}>Total</th>
                      {COLUMNS.map((c) => <th key={c.key} style={{ textAlign: 'right' }}>{c.label}</th>)}
                      <th>Why</th>
                    </tr>
                  </thead>
                  <tbody>
                    {d.ranked.map((r) => (
                      <tr key={r.symbol}>
                        <td><TickerLink symbol={r.symbol} /></td>
                        <td className="tabular-nums" style={{ textAlign: 'right', fontWeight: 700 }}>
                          {r.total.toFixed(1)} {r.total >= d.min_score && <span className="badge badge-green">flagged</span>}
                        </td>
                        {COLUMNS.map((c) => (
                          <td key={c.key} className="tabular-nums" style={{ textAlign: 'right' }}>{r.parts[c.key] ? r.parts[c.key].toFixed(1) : '·'}</td>
                        ))}
                        <td className="text-muted" style={{ fontSize: 12, maxWidth: 420 }}>{r.reasons.slice(0, 2).join(' · ')}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </>
      )}
    </>
  );
}
