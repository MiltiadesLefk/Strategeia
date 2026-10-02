import { useEffect, useState } from 'react';
import { useFundChanges, useFundsOverview, useRefreshFunds } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import type { FundChange, FundChangeStatus, FundSummary, FundsOverview } from '../../api/types';
import { StatCard } from '../StatCard';
import { TickerLink } from '../TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatMoney, isSafeHttpUrl } from '../common';

const STATUS_FILTERS: { value: string; label: string }[] = [
  { value: 'all', label: 'All changes' },
  { value: 'new', label: 'New positions' },
  { value: 'added', label: 'Added to' },
  { value: 'trimmed', label: 'Trimmed' },
  { value: 'sold_out', label: 'Sold out' },
  { value: 'unchanged', label: 'Unchanged' },
];

const STATUS_BADGE: Record<FundChangeStatus, { cls: string; label: string }> = {
  new: { cls: 'badge-green', label: 'New' },
  added: { cls: 'badge-green', label: 'Added' },
  trimmed: { cls: 'badge-amber', label: 'Trimmed' },
  sold_out: { cls: 'badge-red', label: 'Sold out' },
  unchanged: { cls: 'badge-neutral', label: 'Unchanged' },
};

/** What a 13F can and cannot say. Shown above every fund view so the numbers are never read as trades. */
export function FundNotes() {
  return (
    <div className="card" style={{ background: 'var(--canvas)', border: '1px solid var(--border)', fontSize: 13 }} data-testid="fund-notes">
      <strong>Read these before using the numbers.</strong>
      <ul style={{ margin: '6px 0 0 18px', display: 'flex', flexDirection: 'column', gap: 3 }}>
        <li>
          A 13F lists <strong>long positions only</strong>. Short positions and most hedges are invisible, so a fund that
          looks bullish may be hedged.
        </li>
        <li>
          It is a <strong>snapshot of the last day of a quarter</strong>, filed up to <strong>45 days later</strong>. What you
          see here was already weeks old when it appeared.
        </li>
        <li>
          There are <strong>no trade dates</strong>. &quot;Added&quot; means more shares at this quarter end than at the last
          one, not a purchase on any particular day. A stock split looks like a large add.
        </li>
        <li>
          Holdings are matched to tickers by exact company name. A holding that does not match is shown by name and never given
          a guessed ticker.
        </li>
        <li>Only a silent signal uses this data: it adds no points to a trade plan.</li>
      </ul>
    </div>
  );
}

/** Refresh button shared by the Funds and 5% owners tabs. Waits out the server's cooldown on its own. */
export function RefreshFundsControls({ cooldownSeconds }: { cooldownSeconds: number }) {
  const refresh = useRefreshFunds();
  const [waitUntil, setWaitUntil] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    if (waitUntil === null) return;
    const id = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [waitUntil]);

  const remaining = waitUntil === null ? 0 : Math.max(0, Math.ceil((waitUntil - now) / 1000));
  const data = refresh.data;

  function click() {
    setNow(Date.now());
    setWaitUntil(Date.now() + cooldownSeconds * 1000);
    refresh.mutate();
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 6, alignItems: 'flex-start' }}>
      <button className="btn btn-secondary" onClick={click} disabled={refresh.isPending || remaining > 0} data-testid="funds-refresh">
        {refresh.isPending
          ? 'Loading from SEC EDGAR… this can take a minute'
          : remaining > 0
            ? `Wait ${remaining}s to refresh again`
            : 'Load / refresh from SEC EDGAR'}
      </button>
      {refresh.error && <ErrorBanner message={(refresh.error as ApiError).message} />}
      {data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          {data.funds_processed} funds checked: {data.filings_ingested} new 13F reports ({data.holdings_created} holdings stored),{' '}
          {data.ownership_filings_created} new 13D/13G filings.
          {data.legacy_skipped > 0 && ` ${data.legacy_skipped} older free-text filings were not read.`}
          {data.errors.length > 0 && ` ${data.errors.length} problem(s), first: ${data.errors[0]}`}
        </div>
      )}
    </div>
  );
}

