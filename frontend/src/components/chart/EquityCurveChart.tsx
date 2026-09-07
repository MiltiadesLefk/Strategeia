import { Line } from 'react-chartjs-2';
import './chartSetup';
import type { EquityPoint } from '../../api/types';

export function EquityCurveChart({ points }: { points: EquityPoint[] }) {
  const data = {
    labels: points.map((p) => new Date(p.timestamp).toLocaleDateString()),
    datasets: [
      {
        label: 'Portfolio Value',
        data: points.map((p) => p.equity_value),
        borderColor: '#0e9f6e',
        backgroundColor: 'rgba(14, 159, 110, 0.12)',
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
          y: {
            ticks: {
              callback: (value) => new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact' }).format(Number(value)),
            },
          },
        },
      }}
    />
  );
}
