import { Fragment, useState } from 'react';
import { useAnalysis, useEquityCurve, useClosePosition, useResetPortfolio, usePortfolioStats, usePositions, useSettings, useSettingsStatus } from '../api/hooks';
import { LessonCell, LessonDetailRow } from '../components/TradeLesson';
import { StatCard } from '../components/StatCard';
import { DirectionBadge } from '../components/Badge';
import { TickerLink } from '../components/TickerLink';
import { TradeExcursionsCard } from '../components/TradeExcursionsCard';
import { formatAdverseR } from '../lib/excursion';
import { CalibrationCard } from '../components/CalibrationCard';
import { PriceAlertsCard } from '../components/PriceAlertsPanel';
import { EquityCurveChart } from '../components/chart/EquityCurveChart';
import { CandlestickChart, type PriceLevel } from '../components/chart/CandlestickChart';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatNumber, formatPct, formatR, formatRelativeTime, sampleSizeNote } from '../components/common';
import type { ApiError } from '../api/client';
import type { Position } from '../api/types';
import { useInView } from '../lib/useInView';
import { DataFreshness } from '../components/DataFreshness';
import { Flash } from '../components/Flash';

// How a closed trade ended, in the words the rest of the app uses. The backend
// stores the machine value (close_reason); an unknown one is shown as-is.
const CLOSE_REASON_LABELS: Record<string, string> = {
  stop_hit: 'Stop',
  tp1_hit: 'Target (TP1)',
  time_exit: 'Time limit',
  manual: 'Manual',
};
function closeReasonLabel(reason: string | null): string {
  return reason ? (CLOSE_REASON_LABELS[reason] ?? reason) : '—';
}

// Hover text for how an exit was placed in time (PaperPosition.exit_resolution).
const EXIT_RESOLUTION_HINTS: Record<string, string> = {
  daily: 'Found on the daily bar.',
  hourly: 'Hourly bars showed which level was touched first.',
  daily_ambiguous_stop_first:
    'The day reached both the stop and the target and no hourly bars were available, so the stop was taken (the cautious order).',
  hourly_ambiguous_stop_first:
    'Both the stop and the target sat inside one hourly bar, so the stop was taken (the cautious order).',
};
function exitResolutionHint(p: Position): string | undefined {
  const parts: string[] = [];
  if (p.exit_resolution) parts.push(EXIT_RESOLUTION_HINTS[p.exit_resolution] ?? p.exit_resolution);
  if (p.entry_day_check === 'daily_only') parts.push('The rest of the entry day could not be checked hour by hour.');
  return parts.length ? parts.join(' ') : undefined;
}

/** Calendar date (YYYY-MM-DD) of the daily bar a position was entered on: bars
 *  carry the New York date for a US equity and the UTC date for a crypto pair. */
function entryBarDate(symbol: string, openedAt: string): string {
  const timeZone = symbol.toUpperCase().endsWith('-USD') ? 'UTC' : 'America/New_York';
  return new Date(openedAt).toLocaleDateString('en-CA', { timeZone });
}

