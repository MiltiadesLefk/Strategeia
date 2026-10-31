import { useState } from 'react';
import { useBuyReviewItem, useSkipReviewItem, useSmartMoneyReview } from '../../api/hooks';
import type { ApiError } from '../../api/client';
import type { Hindsight, ReviewItem } from '../../api/types';
import { TickerLink } from '../TickerLink';
import { EmptyState, ErrorBanner, LoadingSpinner, formatRelativeTime } from '../common';

const SOURCE_LABEL: Record<string, string> = {
  insider: 'Insiders (Form 4)',
  house: 'House members',
  ownership: '5% holders (13D/13G)',
  fund: 'Funds (13F)',
  filing_8k: '8-K filings',
};

const STATUS_LABEL: Record<string, string> = {
  bought: 'Bought',
  skipped: 'Skipped',
  expired: 'Expired',
  auto_bought: 'Auto-bought',
  needs_review: 'Needs review',
};

const REASON_LABEL: Record<string, string> = {
  tradeable_not_bought: 'Rated tradeable, but the auto sleeve did not buy it',
  rated_bad: 'The full evaluation rated it badly',
};

function Points({ value }: { value: number }) {
  return (
    <span className={value < 0 ? 'chip chip-red' : 'chip chip-green'} style={{ minWidth: 40, justifyContent: 'center' }}>
      {value > 0 ? '+' : ''}
      {value}
    </span>
  );
}

export function expiryCountdown(iso: string | null | undefined, now = Date.now()): string {
  if (!iso) return 'no expiry';
  const ms = new Date(iso).getTime() - now;
  if (Number.isNaN(ms)) return 'no expiry';
  if (ms <= 0) return 'expired';
  const days = Math.floor(ms / 86_400_000);
  const hours = Math.floor((ms % 86_400_000) / 3_600_000);
  return days > 0 ? `expires in ${days}d ${hours}h` : `expires in ${hours}h`;
}

