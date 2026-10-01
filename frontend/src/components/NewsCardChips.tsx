import { useCollectPressReleases, useLabelNews, useNewsCards } from '../api/hooks';
import type { NewsCardOut, NewsItemWithCard } from '../api/types';
import { ErrorBanner, formatRelativeTime, isSafeHttpUrl } from './common';

const EVENT_LABELS: Record<string, string> = {
  earnings: 'Earnings',
  guidance: 'Guidance',
  mna: 'M&A',
  product: 'Product',
  legal: 'Legal',
  regulatory: 'Regulatory',
  leadership: 'Leadership',
  analyst: 'Analyst',
  macro: 'Macro',
  other: 'Other',
};

function sentimentClass(sentiment: string): string {
  if (sentiment === 'positive') return 'badge badge-green';
  if (sentiment === 'negative') return 'badge badge-red';
  if (sentiment === 'mixed') return 'badge badge-amber';
  return 'badge badge-neutral';
}

function materialityClass(materiality: string): string {
  return materiality === 'high' ? 'badge badge-amber' : 'badge badge-neutral';
}

/** Event type, sentiment and materiality chips for one AI-labelled headline. The hover text says it is an
 *  AI label and shows the model's one-line summary; labels are never point-in-time (see the note on the panel). */
export function NewsCardChips({ card }: { card: NewsCardOut }) {
  const tip =
    `AI-labelled by ${card.label_model || 'the configured model'}: ${card.one_line_summary} ` +
    (card.is_about_this_company ? '' : '(Not mainly about this company.) ') +
    'Labels are written when the headline is labelled, so they are not point-in-time.';
  return (
    <span data-testid="news-card-chips" title={tip} style={{ display: 'inline-flex', gap: 4, flexWrap: 'wrap' }}>
      <span className="badge badge-neutral">{EVENT_LABELS[card.event_type] ?? card.event_type}</span>
      <span className={sentimentClass(card.sentiment)}>{card.sentiment}</span>
      <span className={materialityClass(card.materiality)}>{card.materiality} materiality</span>
      <span className="badge badge-neutral" style={{ fontStyle: 'italic' }}>
        AI-labelled
      </span>
    </span>
  );
}

function ReleaseRow({ item }: { item: NewsItemWithCard }) {
  const body = (
    <div>
      <div style={{ fontWeight: 600, fontSize: 14, color: 'var(--text)' }}>{item.headline}</div>
      <div className="text-muted" style={{ fontSize: 12, display: 'flex', gap: 6, alignItems: 'center', flexWrap: 'wrap' }}>
        <span className="badge badge-neutral">PR Newswire</span>
        <span>{formatRelativeTime(item.known_at)}</span>
        {item.card && <NewsCardChips card={item.card} />}
      </div>
    </div>
  );
  return isSafeHttpUrl(item.url) ? (
    <a href={item.url} target="_blank" rel="noreferrer">
      {body}
    </a>
  ) : (
    body
  );
}

/** Press releases saved for the symbol, and the controls for collecting and labelling. */
export function NewsCardsPanel({ symbol }: { symbol: string }) {
  const { data } = useNewsCards(symbol);
  const label = useLabelNews(symbol);
  const collect = useCollectPressReleases();
  if (!data) return null;
  const releases = data.items.filter((i) => i.is_press_release);
  const canLabel = data.news_cards_enabled && data.llm_configured && data.unlabelled_count > 0;
  const labelHint = !data.news_cards_enabled
    ? 'Turn on News cards in Settings first'
    : !data.llm_configured
      ? 'Needs an AI provider (Settings)'
      : data.unlabelled_count === 0
        ? 'Every saved headline already has a label'
        : 'Have the AI label the saved headlines that have no label yet';
  return (
    <div className="card" data-testid="news-cards-panel" style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
      <h3>Press releases and AI labels</h3>
      {releases.length === 0 ? (
        <div className="text-muted" style={{ fontSize: 13 }}>
          No PR Newswire press releases saved for {symbol} yet. Collecting reads PR Newswire&apos;s public feeds once
          and saves only releases that name {symbol} by its exchange ticker or its exact company name.
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 12 }}>
          {releases.map((r) => (
            <ReleaseRow key={r.url || r.headline} item={r} />
          ))}
        </div>
      )}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
        <button className="btn btn-secondary" onClick={() => collect.mutate()} disabled={collect.isPending}>
          {collect.isPending ? 'Collecting…' : 'Collect press releases'}
        </button>
        <button
          className="btn btn-secondary"
          onClick={() => label.mutate()}
          disabled={!canLabel || label.isPending}
          title={labelHint}
        >
          {label.isPending ? 'Labelling…' : `Label now (${data.unlabelled_count} unlabelled)`}
        </button>
        <span className="text-muted tabular-nums" style={{ fontSize: 12 }}>
          {data.labelled_count} of {data.items.length} saved headlines labelled
        </span>
      </div>
      {collect.data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          {collect.data.skipped_reason ??
            `Read ${collect.data.feeds_read} feed${collect.data.feeds_read === 1 ? '' : 's'}: ${collect.data.releases_seen} releases, ${collect.data.releases_matched} matched the watchlist, ${collect.data.new} new.`}
          {collect.data.feeds_failed > 0 && ` ${collect.data.feeds_failed} feed(s) could not be read.`}
        </div>
      )}
      {label.data && (
        <div className="text-muted" style={{ fontSize: 12 }}>
          {label.data.reason ?? `Labelled ${label.data.labelled} headline${label.data.labelled === 1 ? '' : 's'}.`}
          {label.data.errors.length > 0 && ` ${label.data.errors.join(' ')}`}
        </div>
      )}
      {collect.error && <ErrorBanner message={collect.error.message} />}
      {label.error && <ErrorBanner message={label.error.message} />}
      <div className="text-muted" style={{ fontSize: 12, lineHeight: 1.6 }}>
        Labels are written by an AI that only reads the headline. They are rule-checked and recorded, but they do not
        change any score or trade today. They are also not point-in-time: the label is saved when it is made, and a
        model can know how an older story ended, so they are only meant to be judged on news from now on.
      </div>
    </div>
  );
}
