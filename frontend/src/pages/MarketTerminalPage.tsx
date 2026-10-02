import { TradingViewWidget } from '../components/TradingViewWidget';
import { MacroPanel } from '../components/terminal/MacroPanel';
import { MarketRecap } from '../components/terminal/MarketRecap';
import { SectorHeatmap } from '../components/terminal/SectorHeatmap';

export function MarketTerminalPage() {
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 20 }}>
      <div className="page-header">
        <h1 style={{ margin: 0 }}>Market Terminal</h1>
        <div className="text-muted" style={{ fontSize: 13 }}>
          A read-only overview: how sectors moved, the macro backdrop, and a recap. Everything is computed from daily bars; nothing here places a trade.
        </div>
      </div>
      <TradingViewWidget kind="ticker-tape" height={90} compact />
      <MarketRecap />
      <SectorHeatmap />
      <MacroPanel />
    </div>
  );
}
