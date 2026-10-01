"""ui_check scenario: the Analysis chart's intraday ranges (1D/1W).

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 81xx --fe-port 52xx \
        --out <dir> --routes "/analysis?symbol=NVDA" \
        --seed scripts/ui_scenarios/analysis_intraday.py --allow "502"

Two things are checked in a real browser:
  1. Failure path (mocked): the intraday request answers 502 (the provider is
     rate-limited). The chart must drop to the daily 1M view with a visible
     notice and a Retry button, instead of going blank.
  2. Real path: once the mock is removed, Retry (and the 1W range) load real
     intraday bars from the isolated backend, and the time axis says it is in ET.
     This step uses the live data source, so if that is itself rate-limited the
     scenario reports it and still passes (the notice is the correct outcome).
"""

from __future__ import annotations

UNAVAILABLE = "Intraday data is unavailable right now"
ET_CAPTION = "Time axis: ET"
INTRADAY_URL = "**/api/analysis/*range=1d*"


def interact(page, ctx):
    def reject(route):
        route.fulfill(
            status=502,
            content_type="application/json",
            body='{"detail": "No data available: All providers failed for get_ohlcv(): yfinance: 429"}',
        )

    page.route(INTRADAY_URL, reject)
    page.goto(f"{ctx.fe_url}/analysis?symbol=NVDA")
    page.get_by_role("button", name="1D", exact=True).wait_for(timeout=60_000)
    page.get_by_role("button", name="1D", exact=True).click()

    notice = page.get_by_text(UNAVAILABLE)
    notice.wait_for(timeout=30_000)
    assert page.get_by_role("button", name="Retry").is_visible(), "the notice has no Retry button"
    assert page.get_by_text(ET_CAPTION).count() == 0, "a daily chart must not claim an ET intraday axis"
    ctx.screenshot(page, "intraday-fallback-502")
    ctx.log("502 -> notice + Retry shown, daily 1M chart on screen")

    # The provider recovers: Retry goes back to the range that failed.
    page.unroute(INTRADAY_URL)
    page.get_by_role("button", name="Retry").click()
    try:
        page.get_by_text(ET_CAPTION).wait_for(timeout=60_000)
        ctx.log("Retry -> real 1D intraday chart with the ET axis label")
        ctx.screenshot(page, "intraday-1d-real")
    except Exception:
        ctx.log("Retry -> live intraday source unavailable too; the notice returned (correct fallback)")
        notice.wait_for(timeout=30_000)
        ctx.screenshot(page, "intraday-1d-still-unavailable")
        return

    page.get_by_role("button", name="1W", exact=True).click()
    page.get_by_text(ET_CAPTION).wait_for(timeout=60_000)
    page.wait_for_timeout(1500)
    ctx.screenshot(page, "intraday-1w-real")
    ctx.log("1W -> real 15-minute chart with the ET axis label")
