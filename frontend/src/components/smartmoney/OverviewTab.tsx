import { useState } from 'react';
import { useSmartMoneyOverview } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import { StatCard } from '../StatCard';
import { SmartMoneyEvents } from './StockSmartMoney';
import { SmartMoneyTotals, rangeLabel } from './SmartMoneyTotals';
import type { SmartMoneySide } from '../../api/types';
import { SymbolFilterSelect } from '../SymbolFilterSelect';
import { TickerLink } from '../TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatMoney } from '../common';

const DAY_OPTIONS = [30, 90, 180, 365];
const SIDE_OPTIONS = [
  { value: 'all', label: 'Buys and sells' },
  { value: 'buys', label: 'Buys only' },
  { value: 'sells', label: 'Sells only' },
];
const MIN_VALUE_OPTIONS = [
  { value: 0, label: 'Any value' },
  { value: 50_000, label: '$50k +' },
  { value: 100_000, label: '$100k +' },
  { value: 500_000, label: '$500k +' },
  { value: 1_000_000, label: '$1M +' },
];
const COLUMNS: { key: 'insider' | 'house' | 'fund' | 'ownership' | 'filing_8k'; label: string }[] = [
  { key: 'insider', label: 'Insiders' }, { key: 'house', label: 'Congress' }, { key: 'fund', label: 'Funds' },
  { key: 'ownership', label: '5% owners' }, { key: 'filing_8k', label: '8-Ks' },
];

/** Every smart-money source added up: totals per source, and the stocks ranked by their combined score. */
export function OverviewTab() {
  const [days, setDays] = useState(90);
  const [side, setSide] = useState('all');
  const [minValue, setMinValue] = useState(0);
  const [symbol, setSymbol] = useState('');
  const overview = useSmartMoneyOverview({ days, side, minValue, symbol });
  const d = overview.data;
  const t = d?.totals;
  return (
    <>
      <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
        <select aria-label="Side" style={{ width: 'auto' }} value={side} onChange={(e) => setSide(e.target.value)}>
          {SIDE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <select aria-label="Minimum value" style={{ width: 'auto' }} value={minValue} onChange={(e) => setMinValue(Number(e.target.value))}>
          {MIN_VALUE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
        </select>
        <select aria-label="Window" style={{ width: 'auto' }} value={days} onChange={(e) => setDays(Number(e.target.value))}>
          {DAY_OPTIONS.map((n) => <option key={n} value={n}>Last {n} days</option>)}
        </select>
        <SymbolFilterSelect value={symbol} onChange={setSymbol} />
        <span className="text-muted" style={{ fontSize: 12 }}>
          The ranking always uses the scan&apos;s own 30-day score; the cards follow the window. The value filter applies to insider
          trades (dollars) and Congress trades (low end of the reported range) only.
        </span>
      </div>
      {overview.isLoading && <LoadingSpinner label="Adding up every source…" />}
      {overview.error && <ErrorBanner message={(overview.error as ApiError).message} onRetry={() => overview.refetch()} />}
      {d && t && (
        <>
          <SmartMoneyTotals
            buys={{
              count: t.insider.buys + t.congress.buys + t.funds.new_positions + t.funds.added,
              value: `${formatMoney(t.insider.buy_value + t.funds.buy_value, { compact: true })} (insiders and fund positions)`,
            }}
            sells={{
              count: t.insider.sells + t.congress.sells + t.funds.trimmed + t.funds.sold_out,
              value: `${formatMoney(t.insider.sell_value + t.funds.sell_value, { compact: true })} (insiders and fund positions)`,
            }}
            clusters={t.clusters.insider + t.clusters.congress}
            extra={[`Congress: buys ${rangeLabel(t.congress.buy_low, t.congress.buy_high)}, sells ${rangeLabel(t.congress.sell_low, t.congress.sell_high)} (reported ranges)`]}
            note="Every source added up for the window and filters above. Congress forms give ranges, never dollars, so they are not added into the dollar totals; 5% owner filings say what is held, not a direction."
          />
          <div className="grid stat-grid">
            <StatCard label="Insider buys" value={String(t.insider.buys)} note={`${formatMoney(t.insider.buy_value, { compact: true })} bought, ${t.insider.sells} sells (${formatMoney(t.insider.sell_value, { compact: true })})`} />
            <StatCard label="Congress stock trades" value={String(t.congress.buys + t.congress.sells)} note={`${t.congress.buys} buys, ${t.congress.sells} sells, ${t.congress.members} members`} />
            <StatCard label="Fund moves (13F)" value={String(t.funds.new_positions + t.funds.added + t.funds.trimmed + t.funds.sold_out)} note={`${t.funds.new_positions} new, ${t.funds.added} added, ${t.funds.trimmed} trimmed, ${t.funds.sold_out} sold out; ${t.funds.followed_with_filings} funds with filings`} />
            <StatCard label="5% owner filings" value={String(t.owners['13D'] + t.owners['13G'])} note={`${t.owners['13D']} 13D, ${t.owners['13G']} 13G`} />
            <StatCard label="Candidates" value={String(t.candidates.all)} note={`${t.candidates.new} new, ${t.candidates.needs_review} need your review`} />
            <StatCard label="Stocks at or above the minimum" value={String(d.passing)} note={`of ${d.scored_stocks} scored; minimum ${d.min_score}`} />
          </div>
          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
            <h3 style={{ fontSize: 16 }}>Stocks by combined smart-money score</h3>
            {d.ranked.length === 0 ? (
              <EmptyState>
                Nothing scores points in the scan&apos;s last 30 days{symbol ? ` for ${symbol}` : ''}, which is all the morning scan counts.
                {t.insider.buys + t.insider.sells + t.congress.buys + t.congress.sells + t.funds.new_positions + t.funds.added + t.funds.trimmed + t.funds.sold_out + t.owners['13D'] + t.owners['13G'] > 0
                  ? ' The cards above also count older filings in your window.'
                  : ' Load insiders, Congress reports and fund filings from their tabs if you expect more.'}
              </EmptyState>
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
          {symbol && (
            <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              <h3 style={{ fontSize: 16 }}>{symbol}: every source, newest first</h3>
              <SmartMoneyEvents symbol={symbol} days={days} side={side as SmartMoneySide} minValue={minValue} />
            </div>
          )}
        </>
      )}
    </>
  );
}
