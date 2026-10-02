"""ui_check hook: synthetic 13F holdings and 5% owner filings for the Smart Money page.

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 8140 --fe-port 5240 \
        --routes /smart-money /settings --seed scripts/ui_seeds/funds.py --out <dir>

Writes made-up filings for two followed funds (two quarters each, so the Funds tab has
new / added / trimmed / sold-out rows) and the recorded 13D and 13G documents from
backend/tests/fixtures/funds into the throwaway database. Nothing touches the network.
interact() then opens the Funds and 5% owners tabs and screenshots them.
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[2] / "backend"
FIXTURES = BACKEND / "tests" / "fixtures" / "funds"
NS_TABLE = "http://www.sec.gov/edgar/document/thirteenf/informationtable"
NS_COVER = "http://www.sec.gov/edgar/thirteenffiler"


def _row(issuer, cusip, value, shares):
    return (
        f"<infoTable><nameOfIssuer>{issuer}</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>{cusip}</cusip>"
        f"<value>{value}</value><shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt>"
        "<investmentDiscretion>SOLE</investmentDiscretion></infoTable>"
    )


def _table(*rows):
    return f'<informationTable xmlns="{NS_TABLE}">{"".join(rows)}</informationTable>'.encode()


def _cover(period, manager):
    return (
        f'<edgarSubmission xmlns="{NS_COVER}"><headerData><submissionType>13F-HR</submissionType></headerData>'
        f"<formData><coverPage><reportCalendarOrQuarter>{period}</reportCalendarOrQuarter>"
        f"<filingManager><name>{manager}</name></filingManager><reportType>13F HOLDINGS REPORT</reportType></coverPage></formData>"
        "</edgarSubmission>"
    ).encode()


def seed(ctx) -> None:
    sys.path.insert(0, str(BACKEND))
    from sqlmodel import Session, create_engine

    from app.data_providers.sec_13dg import OwnershipFiling, ingest_ownership_filing
    from app.data_providers.sec_13f import Filing13F, IssuerMatcher, ingest_13f_filing

    matcher = IssuerMatcher(
        [("AAPL", "Apple Inc."), ("MSFT", "Microsoft Corporation"), ("KO", "Coca-Cola Co"), ("NVDA", "NVIDIA Corporation"), ("AMZN", "Amazon.com, Inc.")]
    )
    now = datetime.now(timezone.utc).replace(tzinfo=None, microsecond=0)
    q_prev_accepted, q_now_accepted = now - timedelta(days=100), now - timedelta(days=12)
    prev_rows = [
        _row("APPLE INC", "037833100", 200000000, 1000000),
        _row("MICROSOFT CORP", "594918104", 200000000, 500000),
        _row("COCA COLA CO", "191216100", 120000000, 2000000),
        _row("AMAZON COM INC", "023135106", 19000000, 100000),
    ]
    now_rows = [
        _row("APPLE INC", "037833100", 300000000, 1500000),
        _row("MICROSOFT CORP", "594918104", 120000000, 300000),
        _row("COCA COLA CO", "191216100", 120000000, 2000000),
        _row("NVIDIA CORP", "67066G104", 50000000, 400000),
        _row("MYSTERY HOLDINGS CO", "999999999", 9000000, 90000),
    ]
    engine = create_engine(f"sqlite:///{ctx.db_path}")
    with Session(engine) as s:
        for cik, manager in ((1067983, "Berkshire Hathaway Inc"), (1336528, "Pershing Square Capital Management")):
            for n, (accepted, period_text, period, rows) in enumerate(
                ((q_prev_accepted, "03-31-2026", datetime(2026, 3, 31), prev_rows), (q_now_accepted, "06-30-2026", datetime(2026, 6, 30), now_rows))
            ):
                accession = f"0000{cik}-26-00000{n + 1}"
                filing = Filing13F(cik, accession, "13F-HR", accepted.date(), accepted, period.date(), "primary_doc.xml")
                ingest_13f_filing(
                    s, filing, matcher=matcher, cover_xml=_cover(period_text, manager), table_xml=_table(*rows), manager_name=manager
                )
        for accession, accepted, form, cik, symbol, name in (
            ("0000000011-26-000001", now - timedelta(days=4), "SCHEDULE 13D", 1981792, "HHH", "schedule_13d_amendment.xml"),
            ("0000000011-26-000002", now - timedelta(days=9), "SCHEDULE 13G", 320193, "AAPL", "schedule_13g.xml"),
        ):
            xml = (FIXTURES / name).read_bytes()
            if form == "SCHEDULE 13D":  # present the recorded amendment as a first filing
                xml = xml.replace(b"SCHEDULE 13D/A", b"SCHEDULE 13D")
            ingest_ownership_filing(s, OwnershipFiling(cik, accession, form, accepted.date(), accepted, "primary_doc.xml"), symbol, xml=xml)
        s.commit()
    engine.dispose()
    ctx.log("seeded two funds x two quarters of 13F holdings and two 5% owner filings")


def interact(page, ctx) -> None:
    page.goto(f"{ctx.fe_url}/smart-money")
    page.get_by_role("button", name="Funds").click()
    page.wait_for_selector("[data-testid=fund-detail]", timeout=15000)
    ctx.screenshot(page, "funds_tab")
    page.get_by_role("button", name="5% owners").click()
    page.wait_for_selector("[data-testid=ownership-notes]", timeout=15000)
    page.wait_for_selector("table", timeout=15000)
    ctx.screenshot(page, "ownership_tab")
