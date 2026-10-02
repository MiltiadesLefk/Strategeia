// Idea from OpenTerminal and OpenStock: embed TradingView's free widgets. No
// code was copied (OpenStock is AGPL-3.0). The component never loads anything
// by itself: nothing is requested from TradingView until the button is clicked,
// and the widget then runs in a sandboxed cross-origin iframe (see lib/tradingView.ts).
import { useMemo, useState } from 'react';
import { TV_HOME_URL, TV_NOTICE_KEY, buildWidgetConfig, buildEmbedUrl, type TvWidgetKind } from '../lib/tradingView';

function readNoticeSeen(): boolean {
  try {
    return localStorage.getItem(TV_NOTICE_KEY) === '1';
  } catch {
    return false; // private window or blocked storage: show the notice every time
  }
}

function rememberNotice() {
  try {
    localStorage.setItem(TV_NOTICE_KEY, '1');
  } catch {
    // not remembering is fine
  }
}

const KIND_LABEL: Record<TvWidgetKind, string> = {
  'symbol-overview': 'TradingView chart',
  'technical-analysis': 'TradingView technical summary',
  'mini-chart': 'TradingView mini chart',
  'ticker-tape': 'TradingView ticker tape',
};

export function TradingViewWidget({
  kind,
  symbol,
  height = 420,
  compact = false,
}: {
  kind: TvWidgetKind;
  /** Our ticker (not TradingView's name): mapped inside. Not used by the ticker tape. */
  symbol?: string | null;
  height?: number;
  /** One-line layout for the strip on the Market Terminal. */
  compact?: boolean;
}) {
  // Always starts unloaded, on every visit: a remembered preference is only that the
  // notice was read. A third-party script is never started without a click.
  const [loaded, setLoaded] = useState(false);
  const [noticeSeen, setNoticeSeen] = useState(readNoticeSeen);
  const label = KIND_LABEL[kind];

  const src = useMemo(() => {
    if (!loaded) return null;
    const config = buildWidgetConfig(kind, symbol);
    return config ? buildEmbedUrl(kind, config) : null;
  }, [loaded, kind, symbol]);

  const supported = kind === 'ticker-tape' || buildWidgetConfig(kind, symbol) !== null;

  function load() {
    rememberNotice();
    setNoticeSeen(true);
    setLoaded(true);
  }

  if (!supported) {
    return (
      <div className="card text-muted" style={{ fontSize: 13 }}>
        TradingView has no matching symbol for {symbol ?? 'this ticker'}, so this widget is not offered.
      </div>
    );
  }

  if (!loaded || !src) {
    return (
      <div className="card" style={{ display: 'flex', flexDirection: compact ? 'row' : 'column', alignItems: compact ? 'center' : 'flex-start', gap: 12, flexWrap: 'wrap' }}>
        <button type="button" className="btn btn-secondary" onClick={load} data-tradingview-load={kind}>
          Load {label}
        </button>
        <div className="text-muted" style={{ fontSize: 12, maxWidth: 640 }}>
          {noticeSeen
            ? 'Third-party content from TradingView. Loaded only when you click.'
            : "This is third-party content from TradingView. Clicking loads a TradingView frame, which sends your browser's request (your IP address and the symbol shown) to TradingView's servers, outside this app. Nothing is loaded until you click."}
        </div>
      </div>
    );
  }

  return (
    <div className="card" style={{ display: 'flex', flexDirection: 'column', gap: 8, padding: compact ? 8 : undefined }}>
      <iframe
        title={label}
        src={src}
        // The frame is on TradingView's own origin, so allow-same-origin only lets it use its own storage
        // (the widget needs that); it can never touch this app's. No top navigation, forms or downloads.
        sandbox="allow-scripts allow-same-origin allow-popups allow-popups-to-escape-sandbox"
        referrerPolicy="no-referrer"
        loading="lazy"
        style={{ width: '100%', height, border: 'none', borderRadius: 8, background: 'var(--card)' }}
        data-tradingview-frame={kind}
      />
      <div className="text-muted" style={{ fontSize: 11, display: 'flex', justifyContent: 'space-between', gap: 8, flexWrap: 'wrap' }}>
        <span>
          {label} provided by{' '}
          <a href={TV_HOME_URL} target="_blank" rel="noopener noreferrer">
            TradingView
          </a>
          . Its data is TradingView's, not this app's, and can differ from the numbers elsewhere here.
        </span>
        <button type="button" className="btn btn-secondary" style={{ padding: '2px 8px', fontSize: 11 }} onClick={() => setLoaded(false)}>
          Unload
        </button>
      </div>
    </div>
  );
}
