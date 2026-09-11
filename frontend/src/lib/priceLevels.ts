import type { PriceLevel } from '../components/chart/CandlestickChart';

// Same 2% proximity threshold the backend's scanner uses to score "near a
// key level" — narrated here as a breakout callout, not a new signal.
const BREAKOUT_PROXIMITY_PCT = 0.02;

interface ChartLevels {
  price: number;
  trend: string;
  support: number[];
  resistance: number[];
}

export function supportResistanceLevels(data: Pick<ChartLevels, 'support' | 'resistance'>): PriceLevel[] {
  return [
    ...data.support.map((price) => ({ price, color: '#10b981', title: 'Support' })),
    ...data.resistance.map((price) => ({ price, color: '#ef4444', title: 'Resistance' })),
  ];
}

export function isPotentialBreakout(data: ChartLevels): boolean {
  const nearestResistance = data.resistance[0];
  const nearestSupport = data.support[0];
  return (
    (data.trend === 'Bullish' && nearestResistance !== undefined && Math.abs(nearestResistance - data.price) / data.price <= BREAKOUT_PROXIMITY_PCT) ||
    (data.trend === 'Bearish' && nearestSupport !== undefined && Math.abs(nearestSupport - data.price) / data.price <= BREAKOUT_PROXIMITY_PCT)
  );
}
