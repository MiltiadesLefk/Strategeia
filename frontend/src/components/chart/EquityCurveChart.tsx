import { Line } from 'react-chartjs-2';
import './chartSetup';
import type { EquityPoint } from '../../api/types';

const MUTED = '#8b8fa3';
const GRID = '#1c1f29';

export function EquityCurveChart({ points }: { points: EquityPoint[] }) {
  const data = {
    labels: points.map((p) => new Date(p.timestamp).toLocaleDateString()),
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
              label: (ctx) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD' }).format(ctx.parsed.y ?? 0),
            },
          },
        },
        scales: {
          x: { ticks: { color: MUTED }, grid: { color: GRID } },
          y: {
            ticks: {
              color: MUTED,
              callback: (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact' }).format(Number(value)),
            },
            grid: { color: GRID },
          },
        },
      }}
    />
  );
}
