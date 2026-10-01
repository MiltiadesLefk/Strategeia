import { Bar, Line } from 'react-chartjs-2';
import { Chart as ChartJS, LogarithmicScale } from 'chart.js';
import '../chart/chartSetup';
import type { BacktestBaseline, BacktestBenchmarks, BacktestEquityPoint, BacktestMetrics } from '../../api/types';

// The shared setup registers the linear scale only; the equity chart can also be drawn on a log axis.
ChartJS.register(LogarithmicScale);

const MUTED = '#8b8fa3';
const GRID = '#1c1f29';
const GREEN = '#10b981';
const RED = '#ef4444';
const BLUE = '#60a5fa';
const AMBER = '#f59e0b';

const usd0 = new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });

const dayTicks = { color: MUTED, maxTicksLimit: 8, autoSkip: true, maxRotation: 0 };

/** The strategy's equity against SPY (and the equal-weight basket when it exists), all from the same starting capital.
 *  Lines are matched to the strategy's days by date, so a missing benchmark bar leaves a gap instead of shifting the line. */
export function EquityVsBenchmarkChart({
  equity,
  benchmarks,
  logScale,
}: {
  equity: BacktestEquityPoint[];
  benchmarks: BacktestBenchmarks | undefined;
  logScale: boolean;
}) {
  const days = equity.map((p) => p.day);
  const align = (rows: { day: string; equity: number }[] | undefined) => {
    if (!rows) return null;
    const byDay = new Map(rows.map((r) => [r.day, r.equity]));
    return days.map((d) => byDay.get(d) ?? null);
  };
  const spy = benchmarks?.spy.available ? align(benchmarks.spy.equity) : null;
  const basket = benchmarks?.equal_weight.available ? align(benchmarks.equal_weight.equity) : null;
  const line = (label: string, data: (number | null)[], color: string, dash?: number[]) => ({
    label,
    data,
    borderColor: color,
    backgroundColor: color,
    borderWidth: 2,
    borderDash: dash,
    pointRadius: 0,
    tension: 0,
    spanGaps: true,
  });
  const datasets = [
    line('Strategy', equity.map((p) => p.equity), GREEN),
    ...(spy ? [line('SPY buy-and-hold', spy, BLUE)] : []),
    ...(basket ? [line('Equal-weight basket (survivor-biased)', basket, AMBER, [6, 4])] : []),
  ];
  return (
    <Line
      data={{ labels: days, datasets }}
      options={{
        responsive: true,
        animation: false,
        interaction: { mode: 'index', intersect: false },
        plugins: {
          legend: { display: true, labels: { color: MUTED, boxWidth: 14 } },
          tooltip: { callbacks: { label: (ctx) => `${ctx.dataset.label}: ${usd0.format(ctx.parsed.y ?? 0)}` } },
        },
        scales: {
          x: { ticks: dayTicks, grid: { color: GRID } },
          y: {
            type: logScale ? 'logarithmic' : 'linear',
            ticks: { color: MUTED, callback: (v) => usd0.format(Number(v)) },
            grid: { color: GRID },
          },
        },
      }}
    />
  );
}

export function DrawdownChart({ series }: { series: BacktestMetrics['drawdown_series'] }) {
  return (
    <Line
      data={{
        labels: series.map((p) => p.day),
        datasets: [
          {
            label: 'Drawdown from the highest close so far',
            data: series.map((p) => p.drawdown_pct),
            borderColor: RED,
            backgroundColor: 'rgba(239, 68, 68, 0.18)',
            fill: true,
            pointRadius: 0,
            tension: 0,
            borderWidth: 1.5,
          },
        ],
      }}
      options={{
        responsive: true,
        animation: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => `${(ctx.parsed.y ?? 0).toFixed(2)}%` } },
        },
        scales: {
          x: { ticks: dayTicks, grid: { color: GRID } },
          y: { max: 0, ticks: { color: MUTED, callback: (v) => `${Number(v).toFixed(0)}%` }, grid: { color: GRID } },
        },
      }}
    />
  );
}

const HISTOGRAM_BINS = 8;

/** Total return of each random-entry run, binned, with the bin holding the real run marked in a different colour. */
export function BaselineHistogram({ baseline }: { baseline: BacktestBaseline }) {
  const values = (baseline.seeds ?? []).map((s) => s.total_return_pct).filter((v): v is number => v !== null);
  const real = baseline.real?.total_return_pct ?? null;
  if (values.length === 0) return null;
  const all = real === null ? values : [...values, real];
  const low = Math.min(...all);
  const high = Math.max(...all);
  const width = (high - low) / HISTOGRAM_BINS || 1;
  const binOf = (v: number) => Math.min(HISTOGRAM_BINS - 1, Math.floor((v - low) / width));
  const counts = new Array<number>(HISTOGRAM_BINS).fill(0);
  values.forEach((v) => (counts[binOf(v)] += 1));
  const realBin = real === null ? -1 : binOf(real);
  const labels = counts.map((_, i) => `${(low + i * width).toFixed(1)}%`);
  return (
    <Bar
      data={{
        labels,
        datasets: [
          {
            label: 'Random-entry runs',
            data: counts,
            backgroundColor: counts.map((_, i) => (i === realBin ? GREEN : 'rgba(139, 143, 163, 0.55)')),
          },
        ],
      }}
      options={{
        responsive: true,
        animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => {
                const i = items[0]?.dataIndex ?? 0;
                return `${(low + i * width).toFixed(1)}% to ${(low + (i + 1) * width).toFixed(1)}%${i === realBin ? ' (contains the real run)' : ''}`;
              },
              label: (ctx) => `${ctx.parsed.y} random run${ctx.parsed.y === 1 ? '' : 's'}`,
            },
          },
        },
        scales: {
          x: { title: { display: true, text: 'Total return of a random run', color: MUTED }, ticks: { color: MUTED }, grid: { color: GRID } },
          y: { ticks: { color: MUTED, precision: 0 }, grid: { color: GRID }, beginAtZero: true },
        },
      }}
    />
  );
}
