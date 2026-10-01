"""ui_check hook: the command palette, the price flash and the "as of" freshness label.

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 8120 --fe-port 5220 \
        --tickers "" --routes /settings \
        --seed scripts/ui_seeds/palette_flash_freshness.py --out <dir>

`--tickers ""` keeps the full ~500-symbol universe, so the palette is exercised at
its real size. Only `interact` is defined: it mocks the few endpoints whose prices
have to change on cue (scan, dashboard, analysis, positions, market session) with
Playwright routes, so nothing here depends on live market data, and fakes the
browser clock to age the data. Every check is an assert; a failure is reported as
a hook error by ui_check.py.
"""

from __future__ import annotations

import datetime as dt
import json
import re

# --- fake API payloads ---------------------------------------------------------

STATE = {"price": 100.0, "session": "open"}

EXCURSIONS = {
    "closed_trades": 0,
    "measured": 0,
    "winners": {"n": 0, "avg_mfe_r": None, "avg_mae_r": None},
    "losers": {"n": 0, "avg_mfe_r": None, "avg_mae_r": None},
    "exit_efficiency": None,
    "exit_efficiency_n": 0,
    "losers_reached_1r": 0,
    "winners_near_stop": 0,
}


def scan_result(symbol: str, price: float) -> dict:
    return {
        "symbol": symbol,
        "price": price,
        "change_pct_24h": 1.2,
        "signal": "potential_setup",
        "score": 5,
        "direction": "long",
        "trend": "Bullish",
        "momentum": "Strong",
        "sparkline": [price - 3, price - 2, price - 1, price],
    }


def stats() -> dict:
    return {
        "total_trades": 0,
        "win_rate": 0,
        "total_return": 0.0,
        "avg_rr": None,
        "active_positions": 1,
        "portfolio_value": 100000.0,
        "starting_cash": 100000.0,
        "current_cash": 99000.0,
        "exit_reasons": {},
        "excursions": EXCURSIONS,
    }


def analysis(symbol: str, price: float) -> dict:
    today = dt.date.today()
    candles, ema = [], []
    for i in range(60):
        day = (today - dt.timedelta(days=60 - i)).isoformat()
        close = price - (59 - i) * 0.1
        candles.append({"date": day, "open": close - 0.2, "high": close + 0.5, "low": close - 0.5, "close": close, "volume": 1_000_000})
        ema.append({"date": day, "value": close - 0.3})
    return {
        "symbol": symbol,
        "price": price,
        "ema20": price - 0.3,
        "ema50": price - 1,
        "rsi14": 55,
        "trend": "Bullish",
        "momentum": "Strong",
        "support": [price - 4],
        "resistance": [price + 6],
        "candles": candles,
        "ema20_series": ema,
        "ema50_series": ema,
        "insight_text": "Mocked analysis for the UI check.",
        "ai_provider": "none",
        "ai_error": None,
    }


def position() -> dict:
    return {
        "id": 1,
        "trade_plan_id": None,
        "symbol": "NVDA",
        "direction": "long",
        "entry_price": 100.0,
        "stop_loss": 95.0,
        "tp1": 110.0,
        "tp2": 120.0,
        "shares": 10,
        "opened_at": (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=2)).isoformat(),
        "status": "open",
        "closed_at": None,
        "close_price": None,
        "close_reason": None,
        "realized_pnl": None,
        "realized_r": None,
    }


def session_payload() -> dict:
    now = dt.datetime.now(dt.timezone.utc)
    is_open = STATE["session"] == "open"
    return {
        "state": STATE["session"],
        "is_open": is_open,
        "next_open": (now + dt.timedelta(hours=14)).isoformat(),
        "next_close": (now + dt.timedelta(hours=3)).isoformat(),
        "next_close_is_early": False,
        "holiday_name": None,
        "as_of": now.isoformat(),
    }


