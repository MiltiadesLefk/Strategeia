"""The bulk insider backfill: a tiny synthetic data set, no network."""

from __future__ import annotations

import io
import zipfile
from datetime import date, datetime

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import AppSettings
from app.data_providers.sec_insider_bulk import ingest_quarter_zip, quarters_between
from app.knowledge import as_of
from app.smartmoney.tracker import smart_money_score_as_of

SUBMISSION = (
    "ACCESSION_NUMBER\tFILING_DATE\tPERIOD_OF_REPORT\tDATE_OF_ORIG_SUB\tDOCUMENT_TYPE\tISSUERCIK\tISSUERNAME\tISSUERTRADINGSYMBOL\n"
    "0001-26-000001\t05-AUG-2026\t03-AUG-2026\t\t4\t111\tAlpha Inc\tAAA\n"
    "0001-26-000002\t06-AUG-2026\t04-AUG-2026\t\t4\t222\tBeta Inc\tBBB\n"
    "0001-26-000003\t06-AUG-2026\t04-AUG-2026\t\t4\t333\tNo Ticker Inc\t\n"
    "0001-26-000004\t01-JUN-2026\t30-MAY-2026\t\t4\t111\tAlpha Inc\tAAA\n"
)
OWNERS = (
    "ACCESSION_NUMBER\tRPTOWNERCIK\tRPTOWNERNAME\tRPTOWNER_RELATIONSHIP\tRPTOWNER_TITLE\n"
    "0001-26-000001\t9001\tAnn CEO\tOfficer\tChief Executive Officer\n"
    "0001-26-000002\t9002\tBob Holder\tTenPercentOwner\t\n"
    "0001-26-000003\t9003\tCy Director\tDirector\t\n"
    "0001-26-000004\t9001\tAnn CEO\tOfficer\tChief Executive Officer\n"
)
TRANS = (
    "ACCESSION_NUMBER\tNONDERIV_TRANS_SK\tSECURITY_TITLE\tTRANS_DATE\tTRANS_CODE\tTRANS_SHARES\tTRANS_PRICEPERSHARE\t"
    "TRANS_ACQUIRED_DISP_CD\tSHRS_OWNED_FOLLOWING_TRANS\tDIRECT_INDIRECT_OWNERSHIP\n"
    "0001-26-000001\t1\tCommon\t03-AUG-2026\tP\t10000\t50.0\tA\t60000\tD\n"
    "0001-26-000001\t2\tCommon\t03-AUG-2026\tA\t500\t0\tA\t60500\tD\n"
    "0001-26-000002\t1\tCommon\t04-AUG-2026\tS\t100\t20.0\tD\t0\tD\n"
    "0001-26-000003\t1\tCommon\t04-AUG-2026\tP\t100\t20.0\tA\t100\tD\n"
    "0001-26-000004\t1\tCommon\t30-MAY-2026\tP\t10\t10.0\tA\t10\tD\n"
)


def make_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("SUBMISSION.tsv", SUBMISSION)
        z.writestr("REPORTINGOWNER.tsv", OWNERS)
        z.writestr("NONDERIV_TRANS.tsv", TRANS)
    return buf.getvalue()


@pytest.fixture
def session():
    eng = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    SQLModel.metadata.create_all(eng)
    with Session(eng) as s:
        yield s


def test_quarters_between():
    assert quarters_between(date(2025, 11, 20), date(2026, 5, 1)) == ["2025q4", "2026q1", "2026q2"]


def test_stores_buys_and_sells_of_the_asked_symbols_only_and_is_idempotent(session):
    report = ingest_quarter_zip(session, make_zip(), quarter="2026q3", symbols=["AAA", "BBB"], since=date(2026, 7, 1))
    # the June filing is before `since`, the no-ticker filing is skipped, the grant (code A) is not stored
    assert report.rows_created == 2 and report.filings_stored == 2 and report.skipped_symbol == 1
    again = ingest_quarter_zip(session, make_zip(), quarter="2026q3", symbols=["AAA", "BBB"], since=date(2026, 7, 1))
    assert again.rows_created == 0 and again.skipped_existing == 2


def test_a_symbol_not_asked_for_is_not_stored(session):
    report = ingest_quarter_zip(session, make_zip(), quarter="2026q3", symbols=["AAA"], since=date(2026, 7, 1))
    assert report.rows_created == 1


def test_known_at_is_the_end_of_the_filing_day_so_nothing_is_visible_early(session):
    ingest_quarter_zip(session, make_zip(), quarter="2026q3", symbols=["AAA"], since=date(2026, 7, 1))
    settings = AppSettings()
    day_of = datetime(2026, 8, 5, 12, 0)  # midday New York on the filing day: not yet visible
    with as_of(day_of):
        before = smart_money_score_as_of(session, "AAA", day_of, settings)
    next_day = datetime(2026, 8, 6, 12, 0)
    with as_of(next_day):
        after = smart_money_score_as_of(session, "AAA", next_day, settings)
    assert before.inputs.insider_buys == []
    assert len(after.inputs.insider_buys) == 1 and after.inputs.insider_buys[0].is_officer_or_director
