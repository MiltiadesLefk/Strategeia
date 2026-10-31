import { Fragment, useState } from 'react';
import './portfolio.css';
import { getDisplayTimeZone } from '../lib/timezone';
import { useAnalysis, useEquityCurve, useClosePosition, useResetPortfolio, usePortfolioStats, usePositions, useRebuyWatch, useSettingsStatus } from '../api/hooks';
import { PauseHistoryCard, SleeveSwitcher, SleevesOverview, useSelectedSleeve } from '../components/SleeveSwitcher';
import { LessonCell, LessonDetailRow } from '../components/TradeLesson';
import { ThesisBrokenBadge, ThesisPanel } from '../components/ThesisPanel';
import { TickerLink } from '../components/TickerLink';
import { TradeExcursionsCard } from '../components/TradeExcursionsCard';
import { HoldingsCard } from '../components/HoldingsCard';
import { CalibrationCard } from '../components/CalibrationCard';
import { PriceAlertsCard } from '../components/PriceAlertsPanel';
import { EquityCurveChart } from '../components/chart/EquityCurveChart';
import { CandlestickChart, type PriceLevel } from '../components/chart/CandlestickChart';
import { ErrorBanner, EmptyState, LoadingSpinner, formatMoney, formatNumber, formatPct, sampleSizeNote } from '../components/common';
import type { ApiError } from '../api/client';
import type { Position } from '../api/types';
import { useInView } from '../lib/useInView';
import { DataFreshness } from '../components/DataFreshness';
import { Flash } from '../components/Flash';

// How a closed trade ended, in the words the rest of the app uses. The backend
// stores the machine value (close_reason); an unknown one is shown as-is.
const CLOSE_REASON_LABELS: Record<string, string> = {
  stop_hit: 'Stop',
  sell_plan: 'Sell plan',
  disaster_stop: 'Disaster stop',
  manual: 'Manual',
};
function RebuyWatchList() {
  const { data } = useRebuyWatch();
  if (!data?.length) return null;
  return (
    <div className="card">
      <div className="panel-header"><span>Rebuy watch</span></div>
      <ul>
        {data.map((r) => (
          <li key={r.id}>
            {r.symbol}: sold {r.sold_at.slice(0, 10)} at {formatMoney(r.sold_price)} ({r.sold_reason}), {r.status}
            {r.status === 'watching' ? `, can be bought again from ${r.eligible_from.slice(0, 10)} if it passes the gate and dips` : ''}
          </li>
        ))}
      </ul>
    </div>
  );
}

function closeReasonLabel(reason: string | null): string {
  return reason ? (CLOSE_REASON_LABELS[reason] ?? reason) : '—';
}

// Hover text for how an exit was placed in time (PaperPosition.exit_resolution).
const EXIT_RESOLUTION_HINTS: Record<string, string> = {
  daily: 'Found on the daily bar.',
  live: 'Closed at the live price while the market was open.',
};
function exitResolutionHint(p: Position): string | undefined {
  return p.exit_resolution ? (EXIT_RESOLUTION_HINTS[p.exit_resolution] ?? p.exit_resolution) : undefined;
}

function daysHeld(from: string, to?: string | null): number {
  const end = to ? new Date(to).getTime() : Date.now();
  return Math.max(0, Math.round((end - new Date(from).getTime()) / 86_400_000));
}
const shortDate = (iso: string) => new Date(iso).toLocaleDateString(undefined, { timeZone: getDisplayTimeZone(), month: '2-digit', day: '2-digit', year: '2-digit' });
const pnlClass = (v: number | null | undefined) => (v ? (v > 0 ? 'text-green' : 'text-red') : '');

