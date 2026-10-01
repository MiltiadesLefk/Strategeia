// Copyright (c) 2026 OpenTerminal contributors. MIT License; full text in THIRD_PARTY_NOTICES.md.
// Adapted from ErTasselli/OpenTerminal@95618ee web/components/widgets/InsiderWidget.tsx; changes: reads our stored,
// dated Form 4 rows instead of a live proxy; shows both the trade date and the SEC acceptance date with the delay
// between them, role badges, a pre-arranged-plan chip and a source link; the code labels come from the same idea.
import type { ReactNode } from 'react';
import type { SmartMoneyInsiderTrade } from '../../api/types';
import { TickerLink } from '../TickerLink';
import { formatMoney, formatNumber, isSafeHttpUrl } from '../common';

// SEC's single-letter transaction codes. Only P and S are ever listed here; the rest are
// kept so a row the backend might one day return still reads sensibly.
const CODE_LABEL: Record<string, string> = {
  P: 'Open-market buy',
  S: 'Open-market sale',
  A: 'Grant or award',
  M: 'Option exercise',
  G: 'Gift',
  F: 'Tax withholding',
};

export function RoleBadges({ tags }: { tags: string[] }): ReactNode {
  return (
    <>
      {tags.map((tag) => (
        <span
          key={tag}
          className={`badge ${tag === 'CEO' || tag === 'CFO' ? 'badge-amber' : 'badge-neutral'}`}
          style={{ marginRight: 4 }}
        >
          {tag}
        </span>
      ))}
    </>
  );
}

/** "2026-09-24 20:00 UTC": the SEC acceptance time, shown as the moment the trade became public. */
export function formatAccepted(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return `${d.toISOString().slice(0, 10)} ${d.toISOString().slice(11, 16)} UTC`;
}

function delayText(days: number | null): string {
  if (days === null) return '';
  if (days <= 0) return 'filed same day';
  return `filed ${days} day${days === 1 ? '' : 's'} after`;
}

export function InsiderTradesTable({ trades, showSymbol = true }: { trades: SmartMoneyInsiderTrade[]; showSymbol?: boolean }) {
  return (
    <div style={{ overflowX: 'auto' }}>
      <table>
        <thead>
          <tr>
            {showSymbol && <th>Symbol</th>}
            <th>Insider</th>
            <th>Type</th>
            <th style={{ textAlign: 'right' }}>Shares</th>
            <th style={{ textAlign: 'right' }}>Price</th>
            <th style={{ textAlign: 'right' }}>Value</th>
            <th title="The date of the trade itself">Traded</th>
            <th title="When SEC accepted the filing: the first moment anyone could see the trade">Public since</th>
            <th>Source</th>
          </tr>
        </thead>
        <tbody>
          {trades.map((t, i) => (
            <tr key={`${t.symbol}-${t.filing_url}-${t.insider}-${t.transaction_date}-${i}`}>
              {showSymbol && (
                <td>
                  <TickerLink symbol={t.symbol} />
                </td>
              )}
              <td>
                <div style={{ fontWeight: 600 }}>{t.insider ?? '—'}</div>
                <div style={{ marginTop: 2 }} title={t.officer_title ?? undefined}>
                  <RoleBadges tags={t.role_tags} />
                </div>
              </td>
              <td>
                <span className={`badge ${t.side === 'buy' ? 'badge-green' : t.side === 'sell' ? 'badge-red' : 'badge-neutral'}`}>
                  {t.side === 'buy' ? 'Buy' : t.side === 'sell' ? 'Sell' : (t.code ?? 'Other')}
                </span>
                <div className="text-muted" style={{ fontSize: 11, marginTop: 2 }}>
                  {t.code ? (CODE_LABEL[t.code] ?? `Code ${t.code}`) : ''}
                </div>
                {t.is_10b5_1 && (
                  <span
                    className="badge badge-amber"
                    style={{ marginTop: 4 }}
                    title="Made under a pre-arranged trading plan (Rule 10b5-1), set up months earlier: not a fresh view."
                  >
                    10b5-1 plan
                  </span>
                )}
              </td>
              <td className="tabular-nums" style={{ textAlign: 'right' }}>
                {formatNumber(t.shares, 0)}
              </td>
              <td className="tabular-nums" style={{ textAlign: 'right' }}>
                {t.price === null ? '—' : formatMoney(t.price)}
              </td>
              <td className="tabular-nums" style={{ textAlign: 'right', fontWeight: 600 }}>
                {t.value === null ? 'no price' : formatMoney(t.value, { compact: true })}
              </td>
              <td className="tabular-nums" style={{ whiteSpace: 'nowrap' }}>
                {t.transaction_date ?? '—'}
              </td>
              <td className="tabular-nums" style={{ whiteSpace: 'nowrap' }}>
                {formatAccepted(t.known_at)}
                <div className="text-muted" style={{ fontSize: 11 }}>
                  {delayText(t.filed_after_days)}
                </div>
              </td>
              <td>
                {t.filing_url && isSafeHttpUrl(t.filing_url) ? (
                  <a href={t.filing_url} target="_blank" rel="noopener noreferrer">
                    SEC filing
                  </a>
                ) : (
                  <span className="text-muted">no link</span>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
