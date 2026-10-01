"""Rule-based reading of a post: which market topics it touches and which watchlist
companies it names.

No AI is involved, on purpose. A language model may extract facts from words;
what those words mean for a stock is decided by rules written here, so a wrong
alert can always be traced to one line of this file. A post that matches nothing
here is simply not an event.

Two kinds of match, both deliberately strict. A missed mention costs a late alert;
a false one costs a wasted evaluation and teaches the user to ignore alerts.

Topics (`TOPICS`): a small table of patterns for tariffs, China, the Fed and
interest rates, semiconductors, oil and gas, drug makers, banks, electric vehicles
and crypto. Topic hits are alert-only: they never start an evaluation.

Companies (`match_symbols`): only the company's exact name (as the watchlist
spells it, minus legal suffixes such as "Inc." or "Corporation") or its ticker in
an unmistakable form:

  * a name: "Boeing", "Apple", "General Motors", also in capitals ("BOEING");
    names that are ordinary words ("Target", "Visa", "Block", "Fox", "Dow",
    "News"...) match only with their legal suffix ("Target Corporation"), so a
    post about "Fox News" or "the Dow" names no company;
  * a ticker: a cashtag ($NVDA), an exchange-qualified form ("NASDAQ: NVDA") or a
    bare ticker of four or more capital letters that is not also a plain word.
    A short bare ticker ("F", "T", "ALL") is never matched: it is far more often a
    letter or a word.

Crypto pairs (BTC-USD ...) are not matched by name; crypto is a topic.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from functools import lru_cache

# Topic -> pattern. Case-insensitive unless the pattern says otherwise.
# Order is the order topics are reported in.
TOPICS: dict[str, re.Pattern[str]] = {
    "tariffs": re.compile(r"\btariffs?\b", re.IGNORECASE),
    "china": re.compile(r"\bChina\b|\bCHINA\b|\bChinese\b|\bXi Jinping\b|\bBeijing\b"),
    "fed_rates": re.compile(
        # "Fed" must be capitalised: lower-case "fed" is the verb.
        r"(?i:\bfederal reserve\b|\bfomc\b|\bpowell\b|\bwarsh\b|\binterest rates?\b|\brate (?:cuts?|hikes?)\b)"
        r"|\bFed\b|\bFED\b"
    ),
    "semiconductors": re.compile(
        r"\bsemiconductors?\b|\bchipmakers?\b|\b(?:AI|computer|memory|advanced) chips?\b", re.IGNORECASE
    ),
    "oil_gas": re.compile(r"\boil\b|\bcrude\b|\bOPEC\b|\bLNG\b|\bnatural gas\b", re.IGNORECASE),
    "pharma": re.compile(r"\bpharmaceuticals?\b|\bpharma\b|\bdrug prices?\b|\bprescription drugs?\b", re.IGNORECASE),
    "banks": re.compile(r"\bbanks?\b|\bbanking\b", re.IGNORECASE),
    # "EV" must be capitalised: lower-case "ev" is not an electric vehicle.
    "electric_vehicles": re.compile(r"(?i:\belectric (?:vehicles?|cars?)\b)|\bEVs?\b"),
    "crypto": re.compile(
        r"\bbitcoin\b|\bcrypto(?:currenc(?:y|ies))?\b|\bstablecoins?\b|\bethereum\b|\bdigital assets?\b", re.IGNORECASE
    ),
}

# Topics that can move the whole market, not one sector: an alert about them is "notable".
MARKET_WIDE_TOPICS = frozenset({"tariffs", "china", "fed_rates"})

# Legal endings stripped from a watchlist name to get the name people actually say.
_SUFFIX_WORDS = frozenset(
    {
        "inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited", "plc",
        "holdings", "holding", "group", "the", "nv", "sa", "ag", "lp", "llc", "class", "a", "b", "c",
    }
)
# Core names that are ordinary words or other well-known things: matched only with
# the full legal name ("Target Corporation"), never as a bare word.
AMBIGUOUS_CORE_NAMES = frozenset(
    {
        "target", "visa", "block", "gap", "discover", "ball", "fox", "news", "dow", "southern", "match",
        "mosaic", "general", "american", "united", "first", "national", "western", "new", "ross", "best",
        "all", "live", "pool", "snap",
    }
)
# Tickers of four or more letters that are also plain words (shouted posts use capitals).
AMBIGUOUS_TICKERS = frozenset({"FAST", "WELL", "CARR", "ALLE", "TRUE", "LIFE", "GOOD"})
# Names people use that the watchlist's legal name does not contain.
EXTRA_ALIASES: dict[str, tuple[str, ...]] = {
    "AMZN": ("Amazon",),
    "GOOGL": ("Google",),
    "GOOG": ("Google",),
    "META": ("Facebook",),
}
MIN_CORE_NAME_LENGTH = 3
MIN_BARE_TICKER_LENGTH = 4


@dataclass(frozen=True)
class SymbolMatcher:
    symbol: str
    name: str
    pattern: re.Pattern[str]


def core_name(name: str) -> str:
    """"Boeing Co." -> "Boeing"; "Ford Motor Company" -> "Ford Motor". Parenthetical
    notes and trailing legal words are dropped."""
    cleaned = re.sub(r"\(.*?\)", " ", name)
    words = [w for w in re.split(r"[\s,]+", cleaned.strip()) if w]
    while words and words[-1].strip(".").lower() in _SUFFIX_WORDS and len(words) > 1:
        words.pop()
    return " ".join(words).strip(" .,")


def _name_alternatives(name: str, symbol: str) -> list[str]:
    cleaned = re.sub(r"\(.*?\)", " ", name)
    full = " ".join(w for w in re.split(r"[\s,]+", cleaned.strip()) if w).rstrip(",")
    alternatives: list[str] = []
    if full and full.lower() != symbol.lower():
        alternatives.append(full.rstrip("."))
    core = core_name(name)
    if len(core) >= MIN_CORE_NAME_LENGTH and core.lower() not in AMBIGUOUS_CORE_NAMES:
        alternatives.append(core)
    alternatives.extend(EXTRA_ALIASES.get(symbol, ()))
    seen: set[str] = set()
    unique: list[str] = []
    for alt in alternatives:
        if alt.lower() not in seen and alt.lower() != symbol.lower():
            seen.add(alt.lower())
            unique.append(alt)
    return unique


def build_matcher(symbol: str, name: str) -> SymbolMatcher | None:
    """The matcher for one company, or None for a symbol that cannot be matched
    safely (a crypto pair, or one with no usable name and no strict ticker form)."""
    symbol = symbol.strip().upper()
    if not symbol or symbol.endswith("-USD") or symbol.startswith("^"):
        return None
    parts: list[str] = []
    for alt in _name_alternatives(name or "", symbol):
        escaped = re.escape(alt)
        parts.append(rf"(?<!\w)(?:{escaped}|{re.escape(alt.upper())})(?!\w)")
    tick = re.escape(symbol)
    parts.append(rf"\${tick}(?![\w])")
    parts.append(rf"\b(?:NYSE|NASDAQ|Nasdaq|AMEX)\s*:\s*{tick}(?![\w])")
    if len(symbol) >= MIN_BARE_TICKER_LENGTH and symbol.isalpha() and symbol not in AMBIGUOUS_TICKERS:
        parts.append(rf"(?<![\w$@#]){tick}(?![\w])")
    pattern = re.compile("|".join(parts))
    return SymbolMatcher(symbol=symbol, name=name, pattern=pattern)


@lru_cache(maxsize=8)
def _matchers_for(entries: tuple[tuple[str, str], ...]) -> tuple[SymbolMatcher, ...]:
    built = (build_matcher(symbol, name) for symbol, name in entries)
    return tuple(m for m in built if m is not None)


def build_matchers(entries: Iterable[tuple[str, str]]) -> tuple[SymbolMatcher, ...]:
    """Matchers for (symbol, name) pairs. Cached by the whole list, so a poll that
    sees the same watchlist compiles nothing."""
    return _matchers_for(tuple((s.strip().upper(), n) for s, n in entries))


def match_topics(text: str) -> list[str]:
    """Topics the text touches, in table order."""
    return [topic for topic, pattern in TOPICS.items() if pattern.search(text)]


def match_symbols(text: str, matchers: Iterable[SymbolMatcher]) -> list[str]:
    """Watchlist symbols the text names, in matcher order (no duplicates)."""
    found: list[str] = []
    for matcher in matchers:
        if matcher.symbol not in found and matcher.pattern.search(text):
            found.append(matcher.symbol)
    return found


def watchlist_matchers() -> tuple[SymbolMatcher, ...]:
    """Matchers for the symbols the app currently scans."""
    from app.data_providers.universe import load_universe

    return build_matchers((entry.symbol, entry.name) for entry in load_universe())
