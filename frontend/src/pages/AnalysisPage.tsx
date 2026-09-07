import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAnalysis } from '../api/hooks';
import { SymbolPicker } from '../components/SymbolPicker';
import { RangeTabs } from '../components/RangeTabs';
import { TrendBadge } from '../components/Badge';
import { CompanyIcon } from '../components/CompanyIcon';
import { CandlestickChart, type PriceLevel } from '../components/chart/CandlestickChart';
import { ErrorBanner, LoadingSpinner, formatMoney, formatNumber } from '../components/common';
import type { ApiError } from '../api/client';

export function AnalysisPage() {
  const [params, setParams] = useSearchParams();
  const symbol = params.get('symbol') || 'AAPL';
  const [range, setRange] = useState('3mo');
  const { data, isLoading, error } = useAnalysis(symbol, range);

  const levels: PriceLevel[] = data
    ? [
        ...data.support.map((price) => ({ price, color: '#10b981', title: 'Support' })),
        ...data.resistance.map((price) => ({ price, color: '#ef4444', title: 'Resistance' })),
      ]
    : [];

  // Same 2% proximity threshold the backend's scanner uses to score
  // "near a key level" — narrated here as a breakout callout, not a new signal.
  const nearestResistance = data?.resistance[0];
  const nearestSupport = data?.support[0];
  const isPotentialBreakout =
    !!data &&
    ((data.trend === 'Bullish' && nearestResistance !== undefined && Math.abs(nearestResistance - data.price) / data.price <= 0.02) ||
      (data.trend === 'Bearish' && nearestSupport !== undefined && Math.abs(nearestSupport - data.price) / data.price <= 0.02));

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1 style={{ fontSize: 22 }}>Chart Analysis</h1>
        <SymbolPicker value={symbol} onChange={(s) => setParams({ symbol: s })} />
      </div>

      {isLoading && <LoadingSpinner label={`Loading ${symbol}…`} />}
      {error && <ErrorBanner message={(error as ApiError).message} />}

      {data && (
        <>
          <div className="card" style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
              <CompanyIcon symbol={data.symbol} size={36} />
              <div>
                <div style={{ fontSize: 20, fontWeight: 700 }}>{data.symbol}</div>
                <div className="tabular-nums" style={{ fontSize: 15 }}>
                  {formatMoney(data.price)}
                </div>
              </div>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
              {isPotentialBreakout && <span className="badge badge-amber">Potential Breakout</span>}
              <TrendBadge trend={data.trend} />
            </div>
          </div>

          <div className="card">
            <div style={{ display: 'flex', justifyContent: 'flex-end', marginBottom: 12 }}>
              <RangeTabs value={range} onChange={setRange} />
            </div>
            <CandlestickChart candles={data.candles} ema20Series={data.ema20_series} ema50Series={data.ema50_series} levels={levels} />
          </div>

          <div className="grid stat-grid">
            <div className="card">
              <div className="text-muted" style={{ fontSize: 12 }}>
                Trend
              </div>
              <div style={{ fontWeight: 700, color: data.trend === 'Bullish' ? 'var(--green)' : data.trend === 'Bearish' ? 'var(--red)' : undefined }}>
                {data.trend}
              </div>
            </div>
            <div className="card">
              <div className="text-muted" style={{ fontSize: 12 }}>
                Momentum
              </div>
              <div style={{ fontWeight: 700 }}>{data.momentum}</div>
            </div>
            <div className="card">
              <div className="text-muted" style={{ fontSize: 12 }}>
                RSI(14)
              </div>
              <div className="tabular-nums" style={{ fontWeight: 700 }}>
                {formatNumber(data.rsi14, 0)}
              </div>
            </div>
            <div className="card">
              <div className="text-muted" style={{ fontSize: 12 }}>
                EMA20 / EMA50
              </div>
              <div className="tabular-nums" style={{ fontWeight: 700 }}>
                {formatMoney(data.ema20)} / {formatMoney(data.ema50)}
              </div>
            </div>
          </div>

          <div className="card" style={{ background: 'var(--canvas)', border: '1px solid var(--border)' }}>
            <div className="text-muted" style={{ fontSize: 12, marginBottom: 6 }}>
              AI Insight {data.ai_provider !== 'none' ? `· ${data.ai_provider}` : '· rule-based'}
            </div>
            <div>{data.insight_text}</div>
            {data.ai_error && (
              <div className="text-muted" style={{ fontSize: 12, marginTop: 6 }}>
                ({data.ai_error})
              </div>
            )}
          </div>
        </>
      )}
    </div>
  );
}
