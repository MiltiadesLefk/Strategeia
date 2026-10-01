/**
 * Search scoring for the command palette. Pure functions with no imports, so
 * they can be unit-tested with plain Node (frontend/tests/lib.test.mjs).
 *
 * The universe is ~500 symbols and this runs on every keystroke, so each
 * entry's text is lower-cased once (`buildIndex`) and scoring is a handful of
 * `startsWith`/`includes` calls: no library, no per-key allocation.
 *
 * Ranking, best first. A ticker typed exactly or as a prefix beats everything,
 * because that is almost always what someone typing "nv" wants; a company-name
 * prefix or word-prefix ("bank" -> "Bank of America") comes next; plain
 * substring and in-order-letters ("mcsft" -> Microsoft) matches are the tail.
 */

export interface SearchableSymbol {
  symbol: string;
  name: string;
  sector: string;
}

export interface IndexedSymbol<T extends SearchableSymbol = SearchableSymbol> {
  entry: T;
  symbol: string;
  name: string;
  sector: string;
}

// How much a hit in each field is worth relative to the same kind of hit on
// the ticker. A ticker prefix (900) outranks a name prefix (720), which
// outranks a name word-prefix (560), which outranks a ticker substring (500).
const NAME_WEIGHT = 0.8;
const SECTOR_WEIGHT = 0.3;
const KEYWORD_WEIGHT = 0.6;

const SCORE_EXACT = 1000;
const SCORE_PREFIX = 900;
const SCORE_WORD_PREFIX = 700;
const SCORE_INCLUDES = 500;
const PREFIX_LENGTH_PENALTY = 0.5;
const PREFIX_LENGTH_CAP = 50;
// Letters-in-order matches live below every contiguous match, scaled by how
// tightly the letters sit together (see subsequenceScore).
const SCORE_SUBSEQUENCE_BASE = 50;
const SCORE_SUBSEQUENCE_RANGE = 40;
// Two letters in order match half the universe by accident ("ap" is in
// "Capital", "Apple", "Chipotle"), so only longer queries get the fuzzy tier.
const MIN_SUBSEQUENCE_QUERY = 3;

export function normalizeQuery(raw: string): string {
  return raw.trim().toLowerCase().replace(/^\$/, '');
}

function tokenize(query: string): string[] {
  return normalizeQuery(query).split(/\s+/).filter(Boolean);
}

/** Score for the letters of `q` appearing in order inside `text` (both lower case); 0 if they don't. */
export function subsequenceScore(q: string, text: string): number {
  if (q.length < MIN_SUBSEQUENCE_QUERY || q.length > text.length) return 0;
  let qi = 0;
  let first = -1;
  let last = -1;
  for (let ti = 0; ti < text.length && qi < q.length; ti++) {
    if (text.charCodeAt(ti) === q.charCodeAt(qi)) {
      if (first < 0) first = ti;
      last = ti;
      qi++;
    }
  }
  if (qi < q.length) return 0;
  const span = last - first + 1;
  return SCORE_SUBSEQUENCE_BASE + SCORE_SUBSEQUENCE_RANGE * (q.length / span);
}

function isWordBoundary(ch: string): boolean {
  return ch === ' ' || ch === '-' || ch === '.' || ch === '/' || ch === '&';
}

/** Score for one lower-case query token against one lower-case string; 0 = no match. */
export function scoreField(q: string, text: string): number {
  if (!q || !text) return 0;
  if (text === q) return SCORE_EXACT;
  // Among prefix matches the shorter text is the closer one: "nv" -> NVR before NVDA.
  if (text.startsWith(q)) return SCORE_PREFIX - Math.min(text.length - q.length, PREFIX_LENGTH_CAP) * PREFIX_LENGTH_PENALTY;
  let at = text.indexOf(q);
  if (at < 0) return subsequenceScore(q, text);
  while (at > 0) {
    if (isWordBoundary(text.charAt(at - 1))) return SCORE_WORD_PREFIX;
    at = text.indexOf(q, at + 1);
  }
  return SCORE_INCLUDES;
}

export function buildIndex<T extends SearchableSymbol>(entries: readonly T[]): IndexedSymbol<T>[] {
  return entries.map((entry) => ({
    entry,
    symbol: entry.symbol.toLowerCase(),
    name: entry.name.toLowerCase(),
    sector: entry.sector.toLowerCase(),
  }));
}

function scoreToken(token: string, ix: IndexedSymbol): number {
  return Math.max(
    scoreField(token, ix.symbol),
    NAME_WEIGHT * scoreField(token, ix.name),
    SECTOR_WEIGHT * scoreField(token, ix.sector),
  );
}

/**
 * Score of a whole query against one symbol; 0 = no match. A multi-word query
 * ("bank america") needs every word to match something, and is only as good as
 * its weakest word.
 */
export function scoreSymbol(query: string, ix: IndexedSymbol): number {
  const tokens = tokenize(query);
  if (tokens.length === 0) return 0;
  let worst = Infinity;
  for (const token of tokens) {
    const s = scoreToken(token, ix);
    if (s <= 0) return 0;
    if (s < worst) worst = s;
  }
  return worst;
}

/** Best `limit` matches with their scores, highest first, ties broken alphabetically by ticker. */
export function searchSymbolsScored<T extends SearchableSymbol>(
  index: readonly IndexedSymbol<T>[],
  query: string,
  limit: number,
): { entry: T; score: number }[] {
  const scored: { entry: T; score: number }[] = [];
  for (const ix of index) {
    const score = scoreSymbol(query, ix);
    if (score > 0) scored.push({ entry: ix.entry, score });
  }
  scored.sort((a, b) => b.score - a.score || (a.entry.symbol < b.entry.symbol ? -1 : a.entry.symbol > b.entry.symbol ? 1 : 0));
  return scored.slice(0, limit);
}

/** Best `limit` matches, highest score first. */
export function searchSymbols<T extends SearchableSymbol>(index: readonly IndexedSymbol<T>[], query: string, limit: number): T[] {
  return searchSymbolsScored(index, query, limit).map((s) => s.entry);
}

/** Score a command (a label plus hidden keywords) against a query; 0 = no match. Same all-words rule as symbols. */
export function scoreCommand(query: string, label: string, keywords: string = ''): number {
  const tokens = tokenize(query);
  if (tokens.length === 0) return 0;
  const l = label.toLowerCase();
  const k = keywords.toLowerCase();
  let worst = Infinity;
  for (const token of tokens) {
    const s = Math.max(scoreField(token, l), KEYWORD_WEIGHT * scoreField(token, k));
    if (s <= 0) return 0;
    if (s < worst) worst = s;
  }
  return worst;
}
