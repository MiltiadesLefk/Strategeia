"""Test A step 2: parse SEC insider zips into runtime/insider_events.db (filings, owners, purchases). Read-only on everything else.
Usage: python scripts/studies/insider_events_load.py ZIPDIR"""
import csv, io, re, sqlite3, sys, zipfile
from datetime import datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
DB = ROOT / "runtime" / "insider_events.db"
csv.field_size_limit(10**9)
SYM = re.compile(r"^[A-Z]{1,5}([.\-][A-Z])?$")

def clean_symbol(raw: str) -> str | None:
    s = (raw or "").upper().strip()
    if not s:
        return None
    tok = re.split(r"[,\s/;]+", s)[0]
    return tok if SYM.match(tok) else None

def rows(z, name):
    with z.open(name) as f:
        yield from csv.DictReader(io.TextIOWrapper(f, encoding="utf-8", errors="replace", newline=""), delimiter="\t")

def main(zipdir: Path):
    DB.parent.mkdir(exist_ok=True)
    if DB.exists(): DB.unlink()
    con = sqlite3.connect(DB)
    con.executescript("""
    CREATE TABLE filings(accession TEXT PRIMARY KEY, filing_date TEXT, issuer_cik TEXT, symbol TEXT, issuer_name TEXT, doc_type TEXT, quarter TEXT);
    CREATE TABLE owners(accession TEXT, owner_cik TEXT, owner_name TEXT, relationship TEXT, title TEXT);
    CREATE TABLE purchases(accession TEXT, sk TEXT, trans_date TEXT, security TEXT, shares REAL, price REAL, PRIMARY KEY(accession, sk));
    CREATE TABLE load_log(quarter TEXT, form4_filings INT, form4_with_purchase INT, purchases INT, bad_symbol_filings INT);
    """)
    seen = set()
    for zp in sorted(zipdir.glob("*_form345.zip")):
        q = zp.name[:6]
        z = zipfile.ZipFile(zp)
        fil = {}
        bad = 0
        for r in rows(z, "SUBMISSION.tsv"):
            if r["DOCUMENT_TYPE"].strip() != "4" or r["ACCESSION_NUMBER"] in seen:
                continue
            sym = clean_symbol(r["ISSUERTRADINGSYMBOL"])
            if sym is None:
                bad += 1; continue
            d = datetime.strptime(r["FILING_DATE"].strip().title(), "%d-%b-%Y").date().isoformat()
            fil[r["ACCESSION_NUMBER"]] = (d, r["ISSUERCIK"].strip(), sym, r["ISSUERNAME"].strip())
        pur = []
        for r in rows(z, "NONDERIV_TRANS.tsv"):
            a = r["ACCESSION_NUMBER"]
            if a not in fil or r["TRANS_CODE"].strip() != "P" or r["TRANS_ACQUIRED_DISP_CD"].strip() != "A":
                continue
            try:
                sh = float(r["TRANS_SHARES"]); px = float(r["TRANS_PRICEPERSHARE"])
            except ValueError:
                continue
            if sh > 0 and px > 0:
                pur.append((a, r["NONDERIV_TRANS_SK"], r["TRANS_DATE"], r["SECURITY_TITLE"], sh, px))
        have = {p[0] for p in pur}
        con.executemany("INSERT OR IGNORE INTO purchases VALUES(?,?,?,?,?,?)", pur)
        con.executemany("INSERT INTO filings VALUES(?,?,?,?,?,?,?)", [(a, *v, "4", q) for a, v in fil.items() if a in have])
        own = [(r["ACCESSION_NUMBER"], r["RPTOWNERCIK"].strip(), r["RPTOWNERNAME"], r["RPTOWNER_RELATIONSHIP"], r["RPTOWNER_TITLE"])
               for r in rows(z, "REPORTINGOWNER.tsv") if r["ACCESSION_NUMBER"] in have]
        con.executemany("INSERT INTO owners VALUES(?,?,?,?,?)", own)
        seen.update(fil)
        con.execute("INSERT INTO load_log VALUES(?,?,?,?,?)", (q, len(fil), len(have), len(pur), bad))
        con.commit()
        print(q, "form4", len(fil), "with_purchase", len(have), "purchases", len(pur), "bad_symbol", bad, flush=True)
    print(con.execute("SELECT count(*), count(distinct symbol), min(filing_date), max(filing_date) FROM filings").fetchone())
    con.close()

if __name__ == "__main__":
    main(Path(sys.argv[1]))