def install_mocks(page, ctx) -> None:
    cors = {"access-control-allow-origin": ctx.fe_url, "access-control-allow-credentials": "true"}

    def reply(route, body) -> None:
        if route.request.method == "OPTIONS":
            route.fulfill(status=204, headers={**cors, "access-control-allow-headers": "*", "access-control-allow-methods": "*"})
            return
        route.fulfill(status=200, content_type="application/json", headers=cors, body=json.dumps(body))

    page.route(re.compile(r"/api/scan(\?|$)"), lambda r: reply(r, {"results": [scan_result("NVDA", STATE["price"])], "errors": []}))
    page.route(
        re.compile(r"/api/dashboard/summary"),
        lambda r: reply(
            r,
            {
                "stats": stats(),
                "markets_scanned": 1,
                "potential_setups": 1,
                "top_setups": [scan_result("NVDA", STATE["price"])],
                "latest_trade_plan": None,
                "top_pick_trade_plan": None,
            },
        ),
    )
    page.route(re.compile(r"/api/analysis/"), lambda r: reply(r, analysis("NVDA", STATE["price"])))
    page.route(re.compile(r"/api/portfolio/positions"), lambda r: reply(r, [position()]))
    page.route(re.compile(r"/api/portfolio/stats"), lambda r: reply(r, stats()))
    page.route(re.compile(r"/api/market/session"), lambda r: reply(r, session_payload()))


# --- helpers ---------------------------------------------------------------------

STALENESS_FAST_FORWARD = "17:00"  # minutes:seconds, past the 15 minute cache window
AMBER = "rgb(245, 158, 11)"


def refetch_on_refocus(page) -> None:
    """Age the queries past react-query's 15 s staleTime, then fire the refocus that refetches them."""
    page.clock.fast_forward(20_000)
    page.evaluate("window.dispatchEvent(new Event('visibilitychange'))")


def expect_flash(page, direction: str) -> None:
    page.locator(f'[data-flash="{direction}"]').first.wait_for(state="visible", timeout=5_000)
    cls = page.locator(f'[data-flash="{direction}"]').first.get_attribute("class") or ""
    assert f"flash-{direction}" in cls, cls


def expect_no_flash(page) -> None:
    assert page.locator("[data-flash]").count() == 0, "a flash is showing that should not be"


def wait_flash_gone(page) -> None:
    page.wait_for_function("document.querySelectorAll('[data-flash]').length === 0", timeout=5_000)


def check_freshness(page, ctx, name: str) -> None:
    label = page.locator(".freshness").first
    label.wait_for(state="visible", timeout=10_000)
    text = label.inner_text()
    assert re.search(r"as of \d{2}:\d{2}", text), f"{name}: freshness text {text!r}"
    assert label.get_attribute("data-freshness") == "fresh", f"{name}: should start fresh"
    # Age the page past the cache window without refetching: the label must turn amber.
    page.clock.fast_forward(STALENESS_FAST_FORWARD)
    page.wait_for_function("document.querySelector('.freshness')?.dataset.freshness === 'stale'", timeout=5_000)
    colour = page.evaluate("getComputedStyle(document.querySelector('.freshness')).color")
    assert colour == AMBER, f"{name}: stale label colour {colour}"
    ctx.log(f"{name}: freshness {text!r} then amber after {STALENESS_FAST_FORWARD}")


def new_page(context, ctx, width=1440, height=900):
    page = context.new_page()
    page.set_viewport_size({"width": width, "height": height})
    return page


# --- the checks -------------------------------------------------------------------


