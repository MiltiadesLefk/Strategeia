import { Line } from 'react-chartjs-2';
import './chartSetup';
import type { EquityPoint } from '../../api/types';

const MUTED = '#8b8fa3';
const GRID = '#1c1f29';

const DAY_MS = 24 * 60 * 60 * 1000;

/**
 * Axis labels have to resolve the values they're labelling.
 *
 * The Y axis used `notation: 'compact'`, which renders an account moving
 * between $99,411 and $100,000 as six identical "$100K" ticks — a scale with
 * no information in it. The X axis used `toLocaleDateString()` alone, so a
 * series of same-day snapshots printed the same date on every tick.
 *
 * Both now adapt to the range actually being drawn: full dollars on Y, and a
 * time-of-day X label when the whole series fits inside a couple of days.
 */
export function EquityCurveChart({ points }: { points: EquityPoint[] }) {
  const times = points.map((p) => new Date(p.timestamp).getTime());
  const spanMs = times.length > 1 ? Math.max(...times) - Math.min(...times) : 0;
  const intraday = spanMs > 0 && spanMs < 2 * DAY_MS;

  const labels = points.map((p) => {
    const at = new Date(p.timestamp);
    if (intraday) return at.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
    if (spanMs < 14 * DAY_MS) {
      return at.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: 'numeric' });
    }
    return at.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
  });

  const data = {
    labels,
    datasets: [
      {
        label: 'Portfolio Value',
        data: points.map((p) => p.equity_value),
        borderColor: '#10b981',
        backgroundColor: 'rgba(16, 185, 129, 0.14)',
        tension: 0.3,
        pointRadius: 0,
        fill: true,
      },
    ],
  };

  return (
    <Line
      data={data}
      options={{
        responsive: true,
        animation: false,
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              title: (items) => {
                const point = points[items[0]?.dataIndex ?? 0];
                return point ? new Date(point.timestamp).toLocaleString() : '';
              },
              label: (ctx) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(ctx.parsed.y ?? 0),
            },
          },
        },
        scales: {
          x: { ticks: { color: MUTED, maxTicksLimit: 8, autoSkip: true }, grid: { color: GRID } },
          y: {
            ticks: {
              color: MUTED,
              // Full dollars, not compact: the interesting moves in a paper
              // account are usually smaller than compact notation can express.
              callback: (value) =>
                new Intl.NumberFormat('en-US', {
                  style: 'currency',
                  currency: 'USD',
                  maximumFractionDigits: 0,
                }).format(Number(value)),
            },
            grid: { color: GRID },
          },
        },
      }}
    />
  );
}
