import { useState } from 'react';
import {
  useCongressClusters,
  useCongressStatus,
  useCongressTrades,
  useRefreshCongress,
} from '../../api/hooks';
import type { ApiError } from '../../api/client';
import type { CongressCluster, CongressStatus, CongressTrade } from '../../api/types';
import { StatCard } from '../StatCard';
import { TickerLink } from '../TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatMoney, formatRelativeTime, isSafeHttpUrl } from '../common';

const DAY_OPTIONS = [30, 90, 180, 365];
const SIDE_OPTIONS = [
  { value: 'all', label: 'Buys and sells' },
  { value: 'buys', label: 'Buys only' },
  { value: 'sells', label: 'Sells only' },
];
const SYMBOL_PATTERN = /^[A-Z0-9.-]{0,10}$/;

/** "$1,001 - $15,000" is what the form says; keep it. Never a single number. */
function rangeText(t: { amount_text: string; amount_low: number | null; amount_high: number | null }): string {
  if (t.amount_text) return t.amount_text;
  if (t.amount_low === null) return 'amount not stated';
  return t.amount_high === null ? `Over ${formatMoney(t.amount_low)}` : `${formatMoney(t.amount_low)} - ${formatMoney(t.amount_high)}`;
}

function delayText(days: number | null): string {
  if (days === null) return '';
  if (days <= 0) return 'reported the same day';
  return `reported ${days} day${days === 1 ? '' : 's'} later`;
}

function CongressNotes() {
  return (
    <div className="card" style={{ background: 'var(--canvas)', border: '1px solid var(--border)', fontSize: 13 }} data-testid="congress-notes">
      <strong>Read these before using the numbers.</strong>
      <ul style={{ margin: '6px 0 0 18px', display: 'flex', flexDirection: 'column', gap: 3 }}>
        <li>
          Amounts are <strong>ranges</strong> the form forces members to pick from, never exact dollars. The midpoint shown is only
          the middle of the range.
        </li>
        <li>
          Members have <strong>up to 45 days</strong> to file, so a trade is usually weeks old when it appears here. Rows are dated
          by the day the report was filed, which is when anyone could first see it.
        </li>
        <li>
          Trades by a <strong>spouse or dependent child</strong> are included and labelled; they are not necessarily the
          member&apos;s own decision.
        </li>
        <li>
          <strong>House only.</strong> Senate: not available (its disclosure site refuses automated access). Scanned
          paper-style reports cannot be read and are counted, not guessed.
        </li>
        <li>Only stock purchases are ever counted, and only as a silent signal that adds no points to a trade plan.</li>
      </ul>
    </div>
  );
}

function RefreshControls() {
  const refresh = useRefreshCongress();
  const data = refresh.data;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
      <button className="btn btn-secondary" onClick={() => refresh.mutate()} disabled={refresh.isPending}>
        {refresh.isPending ? 'Loading from the House Clerk… this can take a minute' : 'Load / refresh from the House Clerk'}
      </button>
      {refresh.error && <ErrorBanner message={(refresh.error as ApiError).message} />}
      {data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          {data.filings_ingested} new reports read ({data.rows_created} rows stored).
          {data.filings_remaining > 0 && ` ${data.filings_remaining} more are waiting: click again in a minute.`}
          {data.unreadable > 0 && ` ${data.unreadable} could not be read (scanned).`}
          {data.errors.length > 0 && ` ${data.errors.length} problem(s), first: ${data.errors[0]}`}
        </div>
      )}
    </div>
  );
}

function EmptyCongress({ status }: { status: CongressStatus }) {
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h3 style={{ fontSize: 16 }}>No Congress trade reports loaded yet</h3>
      <div className="text-muted" style={{ fontSize: 14 }}>
        Nothing is shown because nothing has been stored, not because Congress is quiet. Load the newest House reports with the
        button, or fill a whole year from a terminal at the repo root:
      </div>
      <code style={{ display: 'block', padding: 10, background: 'var(--canvas)', borderRadius: 6, fontSize: 13, overflowX: 'auto' }}>
        {status.ingest_command}
      </code>
      <RefreshControls />
    </div>
  );
}

