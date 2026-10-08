import { useState } from 'react';
import { useNavigate } from 'react-router-dom';
import { useMissedTrades, useRefreshMissedTrades } from '../api/hooks';
import type { ApiError } from '../api/client';
import type {
  MissedQuestion,
  MissedQuestionVerdict,
  MissedTakenStats,
  MissedTradeCategory,
  MissedTradeItem,
} from '../api/types';
import { TickerLink } from './TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatR, formatRelativeTime } from './common';

const COLLAPSED_ROWS = 12;

const VERDICT_BADGE: Record<MissedQuestionVerdict, { label: string; className: string }> = {
  too_early: { label: 'Too early to tell', className: 'badge badge-amber' },
  no_data: { label: 'No result yet', className: 'badge badge-amber' },
  unclear: { label: 'No clear answer', className: 'badge badge-neutral' },
  saved: { label: 'Saved money', className: 'badge badge-green' },
  cost: { label: 'Cost money', className: 'badge badge-red' },
  bar_justified: { label: 'Bar justified', className: 'badge badge-green' },
  bar_costs: { label: 'Bar may be too strict', className: 'badge badge-red' },
};

const EXIT_LABEL: Record<string, string> = { stop_hit: 'stop', tp1_hit: 'target', time_exit: 'time limit' };

function rClass(value: number | null): string {
  if (value === null) return '';
  return value > 0 ? 'text-green' : value < 0 ? 'text-red' : '';
}

function range(low: number | null, high: number | null, format: (v: number) => string): string | null {
  return low === null || high === null ? null : `${format(low)} to ${format(high)}`;
}

function formatDay(iso: string | null): string {
  if (!iso) return '—';
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? '—' : d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function QuestionCard({ q }: { q: MissedQuestion }) {
  const badge = VERDICT_BADGE[q.verdict];
  return (
    <div style={{ background: 'var(--card-alt)', border: '1px solid var(--border)', borderRadius: 12, padding: 14 }} data-testid={`missed-question-${q.key}`}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, flexWrap: 'wrap', marginBottom: 6 }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>{q.question}</span>
        <span className={badge.className}>{badge.label}</span>
      </div>
      <div className="text-muted" style={{ fontSize: 13, lineHeight: 1.5 }}>
        {q.answer}
      </div>
    </div>
  );
}

function CategoryCard({ c, minTrades }: { c: MissedTradeCategory; minTrades: number }) {
  const winRange = range(c.win_rate_low, c.win_rate_high, (v) => `${v.toFixed(0)}%`);
  const rRange = range(c.avg_r_low, c.avg_r_high, formatR);
  return (
    <div
      style={{ background: 'var(--card-alt)', border: '1px solid var(--border)', borderRadius: 12, padding: 14, minWidth: 0 }}
      data-testid={`missed-category-${c.key}`}
    >
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>{c.label}</span>
        <span className="text-muted tabular-nums" style={{ fontSize: 12 }}>
          {c.n} plan{c.n === 1 ? '' : 's'}
        </span>
      </div>

      {c.resolved === 0 ? (
        <div className="text-muted" style={{ fontSize: 12 }}>
          {c.not_simulated === c.n && c.n > 0 ? 'Counted only: no hypothetical trade can be built for these.' : 'No result yet.'}
        </div>
      ) : (
        <>
          <div className="tabular-nums" style={{ fontSize: 22, fontWeight: 700 }}>
            <span className={rClass(c.avg_r)}>{formatR(c.avg_r)}</span>
            <span className="text-muted" style={{ fontSize: 12, fontWeight: 400 }}> average per trade</span>
          </div>
          <div className="text-muted tabular-nums" style={{ fontSize: 12, marginBottom: 6 }}>
            {rRange ? `95% range ${rRange}` : 'no range yet (one trade)'}
          </div>
          <div className="tabular-nums" style={{ fontSize: 13 }}>
            Win rate <strong>{c.win_rate === null ? '—' : `${c.win_rate.toFixed(0)}%`}</strong>
            {winRange && <span className="text-muted"> (95% {winRange})</span>}
          </div>
          <div className="tabular-nums" style={{ fontSize: 13 }}>
            Total <strong className={rClass(c.total_r)}>{formatR(c.total_r)}</strong>
            <span className="text-muted"> over {c.resolved} resolved</span>
          </div>
          {c.small_sample && (
            <div className="badge badge-amber" style={{ marginTop: 8, display: 'inline-block', whiteSpace: 'normal' }} title={`Fewer than ${minTrades} resolved trades: too few to read much into`}>
              only {c.resolved} resolved: too few to read much into
            </div>
          )}
        </>
      )}

      <div className="text-muted" style={{ fontSize: 11, marginTop: 8, lineHeight: 1.5 }}>
        {c.open > 0 && <div>{c.open} still open (marked, not final{c.open_avg_r !== null ? `: ${formatR(c.open_avg_r)} on average` : ''})</div>}
        {c.awaiting > 0 && <div>{c.awaiting} waiting to be computed: press Refresh</div>}
        {c.not_simulated > 0 && c.resolved + c.open + c.awaiting > 0 && <div>{c.not_simulated} counted, not simulated</div>}
        {c.details.map((d) => (
          <div key={d.detail}>
            {d.n} × {d.label}
          </div>
        ))}
      </div>
    </div>
  );
}

