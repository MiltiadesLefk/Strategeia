import { useEffect, useRef } from 'react';
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { Candle, SeriesPoint } from '../../api/types';

function toTime(date: string): UTCTimestamp {
  // Daily bars arrive as a bare "YYYY-MM-DD"; intraday bars (1D/1W ranges)
  // arrive as a full ISO datetime with time-of-day and offset already
  // included — only the bare-date form needs a time appended.
  const iso = date.includes('T') ? date : `${date}T00:00:00Z`;
  return (new Date(iso).getTime() / 1000) as UTCTimestamp;
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
  // Intraday bars (1D/1W ranges) carry a full ISO datetime; show time-of-day
  // on the axis for those instead of just the date.
  const intraday = candles.length > 0 && candles[0].date.includes('T');

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      height,
      layout: { background: { color: '#12151e' }, textColor: '#8b8fa3', attributionLogo: false },
      grid: { vertLines: { color: '#1c202c' }, horzLines: { color: '#1c202c' } },
      timeScale: { borderColor: '#1e2332', timeVisible: intraday, secondsVisible: false },
      rightPriceScale: { borderColor: '#1e2332', scaleMargins: { top: 0.08, bottom: 0.22 } },
    });
    chartRef.current = chart;

    const candleSeries: ISeriesApi<'Candlestick'> = chart.addSeries(CandlestickSeries, {
      upColor: '#10b981',
      downColor: '#ef4444',
      borderVisible: false,
      wickUpColor: '#10b981',
      wickDownColor: '#ef4444',
    });
    candleSeries.setData(
      candles.map((c) => ({ time: toTime(c.date), open: c.open, high: c.high, low: c.low, close: c.close })),
    );

    const volumeSeries = chart.addSeries(HistogramSeries, {
      priceFormat: { type: 'volume' },
      priceScaleId: 'volume',
      lastValueVisible: false,
      priceLineVisible: false,
    });
    volumeSeries.priceScale().applyOptions({ scaleMargins: { top: 0.82, bottom: 0 } });
    volumeSeries.setData(
      candles.map((c) => ({
        time: toTime(c.date),
        value: c.volume,
        color: c.close >= c.open ? 'rgba(16, 185, 129, 0.5)' : 'rgba(239, 68, 68, 0.5)',
      })),
    );

    if (ema20Series?.length) {
      const s = chart.addSeries(LineSeries, { color: '#38bdf8', lineWidth: 2, title: 'EMA20' });
      s.setData(ema20Series.map((p) => ({ time: toTime(p.date), value: p.value })));
    }
    if (ema50Series?.length) {
      const s = chart.addSeries(LineSeries, { color: '#a78bfa', lineWidth: 2, title: 'EMA50' });
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
