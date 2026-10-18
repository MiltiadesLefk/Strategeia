import { formatMoney } from '../common';

/** "$a - $b" for a sum of reported ranges (Congress forms give ranges, never dollars); "$a+" when the top range is open-ended. */
export function rangeLabel(low: number, high: number | null): string {
  if (low === 0 && high === 0) return '$0';
  return high === null ? `${formatMoney(low, { compact: true })}+` : `${formatMoney(low, { compact: true })} - ${formatMoney(high, { compact: true })}`;
}

interface Side {
  count: number;
  /** Already formatted (a dollar figure or a range); omitted when the source has no amount to add up. */
  value?: string;
}

/** The totals line every smart-money view starts with: buys, sells and, where it applies, clusters. */
export function SmartMoneyTotals({
  buys, sells, clusters, extra, note,
}: { buys?: Side; sells?: Side; clusters?: number; extra?: string[]; note?: string }) {
  return (
    <div data-testid="smart-money-totals" style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
      <div className="tabular-nums" style={{ display: 'flex', gap: 24, flexWrap: 'wrap', fontSize: 14 }}>
        {buys && (
          <span>
            <span className="text-green" style={{ fontWeight: 700 }}>{buys.count} {buys.count === 1 ? 'buy' : 'buys'}</span>
            {buys.value !== undefined && ` ${buys.value}`}
          </span>
        )}
        {sells && (
          <span>
            <span className="text-red" style={{ fontWeight: 700 }}>{sells.count} {sells.count === 1 ? 'sell' : 'sells'}</span>
            {sells.value !== undefined && ` ${sells.value}`}
          </span>
        )}
        {clusters !== undefined && <span>{clusters} cluster{clusters === 1 ? '' : 's'}</span>}
        {extra?.map((e) => <span key={e} className="text-muted">{e}</span>)}
      </div>
      {note && <div className="text-muted" style={{ fontSize: 12 }}>{note}</div>}
    </div>
  );
}
