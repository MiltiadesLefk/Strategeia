from __future__ import annotations

from pydantic import BaseModel, Field

from app.schemas.common import UtcDatetime


class FundOut(BaseModel):
    cik: str
    name: str
    is_starter: bool  # one of the built-in starting funds
    has_data: bool  # at least one 13F holdings report is stored
    latest_period: str | None  # quarter end of the newest holdings report (YYYY-MM-DD)
    latest_filed_at: UtcDatetime | None  # when SEC accepted it
    latest_form: str | None
    quarters_stored: int
    holdings_count: int
    matched_count: int  # holdings with a ticker
    total_value: float
    value_unit: str | None  # "thousands" | "dollars": how the filing's value column was read
    latest_is_notice: bool  # the newest filing is a notice: its holdings are reported by another manager


class FundsOverviewResponse(BaseModel):
    funds: list[FundOut]
    using_starter_list: bool
    has_data: bool
    sec_contact_is_placeholder: bool
    ingest_command: str
    refresh_cooldown_seconds: int


class FundChangeOut(BaseModel):
    cusip: str
    issuer: str
    symbol: str | None
    put_call: str | None
    share_type: str  # "SH" shares | "PRN" principal amount of a bond
    status: str  # new | added | trimmed | sold_out | unchanged
    shares_now: float
    shares_before: float
    change_pct: float | None
    value_now: float
    value_before: float
    weight_now_pct: float
    weight_before_pct: float


class FundPositionOut(BaseModel):
    cusip: str
    issuer: str
    symbol: str | None
    put_call: str | None
    share_type: str
    shares: float
    value: float
    weight_pct: float


class FundChangesResponse(BaseModel):
    cik: str
    manager: str | None
    has_data: bool
    period: str | None
    previous_period: str | None
    filed_at: UtcDatetime | None
    previous_filed_at: UtcDatetime | None
    consecutive: bool  # the earlier quarter is the one right before
    has_comparison: bool
    holdings_count: int
    total_value: float
    counts: dict[str, int]  # new / added / trimmed / sold_out / unchanged
    status: str  # the filter applied ("all" or one status)
    total: int  # changes matching before the display cap
    shown: int
    changes: list[FundChangeOut]
    top_holdings: list[FundPositionOut]
    filing_url: str | None


class FundHolderOut(BaseModel):
    cik: str
    manager: str | None
    period: str | None
    filed_at: UtcDatetime
    shares: float
    value: float
    weight_pct: float
    previous_shares: float | None
    status: str  # new | added | trimmed | sold_out | unchanged | no_comparison
    change_pct: float | None
    filing_url: str | None


class FundHoldersResponse(BaseModel):
    symbol: str
    funds_stored: int  # how many followed funds have any holdings stored
    holders: list[FundHolderOut]


class OwnershipFilingOut(BaseModel):
    symbol: str | None
    accession: str
    schedule: str  # "13D" | "13G"
    form: str
    is_amendment: bool
    known_at: UtcDatetime
    event_date: str | None
    filer_name: str | None
    issuer_name: str | None
    class_title: str | None
    percent: float | None
    shares: float | None
    rule: str | None
    purpose: str | None
    person_count: int
    filing_url: str | None


class OwnershipResponse(BaseModel):
    days: int
    schedule: str  # "all" | "13D" | "13G"
    symbol: str | None
    total: int
    shown: int
    count_13d: int
    count_13g: int
    filings: list[OwnershipFilingOut]
    has_data: bool
    refresh_cooldown_seconds: int


class FundsRefreshResponse(BaseModel):
    funds_processed: int
    filings_ingested: int
    holdings_created: int
    ownership_filings_created: int
    legacy_skipped: int  # older free-text 13D/13G filings, not read
    errors: list[str] = Field(default_factory=list)
