import { useEffect, useRef, useState } from 'react';
import {
  CandlestickSeries,
  HistogramSeries,
  LineSeries,
  TickMarkType,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type Time,
  type UTCTimestamp,
} from 'lightweight-charts';
import type { Candle, SeriesPoint } from '../../api/types';
import { formatEtCrosshair, formatEtTick } from '../../lib/intraday';

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
  bollingerUpperSeries?: SeriesPoint[];
  bollingerLowerSeries?: SeriesPoint[];
  /** Intraday only — empty on daily ranges by design, see indicators.session_vwap. */
  vwapSeries?: SeriesPoint[];
  levels?: PriceLevel[];
  height?: number;
}

const COLORS = {
  ema20: '#38bdf8',
  ema50: '#a78bfa',
  band: 'rgba(139, 143, 163, 0.55)',
  vwap: '#f59e0b',
};

/** What the crosshair is currently sitting on. */
interface HoverRow {
  label: string;
  value: string;
  color?: string;
}

function formatPrice(value: number): string {
  return value.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function formatVolume(value: number): string {
  if (value >= 1e9) return `${(value / 1e9).toFixed(2)}B`;
  if (value >= 1e6) return `${(value / 1e6).toFixed(2)}M`;
  if (value >= 1e3) return `${(value / 1e3).toFixed(1)}K`;
  return String(Math.round(value));
}

export function CandlestickChart({
  candles,
  ema20Series,
  ema50Series,
  bollingerUpperSeries,
  bollingerLowerSeries,
  vwapSeries,
  levels,
  height = 380,
}: Props) {
  const containerRef = useRef<HTMLDivElement>(null);
  const chartRef = useRef<IChartApi | null>(null);
  // A readout of the bar under the cursor. Without it the chart could show a
  // shape but never a number — you could see that price crossed EMA20 and
  // still not know either value.
  const [hover, setHover] = useState<{ time: string; rows: HoverRow[] } | null>(null);

  // Intraday bars (1D/1W ranges) carry a full ISO datetime; show time-of-day
  // on the axis for those instead of just the date.
  const intraday = candles.length > 0 && candles[0].date.includes('T');

  useEffect(() => {
    if (!containerRef.current) return;

    const chart = createChart(containerRef.current, {
      height,
      layout: { background: { color: '#12151e' }, textColor: '#8b8fa3', attributionLogo: false },
      grid: { vertLines: { color: '#1c202c' }, horzLines: { color: '#1c202c' } },
      timeScale: {
        borderColor: '#1e2332',
        timeVisible: intraday,
        secondsVisible: false,
        // Intraday only: lightweight-charts labels every timestamp as UTC, so a 9:30 am open
        // read 1:30 pm. Daily ranges keep the default, which is correct for a bare date.
        ...(intraday
          ? {
              tickMarkFormatter: (time: Time, type: TickMarkType) => {
                const seconds = time as number;
                if (type === TickMarkType.Year) return formatEtTick(seconds, 'year');
                if (type === TickMarkType.Month) return formatEtTick(seconds, 'month');
                if (type === TickMarkType.DayOfMonth) return formatEtTick(seconds, 'day');
                return formatEtTick(seconds, 'time');
              },
            }
          : {}),
      },
      ...(intraday ? { localization: { timeFormatter: (time: Time) => formatEtCrosshair(time as number) } } : {}),
      rightPriceScale: { borderColor: '#1e2332', scaleMargins: { top: 0.08, bottom: 0.22 } },
      crosshair: { mode: 0 },
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

    // Overlays, tracked so the crosshair handler can read each one's value at
    // the hovered bar rather than only showing OHLC.
    const overlays: { key: string; label: string; color: string; series: ISeriesApi<'Line'> }[] = [];
    const addLine = (
      key: string,
      label: string,
      color: string,
      data: SeriesPoint[] | undefined,
      opts: { lineWidth?: 1 | 2; lineStyle?: number } = {},
    ) => {
      if (!data?.length) return;
      const series = chart.addSeries(LineSeries, {
        color,
        lineWidth: opts.lineWidth ?? 2,
        lineStyle: opts.lineStyle ?? 0,
        title: label,
        lastValueVisible: false,
        priceLineVisible: false,
      });
      series.setData(data.map((p) => ({ time: toTime(p.date), value: p.value })));
      overlays.push({ key, label, color, series });
    };

    addLine('ema20', 'EMA20', COLORS.ema20, ema20Series);
    addLine('ema50', 'EMA50', COLORS.ema50, ema50Series);
    // Bands are dashed and thin so they read as context, not as signal lines
    // competing with the EMAs.
    addLine('bbU', 'BB↑', COLORS.band, bollingerUpperSeries, { lineWidth: 1, lineStyle: 2 });
    addLine('bbL', 'BB↓', COLORS.band, bollingerLowerSeries, { lineWidth: 1, lineStyle: 2 });
    addLine('vwap', 'VWAP', COLORS.vwap, vwapSeries, { lineWidth: 2 });

    for (const level of levels ?? []) {
      candleSeries.createPriceLine({
        price: level.price,
        color: level.color,
        lineWidth: 1,
        lineStyle: 2,
        title: level.title,
      });
    }

    const byTime = new Map(candles.map((c) => [toTime(c.date), c]));

    chart.subscribeCrosshairMove((param) => {
      if (param.time === undefined || !param.point) {
        setHover(null);
        return;
      }
      const candle = byTime.get(param.time as UTCTimestamp);
      if (!candle) {
        setHover(null);
        return;
      }
      const change = candle.close - candle.open;
      const changePct = candle.open ? (change / candle.open) * 100 : 0;

      const rows: HoverRow[] = [
        { label: 'O', value: formatPrice(candle.open) },
        { label: 'H', value: formatPrice(candle.high) },
        { label: 'L', value: formatPrice(candle.low) },
        {
          label: 'C',
          value: `${formatPrice(candle.close)} (${change >= 0 ? '+' : ''}${changePct.toFixed(2)}%)`,
          color: change >= 0 ? '#10b981' : '#ef4444',
        },
        { label: 'Vol', value: formatVolume(candle.volume) },
      ];

      for (const overlay of overlays) {
        const point = param.seriesData.get(overlay.series) as { value?: number } | undefined;
        if (point?.value !== undefined) {
          rows.push({ label: overlay.label, value: formatPrice(point.value), color: overlay.color });
        }
      }

      const at = new Date((param.time as number) * 1000);
      setHover({
        time: intraday
          ? formatEtCrosshair(param.time as number)
          : at.toLocaleDateString(undefined, { year: 'numeric', month: 'short', day: 'numeric' }),
        rows,
      });
    });

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
      setHover(null);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [candles, ema20Series, ema50Series, bollingerUpperSeries, bollingerLowerSeries, vwapSeries, levels, height]);

  return (
    <div style={{ position: 'relative', width: '100%' }}>
      <div ref={containerRef} style={{ width: '100%' }} />
      {intraday && (
        <div className="text-muted" style={{ fontSize: 11, textAlign: 'right', marginTop: 4 }}>
          Time axis: ET (US Eastern)
        </div>
      )}
      {hover && (
        <div
          // pointerEvents none: the legend sits over the plot area, and must
          // never swallow the mouse move that produced it.
          style={{
            position: 'absolute',
            top: 8,
            left: 8,
            // lightweight-charts stacks its own canvases inside the container;
            // without an explicit z-index this legend sits in the DOM,
            // correctly positioned and fully opaque, and is painted straight
            // over by the chart.
            zIndex: 5,
            pointerEvents: 'none',
            display: 'flex',
            flexWrap: 'wrap',
            alignItems: 'center',
            gap: '2px 10px',
            maxWidth: 'calc(100% - 16px)',
            padding: '6px 9px',
            borderRadius: 8,
            background: 'rgba(11, 13, 19, 0.86)',
            border: '1px solid var(--border)',
            fontSize: 11,
            lineHeight: 1.5,
          }}
          className="tabular-nums"
        >
          <span className="text-muted" style={{ fontWeight: 600 }}>
            {hover.time}
          </span>
          {hover.rows.map((row) => (
            <span key={row.label} style={{ whiteSpace: 'nowrap' }}>
              <span className="text-muted">{row.label}</span>{' '}
              <span style={{ color: row.color ?? 'var(--text)', fontWeight: 600 }}>{row.value}</span>
            </span>
          ))}
        </div>
      )}
    </div>
  );
}
