import { useState } from 'react';
import { SymbolFilterSelect } from '../components/SymbolFilterSelect';
import {
  useRefreshSmartMoneyInsiders,
  useSmartMoneyClusters,
  useSmartMoneyInsiders,
  useSmartMoneyStatus,
} from '../api/hooks';
import type { ApiError } from '../api/client';
import type { SmartMoneyInsiderCluster, SmartMoneySide, SmartMoneyStatus } from '../api/types';
import { StatCard } from '../components/StatCard';
import { TickerLink } from '../components/TickerLink';
import { CongressTab } from '../components/smartmoney/CongressTab';
import { ActivistsTab } from '../components/smartmoney/ActivistsTab';
import { FundsTab } from '../components/smartmoney/FundsTab';
import { InsiderTradesTable, RoleBadges, formatAccepted } from '../components/smartmoney/InsiderTradesTable';
import { EmptyState, ErrorBanner, LoadingSpinner, formatMoney, formatRelativeTime } from '../components/common';

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

type SourceTab = 'insiders' | 'congress' | 'funds' | 'activists';

const PLANNED_TABS: { label: string; why: string }[] = [];

function SourceTabs({ tab, onTab }: { tab: SourceTab; onTab: (tab: SourceTab) => void }) {
  const tabs: { id: SourceTab; label: string }[] = [
    { id: 'insiders', label: 'Insiders' },
    { id: 'congress', label: 'Congress' },
    { id: 'funds', label: 'Funds' },
    { id: 'activists', label: '5% owners' },
  ];
  return (
    <div className="card">
      <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
        {tabs.map((t) => (
          <button
            key={t.id}
            type="button"
            onClick={() => onTab(t.id)}
            aria-current={tab === t.id ? 'page' : undefined}
            style={{
              border: 'none',
              padding: '7px 16px',
              borderRadius: 6,
              fontSize: 13,
              fontWeight: tab === t.id ? 600 : 400,
              cursor: 'pointer',
              background: tab === t.id ? 'var(--canvas)' : 'transparent',
              color: tab === t.id ? 'var(--text)' : 'var(--text-muted)',
            }}
          >
            {t.label}
          </button>
        ))}
        {PLANNED_TABS.map((t) => (
          <button
            key={t.label}
            type="button"
            disabled
            title={t.why}
            style={{ border: 'none', background: 'transparent', padding: '7px 16px', fontSize: 13, color: 'var(--text-muted)', cursor: 'not-allowed' }}
          >
            {t.label} <span style={{ fontSize: 11 }}>(planned)</span>
          </button>
        ))}
      </div>
      {PLANNED_TABS.length > 0 && (
        <div className="text-muted" style={{ fontSize: 12, marginTop: 8 }}>
          {PLANNED_TABS.map((t) => `${t.label}: ${t.why}`).join(' · ')}
        </div>
      )}
    </div>
  );
}

function ScoringNote() {
  return (
    <div className="card" style={{ background: 'var(--canvas)', border: '1px solid var(--border)', fontSize: 13 }}>
      <strong>How this is used.</strong> All four sources count, <strong>buying and selling</strong>, and each is worth
      up to 2 points on a trade plan (buying supports a long and argues against a short; selling does the reverse).{' '}
      <strong>Insiders:</strong> net buying counts, and so does selling, in full when it was the insider&apos;s own choice
      and at a quarter of its value when it was made under a 10b5-1 plan (scheduled months earlier).{' '}
      <strong>Congress:</strong> members who net bought or sold in the last 45 days (4 members is the strong reading).{' '}
      <strong>Funds:</strong> followed funds&apos; 13F changes (3 funds is the strong reading).{' '}
      <strong>5% owners:</strong> a new Schedule 13D or a raised stake is buying; a cut stake, or one that fell under 5%,
      is selling. Every row is dated by the moment SEC accepted the filing, which is the first time anyone could have
      seen it; the trade itself happened earlier.
    </div>
  );
}