// One open position: a ledger row, with the chart and thesis opened underneath on demand.
function OpenPositionRows({ position }: { position: Position }) {
  const { ref, inView } = useInView<HTMLTableRowElement>();
  const [expanded, setExpanded] = useState(false);
  const { data: analysis, isLoading, dataUpdatedAt } = useAnalysis(position.symbol, '3mo', inView);
  const { mutate: closePosition, isPending: closing } = useClosePosition();
  const levels: PriceLevel[] = [
    { price: position.entry_price, color: '#0ea5e9', title: 'Entry' },
    { price: position.stop_loss, color: '#ef4444', title: 'Stop' },
  ].filter((l) => l.price > 0);
  const price = analysis?.price;
  const unrealizedPct = price !== undefined ? ((price - position.entry_price) / position.entry_price) * 100 : null;
  const unrealized = price !== undefined ? (price - position.entry_price) * position.shares : null;
  return (
    <>
      <tr ref={ref}>
        <td>
          <TickerLink symbol={position.symbol} iconSize={24} fontWeight={700} /> <ThesisBrokenBadge positionId={position.id} />
        </td>
        <td className="mono">
          {formatMoney(position.entry_price)}
          <div className="label-mono">{position.shares} sh</div>
        </td>
        <td className="mono">
          {price !== undefined ? <Flash value={price} scope={String(position.id)}>{formatMoney(price)}</Flash> : '—'}
          <div className="label-mono"><DataFreshness updatedAt={dataUpdatedAt} compact /></div>
        </td>
        <td className="mono text-red">{position.stop_loss > 0 ? formatMoney(position.stop_loss) : 'alert only'}</td>
        <td className="mono">
          {unrealizedPct !== null && unrealized !== null ? (
            <>
              <span className={unrealizedPct >= 0 ? 'delta-up' : 'delta-down'}>{formatPct(unrealizedPct, 2)}</span>
              <div className={`label-mono ${pnlClass(unrealized)}`}>
                <Flash value={unrealized} scope={String(position.id)}>{formatMoney(unrealized)}</Flash>
              </div>
            </>
          ) : (
            '—'
          )}
        </td>
        <td className="mono">
          {daysHeld(position.opened_at)}d<div className="label-mono">{shortDate(position.opened_at)}</div>
        </td>
        <td className="pf-actions">
          <button className="btn btn-secondary" onClick={() => setExpanded(!expanded)}>{expanded ? 'Hide' : 'Chart'}</button>{' '}
          <button className="btn btn-secondary" disabled={closing} onClick={() => closePosition(position.id)}>Close</button>
        </td>
      </tr>
      {expanded && (
        <tr>
          <td colSpan={7} className="pf-expand">
            {isLoading || !analysis ? <LoadingSpinner label={`Loading ${position.symbol} chart…`} /> : <CandlestickChart candles={analysis.candles} levels={levels} height={220} />}
            <ThesisPanel positionId={position.id} />
          </td>
        </tr>
      )}
    </>
  );
}