function FundCard({ fund, selected, onSelect }: { fund: FundSummary; selected: boolean; onSelect: () => void }) {
  return (
    <button
      type="button"
      onClick={onSelect}
      aria-pressed={selected}
      className="card"
      data-testid={`fund-${fund.cik}`}
      style={{
        textAlign: 'left',
        cursor: 'pointer',
        display: 'flex',
        flexDirection: 'column',
        gap: 4,
        borderColor: selected ? 'var(--text)' : undefined,
        color: 'var(--text)',
        font: 'inherit',
      }}
    >
      <div style={{ fontWeight: 600, fontSize: 14 }}>{fund.name}</div>
      {fund.has_data ? (
        <>
          <div className="text-muted" style={{ fontSize: 12 }}>
            Quarter ended {fund.latest_period} · filed {fund.latest_filed_at?.slice(0, 10)}
          </div>
          <div className="text-muted tabular-nums" style={{ fontSize: 12 }}>
            {fund.holdings_count} positions, {formatMoney(fund.total_value, { compact: true })}
            {fund.value_unit === 'thousands' && ' (filed in thousands, shown in dollars)'}
          </div>
          <div className="text-muted" style={{ fontSize: 11 }}>
            {fund.quarters_stored} quarter{fund.quarters_stored === 1 ? '' : 's'} stored · {fund.matched_count}/{fund.holdings_count} matched to a ticker
          </div>
        </>
      ) : (
        <div className="text-muted" style={{ fontSize: 12 }}>
          {fund.latest_is_notice ? 'Files a notice: its holdings are reported inside another manager’s 13F.' : 'Nothing loaded yet.'}
        </div>
      )}
      {fund.latest_is_notice && fund.has_data && <span className="badge badge-amber">latest filing is a notice</span>}
    </button>
  );
}

function changeName(c: FundChange) {
  return (
    <>
      {c.symbol ? <TickerLink symbol={c.symbol} /> : <span className="text-muted">no ticker</span>}
      <div className="text-muted" style={{ fontSize: 11, maxWidth: 260 }}>
        {c.issuer}
        {c.put_call ? ` · ${c.put_call.toUpperCase()} option` : ''}
        {c.share_type === 'PRN' ? ' · bond (principal)' : ''}
      </div>
    </>
  );
}