function TakenCard({ taken, minTrades }: { taken: MissedTakenStats; minTrades: number }) {
  const winRange = range(taken.win_rate_low, taken.win_rate_high, (v) => `${v.toFixed(0)}%`);
  const rRange = range(taken.avg_r_low, taken.avg_r_high, formatR);
  return (
    <div style={{ border: '1px solid var(--border)', borderRadius: 12, padding: 14, minWidth: 0 }} data-testid="missed-taken">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: 8, marginBottom: 8 }}>
        <span style={{ fontWeight: 700, fontSize: 13 }}>For comparison: the trades taken</span>
        <span className="text-muted tabular-nums" style={{ fontSize: 12 }}>
          {taken.n} closed
        </span>
      </div>
      {taken.n === 0 ? (
        <div className="text-muted" style={{ fontSize: 12 }}>No closed paper trades yet.</div>
      ) : (
        <>
          <div className="tabular-nums" style={{ fontSize: 22, fontWeight: 700 }}>
            <span className={rClass(taken.avg_r)}>{formatR(taken.avg_r)}</span>
            <span className="text-muted" style={{ fontSize: 12, fontWeight: 400 }}> average per trade</span>
          </div>
          <div className="text-muted tabular-nums" style={{ fontSize: 12, marginBottom: 6 }}>
            {rRange ? `95% range ${rRange}` : 'no range yet (one trade)'}
          </div>
          <div className="tabular-nums" style={{ fontSize: 13 }}>
            Win rate <strong>{taken.win_rate === null ? '—' : `${taken.win_rate.toFixed(0)}%`}</strong>
            {winRange && <span className="text-muted"> (95% {winRange})</span>}
          </div>
          <div className="tabular-nums" style={{ fontSize: 13 }}>
            Total <strong className={rClass(taken.total_r)}>{formatR(taken.total_r)}</strong>
          </div>
          {taken.small_sample && (
            <div className="badge badge-amber" style={{ marginTop: 8, display: 'inline-block' }} title={`Fewer than ${minTrades} closed trades`}>
              only {taken.n} closed: too few to read much into
            </div>
          )}
        </>
      )}
      <div className="text-muted" style={{ fontSize: 11, marginTop: 8 }}>
        Real paper results, including hourly-bar exits, so not strictly like-for-like.
      </div>
    </div>
  );
}

