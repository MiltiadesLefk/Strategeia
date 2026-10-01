import { StatCard } from './StatCard';
import { EmptyState, MIN_MEANINGFUL_TRADES, formatNumber, formatR } from './common';
import type { ExcursionStats } from '../api/types';
import { formatAdverseR } from '../lib/excursion';

/** The best and worst price reached during closed trades, in R, split into winners
 *  and losers. Every number is computed on the server from closed rows that have
 *  the figures; trades closed before this was tracked are counted out, not guessed. */
export function TradeExcursionsCard({ excursions }: { excursions: ExcursionStats }) {
  const { measured, closed_trades: closed, winners, losers } = excursions;
  const pair = (mfe: number | null, mae: number | null) => `${formatR(mfe)} / ${formatAdverseR(mae)}`;

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div>
        <h3>Trade excursions</h3>
        <div className="text-muted" style={{ fontSize: 12, marginTop: 4 }}>
          The best and worst price reached during each closed trade, in R (multiples of the trade's initial risk).
          Computed from daily bars while the trade was open.
        </div>
      </div>
      {measured === 0 ? (
        <EmptyState>
          {closed === 0
            ? 'No closed trades yet.'
            : 'None of the closed trades has excursion figures (they were closed before this was tracked, or no price history was available).'}
        </EmptyState>
      ) : (
        <>
          <div className="grid stat-grid">
            <StatCard
              label="Winners: best / worst"
              value={pair(winners.avg_mfe_r, winners.avg_mae_r)}
              note={`average over ${winners.n} winner${winners.n === 1 ? '' : 's'}`}
            />
            <StatCard
              label="Losers: best / worst"
              value={pair(losers.avg_mfe_r, losers.avg_mae_r)}
              note={`average over ${losers.n} loser${losers.n === 1 ? '' : 's'}`}
            />
            <StatCard
              label="Winners' exit efficiency"
              value={excursions.exit_efficiency === null ? '—' : `${formatNumber(excursions.exit_efficiency * 100, 0)}%`}
              note="realized R ÷ best R reached"
            />
            <StatCard
              label="Losers once 1R ahead"
              value={`${excursions.losers_reached_1r} of ${losers.n}`}
              note="losers that were at least +1R first"
            />
          </div>
          <div className="text-muted" style={{ fontSize: 12, display: 'flex', flexDirection: 'column', gap: 4 }}>
            {losers.n > 0 && losers.avg_mfe_r !== null && (
              <div>
                Losers went {formatR(losers.avg_mfe_r)} in your favour on average before they reversed
                {excursions.losers_reached_1r > 0 ? ` (${excursions.losers_reached_1r} of ${losers.n} were at least +1R ahead)` : ''}.
                If that is large, exits may be too slow.
              </div>
            )}
            {winners.n > 0 && winners.avg_mae_r !== null && (
              <div>
                Winners dipped {formatNumber(winners.avg_mae_r, 2)}R against you on average first
                {excursions.winners_near_stop > 0
                  ? ` (${excursions.winners_near_stop} of ${winners.n} came within 0.2R of the stop)`
                  : ''}
                . If many winners nearly stopped out, the stop may be too tight.
              </div>
            )}
            {excursions.exit_efficiency !== null && (
              <div>
                Exit efficiency stays near 100% by design: the whole position closes at TP1, so a winner leaves at its target.
                It only says something once time-limit or manual exits win trades.
              </div>
            )}
            <div>
              {measured} of {closed} closed trade{closed === 1 ? '' : 's'} {measured === 1 ? 'has' : 'have'} figures
              {measured < MIN_MEANINGFUL_TRADES ? ' — too few to read much into' : ''}
              {measured < closed ? ' (the rest closed before this was tracked, or had no price history)' : ''}.{' '}
              Descriptive, not a recommendation.
            </div>
          </div>
        </>
      )}
    </div>
  );
}