function ActivePositionCard({ position }: { position: Position }) {
  // Deferred until the card is near the viewport — see lib/useInView.
  const { ref, inView } = useInView<HTMLDivElement>();
  const { data: analysis, isLoading, dataUpdatedAt: priceUpdatedAt } = useAnalysis(position.symbol, '3mo', inView);
  const { mutate: closePosition, isPending: closing } = useClosePosition();
  const { data: appSettings } = useSettings();

  const levels: PriceLevel[] = [
    { price: position.entry_price, color: '#2563eb', title: 'Entry' },
    { price: position.stop_loss, color: '#ef4444', title: 'SL' },
    { price: position.tp1, color: '#10b981', title: 'TP1' },
    { price: position.tp2, color: '#10b981', title: 'TP2' },
  ];

  // R is the unit the rest of this app reasons in — plans are sized by it,
  // closed trades are scored by it — and open positions were the one place it
  // was missing, showing a bare % that says nothing about how the trade is
  // doing against its own risk. A +3% move is a different trade on a 1% stop
  // than on a 10% one.
  const currentPrice = analysis?.price;
  const sign = position.direction === 'long' ? 1 : -1;
  const riskPerShare = Math.abs(position.entry_price - position.stop_loss);

  const unrealizedPct =
    currentPrice !== undefined ? ((currentPrice - position.entry_price) / position.entry_price) * 100 * sign : null;
  const unrealizedDollars = currentPrice !== undefined ? (currentPrice - position.entry_price) * position.shares * sign : null;
  const currentR = currentPrice !== undefined && riskPerShare > 0 ? ((currentPrice - position.entry_price) * sign) / riskPerShare : null;
  // How much room is left before the stop, as a share of the current price.
  const roomToStopPct = currentPrice !== undefined && currentPrice > 0 ? ((currentPrice - position.stop_loss) * sign * 100) / currentPrice : null;
  // Time limit: which trading day this is, counted from the bars actually on the
  // chart (bars after the entry bar; today's still-forming bar counts as "today").
  // Only shown when the entry bar is inside the loaded window, so it is never a guess.
  const maxHoldingDays = appSettings?.max_holding_days ?? 0;
  const candles = analysis?.candles;
  const entryDate = entryBarDate(position.symbol, position.opened_at);
  const tradingDay =
    candles && candles.length > 0 && candles[0].date <= entryDate ? candles.filter((c) => c.date > entryDate).length : null;
  const slipped =
    position.planned_entry_price != null && Math.abs(position.planned_entry_price - position.entry_price) > 0.005;

  return (
    <div className="card" ref={ref}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 12, flexWrap: 'wrap', gap: 10 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
          <TickerLink symbol={position.symbol} iconSize={30} fontWeight={700} style={{ fontSize: 15 }} />
          <DirectionBadge direction={position.direction} />
          <span className="text-muted" style={{ fontSize: 12 }}>
            {position.shares} shares @ {formatMoney(position.entry_price)}
          </span>
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          {unrealizedPct !== null && unrealizedDollars !== null && (
            <div style={{ textAlign: 'right' }}>
              <div className={`tabular-nums ${unrealizedPct >= 0 ? 'text-green' : 'text-red'}`} style={{ fontWeight: 700, fontSize: 15 }}>
                <Flash value={unrealizedDollars} scope={String(position.id)}>
                  {formatMoney(unrealizedDollars)}
                </Flash>
                {currentR !== null && <span style={{ marginLeft: 8 }}>{formatR(currentR)}</span>}
              </div>
              <div className="text-muted tabular-nums" style={{ fontSize: 11 }}>
                {unrealizedPct >= 0 ? '+' : ''}
                {unrealizedPct.toFixed(2)}% unrealized
              </div>
              {currentPrice !== undefined && (
                <div className="text-muted tabular-nums" style={{ fontSize: 11 }}>
                  last{' '}
                  <Flash value={currentPrice} scope={String(position.id)}>
                    {formatMoney(currentPrice)}
                  </Flash>{' '}
                  · <DataFreshness updatedAt={priceUpdatedAt} compact />
                </div>
              )}
            </div>
          )}
          <button className="btn btn-secondary" disabled={closing} onClick={() => closePosition(position.id)}>
            Close
          </button>
        </div>
      </div>
      {isLoading || !analysis ? (
        <LoadingSpinner label={`Loading ${position.symbol} chart…`} />
      ) : (
        <CandlestickChart candles={analysis.candles} levels={levels} height={260} />
      )}
      <div className="grid position-detail-grid" style={{ gap: 12, marginTop: 12 }}>
        <div>
          <div className="text-muted" style={{ fontSize: 11 }}>
            Entry
          </div>
          <div className="tabular-nums">{formatMoney(position.entry_price)}</div>
          {slipped && (
            <div className="text-muted tabular-nums" style={{ fontSize: 10 }}>
              planned {formatMoney(position.planned_entry_price)}
            </div>
          )}
        </div>
        <div>
          <div className="text-muted" style={{ fontSize: 11 }}>
            Stop Loss
          </div>
          <div className="tabular-nums text-red">{formatMoney(position.stop_loss)}</div>
        </div>
        <div>
          {/* The number that actually matters minute to minute: how far the
              trade can move before the thesis is done. Absent entirely
              before — you had to work it out from entry and stop by hand. */}
          <div className="text-muted" style={{ fontSize: 11 }}>
            Room to Stop
          </div>
          <div className={`tabular-nums ${roomToStopPct !== null && roomToStopPct < 0 ? 'text-red' : ''}`}>
            {roomToStopPct !== null ? `${roomToStopPct.toFixed(1)}%` : '—'}
          </div>
        </div>
        <div>
          <div className="text-muted" style={{ fontSize: 11 }}>
            Risk
          </div>
          <div className="tabular-nums">{formatMoney(riskPerShare * position.shares)}</div>
        </div>
        <div>
          <div className="text-muted" style={{ fontSize: 11 }}>
            TP1 / TP2
          </div>
          <div className="tabular-nums text-green">
            {formatMoney(position.tp1)} / {formatMoney(position.tp2)}
          </div>
        </div>
        <div>
          <div className="text-muted" style={{ fontSize: 11 }}>
            Opened
          </div>
          <div className="tabular-nums">{new Date(position.opened_at).toLocaleDateString()}</div>
          <div className="text-muted" style={{ fontSize: 10 }}>
            {formatRelativeTime(position.opened_at)}
          </div>
          {maxHoldingDays > 0 && (
            <div className="text-muted tabular-nums" style={{ fontSize: 10 }}>
              {tradingDay !== null
                ? `day ${tradingDay} of ${maxHoldingDays}`
                : `closes after ${maxHoldingDays} trading days`}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

export function PortfolioPage() {
  const { data: stats, isLoading: statsLoading, dataUpdatedAt: statsUpdatedAt } = usePortfolioStats();
  const { data: equity } = useEquityCurve();
  const { data: positions, isLoading: positionsLoading, dataUpdatedAt: positionsUpdatedAt } = usePositions();
  const { mutate: resetPortfolio, isPending: resetting, error: resetError } = useResetPortfolio();
  // lesson column: which closed trade's lesson is open, and whether an AI provider can write one
  const [openLessonId, setOpenLessonId] = useState<number | null>(null);
  const lessonAiOnline = useSettingsStatus().data?.ai_online ?? false;

  function handleReset() {
    if (window.confirm('Reset the paper account? This closes out all positions and equity history and starts fresh at the configured starting cash. This cannot be undone.')) {
      resetPortfolio();
    }
  }

  const openPositions = positions?.filter((p) => p.status === 'open') ?? [];
  const closedPositions = positions?.filter((p) => p.status === 'closed') ?? [];
  const exitMix = Object.entries(stats?.exit_reasons ?? {}).sort((a, b) => b[1] - a[1]);
  const exitMixTotal = exitMix.reduce((sum, [, n]) => sum + n, 0);

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <div>
          <h1 style={{ fontSize: 22 }}>Portfolio</h1>
          {/* The older of the two fetches the header cards are built from. */}
          <div style={{ marginTop: 4 }}>
            <DataFreshness updatedAt={statsUpdatedAt && positionsUpdatedAt ? Math.min(statsUpdatedAt, positionsUpdatedAt) : statsUpdatedAt || positionsUpdatedAt} />
          </div>
        </div>
        <button className="btn btn-secondary" onClick={handleReset} disabled={resetting}>
          {resetting ? 'Resetting…' : 'Reset Paper Account'}
        </button>
      </div>
      {resetError && <ErrorBanner message={(resetError as ApiError).message} />}

      {statsLoading && <LoadingSpinner label="Loading portfolio…" />}
      {stats && (
        <div className="grid stat-grid">
          <StatCard label="Portfolio Value" value={formatMoney(stats.portfolio_value)} note={`${formatMoney(stats.current_cash)} cash`} />
          <StatCard
            label="Total Return"
            value={formatPct(stats.total_return)}
            positive={stats.total_return > 0 ? true : stats.total_return < 0 ? false : null}
          />
          <StatCard label="Win Rate" value={`${formatNumber(stats.win_rate, 0)}%`} note={sampleSizeNote(stats.total_trades)} />
          {/* Was "Avg R:R" rendered as `${value}:1` — which turned a losing
              average into "-0.42:1", not a ratio and not a thing. This field
              is mean realized R, i.e. expectancy per trade. */}
          <StatCard
            label="Avg R"
            value={formatR(stats.avg_rr)}
            positive={stats.avg_rr === null ? null : stats.avg_rr > 0}
            note="expectancy per closed trade"
          />
        </div>
      )}

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>Equity Curve</h3>
        {!equity || equity.length === 0 ? <EmptyState>No equity history yet — open a paper position to start tracking.</EmptyState> : <EquityCurveChart points={equity} />}
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
        <h3>Active Positions</h3>
        {positionsLoading && <LoadingSpinner />}
        {positions && openPositions.length === 0 && (
          <div className="card">
            <EmptyState>No open positions — execute a trade plan to open one.</EmptyState>
          </div>
        )}
        {openPositions.map((p) => (
          <ActivePositionCard key={p.id} position={p} />
        ))}
      </div>

      {stats && <TradeExcursionsCard excursions={stats.excursions} />}

      <div className="card">
        <h3 style={{ marginBottom: 12 }}>Closed Positions</h3>
        {closedPositions.length === 0 ? (
          <EmptyState>No closed trades yet.</EmptyState>
        ) : (
          <>
          {/* How trades ended, counted from the closed rows themselves. A large
              share of time-limit exits means setups mostly stall instead of
              resolving, which win rate alone hides. */}
          {exitMixTotal > 0 && (
            <div className="text-muted" style={{ fontSize: 12, marginBottom: 12 }}>
              How trades ended:{' '}
              {exitMix.map(([reason, n]) => `${closeReasonLabel(reason)} ${n} (${Math.round((n / exitMixTotal) * 100)}%)`).join(' · ')}
              {' — '}
              {sampleSizeNote(exitMixTotal)}
            </div>
          )}
          <table>
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Direction</th>
                <th>Entry</th>
                <th>Shares</th>
                <th>Close Price</th>
                <th>Reason</th>
                <th>P&L</th>
                <th>R</th>
                <th title="Best price reached during the trade, in R (MFE)">MFE</th>
                <th title="Worst price reached during the trade, in R (MAE)">MAE</th>
                <th title="An AI-written note on how the trade went, compared with SPY">Lesson</th>
              </tr>
            </thead>
            <tbody>
              {closedPositions.map((p) => (
                <Fragment key={p.id}>
                <tr>
                  <td style={{ fontWeight: 600 }}>
                    <TickerLink symbol={p.symbol} iconSize={24} />
                  </td>
                  <td>
                    <DirectionBadge direction={p.direction} />
                  </td>
                  <td className="tabular-nums">{formatMoney(p.entry_price)}</td>
                  <td className="tabular-nums">{p.shares}</td>
                  <td className="tabular-nums">{formatMoney(p.close_price)}</td>
                  <td className="text-muted" title={exitResolutionHint(p)}>
                    {closeReasonLabel(p.close_reason)}
                  </td>
                  <td className={`tabular-nums ${p.realized_pnl && p.realized_pnl > 0 ? 'text-green' : p.realized_pnl && p.realized_pnl < 0 ? 'text-red' : ''}`}>
                    {p.realized_pnl !== null ? formatMoney(p.realized_pnl) : '—'}
                  </td>
                  <td className={`tabular-nums ${p.realized_r && p.realized_r > 0 ? 'text-green' : p.realized_r && p.realized_r < 0 ? 'text-red' : ''}`}>
                    {formatR(p.realized_r)}
                  </td>
                  <td className="tabular-nums text-muted">{formatR(p.mfe_r)}</td>
                  <td className="tabular-nums text-muted">{formatAdverseR(p.mae_r)}</td>
                  <td>
                    <LessonCell position={p} open={openLessonId === p.id} onToggle={() => setOpenLessonId(openLessonId === p.id ? null : p.id)} aiOnline={lessonAiOnline} />
                  </td>
                </tr>
                {openLessonId === p.id && p.lesson_text && <LessonDetailRow position={p} colSpan={11} aiOnline={lessonAiOnline} />}
                </Fragment>
              ))}
            </tbody>
          </table>
          </>
        )}
      </div>

      <CalibrationCard />

      <PriceAlertsCard />
    </div>
  );
}
