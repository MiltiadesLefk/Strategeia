import { useState } from 'react';

// Best-effort mapping to Simple Icons (simpleicons.org) slugs for tickers in
// our bundled universe. Unmapped tickers, and any slug that 404s, silently
// fall back to a colored monogram below — never a broken-image icon.
const ICON_SLUGS: Record<string, string> = {
  AAPL: 'apple',
  MSFT: 'microsoft',
  GOOGL: 'google',
  AMZN: 'amazon',
  NVDA: 'nvidia',
  META: 'meta',
  TSLA: 'tesla',
  V: 'visa',
  WMT: 'walmart',
  MA: 'mastercard',
  KO: 'cocacola',
  PEP: 'pepsi',
  AVGO: 'broadcom',
  ADBE: 'adobe',
  CRM: 'salesforce',
  MCD: 'mcdonalds',
  CSCO: 'cisco',
  ACN: 'accenture',
  NKE: 'nike',
  AMD: 'amd',
  INTC: 'intel',
  VZ: 'verizon',
  CMCSA: 'comcast',
  INTU: 'intuit',
  IBM: 'ibm',
  NOW: 'servicenow',
  QCOM: 'qualcomm',
  SPGI: 'spglobal',
  GE: 'generalelectric',
  CAT: 'caterpillar',
};

const MONOGRAM_COLORS = ['#3b82f6', '#10b981', '#f59e0b', '#ef4444', '#8b5cf6', '#06b6d4', '#ec4899', '#84cc16'];

function colorFor(symbol: string): string {
  let hash = 0;
  for (let i = 0; i < symbol.length; i++) hash = symbol.charCodeAt(i) + ((hash << 5) - hash);
  return MONOGRAM_COLORS[Math.abs(hash) % MONOGRAM_COLORS.length];
}

export function CompanyIcon({ symbol, size = 28 }: { symbol: string; size?: number }) {
  const slug = ICON_SLUGS[symbol];
  const [failed, setFailed] = useState(false);

  if (!slug || failed) {
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
        background: '#fff',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        flexShrink: 0,
        padding: size * 0.16,
      }}
    >
      <img
        src={`https://cdn.simpleicons.org/${slug}`}
        alt={symbol}
        style={{ width: '100%', height: '100%', objectFit: 'contain' }}
        onError={() => setFailed(true)}
      />
    </div>
  );
}
