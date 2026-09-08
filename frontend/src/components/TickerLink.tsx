import type { CSSProperties } from 'react';
import { Link } from 'react-router-dom';
import { CompanyIcon } from './CompanyIcon';

/** Every place a ticker symbol appears should be clickable through to its
 * chart — this is the one place that destination is decided. */
export function tickerHref(symbol: string): string {
  return `/analysis?symbol=${symbol}`;
}

export function TickerLink({
  symbol,
  iconSize,
  fontWeight = 600,
  style,
}: {
  symbol: string;
  iconSize?: number;
  fontWeight?: number;
  style?: CSSProperties;
}) {
  return (
    <Link to={tickerHref(symbol)} style={{ display: 'flex', alignItems: 'center', gap: 10, color: 'var(--text)', fontWeight, ...style }}>
      {iconSize && <CompanyIcon symbol={symbol} size={iconSize} />}
      {symbol}
    </Link>
  );
}