function stateLabel(t: MissedTradeItem): { text: string; className: string } {
  if (t.state === 'resolved') return { text: EXIT_LABEL[t.exit_reason ?? ''] ?? 'closed', className: 'badge badge-neutral' };
  if (t.state === 'open') return { text: 'open (marked)', className: 'badge badge-amber' };
  if (t.state === 'awaiting') return { text: 'to compute', className: 'badge badge-amber' };
  return { text: 'not simulated', className: 'badge badge-neutral' };
}

function TradeRow({ t }: { t: MissedTradeItem }) {
  const state = stateLabel(t);
  const navigate = useNavigate();
  return (
    <tr
      onClick={() => navigate(`/trade-plans?plan=${t.plan_id}`)}
      style={{ cursor: 'pointer' }}
      title="Click to read this plan"
      data-testid={`missed-row-${t.plan_id}`}
    >
      <td style={{ fontWeight: 600 }}>
        <TickerLink symbol={t.symbol} iconSize={22} />
      </td>
      <td className="text-muted" style={{ whiteSpace: 'nowrap' }}>{formatDay(t.created_at)}</td>
      <td>{t.category_label}{t.detail_label && <div className="text-muted" style={{ fontSize: 11 }}>{t.detail_label}</div>}</td>
      <td style={{ textTransform: 'capitalize' }}>{t.direction ?? '—'}</td>
      <td className="tabular-nums">{t.confidence_score}</td>
      <td>
        <span className={state.className} title={t.note ?? undefined}>{state.text}</span>
        {t.exit_date && <span className="text-muted" style={{ fontSize: 11, marginLeft: 6 }}>{formatDay(t.exit_date)}</span>}
      </td>
      <td className={`tabular-nums ${rClass(t.r_multiple)}`}>
        {t.r_multiple === null ? '—' : formatR(t.r_multiple)}
        {t.state === 'open' && t.r_multiple !== null && <span className="text-muted" style={{ fontSize: 10 }}> mark</span>}
      </td>
      <td style={{ textAlign: 'right', whiteSpace: 'nowrap' }}>
        <button
          type="button"
          className="btn btn-secondary"
          style={{ padding: '4px 12px', fontSize: 12 }}
          aria-label={`Read plan ${t.plan_id}, ${t.symbol}`}
          onClick={(e) => {
            e.stopPropagation();
            navigate(`/trade-plans?plan=${t.plan_id}`);
          }}
        >
          Read plan
        </button>
      </td>
    </tr>
  );
}

/**
 * What the trades the app did not take would have earned. Everything is computed on the
 * server (a refresh builds each hypothetical trade from the bars known at the decision and
 * walks it with the paper engine's exit rules); this card only lays it out. It leads with
 * the caveat, because a hypothetical result is the easiest kind to over-read.
 */
