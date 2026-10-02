"""Builders for the 13F tests: small information tables and cover pages in the
real EDGAR shape, and a fake SEC that serves them with the real index layout.
No network."""

from __future__ import annotations

import json
from datetime import datetime

from app.data_providers import sec_13f
from tests.test_sec_8k import FakeSec, submissions

TABLE_NS = "http://www.sec.gov/edgar/document/thirteenf/informationtable"
COVER_NS = "http://www.sec.gov/edgar/thirteenffiler"

FUND = 1067983  # an arbitrary manager CIK
OTHER_FUND = 1336528

CATALOGUE = [
    ("AAPL", "Apple Inc."),
    ("MSFT", "Microsoft Corporation"),
    ("KO", "Coca-Cola Co"),
    ("AMZN", "Amazon.com, Inc."),
    ("NVDA", "NVIDIA Corporation"),
]


def matcher() -> sec_13f.IssuerMatcher:
    return sec_13f.IssuerMatcher(CATALOGUE)


def info_row(issuer, cusip, value, shares, *, put_call=None, kind="SH", cls="COM", discretion="SOLE", other=None) -> str:
    return (
        "<infoTable>"
        f"<nameOfIssuer>{issuer}</nameOfIssuer><titleOfClass>{cls}</titleOfClass><cusip>{cusip}</cusip>"
        f"<value>{value}</value><shrsOrPrnAmt><sshPrnamt>{shares}</sshPrnamt><sshPrnamtType>{kind}</sshPrnamtType></shrsOrPrnAmt>"
        + (f"<putCall>{put_call}</putCall>" if put_call else "")
        + f"<investmentDiscretion>{discretion}</investmentDiscretion>"
        + (f"<otherManager>{other}</otherManager>" if other else "")
        + "<votingAuthority><Sole>1</Sole><Shared>0</Shared><None>0</None></votingAuthority></infoTable>"
    )


def table_xml(*rows: str) -> bytes:
    return (f'<?xml version="1.0" encoding="UTF-8"?><informationTable xmlns="{TABLE_NS}">' + "".join(rows) + "</informationTable>").encode()


def cover_xml(period="06-30-2025", manager="Test Fund LP", *, amendment=None, report_type="13F HOLDINGS REPORT", entries=None, total=None) -> bytes:
    amend = ""
    if amendment:
        amend = f"<isAmendment>true</isAmendment><amendmentInfo><amendmentType>{amendment}</amendmentType></amendmentInfo>"
    summary = ""
    if entries is not None:
        summary = f"<summaryPage><tableEntryTotal>{entries}</tableEntryTotal><tableValueTotal>{total or 0}</tableValueTotal></summaryPage>"
    return (
        f'<?xml version="1.0" encoding="UTF-8"?><edgarSubmission xmlns="{COVER_NS}">'
        f"<headerData><submissionType>{'13F-HR/A' if amendment else '13F-HR'}</submissionType>"
        f"<filerInfo><periodOfReport>{period}</periodOfReport></filerInfo></headerData>"
        f"<formData><coverPage><reportCalendarOrQuarter>{period}</reportCalendarOrQuarter>{amend}"
        f"<filingManager><name>{manager}</name></filingManager><reportType>{report_type}</reportType></coverPage>{summary}</formData>"
        "</edgarSubmission>"
    ).encode()


class FakeFunds(FakeSec):
    """FakeSec that can serve whole 13F filings."""

    def __init__(self):
        super().__init__()
        self._rows: dict[int, list[dict]] = {}

    def serve_13f(self, cik, accession, accepted, period_iso, rows, *, form="13F-HR", manager="Test Fund LP", amendment=None, table_name="infotable.xml"):
        """`rows` are info_row strings; period_iso is YYYY-MM-DD. Returns the filing's folder URL."""
        y, m, d = period_iso.split("-")
        directory = sec_13f.ARCHIVE_DIR_URL.format(cik=cik, accession_nodash=accession.replace("-", ""))
        notice = form.startswith("13F-NT")
        self.documents[f"{directory}/primary_doc.xml"] = cover_xml(
            f"{m}-{d}-{y}", manager, amendment=amendment, report_type="13F NOTICE" if notice else "13F HOLDINGS REPORT"
        )
        if not notice:
            self.documents[f"{directory}/{table_name}"] = table_xml(*rows)
            items = [{"name": "primary_doc.xml"}, {"name": table_name}, {"name": f"{accession}-index.htm"}]
            self.documents[f"{directory}/index.json"] = json.dumps({"directory": {"item": items}}).encode()
        self._rows.setdefault(cik, []).append(
            {
                "accession": accession,
                "accepted": accepted,
                "form": form,
                "filed": accepted[:10],
                "report": period_iso,
                "doc": "xslForm13F_X02/primary_doc.xml",
                "items": "",
            }
        )
        self.submissions[cik] = {**submissions(self._rows[cik]), "name": manager}
        return directory


def when(text: str) -> datetime:
    """An EDGAR acceptance time ("2025-05-15T21:00:00.000Z") as the naive UTC the app stores."""
    return datetime.fromisoformat(text.replace("Z", "").split(".")[0])


# Two quarters of one fund, the story the tests read:
#   Q1 (period 2025-03-31, accepted 2025-05-15): AAPL 1000, MSFT 500, KO 2000, AMZN 100
#   Q2 (period 2025-06-30, accepted 2025-08-14): AAPL 1500 (two lots), MSFT 300, KO 2000, NVDA 400 (new), AMZN gone
Q1_ACCEPTED = "2025-05-15T21:00:00.000Z"
Q2_ACCEPTED = "2025-08-14T21:00:00.000Z"
Q1_ROWS = [
    info_row("APPLE INC", "037833100", 200000, 1000),
    info_row("MICROSOFT CORP", "594918104", 200000, 500),
    info_row("COCA COLA CO", "191216100", 120000, 2000),
    info_row("AMAZON COM INC", "023135106", 19000, 100),
]
Q2_ROWS = [
    info_row("APPLE INC", "037833100", 150000, 750, discretion="SOLE"),
    info_row("APPLE INC", "037833100", 150000, 750, discretion="DFND", other="2"),
    info_row("MICROSOFT CORP", "594918104", 120000, 300),
    info_row("COCA COLA CO", "191216100", 120000, 2000),
    info_row("NVIDIA CORP", "67066G104", 50000, 400),
]


def serve_two_quarters(sec: FakeFunds, cik: int = FUND) -> None:
    sec.serve_13f(cik, "0000000001-25-000001", Q1_ACCEPTED, "2025-03-31", Q1_ROWS)
    sec.serve_13f(cik, "0000000001-25-000002", Q2_ACCEPTED, "2025-06-30", Q2_ROWS)
