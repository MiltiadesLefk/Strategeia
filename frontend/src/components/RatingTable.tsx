import type { RatingRow } from '../api/types';
import { ScoreTable, type ScorePart } from './ScoreTable';

// Keyed by the part names the backend stores (rating_service labels).
const HELP: Record<string, string> = {
  technical: 'Trend, momentum, structure and volume from the price chart.',
  fundamentals: 'Revenue growth, margins and balance-sheet health.',
  news: 'Tone of recent headlines. Bullish news supports a long.',
  'weekly/market': 'Weekly-timeframe and broad market (SPY) agreement with the trade.',
  VIX: 'VIX regime. Only ever a penalty when elevated (25 or more), never a bonus.',
  options: 'Options put/call volume skew agreement (no chain data for most thin names).',
  insiders: 'SEC Form 4, last 90 days, up to 2 points either way. Net open-market buying supports a long; selling argues against it (10b5-1 plan sales count at a quarter).',
  'earnings record': 'Consistent beat or miss streak on reported consensus EPS over the last quarters. Real history, not a forecast.',
  'expected move': "Options market pricing a move larger than this stock's own recent range. A magnitude read, so it only subtracts.",
  'macro days': 'A scheduled FOMC decision, CPI release or jobs report today or tomorrow. Market-wide event risk.',
  Congress: 'Members of Congress net buying, by filing date, last 45 days. A net of 4 members is worth 2 points. Reports lag up to 45 days.',
  'Funds (13F)': 'Followed funds 13F changes: net buying supports a long. 3 or more funds is worth 2 points. Data is up to 45 days old.',
  '5% owners': 'Holders of 5% or more: a new Schedule 13D or a raised stake is buying, a cut stake is selling. Last 90 days, up to 2 points.',
  'Short volume': 'Unusually high FINRA short-sale volume against its own baseline argues against a long. Short volume is not short interest.',
  '8-K filings': 'Negative-leaning 8-K items filed in the last 3 days (bankruptcy, delisting, restatement, executive change): against a long.',
  Financials: 'Profitability trend from the reported years: profitable and improving supports a long.',
  'Balance sheet': 'Debt-to-equity, current ratio and free cash flow of the latest year (Yahoo only): strong supports a long, weak argues against. Missing figures score 0.',
  Valuation: 'P/E at the extremes plus the DCF and peer comparison: cheap supports a long, expensive argues against. Up to 2 points.',
  Consensus: 'Consensus EPS for the next report against the last reported EPS: expected growth of 10% or more supports a long.',
  'Vs market': "The stock's 63-day return against SPY: leading by 5 points or more supports a long.",
  'Volume trend': 'Volume on up days against down days over 20 days: accumulation supports a long.',
  'Options OI': 'Put/call ratio of open interest (positions still held): call-heavy supports a long.',
  'Fed window': 'Caution flag: a Fed statement or Chair speech within a day. Only ever subtracts.',
  Posts: 'Caution flag: a post naming this company in the last 24 hours (an unofficial archive). Only ever subtracts.',
};
const PENALTY_ONLY = new Set(['VIX', 'expected move', 'macro days', 'Fed window', 'Posts']);

/** The explained table of one rating: every part, its points, and why. */
export function RatingTable({ row }: { row: RatingRow }) {
  const why = row.detail.why ?? {};
  const parts: ScorePart[] = Object.entries(row.detail.parts ?? {}).map(([label, value]) => ({
    label: label.charAt(0).toUpperCase() + label.slice(1),
    value,
    note: why[label],
    help: HELP[label] ?? '',
    items: row.detail.items?.[label],
    unsigned: label === 'technical',
    penaltyOnly: PENALTY_ONLY.has(label),
  }));
  const bar = row.detail.bar_pct;
  return (
    <ScoreTable
      parts={parts}
      summary={`Score ${row.percent}/100${bar != null ? ` (bar ${bar})` : ''}. The points per part below add up to it, scaled so the best possible total is 100.`}
    />
  );
}
