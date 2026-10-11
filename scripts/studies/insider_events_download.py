"""Test A step 1: download SEC Insider Transactions Data Sets (quarterly zips) via the shared SEC client.
Usage: python scripts/studies/insider_events_download.py OUTDIR 2021q4 2022q1 ...   (read-only research; one at a time)"""
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "backend"))
for line in (ROOT / ".env").read_text().splitlines():
    if line.startswith("SEC_EDGAR_USER_AGENT="):
        os.environ["SEC_EDGAR_USER_AGENT"] = line.split("=", 1)[1].strip()
from app.data_providers.sec_client import get_sec_client

def url(q: str) -> str:
    base = "datastandardsinnovation" if q in ("2026q2", "2026q3") else "structureddata"
    return f"https://www.sec.gov/files/{base}/data/insider-transactions-data-sets/{q}_form345.zip"

if __name__ == "__main__":
    out = Path(sys.argv[1]); out.mkdir(parents=True, exist_ok=True)
    c = get_sec_client()
    for q in sys.argv[2:]:
        p = out / f"{q}_form345.zip"
        if p.exists():
            print(q, "exists", p.stat().st_size); continue
        try:
            b = c.get_bytes(url(q))
        except Exception as e:
            print(q, "FAILED", type(e).__name__, str(e)[:200]); continue
        p.write_bytes(b); print(q, "ok", len(b))
