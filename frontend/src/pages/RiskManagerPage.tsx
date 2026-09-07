import { useEffect, useState } from 'react';
import { useSearchParams } from 'react-router-dom';
import { useAnalysis, useCalculateRisk } from '../api/hooks';
import { SymbolPicker } from '../components/SymbolPicker';
import { CandlestickChart, type PriceLevel } from '../components/chart/CandlestickChart';
import { ErrorBanner, LoadingSpinner, formatMoney, formatNumber } from '../components/common';
import type { ApiError } from '../api/client';

export function RiskManagerPage() {
  const [params, setParams] = useSearchParams();
  const symbol = params.get('symbol') || 'AAPL';
  const { data: analysis, isLoading: analysisLoading } = useAnalysis(symbol);
  const { mutate: calculate, data: result, isPending, error } = useCalculateRisk();

  const [accountSize, setAccountSize] = useState(100_000);
  const [riskPct, setRiskPct] = useState(1);
  const [entry, setEntry] = useState<number | null>(null);
  const [stop, setStop] = useState<number | null>(null);
  const [direction, setDirection] = useState<'long' | 'short'>('long');

  useEffect(() => {
    if (analysis) {
      // Mirrors backend's _derive_entry_and_stop (trade_plan_service.py):
      // nearest level with a 1% buffer, not the raw level itself, so the
      // stop isn't placed right at-the-money when a support/resistance
      // cluster happens to sit close to the current price.
      const STOP_BUFFER_PCT = 0.01;
      setEntry(Math.round(analysis.price * 100) / 100);
      setDirection(analysis.trend === 'Bearish' ? 'short' : 'long');
      const fallbackStop = analysis.trend === 'Bearish' ? analysis.price * 1.03 : analysis.price * 0.97;
      const level = analysis.trend === 'Bearish' ? analysis.resistance[0] : analysis.support[0];
      const buffered = level !== undefined ? level * (analysis.trend === 'Bearish' ? 1 + STOP_BUFFER_PCT : 1 - STOP_BUFFER_PCT) : fallbackStop;
      setStop(Math.round(buffered * 100) / 100);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [analysis?.symbol]);

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (entry === null || stop === null) return;
    calculate({ symbol, account_size: accountSize, risk_pct: riskPct, entry, stop, direction });
  }

  const levels: PriceLevel[] =
    result && entry !== null && stop !== null
      ? [
          { price: entry, color: '#533afd', title: 'Entry' },
          { price: stop, color: '#e02424', title: 'Stop' },
          { price: result.tp1, color: '#0e9f6e', title: 'TP1' },
          { price: result.tp2, color: '#0e9f6e', title: 'TP2' },
        ]
      : [];

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        <h1 style={{ fontSize: 22 }}>Risk Manager</h1>
        <SymbolPicker value={symbol} onChange={(s) => setParams({ symbol: s })} />
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: '320px 1fr', gap: 20 }}>
        <form className="card" onSubmit={handleSubmit} style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          <div>
            <label>Account Size ($)</label>
            <input type="number" value={accountSize} onChange={(e) => setAccountSize(Number(e.target.value))} min={0} />
          </div>
          <div>
            <label>Risk per Trade (%)</label>
            <input type="number" value={riskPct} onChange={(e) => setRiskPct(Number(e.target.value))} min={0.1} step={0.1} />
          </div>
          <div>
            <label>Direction</label>
            <select value={direction} onChange={(e) => setDirection(e.target.value as 'long' | 'short')}>
              <option value="long">Long</option>
              <option value="short">Short</option>
            </select>
          </div>
          <div>
            <label>Entry Price</label>
            <input type="number" value={entry ?? ''} onChange={(e) => setEntry(Number(e.target.value))} step={0.01} />
          </div>
          <div>
            <label>Stop Loss</label>
            <input type="number" value={stop ?? ''} onChange={(e) => setStop(Number(e.target.value))} step={0.01} />
          </div>
          <button type="submit" className="btn btn-primary" disabled={isPending || entry === null || stop === null}>
            {isPending ? 'Calculating…' : 'Calculate'}
          </button>
          {error && <ErrorBanner message={(error as ApiError).message} />}
        </form>

        <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {analysisLoading && <LoadingSpinner label={`Loading ${symbol}…`} />}
          {analysis && (
            <div className="card">
              <CandlestickChart candles={analysis.candles} levels={levels} height={320} />
            </div>
          )}

          {result && (
            <div className="grid stat-grid">
              <div className="card">
                <div className="text-muted" style={{ fontSize: 12 }}>
                  Position Size
                </div>
                <div className="tabular-nums" style={{ fontWeight: 700, fontSize: 20 }}>
                  {result.shares} shares
                </div>
                {result.capped_by_cash && (
                  <div className="text-muted" style={{ fontSize: 11 }}>
                    reduced to fit available cash
                  </div>
                )}
              </div>
              <div className="card">
                <div className="text-muted" style={{ fontSize: 12 }}>
                  Account Risk
                </div>
                <div className="tabular-nums" style={{ fontWeight: 700, fontSize: 20 }}>
                  {formatMoney(result.account_risk_dollars)}
                </div>
              </div>
              <div className="card">
                <div className="text-muted" style={{ fontSize: 12 }}>
                  Risk:Reward (TP1)
                </div>
                <div className="tabular-nums" style={{ fontWeight: 700, fontSize: 20 }}>
                  {formatNumber(result.rr1)}:1
                </div>
              </div>
              <div className="card">
                <div className="text-muted" style={{ fontSize: 12 }}>
                  Potential Gain / Risk
                </div>
                <div className="tabular-nums" style={{ fontWeight: 700, fontSize: 16 }}>
                  <span className="text-green">{formatMoney(result.potential_gain)}</span> /{' '}
                  <span className="text-red">{formatMoney(result.potential_risk)}</span>
                </div>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
