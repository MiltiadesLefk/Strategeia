import type { ReactNode } from 'react';

export function LoadingSpinner({ label = 'Loading…' }: { label?: string }) {
  return <div className="text-muted" style={{ padding: 24, textAlign: 'center' }}>{label}</div>;
}

export function ErrorBanner({ message }: { message: string }) {
  return (
    <div
      className="badge-red"
      style={{ borderRadius: 8, padding: '12px 16px', fontWeight: 500, fontSize: 14 }}
    >
      {message}
    </div>
  );
}

export function EmptyState({ children }: { children: ReactNode }) {
  return (
    <div className="text-muted" style={{ padding: '32px 16px', textAlign: 'center' }}>
      {children}
    </div>
  );
}

export function formatMoney(value: number | null | undefined, opts?: { compact?: boolean }): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  if (opts?.compact) {
    return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', notation: 'compact', maximumFractionDigits: 2 }).format(value);
  }
  return new Intl.NumberFormat('en-US', { style: 'currency', currency: 'USD', maximumFractionDigits: 2 }).format(value);
}

export function formatPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${value >= 0 ? '+' : ''}${value.toFixed(digits)}%`;
}

export function formatNumber(value: number | null | undefined, digits = 2): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return value.toFixed(digits);
}

export function formatRelativeTime(iso: string | null | undefined): string {
  if (!iso) return '—';
  const then = new Date(iso).getTime();
  if (Number.isNaN(then)) return '—';
  const minutes = Math.round((Date.now() - then) / 60_000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** Only http(s) URLs are safe to render as a clickable <a href>. News/link
 * fields come from third-party data providers (yfinance/Finnhub) — an
 * unvalidated `javascript:`/`data:` URL there would execute on click. */
export function isSafeHttpUrl(url: string): boolean {
  try {
    const parsed = new URL(url);
    return parsed.protocol === 'http:' || parsed.protocol === 'https:';
  } catch {
    return false;
  }
}
