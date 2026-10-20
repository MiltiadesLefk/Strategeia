export interface EvidenceItem {
  text: string;
  meta?: string;
  url?: string;
  /** The AI's label for a headline: shown as a coloured badge at the right. */
  sentiment?: string | null;
}

const BADGE: Record<string, { cls: string; text: string }> = {
  positive: { cls: 'badge badge-green', text: 'Bullish' },
  negative: { cls: 'badge badge-red', text: 'Bearish' },
  neutral: { cls: 'badge badge-neutral', text: 'Neutral' },
  mixed: { cls: 'badge badge-neutral', text: 'Mixed' },
};

/** One badge for a company's headlines: Bullish/Bearish when they lean one way, Mixed when both, Neutral otherwise. */
export function SentimentSummary({ items }: { items: EvidenceItem[] }) {
  const labels = items.map((it) => it.sentiment).filter(Boolean);
  if (labels.length === 0) return null;
  const pos = labels.includes('positive');
  const neg = labels.includes('negative');
  const badge = BADGE[pos && neg ? 'mixed' : pos ? 'positive' : neg ? 'negative' : 'neutral'];
  return <span className={badge.cls} style={{ minWidth: 64, textAlign: 'center' }}>{badge.text}</span>;
}

/** What a rating part or report line looked at, one row each, the headline's sentiment highlighted at the right. */
export function EvidenceItems({ items }: { items: EvidenceItem[] }) {
  return (
    <ul style={{ margin: '4px 0 0', padding: 0, listStyle: 'none', fontSize: 12, lineHeight: 1.6 }}>
      {items.map((it, i) => {
        const badge = it.sentiment ? BADGE[it.sentiment] : undefined;
        return (
          <li key={i} style={{ display: 'flex', gap: 12, alignItems: 'baseline', padding: '2px 0' }}>
            <span style={{ flex: 1 }}>
              {it.url ? <a href={it.url} target="_blank" rel="noreferrer">{it.text}</a> : it.text}
              {it.meta && <span className="text-muted"> · {it.meta}</span>}
            </span>
            {badge && <span className={badge.cls} style={{ minWidth: 64, textAlign: 'center' }}>{badge.text}</span>}
          </li>
        );
      })}
    </ul>
  );
}