def check_palette(page, ctx) -> None:
    universe = ctx.api("GET", "/api/universe").json()
    assert len(universe) > 100, f"expected the full universe, got {len(universe)} (run with --tickers '')"
    page.goto(ctx.fe_url + "/settings")
    page.wait_for_load_state("networkidle")

    # Opens with the keyboard, focuses the field, has the dialog roles.
    page.keyboard.press("Control+K")
    dialog = page.get_by_role("dialog", name="Command palette")
    dialog.wait_for(state="visible")
    assert page.evaluate("document.activeElement?.getAttribute('role')") == "combobox", "field should have focus"
    assert dialog.get_attribute("aria-modal") == "true"
    # Empty query: pages are listed (recents appear after the first use).
    assert dialog.get_by_role("option").count() >= 6
    ctx.screenshot(page, "palette-empty")

    # Ctrl+K again toggles it closed; Esc closes it; focus goes back to the page.
    page.keyboard.press("Control+K")
    dialog.wait_for(state="hidden")
    page.keyboard.press("Control+K")
    dialog.wait_for(state="visible")
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")

    # Meta+K (Cmd+K on a Mac) also opens it.
    page.keyboard.press("Meta+K")
    dialog.wait_for(state="visible")

    # Type a symbol: first row is that symbol; arrows move the selection; Tab can't leave the field.
    page.keyboard.type("nvd")
    first = dialog.get_by_role("option").first
    assert "NVDA" in first.inner_text(), first.inner_text()
    assert first.get_attribute("aria-selected") == "true"
    page.keyboard.press("ArrowDown")
    assert dialog.get_by_role("option").nth(1).get_attribute("aria-selected") == "true"
    assert first.get_attribute("aria-selected") == "false"
    page.keyboard.press("ArrowUp")
    assert first.get_attribute("aria-selected") == "true"
    active_id = page.evaluate("document.activeElement.getAttribute('aria-activedescendant')")
    assert active_id and page.locator(f"#{active_id}").get_attribute("aria-selected") == "true"
    page.keyboard.press("Tab")
    assert page.evaluate("document.activeElement?.getAttribute('role')") == "combobox", "Tab escaped the dialog"
    ctx.screenshot(page, "palette-nvda")

    # Enter goes to the symbol's Analysis page and closes the palette.
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/analysis\?symbol=NVDA$"))
    dialog.wait_for(state="hidden")

    # The choice is remembered; Shift+Enter on a symbol goes to Trade Plans with it selected.
    page.keyboard.press("Control+K")
    dialog.wait_for(state="visible")
    assert "recent" in dialog.inner_text().lower()  # the header is upper-cased by CSS
    assert "NVDA" in dialog.get_by_role("option").first.inner_text()
    page.keyboard.type("aapl")
    page.keyboard.press("Shift+Enter")
    page.wait_for_url(re.compile(r"/trade-plans\?symbol=AAPL$"))

    # Page names, and the trade-plan action row.
    page.keyboard.press("Control+K")
    page.keyboard.type("portf")
    assert "Portfolio" in dialog.get_by_role("option").first.inner_text()
    page.keyboard.press("Enter")
    page.wait_for_url(re.compile(r"/portfolio$"))
    page.keyboard.press("Control+K")
    page.keyboard.type("msft")
    dialog.get_by_role("option", name=re.compile("Generate trade plan for MSFT")).click()
    page.wait_for_url(re.compile(r"/trade-plans\?symbol=MSFT$"))

    # A broad query on the 500-symbol universe renders a capped list, quickly.
    page.keyboard.press("Control+K")
    page.keyboard.type("a")
    n = dialog.get_by_role("option").count()
    assert 0 < n <= 12, n
    page.keyboard.press("Backspace")
    page.keyboard.type("zzzz")
    assert dialog.get_by_role("option").count() == 0
    assert "No symbol or page matches" in dialog.inner_text()
    ctx.screenshot(page, "palette-no-match")

    # Clicking the backdrop closes it; the sidebar button opens it.
    page.mouse.click(5, 5)
    dialog.wait_for(state="hidden")
    page.get_by_role("button", name="Search symbols and pages").first.click()
    dialog.wait_for(state="visible")
    page.keyboard.press("Escape")
    dialog.wait_for(state="hidden")
    ctx.log(f"palette: ok ({len(universe)} symbols, {n} rows for 'a')")


def check_palette_mobile(context, ctx) -> None:
    page = new_page(context, ctx, 390, 844)
    page.goto(ctx.fe_url + "/settings")
    page.wait_for_load_state("networkidle")
    # On a phone the sidebar is off-canvas; the search icon in the top bar is the way in.
    page.locator(".mobile-menu-btn").get_by_role("button", name="Search symbols and pages").click()
    dialog = page.get_by_role("dialog", name="Command palette")
    dialog.wait_for(state="visible")
    page.keyboard.type("bank")
    box = dialog.bounding_box()
    assert box["x"] >= 0 and box["x"] + box["width"] <= 390, box
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "page scrolls sideways"
    ctx.screenshot(page, "palette-mobile")
    dialog.get_by_role("option").first.click()
    page.wait_for_url(re.compile(r"/analysis\?symbol="))
    page.close()
    ctx.log("palette mobile: ok")