function EmptyInsiders({ status }: { status: SmartMoneyStatus }) {
  const refresh = useRefreshSmartMoneyInsiders();
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h3 style={{ fontSize: 16 }}>No insider filings loaded yet</h3>
      <div className="text-muted" style={{ fontSize: 14 }}>
        Nothing is shown because nothing has been stored, not because insiders are quiet. Load SEC Form 4 filings for your
        watchlist with the button, or from a terminal at the repo root:
      </div>
      <code style={{ display: 'block', padding: 10, background: 'var(--canvas)', borderRadius: 6, fontSize: 13, overflowX: 'auto' }}>
        {status.ingest_command}
      </code>
      {status.sec_contact_is_placeholder && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          SEC asks every client to identify itself. This install still uses the placeholder contact; set
          SEC_EDGAR_USER_AGENT to your own contact for reliable access.
        </div>
      )}
      <RefreshControls />
      {refresh.isSuccess && refresh.data.rows_created === 0 && refresh.data.errors.length === 0 && (
        <div className="text-muted" style={{ fontSize: 13 }}>
          The refresh finished without storing anything new.
        </div>
      )}
    </div>
  );
}

function RefreshControls() {
  const refresh = useRefreshSmartMoneyInsiders();
  const data = refresh.data;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
      <button className="btn btn-secondary" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
        {refresh.isPending ? 'Loading from SEC… this can take a minute' : 'Load / refresh from SEC'}
      </button>
      {refresh.error && <ErrorBanner message={(refresh.error as ApiError).message} />}
      {data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          Checked {data.symbols_processed} of {data.symbols_requested} symbols: {data.filings_ingested} new filings,{' '}
          {data.rows_created} rows stored.
          {data.symbols_remaining > 0 && ` ${data.symbols_remaining} more symbols are waiting: click again in a minute.`}
          {data.unknown_symbols.length > 0 && ` SEC has no filer for ${data.unknown_symbols.join(', ')}.`}
          {data.errors.length > 0 && ` ${data.errors.length} problem(s), first: ${data.errors[0]}`}
        </div>
      )}
    </div>
  );
}

