import { useEffect, useRef } from 'react';
import { CandlestickSeries, LineSeries, createChart, type IChartApi, type ISeriesApi, type UTCTimestamp } from 'lightweight-charts';
import type { Candle, SeriesPoint } from '../../api/types';

function toTime(date: string): UTCTimestamp {
  return (new Date(`${date}T00:00:00Z`).getTime() / 1000) as UTCTimestamp;
}

export interface PriceLevel {
  price: number;
  color: string;
  title: string;
}

interface Props {
  candles: Candle[];
  ema20Series?: SeriesPoint[];
  ema50Series?: SeriesPoint[];
  levels?: PriceLevel[];
  height?: number;
}

export function CandlestickChart({ candles, ema20Series, ema50Series, levels, height = 380 }: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      height,
      layout: { background: { color: '#ffffff' }, textColor: '#0d253d', attributionLogo: false },
      grid: { vertLines: { color: '#f0f1f5' }, horzLines: { color: '#f0f1f5' } },
      timeScale: { borderColor: '#e6e8f0' },
      rightPriceScale: { borderColor: '#e6e8f0' },
    });
    chartRef.current = chart;

    const candleSeries: ISeriesApi<'Candlestick'> = chart.addSeries(CandlestickSeries, {
      upColor: '#0e9f6e',
      downColor: '#e02424',
      borderVisible: false,
      wickUpColor: '#0e9f6e',
      wickDownColor: '#e02424',
    });
    candleSeries.setData(
      candles.map((c) => ({ time: toTime(c.date), open: c.open, high: c.high, low: c.low, close: c.close })),
    );

    if (ema20Series?.length) {
      const s = chart.addSeries(LineSeries, { color: '#533afd', lineWidth: 2, title: 'EMA20' });
      s.setData(ema20Series.map((p) => ({ time: toTime(p.date), value: p.value })));
    }
    if (ema50Series?.length) {
      const s = chart.addSeries(LineSeries, { color: '#b45309', lineWidth: 2, title: 'EMA50' });
      s.setData(ema50Series.map((p) => ({ time: toTime(p.date), value: p.value })));
    }
    for (const level of levels ?? []) {
      candleSeries.createPriceLine({
        price: level.price,
        color: level.color,
        lineWidth: 1,
        lineStyle: 2,
        title: level.title,
      });
    }

    chart.timeScale().fitContent();

    const resizeObserver = new ResizeObserver((entries) => {
      const width = entries[0]?.contentRect.width;
      if (width) chart.applyOptions({ width });
    });
    resizeObserver.observe(containerRef.current);

    return () => {
      resizeObserver.disconnect();
      chart.remove();
      chartRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, ema20Series, ema50Series, levels, height]);

  return <div ref={containerRef} style={{ width: '100%' }} />;
}