function ReviewCard({ item }: { item: ReviewItem }) {
  const buy = useBuyReviewItem();
  const skip = useSkipReviewItem();
  const [confirm, setConfirm] = useState<'buy' | 'skip' | null>(null);
  const [note, setNote] = useState('');
  const busy = buy.isPending || skip.isPending;
  const error = (buy.error ?? skip.error) as ApiError | null;
  const open = item.status === 'needs_review';
  const r = item.rating;
  // The verdict line already quotes the plan's own reason; list only what it does not say.
  const shownReasons = item.reasons_declined.filter((d) => !(r?.verdict_text ?? '').includes(d));

  const startConfirm = (kind: 'buy' | 'skip') => {
    buy.reset();
    skip.reset();
    setConfirm(kind);
  };

  return (
    <div className="card sm-review-card" data-testid="review-card">
      <div className="sm-review-head">
        <div className="sm-who">
          <TickerLink symbol={item.symbol} />
          <span className={`chip ${open ? 'chip-amber' : ''}`}>{STATUS_LABEL[item.status] ?? item.status}</span>
          {open && <span className="text-muted" style={{ fontSize: 12 }}>{expiryCountdown(item.expires_at)}</span>}
        </div>
        <div className="sm-score">SCORE {item.score}</div>
      </div>

      {item.delay_note && <div className="text-muted" style={{ fontSize: 12 }}>{item.delay_note}</div>}
      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        {item.components.map((c, i) => (
          <div key={`${c.source}-${i}`} className="sm-component">
            <Points value={c.points} />
            <div>
              <strong>{SOURCE_LABEL[c.source] ?? c.source}:</strong> {c.reason}
            </div>
          </div>
        ))}
      </div>

      <div className="sm-box">
        <strong>Plan rating: </strong>
        {r ? (
          <>
            <span className="mono">
              {r.confidence_points}/{r.confidence_points_max} pts ({Math.round(r.confidence_score)}%)
            </span>
            {r.verdict_text && <div style={{ marginTop: 4 }}>{r.verdict_text}</div>}
          </>
        ) : (
          <span className="text-muted">no plan was produced for this item</span>
        )}
        {open && item.reason && REASON_LABEL[item.reason] && (
          <div style={{ marginTop: 4 }} data-testid="review-reason">
            <em>{REASON_LABEL[item.reason]}.</em>
          </div>
        )}
        {shownReasons.length > 0 && (
          <ul style={{ margin: '6px 0 0 18px', fontSize: 12 }}>
            {shownReasons.map((d, i) => (
              <li key={i}>{d}</li>
            ))}
          </ul>
        )}
      </div>

      {!open && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          {STATUS_LABEL[item.status]}
          {item.decided_by && <> by {item.decided_by}</>}
          {item.decided_at && <> {formatRelativeTime(item.decided_at)}</>}
          {item.note && <>: “{item.note}”</>}
          {item.position_id != null && (
            <> · paper position #{item.position_id}{item.sleeve_key ? ` in ${item.sleeve_key}` : ''}</>
          )}
        </div>
      )}

      {open && item.also_held_in_core && (
        <div className="text-muted" style={{ fontSize: 12 }} data-testid="also-held-in-core">
          Also held in the core sleeve (a separate paper account), so a buy here adds a second position in the same stock.
        </div>
      )}

      {open && item.buy_queued && (
        <div className="sm-box sm-box-amber" data-testid="buy-queued">
          <strong>Buy queued.</strong> Waiting for the open: it will be bought from fresh data once the session is under way, if it
          still passes the checks then. Nothing fills at a stale price. No position exists yet.
        </div>
      )}

      {open && (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
          {!item.market_open && !item.buy_queued && (
            <div className="text-muted" style={{ fontSize: 12 }}>
              The market is closed. A buy now is refused or waits for a fresh evaluation at the next open; nothing fills at a stale price.
            </div>
          )}
          {confirm === 'skip' && (
            <input
              type="text"
              aria-label="Note (optional)"
              placeholder="Note (optional)"
              value={note}
              maxLength={300}
              onChange={(e) => setNote(e.target.value)}
            />
          )}
          {confirm && (
            <div style={{ fontSize: 13 }}>
              {confirm === 'buy'
                ? `Open a simulated ${item.symbol} position in the smart-money-manual sleeve? No real money moves.`
                : `Skip ${item.symbol}? It stays in the history and is counted as a missed trade.`}
            </div>
          )}
          <div className="sm-actions">
            {confirm ? (
              <>
                <button
                  type="button"
                  className="btn btn-primary"
                  disabled={busy}
                  onClick={() => {
                    if (confirm === 'buy') buy.mutate(item.id, { onSuccess: () => setConfirm(null) });
                    else skip.mutate({ id: item.id, note: note.trim() || undefined }, { onSuccess: () => setConfirm(null) });
                  }}
                >
                  {busy ? 'Working…' : confirm === 'buy' ? 'Confirm buy' : 'Confirm skip'}
                </button>
                <button type="button" className="btn btn-secondary" disabled={busy} onClick={() => setConfirm(null)}>
                  Cancel
                </button>
              </>
            ) : (
              <>
                {!item.buy_queued && (
                  <button type="button" className="btn btn-primary" onClick={() => startConfirm('buy')}>
                    Buy
                  </button>
                )}
                <button type="button" className="btn btn-secondary" onClick={() => startConfirm('skip')}>
                  Skip
                </button>
              </>
            )}
          </div>
          {error && (
            <div className="text-red" role="alert" style={{ fontSize: 13 }}>
              {error.status === 409 ? `Not done: ${error.message}` : error.message}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function pct(v: number | null): string {
  return v == null ? '–' : `${v.toFixed(0)}%`;
}

function HindsightCard({ data }: { data: Hindsight }) {
  return (
    <div className="card" style={{ fontSize: 13 }} data-testid="hindsight">
      <div className="panel-header"><span>In hindsight: what the items nobody bought would have done</span></div>
      <div className="text-muted" style={{ marginTop: 4 }}>
        {data.note} Counts are all items in the group; only the resolved ones enter the averages.
      </div>
      <div className="sm-hindsight-grid">
        {data.groups.map((g) => (
          <div key={g.key}>
            <div style={{ fontWeight: 600 }}>{g.label}</div>
            <div className="tabular-nums">n = {g.n}</div>
            <div className="text-muted tabular-nums" style={{ fontSize: 12 }}>
              {g.resolved} resolved · {g.open} still open · {g.awaiting} not computed yet
              {g.not_simulated > 0 && <> · {g.not_simulated} not simulated</>}
            </div>
            {g.resolved === 0 ? (
              <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>Nothing resolved yet.</div>
            ) : g.small_sample ? (
              <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
                Too early: {g.resolved} resolved, fewer than {data.min_trades_for_reading}. Not enough to say anything.
              </div>
            ) : (
              <div className="tabular-nums" style={{ fontSize: 12, marginTop: 4 }}>
                win rate {pct(g.win_rate)} · avg {g.avg_r != null ? `${g.avg_r.toFixed(2)}R` : '–'}
                {g.avg_r_low != null && g.avg_r_high != null && <> (95% {g.avg_r_low.toFixed(2)} to {g.avg_r_high.toFixed(2)}R)</>}
              </div>
            )}
          </div>
        ))}
      </div>
      <div className="text-muted" style={{ fontSize: 12, marginTop: 6 }}>
        Hypothetical: daily bars, filled at the last known close, no costs. Reported, not proven.
      </div>
    </div>
  );
}

export function ReviewQueueTab() {
  const pendingQ = useSmartMoneyReview('needs_review');
  const decidedQ = useSmartMoneyReview('decided');
  const pending = pendingQ.data?.items ?? [];
  const decided = decidedQ.data?.items ?? [];
  const hindsight = decidedQ.data?.hindsight ?? pendingQ.data?.hindsight ?? null;

  return (
    <>
      <div className="card sm-queue-intro">
        <div className="panel-header"><span>Review queue</span></div>
        <div><strong>What the review queue is.</strong> Symbols whose reported filings (as of the dates shown, up to 45 days late)
        triggered the tracker but which the auto sleeve did not buy: the normal trade plan was rated poorly, or it was tradeable
        and still not taken. You decide: Buy opens a simulated position in the smart-money-manual sleeve, Skip records your
        choice. Nothing here shows the signal works. {pendingQ.data?.disclaimer}
        </div>
      </div>
      {pendingQ.isLoading && <LoadingSpinner />}
      {pendingQ.error && <ErrorBanner message={(pendingQ.error as ApiError).message} onRetry={() => pendingQ.refetch()} />}
      {pendingQ.data && <div className="label-mono">Needs review ({pending.length})</div>}
      {pendingQ.data && pending.length === 0 && (
        <EmptyState>Nothing needs review. An empty queue is a normal day, not an error.</EmptyState>
      )}
      <div className="sm-queue-grid">
        {pending.map((i) => (
          <ReviewCard key={i.id} item={i} />
        ))}
      </div>
      {decidedQ.error && <ErrorBanner message={(decidedQ.error as ApiError).message} onRetry={() => decidedQ.refetch()} />}
      {hindsight && <HindsightCard data={hindsight} />}
      {decided.length > 0 && (
        <>
          <div className="label-mono" style={{ marginTop: 8 }}>Decided ({decided.length})</div>
          <div className="sm-queue-grid">
            {decided.map((i) => (
              <ReviewCard key={i.id} item={i} />
            ))}
          </div>
        </>
      )}
    </>
  );
}