function ChangesTable({ changes }: { changes: FundChange[] }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table>
        <thead>
          <tr>
            <th>Holding</th>
            <th>Change</th>
            <th style={{ textAlign: 'right' }}>Shares now</th>
            <th style={{ textAlign: 'right' }}>Shares before</th>
            <th style={{ textAlign: 'right' }}>Share change</th>
            <th style={{ textAlign: 'right' }} title="Share of the fund's reported portfolio value">
              Weight now
            </th>
          </tr>
        </thead>
        <tbody>
          {changes.map((c) => {
            const badge = STATUS_BADGE[c.status];
            return (
              <tr key={`${c.cusip}-${c.put_call ?? ''}-${c.share_type}`}>
                <td>{changeName(c)}</td>
                <td>
                  <span className={`badge ${badge.cls}`}>{badge.label}</span>
                </td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>{c.shares_now.toLocaleString()}</td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>{c.shares_before.toLocaleString()}</td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>
                  {c.change_pct === null ? 'new' : `${c.change_pct >= 0 ? '+' : ''}${c.change_pct.toFixed(1)}%`}
                </td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>
                  {c.weight_now_pct > 0 ? `${c.weight_now_pct.toFixed(2)}%` : '—'}
                  {c.weight_before_pct > 0 && (
                    <div className="text-muted" style={{ fontSize: 11 }}>
                      was {c.weight_before_pct.toFixed(2)}%
                    </div>
                  )}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function FundDetail({ cik }: { cik: string }) {
  const [status, setStatus] = useState('all');
  const changes = useFundChanges(cik, status);
  if (changes.isLoading) return <LoadingSpinner label="Loading changes…" />;
  if (changes.error) return <ErrorBanner message={(changes.error as ApiError).message} onRetry={() => changes.refetch()} />;
  const d = changes.data;
  if (!d) return null;
  if (!d.has_data) {
    return (
      <div className="card text-muted" style={{ fontSize: 13 }}>
        No 13F holdings report is stored for this fund yet. Use the load button above.
      </div>
    );
  }
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 14 }} data-testid="fund-detail">
      <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: 16 }}>{d.manager ?? `CIK ${d.cik}`}</h3>
        <span className="text-muted" style={{ fontSize: 12 }}>
          Quarter ended {d.period}, public from {d.filed_at?.slice(0, 10)}
          {d.has_comparison && ` · compared with the quarter ended ${d.previous_period}`}
          {d.filing_url && isSafeHttpUrl(d.filing_url) && (
            <>
              {' · '}
              <a href={d.filing_url} target="_blank" rel="noopener noreferrer">
                filing
              </a>
            </>
          )}
        </span>
      </div>
      {!d.has_comparison ? (
        <div className="text-muted" style={{ fontSize: 13 }}>
          Only one quarter is stored for this fund, so there is nothing to compare yet. Its largest positions are below.
        </div>
      ) : (
        <>
          {!d.consecutive && (
            <div className="badge badge-amber" style={{ fontSize: 12 }}>
              The earlier stored quarter is not the quarter right before this one: changes may span several quarters.
            </div>
          )}
          <div className="stat-grid">
            <StatCard label="New positions" value={String(d.counts.new ?? 0)} />
            <StatCard label="Added to" value={String(d.counts.added ?? 0)} />
            <StatCard label="Trimmed" value={String(d.counts.trimmed ?? 0)} />
            <StatCard label="Sold out" value={String(d.counts.sold_out ?? 0)} />
          </div>
          <div style={{ display: 'flex', gap: 12, alignItems: 'center', flexWrap: 'wrap' }}>
            <select value={status} onChange={(e) => setStatus(e.target.value)} aria-label="Filter changes" style={{ width: 'auto' }}>
              {STATUS_FILTERS.map((s) => (
                <option key={s.value} value={s.value}>
                  {s.label}
                </option>
              ))}
            </select>
            <span className="text-muted" style={{ fontSize: 12 }}>
              {d.total > d.shown ? `Showing ${d.shown} of ${d.total}` : `${d.total} rows`}, largest first
            </span>
          </div>
          {d.changes.length === 0 ? <EmptyState>No positions with this change.</EmptyState> : <ChangesTable changes={d.changes} />}
        </>
      )}
      <div>
        <h4 style={{ fontSize: 14, marginBottom: 6 }}>Largest positions</h4>
        <div style={{ overflowX: 'auto' }}>
          <table>
            <thead>
              <tr>
                <th>Holding</th>
                <th style={{ textAlign: 'right' }}>Shares</th>
                <th style={{ textAlign: 'right' }}>Value</th>
                <th style={{ textAlign: 'right' }}>Weight</th>
              </tr>
            </thead>
            <tbody>
              {d.top_holdings.map((p) => (
                <tr key={`${p.cusip}-${p.put_call ?? ''}-${p.share_type}`}>
                  <td>
                    {p.symbol ? <TickerLink symbol={p.symbol} /> : <span className="text-muted">no ticker</span>}
                    <div className="text-muted" style={{ fontSize: 11 }}>
                      {p.issuer}
                      {p.put_call ? ` · ${p.put_call.toUpperCase()}` : ''}
                    </div>
                  </td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{p.shares.toLocaleString()}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatMoney(p.value, { compact: true })}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{p.weight_pct.toFixed(2)}%</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
  );
}

function EmptyFunds({ data }: { data: FundsOverview }) {
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }} data-testid="funds-empty">
      <h3 style={{ fontSize: 16 }}>No fund filings loaded yet</h3>
      <div className="text-muted" style={{ fontSize: 14 }}>
        Nothing is shown because nothing has been stored, not because the funds are quiet. Load the latest two quarters of each
        followed fund with the button, or fill a longer history from a terminal at the repo root:
      </div>
      <code style={{ display: 'block', padding: 10, background: 'var(--canvas)', borderRadius: 6, fontSize: 13, overflowX: 'auto' }}>
        {data.ingest_command}
      </code>
      <RefreshFundsControls cooldownSeconds={data.refresh_cooldown_seconds} />
    </div>
  );
}

export function FundsTab() {
  const overview = useFundsOverview();
  const [picked, setPicked] = useState<string | null>(null);

  if (overview.isLoading) return <LoadingSpinner />;
  if (overview.error) return <ErrorBanner message={(overview.error as ApiError).message} onRetry={() => overview.refetch()} />;
  const data = overview.data;
  if (!data) return null;

  const selected = picked ?? data.funds.find((f) => f.has_data)?.cik ?? null;
  return (
    <>
      <FundNotes />
      {data.sec_contact_is_placeholder && (
        <div className="badge badge-amber" style={{ fontSize: 13, padding: '10px 14px' }}>
          No contact address is set for SEC requests (SEC_EDGAR_USER_AGENT), so SEC may refuse them. Set one in the backend environment.
        </div>
      )}
      {!data.has_data ? (
        <EmptyFunds data={data} />
      ) : (
        <>
          <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start', justifyContent: 'space-between', flexWrap: 'wrap' }}>
            <div className="text-muted" style={{ fontSize: 13, maxWidth: 560 }}>
              {data.using_starter_list ? 'The built-in starting list of funds. ' : 'The funds you follow. '}
              Change the list on the Settings page.
            </div>
            <RefreshFundsControls cooldownSeconds={data.refresh_cooldown_seconds} />
          </div>
          <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(260px, 1fr))' }}>
            {data.funds.map((f) => (
              <FundCard key={f.cik} fund={f} selected={f.cik === selected} onSelect={() => setPicked(f.cik)} />
            ))}
          </div>
          {selected && <FundDetail cik={selected} />}
        </>
      )}
    </>
  );
}