function ClusterRow({ c }: { c: CongressCluster }) {
  const total = c.total_high === null ? `${formatMoney(c.total_low, { compact: true })} or more` : `${formatMoney(c.total_low, { compact: true })} to ${formatMoney(c.total_high, { compact: true })}`;
  return (
    <div className="split-row" style={{ padding: '10px 0', borderBottom: '1px solid var(--border)', gap: 12 }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <TickerLink symbol={c.symbol} />
          <span className="badge badge-green">{c.member_count} members bought</span>
        </div>
        <div className="text-muted" style={{ fontSize: 12 }}>
          {c.members.join(', ')} · traded {c.start_date} to {c.end_date} · public from {c.visible_from.slice(0, 10)}
        </div>
      </div>
      <div className="tabular-nums" style={{ fontWeight: 700, textAlign: 'right' }}>
        {total}
        <div className="text-muted" style={{ fontSize: 11, fontWeight: 400 }}>
          sum of ranges
        </div>
      </div>
    </div>
  );
}

function CongressTradesTable({ trades }: { trades: CongressTrade[] }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table>
        <thead>
          <tr>
            <th>Member</th>
            <th>Asset</th>
            <th>Type</th>
            <th style={{ textAlign: 'right' }}>Amount (range)</th>
            <th title="The date of the trade itself">Traded</th>
            <th title="The day the report was filed: the first moment anyone could see the trade">Filed</th>
            <th>Report</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={`${t.filing_url}-${t.asset}-${t.trade_date}-${i}`}>
              <td>
                <div style={{ fontWeight: 600 }}>{t.member}</div>
                <div className="text-muted" style={{ fontSize: 11 }}>
                  {t.state_district ?? ''}
                  {t.owner !== 'self' && <span className="badge badge-amber" style={{ marginLeft: 6 }}>{t.owner}</span>}
                </div>
              </td>
              <td>
                {t.symbol ? <TickerLink symbol={t.symbol} /> : <span className="text-muted">no ticker</span>}
                <div className="text-muted" style={{ fontSize: 11, maxWidth: 260 }}>
                  {t.asset}
                </div>
              </td>
              <td>
                <span className={`badge ${t.side === 'buy' ? 'badge-green' : t.side === 'sell' ? 'badge-red' : 'badge-neutral'}`}>
                  {t.type || t.side}
                </span>
                {t.asset_type && t.asset_type !== 'ST' && (
                  <div className="text-muted" style={{ fontSize: 11 }}>
                    {t.asset_type === 'OP' ? 'option: not counted' : `type ${t.asset_type}: not counted`}
                  </div>
                )}
              </td>
              <td className="tabular-nums" style={{ textAlign: 'right' }}>
                {rangeText(t)}
                {t.range_midpoint !== null && (
                  <div className="text-muted" style={{ fontSize: 11 }}>
                    range midpoint {formatMoney(t.range_midpoint, { compact: true })}
                  </div>
                )}
              </td>
              <td className="tabular-nums" style={{ whiteSpace: 'nowrap' }}>
                {t.trade_date ?? '—'}
              </td>
              <td className="tabular-nums" style={{ whiteSpace: 'nowrap' }}>
                {t.filed_date ?? '—'}
                <div className="text-muted" style={{ fontSize: 11 }}>
                  {delayText(t.filing_delay_days)}
                </div>
              </td>
              <td>
                {t.filing_url && isSafeHttpUrl(t.filing_url) ? (
                  <a href={t.filing_url} target="_blank" rel="noopener noreferrer">
                    PDF
                  </a>
                ) : (
                  <span className="text-muted">—</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function CongressBody({ status }: { status: CongressStatus }) {
  const [days, setDays] = useState(90);
  const [side, setSide] = useState('all');
  const [symbol, setSymbol] = useState('');
  const [followedOnly, setFollowedOnly] = useState(false);
  const listMode = status.follow_mode === 'list';
  const applyFollow = listMode && followedOnly;

  const trades = useCongressTrades({ days, side, symbol, followedOnly: applyFollow });
  const clusters = useCongressClusters(days, symbol, applyFollow);
  const data = trades.data;
  const filtered = side !== 'all' || symbol !== '' || applyFollow;

  return (
    <>
      <div className="grid stat-grid">
        <StatCard
          label={`Trades in ${days} days`}
          value={data ? String(data.total) : '—'}
          note={data ? `${data.buy_count} buys, ${data.sell_count} sells, ${data.members} members` : undefined}
        />
        <StatCard label="Stock purchases" value={data ? String(data.stock_buy_count) : '—'} note="options and funds not counted" />
        <StatCard label="Cluster buys" value={clusters.data ? String(clusters.data.clusters.length) : '—'} note="2+ members within 30 days" />
        <StatCard
          label="Unreadable reports"
          value={String(status.unreadable_filings)}
          note={status.partial_filings > 0 ? `${status.partial_filings} partly read` : 'scanned images are never guessed'}
        />
      </div>

      <div className="card">
        <div className="split-row" style={{ marginBottom: 8 }}>
          <h3 style={{ fontSize: 16 }}>Cluster buys</h3>
          <span className="text-muted" style={{ fontSize: 12 }}>
            Several different members buying the same stock close together is a stronger sign than one.
          </span>
        </div>
        {clusters.isLoading && <LoadingSpinner />}
        {clusters.error && <ErrorBanner message={(clusters.error as ApiError).message} onRetry={() => clusters.refetch()} />}
        {clusters.data && clusters.data.clusters.length === 0 && <EmptyState>No cluster buys in reports filed in this window.</EmptyState>}
        {clusters.data?.clusters.map((c) => <ClusterRow key={`${c.symbol}-${c.start_date}`} c={c} />)}
      </div>

      <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <div style={{ display: 'flex', gap: 10, flexWrap: 'wrap', alignItems: 'center' }}>
          <h3 style={{ fontSize: 16, marginRight: 8 }}>Congress trades</h3>
          <select aria-label="Side" style={{ width: 'auto' }} value={side} onChange={(e) => setSide(e.target.value)}>
            {SIDE_OPTIONS.map((o) => (
              <option key={o.value} value={o.value}>
                {o.label}
              </option>
            ))}
          </select>
          <select aria-label="Days" style={{ width: 'auto' }} value={days} onChange={(e) => setDays(Number(e.target.value))}>
            {DAY_OPTIONS.map((d) => (
              <option key={d} value={d}>
                Reports filed in the last {d} days
              </option>
            ))}
          </select>
          <input
            type="text"
            aria-label="Symbol"
            placeholder="Symbol"
            value={symbol}
            onChange={(e) => {
              const next = e.target.value.toUpperCase();
              if (SYMBOL_PATTERN.test(next)) setSymbol(next);
            }}
            style={{ width: 110 }}
          />
          {listMode && (
            <label style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 13 }}>
              <input type="checkbox" checked={followedOnly} onChange={(e) => setFollowedOnly(e.target.checked)} style={{ width: 'auto' }} />
              Only members I follow ({status.followed_members.length})
            </label>
          )}
          <div style={{ marginLeft: 'auto' }} className="text-muted">
            <span style={{ fontSize: 12 }}>Reports stored through {status.newest_filing ? formatRelativeTime(status.newest_filing) : '—'}</span>
          </div>
        </div>

        {trades.isLoading && <LoadingSpinner />}
        {trades.error && <ErrorBanner message={(trades.error as ApiError).message} onRetry={() => trades.refetch()} />}
        {data && data.trades.length === 0 && (
          <EmptyState>{filtered ? 'No trades match these filters.' : 'No House trade reports filed in this window.'}</EmptyState>
        )}
        {data && data.trades.length > 0 && (
          <>
            <CongressTradesTable trades={data.trades} />
            {data.total > data.shown && (
              <div className="text-muted" style={{ fontSize: 12 }}>
                Showing the newest {data.shown} of {data.total} matching trades: narrow the filters to see the rest.
              </div>
            )}
          </>
        )}
        <RefreshControls />
      </div>

      {status.problem_filings.length > 0 && (
        <div className="card" data-testid="congress-unreadable">
          <h3 style={{ fontSize: 16, marginBottom: 6 }}>Reports that could not be read in full</h3>
          <div className="text-muted" style={{ fontSize: 12, marginBottom: 8 }}>
            Their trades are missing above. Open the PDF to read them by hand.
          </div>
          {status.problem_filings.map((p) => (
            <div key={p.doc_id} className="split-row" style={{ padding: '6px 0', fontSize: 13 }}>
              <span>
                {p.member} · filed {p.filed_date ?? '—'} · {p.status}
                {p.reason ? ` (${p.reason})` : ''}
              </span>
              {p.filing_url && isSafeHttpUrl(p.filing_url) && (
                <a href={p.filing_url} target="_blank" rel="noopener noreferrer">
                  PDF
                </a>
              )}
            </div>
          ))}
        </div>
      )}
    </>
  );
}

/** The Congress tab of the Smart Money page: House trade reports, dated by filing day. */
export function CongressTab() {
  const status = useCongressStatus();
  return (
    <>
      <CongressNotes />
      {status.isLoading && <LoadingSpinner />}
      {status.error && <ErrorBanner message={(status.error as ApiError).message} onRetry={() => status.refetch()} />}
      {status.data && (status.data.has_data ? <CongressBody status={status.data} /> : <EmptyCongress status={status.data} />)}
    </>
  );
}
