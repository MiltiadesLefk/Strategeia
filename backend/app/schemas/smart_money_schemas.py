from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime


class InsiderTradeOut(BaseModel):
    symbol: str
    insider: str | None
    role_tags: list[str]  # e.g. ["CEO", "Director"]
    officer_title: str | None
    code: str | None  # SEC transaction code: P = open-market buy, S = open-market sale
    side: str  # "buy" | "sell" | "other"
    shares: float | None
    price: float | None
    value: float | None  # None when the filing gave no price
    transaction_date: str | None  # what the trade is about (YYYY-MM-DD)
    known_at: UtcDatetime  # when SEC accepted the filing: the first moment anyone could see it
    filed_after_days: int | None  # calendar days between the trade and the filing
    is_10b5_1: bool
    filing_url: str | None


class InsiderTradesResponse(BaseModel):
    days: int
    side: str
    symbol: str | None
    min_value: float
    total: int  # rows matching before the display cap
    shown: int
    buy_value: float
    sell_value: float
    buy_count: int
    sell_count: int
    trades: list[InsiderTradeOut]


class InsiderClusterOut(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    insider_count: int
    trade_count: int
    total_value: float
    unpriced_trades: int
    role_tags: list[str]
    insiders: list[str]
    any_10b5_1: bool
    visible_from: UtcDatetime  # when the filing that completed the cluster was accepted


class InsiderClustersResponse(BaseModel):
    days: int
    symbols_checked: int
    clusters: list[InsiderClusterOut]


class InsiderSummaryResponse(BaseModel):
    symbol: str
    window_days: int
    data_loaded: bool  # False: nothing stored for this symbol, which is not the same as "quiet"
    buy_count: int
    sell_count: int
    buy_value: float
    sell_value: float
    net_value: float
    cluster_count: int
    newest_filing: UtcDatetime | None
    would_score_long: int  # information only: what the live scorer would add to a long today
    would_score_short: int
    score_reasons: list[str]


class InsiderStatusResponse(BaseModel):
    has_data: bool
    trade_rows: int
    symbols: int
    oldest_filing: UtcDatetime | None
    newest_filing: UtcDatetime | None
    last_stored_at: UtcDatetime | None  # when this program last saved a row
    symbol_list: list[str]
    sec_contact_is_placeholder: bool
    ingest_command: str


class InsiderRefreshRequest(BaseModel):
    symbols: list[str] | None = Field(default=None, max_length=200)


class InsiderRefreshResponse(BaseModel):
    symbols_requested: int
    symbols_processed: int
    symbols_remaining: int  # run again to continue
    filings_seen: int
    filings_ingested: int
    rows_created: int
    unknown_symbols: list[str]
    errors: list[str]
