import { useEffect, useState } from 'react';

// Automated logo lookup by ticker symbol via Elbstream's free Stock Logo API
// (api.elbstream.com) — no key, no per-ticker mapping to maintain, so it
// keeps working as the bundled universe (sp500.csv) grows. Falls back to a
// deterministic colored monogram only if that request itself fails (unknown
// ticker, network hiccup) — never a broken-image icon.
// Free-tier requires visible attribution: see Sidebar.tsx's footer link.
//
// Elbstream is a *stock* logo API — it doesn't know "BTC-USD"/"ETH-USD"
// pairs, so every crypto symbol was silently falling through to the
// monogram circle. Crypto pairs (our sp500.csv convention: "<COIN>-USD")
// instead resolve against spothq/cryptocurrency-icons, a free/keyless,
// widely-used icon set covering BTC/ETH/SOL and more — same
// automated-lookup, no-per-ticker-mapping approach, just a different source
// for a different asset class.
const MONOGRAM_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16'];
const CRYPTO_ICON_BASE = 'https://cdn.jsdelivr.net/gh/spothq/cryptocurrency-icons@master/128/color';

function colorFor(symbol: string): string {
  let hash = 0;
  for (let i = 0; i < symbol.length; i++) hash = symbol.charCodeAt(i) + ((hash << 5) - hash);
  return MONOGRAM_COLORS[Math.abs(hash) % MONOGRAM_COLORS.length];
}

function logoUrlFor(symbol: string): string {
  if (symbol.endsWith('-USD')) {
    const coin = symbol.slice(0, -'-USD'.length).toLowerCase();
    return `${CRYPTO_ICON_BASE}/${coin}.png`;
  }
  return `https://api.elbstream.com/logos/symbol/${symbol}`;
}

// A free logo API sometimes answers a burst of image requests with an error for a second or two. Retry a couple of
// times (with a different URL so the browser does not reuse the failure) before settling on the letter circle.
const RETRY_DELAYS_MS = [1500, 5000];

export function CompanyIcon({ symbol, size = 28 }: { symbol: string; size?: number }) {
  const [failed, setFailed] = useState(false);
  const [attempt, setAttempt] = useState(0);

  useEffect(() => {
    setFailed(false);
    setAttempt(0);
  }, [symbol]);

  useEffect(() => {
    if (!failed || attempt >= RETRY_DELAYS_MS.length) return;
    const timer = setTimeout(() => {
      setAttempt((n) => n + 1);
      setFailed(false);
    }, RETRY_DELAYS_MS[attempt]);
    return () => clearTimeout(timer);
  }, [failed, attempt]);

  if (failed) {
    return (
      <div
        style={{
          width: size,
          height: size,
          borderRadius: '50%',
          background: colorFor(symbol),
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          fontSize: size * 0.42,
          fontWeight: 700,
          color: '#fff',
          flexShrink: 0,
        }}
      >
        {symbol[0]}
      </div>
    );
  }

  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: '50%',
        background: 'transparent',
        border: '1px solid var(--border)',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        padding: size * 0.1,
        overflow: 'hidden',
      }}
    >
      <img
        src={attempt === 0 ? logoUrlFor(symbol) : `${logoUrlFor(symbol)}?retry=${attempt}`}
        alt={symbol}
        style={{ width: '100%', height: '100%', objectFit: 'contain' }}
        onError={() => setFailed(true)}
      />
    </div>
  );
}
