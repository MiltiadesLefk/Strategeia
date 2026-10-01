import { useMemo, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import {
  useBacktest,
  useBacktestBaseline,
  useBacktestBenchmarks,
  useBacktestEquity,
  useBacktestHistoryCoverage,
  useBacktestMetrics,
  useBacktestScorecard,
  useBacktestTrades,
  useBacktests,
  useCancelBacktest,
  useSettings,
  useStartBacktest,
  useWatchlist,
} from '../api/hooks';
import type { ApiError } from '../api/client';
import type {
  BacktestBaseline,
  BacktestMetrics,
  BacktestPeriodReturn,
  BacktestRun,
  BacktestScorecard,
  BacktestTrade,
} from '../api/types';
import { StatCard } from '../components/StatCard';
import { DirectionBadge } from '../components/Badge';
import { TickerLink } from '../components/TickerLink';
import { BaselineHistogram, DrawdownChart, EquityVsBenchmarkChart } from '../components/backtest/BacktestCharts';
import { EmptyState, ErrorBanner, LoadingSpinner, formatMoney, formatNumber, formatPct, formatR } from '../components/common';
import { ValidationTab } from '../components/backtest/ValidationTab';

const MACHINERY_CHECK_SYMBOLS = ['NVDA', 'AAPL'];
const DEFAULT_RUN_YEARS = 3;
const DEFAULT_BASELINE_RUNS = 20;
const MAX_BASELINE_RUNS = 50;
const MAX_DECISION_EVERY = 60;
const MIN_FULL_RUN_DAYS = 365; // the strategy needs about a year of bars before it evaluates a symbol

// The overrides a run may change, with the live setting each one starts from.
const OVERRIDE_FIELDS: { key: string; label: string; step: string; fallback: number }[] = [
  { key: 'slippage_bps', label: 'Slippage (bps)', step: '0.5', fallback: 5 },
  { key: 'commission_per_trade', label: 'Commission per trade ($)', step: '0.01', fallback: 0 },
  { key: 'default_risk_pct', label: 'Risk per trade (%)', step: '0.1', fallback: 1 },
  { key: 'min_confidence_for_trade', label: 'Minimum confidence (%)', step: '1', fallback: 30 },
  { key: 'max_concurrent_positions', label: 'Max open positions', step: '1', fallback: 5 },
  { key: 'max_holding_days', label: 'Max holding days (0 = none)', step: '1', fallback: 0 },
];

const CLOSE_REASON_LABELS: Record<string, string> = {
  stop_hit: 'Stop',
  tp1_hit: 'Target (TP1)',
  time_exit: 'Time limit',
  open_at_end: 'Still open at the end',
};
const reasonLabel = (reason: string) => CLOSE_REASON_LABELS[reason] ?? reason;

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

const returnColor = (v: number | null | undefined) => (v === null || v === undefined ? undefined : v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : undefined);
const signed = (v: number | null | undefined, digits = 2) => (v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(digits)}`);
const plain = (v: number | null | undefined, digits = 2) => formatNumber(v, digits);

function statusBadge(run: BacktestRun) {
  const cls = run.status === 'done' ? 'badge-green' : run.status === 'failed' ? 'badge-red' : run.status === 'cancelled' ? 'badge-neutral' : 'badge-amber';
  return <span className={`badge ${cls}`}>{run.status}</span>;
}

function addYears(iso: string, years: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCFullYear(d.getUTCFullYear() + years);
  return d.toISOString().slice(0, 10);
}

// ---------------------------------------------------------------- new run

function NewRunForm({ onStarted }: { onStarted: (id: number) => void }) {
  const settings = useSettings();
  const watchlist = useWatchlist();
  const start = useStartBacktest();
  const [symbols, setSymbols] = useState<string[]>(MACHINERY_CHECK_SYMBOLS);
  const [draft, setDraft] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [every, setEvery] = useState(1);
  const [overrides, setOverrides] = useState<Record<string, string>>({});
  const [includeFundamentals, setIncludeFundamentals] = useState(false);
  const [includeInsiders, setIncludeInsiders] = useState(false);
  const [includeEarnings, setIncludeEarnings] = useState(false);
  const [baselineOn, setBaselineOn] = useState(true);
  const [baselineRuns, setBaselineRuns] = useState(DEFAULT_BASELINE_RUNS);
  const coverage = useBacktestHistoryCoverage(symbols);

  const live = (settings.data ?? {}) as unknown as Record<string, number | undefined>;
  const watchSymbols = (watchlist.data?.entries ?? []).map((e) => e.symbol);

  const spy = coverage.data?.benchmarks.find((b) => b.symbol === 'SPY');
  const minStart = spy?.first_date ?? undefined;
  const maxEnd = coverage.data?.latest_end_date ?? undefined;
  // Defaults follow what is stored: the last three years that end where history ends.
  const effectiveEnd = endDate || maxEnd || '';
  const defaultStart = effectiveEnd ? addYears(effectiveEnd, -DEFAULT_RUN_YEARS) : '';
  const effectiveStart = startDate || (minStart && defaultStart < minStart ? minStart : defaultStart);
  const spanDays = effectiveStart && effectiveEnd ? (Date.parse(effectiveEnd) - Date.parse(effectiveStart)) / 86_400_000 : 0;

  const historyMissing = Boolean(coverage.data && (coverage.data.missing.length > 0 || coverage.data.benchmarks_missing.length > 0));
  const datesOk = Boolean(effectiveStart && effectiveEnd && effectiveEnd > effectiveStart);
  const canStart = symbols.length > 0 && datesOk && !historyMissing && !start.isPending;

  function addSymbols(raw: string) {
    const parsed = raw
      .split(/[\s,;]+/)
      .map((s) => s.trim().toUpperCase())
      .filter(Boolean);
    setSymbols((prev) => Array.from(new Set([...prev, ...parsed])));
    setDraft('');
  }

  function submit() {
    const body: Record<string, number> = {};
    for (const field of OVERRIDE_FIELDS) {
      const raw = overrides[field.key];
      if (raw !== undefined && raw !== '' && !Number.isNaN(Number(raw))) body[field.key] = Number(raw);
    }
    start.mutate(
      {
        symbols,
        start: effectiveStart,
        end: effectiveEnd,
        decision_every_n_days: every,
        overrides: body,
        run_baseline: baselineOn && baselineRuns > 0,
        baseline_runs: baselineOn ? baselineRuns : 0,
        include_fundamentals: includeFundamentals,
        include_insiders: includeInsiders,
        include_earnings: includeEarnings,
      },
      { onSuccess: (r) => onStarted(r.id) },
    );
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 18 }}>New run</h2>
        <div className="text-muted" style={{ fontSize: 13, marginTop: 4 }}>
          Replays the live scoring and the paper engine over past days using only price history already stored on this machine. Nothing is downloaded when a run starts.
        </div>
      </div>

      <div>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Symbols ({symbols.length})</div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
          <button className="btn btn-secondary" onClick={() => setSymbols(MACHINERY_CHECK_SYMBOLS)}>
            NVDA + AAPL (machinery check)
          </button>
          <button className="btn btn-secondary" disabled={watchSymbols.length === 0} onClick={() => setSymbols(watchSymbols)}>
            Current watchlist{watchSymbols.length ? ` (${watchSymbols.length})` : ''}
          </button>
          <button className="btn btn-secondary" disabled={symbols.length === 0} onClick={() => setSymbols([])}>
            Clear
          </button>
        </div>
        <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', alignItems: 'center' }}>
          <input
            aria-label="Add symbols"
            placeholder="Add symbols, e.g. MSFT, AMZN"
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') addSymbols(draft);
            }}
            style={{ minWidth: 220 }}
          />
          <button className="btn btn-secondary" disabled={!draft.trim()} onClick={() => addSymbols(draft)}>
            Add
          </button>
        </div>
        {symbols.length > 0 && (
          <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8, maxHeight: 120, overflowY: 'auto' }}>
            {symbols.map((s) => (
              <button
                key={s}
                className="badge badge-neutral"
                style={{ cursor: 'pointer', border: 'none' }}
                title="Remove"
                onClick={() => setSymbols((prev) => prev.filter((x) => x !== s))}
              >
                {s} ×
              </button>
            ))}
          </div>
        )}
      </div>

      {coverage.isLoading && <LoadingSpinner label="Checking stored history…" />}
      {coverage.isError && <ErrorBanner message={(coverage.error as ApiError).message} onRetry={() => coverage.refetch()} />}
      {historyMissing && coverage.data && (
        <div className="card" style={{ background: 'var(--card-alt)', borderColor: 'var(--amber)' }} role="alert">
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Price history is missing, so this run cannot start yet</div>
          <div style={{ fontSize: 13 }}>
            Stored history is missing for: <b>{[...coverage.data.benchmarks_missing, ...coverage.data.missing].join(', ')}</b>. The backtester never downloads prices
            when a run starts. Fill the store once from a terminal in the repo folder, then come back:
          </div>
          <code style={{ display: 'block', marginTop: 8, padding: 8, overflowX: 'auto', whiteSpace: 'pre-wrap', userSelect: 'all' }}>
            {coverage.data.preload_command}
          </code>
        </div>
      )}
      {coverage.data && !historyMissing && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          History stored for every symbol. SPY covers {spy?.first_date ?? '?'} to {spy?.last_date ?? '?'}. {coverage.data.warmup_note}
        </div>
      )}

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          Start
          <input type="date" value={effectiveStart} min={minStart} max={effectiveEnd || maxEnd} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          End
          <input type="date" value={effectiveEnd} min={minStart} max={maxEnd} onChange={(e) => setEndDate(e.target.value)} />
        </label>
        <label style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
          Decide every N trading days
          <input type="number" min={1} max={MAX_DECISION_EVERY} value={every} onChange={(e) => setEvery(Math.max(1, Math.min(MAX_DECISION_EVERY, Number(e.target.value) || 1)))} style={{ width: 120 }} />
        </label>
      </div>
      {datesOk && spanDays < MIN_FULL_RUN_DAYS && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          This span is under a year. Each symbol needs about a year of bars before it is evaluated, which the stored history before the start date supplies, but short runs make very few trades.
        </div>
      )}

      <div>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Overrides (left blank, a run uses your live setting shown as the placeholder)</div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))', gap: 12 }}>
          {OVERRIDE_FIELDS.map((f) => (
            <label key={f.key} style={{ display: 'flex', flexDirection: 'column', gap: 4, fontSize: 13 }}>
              {f.label}
              <input
                type="number"
                step={f.step}
                min={0}
                placeholder={String(live[f.key] ?? f.fallback)}
                value={overrides[f.key] ?? ''}
                onChange={(e) => setOverrides((prev) => ({ ...prev, [f.key]: e.target.value }))}
              />
            </label>
          ))}
        </div>
      </div>

      <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
        <div style={{ fontWeight: 600, fontSize: 13 }}>Dated data to score (off = prices only)</div>
        <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap', fontSize: 14 }}>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="checkbox" checked={includeFundamentals} onChange={(e) => setIncludeFundamentals(e.target.checked)} />
            Fundamentals (revenue by filing date, 52-week range)
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="checkbox" checked={includeInsiders} onChange={(e) => setIncludeInsiders(e.target.checked)} />
            Insider buying
          </label>
          <label style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            <input type="checkbox" checked={includeEarnings} onChange={(e) => setIncludeEarnings(e.target.checked)} />
            Earnings surprise record
          </label>
        </div>
        {(includeFundamentals || includeInsiders || includeEarnings) && (
          <span className="text-muted" style={{ fontSize: 12 }}>
            These read data stored beforehand by scripts/backfill_fundamentals.py, backfill_insider_trades.py and backfill_earnings.py. A symbol
            with nothing stored scores 0 for that part; the run's summary says how much data was found.
          </span>
        )}
      </div>
      <div style={{ display: 'flex', gap: 16, alignItems: 'center', flexWrap: 'wrap' }}>
        <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 14 }}>
          <input type="checkbox" checked={baselineOn} onChange={(e) => setBaselineOn(e.target.checked)} />
          Also run the random-entry baseline
        </label>
        {baselineOn && (
          <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
            random runs (K)
            <input
              type="number"
              min={1}
              max={MAX_BASELINE_RUNS}
              value={baselineRuns}
              onChange={(e) => setBaselineRuns(Math.max(1, Math.min(MAX_BASELINE_RUNS, Number(e.target.value) || 1)))}
              style={{ width: 80 }}
            />
          </label>
        )}
        {baselineOn && <span className="text-muted" style={{ fontSize: 12 }}>Each random run is a full replay, so K runs take about K times as long as the main run.</span>}
      </div>

      {start.isError && <ErrorBanner message={(start.error as ApiError).message} />}
      <div>
        <button className="btn" disabled={!canStart} onClick={submit}>
          {start.isPending ? 'Starting…' : 'Start backtest'}
        </button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- list

function RunsList({ selected, onSelect }: { selected: number | null; onSelect: (id: number) => void }) {
  const runs = useBacktests();
  if (runs.isLoading) return <LoadingSpinner label="Loading runs…" />;
  if (runs.isError) return <ErrorBanner message={(runs.error as ApiError).message} onRetry={() => runs.refetch()} />;
  if (!runs.data || runs.data.length === 0) return <EmptyState>No backtests yet. Start one above.</EmptyState>;
  return (
    <div className="card" style={{ overflowX: 'auto' }}>
      <h2 style={{ margin: '0 0 8px', fontSize: 18 }}>Runs</h2>
      <table>
        <thead>
          <tr>
            <th>#</th>
            <th>Status</th>
            <th>Symbols</th>
            <th>Period</th>
            <th style={{ textAlign: 'right' }}>Return</th>
            <th style={{ textAlign: 'right' }}>Trades</th>
          </tr>
        </thead>
        <tbody>
          {runs.data.map((r) => {
            const symbols = r.params.symbols ?? [];
            const ret = typeof r.summary?.total_return_pct === 'number' ? r.summary.total_return_pct : null;
            const trades = typeof r.summary?.trade_count === 'number' ? r.summary.trade_count : null;
            return (
              <tr key={r.id} onClick={() => onSelect(r.id)} style={{ cursor: 'pointer', background: r.id === selected ? 'var(--card-alt)' : undefined }}>
                <td>
                  <button className="btn btn-secondary" onClick={() => onSelect(r.id)} aria-label={`Open run ${r.id}`}>
                    {r.id}
                  </button>
                </td>
                <td>{statusBadge(r)}</td>
                <td>{symbols.length <= 4 ? symbols.join(', ') : `${symbols.slice(0, 3).join(', ')} +${symbols.length - 3} more`}</td>
                <td className="tabular-nums">
                  {r.params.start ?? '—'} to {r.params.end ?? '—'}
                </td>
                <td className="tabular-nums" style={{ textAlign: 'right', color: returnColor(ret) }}>
                  {formatPct(ret)}
                </td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>
                  {trades ?? '—'}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

// ---------------------------------------------------------------- run detail

function ProgressPanel({ run }: { run: BacktestRun }) {
  const cancel = useCancelBacktest();
  const { progress } = run;
  const total = progress.days_total || 0;
  const pct = total > 0 ? Math.min(100, (progress.days_done / total) * 100) : 0;
  const baseline = progress.phase === 'baseline';
  const k = progress.baseline_seeds_total ?? 0;
  const seed = Math.min(k, (progress.baseline_seeds_done ?? 0) + 1);
  const label =
    run.status === 'queued'
      ? 'Queued'
      : baseline
        ? `Baseline seed ${seed}/${k}: day ${progress.days_done} of ${total}`
        : `Main run: day ${progress.days_done} of ${total}${progress.current_date ? ` (${progress.current_date})` : ''}`;
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ fontWeight: 600 }}>{label}</div>
        <button className="btn btn-secondary" disabled={cancel.isPending || run.cancel_requested} onClick={() => cancel.mutate(run.id)}>
          {run.cancel_requested ? 'Cancelling…' : 'Cancel'}
        </button>
      </div>
      <div role="progressbar" aria-valuenow={Math.round(pct)} aria-valuemin={0} aria-valuemax={100} style={{ height: 10, background: 'var(--card-alt)', borderRadius: 5, overflow: 'hidden' }}>
        <div style={{ width: `${pct}%`, height: '100%', background: 'var(--green)', transition: 'width 0.3s' }} />
      </div>
      {baseline && <div className="text-muted" style={{ fontSize: 12 }}>The main run has finished and its results are shown below; the random runs are still going.</div>}
    </div>
  );
}

function Cards({ m }: { m: BacktestMetrics }) {
  const r = m.returns;
  const t = m.trades;
  const years = m.period.years;
  const sample = `${t.closed_trades} closed trade${t.closed_trades === 1 ? '' : 's'}`;
  const annualNote = m.period.annualised ? undefined : 'run too short to annualise';
  return (
    <div className="grid" style={{ gridTemplateColumns: 'repeat(auto-fill, minmax(190px, 1fr))' }}>
      <StatCard label="Total return" value={formatPct(r.total_return_pct)} positive={r.total_return_pct === null ? null : r.total_return_pct > 0} note={`${formatMoney(r.starting_equity)} to ${formatMoney(r.final_equity)}`} />
      <StatCard label="CAGR" value={formatPct(r.cagr_pct)} positive={r.cagr_pct === null ? null : r.cagr_pct > 0} note={annualNote ?? (years ? `over ${years.toFixed(2)} years` : undefined)} />
      <StatCard label="Max drawdown" value={`-${m.drawdown.max_drawdown_pct.toFixed(1)}%`} positive={m.drawdown.max_drawdown_pct === 0 ? null : false} note={`${m.drawdown.max_drawdown_days} days peak to trough, daily closes`} />
      <StatCard label="Sharpe" value={plain(r.sharpe)} note={annualNote ?? 'daily returns, risk-free 0, 252-day year'} />
      <StatCard label="Sortino" value={plain(r.sortino)} note={annualNote ?? 'penalises downside only'} />
      <StatCard label="Calmar" value={plain(r.calmar)} note="CAGR / max drawdown" />
      <StatCard label="Volatility" value={r.volatility_pct === null ? '—' : `${r.volatility_pct.toFixed(1)}%`} note="annualised, daily" />
      <StatCard label="Win rate" value={t.win_rate_pct === null ? '—' : `${t.win_rate_pct.toFixed(0)}%`} note={t.win_rate_low_pct === null ? sample : `95% range ${t.win_rate_low_pct.toFixed(0)}-${t.win_rate_high_pct?.toFixed(0)}%, ${sample}`} />
      <StatCard label="Average R" value={formatR(t.average_r)} positive={t.average_r === null ? null : t.average_r > 0} note={t.average_r_low === null ? sample : `95% range ${signed(t.average_r_low)} to ${signed(t.average_r_high)}`} />
      <StatCard label="Profit factor" value={t.profit_factor === null ? (t.closed_trades ? 'no losses' : '—') : plain(t.profit_factor)} note="gross profit / gross loss" />
      <StatCard label="Avg win / avg loss" value={`${formatMoney(t.average_win)} / ${formatMoney(t.average_loss)}`} note={`payoff ${plain(t.payoff_ratio)}`} />
      <StatCard label="Trades per year" value={t.trades_per_year === null ? '—' : t.trades_per_year.toFixed(1)} note={`avg hold ${t.average_holding_days === null ? '—' : `${t.average_holding_days.toFixed(1)} days`}`} />
      <StatCard label="Exposure" value={m.exposure.exposure_pct === null ? '—' : `${m.exposure.exposure_pct.toFixed(0)}%`} note="days with a position open" />
      <StatCard label="Longest losing streak" value={`${t.longest_losing_streak}`} note="trades in a row that did not win" />
    </div>
  );
}

function PeriodTables({ m }: { m: BacktestMetrics }) {
  const monthsByYear = useMemo(() => {
    const map = new Map<number, Map<number, BacktestPeriodReturn>>();
    for (const row of m.monthly_returns) {
      if (!map.has(row.year)) map.set(row.year, new Map());
      map.get(row.year)!.set(row.month ?? 0, row);
    }
    return map;
  }, [m.monthly_returns]);
  return (
    <div className="split-row">
      <div className="card" style={{ overflowX: 'auto' }}>
        <h3 style={{ margin: '0 0 8px' }}>Return by calendar year</h3>
        {m.yearly_returns.length === 0 ? (
          <EmptyState>No days simulated.</EmptyState>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Year</th>
                <th style={{ textAlign: 'right' }}>Return</th>
                <th style={{ textAlign: 'right' }}>Days</th>
              </tr>
            </thead>
            <tbody>
              {m.yearly_returns.map((y) => (
                <tr key={y.year}>
                  <td>
                    {y.year}
                    {y.partial && <span className="text-muted"> (part year)</span>}
                  </td>
                  <td className="tabular-nums" style={{ textAlign: 'right', color: returnColor(y.return_pct) }}>
                    {formatPct(y.return_pct)}
                  </td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>
                    {y.days}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="card" style={{ overflowX: 'auto' }}>
        <h3 style={{ margin: '0 0 8px' }}>Return by month</h3>
        {monthsByYear.size === 0 ? (
          <EmptyState>No days simulated.</EmptyState>
        ) : (
          <table style={{ fontSize: 12 }}>
            <thead>
              <tr>
                <th>Year</th>
                {MONTHS.map((n) => (
                  <th key={n} style={{ textAlign: 'right', padding: '8px 4px' }}>
                    {n}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {[...monthsByYear.entries()].map(([year, months]) => (
                <tr key={year}>
                  <td style={{ padding: '8px 4px' }}>{year}</td>
                  {MONTHS.map((_, i) => {
                    const cell = months.get(i + 1);
                    return (
                      <td key={i} className="tabular-nums" title={cell?.partial ? 'part month' : undefined} style={{ textAlign: 'right', padding: '8px 4px', color: returnColor(cell?.return_pct), fontStyle: cell?.partial ? 'italic' : undefined }}>
                        {cell ? cell.return_pct.toFixed(1) : ''}
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </table>
        )}
        <div className="text-muted" style={{ fontSize: 11, marginTop: 6 }}>Percent. Italic cells are part months.</div>
      </div>
    </div>
  );
}

type TradeSort = 'entry_date' | 'symbol' | 'direction' | 'exit_date' | 'close_reason' | 'realized_r' | 'realized_pnl' | 'holding_days';

function TradesTable({ trades }: { trades: BacktestTrade[] }) {
  const [sort, setSort] = useState<TradeSort>('entry_date');
  const [desc, setDesc] = useState(true);
  const [limit, setLimit] = useState(100);
  const sorted = useMemo(() => {
    const rows = [...trades];
    rows.sort((a, b) => {
      const av = a[sort];
      const bv = b[sort];
      if (av === bv) return 0;
      if (av === null || av === undefined) return 1; // missing values always last
      if (bv === null || bv === undefined) return -1;
      const cmp = av < bv ? -1 : 1;
      return desc ? -cmp : cmp;
    });
    return rows;
  }, [trades, sort, desc]);
  const header = (key: TradeSort, label: string, right = false) => (
    <th style={{ textAlign: right ? 'right' : 'left' }} aria-sort={sort === key ? (desc ? 'descending' : 'ascending') : 'none'}>
      <button
        className="text-muted"
        style={{ background: 'none', border: 'none', cursor: 'pointer', font: 'inherit', textTransform: 'inherit', letterSpacing: 'inherit', padding: 0 }}
        onClick={() => {
          if (sort === key) setDesc(!desc);
          else {
            setSort(key);
            setDesc(true);
          }
        }}
      >
        {label}
        {sort === key ? (desc ? ' ▼' : ' ▲') : ''}
      </button>
    </th>
  );
  if (trades.length === 0) return <EmptyState>The run made no trades.</EmptyState>;
  return (
    <div className="card" style={{ overflowX: 'auto' }}>
      <h3 style={{ margin: '0 0 8px' }}>Trades ({trades.length})</h3>
      <table>
        <thead>
          <tr>
            {header('symbol', 'Symbol')}
            {header('direction', 'Side')}
            {header('entry_date', 'Entry')}
            {header('exit_date', 'Exit')}
            {header('close_reason', 'Ended by')}
            {header('holding_days', 'Days', true)}
            {header('realized_r', 'R', true)}
            {header('realized_pnl', 'P&L', true)}
          </tr>
        </thead>
        <tbody>
          {sorted.slice(0, limit).map((t) => (
            <tr key={t.id}>
              <td>
                <TickerLink symbol={t.symbol} />
              </td>
              <td>
                <DirectionBadge direction={t.direction} />
              </td>
              <td className="tabular-nums">
                {t.entry_date} @ {t.entry_price.toFixed(2)}
              </td>
              <td className="tabular-nums">{t.exit_date ? `${t.exit_date} @ ${t.exit_price?.toFixed(2)}` : 'open'}</td>
              <td>{t.close_reason ? reasonLabel(t.close_reason) : '—'}</td>
              <td className="tabular-nums" style={{ textAlign: 'right' }}>
                {t.holding_days ?? '—'}
              </td>
              <td className="tabular-nums" style={{ textAlign: 'right', color: returnColor(t.realized_r) }}>
                {formatR(t.realized_r)}
              </td>
              <td className="tabular-nums" style={{ textAlign: 'right', color: returnColor(t.realized_pnl) }}>
                {formatMoney(t.realized_pnl)}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
      {sorted.length > limit && (
        <div style={{ marginTop: 8 }}>
          <button className="btn btn-secondary" onClick={() => setLimit(limit + 200)}>
            Show more ({sorted.length - limit} left)
          </button>
        </div>
      )}
    </div>
  );
}

function MixAndSplit({ m }: { m: BacktestMetrics }) {
  return (
    <div className="split-row">
      <div className="card" style={{ overflowX: 'auto' }}>
        <h3 style={{ margin: '0 0 8px' }}>How trades ended</h3>
        {m.exit_reasons.length === 0 ? (
          <EmptyState>No trades.</EmptyState>
        ) : (
          <table>
            <thead>
              <tr>
                <th>Reason</th>
                <th style={{ textAlign: 'right' }}>Trades</th>
                <th style={{ textAlign: 'right' }}>Share</th>
                <th style={{ textAlign: 'right' }}>Avg R</th>
                <th style={{ textAlign: 'right' }}>P&L</th>
              </tr>
            </thead>
            <tbody>
              {m.exit_reasons.map((e) => (
                <tr key={e.reason}>
                  <td>{reasonLabel(e.reason)}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{e.count}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{e.share_pct.toFixed(0)}%</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatR(e.average_r)}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right', color: returnColor(e.total_pnl) }}>{formatMoney(e.total_pnl)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
      <div className="card" style={{ overflowX: 'auto' }}>
        <h3 style={{ margin: '0 0 8px' }}>Long versus short</h3>
        <table>
          <thead>
            <tr>
              <th>Side</th>
              <th style={{ textAlign: 'right' }}>Trades</th>
              <th style={{ textAlign: 'right' }}>Win rate</th>
              <th style={{ textAlign: 'right' }}>Avg R</th>
              <th style={{ textAlign: 'right' }}>P&L</th>
            </tr>
          </thead>
          <tbody>
            {m.by_direction.map((d) => (
              <tr key={d.direction}>
                <td><DirectionBadge direction={d.direction} /></td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>{d.trades}</td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>{d.win_rate_pct === null ? '—' : `${d.win_rate_pct.toFixed(0)}%`}</td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatR(d.average_r)}</td>
                <td className="tabular-nums" style={{ textAlign: 'right', color: returnColor(d.total_pnl) }}>{formatMoney(d.total_pnl)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ScorecardPanel({ card }: { card: BacktestScorecard }) {
  const mark = (status: string) =>
    status === 'pass' ? <span className="badge badge-green">pass</span> : status === 'fail' ? <span className="badge badge-red">fail</span> : <span className="badge badge-neutral">not enough data</span>;
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div>
        <h3 style={{ margin: 0 }}>Scorecard</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          {card.note} {card.counts.pass} pass, {card.counts.fail} fail, {card.counts.insufficient_data} not enough data.
        </div>
      </div>
      {card.banners.map((b) => (
        <div key={b.key} role="note" className="card" style={{ background: 'var(--card-alt)', borderColor: b.level === 'warning' ? 'var(--amber)' : undefined, fontSize: 13, padding: 12 }}>
          {b.text}
        </div>
      ))}
      <div style={{ overflowX: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th>Check</th>
              <th>Criterion</th>
              <th>Actual</th>
              <th>Result</th>
            </tr>
          </thead>
          <tbody>
            {card.checks.map((c) => (
              <tr key={c.key}>
                <td>
                  {c.label}
                  {c.detail && <div className="text-muted" style={{ fontSize: 11 }}>{c.detail}</div>}
                </td>
                <td className="text-muted">{c.criterion}</td>
                <td className="tabular-nums">{c.actual}</td>
                <td>{mark(c.status)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function BaselinePanel({ baseline, running }: { baseline: BacktestBaseline | undefined; running: boolean }) {
  if (!baseline) return null;
  if (!baseline.available) {
    return (
      <div className="card">
        <h3 style={{ margin: '0 0 6px' }}>Random-entry baseline</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          {running ? 'The first random run has not finished yet.' : baseline.reason ?? 'No baseline for this run.'}
        </div>
      </div>
    );
  }
  const rows: [string, string, (v: number) => string][] = [
    ['total_return_pct', 'Total return', (v) => formatPct(v)],
    ['average_r', 'Average R', (v) => formatR(v)],
    ['sharpe', 'Sharpe', (v) => plain(v)],
  ];
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div>
        <h3 style={{ margin: 0 }}>Random-entry baseline</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>
          The same run {baseline.seeds?.length ?? 0} times with entries chosen at random (same symbols, days, sizing, stops, targets and exit rules). The green bar holds the real run.
          {baseline.status === 'cancelled' && ' Cancelled: only the finished random runs are counted.'}
          {baseline.status === 'failed' && ` ${baseline.note ?? ''}`}
        </div>
      </div>
      {baseline.caveat && <div className="text-muted" style={{ fontSize: 12 }}>{baseline.caveat}</div>}
      <BaselineHistogram baseline={baseline} />
      <div style={{ overflowX: 'auto' }}>
        <table>
          <thead>
            <tr>
              <th>Statistic</th>
              <th style={{ textAlign: 'right' }}>Real run</th>
              <th style={{ textAlign: 'right' }}>Random median</th>
              <th style={{ textAlign: 'right' }}>Random range</th>
              <th style={{ textAlign: 'right' }}>Percentile</th>
              <th style={{ textAlign: 'right' }}>Chance luck does this well</th>
            </tr>
          </thead>
          <tbody>
            {rows.map(([key, label, fmt]) => {
              const p = baseline.placement?.[key];
              if (!p) return null;
              return (
                <tr key={key}>
                  <td>{label}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{fmt(p.real)}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{fmt(p.random_median)}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{fmt(p.random_min)} to {fmt(p.random_max)}</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{p.percentile.toFixed(0)}th</td>
                  <td className="tabular-nums" style={{ textAlign: 'right' }}>{(p.chance_random_matches * 100).toFixed(0)}% (of {p.n} runs)</td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      {baseline.notes?.map((n) => (
        <div key={n} className="text-muted" style={{ fontSize: 12 }}>{n}</div>
      ))}
    </div>
  );
}

const DATED_PART_NAMES: Record<string, string> = { fundamentals: 'fundamentals', insiders: 'insiders', earnings: 'earnings' };

/** "fundamentals: 12 symbols had data (80% of 1,200 lookups)", from the run summary, or null when the run was price-only. */
function datedAvailability(summary: BacktestRun['summary']): string | null {
  const raw = summary?.dated_data;
  if (!raw || typeof raw !== 'object') return null;
  const parts = Object.entries(raw as Record<string, { requests?: number; answered?: number; symbols_with_data?: number }>).map(([part, v]) => {
    const requests = v.requests ?? 0;
    const share = requests > 0 ? Math.round(((v.answered ?? 0) / requests) * 100) : 0;
    return `${DATED_PART_NAMES[part] ?? part}: ${v.symbols_with_data ?? 0} symbol(s) had data (${share}% of ${requests} lookups)`;
  });
  return parts.length ? parts.join('; ') : null;
}
function CoveragePanel({ run }: { run: BacktestRun }) {
  const c = run.coverage;
  const s = run.summary;
  if (!c) return null;
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <h3 style={{ margin: 0 }}>What this run tested</h3>
      <div style={{ fontSize: 13 }}>{c.summary}</div>
      <div className="split-row">
        <div>
          <div style={{ fontWeight: 600, fontSize: 13 }}>{c.profile === 'price_plus_dated_data' ? 'Scored (prices plus the dated data switched on)' : 'Scored (prices only)'}</div>
          <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 13 }}>
            {c.active_parts.map((p) => (
              <li key={p.part}>{p.label}: up to {p.points_max} points{p.note ? ` (${p.note})` : ''}</li>
            ))}
          </ul>
        </div>
        <div>
          <div style={{ fontWeight: 600, fontSize: 13 }}>Not in this run (contribute 0)</div>
          <ul style={{ margin: '4px 0', paddingLeft: 18, fontSize: 13 }}>
            {c.inactive_parts.map((p) => (
              <li key={p.part}>{p.label}: {p.reason}</li>
            ))}
          </ul>
        </div>
      </div>
      {datedAvailability(s) && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          Dated data found: {datedAvailability(s)}
        </div>
      )}
      <div className="text-muted" style={{ fontSize: 12 }}>
        Confidence bar {c.min_confidence_for_trade}% needs {c.bar_points_needed ?? 'more than the maximum'} of the {c.achievable_points} reachable points
        {c.bar_reachable ? '.' : ', which is not reachable: no trade could be taken.'}
        {s && typeof s.symbols_skipped === 'object' && s.symbols_skipped !== null && Object.keys(s.symbols_skipped as object).length > 0 && ` Skipped symbols: ${Object.keys(s.symbols_skipped as object).join(', ')}.`}
      </div>
    </div>
  );
}

function RunDetail({ id }: { id: number }) {
  const run = useBacktest(id);
  const [logScale, setLogScale] = useState(false);
  const data = run.data;
  const hasResults = Boolean(data?.summary);
  const active = data?.status === 'queued' || data?.status === 'running';
  const baselineRunning = data?.progress.phase === 'baseline';
  const metrics = useBacktestMetrics(id, hasResults, baselineRunning);
  const equity = useBacktestEquity(id, hasResults);
  const trades = useBacktestTrades(id, hasResults);
  const benchmarks = useBacktestBenchmarks(id, hasResults);
  const baseline = useBacktestBaseline(id, hasResults, baselineRunning);
  const scorecard = useBacktestScorecard(id, hasResults, baselineRunning);

  if (run.isLoading) return <LoadingSpinner label="Loading run…" />;
  if (run.isError || !data) return <ErrorBanner message={(run.error as ApiError | null)?.message ?? 'Could not load the run'} onRetry={() => run.refetch()} />;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <div>
          <h2 style={{ margin: 0, fontSize: 20 }}>
            Run #{data.id} {statusBadge(data)}
          </h2>
          <div className="text-muted" style={{ fontSize: 13 }}>
            {(data.params.symbols ?? []).length} symbols, {data.params.start} to {data.params.end}, decisions every {data.params.decision_every_n_days ?? 1} day
            {(data.params.decision_every_n_days ?? 1) === 1 ? '' : 's'}
            {data.strategy_fingerprint ? `, strategy ${data.strategy_fingerprint.slice(0, 8)}` : ''}
          </div>
        </div>
      </div>

      {active && <ProgressPanel run={data} />}
      {data.status === 'failed' && <ErrorBanner message={data.error ?? 'The run failed.'} />}
      {data.status === 'cancelled' && <div className="card text-muted">Cancelled{hasResults ? ': the results below cover only the days simulated before it stopped.' : ' before any results were saved.'}</div>}
      {!hasResults && active && <EmptyState>Results appear here as soon as the main run finishes.</EmptyState>}

      {hasResults && metrics.isLoading && <LoadingSpinner label="Computing statistics…" />}
      {metrics.isError && <ErrorBanner message={(metrics.error as ApiError).message} onRetry={() => metrics.refetch()} />}
      {metrics.data && (
        <>
          {metrics.data.partial && <div className="card text-muted" style={{ fontSize: 13 }}>Partial run: statistics cover the days simulated before the cancel.</div>}
          <Cards m={metrics.data} />

          <div className="card">
            <div className="page-header" style={{ marginBottom: 8 }}>
              <h3 style={{ margin: 0 }}>Equity versus SPY</h3>
              <label style={{ display: 'flex', gap: 8, alignItems: 'center', fontSize: 13 }}>
                <input type="checkbox" checked={logScale} onChange={(e) => setLogScale(e.target.checked)} />
                Log scale
              </label>
            </div>
            {equity.isLoading || benchmarks.isLoading ? (
              <LoadingSpinner />
            ) : equity.data && equity.data.length > 0 ? (
              <EquityVsBenchmarkChart equity={equity.data} benchmarks={benchmarks.data} logScale={logScale} />
            ) : (
              <EmptyState>No equity points.</EmptyState>
            )}
            {benchmarks.data && !benchmarks.data.spy.available && (
              <div className="text-muted" style={{ fontSize: 12, marginTop: 8 }}>SPY line unavailable: {benchmarks.data.spy.reason}</div>
            )}
            {benchmarks.isError && <ErrorBanner message={(benchmarks.error as ApiError).message} />}
            {benchmarks.data?.comparison && <BenchmarkTable b={benchmarks.data} />}
          </div>

          <div className="card">
            <h3 style={{ margin: '0 0 8px' }}>Drawdown</h3>
            {metrics.data.drawdown_series.length > 0 ? <DrawdownChart series={metrics.data.drawdown_series} /> : <EmptyState>No days simulated.</EmptyState>}
            <div className="text-muted" style={{ fontSize: 12, marginTop: 6 }}>
              Worst: -{metrics.data.drawdown.max_drawdown_pct.toFixed(1)}% (peak {metrics.data.drawdown.peak_date ?? '—'}, trough {metrics.data.drawdown.trough_date ?? '—'},{' '}
              {metrics.data.drawdown.recovery_date ? `recovered ${metrics.data.drawdown.recovery_date}` : 'not recovered'}). Longest stretch below a prior high: {metrics.data.drawdown.longest_underwater_days} days.
            </div>
          </div>

          <PeriodTables m={metrics.data} />
          <MixAndSplit m={metrics.data} />
        </>
      )}

      {hasResults && (trades.isLoading ? <LoadingSpinner /> : trades.isError ? <ErrorBanner message={(trades.error as ApiError).message} /> : trades.data && <TradesTable trades={trades.data} />)}
      {hasResults && scorecard.data && <ScorecardPanel card={scorecard.data} />}
      {scorecard.isError && <ErrorBanner message={(scorecard.error as ApiError).message} onRetry={() => scorecard.refetch()} />}
      {hasResults && <BaselinePanel baseline={baseline.data} running={baselineRunning} />}
      {hasResults && <CoveragePanel run={data} />}
    </div>
  );
}

function BenchmarkTable({ b }: { b: NonNullable<ReturnType<typeof useBacktestBenchmarks>['data']> }) {
  const c = b.comparison!;
  const ew = b.equal_weight;
  return (
    <div style={{ marginTop: 12, overflowX: 'auto' }}>
      <table>
        <thead>
          <tr>
            <th>Compared with SPY buy-and-hold</th>
            <th style={{ textAlign: 'right' }}>Value</th>
          </tr>
        </thead>
        <tbody>
          <tr><td>Strategy / SPY return</td><td className="tabular-nums" style={{ textAlign: 'right' }}>{formatPct(c.strategy_return_pct)} / {formatPct(c.spy_return_pct)}</td></tr>
          <tr><td>Excess return</td><td className="tabular-nums" style={{ textAlign: 'right', color: returnColor(c.excess_return_pct) }}>{formatPct(c.excess_return_pct)}</td></tr>
          <tr><td>Beta (n={c.n}{c.beta_t !== null && c.beta_t !== undefined ? `, t=${plain(c.beta_t, 1)}` : ''})</td><td className="tabular-nums" style={{ textAlign: 'right' }}>{plain(c.beta)}</td></tr>
          <tr><td>Alpha per year (t={plain(c.alpha_t, 1)})</td><td className="tabular-nums" style={{ textAlign: 'right' }}>{formatPct(c.alpha_annual_pct)}</td></tr>
          <tr><td>Correlation</td><td className="tabular-nums" style={{ textAlign: 'right' }}>{plain(c.correlation)}</td></tr>
          <tr><td>Days outperforming SPY</td><td className="tabular-nums" style={{ textAlign: 'right' }}>{c.outperform_days_pct === null ? '—' : `${c.outperform_days_pct.toFixed(0)}%`}</td></tr>
          {ew.available && (
            <tr>
              <td>{ew.label} <span className="badge badge-amber">survivor-biased</span></td>
              <td className="tabular-nums" style={{ textAlign: 'right' }}>{formatPct(ew.total_return_pct)}</td>
            </tr>
          )}
        </tbody>
      </table>
      <div className="text-muted" style={{ fontSize: 11, marginTop: 6 }}>
        {b.costs} The basket holds only today's symbols, so it is flattered by hindsight.
        {ew.symbols_excluded && ew.symbols_excluded.length > 0 && ` Left out of the basket: ${ew.symbols_excluded.map((e) => e.symbol).join(', ')}.`}
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- page

export function BacktestsPage() {
  const [params, setParams] = useSearchParams();
  const selected = params.get('run') ? Number(params.get('run')) : null;
  const [showForm, setShowForm] = useState(true);
  const select = (id: number | null) => {
    const next = new URLSearchParams(params);
    if (id === null) next.delete('run');
    else next.set('run', String(id));
    setParams(next);
  };
  const validationTab = params.get('tab') === 'validation';
  const setTab = (tab: 'runs' | 'validation') => {
    const next = new URLSearchParams(params);
    if (tab === 'validation') next.set('tab', 'validation');
    else next.delete('tab');
    setParams(next);
  };
  const selectedValidation = params.get('validation') ? Number(params.get('validation')) : null;
  const selectValidation = (id: number | null) => {
    const next = new URLSearchParams(params);
    if (id === null) next.delete('validation');
    else next.set('validation', String(id));
    setParams(next);
  };
  const tabs = (
    <div role="tablist" style={{ display: 'flex', gap: 8 }}>
      <button role="tab" aria-selected={!validationTab} className={validationTab ? 'btn btn-secondary' : 'btn'} onClick={() => setTab('runs')}>
        Runs
      </button>
      <button role="tab" aria-selected={validationTab} className={validationTab ? 'btn' : 'btn btn-secondary'} onClick={() => setTab('validation')}>
        Validation
      </button>
    </div>
  );
  if (validationTab) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
        <div className="page-header">
          <div>
            <h1 style={{ margin: 0 }}>Backtest Lab</h1>
            <div className="text-muted" style={{ fontSize: 14 }}>
              Walk-forward validation: does the strategy hold up on days it was not tuned on, and how much of its Sharpe ratio could be luck? Paper trading only; nothing here places an order.
            </div>
          </div>
        </div>
        {tabs}
        <ValidationTab selected={selectedValidation} onSelect={selectValidation} />
      </div>
    );
  }
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <div>
          <h1 style={{ margin: 0 }}>Backtest Lab</h1>
          <div className="text-muted" style={{ fontSize: 14 }}>
            Replay the live rules over past years on stored prices, then compare the result with SPY and with random entries. Paper trading only; nothing here places an order.
          </div>
        </div>
        <button className="btn btn-secondary" onClick={() => setShowForm(!showForm)}>
          {showForm ? 'Hide new run' : 'New run'}
        </button>
      </div>
      {showForm && <NewRunForm onStarted={(id) => select(id)} />}
      <RunsList selected={selected} onSelect={select} />
      {selected !== null && Number.isFinite(selected) && <RunDetail key={selected} id={selected} />}
    </div>
  );
}