export function MissedTradesCard() {
  const { data, isLoading, error, refetch } = useMissedTrades();
  const { mutate: refresh, isPending, data: refreshed, error: refreshError } = useRefreshMissedTrades();
  const [showAll, setShowAll] = useState(false);

  const trades = data?.trades ?? [];
  const shown = showAll ? trades : trades.slice(0, COLLAPSED_ROWS);
  const categories = data?.categories.filter((c) => c.key !== 'unclassified' || c.n > 0) ?? [];

  return (
    <div className="card" data-testid="missed-trades-card">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', flexWrap: 'wrap', gap: 8, marginBottom: 4 }}>
        <div>
          <h3>Missed trades</h3>
          <div className="text-muted" style={{ fontSize: 12, maxWidth: 640, marginTop: 2 }}>
            What the trades the app declined, vetoed or left unfilled would have earned. Rule-based arithmetic on past
            prices; no AI involved.
          </div>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10, flexWrap: 'wrap' }}>
          {data?.last_computed_at && (
            <span className="text-muted" style={{ fontSize: 12 }}>computed {formatRelativeTime(data.last_computed_at)}</span>
          )}
          <button type="button" className="btn btn-secondary" onClick={() => refresh()} disabled={isPending}>
            {isPending ? 'Computing…' : 'Refresh'}
          </button>
        </div>
      </div>

      {refreshError && <ErrorBanner message={(refreshError as ApiError).message} />}
      {refreshed && !refreshError && (
        <div className="text-muted" style={{ fontSize: 12, marginBottom: 8 }} role="status">
          {refreshed.busy
            ? 'A refresh is already running.'
            : `Computed ${refreshed.computed} (${refreshed.resolved} resolved, ${refreshed.still_open} still open` +
              `${refreshed.no_data > 0 ? `, ${refreshed.no_data} without price data` : ''}).` +
              `${refreshed.remaining > 0 ? ` ${refreshed.remaining} more are left: refresh again.` : ''}`}
        </div>
      )}
      {isLoading && <LoadingSpinner label="Loading missed trades…" />}
      {error && <ErrorBanner message={(error as ApiError).message} onRetry={() => refetch()} />}

      {data && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 16, marginTop: 8 }}>
          <div className="badge badge-amber" style={{ whiteSpace: 'normal', padding: '10px 14px', fontSize: 12, fontWeight: 500, lineHeight: 1.5 }} role="note">
            These are hypothetical trades, not results: filled at the last daily close known when the plan was made,
            walked on daily bars only, with no cash or position limits.{' '}
            <details style={{ display: 'inline' }}>
              <summary style={{ cursor: 'pointer', display: 'inline', fontWeight: 600 }}>All caveats</summary>
              <ul style={{ margin: '6px 0 0', paddingLeft: 18 }}>
                {data.caveats.map((line) => (
                  <li key={line}>{line}</li>
                ))}
              </ul>
            </details>
          </div>

          {data.plans_considered === 0 ? (
            <EmptyState>
              Nothing declined yet. Plans the app turns down, vetoes or leaves unfilled appear here once you have
              generated some.
            </EmptyState>
          ) : (
            <>
              {data.awaiting_refresh > 0 && (
                <div className="text-muted" style={{ fontSize: 12 }}>
                  {data.awaiting_refresh} plan{data.awaiting_refresh === 1 ? ' is' : 's are'} waiting to be computed.
                  Press Refresh (it also runs on its own after the close each day).
                </div>
              )}

              <div className="split-row" style={{ alignItems: 'stretch' }}>
                {data.questions.map((q) => (
                  <QuestionCard key={q.key} q={q} />
                ))}
              </div>

              <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fit, minmax(230px, 1fr))', gap: 12 }}>
                {categories.map((c) => (
                  <CategoryCard key={c.key} c={c} minTrades={data.min_trades_for_reading} />
                ))}
                <TakenCard taken={data.taken} minTrades={data.min_trades_for_reading} />
              </div>

              <div>
                <h4 style={{ marginBottom: 8 }}>
                  Every declined plan
                  {data.plans_considered > data.trades_listed && (
                    <span className="text-muted" style={{ fontSize: 12, fontWeight: 400 }}> (newest {data.trades_listed} of {data.plans_considered})</span>
                  )}
                </h4>
                <div style={{ overflowX: 'auto' }}>
                  <table>
                    <thead>
                      <tr>
                        <th>Symbol</th>
                        <th>Date</th>
                        <th>Kind</th>
                        <th>Side</th>
                        <th>Confidence</th>
                        <th>Outcome</th>
                        <th>Hypothetical R</th>
                        <th />
                      </tr>
                    </thead>
                    <tbody>
                      {shown.map((t) => (
                        <TradeRow key={t.plan_id} t={t} />
                      ))}
                    </tbody>
                  </table>
                </div>
                {trades.length > COLLAPSED_ROWS && (
                  <button type="button" className="btn btn-secondary" style={{ marginTop: 10 }} onClick={() => setShowAll((v) => !v)}>
                    {showAll ? 'Show fewer' : `Show all ${trades.length}`}
                  </button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </div>
  );
}
