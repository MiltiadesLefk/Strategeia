"""ui_check scenario: the Ratings page rates one company, rates all, and shows a past run's own table.

    backend/.venv/Scripts/python scripts/ui_check.py --be-port 81xx --fe-port 52xx \
        --out <dir> --routes /ratings --seed scripts/ui_scenarios/ratings_page.py
Uses the live data sources.
"""

from __future__ import annotations


def interact(page, ctx):
    page.goto(f"{ctx.fe_url}/ratings")
    page.get_by_text("Latest per company").wait_for(timeout=60_000)
    page.locator("select").first.select_option(index=1)  # rates the first company
    page.get_by_role("button", name="Rate", exact=True).first.wait_for(timeout=120_000)
    ctx.log("rated one company; it has its own Rate button")
    page.get_by_role("button", name="Rate all").click()
    run = page.get_by_role("button").filter(has_text="Manual ·").first
    run.wait_for(timeout=120_000)
    page.get_by_role("button", name="Rate all").wait_for(timeout=180_000)  # back from "Rating…"
    page.get_by_role("button", name="Latest per company").click()
    run.click()
    page.get_by_text("show table").first.click()
    page.get_by_text("What it measures, and why it scored this way").wait_for(timeout=30_000)
    ctx.log("a past run opens its own explained table")
    ctx.screenshot(page, "ratings-run")
