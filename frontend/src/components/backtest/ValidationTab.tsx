import { useMemo, useState } from 'react';
import { Line } from 'react-chartjs-2';
import '../chart/chartSetup';
import {
  useBacktestHistoryCoverage,
  useCancelValidation,
  useStartValidation,
  useValidation,
  useValidationOptions,
  useValidations,
} from '../../api/hooks';
import type { ApiError } from '../../api/client';
import type { BacktestValidation, ValidationFold, ValidationOptions, ValidationScorecard, ValidationWindowStats } from '../../api/types';
import { StatCard } from '../StatCard';
import { EmptyState, ErrorBanner, LoadingSpinner, formatNumber } from '../common';

const DEFAULT_SYMBOLS = ['NVDA', 'AAPL'];
const DEFAULT_PERIOD_YEARS = 6; // folds need many trading days: each window is at least a month
const MUTED = '#8b8fa3';
const GRID = '#1c1f29';
const GREEN = '#10b981';
const usd0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

const returnColor = (v: number | null | undefined) => (v === null || v === undefined ? undefined : v > 0 ? 'var(--green)' : v < 0 ? 'var(--red)' : undefined);
const pct = (v: number | null | undefined, digits = 1) => (v === null || v === undefined ? '—' : `${v >= 0 ? '+' : ''}${v.toFixed(digits)}%`);
const num = (v: number | null | undefined, digits = 2) => formatNumber(v, digits);

function addYears(iso: string, years: number): string {
  const d = new Date(`${iso}T00:00:00Z`);
  d.setUTCFullYear(d.getUTCFullYear() + years);
  return d.toISOString().slice(0, 10);
}

function describeParams(params: Record<string, number>, knobs: ValidationOptions['knobs'] | undefined): string {
  const entries = Object.entries(params);
  if (entries.length === 0) return 'settings as they are';
  return entries.map(([k, val]) => `${knobs?.find((x) => x.name === k)?.label ?? k} = ${val}`).join(', ');
}

// ---------------------------------------------------------------- form

