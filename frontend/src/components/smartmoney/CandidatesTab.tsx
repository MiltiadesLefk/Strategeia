import { useState } from 'react';
import { useSmartMoneyCandidates } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import type { SmartMoneyCandidate } from '../../api/types';
import { StatCard } from '../StatCard';
import { SmartMoneyTotals } from './SmartMoneyTotals';
import { TickerLink } from '../TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatRelativeTime } from '../common';

const STATUS_OPTIONS = [
  { value: '', label: 'All statuses' },
  { value: 'new', label: 'New (not evaluated yet)' },
  { value: 'needs_review', label: 'Needs review' },
  { value: 'auto_bought', label: 'Auto-bought' },
  { value: 'bought', label: 'Bought' },
  { value: 'skipped', label: 'Skipped' },
  { value: 'expired', label: 'Expired' },
];

const SOURCE_LABEL: Record<string, string> = {
  insider: 'Insiders (Form 4)',
  house: 'House members',
  ownership: '5% holders (13D/13G)',
  fund: 'Funds (13F)',
  filing_8k: '8-K filings',
};

function formatDate(iso: string | null | undefined): string {
  return iso ? new Date(iso).toISOString().slice(0, 10) : '—';
}

function Points({ value }: { value: number }) {
  const sign = value > 0 ? '+' : '';
  return (
    <span className={value < 0 ? 'badge badge-red' : 'badge badge-green'} style={{ minWidth: 44, textAlign: 'center' }}>
      {sign}
      {value}
    </span>
  );
}

function CandidateCard({ c }: { c: SmartMoneyCandidate }) {
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 10 }} data-testid="candidate-card">
      <div className="split-row" style={{ gap: 12, flexWrap: 'wrap' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          <TickerLink symbol={c.symbol} />
          <span className="badge badge-neutral">{c.status.replace('_', ' ')}</span>
          {c.scope && <span className="badge badge-neutral">{c.scope === 'market_wide' ? 'market-wide' : 'universe'}</span>}
          {c.reason && <span className="text-muted" style={{ fontSize: 12 }}>{c.reason}</span>}
        </div>
        <div className="tabular-nums" style={{ fontWeight: 700, fontSize: 18 }}>
          score {c.score}
        </div>
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        Reported filings counted as of {formatDate(c.scored_at)}
        {c.first_trigger_known_at && <>; the earliest of them became public {formatDate(c.first_trigger_known_at)}</>}. Reports can be up
        to 45 days after the trade. Flagged {formatRelativeTime(c.created_at)}
        {c.expires_at && <>, expires {formatDate(c.expires_at)}</>}.
      </div>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {c.components.map((comp, i) => (
          <div key={`${comp.source}-${i}`} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
            <Points value={comp.points} />
            <div style={{ fontSize: 13 }}>
              <strong>{SOURCE_LABEL[comp.source] ?? comp.source}:</strong> {comp.reason}
              {comp.delay_note && (
                <div className="text-muted" style={{ fontSize: 11 }}>
                  {comp.delay_note}
                </div>
              )}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

export function CandidatesTab() {
  const [status, setStatus] = useState('');
  const q = useSmartMoneyCandidates(status || undefined);
  const data = q.data;
  const open = data ? data.candidates.filter((c) => c.status === 'new' || c.status === 'needs_review').length : 0;

  return (
    <>
      <div className="card" style={{ background: 'var(--canvas)', border: '1px solid var(--border)', fontSize: 13 }}>
        <strong>What a candidate is.</strong> A symbol whose reported insider, House, 5% holder or fund filings added up to
        the bar. It only means the symbol is worth the normal full evaluation; it does not open a trade, and nothing here
        says anyone knew something. {data?.disclaimer}
      </div>
      {data && <SmartMoneyTotals extra={[`${data.total} candidates`, `${open} open`, `bar ${data.min_score} points`]} note="A candidate is a stock whose buying evidence reached the bar; selling never creates one." />}
      <div className="grid stat-grid">
        <StatCard label="Candidates" value={data ? String(data.total) : '—'} note={data ? `${open} open` : undefined} />
        <StatCard label="Bar" value={data ? String(data.min_score) : '—'} note="points to become a candidate" />
        <StatCard label="Tracker scope" value={data ? (data.scope === 'market_wide' ? 'Market-wide' : 'Universe') : '—'} note="Settings: smart money scope" />
      </div>
      <div className="card" style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
        <h3 style={{ fontSize: 16, marginRight: 8 }}>Candidates</h3>
        <select aria-label="Status" style={{ width: 'auto' }} value={status} onChange={(e) => setStatus(e.target.value)}>
          {STATUS_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      </div>
      {q.isLoading && <LoadingSpinner />}
      {q.error && <ErrorBanner message={(q.error as ApiError).message} onRetry={() => q.refetch()} />}
      {data && data.candidates.length === 0 && (
        <EmptyState>
          No candidates yet. The tracker (the smart_money watcher, on the Watchers settings) records one when the reported
          filings for a symbol add up to the bar; an empty list is a normal day, not an error.
        </EmptyState>
      )}
      {data?.candidates.map((c) => <CandidateCard key={c.id} c={c} />)}
    </>
  );
}
