from __future__ import annotations

import time
from collections.abc import Callable

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session

from app.api.deps import get_app_settings, get_llm_provider, get_session, require_auth
from app.config import AppSettings
from app.data_providers import universe
from app.data_providers.pr_newswire import SOURCE_LABEL, collect_press_releases
from app.knowledge.news_cards import cards_by_news_key
from app.llm_providers.base import LLMProvider
from app.schemas.news_schemas import (
    NewsCardOut,
    NewsCardsResponse,
    NewsCollectRequest,
    NewsCollectResponse,
    NewsItemWithCard,
    NewsLabelResponse,
)
from app.services.archive_service import archived_news
from app.services.news_card_service import label_new_items, llm_is_usable, unlabelled_news

router = APIRouter(prefix="/api/news", tags=["news"], dependencies=[Depends(require_auth)])

# Collecting reads two public feeds and labelling spends AI calls: neither is a
# button to mash. Same in-process pattern as the other expensive routes (reset
# in tests/conftest.py).
NEWS_COLLECT_COOLDOWN_SECONDS = 60
NEWS_LABEL_COOLDOWN_SECONDS = 20
_last_news_collect_monotonic: float | None = None
_last_news_label_monotonic: dict[str, float] = {}
# Items listed back by the read endpoint.
NEWS_ITEMS_SHOWN = 30
UNLABELLED_COUNT_SCAN = 200


def get_press_release_collector() -> Callable[..., object]:
    """The press-release collector; a test overrides this dependency."""
    return collect_press_releases


def _card_out(payload: dict) -> NewsCardOut:
    return NewsCardOut(
        event_type=str(payload.get("event_type", "other")),
        sentiment=str(payload.get("sentiment", "neutral")),
        materiality=str(payload.get("materiality", "low")),
        companies_mentioned=[str(c) for c in payload.get("companies_mentioned", [])],
        one_line_summary=str(payload.get("one_line_summary", "")),
        is_about_this_company=bool(payload.get("is_about_this_company", False)),
        label_model=str(payload.get("label_model", "")),
        labelled_at=str(payload.get("labelled_at", "")),
    )


@router.get("/{symbol}", response_model=NewsCardsResponse)
def news_with_cards(
    symbol: str,
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> NewsCardsResponse:
    """The symbol's saved headlines (including press releases) with their AI
    label when one exists. Read-only: it never fetches, labels or writes."""
    symbol = symbol.upper()
    cards = cards_by_news_key(session, symbol)
    items = []
    for fact in archived_news(session, symbol, limit=NEWS_ITEMS_SHOWN):
        p = fact.payload
        card = cards.get(fact.dedupe_key)
        items.append(
            NewsItemWithCard(
                headline=str(p.get("headline", "")),
                publisher=str(p.get("publisher", "")),
                url=str(p.get("url", "")),
                published_at=str(p.get("published_at", "")),
                known_at=fact.known_at,
                is_press_release=fact.source == SOURCE_LABEL or p.get("data_provider") == SOURCE_LABEL,
                card=_card_out(card.payload) if card else None,
            )
        )
    return NewsCardsResponse(
        symbol=symbol,
        news_cards_enabled=settings.news_cards_enabled,
        llm_configured=llm_is_usable(llm_provider),
        labelled_count=sum(1 for i in items if i.card is not None),
        unlabelled_count=len(unlabelled_news(session, symbol, UNLABELLED_COUNT_SCAN)),
        items=items,
    )


@router.post("/collect", response_model=NewsCollectResponse)
def collect_news(
    body: NewsCollectRequest | None = None,
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
    collector: Callable[..., object] = Depends(get_press_release_collector),
) -> NewsCollectResponse:
    """Read PR Newswire's public feeds once and save the releases that match
    the watchlist. Idempotent: a release already saved is not saved twice."""
    global _last_news_collect_monotonic
    now = time.monotonic()
    if _last_news_collect_monotonic is not None:
        elapsed = now - _last_news_collect_monotonic
        if elapsed < NEWS_COLLECT_COOLDOWN_SECONDS:
            raise HTTPException(
                status_code=429,
                detail=f"Press releases were just collected {elapsed:.0f}s ago: wait "
                f"{NEWS_COLLECT_COOLDOWN_SECONDS - elapsed:.0f}s before collecting again.",
            )
    _last_news_collect_monotonic = now
    symbols = body.symbols if body and body.symbols else universe.get_default_watchlist(settings.scan_universe_size)
    result = collector(session, symbols)
    return NewsCollectResponse(
        symbols=len(set(symbols)),
        feeds_read=result.feeds_read,
        feeds_failed=result.feeds_failed,
        releases_seen=result.releases_seen,
        releases_matched=result.releases_matched,
        new=result.new,
        already_saved=result.already_saved,
        skipped_reason=result.skipped_reason,
        failures=result.failures,
    )


@router.post("/label/{symbol}", response_model=NewsLabelResponse)
def label_news(
    symbol: str,
    limit: int | None = Query(None, ge=1, le=30, description="Most headlines to label (default: the setting)"),
    session: Session = Depends(get_session),
    settings: AppSettings = Depends(get_app_settings),
    llm_provider: LLMProvider = Depends(get_llm_provider),
) -> NewsLabelResponse:
    """Have the configured AI label this symbol's saved headlines that have no
    label yet. Needs the News cards setting on; spends a bounded number of AI calls."""
    symbol = symbol.upper()
    if not settings.news_cards_enabled:
        raise HTTPException(status_code=400, detail="News cards are turned off. Turn them on in Settings first.")
    now = time.monotonic()
    last = _last_news_label_monotonic.get(symbol)
    if last is not None and now - last < NEWS_LABEL_COOLDOWN_SECONDS:
        raise HTTPException(
            status_code=429,
            detail=f"{symbol} was just labelled: wait {NEWS_LABEL_COOLDOWN_SECONDS - (now - last):.0f}s before asking again.",
        )
    _last_news_label_monotonic[symbol] = now
    name = next((e.name for e in universe.load_universe() if e.symbol.upper() == symbol), None)
    result = label_new_items(
        session, symbol, llm_provider, limit or settings.news_card_batch_limit, company_name=name
    )
    return NewsLabelResponse(
        symbol=symbol,
        labelled=result.labelled,
        llm_calls=result.llm_calls,
        skipped_batches=result.skipped_batches,
        remaining_unlabelled=result.remaining_unlabelled,
        reason=result.reason,
        errors=result.errors,
    )