function ValidationForm({ onStarted }: { onStarted: (id: number) => void }) {
  const options = useValidationOptions();
  const start = useStartValidation();
  const [symbols, setSymbols] = useState<string[]>(DEFAULT_SYMBOLS);
  const [draft, setDraft] = useState('');
  const [startDate, setStartDate] = useState('');
  const [endDate, setEndDate] = useState('');
  const [folds, setFolds] = useState<number | null>(null);
  const [mode, setMode] = useState<string | null>(null);
  const [trainRatio, setTrainRatio] = useState<number | null>(null);
  const [embargo, setEmbargo] = useState<number | null>(null);
  const [grid, setGrid] = useState<Record<string, string>>({});
  const coverage = useBacktestHistoryCoverage(symbols);

  const o = options.data;
  const spy = coverage.data?.benchmarks.find((b) => b.symbol === 'SPY');
  const effectiveEnd = endDate || coverage.data?.latest_end_date || '';
  const defaultStart = effectiveEnd ? addYears(effectiveEnd, -DEFAULT_PERIOD_YEARS) : '';
  const effectiveStart = startDate || (spy?.first_date && defaultStart < spy.first_date ? spy.first_date : defaultStart);
  const historyMissing = Boolean(coverage.data && (coverage.data.missing.length > 0 || coverage.data.benchmarks_missing.length > 0));

  // The grid as numbers, with what is wrong with each entry; variants and runs are counted from it live.
  const parsed = useMemo(() => {
    const out: Record<string, number[]> = {};
    const problems: string[] = [];
    for (const knob of o?.knobs ?? []) {
      const raw = (grid[knob.name] ?? '').trim();
      if (!raw) continue;
      const values = raw.split(/[\s,;]+/).filter(Boolean).map(Number);
      if (values.some((x) => Number.isNaN(x) || x < knob.low || x > knob.high)) {
        problems.push(`${knob.label}: values must be numbers from ${knob.low} to ${knob.high}`);
        continue;
      }
      if (knob.kind === 'int' && values.some((x) => !Number.isInteger(x))) {
        problems.push(`${knob.label}: whole numbers only`);
        continue;
      }
      const unique = Array.from(new Set(values));
      if (o && unique.length > o.max_values_per_knob) {
        problems.push(`${knob.label}: at most ${o.max_values_per_knob} values`);
        continue;
      }
      out[knob.name] = unique;
    }
    return { grid: out, problems };
  }, [grid, o]);

  const foldCount = folds ?? o?.default_folds ?? 4;
  const variants = Object.values(parsed.grid).reduce((n, v) => n * v.length, 1);
  const trials = variants <= 1 ? 1 : variants * foldCount;
  const runs = variants * foldCount + foldCount;
  const limitsOk = !o || (variants <= o.max_variants && runs <= o.max_total_runs);
  const datesOk = Boolean(effectiveStart && effectiveEnd && effectiveEnd > effectiveStart);
  const canStart = Boolean(o) && symbols.length > 0 && datesOk && !historyMissing && parsed.problems.length === 0 && limitsOk && !start.isPending;

  function addSymbols(raw: string) {
    const names = raw.split(/[\s,;]+/).map((s) => s.trim().toUpperCase()).filter(Boolean);
    setSymbols((prev) => Array.from(new Set([...prev, ...names])));
    setDraft('');
  }

  function submit() {
    if (!o) return;
    start.mutate(
      {
        symbols,
        start: effectiveStart,
        end: effectiveEnd,
        folds: foldCount,
        mode: mode ?? o.default_mode,
        train_ratio: trainRatio ?? o.default_train_ratio,
        embargo_days: embargo ?? o.default_embargo_days,
        grid: parsed.grid,
        overrides: {},
        decision_every_n_days: 1,
      },
      { onSuccess: (r) => onStarted(r.id) },
    );
  }

  if (options.isLoading) return <LoadingSpinner label="Loading options…" />;
  if (options.isError || !o) return <ErrorBanner message={(options.error as ApiError | null)?.message ?? 'Could not load validation options'} onRetry={() => options.refetch()} />;

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 18 }}>New validation</h2>
        <div className="text-muted" style={{ fontSize: 13, marginTop: 4 }}>
          Cuts the period into folds. In each fold the settings are tried on the earlier (train) days, the best one is picked, and only that one is tested on the later (test) days it has never seen.
          Uses stored prices only; nothing is downloaded.
        </div>
      </div>

      <div>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Symbols ({symbols.length})</div>
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
          <button className="btn btn-secondary" disabled={!draft.trim()} onClick={() => addSymbols(draft)}>Add</button>
          <button className="btn btn-secondary" disabled={symbols.length === 0} onClick={() => setSymbols([])}>Clear</button>
        </div>
        <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginTop: 8 }}>
          {symbols.map((s) => (
            <button key={s} className="badge badge-neutral" style={{ cursor: 'pointer', border: 'none' }} title="Remove" onClick={() => setSymbols((prev) => prev.filter((x) => x !== s))}>
              {s} ×
            </button>
          ))}
        </div>
      </div>

      {historyMissing && coverage.data && (
        <div className="card" style={{ background: 'var(--card-alt)', borderColor: 'var(--amber)' }} role="alert">
          <div style={{ fontWeight: 600, marginBottom: 6 }}>Price history is missing, so this cannot start yet</div>
          <div style={{ fontSize: 13 }}>
            Missing: <b>{[...coverage.data.benchmarks_missing, ...coverage.data.missing].join(', ')}</b>. Fill the store once from a terminal in the repo folder:
          </div>
          <code style={{ display: 'block', marginTop: 8, padding: 8, overflowX: 'auto', whiteSpace: 'pre-wrap', userSelect: 'all' }}>{coverage.data.preload_command}</code>
        </div>
      )}

      <div style={{ display: 'flex', gap: 16, flexWrap: 'wrap' }}>
        <label style={{ fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>From</div>
          <input type="date" value={effectiveStart} min={spy?.first_date ?? undefined} onChange={(e) => setStartDate(e.target.value)} />
        </label>
        <label style={{ fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>To</div>
          <input type="date" value={effectiveEnd} max={coverage.data?.latest_end_date ?? undefined} onChange={(e) => setEndDate(e.target.value)} />
        </label>
        <label style={{ fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Folds ({o.min_folds} to {o.max_folds})</div>
          <input type="number" min={o.min_folds} max={o.max_folds} value={foldCount} onChange={(e) => setFolds(Number(e.target.value))} style={{ width: 80 }} />
        </label>
        <label style={{ fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Train window</div>
          <select value={mode ?? o.default_mode} onChange={(e) => setMode(e.target.value)}>
            {o.modes.map((m) => (
              <option key={m} value={m}>{m === 'anchored' ? 'Anchored (grows from the start)' : 'Rolling (fixed length)'}</option>
            ))}
          </select>
        </label>
        <label style={{ fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Train length (x test)</div>
          <input type="number" step="0.5" min={o.min_train_ratio} max={o.max_train_ratio} value={trainRatio ?? o.default_train_ratio} onChange={(e) => setTrainRatio(Number(e.target.value))} style={{ width: 80 }} />
        </label>
        <label style={{ fontSize: 13 }}>
          <div style={{ fontWeight: 600, marginBottom: 4 }}>Embargo (days)</div>
          <input type="number" min={0} max={o.max_embargo_days} value={embargo ?? o.default_embargo_days} onChange={(e) => setEmbargo(Number(e.target.value))} style={{ width: 80 }} />
        </label>
      </div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        The embargo is a gap of trading days between the end of the train window and the start of its test window. Each test window is at least {o.min_window_days} trading days, so the period must be long enough.
      </div>

      <div>
        <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Settings to try (optional)</div>
        <div className="text-muted" style={{ fontSize: 12, marginBottom: 8 }}>
          Type values separated by commas, up to {o.max_values_per_knob} per setting. Leave all empty to test the settings as they are. Every combination is one variant.
        </div>
        <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
          {o.knobs.map((knob) => (
            <label key={knob.name} style={{ fontSize: 13 }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>{knob.label}</div>
              <input
                aria-label={knob.label}
                placeholder={`now ${knob.live_value}; e.g. ${knob.live_value}, ${knob.kind === 'int' ? Math.round(knob.live_value * 1.5) : +(knob.live_value * 1.5).toFixed(2)}`}
                value={grid[knob.name] ?? ''}
                onChange={(e) => setGrid({ ...grid, [knob.name]: e.target.value })}
                style={{ width: '100%' }}
              />
              <div className="text-muted" style={{ fontSize: 11, marginTop: 2 }}>{knob.help} Allowed {knob.low} to {knob.high}.</div>
            </label>
          ))}
        </div>
        {parsed.problems.map((p) => (
          <div key={p} role="alert" className="text-red" style={{ fontSize: 12, marginTop: 4 }}>{p}</div>
        ))}
      </div>

      <div className="card" style={{ background: 'var(--card-alt)', fontSize: 13 }}>
        <b>{variants}</b> variant{variants === 1 ? '' : 's'} x <b>{foldCount}</b> folds: <b>{runs}</b> window runs.{' '}
        {variants > 1 ? (
          <>It counts as <b>{trials} tries</b> when the result is deflated, because every variant was tried in every fold.</>
        ) : (
          <>No search happens with one variant, so there is nothing to deflate for beyond the sample length.</>
        )}
        {!limitsOk && <div className="text-red">Limits: at most {o.max_variants} variants and {o.max_total_runs} runs.</div>}
        <div className="text-muted" style={{ marginTop: 4 }}>Runs go one after another in the background, like a backtest; a long period with many variants takes a while.</div>
      </div>

      {start.isError && <ErrorBanner message={(start.error as ApiError).message} />}
      <div>
        <button className="btn" disabled={!canStart} onClick={submit}>{start.isPending ? 'Starting…' : 'Start validation'}</button>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------- list

function ValidationList({ selected, onSelect }: { selected: number | null; onSelect: (id: number) => void }) {
  const list = useValidations();
  if (list.isLoading) return <LoadingSpinner label="Loading validations…" />;
  if (list.isError) return <ErrorBanner message={(list.error as ApiError).message} onRetry={() => list.refetch()} />;
  if (!list.data || list.data.length === 0) return <EmptyState>No validations yet. Start one above.</EmptyState>;
  return (
    <div className="card" style={{ overflowX: 'auto' }}>
      <h2 style={{ margin: '0 0 8px', fontSize: 18 }}>Validations</h2>
      <table>
        <thead>
          <tr><th>#</th><th>Status</th><th>Symbols</th><th>Period</th><th style={{ textAlign: 'right' }}>Folds</th></tr>
        </thead>
        <tbody>
          {list.data.map((v) => {
            const p = v.params as { symbols?: string[]; start?: string; end?: string; folds?: number };
            return (
              <tr key={v.id} onClick={() => onSelect(v.id)} style={{ cursor: 'pointer', background: v.id === selected ? 'var(--card-alt)' : undefined }}>
                <td><button className="btn btn-secondary" onClick={() => onSelect(v.id)} aria-label={`Open validation ${v.id}`}>{v.id}</button></td>
                <td><StatusBadge status={v.status} /></td>
                <td>{(p.symbols ?? []).slice(0, 4).join(', ')}{(p.symbols?.length ?? 0) > 4 ? ` +${(p.symbols?.length ?? 0) - 4}` : ''}</td>
                <td className="tabular-nums">{p.start} to {p.end}</td>
                <td className="tabular-nums" style={{ textAlign: 'right' }}>{p.folds}</td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function StatusBadge({ status }: { status: string }) {
  const cls = status === 'done' ? 'badge-green' : status === 'failed' ? 'badge-red' : status === 'cancelled' ? 'badge-neutral' : 'badge-amber';
  return <span className={`badge ${cls}`}>{status}</span>;
}

// ---------------------------------------------------------------- detail

function Progress({ v }: { v: BacktestValidation }) {
  const cancel = useCancelValidation();
  const { progress } = v;
  const within = progress.days_total > 0 ? progress.days_done / progress.days_total : 0;
  const pctDone = progress.runs_total > 0 ? Math.min(100, ((progress.runs_done + within) / progress.runs_total) * 100) : 0;
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <div style={{ fontWeight: 600 }}>
          {v.status === 'queued' ? 'Queued' : `Run ${Math.min(progress.runs_done + 1, progress.runs_total)} of ${progress.runs_total}${progress.label ? `: ${progress.label}` : ''}`}
        </div>
        <button className="btn btn-secondary" disabled={cancel.isPending || v.cancel_requested} onClick={() => cancel.mutate(v.id)}>
          {v.cancel_requested ? 'Cancelling…' : 'Cancel'}
        </button>
      </div>
      <div role="progressbar" aria-valuenow={Math.round(pctDone)} aria-valuemin={0} aria-valuemax={100} style={{ height: 10, background: 'var(--card-alt)', borderRadius: 5, overflow: 'hidden' }}>
        <div style={{ width: `${pctDone}%`, height: '100%', background: 'var(--green)', transition: 'width 0.3s' }} />
      </div>
    </div>
  );
}

function FoldTable({ folds, knobs }: { folds: ValidationFold[]; knobs: ValidationOptions['knobs'] | undefined }) {
  const cell = (s: ValidationWindowStats) => (
    <>
      <td className="tabular-nums" style={{ textAlign: 'right', color: returnColor(s.total_return_pct) }}>{pct(s.total_return_pct)}</td>
      <td className="tabular-nums" style={{ textAlign: 'right' }}>{num(s.sharpe)}</td>
      <td className="tabular-nums" style={{ textAlign: 'right' }}>{s.trade_count}</td>
    </>
  );
  return (
    <div className="card" style={{ overflowX: 'auto' }}>
      <h3 style={{ margin: '0 0 4px' }}>Folds: in-sample against out-of-sample</h3>
      <div className="text-muted" style={{ fontSize: 12, marginBottom: 8 }}>
        In-sample is the selected variant on the train days it was picked on; out-of-sample is the same variant on the later test days. Sharpe is yearly. A large gap between the two sides is the signature of tuning to noise.
      </div>
      <table>
        <thead>
          <tr>
            <th rowSpan={2}>Fold</th>
            <th rowSpan={2}>Train</th>
            <th rowSpan={2}>Test</th>
            <th rowSpan={2}>Selected</th>
            <th colSpan={3} style={{ textAlign: 'center' }}>In-sample</th>
            <th colSpan={3} style={{ textAlign: 'center' }}>Out-of-sample</th>
          </tr>
          <tr>
            <th style={{ textAlign: 'right' }}>Return</th><th style={{ textAlign: 'right' }}>Sharpe</th><th style={{ textAlign: 'right' }}>Trades</th>
            <th style={{ textAlign: 'right' }}>Return</th><th style={{ textAlign: 'right' }}>Sharpe</th><th style={{ textAlign: 'right' }}>Trades</th>
          </tr>
        </thead>
        <tbody>
          {folds.map((f) => (
            <tr key={f.index}>
              <td>{f.index}</td>
              <td className="tabular-nums" style={{ fontSize: 12 }}>{f.train.start} to {f.train.end}<div className="text-muted">{f.train.days} days</div></td>
              <td className="tabular-nums" style={{ fontSize: 12 }}>{f.test.start} to {f.test.end}<div className="text-muted">{f.test.days} days, after {f.embargo_days} day gap</div></td>
              <td style={{ fontSize: 12 }}>{describeParams(f.selected.params, knobs)}</td>
              {cell(f.selected.in_sample)}
              {cell(f.selected.out_of_sample)}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function OosChart({ points }: { points: { day: string; equity: number }[] }) {
  return (
    <Line
      data={{
        labels: points.map((p) => p.day),
        datasets: [{ label: 'Out-of-sample equity', data: points.map((p) => p.equity), borderColor: GREEN, backgroundColor: GREEN, borderWidth: 2, pointRadius: 0, tension: 0 }],
      }}
      options={{
        responsive: true,
        animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: { legend: { display: false }, tooltip: { callbacks: { label: (ctx) => usd0.format(ctx.parsed.y ?? 0) } } },
        scales: {
          x: { ticks: { color: MUTED, maxTicksLimit: 8, autoSkip: true, maxRotation: 0 }, grid: { color: GRID } },
          y: { ticks: { color: MUTED, callback: (v) => usd0.format(Number(v)) }, grid: { color: GRID } },
        },
      }}
    />
  );
}

function Scorecard({ card }: { card: ValidationScorecard }) {
  const mark = (s: string) => (s === 'pass' ? <span className="badge badge-green">pass</span> : s === 'fail' ? <span className="badge badge-red">fail</span> : <span className="badge badge-neutral">not enough data</span>);
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <div>
        <h3 style={{ margin: 0 }}>Scorecard</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>{card.note}</div>
      </div>
      {card.banners.map((b) => (
        <div key={b.key} role="note" className="card" style={{ background: 'var(--card-alt)', borderColor: b.level === 'warning' ? 'var(--amber)' : undefined, fontSize: 13, padding: 12 }}>{b.text}</div>
      ))}
      <div style={{ overflowX: 'auto' }}>
        <table>
          <thead><tr><th>Check</th><th>Criterion</th><th>Actual</th><th>Result</th></tr></thead>
          <tbody>
            {card.checks.map((c) => (
              <tr key={c.key}>
                <td>{c.label}{c.detail && <div className="text-muted" style={{ fontSize: 11 }}>{c.detail}</div>}</td>
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

function DsrPanel({ v }: { v: BacktestValidation }) {
  const dsr = v.result?.dsr;
  if (!dsr) return null;
  if (!dsr.available) {
    return (
      <div className="card" role="note">
        <h3 style={{ margin: '0 0 4px' }}>Deflated Sharpe ratio</h3>
        <div className="text-muted" style={{ fontSize: 13 }}>{dsr.reason}</div>
      </div>
    );
  }
  const passes = (dsr.dsr ?? 0) >= dsr.confidence_level;
  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
      <h3 style={{ margin: 0 }}>How much of this could be luck</h3>
      <div className="stat-grid">
        <StatCard label="Out-of-sample Sharpe" value={num(dsr.sharpe_annualised)} note={`yearly, ${dsr.n_observations} days`} />
        <StatCard label={`Luck from ${dsr.n_trials} ${dsr.n_trials === 1 ? 'try' : 'tries'}`} value={num(dsr.expected_max_sharpe_annualised)} note="expected best Sharpe by chance" />
        <StatCard label="Probabilistic Sharpe" value={`${((dsr.psr ?? 0) * 100).toFixed(0)}%`} note="chance the true Sharpe is above 0" />
        <StatCard label="Deflated Sharpe" value={`${((dsr.dsr ?? 0) * 100).toFixed(0)}%`} note={passes ? `clears ${(dsr.confidence_level * 100).toFixed(0)}%` : `under ${(dsr.confidence_level * 100).toFixed(0)}%`} />
      </div>
      <div style={{ fontSize: 14 }}>{(dsr.reading ?? []).map((line, i) => <p key={i} style={{ margin: '0 0 6px' }}>{line}</p>)}</div>
      <div className="text-muted" style={{ fontSize: 12 }}>
        Why deflate: the best of many tries looks better than it is, even when none has any skill. The deflated Sharpe ratio asks whether the result beats what that many tries would give by luck alone, and
        allows for fat tails and a short sample. It is evidence about luck, not a promise about the future.
      </div>
    </div>
  );
}

function ValidationDetail({ id }: { id: number }) {
  const query = useValidation(id);
  const options = useValidationOptions();
  const v = query.data;
  if (query.isLoading) return <LoadingSpinner label="Loading validation…" />;
  if (query.isError || !v) return <ErrorBanner message={(query.error as ApiError | null)?.message ?? 'Could not load this validation'} onRetry={() => query.refetch()} />;
  const active = v.status === 'queued' || v.status === 'running';
  const result = v.result;
  const agg = result?.aggregate;
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
        <h2 style={{ margin: 0, fontSize: 18 }}>Validation #{v.id}</h2>
        <StatusBadge status={v.status} />
        {result && <span className="text-muted" style={{ fontSize: 13 }}>{result.mode} train window, {result.n_variants} variant{result.n_variants === 1 ? '' : 's'}, {result.n_trials} {result.n_trials === 1 ? 'try' : 'tries'}</span>}
      </div>
      {v.error && <ErrorBanner message={v.error} />}
      {active && <Progress v={v} />}
      {v.status === 'cancelled' && <div role="note" className="card" style={{ borderColor: 'var(--amber)', fontSize: 13 }}>Cancelled. The folds that finished are shown; there is no combined result or deflated Sharpe for a partial validation.</div>}
      {agg && (
        <div className="stat-grid">
          <StatCard label="Out-of-sample return" value={pct(agg.total_return_pct)} note={`${agg.trading_days} test days stitched`} />
          <StatCard label="Out-of-sample Sharpe" value={num(agg.sharpe)} note={`in-sample avg ${num(agg.in_sample_sharpe_mean)}`} />
          <StatCard label="Folds positive" value={`${agg.folds_positive} of ${agg.folds}`} note="out-of-sample return above 0" />
          <StatCard label="Trades" value={String(agg.trade_count)} note={`max drawdown ${num(agg.max_drawdown_pct, 1)}%`} />
        </div>
      )}
      {result && result.folds.length > 0 && <FoldTable folds={result.folds} knobs={options.data?.knobs} />}
      {result && result.oos_equity.length > 0 && (
        <div className="card">
          <h3 style={{ margin: '0 0 4px' }}>Out-of-sample equity</h3>
          <div className="text-muted" style={{ fontSize: 12, marginBottom: 8 }}>The test windows only, joined end to end: each one continues from where the last ended.</div>
          <OosChart points={result.oos_equity} />
        </div>
      )}
      <DsrPanel v={v} />
      {v.scorecard && <Scorecard card={v.scorecard} />}
    </div>
  );
}

// ---------------------------------------------------------------- tab

export function ValidationTab({ selected, onSelect }: { selected: number | null; onSelect: (id: number | null) => void }) {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <ValidationForm onStarted={(id) => onSelect(id)} />
      <ValidationList selected={selected} onSelect={onSelect} />
      {selected !== null && Number.isFinite(selected) && <ValidationDetail key={selected} id={selected} />}
    </div>
  );
}
