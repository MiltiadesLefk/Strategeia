import { Bar } from 'react-chartjs-2';
import './chartSetup';
import type { FinancialYear } from '../../api/types';

const MUTED = '#8b8fa3';
const GRID = '#1c1f29';

export function RevenueChart({ years }: { years: FinancialYear[] }) {
  const data = {
    labels: years.map((y) => String(y.year)),
    datasets: [
      { label: 'Revenue', data: years.map((y) => y.revenue), backgroundColor: '#3b82f6', borderRadius: 4 },
      { label: 'Net Income', data: years.map((y) => y.net_income), backgroundColor: '#4b5065', borderRadius: 4 },
    ],
  };

  return (
    <Bar
      data={data}
      options={{
        responsive: true,
        animation: false,
        plugins: {
          legend: { position: 'top', labels: { boxWidth: 10, font: { size: 12 }, color: MUTED } },
          tooltip: {
            callbacks: {
              label: (ctx) => `${ctx.dataset.label}: ${new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact' }).format(ctx.parsed.y ?? 0)}`,
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