function ClusterRow({ c }: { c: SmartMoneyInsiderCluster }) {
  return (
    <div className="split-row" style={{ padding: '10px 0', borderBottom: '1px solid var(--border)', gap: 12 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <TickerLink symbol={c.symbol} />
          <span className="badge badge-green">{c.insider_count} insiders bought</span>
          <RoleBadges tags={c.role_tags} />
          {c.any_10b5_1 && <span className="badge badge-amber">includes a 10b5-1 plan</span>}
        </div>
        <div className="text-muted" style={{ fontSize: 12 }}>
          {c.insiders.join(', ')} · traded {c.start_date} to {c.end_date} · visible to the public from{' '}
          {formatAccepted(c.visible_from)}
        </div>
      </div>
      <div className="tabular-nums" style={{ fontWeight: 700 }}>
        {formatMoney(c.total_value, { compact: true })}
        {c.unpriced_trades > 0 && (
          <div className="text-muted" style={{ fontSize: 11, fontWeight: 400 }}>
            {c.unpriced_trades} trade(s) had no price
          </div>
        )}
      </div>
    </div>
  );
}

function InsidersTab({ status }: { status: SmartMoneyStatus }) {
  const [days, setDays] = useState(90);
  const [side, setSide] = useState<SmartMoneySide>('all');
  const [minValue, setMinValue] = useState(0);
  const [symbol, setSymbol] = useState('');

  const trades = useSmartMoneyInsiders({ days, side, symbol, minValue });
  const clusters = useSmartMoneyClusters(days, symbol);

  const data = trades.data;
  const filtered = side !== 'all' || minValue > 0 || symbol !== '';

  return (
    <>
      <div className="grid stat-grid">
        <StatCard
          label={`Trades in ${days} days`}
          value={data ? String(data.buy_count + data.sell_count) : '—'}
          note={data ? `${data.buy_count} buys, ${data.sell_count} sells (open market)` : undefined}
        />
        <StatCard label="Bought" value={data ? formatMoney(data.buy_value, { compact: true }) : '—'} positive={data && data.buy_value > 0 ? true : null} />
        <StatCard label="Sold" value={data ? formatMoney(data.sell_value, { compact: true }) : '—'} note="plan sales count at a quarter" />
        <StatCard label="Cluster buys" value={clusters.data ? String(clusters.data.clusters.length) : '—'} note="2+ insiders within 14 days" />
      </div>

      <div className="card">
        <div className="split-row" style={{ marginBottom: 8 }}>
          <h3 style={{ fontSize: 16 }}>Cluster buys</h3>
          <span className="text-muted" style={{ fontSize: 12 }}>
            Several different insiders buying close together is a stronger sign than one person buying.
          </span>
        </div>
        {clusters.isLoading && <LoadingSpinner />}
        {clusters.error && <ErrorBanner message={(clusters.error as ApiError).message} onRetry={() => clusters.refetch()} />}
        {clusters.data && clusters.data.clusters.length === 0 && (
          <EmptyState>No cluster buys among the {clusters.data.symbols_checked} symbols with filings in this window.</EmptyState>
        )}
        {clusters.data?.clusters.map((c) => <ClusterRow key={`${c.symbol}-${c.start_date}`} c={c} />)}
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <h3 style={{ fontSize: 16, marginRight: 8 }}>Insider trades</h3>
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
          <SymbolFilterSelect value={symbol} onChange={setSymbol} />
          <div style={{ marginLeft: 'auto' }} className="text-muted">
            <span style={{ fontSize: 12 }}>
              Filings stored through {status.newest_filing ? formatRelativeTime(status.newest_filing) : '—'}
            </span>
          </div>
        </div>

        {trades.isLoading && <LoadingSpinner />}
        {trades.error && <ErrorBanner message={(trades.error as ApiError).message} onRetry={() => trades.refetch()} />}
        {data && data.trades.length === 0 && (
          <EmptyState>{filtered ? 'No trades match these filters.' : 'No open-market insider trades in this window.'}</EmptyState>
        )}
        {data && data.trades.length > 0 && (
          <>
            <InsiderTradesTable trades={data.trades} />
            {data.total > data.shown && (
              <div className="text-muted" style={{ fontSize: 12 }}>
                Showing the newest {data.shown} of {data.total} matching trades: narrow the filters to see the rest.
              </div>
            )}
          </>
        )}
        <RefreshControls />
      </div>
    </>
  );
}

export function SmartMoneyPage() {
  const status = useSmartMoneyStatus();
  const [tab, setTab] = useState<SourceTab>('insiders');

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <div>
          <h1 style={{ fontSize: 22 }}>Smart Money</h1>
          <div className="text-muted" style={{ fontSize: 13, marginTop: 4 }}>
            What people with inside knowledge or deep pockets have done, from public filings.
            {status.data?.has_data &&
              ` ${status.data.trade_rows} rows from ${status.data.symbols} symbols, newest filing ${formatRelativeTime(status.data.newest_filing)}.`}
          </div>
        </div>
      </div>
      <SourceTabs tab={tab} onTab={setTab} />
      {tab === 'congress' && <CongressTab />}
      {tab === 'funds' && <FundsTab />}
      {tab === 'activists' && <ActivistsTab />}
      {tab === 'insiders' && (
        <>
          <ScoringNote />
          {status.isLoading && <LoadingSpinner />}
          {status.error && <ErrorBanner message={(status.error as ApiError).message} onRetry={() => status.refetch()} />}
          {status.data && (status.data.has_data ? <InsidersTab status={status.data} /> : <EmptyInsiders status={status.data} />)}
        </>
      )}
    </div>
  );
}
