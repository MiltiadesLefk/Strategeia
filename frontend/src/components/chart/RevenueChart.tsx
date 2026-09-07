import { Bar } from 'react-chartjs-2';
import './chartSetup';
import type { FinancialYear } from '../../api/types';

export function RevenueChart({ years }: { years: FinancialYear[] }) {
  const data = {
    labels: years.map((y) => String(y.year)),
    datasets: [
      { label: 'Revenue', data: years.map((y) => y.revenue), backgroundColor: '#533afd', borderRadius: 4 },
      { label: 'Net Income', data: years.map((y) => y.net_income), backgroundColor: '#9aa4c4', borderRadius: 4 },
    ],
  };

  return (
    <Bar
      data={data}
      options={{
        responsive: true,
        animation: false,
        plugins: {
          legend: { position: 'top', labels: { boxWidth: 10, font: { size: 12 } } },
          tooltip: {
            callbacks: {
              label: (ctx) => `${ctx.dataset.label}: ${new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact' }).format(ctx.parsed.y ?? 0)}`,
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
