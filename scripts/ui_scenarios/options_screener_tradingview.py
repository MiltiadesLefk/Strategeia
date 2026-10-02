"""ui_check scenario: Options tab, Screener page and the click-to-load TradingView widget.

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 81xx --fe-port 52xx \
        --out <dir> --routes /screener "/analysis?symbol=NVDA" /terminal \
        --seed scripts/ui_scenarios/options_screener_tradingview.py

Checked in a real browser:
  1. Opening the Analysis TradingView tab and the Market Terminal sends no request to
     TradingView; only after the load button is clicked does the sandboxed
     iframe appear and its address get requested. Those requests are answered by an
     empty stub here, so the check never talks to TradingView itself.
  2. The Options tab shows either a chain or the clean "no options" state.
  3. The Screener runs a rule over the dev symbols, shows "N of M scanned",
     saves the screen, and the saved screen then shows up as a button.
"""

from __future__ import annotations


def _watch(page, seen):
    def stub(route):
        seen.append(route.request.url)
        route.fulfill(status=200, content_type="text/html", body="<!doctype html><title>stub</title>")

    # On the context so frames inside the page are covered too.
    page.context.route(lambda url: "tradingview" in url, stub)


def interact(page, ctx):
    seen: list[str] = []
    _watch(page, seen)

    # 1. TradingView: nothing until the click.
    page.goto(f"{ctx.fe_url}/analysis?symbol=NVDA")
    page.get_by_role("button", name="TradingView", exact=True).wait_for(timeout=60_000)
    page.get_by_role("button", name="TradingView", exact=True).click()
    load = page.locator("[data-tradingview-load='symbol-overview']")
    load.wait_for(timeout=30_000)
    page.wait_for_timeout(1500)
    assert not seen, f"TradingView was requested before any click: {seen}"
    assert page.locator("iframe[data-tradingview-frame]").count() == 0
    ctx.screenshot(page, "tradingview-before-click")
    load.click()
    frame = page.locator("iframe[data-tradingview-frame='symbol-overview']")
    frame.wait_for(timeout=15_000)
    sandbox = frame.get_attribute("sandbox") or ""
    assert "allow-scripts" in sandbox and "allow-top-navigation" not in sandbox, f"the widget frame must stay sandboxed: {sandbox}"
    assert (frame.get_attribute("src") or "").startswith("https://www.tradingview-widget.com/embed-widget/")
    assert frame.get_attribute("srcdoc") is None, "no TradingView script may run inside this app's own page"
    page.wait_for_timeout(1500)
    assert any("embed-widget/symbol-overview" in u for u in seen), f"the widget frame was not requested after the click: {seen}"
    ctx.screenshot(page, "tradingview-after-click")
    ctx.log("TradingView: no request before the click; sandboxed cross-origin iframe after it")

    # Market Terminal ticker tape: same rule.
    seen.clear()
    page.goto(f"{ctx.fe_url}/terminal")
    page.locator("[data-tradingview-load='ticker-tape']").wait_for(timeout=30_000)
    page.wait_for_timeout(1000)
    assert not seen, f"ticker tape requested TradingView on mount: {seen}"
    ctx.log("Terminal: ticker tape waits for its click")

    # 2. Options tab.
    page.goto(f"{ctx.fe_url}/analysis?symbol=NVDA")
    page.get_by_role("button", name="Options", exact=True).wait_for(timeout=60_000)
    page.get_by_role("button", name="Options", exact=True).click()
    page.locator("[data-options-state]").wait_for(timeout=60_000)
    state = page.locator("[data-options-state]").first.get_attribute("data-options-state")
    ctx.log(f"Options tab state: {state}")
    ctx.screenshot(page, "options-tab")

    # 3. Screener: one rule, run, save, saved screen appears.
    page.goto(f"{ctx.fe_url}/screener")
    page.get_by_role("button", name="Add rule").wait_for(timeout=30_000)
    page.get_by_role("button", name="Add rule").click()
    page.get_by_label("Operator").select_option("gt")
    page.get_by_label("Value", exact=True).fill("0")
    page.get_by_role("button", name="Run screen").click()
    page.locator("[data-screener-results]").wait_for(timeout=120_000)
    text = page.locator("[data-screener-results]").inner_text()
    assert " of " in text and "symbols scanned" in text, text[:200]
    ctx.screenshot(page, "screener-results")
    page.get_by_label("Screen name").fill("Check screen")
    page.get_by_role("button", name="Save screen").click()
    page.locator("[data-screener-saved]").get_by_role("button", name="Check screen", exact=True).wait_for(timeout=15_000)
    ctx.screenshot(page, "screener-saved")
    ctx.log("Screener: ran, showed the scanned count, saved the screen")