export function PortfolioPage() {
  const [sleeve, selectSleeve] = useSelectedSleeve();
  const { data: stats, isLoading: statsLoading, dataUpdatedAt: statsUpdatedAt } = usePortfolioStats(sleeve);
  const { data: equity } = useEquityCurve(sleeve);
  const { data: positions, isLoading: positionsLoading, dataUpdatedAt: positionsUpdatedAt } = usePositions(sleeve);
  const { mutate: resetPortfolio, isPending: resetting, error: resetError } = useResetPortfolio(sleeve);
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
    <div className="pf-page">
      <div className="page-header">
        <div>
          <h1 className="page-title">Portfolio</h1>
          <div style={{ marginTop: 4 }}>
            <DataFreshness updatedAt={statsUpdatedAt && positionsUpdatedAt ? Math.min(statsUpdatedAt, positionsUpdatedAt) : statsUpdatedAt || positionsUpdatedAt} />
          </div>
        </div>
        <button className="btn btn-secondary" onClick={handleReset} disabled={resetting}>
          {resetting ? 'Resetting…' : sleeve === 'core' ? 'Reset Paper Account' : 'Reset this sleeve'}
        </button>
      </div>
      <SleeveSwitcher selected={sleeve} onSelect={selectSleeve} />
      <SleevesOverview selected={sleeve} onSelect={selectSleeve} />
      <PauseHistoryCard />
      {resetError && <ErrorBanner message={(resetError as ApiError).message} />}

      {statsLoading && <LoadingSpinner label="Loading portfolio…" />}
      {stats && (
        <div className="pf-tiles">
          <div className="stat-tile">
            <div className="stat-tile-label">Portfolio value</div>
            <div className="stat-tile-value">{formatMoney(stats.portfolio_value)}</div>
            <div className="stat-tile-sub">{formatMoney(stats.current_cash)} cash</div>
          </div>
          <div className="stat-tile">
            <div className="stat-tile-label">Total return</div>
            <div className={`stat-tile-value ${pnlClass(stats.total_return)}`}>{formatPct(stats.total_return)}</div>
            <div className="stat-tile-sub">from {formatMoney(stats.starting_cash)}</div>
          </div>
          <div className="stat-tile">
            <div className="stat-tile-label">Win rate</div>
            <div className="stat-tile-value">{formatNumber(stats.win_rate, 0)}%</div>
            <div className="stat-tile-sub">{sampleSizeNote(stats.total_trades)}</div>
          </div>
          <div className="stat-tile">
            <div className="stat-tile-label">Open positions</div>
            <div className="stat-tile-value">{stats.active_positions}</div>
            <div className="stat-tile-sub">{stats.total_trades} closed</div>
          </div>
        </div>
      )}

      <div className="card">
        <div className="panel-header"><span>Equity curve</span></div>
        {!equity || equity.length === 0 ? <EmptyState>No equity history yet — open a paper position to start tracking.</EmptyState> : <EquityCurveChart points={equity} />}
      </div>

      <div className="card card-flush">
        <div className="panel-header pf-pad"><span>Open positions</span><span className="label-mono">{openPositions.length} held</span></div>
        {positionsLoading && <LoadingSpinner />}
        {positions && openPositions.length === 0 && (
          <div className="pf-pad"><EmptyState>No open positions yet. The hold scan buys when a stock passes the quality gate.</EmptyState></div>
        )}
        {openPositions.length > 0 && (
          <div className="pf-scroll">
            <table className="pf-table">
              <thead>
                <tr><th>Ticker</th><th>Entry</th><th>Current</th><th>Disaster stop</th><th>Unrealized</th><th>Held</th><th /></tr>
              </thead>
              <tbody>{openPositions.map((p) => <OpenPositionRows key={p.id} position={p} />)}</tbody>
            </table>
          </div>
        )}
      </div>

      <HoldingsCard sleeve={sleeve} />
      <RebuyWatchList />
      {stats && <TradeExcursionsCard excursions={stats.excursions} />}

      <div className="card card-flush">
        <div className="panel-header pf-pad"><span>Closed trades</span><span className="label-mono">{closedPositions.length}</span></div>
        {closedPositions.length === 0 ? (
          <div className="pf-pad"><EmptyState>No closed trades yet.</EmptyState></div>
        ) : (
          <>
            {exitMixTotal > 0 && (
              <div className="text-muted pf-pad" style={{ fontSize: 12, marginBottom: 8 }}>
                How trades ended: {exitMix.map(([reason, n]) => `${closeReasonLabel(reason)} ${n} (${Math.round((n / exitMixTotal) * 100)}%)`).join(' · ')}
                {' — '}
                {sampleSizeNote(exitMixTotal)}
              </div>
            )}
            <div className="pf-scroll">
              <table className="pf-table">
                <thead>
                  <tr>
                    <th>Ticker</th>
                    <th>Dates</th>
                    <th>Exit reason</th>
                    <th>Return</th>
                    <th>P&amp;L</th>
                    <th>Held</th>
                    <th title="Best / worst price reached during the trade, as % of entry">MFE / MAE</th>
                    <th title="An AI-written note on how the trade went, compared with SPY">AI note</th>
                  </tr>
                </thead>
                <tbody>
                  {closedPositions.map((p) => {
                    const ret = p.close_price != null ? ((p.close_price - p.entry_price) / p.entry_price) * 100 : null;
                    return (
                      <Fragment key={p.id}>
                        <tr>
                          <td><TickerLink symbol={p.symbol} iconSize={24} fontWeight={700} /></td>
                          <td className="mono">
                            {shortDate(p.opened_at)}
                            <div className="label-mono">{p.closed_at ? `to ${shortDate(p.closed_at)}` : ''}</div>
                          </td>
                          <td title={exitResolutionHint(p)}><span className="chip">{closeReasonLabel(p.close_reason)}</span></td>
                          <td className="mono">{ret !== null ? <span className={ret >= 0 ? 'delta-up' : 'delta-down'}>{formatPct(ret, 2)}</span> : '—'}</td>
                          <td className={`mono ${pnlClass(p.realized_pnl)}`}>{p.realized_pnl !== null ? formatMoney(p.realized_pnl) : '—'}</td>
                          <td className="mono">{p.closed_at ? `${daysHeld(p.opened_at, p.closed_at)}d` : '—'}</td>
                          <td className="mono text-muted">
                            {p.mfe_pct != null ? `+${p.mfe_pct.toFixed(1)}%` : '—'} / {p.mae_pct != null ? `-${p.mae_pct.toFixed(1)}%` : '—'}
                          </td>
                          <td>
                            <LessonCell position={p} open={openLessonId === p.id} onToggle={() => setOpenLessonId(openLessonId === p.id ? null : p.id)} aiOnline={lessonAiOnline} />
                          </td>
                        </tr>
                        {openLessonId === p.id && p.lesson_text && <LessonDetailRow position={p} colSpan={8} aiOnline={lessonAiOnline} />}
                      </Fragment>
                    );
                  })}
                </tbody>
              </table>
            </div>
          </>
        )}
      </div>

      <div className="text-muted" style={{ fontSize: 12 }}>
        Calibration below, and the missed-trades and replay studies, cover the core sleeve only.
      </div>
      <CalibrationCard />

      <PriceAlertsCard />
    </div>
  );
}
