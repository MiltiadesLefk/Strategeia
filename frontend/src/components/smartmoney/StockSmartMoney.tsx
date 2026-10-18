import { useState } from 'react';
import {
  useCongressTrades,
  useFundHolders,
  useOwnershipFilings,
  useSmartMoneyCandidates,
  useSmartMoneyInsiders,
} from '../../api/hooks';
import type { SmartMoneySide } from '../../api/types';
import { Tabs } from '../Tabs';
import { FinraPanel } from '../FinraPanel';
import { EmptyState, LoadingSpinner, formatMoney, isSafeHttpUrl } from '../common';
import { FundHoldersPanel } from './FundHoldersPanel';
import { InsiderPanel } from './InsiderPanel';
import { SmartMoneyTotals } from './SmartMoneyTotals';
import { SmartMoneyScoreCard, StockCongressPanel, StockOwnersPanel } from './StockSmartMoneyPanels';

const DAY_OPTIONS = [30, 90, 180, 365];
const SIDE_OPTIONS: { value: SmartMoneySide; label: string }[] = [
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

interface EventRow {
  when: string;
  source: string;
  who: string;
  action: 'buy' | 'sell' | 'info';
  actionText: string;
  amount: string;
  /** Exact dollars when the source gives them (insiders); null for ranges and fund values. */
  dollars: number | null;
  url: string | null;
}

const FUND_ACTION: Record<string, { action: EventRow['action']; text: string }> = {
  new: { action: 'buy', text: 'new position' },
  added: { action: 'buy', text: 'added' },
  trimmed: { action: 'sell', text: 'trimmed' },
  sold_out: { action: 'sell', text: 'sold out' },
  unchanged: { action: 'info', text: 'unchanged' },
  no_comparison: { action: 'info', text: 'held (one quarter stored)' },
};

/** Every smart-money event of one stock, all sources in one newest-first list, with the Insiders tab's filters.
 *  Reads the same data as the per-source tabs; nothing here is scored. */
export function SmartMoneyEvents({
  symbol, days, side, minValue,
}: { symbol: string; days: number; side: SmartMoneySide; minValue: number }) {
  const insiders = useSmartMoneyInsiders({ days, side, symbol, minValue });
  const congress = useCongressTrades({ days, side, symbol, followedOnly: false });
  const owners = useOwnershipFilings({ days, schedule: 'all', symbol });
  const funds = useFundHolders(symbol);

  if (insiders.isLoading || congress.isLoading || owners.isLoading || funds.isLoading) return <LoadingSpinner label="Collecting every source…" />;

  const rows: EventRow[] = [];
  for (const t of insiders.data?.trades ?? []) {
    if (t.side === 'other') continue;
    rows.push({
      when: t.known_at, source: 'Insider', who: `${t.insider ?? 'unknown'}${t.role_tags.length ? ` (${t.role_tags.join(', ')})` : ''}`,
      action: t.side, actionText: t.side === 'buy' ? 'bought' : 'sold', amount: t.value === null ? '—' : formatMoney(t.value, { compact: true }), dollars: t.value, url: t.filing_url,
    });
  }
  for (const t of congress.data?.trades ?? []) {
    if (t.side === 'other' || (minValue > 0 && (t.amount_low ?? 0) < minValue)) continue;
    rows.push({
      when: t.known_at, source: 'Congress', who: `${t.member}${t.owner !== 'self' ? ` (${t.owner})` : ''}`,
      action: t.side, actionText: `${t.side === 'buy' ? 'bought' : 'sold'}${t.asset_type && t.asset_type !== 'ST' ? ` (${t.asset_type === 'OP' ? 'option' : t.asset_type})` : ''}`,
      amount: t.amount_text || 'range not stated', dollars: null, url: t.filing_url,
    });
  }
  for (const h of funds.data?.holders ?? []) {
    const f = FUND_ACTION[h.status] ?? { action: 'info' as const, text: h.status };
    if (days < 365 && Date.parse(h.filed_at) < Date.now() - days * 86_400_000) continue;
    rows.push({ when: h.filed_at, source: 'Fund (13F)', who: h.manager ?? `CIK ${h.cik}`, action: f.action, actionText: f.text, amount: formatMoney(h.value, { compact: true }), dollars: null, url: h.filing_url });
  }
  for (const o of owners.data?.filings ?? []) {
    rows.push({
      when: o.known_at, source: '5% owner', who: o.filer_name ?? 'unknown', action: 'info',
      actionText: `${o.schedule}${o.is_amendment ? '/A' : ''} filing`, amount: o.percent === null ? '—' : `${o.percent}%`, dollars: null, url: o.filing_url,
    });
  }
  const shown = rows
    .filter((r) => side === 'all' || (side === 'buys' && r.action === 'buy') || (side === 'sells' && r.action === 'sell'))
    .sort((a, b) => Date.parse(b.when) - Date.parse(a.when));

  const buyRows = shown.filter((r) => r.action === 'buy');
  const sellRows = shown.filter((r) => r.action === 'sell');
  const dollars = (rs: EventRow[], sources: string[]) => rs.filter((r) => sources.includes(r.source) && r.dollars !== null).reduce((n, r) => n + (r.dollars ?? 0), 0);
  const congressOf = (rs: EventRow[]) => rs.filter((r) => r.source === 'Congress').length;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <SmartMoneyTotals
        buys={{ count: buyRows.length, value: formatMoney(dollars(buyRows, ['Insider']), { compact: true }) + ' (insider dollars)' }}
        sells={{ count: sellRows.length, value: formatMoney(dollars(sellRows, ['Insider']), { compact: true }) + ' (insider dollars)' }}
        extra={[`${congressOf(buyRows)} Congress buys, ${congressOf(sellRows)} sells (ranges in the table)`, `${shown.filter((r) => r.action === 'info').length} filings with no direction`]}
      />
      {shown.length === 0 ? (
      <EmptyState>No smart-money event for {symbol} in the last {days} days with these filters. If nothing is stored at all, load the sources from the Smart Money page.</EmptyState>
    ) : (
    <div style={{ overflowX: 'auto' }} data-testid="smart-money-events">
      <table>
        <thead>
          <tr><th>Public since</th><th>Source</th><th>Who</th><th>What</th><th style={{ textAlign: 'right' }}>Amount</th><th>Filing</th></tr>
        </thead>
        <tbody>
          {shown.map((r, i) => (
            <tr key={`${r.source}-${r.when}-${r.who}-${i}`}>
              <td className="tabular-nums" style={{ whiteSpace: 'nowrap' }}>{r.when.slice(0, 10)}</td>
              <td>{r.source}</td>
              <td>{r.who}</td>
              <td><span className={`badge ${r.action === 'buy' ? 'badge-green' : r.action === 'sell' ? 'badge-red' : 'badge-neutral'}`}>{r.actionText}</span></td>
              <td className="tabular-nums" style={{ textAlign: 'right' }}>{r.amount}</td>
              <td>{r.url && isSafeHttpUrl(r.url) ? <a href={r.url} target="_blank" rel="noopener noreferrer">Filing</a> : <span className="text-muted">—</span>}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
    )}
    </div>
  );
}

function StockCandidates({ symbol }: { symbol: string }) {
  const candidates = useSmartMoneyCandidates();
  if (candidates.isLoading) return <LoadingSpinner label="Loading candidates…" />;
  const rows = (candidates.data?.candidates ?? []).filter((c) => c.symbol === symbol);
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }} data-testid="stock-candidates">
      <h3 style={{ fontSize: 15 }}>Times the scan flagged {symbol} as a smart-money candidate</h3>
      <SmartMoneyTotals extra={[`${rows.length} flagged`, `${rows.filter((c) => ['new', 'evaluating', 'needs_review'].includes(c.status)).length} open`]} />
      {rows.length === 0 ? (
        <div className="text-muted" style={{ fontSize: 13 }}>
          Never flagged: its score has not reached the minimum of {candidates.data?.min_score ?? 3} in a scan.
        </div>
      ) : (
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead><tr><th>Flagged</th><th style={{ textAlign: 'right' }}>Score</th><th>Status</th><th>Why</th></tr></thead>
            <tbody>
              {rows.map((c) => (
                <tr key={c.id}>
                  <td className="tabular-nums" style={{ whiteSpace: 'nowrap' }}>{c.scored_at.slice(0, 10)}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{c.score.toFixed(1)}</td>
                  <td><span className="badge badge-neutral">{c.status}</span></td>
                  <td style={{ fontSize: 12 }}>{c.components.map((k) => k.reason).slice(0, 2).join(' · ')}{c.reason ? ` — ${c.reason}` : ''}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}

type SubTab = 'total' | 'candidates' | 'insiders' | 'congress' | 'funds' | 'owners';

/** The company page's Smart Money tab: one general Total tab (score, filters, every event) and one tab per source. */
export function StockSmartMoney({ symbol }: { symbol: string }) {
  const [tab, setTab] = useState<SubTab>('total');
  const [side, setSide] = useState<SmartMoneySide>('all');
  const [minValue, setMinValue] = useState(0);
  const [days, setDays] = useState(90);
  const tabs: { value: SubTab; label: string }[] = [
    { value: 'total', label: 'Total' },
    { value: 'candidates', label: 'Candidates' },
    { value: 'insiders', label: 'Insiders' },
    { value: 'congress', label: 'Congress' },
    { value: 'funds', label: 'Funds' },
    { value: 'owners', label: '5% owners' },
  ];
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <Tabs tabs={tabs} value={tab} onChange={(v) => setTab(v as SubTab)} />
      {tab === 'total' && (
        <>
          <SmartMoneyScoreCard symbol={symbol} />
          <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
            <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
              <h3 style={{ fontSize: 15, marginRight: 8 }}>Every source, newest first</h3>
              <select aria-label="Side" style={{ width: 'auto' }} value={side} onChange={(e) => setSide(e.target.value as SmartMoneySide)}>
                {SIDE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              <select aria-label="Minimum value" style={{ width: 'auto' }} value={minValue} onChange={(e) => setMinValue(Number(e.target.value))}>
                {MIN_VALUE_OPTIONS.map((o) => <option key={o.value} value={o.value}>{o.label}</option>)}
              </select>
              <select aria-label="Window" style={{ width: 'auto' }} value={days} onChange={(e) => setDays(Number(e.target.value))}>
                {DAY_OPTIONS.map((n) => <option key={n} value={n}>Last {n} days</option>)}
              </select>
            </div>
            <SmartMoneyEvents symbol={symbol} days={days} side={side} minValue={minValue} />
          </div>
          <FinraPanel symbol={symbol} />
        </>
      )}
      {tab === 'candidates' && <StockCandidates symbol={symbol} />}
      {tab === 'insiders' && <InsiderPanel symbol={symbol} />}
      {tab === 'congress' && <StockCongressPanel symbol={symbol} />}
      {tab === 'funds' && <FundHoldersPanel symbol={symbol} />}
      {tab === 'owners' && <StockOwnersPanel symbol={symbol} />}
    </div>
  );
}
