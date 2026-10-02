"""Response models for the Congress trades endpoints (Smart Money, Congress tab)."""

from __future__ import annotations

from pydantic import BaseModel

from app.schemas.common import UtcDatetime


class CongressTradeOut(BaseModel):
    symbol: str | None  # None for bonds, funds, private assets: shown, never scored
    member: str
    state_district: str | None
    owner: str  # "self" | "spouse" | "dependent child" | "joint"
    asset: str
    asset_type: str | None  # the form's two-letter code, e.g. "ST" (stock), "OP" (option)
    ticker: str | None
    type: str  # "purchase" | "sale" | "partial sale" | "exchange"
    side: str  # "buy" | "sell" | "other"
    amount_low: int | None
    amount_high: int | None  # None for an open-ended top range
    amount_text: str
    range_midpoint: float | None  # middle of the range, a label only; None when open-ended
    trade_date: str | None
    filed_date: str | None
    filing_delay_days: int | None  # calendar days between the trade and its report
    known_at: UtcDatetime
    filing_url: str | None
    notes: str


class CongressTradesResponse(BaseModel):
    days: int
    side: str
    symbol: str | None
    member: str | None
    follow_mode: str  # "all" | "list"
    followed_only: bool  # whether the follow list was applied to this response
    total: int  # rows matching before the display cap
    shown: int
    buy_count: int
    sell_count: int
    stock_buy_count: int
    members: int  # distinct members among the matching rows
    trades: list[CongressTradeOut]


class CongressClusterOut(BaseModel):
    symbol: str
    start_date: str
    end_date: str
    member_count: int
    trade_count: int
    members: list[str]
    total_low: int
    total_high: int | None
    visible_from: UtcDatetime  # when the report that completed the cluster was filed


class CongressClustersResponse(BaseModel):
    days: int
    follow_mode: str
    followed_only: bool
    clusters: list[CongressClusterOut]


class CongressMemberOut(BaseModel):
    member: str
    state_district: str | None
    trade_count: int
    buy_count: int
    sell_count: int
    stock_buy_count: int
    filings: int
    newest_filing: str | None
    median_filing_delay_days: int | None
    followed: bool


class CongressMembersResponse(BaseModel):
    days: int
    follow_mode: str
    members: list[CongressMemberOut]


class CongressUnreadableOut(BaseModel):
    doc_id: str
    member: str
    filed_date: str | None
    status: str  # "partial" | "unreadable"
    reason: str | None
    rows: int
    filing_url: str | None


class CongressStatusResponse(BaseModel):
    has_data: bool
    trade_rows: int
    filings: int
    members: int
    unreadable_filings: int  # reports with no readable table (scanned images and the like)
    partial_filings: int  # reports where only some rows could be read
    problem_filings: list[CongressUnreadableOut]
    oldest_filing: UtcDatetime | None
    newest_filing: UtcDatetime | None
    last_stored_at: UtcDatetime | None
    follow_mode: str
    followed_members: list[str]
    senate_note: str
    ingest_command: str


class CongressMemberNameOut(BaseModel):
    name: str
    state_district: str | None
    followed: bool


class CongressMemberNamesResponse(BaseModel):
    names: list[CongressMemberNameOut]
    total: int


class CongressRefreshResponse(BaseModel):
    year: int
    filings_listed: int
    filings_ingested: int
    filings_remaining: int
    rows_created: int
    unreadable: int
    partial: int
    errors: list[str]
