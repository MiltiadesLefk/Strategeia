import { useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAnalysis } from '../api/hooks';
import { SymbolPicker } from '../components/SymbolPicker';
import { RangeTabs } from '../components/RangeTabs';
import { TrendBadge } from '../components/Badge';
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
        ...data.support.map((price) => ({ price, color: '#0e9f6e', title: 'Support' })),
        ...data.resistance.map((price) => ({ price, color: '#e02424', title: 'Resistance' })),
      ]
    : [];

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
            <div>
              <div style={{ fontSize: 20, fontWeight: 700 }}>{data.symbol}</div>
              <div className="tabular-nums" style={{ fontSize: 15 }}>
                {formatMoney(data.price)}
              </div>
            </div>
            <TrendBadge trend={data.trend} />
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