def check_scan(context, ctx) -> None:
    page = new_page(context, ctx)
    page.clock.install()
    install_mocks(page, ctx)
    STATE.update(price=100.0, session="closed")
    page.goto(ctx.fe_url + "/scan")
    page.locator("tbody tr").first.wait_for(state="visible", timeout=15_000)
    page.wait_for_timeout(500)
    expect_no_flash(page)  # first mount is not a change
    text = page.locator(".freshness").first.inner_text()
    assert "US market closed" in text, text
    ctx.screenshot(page, "scan-before")

    STATE["price"] = 101.5
    page.get_by_role("button", name="Rescan").click()
    expect_flash(page, "up")
    ctx.screenshot(page, "scan-flash-up")
    wait_flash_gone(page)

    STATE["price"] = 99.0
    page.get_by_role("button", name="Rescan").click()
    expect_flash(page, "down")
    wait_flash_gone(page)

    # Reduced motion: no animation, but the change is still marked with a static highlight.
    page.emulate_media(reduced_motion="reduce")
    STATE["price"] = 103.0
    page.get_by_role("button", name="Rescan").click()
    expect_flash(page, "up")
    style = page.evaluate(
        "(() => { const s = getComputedStyle(document.querySelector('[data-flash]')); return [s.animationName, s.backgroundColor]; })()"
    )
    assert style[0] == "none", style
    assert style[1] != "rgba(0, 0, 0, 0)", style
    wait_flash_gone(page)
    page.emulate_media(reduced_motion="no-preference")

    # Unchanged data does not flash.
    page.get_by_role("button", name="Rescan").click()
    page.wait_for_timeout(600)
    expect_no_flash(page)

    check_freshness(page, ctx, "scan")
    ctx.screenshot(page, "scan-stale")
    page.close()


def check_dashboard(context, ctx) -> None:
    page = new_page(context, ctx)
    page.clock.install()
    install_mocks(page, ctx)
    STATE.update(price=100.0, session="open")
    page.goto(ctx.fe_url + "/")
    page.get_by_text("Top Setups Today").wait_for(state="visible", timeout=15_000)
    page.wait_for_timeout(500)
    expect_no_flash(page)
    text = page.locator(".freshness").first.inner_text()
    assert "closed" not in text, text  # the market is mocked open
    ctx.screenshot(page, "dashboard-before")

    STATE["price"] = 102.0
    refetch_on_refocus(page)
    expect_flash(page, "up")
    ctx.screenshot(page, "dashboard-flash")
    wait_flash_gone(page)
    check_freshness(page, ctx, "dashboard")
    page.close()


def check_portfolio(context, ctx) -> None:
    page = new_page(context, ctx)
    page.clock.install()
    install_mocks(page, ctx)
    STATE.update(price=104.0, session="open")
    page.goto(ctx.fe_url + "/portfolio")
    page.get_by_text("unrealized").first.wait_for(state="visible", timeout=15_000)
    page.wait_for_timeout(500)
    expect_no_flash(page)
    ctx.screenshot(page, "portfolio-before")

    STATE["price"] = 106.0
    refetch_on_refocus(page)
    expect_flash(page, "up")
    assert page.locator('[data-flash="up"]').count() >= 2, "both the last price and the P&L should flash"
    ctx.screenshot(page, "portfolio-flash")
    wait_flash_gone(page)
    check_freshness(page, ctx, "portfolio")
    page.close()


def check_analysis(context, ctx) -> None:
    page = new_page(context, ctx)
    page.clock.install()
    install_mocks(page, ctx)
    STATE.update(price=100.0, session="open")
    page.goto(ctx.fe_url + "/analysis?symbol=NVDA")
    page.get_by_text("Mocked analysis").first.wait_for(state="visible", timeout=15_000)
    page.wait_for_timeout(500)
    expect_no_flash(page)

    STATE["price"] = 99.0
    refetch_on_refocus(page)
    expect_flash(page, "down")
    ctx.screenshot(page, "analysis-flash")
    wait_flash_gone(page)

    # Switching symbol is not a price move: the new symbol's first price must not flash.
    page.goto(ctx.fe_url + "/analysis?symbol=AAPL")
    page.wait_for_timeout(1_000)
    expect_no_flash(page)
    check_freshness(page, ctx, "analysis")
    page.close()


def interact(page, ctx) -> None:
    context = page.context
    check_palette(page, ctx)
    check_palette_mobile(context, ctx)
    check_scan(context, ctx)
    check_dashboard(context, ctx)
    check_portfolio(context, ctx)
    check_analysis(context, ctx)
