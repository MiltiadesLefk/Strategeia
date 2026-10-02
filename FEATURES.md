# Strategeia: how the app works

*What the app does today, checked against the code. Updated as features land.*

## Contents

1. [What the app is](#1-what-the-app-is)
2. [The flow, end to end](#2-the-flow-end-to-end)
3. [The pages](#3-the-pages)
4. [How one stock is evaluated](#4-how-one-stock-is-evaluated)
5. [The confidence score, part by part](#5-the-confidence-score-part-by-part)
6. [Entry, stop, targets and position size](#6-entry-stop-targets-and-position-size)
7. [The paper-trading engine](#7-the-paper-trading-engine)
8. [Automation: what runs by itself](#8-automation-what-runs-by-itself)
9. [The AI](#9-the-ai)
10. [Telegram notifications](#10-telegram-notifications)
11. [Where the data comes from](#11-where-the-data-comes-from)
12. [Settings reference](#12-settings-reference)
13. [Login and security](#13-login-and-security)
14. [Limitations and known bugs](#14-limitations-and-known-bugs)
15. [Running it, and where things are stored](#15-running-it-and-where-things-are-stored)
16. [API endpoints](#16-api-endpoints)
17. [Glossary](#17-glossary)
18. [Backtesting](#18-backtesting)
19. [Watchers](#19-watchers)
20. [Smart Money](#20-smart-money)
21. [Market Terminal](#21-market-terminal)
22. [Calendar and earnings preview](#22-calendar-and-earnings-preview)
23. [Notifications: morning note, weekly digest and price alerts](#23-notifications-morning-note-weekly-digest-and-price-alerts)
24. [Sleeves: one paper account per trading style](#24-sleeves-one-paper-account-per-trading-style)
25. [AI Committee](#25-ai-committee)

---

## 1. What the app is

A personal, single-user app for **swing trading US stocks** (plus BTC, ETH and SOL) on **daily charts**,
with trades held for **days to weeks**. It:

1. **scans** a list of stocks,
2. **evaluates** each one: the chart, fundamentals, news, insider buying, options, the weekly chart,
   the broad market and the market's mood,
3. writes a **trade plan** (entry, stop, two targets, share count), or an explicit **"no trade"** with
   the reason,
4. **paper-trades** the plan: simulated money at real market prices,
5. shows **honest statistics** computed from the simulated trades.

**Fixed rules make every decision.** The AI only explains the numbers, and optionally gives a second
opinion that can *stop* a trade but never start one or change it. The data is free and the money is
fake: no real broker is connected.

---

## 2. The flow, end to end

```
 STOCK LIST  your saved watchlist, else backend/data/sp500.csv (~500 symbols); scans use the first 50; the test filter overrides both (NVDA, AAPL in Docker)
     │
     ▼
 MARKET SCAN ─ chart-only score 0–6 per stock → "potential setup" / "watching" / "no signal"
     │
     ▼
 TRADE PLAN (per stock: you click Generate, or the auto-scan / auto-trade loop runs it)
     │  chart + 9 more checks → confidence 0–100 %
     ├─ no trend, AI veto, or confidence under 30 % ──▶ saved as "no trade", with the reason
     └─ otherwise: entry, stop, TP1, TP2, share count ──▶ saved as "pending"
                                                          │ + Telegram message
     ▼
 IS THE MARKET OPEN? (US stocks: 09:30–16:00 New York time on trading days; crypto: always)
     ├─ no: night, weekend or US holiday ──▶ stays "pending", never filled; REDONE from fresh
     │                                        data at 09:45 New York time on the next trading
     │                                        day, and only that new plan can trade
     ▼ yes
 AUTO-EXECUTE (on by default) ─ safety checks ─▶ PAPER POSITION opened ("executed")
     │
     ▼
 EXIT CHECKS every 15 min during US market hours ─ stop or TP1 touched, or 20 trading days up ─▶ position CLOSED
     │
     ▼
 STATISTICS AND EQUITY CURVE ─ computed from the real simulated trades
```

Behind all of it, every 15 minutes, a **health check** confirms the market data, the AI and Finnhub
still answer, and messages you on Telegram if one goes down or comes back.

---

## 3. The pages

The sidebar holds Dashboard, Market Scan, Analysis, Calendar (§22), Trade Plans, Portfolio, Market Terminal (§21), Smart Money (§20), Backtest Lab (§18), AI Committee (§25) and Settings, plus status
pills (AI, AI overlay, Finnhub, Telegram) that expand when something is down, and a log-out button.

### Backtest Lab
The **Backtest Lab** (`/backtests`) is where past-data replays are started and read; §18 explains the
numbers. From top to bottom: a **New run** form, the list of runs, and, for the run you open, its
detail. The form has symbol buttons (NVDA + AAPL as a machinery check, or your current watchlist, or type
your own), start and end dates limited to what is stored, "decide every N days", a few overrides that show
your live setting until you type over it, and the random-entry baseline switch with its number of runs. If
price history is missing for any symbol (or SPY or ^VIX) the form says so and shows the exact
`python scripts/preload_history.py ...` command to run first; a run never downloads anything. The run
detail has a progress bar with Cancel (it says "Main run" or "Baseline seed 3/10"), stat cards, equity
against SPY (linear or log), a drawdown chart, tables by year and month, the trades (click a heading to
sort), how trades ended, long against short, the scorecard, the baseline chart and what the run could
and couldn't test.

### On every page: search, price flash and the "as of" label
- **Search (Ctrl+K, or Cmd+K on a Mac):** opens a search box over any page. It is also the Search button
  at the top of the sidebar (a magnifier in the top bar on a phone). Type a ticker, a company name or a
  sector and the matching stocks from the stock list appear; the closest match comes first, and a ticker
  typed exactly or as a start beats a company name. Page names (Dashboard, Portfolio, ...) match too.
  **Up/Down** move, **Enter** opens the stock's Analysis page, **Shift+Enter** opens Trade Plans with that
  stock chosen (you still press Generate yourself), and **Esc** closes it. A "Generate trade plan for ..."
  row does the same thing as Shift+Enter. The last five stocks you opened are listed when the box is
  empty; they are remembered in this browser only. At most 12 rows are shown, however long the list is.
  The whole thing works from the keyboard and is readable by screen readers.
- **Price flash:** when a price you are looking at changes after a refresh, it briefly flashes green
  (went up) or red (went down). It shows on the Market Scan price, the Dashboard top-setup prices and
  the top-pick price, each open position's last price and unrealized P&L on Portfolio, and the price at
  the top of Analysis. It never flashes when a page first opens, when you switch to a different stock, or
  when the number did not change. With the system "reduce motion" setting on, the colour is shown as a
  steady highlight for a moment instead of fading.
- **"As of" label:** Dashboard, Market Scan, Portfolio and Analysis show a small line such as
  *as of 14:32 · 3 min ago*. Hover it for what the time means. It is the moment this browser last
  received the data, because the server does not put its own timestamp on scan, dashboard or analysis
  results (if an endpoint ever supplies one, the label uses that and says so). It is not the exchange
  time of the last trade. The line turns **amber** once the data is older than 15 minutes, the length of
  the price cache (§11), and adds **US market closed** (with the reason, such as a holiday or after hours)
  when the market is not open. Crypto pairs never say the market is closed. On Portfolio the header label
  is the older of the two requests it is built from, and each open position also shows how old its last
  price is.

### Dashboard
- **Stat cards:** Portfolio Value, Total Return, Win Rate, Total Trades, Avg R, Active Positions.
  Rate-style numbers show how many trades they're based on. Sparklines appear only where a real
  history exists (the equity curve).
- **Market status:** the US market's state (open, pre-market, after hours, closed, or closed for a
  named holiday) and a countdown to the next open or close (1:00 pm early closes included); beside it
  sits the "as of" label for the data. It comes from the backend's market calendar (§8), the same one that decides
  whether a position may open.
- **Top Setups Today:** the 5 highest-scoring stocks from a fresh scan of the whole list.
- **Top Pick:** the latest plan for the #1 setup, with its technical read, AI take and risk.
- **Latest Trade Plan:** the most recent plan for any stock.
- Opening the dashboard scans the list (answered from the cache when fresh) and runs the exit check,
  so a position whose stop or TP1 was hit since the last check closes straight away. Opening it
  **never adds a point to the equity curve** (§7).

### Market Scan (Market Scanner)
- **Screen presets:** a row of chips above the table: Value, Growth, Quality, Short ideas, Special situations. Pick one and the table is replaced by the symbols that fit it, each with the criteria it met. See "Screen presets" below.
- A table of every stock in the list: price, 24-hour change, trend, momentum, score (0–6), signal and
  a small price trend line.
- **Signals:** score ≥ 4 is a **potential setup**, 2–3 is **watching**, and below 2 is **no signal**.
- A filter to show a single symbol.
- **Auto-trade button:** runs the full evaluation loop now (see §8), at most once every 60 seconds.

### Analysis
- **Valuation tab:** see "Valuation tab (Analysis)" below.
- **Options and TradingView tabs:** see "Options tab (Analysis)" and "TradingView widgets" below.
- **Price chart** (candles) with EMA20, EMA50, Bollinger bands, support and resistance lines, RSI and
  MACD. A legend under your cursor shows the exact numbers for that bar.
- **Ranges:**
  - **1D** uses 5-minute bars and **1W** uses 15-minute bars. Both also show VWAP, and their time axis
    and the hover label are in **US Eastern time (ET)**, daylight saving included (the open reads
    09:30). A small "Time axis: ET" note sits under the chart. Daily ranges are unchanged.
  - **If intraday data is unavailable** (Yahoo is rate-limited and there is no second free source),
    the chart switches to the 1M daily view and shows a notice, "Intraday data is unavailable right
    now (the provider is rate-limited); showing daily bars", with a **Retry** button that goes back
    to the range that failed. Nothing is made up to fill the gap.
  - **1M, 3M, 6M and 1Y** use daily bars. The indicators are always computed on a full year, then
    trimmed to the view.
- **Potential Breakout** badge: shown when there's a clear trend and price is within 2% of the next
  level.
- **AI Insight:** a short explanation of the chart.
- **The research panel** (the old Research page now opens here):
  - **AI Summary**,
  - **Key Financials** (a revenue chart),
  - **Key Catalysts**,
  - **Upcoming Earnings**, with analyst estimates under "About These Estimates" and an **Earnings preview** below them (§22),
  - **Recent News**. Links are only clickable if they're normal web links, for safety.
  - **Press releases and AI labels** (News tab): PR Newswire press releases saved for this stock (with a
    "PR Newswire" badge), a **Collect press releases** button, and a **Label now** button (needs News cards
    switched on in Settings and an AI provider). A headline that has an AI label also shows chips for its
    event type, sentiment and materiality, marked "AI-labelled" (§11, "News cards").
  - **Saved history** (News tab): how many news items and fundamentals snapshots the app has saved for
    this stock, and since when, with a note that a backtest can only use news from the day the archive
    began (§11).
- **Generate Trade Plan →** button.

### Trade Plans
- **Generate Trade Plan** for any symbol.
- **Each plan card shows:**
  - direction, entry, stop, TP1, TP2, **R:R ratio**, share count, money at risk and potential gain,
  - confidence as a % *and* in points (for example "7/16 pts"), with a chip per scoring part (§5),
  - the list of reasons behind the score,
  - a muted **Silent signals (not scored)** line: what each new, not-yet-scored signal read and what it would have added (§5),
  - the ATR and how many ATRs away the stop is (amber if it's under 1×, which means it's inside normal
    daily noise),
  - the options-implied move next to the stock's typical move after past earnings,
  - the AI take (with the provider named, or marked as rule-based),
  - the AI overlay's opinion, if that's switched on, with the model that gave it ("AI overlay · claude-opus-4-1-20250805"),
    a note when its reply was recovered from a damaged answer or couldn't be read at all, and a
    "Check these figures" line when it quoted numbers that weren't in its data (§9),
  - the "why it didn't auto-execute" note,
  - a **v3**-style chip: the strategy version the plan was made under (§4),
  - a chart with the entry, stop and target lines.
- **Pending plans** can be executed by hand, but only while the US market is open (crypto: any
  time). While it's closed, the **Execute** button is greyed out and says why.
- **A plan made while the market was closed** reads **"Waiting for the next open"**. It can't be
  executed, even in the first minutes after the bell: it is redone from fresh data at 09:45 New
  York time (the card also shows that time on your own clock), and only the new plan can be
  executed.
- **History:** every plan, including **"no trade"** decisions with their reason, and the strategy
  version each was made under. Plans waiting for their redo are marked "redo at the open".
- **Strategy history:** a card under the history table listing each strategy version, what changed
  from the one before and how many plans and closed trades it produced (§4).
- **Missed trades:** a card above Strategy history showing what the plans the app did **not** take
  would have earned (§5, "Missed trades"): two headline questions, a summary per kind of missed trade
  with sample sizes and 95% intervals, the trades taken for comparison, and the list of every declined
  plan. A **Refresh** button computes the results; opening the page computes nothing.

### Portfolio
- **Thesis panel (open positions):** every open position gets a collapsible **Thesis** panel (see "Thesis tracker" below). A red **thesis broken** badge shows on the card header when the main pillar has failed.
- **Stat cards**, **Active Positions** (entry, planned entry, stop, TP1, TP2, shares, current P&L) with
  a **close** button that closes at the market.
- **Closed Positions:** exit price, reason (Stop, Target (TP1), Time limit or Manual), P&L and result
  in R. A line above the table counts how the closed trades ended, with the share of each, straight
  from the closed rows. A high share of time-limit exits means setups mostly stall instead of resolving.
  Two compact columns, **MFE** and **MAE**, show the best and worst price each trade reached, in R (§7).
  Hovering a trade's reason shows how the exit was placed in time (on a daily or an hourly bar, or by the
  cautious stop-first rule), and notes when the rest of its entry day could not be checked (§7).
  A last column, **Lesson**, opens a short AI-written note on the trade (§9) with who wrote it (provider
  and model) and when, labelled as a note and not a fact. A trade with no lesson shows **Write lesson**
  when an AI provider is set up (a dash when none is); one that has a lesson offers **Rewrite lesson**.
  If the last attempt failed, the reason is shown and nothing is written in its place.
- **Trade excursions:** a small block above the closed table with the average best and worst price of
  winners and of losers, the winners' exit efficiency, and how many losers were at least +1R ahead
  first, with a plain-English reading and the sample size. Trades closed before this was tracked show
  a dash and are left out of the averages (§7).
- Each open position shows **"day 7 of 20"** under its opened date: the number of trading days since
  entry, counted from the real bars on its chart (shown only when the entry day is in that chart's
  history; otherwise it says "closes after 20 trading days").
- **Equity Curve.**
- **Does confidence predict results?** A report card on the closed trades: a table by confidence band
  (trades, win rate and average R with their 95% intervals, total P&L), the information coefficient,
  a per-part table, how many closed trades were left out, and a short "how to read this". With under
  20 trades it says loudly that the numbers are noise (§5).
- **Reset** button: wipes positions, equity history and cash, and restarts at the Starting Cash
  setting. Plan history is kept.

### Settings
Grouped into cards. Every setting is listed in §12:
- **AI Narrative Provider**,
- **AI Trading Overlay**,
- **Finnhub (optional, free tier)**,
- **Paper Account**,
- **Telegram Notifications**,
- **Unattended Auto-Scan**,
- **Notifications** (morning note and its time, weekly digest, alerts on open positions and their distances, with preview and send-now buttons; §23),
- **Watchers** (the master switch, what a found event does, how often to check, the installed watchers and the latest events; §19),
- **Watchlist** (the symbols every scan and screen use: add, remove and reorder them, saved on the server; §11),
- **Data cache** (read-only numbers about the cache and the price history store, plus a Clear cache
  button; §11).

Keys are shown masked (the last 4 characters only), and there's a **Test Connection** button for each
provider. A test button tests **what is in the form right now**, saved or not: type a new key or model
and press Test before you Save. Nothing is stored by a test, a box you have not touched uses the saved
value, and a key never appears in the result (even if the provider echoes it in an error). A test is
still limited to one per 10 seconds per target. The AI card also has the **Research mode** choice and a line saying whether the chosen provider supports web search. When **Claude Code CLI** is the chosen provider, the AI card also shows a **Model** box
(example aliases `sonnet`, `opus`, `haiku`, or a full id). Blank means "whatever the CLI is set to".
Under each provider's model there is also a **Decision model (AI overlay)** box (blank = use the same
model) and a **Test decision model** button (§9).

### Login
Username and password. Three wrong tries lock that device out for 15 minutes, and a login lasts 7
days. (In the current `docker-compose.yml` the login is switched off; see §13.)

### Thesis tracker (Portfolio)

A **thesis** is the written reason you hold a position, split so it can be checked. Every open position gets one, made automatically from what the app already knew when it opened the trade.

| Part | What it holds |
|---|---|
| **Pillars** | One per scored reason on the plan (for example "weekly timeframe also bullish"), plus "trend still agrees with the direction" and "price holds above the stop" (below it for a short), plus the AI second opinion's verdict if there was one. Each is **intact**, **at risk** or **broken** |
| **Risks** | Reasons on the plan that argued against the trade, and anything you add |
| **Catalysts** | Earnings dates and macro events inside the holding window, with a countdown |
| **Log** | Dated lines: your notes, and the changes the app made on its own |

**Re-check.** The app re-reads fresh daily and weekly bars and updates the mechanical pillars: the daily trend, the weekly trend, the broad market, and the stop. The stop pillar turns **at risk** when price is within one ATR of the stop, and **broken** at the stop. The trend pillar is the core pillar: when it breaks, the thesis is flagged **broken**. Each change is written to the log with its date. An earnings report within 3 days is logged once as a catalyst alert. The re-check runs in the scheduled sweep (checked at most once an hour per position) and when you press **Re-check**. Opening the page never changes anything. Pillars you add yourself are only changed by you.

**Review.** The **Review** button asks the AI for one short paragraph that restates the stored statuses and dates. It is only sent facts the app holds, in a block marked as data, and it is told not to add facts or advise. With no AI set up, the button says so and nothing is stored. Calls are limited to one every 20 seconds.

**Telegram.** The first time a thesis becomes broken, one message is sent (setting **Thesis alerts**, on by default). It is not repeated.

### Screen presets (Market Scan)

Presets are named rule filters over the data held for each symbol. They are screens to find names worth a look, not signals: they do not change any score or plan.

| Preset | Must pass | Also counted (at least this many in total) |
|---|---|---|
| **Value** | P/E at or below 15 | positive earnings, lower 35% of the 52-week range, net insider buying (2) |
| **Growth** | revenue growth at least 15% | net income growth at least 20%, growth accelerating, bullish trend with strong momentum (2) |
| **Quality** | profitable in every reported year (at least 3) | revenue up every year, net margin holding, not in a downtrend (3) |
| **Short ideas** | bearish daily trend | revenue falling, growth slowing, margin shrinking, insider sales exceeding purchases (2) |
| **Special situations** | none | earnings within 14 days, insider buying, a 5% move today, volume at least 2x average (2) |

When a symbol lacks the data a required rule needs, it is shown as **not judged**, never as a match or a miss. Each preset also lists the rules from the idea sources that we cannot answer (for example free cash flow yield, return on equity, short interest) and why. A run checks the first N symbols of your list only (default 25, at most 100), using cached provider data, and says how many it covered.

### Options tab (Analysis)
- **What it shows:** the options chain for one expiration: an **expiration** picker, summary cards, and a
  table of calls on the left and puts on the right, around the current price. Shaded rows are in the
  money; the outlined row is the strike nearest the price. The bars behind **volume** and **open interest**
  compare cells inside the table. Prices are delayed and can be blank outside market hours.
- **Summary cards** (all worked out from the chain's real fields, using every strike): **put/call volume**
  and **put/call open interest** ratios, the **at-the-money implied volatility** (mean of the call and put
  at the strike nearest the price), the **expected move** to that expiration (price x IV x square root of
  days / 365; blank when the expiration is today or IV is missing), the **max pain** strike (the strike where
  all open contracts together would pay out the least) and an **IV skew** (puts 3-10% below the price against
  calls 3-10% above, in volatility points).
- **No Greeks:** the free data source does not supply them, so none are shown or estimated.
- **No options:** crypto and symbols with no listed options show a plain "no options" message.
- Read-only: opening the tab never writes anything. The data comes from the usual provider chain (Yahoo
  first), is cached for 30 minutes, and shows the usual "as of" label.

### Valuation tab (Analysis)
- **What it shows:** a rough **discounted earnings estimate** and a **peer multiples** table. It is
  informational only: it never changes a trade plan's score, direction or size.
- **The estimate:** it takes revenue (last twelve months, else the latest reported year), grows it by a
  yearly rate, multiplies by a net margin, discounts each year at your discount rate and adds a **terminal
  value** (the years after the last one, from the terminal growth rate). Defaults: growth is the compound
  revenue growth over the reported years (limited to -10% to +25%), margin is the latest reported net
  income over revenue, discount 10%, terminal growth 2.5%, 5 years. You can edit all of them and press
  **Recalculate**; blank growth or margin means "use the reported figure". A small table shows the value
  per share at discount rates and terminal growth rates one step either side.
- **Why net income:** the free sources give no cash-flow statement, so earnings stand in for cash flow.
  Treat the result as rough. A missing input is shown as not available, never guessed; if growth or margin
  cannot be taken from reported figures you must type them.
- **Peer multiples:** up to 8 other companies from the same sector of the bundled list (in symbol order).
  P/E and price/sales are shown per peer with the **median**, range and number of peers used; peers with a
  loss or no figure are left out. The **implied price** is the median applied to the company's own earnings
  (or sales) per share. EV/EBITDA is not shown because the free sources do not supply debt or EBITDA.
- **Explain in words:** an optional short paragraph from the routine AI model, told to use only the numbers
  shown. With no AI configured it is plain rule-based text; the label says which.
- Read-only: nothing is stored. Provider data is cached as usual.

### Library tab (Analysis)
- **What it shows:** everything the app has recorded about one ticker, newest first: archived **news**,
  **fundamentals** snapshots, **watcher events**, **8-K** filings, **insider trades**, **5% owner filings**,
  **Congress trades**, **earnings reports**, **price alerts** and the **AI lessons** written about closed paper
  trades. Each row shows when it became public, its kind, a one-line title, a short summary, its source and a link.
- **Search and filter:** type text to search titles and summaries, or pick one kind. The kind menu shows how many
  entries each kind has.
- **Only what was recorded:** the archive starts the day it was switched on, so an empty list means "not
  recorded", never "nothing happened". Each read looks at the newest 600 rows per kind.
- **For research prompts:** the code can also build a short block of the newest matching entries for a research
  or committee prompt. The block is fenced and labelled as quoted data, never instructions, with fence markers
  removed from the text, each line cut to 300 characters, and at most 25 items. If nothing matches or the read
  fails the block is empty.
- Read-only: it never fetches data and never writes. It follows the same "known at" rule as everything else,
  so a replay of the past cannot see later records.

### Screener (its own page)
- **What it is:** your own rules over this app's stock list. It is separate from the Market Scan presets:
  there you pick a ready-made idea; here you build the rules.
- **Rules:** pick a field, an operator (**>, >=, <, <=, between, equals, not equal**; text fields use
  equals, not equal and contains) and a value. At most 12 rules; a stock must pass all of them.
- **Fields:** price, change %, volume ratio, trend, momentum, RSI, scanner score, signal, % from EMA20,
  52-week position, P/E, market cap, revenue growth and sector. Fields that need an extra fetch per stock
  (P/E and market cap, revenue growth) are marked "slower" and only loaded when a rule, the sort or a
  column uses them.
- **Missing data:** a stock with no value for a field a rule uses never matches that rule. The result
  says how many were left out for that reason, and which had no price data at all.
- **Size:** a run reads only the first N symbols of the list (default 60, at most 200) so it stays quick and
  stays inside the cache; the result says "N of M symbols scanned".
- **Sort, row limit, CSV:** sort by any field, choose how many rows to show, and **Export CSV** downloads
  the shown rows (text that looks like a spreadsheet formula is neutralised).
- **Saved screens:** a name plus the rules, sort and limit, stored on the server in
  `runtime/screener_saved.json` (at most 50, names unique). Click one to load it, "x" to delete it.
- Read-only: running a screen never writes anything and never opens a trade.

### TradingView widgets (optional, load on click)
- **Where:** a **TradingView** tab on Analysis (a chart and a technical summary) and a ticker tape on the
  Market Terminal.
- **Privacy:** nothing from TradingView is requested until you click the load button. Clicking loads a frame
  from TradingView's servers, so TradingView sees your browser's request (your IP address and the symbol).
  The choice is never remembered as "load automatically": every visit starts unloaded. Only the fact that
  you have read the notice is saved in your browser.
- **Safety:** the widget is TradingView's own embed frame, sandboxed, on TradingView's origin. Its script
  never runs inside this app's page and it cannot read this app's cookies or data.
- **Data:** it is TradingView's data, not this app's, and can differ from the numbers elsewhere. Symbols
  with no TradingView equivalent show no widget. Attribution is shown under every widget.

---

## 4. How one stock is evaluated

`generate_trade_plan` in `backend/app/services/trade_plan_service.py` runs these steps in order:

1. **Prices:** 1 year of daily bars, plus the current quote.
2. **Chart analysis:** EMA20, EMA50, RSI14, trend, momentum, and support/resistance levels (§5).
3. **Direction:** Bullish trend means **long**, Bearish means **short**, and Neutral means no
   direction.
4. **Every other check,** scored *for the trade's direction*, so evidence that supports a short counts
   *for* a short:
   - fundamentals and news (the app also saves what it fetched here into the dated archive, §11; this
     never changes the score),
   - the weekly chart and SPY,
   - VIX,
   - options positioning,
   - insider buying,
   - the options-implied move,
   - the earnings surprise record,
   - macro events.
5. **The AI overlay** (only if it's switched on): its own read, and a possible penalty or veto (§9).
6. **The decision.** It's a **"no trade"** if:
   - the trend is Neutral, *or*
   - the AI overlay vetoes it (with the action set to "cancel"), *or*
   - confidence is under the bar (30% by default).

   A "no trade" is saved with its reason, and the AI narrative is skipped for it, which saves tokens.
7. **Otherwise it becomes a plan:** entry, stop, targets and size (§6), an AI narrative (or rule-based
   text), saved as **pending**. An older pending plan for the same stock becomes **discarded**.
8. **Market check, then auto-execute.** If the stock's market is closed (a night, a weekend or a US
   holiday; crypto never closes), the plan is **not executed**, whatever the auto-execute setting. It
   stays pending with a note ("Market closed (weekend), so this plan was not executed…"), and a
   **redo** is queued: at 09:45 New York time on the next trading day the stock is evaluated again
   from fresh data, and only that new plan can execute (§8). Otherwise, if auto-execute is on and
   allowed, it tries to open the paper position (§7), and records why if it can't.
9. **Telegram:** the new plan is sent to you, if Telegram is set up, with that note.

A plan's **status** is one of: `pending` (waiting for you, or for its redo at the next open),
`executed` (a position was opened), `no_trade` (it was rejected, with a reason) or `discarded`
(replaced by a newer plan, such as the fresh one a redo makes).

### Strategy versions: which rules made a plan

Every plan, including every "no trade" decision, is stamped with a **strategy version** such as
**v3**. A version is one exact combination of the settings and rules that decide what comes out of a
stock's data. If you change the confidence bar, the same stock can become a plan instead of a "no
trade", so results can only be compared fairly if each plan says which rules it was made under.

**What counts as part of the strategy** (changing any of these makes a new version):

| Group | What |
|---|---|
| Settings | Minimum confidence, default risk per trade, slippage, commission, max open positions, max positions per sector, max position as a % of daily volume, maximum holding days, auto-execute, and whether the AI overlay is on |
| Only while the AI overlay is on | Count disagreement in the confidence score, the objection action, the AI provider and the model that answers the overlay (its decision model, or the normal model when no decision model is set), and the overlay's own scoring numbers. Switch the overlay off and changing these does nothing, so they don't matter then |
| Rules in the code | The named numbers behind the scoring and sizing: score caps, trend and RSI thresholds, the stop rules (ATR multiple, stop buffer, fallback stop), the target rules, the VIX and put/call thresholds, the insider-buying minimum, the confidence scale, the exit-check window and the entry price-drift limit. If a developer changes one, the next plan gets a new version without anyone remembering to record it |

**What does not count** (changing it never makes a version): API keys and the Telegram settings; which
AI writes the plan's text, meaning the normal model (it changes wording, not the decision; once a decision model is set it also stops mattering to the overlay); the scan universe size, auto-scan
on/off and Finnhub on/off (they change *which* stocks are looked at or where data comes from, not
what the rules decide for a given stock); starting cash and the exit-check interval; and the
hand-typed macro calendar dates and market-hours tables.

**How versions are created.** A version is made **when a plan is generated**, never when you edit a
setting and never when you open a page. Edit settings and look at nothing: no version exists yet.
Generate the next plan and the app finds (or creates) the version for the settings and rules in force
at that moment. Going back to settings you used before reuses their old version number instead of
making a new one. Versions are numbered 1, 2, 3 in the order they were first used.

**Old plans** from before this existed have no version and show a dash. Nothing is back-filled,
because what the rules were then can't be known.

**Where you see it.** A small **v3** chip on the plan card, a **Strategy** column in the Trade Plans
history, and a **Strategy history** card at the bottom of Trade Plans. It lists each version with its
date, what changed from the one before it in plain words ("min confidence 30 -> 38"), and how many
plans and closed trades were made under it. A position's version is its plan's version.

---

## 5. The confidence score, part by part

The **confidence** is the points a setup earned out of the **16 achievable points**, as a %.
Examples: 5/16 = 31%, 9/16 = 56%. Only 17 values are possible (0, 6, 12, 19, 25, 31, …, 100), which is
why the card shows the points too. It measures *how much evidence lined up*, **not** the chance of
winning.

### Parts that can add or subtract (they make up the 16 points)

| Part | Points | How it's scored |
|---|---|---|
| **Chart (technical)** | 0 to 6 | Only when there's a trend. **Trend** = EMA20 above EMA50, price above EMA20, and EMA20 rising over 5 days (mirrored for a downtrend): **+2**. **Strong momentum** = over 5% move in 10 days *and* RSI above 55 (below 45 for shorts): **+2**. **Volume** over 1.5× its 20-day average: **+1**. **Near a key level** (within 2% of resistance for a long, support for a short): **+1**. **Overextended** (RSI above 75 for a long, below 25 for a short): **−1**. |
| **Fundamentals** | −3 to +3 | Revenue up vs the previous year **±1**. Within 5% of the 52-week high **±1** (or the low, the other way). **Earnings within 3 days: −1** for both directions, because it's event risk. |
| **News** | −2 to +2 | The latest 5 headlines, matched against keyword lists (for example "beats", "upgrade" or "record" vs "miss", "downgrade" or "lawsuit"). Each positive headline counts for a long and against a short. |
| **Weekly chart and SPY** | −2 to +2 | The weekly trend agrees **+1** or disagrees **−1**. The broad market (SPY) agrees **+1** or disagrees **−1**. A Neutral chart counts 0. |
| **Options** | −1 to +1 | The put/call volume ratio on the nearest expiry at least 7 days out: under 0.7 is bullish, over 1.0 is bearish. It agrees with the trade **+1**, or goes against it **−1**. |
| **Insider buying** | −1 to +1 | Open-market insider buying beating selling by at least **$100,000** over **90 days**: **+1** for a long, **−1** for a short. **Selling never counts.** |
| **Earnings surprise record** | −1 to +1 | Needs at least 4 past quarters. If 75% or more beat estimates by at least 1%, that supports a long; if 75% or more missed, that supports a short. |

### Penalty-only parts (never a bonus, so they aren't counted in the 16)

| Part | Points | When |
|---|---|---|
| **VIX** | 0 or −1 | VIX at **25 or higher**, a fearful market |
| **Options-implied move** | 0 or −1 | The options market prices a move of at least **1.5×** the stock's normal (ATR-based) move over the same period |
| **Macro events** | 0 or −1 | A **Fed decision, CPI or jobs report today or tomorrow**. The dates are hand-kept: Fed decisions for **2026 and 2027**, CPI and jobs reports for **2026 only so far** (§14). |
| **AI overlay** | 0 to −3 | Only if the overlay is on and would not take the trade: **−1** under 45% conviction, **−2** from 45–64%, **−3** at 65% or more |

**The bar:** a plan needs a trend **and** at least **30%** confidence (the `min_confidence_for_trade`
setting), which is 5 of 16 points.

### Does the confidence score predict results? (the calibration report)

A score that sounds precise is only useful if higher scores really do come with better trades. The
**Portfolio** page ends with a card that checks this, using **only your closed paper trades** and the
plan each one came from. Nothing in it is typed in or smoothed, and opening the page changes nothing.

- **By confidence band.** Closed trades are grouped by evidence points (not by percentage, since the
  score only has 17 possible values): 6 or fewer, 7-8, 9-10 and 11 or more points out of 16. For each
  group the card shows the number of trades, the **win rate**, the **average R**, and the total
  profit or loss. The win rate comes with a **95% interval** (the Wilson interval, which stays honest
  at small sizes) and the average R with a 95% interval from resampling the trades. The win-rate bar
  has the interval drawn on it, so a small group visibly covers most of the bar. A group with fewer
  than **20 trades** is flagged "few" and drawn in amber. A win means a profit after fees, the same
  rule as the Win Rate card.
- **Information coefficient (IC).** One number from -1 to +1: do plans with more points tend to end
  with a better R? It is the **Spearman rank correlation** between the plan's points and the trade's
  R. 0 means no link; for trading signals, even 0.05 to 0.15 is interesting. It is shown with its
  number of trades, a 95% interval and a **p-value** (from shuffling the results at random 5,000
  times: how often would luck alone look this strong?). The same is shown for long trades only and
  short trades only.
- **Which parts of the score matter.** The same IC for each stored part of the score (chart,
  fundamentals, news, weekly chart and SPY, options, insider buying, earnings surprise, and the AI
  overlay, VIX, expected-move and macro penalties), each with its own trade count. A part that was not
  recorded on older plans is counted only on the plans that have it. A part that never changes
  (for example a penalty that is always 0) says "No variation". Because several parts are tested
  together, their p-values are adjusted upwards (Bonferroni): test enough things and one looks good by luck.
- **A reading, never a finding too early.** Below **20 closed trades** every row says "Not enough data"
  and a warning at the top tells you to treat the numbers as noise. At 20 or more, a part is reported as
  "rises with results" or "falls with results" only if its adjusted p-value is below 0.05; otherwise it
  says "No clear link".
- **Nothing is dropped silently.** The card says how many closed trades were left out and why: no
  linked plan (for example a manually opened position), no recorded R, or a score outside every band.
- The random steps use fixed seeds, so the same trades always give the same report.

It reads the table of stored trades; it does not call the market data providers and uses no AI. It
describes what happened, it does not predict. The code is `backend/app/portfolio/calibration.py` (the
report) and `signal_stats.py` (the statistics).

### Missed trades (what the declined plans would have earned)

The calibration report only sees trades that were taken. The app also **declines** plenty (under the
confidence bar, no trend, the AI overlay objecting) and leaves some written plans unfilled, and without
looking at those nobody can tell whether the confidence bar, or the AI veto, helps or hurts. The
**Missed trades** card on the **Trade Plans** page builds that comparison.

**What counts as a missed trade.** Every plan that was not executed, sorted into one kind:

| Kind | What it is | Simulated? |
|---|---|---|
| **AI veto** | A "no trade" because the AI overlay objected (Settings: "cancel") | Yes |
| **Under the confidence bar** | A "no trade" because confidence was below the bar | Yes |
| **Held by the AI** | A plan was written but left pending because the overlay objected (Settings: "hold") | Yes, with its own entry, stop and target |
| **Written, never filled** | A pending plan the engine refused (not enough cash, position cap, sector cap, price moved too far) or that nobody has executed yet (auto-execute off) | Yes, with its own levels |
| **No trend** | A "no trade" with no clear trend, so there is no direction to simulate | Counted only |
| **Other** | A plan whose text matches none of the above | Counted only |

Two kinds of pending plan are counted but **not** simulated, so one idea is never counted twice: a plan
for a stock that is already held (a duplicate), and a plan made while the market was closed, which is
waiting for its redo (the fresh plan from the redo is the decision that counts). Plans that were
executed, or discarded because a newer plan replaced them, are not missed trades.

**How a hypothetical trade is built and walked.**
1. A "no trade" record has no entry, stop or target, so they are rebuilt with the **same functions the
   live plan uses** (the chart analysis, the stop rule with its ATR floor, the target rules), on the
   daily bars that were final when the plan was made and **nothing later**. The direction is the one the
   decision's own text names ("against a rule-based long", "despite a bullish trend"); failing that, the
   chart's trend. A plan that was written keeps its own stored entry, stop and target.
2. The **entry** is the close of the last daily bar that was final at that moment (the quote the plan used
   is not stored). A plan made during a session therefore enters at the previous close, and the day it
   was made on is skipped, as the paper engine skips its own entry bar.
3. The trade is walked bar by bar with the **paper engine's own exit scan** (§7): the first stop or TP1
   touch, the stop first when one bar holds both, gaps filled at the open, and the holding limit. Slippage
   and commission come from Settings, and R is computed the way a real close does.
4. The result is **resolved** (stop, target or time limit reached: final, never recomputed), **open**
   (marked at the latest close and recomputed on later refreshes, never counted in the statistics) or
   **no data** (the price history could not be loaded: retried, never guessed).

**What the card shows.** Two headline questions: *"Did the AI veto save or cost money?"* and *"Are the
plans under the confidence bar worse than the ones taken?"* Each says **too early to tell** until at
least **20** resolved trades (and, for the second, 20 closed trades to compare with); after that it
answers only if the 95% range of the average R sits clear of zero (first question) or clear of the
range of the taken trades (second), and otherwise says there is no clear answer. Under them, one box per
kind with the number of plans, resolved trades, **win rate with its 95% (Wilson) interval**, **average R
with its 95% range**, the total R, how many are still open, waiting or not simulated, and a "too few to
read much into" note below 20. A box shows the closed paper trades for comparison, and a list shows
every declined plan with a link to its chart, its kind, its outcome and its hypothetical R.

**When it is computed.** Never by opening the page. The **Refresh** button (30-second cooldown) and a
daily job at 16:45 New York time on weekdays compute the results that are new or still open, at most 200
per run (the rest on the next run), one price-history load per stock. Prices come from the local price
history store (§11), and may be a little stale: this is analysis, not a trading decision.

**What it cannot tell you (the card says so too).**
- Results are **hypothetical**: filled at the last known daily close with the paper account's slippage,
  ignoring cash, the position cap and the sector cap, so trades can overlap in ways the account could not.
- They are walked on **daily bars only**. The paper engine also reads hourly bars to order a stop and a
  target inside one day; here such a day counts as the stop. This can understate a hypothetical result,
  never flatter it. The taken trades it is compared with do use hourly bars, so the comparison is not
  strictly like for like.
- Levels are rebuilt with **today's rules** from the bars known then, not the exact numbers the decision
  saw. Prices are adjusted for splits and dividends as of when they were downloaded.
- Hindsight and survivorship: the prices come from stocks that still trade, and several declined plans
  on one stock are one idea counted more than once.

### Silent signals (recorded, not scored)

A new signal does not get to move confidence just because it sounds sensible. It starts **silent**: on every
plan, traded or declined, the app records what the signal read and the points it **would** have added, but
those points are never added to the confidence score, never change the direction, the entry, stop or targets,
and never change the position size. Once enough plans have piled up, a backtest can show whether plans where
the signal agreed did better than luck. Only then is it promoted: its points move into real scoring under a
cap and the strategy version changes (the list of promoted signals, `LIVE_SIGNALS`, is part of the rules in
the strategy fingerprint, §4). Today that list is empty.

The Trade Plans page shows a muted **Silent signals (not scored)** line on each plan card, with the reading
and "would have added +1" (or "-1", or "no data yet"). A signal with no stored data, or whose newest data is
more than 7 days old, says "no data yet": it never guesses. A signal that fails to read is marked the same
way and never breaks the plan.

**FINRA short volume** is the first one. It compares the share of volume sold short over the last 5 trading
days (volume-weighted) with the stock's own median share over up to 60 earlier days (needs at least 20). A
share at least **5 percentage points above its own baseline** reads as mildly bearish context: it would
subtract 1 from a long and add 1 to a short. Anything else adds 0.

**Negative 8-K items** is the second one (§11, "Company announcements"). It looks for an 8-K filed in the
**last 3 days** that lists a bad-news item: bankruptcy (1.03), a material impairment (2.06), a delisting
notice (3.01), a statement that earlier financials can no longer be relied on (4.02) or a director or officer
change (5.02, the weakest: that item also covers appointments and pay changes, and the filing's text is not
read). It would subtract 1 from a long and add 1 to a short. A company with no stored 8-Ks reads "no data
yet"; one with stored 8-Ks but nothing in the window reads 0.

**Fed event window** (`fed_event_window`) is a market-wide **risk flag**, like the macro-calendar penalty but read
from the Fed's own published items (§11, "The Fed"). It reads **1** and would subtract **1** when a Fed **policy
statement** or a speech or testimony by a **Chair** was published within **one day** of the plan's moment, and
reads 0 otherwise. It ignores the trade's direction (a Fed day says the chart is less reliable, not which way to
trade) and can never add. It reads nothing of what was said. Because the app only sees what was already
published, a statement due tomorrow cannot be seen here; the macro calendar covers the dates ahead. Reads "no
data yet" until some Fed item is stored.

**News cards** (`news_cards`) reads the AI labels on this stock's saved headlines (§11, "News cards"). Only
headlines published in the **last 3 days** count, and only labels that say the headline is mainly about this
company and rate it **high** or **medium** materiality. Each counts with a weight (high 2, medium 1) and a
sign (positive +1, negative -1, neutral or mixed 0), and the weights are added up. A total of at least +2 or
at most -2 is evidence (one high-materiality story, or two medium ones); anything between reads 0. Positive
news would add 1 to a long and subtract 1 from a short, negative news the reverse, and with no clear direction
it reads 0. The AI only fills in the labels: these rules decide what they mean, and the existing keyword news
check still decides the real score. It reads "no data yet" when there is no label for the last 3 days. A label
is saved when it is made, so a backtest of an earlier moment sees none.

**Fund accumulation** (`fund_accumulation`) and **5% owner filing** (`ownership_5pct_filing`) read the 13F holdings of followed funds and new Schedule 13D filings (§20). Net fund buying or a new 13D would add 1 to a long and subtract 1 from a short; each is capped at 1 and both read "no data yet" until filings are stored.

**Post mentions** (`post_mentions`) counts the posts in the **last 24 hours** that name this company (§11, "Posts").
It reads the count and would subtract **1** when the count is above 0, whatever the trade's direction, because a
post can be good or bad for a company and nothing here reads which. A repost or a post with no words is not
counted. It reads 0 when none named the company and "no data yet" until some post is stored. The source is an
unofficial archive.

The code is `backend/app/portfolio/missed_trades.py` (sorting, rebuilding, walking, refreshing),
`missed_trade_report.py` (the report) and `missed_trade_models.py` (the stored results, table
`missedtradeoutcome`).

---

## 6. Entry, stop, targets and position size

`backend/app/risk/position_sizing.py` and `_derive_entry_and_stop`:

- **Entry:** the latest price when the plan is made. When it executes, the price is fetched again
  (§7).
- **Stop, for a long** (a short is mirrored):
  - **1% below the nearest support**, or **3% below entry** if no support level exists,
  - then pushed further if needed, so it's **at least 1.5× ATR14** below entry. The volatility floor
    stops the stop sitting inside the stock's normal daily noise.
  - The stop is never pulled closer than the chart level.
- **Targets:** **TP1** and **TP2** are the next resistance levels (support for a short), if each gives
  at least 1:1 reward to risk. Otherwise they're set at **1.5R** and **3R**.
- **Share count:** (account × risk %) ÷ (entry − stop). With 1% risk on $100,000, a trade risks
  $1,000. Then it's capped by the cash the account can really use, and at the open by 1% of the
  stock's average daily volume.
- **Time horizon** is labelled "1–4 weeks". It is also enforced: a position still open after the
  **maximum holding time** (default **20 trading days**, the top of that range; §7) is closed at that
  day's close.

---

## 7. The paper-trading engine

`backend/app/portfolio/engine.py`, which is what makes every statistic real.

### Opening a position: the checks
- **Refused** if:
  - **the stock's market is closed.** US stocks only fill during the session (09:30–16:00 New York
    time on trading days, 13:00 on early-close days; §8). Off-hours the only price is the last
    close, which nobody could actually trade at. Crypto never closes. This check sits inside the
    engine, so every way a position opens obeys it: auto-execute, the Execute button, auto-scan,
    and later the watchers, and the backtest (§18). (The engine's clock can be swapped for a simulated
    one, which the backtest does.)
  - the stock is already held (no adding to a position),
  - the maximum number of open positions is reached (**5**),
  - the sector already has 2 open positions (using the `sector` column of the stock list),
  - there isn't enough cash (a short's sale money is held back as collateral).
- **Re-quoted:** it fills at the *current* price plus **slippage (5 bps)**. It's **refused** if the
  price has moved **more than 2%** from the plan ("stale plan": the stop, size and R:R would no longer
  match).
- **Trimmed** to at most **1% of average daily volume**.

### Closing: the exit check
- It runs every 15 minutes during US market hours, plus 30 minutes after the close (after the 1:00
  pm close on an early-close day; not at all on a holiday), and around the clock if a crypto
  position is open.
- It walks **every daily bar since entry**, not just today's, so a stop that was hit and then
  recovered isn't missed. **The entry day's daily bar is skipped** (the entry is priced at that day's
  close or current price, so the bar's earlier low must not stop out a trade that didn't exist yet).
  The rest of the entry day is checked **hour by hour** instead (see "The entry day, hour by hour" below).
- A position closes **fully** at whichever comes first, **stop** or **TP1**. TP2 is for information
  only.
- **Time limit (third way out).** If neither was touched, a position that has been open for the
  **maximum holding time** is closed at the **close** of that day. The days are **trading days**
  counted from the bars (the entry day is day 0 and is not counted), so weekends and holidays don't
  count; a crypto pair trades every day, so each calendar day counts. It is a market order, so it pays
  slippage like a stop. The time exit comes **last**: on that same day the stop is checked first, then
  TP1, and only if both are clear does the time limit close it. The close used is that day's final one:
  **a day still in progress is never used** (a US stock's day is final 30 minutes after the closing
  bell, 13:30 on an early-close day; a crypto day at midnight UTC), so during the session the
  position simply waits for the next check. Set the maximum to **0** to switch the limit off.
- **If one day touches both** the stop and TP1, a daily bar can't show which came first. The engine
  then asks for **that day's hourly bars** and takes whichever level was touched **first** (see "Which
  level came first" below). If the hourly bars aren't available, the answer is a **stop**, the cautious
  choice, and the trade is marked as decided that way.
- **Gaps:** a day (or, when hourly bars are used, an hour) that opens past the stop fills at the **open**
  (worse). One that opens past the target fills at the **open** (better).
- Stop exits pay slippage (a market order). Target exits don't (a resting limit order).
- A **manual close** from the Portfolio page closes at the current market price.
- **Why a time limit:** a stalled position used to sit open forever, holding one of the 5 slots and
  a sector slot while its original reason aged. Turning the limit on changed behaviour for stalled
  positions: ones already open longer than the limit close at the next check (at the close of the day their limit
  ran out), and win rate and Avg R
  now include those results. The limit needs the entry day to be in the 3 months of history the exit
  check reads, so the maximum is capped at 60 trading days.

### Which level came first, and the entry day, hour by hour
Daily bars have two blind spots. Hourly bars (Yahoo keeps about two years of them) fill both. They
are fetched **only when needed**: for a day that touched both levels, and for the entry day of a
position whose entry day hasn't been checked yet, one request per stock per check (the data is also
cached for 15 minutes). A check with nothing ambiguous makes no hourly request.

**Which level came first.** For a day whose range holds both the stop and TP1:
- The engine walks that day's hourly bars in order and closes at the **first** one that touches a
  level. The same fill rules apply to the hour: a stop is a market order (an hour that opens past it
  fills at that hour's open, and pays slippage), a target is a resting order (an hour that opens past
  it fills at the open, which is better).
- **Inside one hour the stop still comes before the target.** If both levels sit inside the same
  hourly bar, the order can't be known, so the stop is taken (the cautious choice, one level down).
- A day that **opens past the stop** needs no hourly look: the open itself triggered the stop.
- The hourly bars must themselves reach both levels, like the daily bar did. If an hour seems to be
  missing, they aren't trusted and the stop is taken.
- Hourly bars that can't be fetched, or that turn out not to be hourly, change nothing: the stop is
  taken, as before.

**The entry day, hour by hour.** The position's **opening hour** and every later hour of the entry
day are checked, so a stop or target touched later that day is no longer missed (a missed stop only
ever deletes losses):
- The hour the position was **opened in** is partly before the entry, and an hourly bar can't say
  which part its extremes came from. In that hour only the **stop** counts (the cautious reading: at
  worst it closes a trade that was never exposed); a target there is ignored (it may have been reached
  before the entry, and counting it would flatter the trade). A stop in that hour fills **at the stop
  price**, not at the hour's open, which was before the position existed.
- A position opened exactly at the start of an hourly bar has that whole bar checked.
- Every later hour is checked in full. If a stop or target was touched, the position closes there.
- A US stock's day is New York time; a crypto pair's day is a UTC day.
- When the day has been fully checked, the position's **entry-day check** is saved as "hourly". If the
  hourly bars never arrive (or stay incomplete for a day after the close), it is saved as
  "daily_only": the entry day then stays unchecked, as before, and the row says so.
- A position that was already open before this check existed gets its entry day looked at the first
  time the exit check runs.

**How each exit was decided** is saved on the closed position as its **exit resolution**:

| Value | Meaning |
|---|---|
| `daily` | One level on the daily bar (or the open already settled it). |
| `hourly` | Found on an hourly bar. |
| `daily_ambiguous_stop_first` | The day held both levels and no hourly answer was available: the stop. |
| `hourly_ambiguous_stop_first` | Both levels sat inside one hourly bar: the stop. |

A time-limit exit is `daily`; a manual close has none. The Portfolio page shows it as the hover text
of a closed trade's reason. Comparing the "ambiguous" rows with the rest later shows how much the
cautious rule costs.

**Limits.** Hourly bars only go back about two years (the engine asks for at most one year). Hourly
is also a resolution, not a tick: two levels inside one hour still go to the stop. The backtester
turns hourly bars off and keeps the plain daily rules, because its data source has no hourly bars for
past dates.

### Best and worst price during a trade (MFE and MAE)
Win rate can't tell you whether stops are too tight or exits too slow. Two numbers per closed trade
can:
- **MFE** (maximum favourable excursion): the furthest the price moved **in the trade's favour**
  between entry and exit.
- **MAE** (maximum adverse excursion): the furthest it moved **against** the trade.

Both are stored as plain positive distances, for a long or a short, as a **percent of the entry price**
and in **R** (multiples of the initial risk, entry to stop). A trade that exited at its stop has an MAE
of about 1R (more if it gapped through).

How they are worked out, with the same honesty rules as the exit check:
- Only the bars the position was **open for** count: the entry day's daily bar is skipped (its hours
  after the entry count only when an exit is found on the entry day), and nothing after the exit is
  looked at.
- A daily bar can't say whether its high or its low came first, so the **exit day** counts only what
  is certain: its open and the **exit price**. When the exit was found on an hourly bar, the hours
  before the exit hour count in full and the exit hour counts the same way (its open and the exit
  price), so a day settled as "target first" does not count the low that came after the target. A stop-hit day does not count its low (that may be far
  below the fill and came after the position was gone) or its high; a TP1-hit day does not count its
  high or low beyond the fill. Leaving something out only makes an excursion look smaller, never better.
  A time-limit exit happens at the close, so that whole day counts.
- They are saved when the position closes. A **manual close** fetches the bars at that moment; if the
  price history can't be fetched, the close still goes through and the figures stay empty.
- If the bars aren't available, or the entry day is outside the history window, the figures are
  **empty, never guessed**.
- An **open** position's figures are computed on the fly each time the positions list is read, and are
  never stored. Trades closed before this feature existed show a dash.
- A **backfill** function (`backfill_excursions` in `backend/app/portfolio/excursion_service.py`)
  can fill in older closed trades from the price history. It does not run by itself; it only touches
  rows that have no figures (so running it twice changes nothing), and it leaves a row empty when it
  can't place the exit day honestly.

The **Trade excursions** block on the Portfolio page averages them over closed trades that have the
figures: winners' and losers' average best and worst price in R, **exit efficiency** (realized R
divided by the best R reached, for winners), how many losers were at least **+1R ahead** first, and
how many winners came within 0.2R of the stop. Read it like this: losers that were far ahead first
suggest slow exits; winners that nearly hit the stop suggest the stop may be too tight. Because the
whole position closes at TP1, a winner leaves at its target, so exit efficiency stays near 100% by
design; the best price **after** an exit is unknowable and is never shown.

### Statistics
Portfolio value, total return (including open positions at current prices), win rate, total closed
trades, **Avg R** (the average result per closed trade, in R), active positions and cash, plus the
**exit mix**: how many closed trades ended at the stop, TP1, the time limit or by hand. The
**equity curve** is built from snapshots taken when a position opens, when one closes by hand, and on
each scheduled exit-check pass. Opening the Dashboard or Portfolio page runs the exit check too, but
never adds a point, so the curve follows time and trades, not page views. If a page load closes a
position, the curve shows it at the next snapshot.

---

## 8. Automation: what runs by itself

| What | When | Default |
|---|---|---|
| **Auto-execute** | Right after any tradeable plan is generated, whether by you, auto-scan or auto-trade, **if the stock's market is open**. Otherwise the plan waits for the market-open redo. | **On** |
| **Auto-scan** | Mon–Fri at **10:00, 13:00 and 16:15 New York time**, skipping US market holidays. It evaluates every stock in the list that isn't already held, and auto-executes while position slots remain. The 16:15 run comes after the close, so its plans can't execute: they're queued for the redo (on a 1:00 pm early-close day, so are the 13:00 run's). | **Off** |
| **Auto-trade button** (Market Scan) | When you click it. It runs the same loop as auto-scan, at most once a minute. At night or at the weekend, its plans are queued for the redo too. | — |
| **Watchers** | One check every **5 minutes** (a setting, 1 to 60) that runs whichever watchers are due; nothing happens while the master switch is off. See §19. | **Off** |
| **Price alert** | A message you ask for when a stock's price meets a condition you set (§23). It never trades. |
| **Market-open redo** | Checked every 15 minutes through the US session; acts from **09:45 New York time** on trading days | Always |
| **Exit check** | Every **15 minutes** (a setting), only during US hours plus 30 minutes, unless crypto is held | Always |
| **Missed-trades refresh** | Mon–Fri at **16:45 New York time**: computes what the declined plans would have earned, once the day's bars are final (§5, "Missed trades"). Analysis only; it places nothing | Always |
| **Trade lessons** | Every **5 minutes**: writes an AI lesson for up to 3 trades closed in the last 3 days that have none (§9). Does nothing without an AI provider | Always (needs an AI provider) |
| **Price alerts** | Every **5 minutes**. Checks your alerts and the open positions against fresh quotes; stocks only while the US market is open, crypto always (§23) | Always (alerts on open positions can be switched off) |
| **Morning note** | Looked at every 5 minutes; sent at the time you set (default **08:45 New York time**) on US trading days (§23) | **Off** |
| **Weekly digest** | Sent at **16:30 New York time** on the last trading day of the week (§23) | **Off** |
| **Health check** | Every 15 minutes, and once at startup. It checks a SPY quote, the AI setting and Finnhub, and messages you on Telegram only when one **changes** between up and down | Always |

### The market-open redo
A tradeable plan made while its market is closed was built on the last session's prices: a Sunday
plan is Friday's close, and by Monday the news and the opening price can make it stale. So it is
never filled. Instead:
1. The plan stays **pending**, and the stock goes into a **queue** (the `DeferredEvaluation` table),
   due at **09:45 New York time** on the next trading day: 15 minutes after the bell, once the
   opening auction is over. One entry per stock: generating it twice on a Sunday doesn't queue two.
2. From 09:45 the stock is **evaluated again from fresh data**, with the same account size and risk
   as the original request. The new plan goes through every normal check, including auto-execute,
   so the auto-execute setting *at that moment* decides whether it opens a position.
3. The old plan is **discarded** either way, so only the new plan can ever execute. If the new
   evaluation says "no trade", that is the answer.
4. **Skipped** if the stock is already held by then, or the old plan was already replaced by one
   made during the session.
5. If the data can't be fetched, it **tries again** 15 minutes later, 3 attempts in all, then gives
   up and discards the old plan with a note to generate a fresh one by hand.
6. A redo missed while the app was off runs on the next check during a session, still from 09:45.

Telegram gets a one-line summary of each redo pass, plus the usual message for each new plan.
Crypto is never queued: its market never closes.

### The US market calendar
`backend/app/markets.py` works the calendar out from the exchange's own rules, so it doesn't need
updating each year:
- **Regular hours:** 09:30–16:00 New York time, Monday to Friday. Summer time is handled.
- **Closed** on the 10 NYSE holidays: New Year's Day, Martin Luther King Jr. Day, Washington's
  Birthday, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving and
  Christmas. A holiday on a Saturday closes the market the Friday before, and one on a Sunday the
  Monday after, except that a Saturday New Year's Day isn't made up at all (so 31 December trades).
- **Closes at 1:00 pm** on the day after Thanksgiving, and on 3 July and 24 December when those fall
  Monday to Thursday.
- **One-off closures** (a national day of mourning, for example) can't be worked out and are added
  by hand; the list starts in 2018.
- **Checked** against NYSE's published calendar for 2026, 2027 and 2028. Still to come in 2026:
  Thanksgiving (Thu 26 Nov, closed), Fri 27 Nov (1:00 pm close), Thu 24 Dec (1:00 pm close) and
  Christmas (Fri 25 Dec, closed).

---

## 9. The AI

### Providers (chosen in Settings)
- **None:** the default. Clear rule-based text instead of AI.
- **Claude Code CLI:** uses your own Claude login (no API key). The app runs `claude -p` with **every
  tool switched off**, so it can only read what the app sends it (the one exception is a research call
  with web search allowed, see "Research mode" below). The model is **pinned** (default
  `sonnet`, set in Settings): without a pin the CLI answers with whichever model you last picked in an
  interactive session, so the narratives and the AI overlay's objections could change model without any
  sign of it. `sonnet`, `opus` and `haiku` always mean the latest model of that family; a full id such
  as `claude-sonnet-5-5` fixes one exact version. A blank value means "don't pin": whatever the CLI is
  set to. The sidebar shows the pin (for example `AI · claude_code_cli (sonnet)`), and **Test
  Connection** reports the model that really answered. This also works in Docker: the image installs
  the same CLI, and the app only adds `--model <name>` to the command.
- **OpenRouter, OrcaRouter, OpenAI, Gemini:** with your own key.

### Two models: a normal one and a decision one
Every provider can use two models:

| Tier | Used for | Setting |
|---|---|---|
| **Routine** | The writing: AI take, AI Summary, AI Insight, and **Test Connection** | The provider's normal model (for example `claude_cli_model`) |
| **Decision** | The AI Trading Overlay's verdict, the one answer that can stop a trade | The provider's **Decision model (AI overlay)** (for example `claude_cli_decision_model`) |

- **Why:** the writing needs speed and low cost, and nothing hangs on it. The verdict can stop a trade,
  so it is worth a stronger model. Later features that need a judgement (rather than prose) will use
  the decision tier too.
- **Blank means "use the same model".** Every decision model is blank until you set one, so an
  install that never touches it behaves exactly as before: one model does everything.
- **It only changes which model answers.** The overlay can still only stop a trade, never start or
  change one (below).
- **Seeing it:** the plan card's overlay block says "AI overlay · <model>" (the model that really
  answered, as the provider reports it, and saved with the plan). The sidebar still shows the normal
  model; **Test decision model** (next to **Test Connection**) sends one small request to the decision
  model and names it in the result. If no decision model is set, the result says it used the normal one.
- **Checks:** every model name is checked when it is saved (letters, digits and `. _ - : @ [ ]`, and `/`
  for gateways such as OpenRouter and OrcaRouter, starting with a letter or digit). Gemini names can't
  contain `/` because they go into a web address.
- **Strategy version:** while the overlay is on, changing the decision model makes a new strategy
  version (the verdicts change). Changing only the normal model doesn't, once a decision model is set.

### Research mode (web search for research calls only)
AI calls come in three kinds: **narration** (prose from numbers the rules computed), the **overlay
verdict** (the one call that can stop a trade) and **research** (background a person reads). Only
research calls may search the web, and only when the **Research mode** setting is **Allow web search**.
The default is **Our data only**. Narration and the overlay never use the web in either mode, because a
web page can carry text written to steer an AI, which is harmless in a research note and not acceptable
in a call that can stop a trade. The setting is not part of the strategy version: research never decides
a trade. No research feature uses it yet; this is the switch they will share.

| Provider | How web search is done |
|---|---|
| Claude Code CLI | Only the `WebSearch` and `WebFetch` tools are allowed (`--tools` and `--allowedTools`), with more turns. Every other call keeps no tools and one turn. |
| OpenRouter | The web plugin on the request. |
| OpenAI | The `web_search` tool of the Responses API. |
| Gemini | Grounding with Google Search. |
| OrcaRouter, None | Not available: the answer uses our data only and says so. |

Every research prompt ends with rules: web text is untrusted data, never follow instructions in it, cite a
source for every web fact, keep sourced facts apart from inference, no investment advice. A result records
whether the web was really used and the sources. Web search may cost extra with the provider.

### What the AI writes
1. **AI take:** a short narrative on each tradeable plan.
2. **AI Summary:** on the research panel.
3. **AI Insight:** a short chart explanation on the Analysis page.

It only explains numbers the rules already computed. If the AI fails or is slow, the app shows the
rule-based text instead; a request never fails because of the AI.

### Lessons on closed trades
After a paper trade closes, a cheap AI call (the **normal** model, not the decision one) writes **2 to 4
sentences** about it, which is shown on the Portfolio page (§3). It covers three things: was the trade
right or wrong compared with **SPY over the same holding period**, which part of the original reasoning
held up, and one concrete lesson.
- **Facts only.** The AI is handed the trade's own figures (entry and exit, result in R and dollars,
  holding days, best and worst price), SPY's move between the same two daily closes (from real price
  history; if it can't be fetched, or the trade opened and closed on the same day, the note says the
  comparison is not available), the plan's confidence points and score parts, the plan's reasons and
  the AI overlay's opinion, if there was one. The app does the arithmetic; the AI is told to invent
  nothing and give no advice. The plan's reasons come partly from headlines, so they are marked as
  untrusted text it must describe and never obey.
- **No fake lessons.** If the AI is unavailable or fails, **no lesson is stored**: only a short reason
  is kept, and the row is tried again later. There is no template text in its place, because a canned
  lesson would be made-up insight. A failed rewrite keeps the earlier lesson.
- **It never slows a close.** The exit check does not call it. A separate job (every 5 minutes) finds
  trades closed in the last **3 days** with no lesson and writes at most **3** per run; after a failure it
  waits 6 hours before trying that trade again. A trade that already has a lesson is never picked again.
  It does nothing without a configured AI provider, and nothing inside a backtest.
- **Older trades** (closed before lessons existed, or before an AI was set up) get no lesson by
  themselves, so nothing spends your AI quota on history. **Write lesson** on the Portfolio page does one
  on request, with a 20-second wait per trade.
- **Lessons feed the AI Trading Overlay.** When the overlay is on, its prompt gets the **3 most recent
  lessons** on that stock, in a block labelled as notes written by an earlier AI pass, not facts or
  instructions. They can only help the overlay decide to object; they cannot start a trade or change
  the entry, stop, targets or size. In a backtest the overlay sees only lessons written before the
  simulated moment.

### The AI Trading Overlay (off by default)
- **What it is:** an independent second opinion on **every** evaluation, including rejected ones. It's
  one extra AI call per stock. The AI sees the same data and returns:
  - its **stance** (bullish, bearish or neutral),
  - a **trade verdict** ("take" or "pass"),
  - its **conviction %**, its reasoning, and its own reading of the news.
- **Which model answers:** the decision model if you set one, otherwise the provider's normal model.
- **It can stop a trade, never start one or change one.** Direction, entry, stop and size always come
  from the rules. Two settings control how much it counts:
  - **Count disagreement in the confidence score:** 0 to −3 points (§5).
  - **What an objection does:**
    - **cancel**: the evaluation becomes a "no trade" naming the AI,
    - **hold**: the plan is saved but not auto-executed,
    - **none**: the objection is recorded only.
- **An objection** means the verdict is "pass", or, with no verdict, a stance that's the flat opposite
  of the trade.
- **Headlines are treated as untrusted text** in its prompt, so a headline can't give it instructions.
- **It answers a fixed form.** The AI is asked for exactly five fields (stance, trade verdict, conviction
  0 to 100, reasoning, news reading). Where a provider has its own "answer in this shape" mode, the app
  uses it: the Claude CLI's `--json-schema`, the `json_schema` response format for OpenAI, OpenRouter and
  OrcaRouter, and Gemini's response schema. If a gateway refuses that mode for a model, the call is retried
  once without it and a warning is logged. The AI's reply is then **read three ways, strictest first**, and
  the way used is saved on the plan:
  - **structured**: the whole reply was one JSON object that matched the form,
  - **lenient**: the reply was damaged (wrapped in a code fence or a sentence, a field missing or
    out of range) but a stance or verdict could still be recovered,
  - **failed**: nothing usable. The AI's own words are kept and shown, the log gets a warning, and the plan
    card says "AI overlay answered but its reply could not be read". **No verdict is ever invented**, so a
    failed read has no say in the trade, and now you can see that it happened instead of mistaking it
    for "the AI had no objection".
- **It is given a block of ground-truth numbers.** Before the other data, the prompt carries a block marked
  GROUND TRUTH that the app computed itself, with no AI: price and latest quote, change, trend and momentum,
  RSI, the 20 and 50 day averages, the nearest support and resistance, ATR, volume against its average, the
  52-week range, the next earnings date and how many days away it is, the options-implied move, and what the
  rules concluded (verdict, confidence, points by part). Units and rounding are fixed, and anything the app
  doesn't have reads "not available". The AI is told that every price, level, percentage or date it states
  must come from that block or the other data given.
- **Quoted figures are checked.** After the reply, the app looks for specific dollar prices and decimal
  percentages in the AI's reasoning that appear nowhere in the data it was given (within 1%, or equal at the
  precision quoted). Any it finds are saved on the plan and shown under the opinion as "Check these figures:
  model quoted $412.50, not in the data given". The check is deliberately cautious: it ignores years, round
  levels like "$550", whole-number percentages (often the AI's own confidence), and figures that appear in
  the headlines or fundamentals it was shown. **It is an honesty signal only: it never changes the verdict,
  the score, the direction or the size.**

---

## 10. Telegram notifications

Needs a bot token and a chat ID in Settings, and the Settings page has a test button.
- **Every tradeable plan:** symbol, direction, entry, stop, TP1, confidence, and whether it was
  auto-executed (or why not).
- **Provider down or back up:** market data, AI or Finnhub, sent only when the state changes.
- **Morning note, weekly digest and price alerts:** see §23. Each is its own switch in the Notifications card.

---

## 11. Where the data comes from

### Sources, tried in this order

For each kind of data, the app asks the first source that offers it. If that fails, it asks the next.

| Order | Source | Key needed? | What the app gets from it |
|---|---|---|---|
| 1 (only when enabled) | **Finnhub** | Free key | Quotes, company profile, news, earnings dates and estimates |
| 2 | **Yahoo Finance** (`yfinance`) | No | Everything except insider trades: daily, weekly and intraday prices; quotes; company info; financials; news; earnings dates, estimates and history; options |
| 3 | **Nasdaq** | No | Quotes, daily prices and company info, when Yahoo fails |
| 4 | **StockAnalysis** | No | Daily prices of stocks and ETFs only (unofficial endpoint); the last price fallback. Stooq now serves scripts a browser-check page, so it is skipped unless `stooq_enabled` is set |
| 5 | **SEC EDGAR** | No (it needs a contact label; see §14) | **Insider trades** (Form 4): the last 90 days, up to 12 filings per stock for the live check; years of dated history through the backfill (§11, "What the app knew, and when") |

**Other sources:**
- **Stock logos:** Elbstream (by ticker), with coloured letters when there's no logo.
- **Crypto logos:** the `spothq/cryptocurrency-icons` set, via jsDelivr.
- **Macro calendar:** a hand-kept table of Fed decisions (2026 and 2027), CPI and jobs reports (2026;
  the 2027 dates were not yet published by the Bureau of Labor Statistics when this was last checked),
  read from federalreserve.gov and bls.gov. If a day falls outside what the table covers, the app
  logs one warning and the macro penalty stays at 0 for the missing events.
- **Telegram:** for messages.
- **The AI providers** from §9.

**Credits and licenses:** the terms for these sources, and for any code copied from other open-source
projects, are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

### When a source fails
- If **every** source fails, the app answers "No data available" (HTTP 502). It never invents numbers.
- For **display**, the app can show the **last good value** it received. For **decisions** (the exit
  check and the re-quote at execution) it never uses old values; that part fails safely instead.

### Cache: how long answers are reused
Answers are kept in memory, so refreshing a page doesn't call the sources again, and **also saved to a
file** (`runtime/cache.db`), so a restart or redeploy starts with them instead of empty:

| Data | Reused for |
|---|---|
| Prices and quotes | 15 minutes |
| News, options | 30 minutes |
| Insider trades | 12 hours |
| Company info, financials, earnings | 24 hours |
| The SEC ticker list | 7 days |

- Identical requests arriving at the same moment are merged into one.
- The browser reuses answers for 15 seconds.
- The sidebar's check every 60 seconds only reads your settings.
- **Saved to disk.** Every answer is also written to `runtime/cache.db`, a small file kept apart from the
  trading database (so it can be deleted at any time and never bloats a backup). After a restart the
  app reads answers from it while they are still fresh, so a redeploy doesn't send a whole scan's worth of
  requests back to Yahoo. The time limits above are counted by the clock, so they keep running while the
  app is stopped.
- **Old data still survives a restart.** The "last good value" shown during a provider outage (above) is
  kept in the file too, for up to 14 days. A redeploy in the middle of a Yahoo outage therefore still
  shows the last real numbers, and the exit check and the execution re-quote still refuse every old
  value, from memory or from the file.
- **Safe to lose.** If the file can't be written (full disk, locked, damaged), the app carries on with
  the in-memory cache alone; a damaged file is replaced. Answers are stored as plain data, never as
  executable objects. The file holds at most 200 MB (the least recently used answers go first), and one
  answer bigger than 100 KB (a multi-year download) is not saved there: those belong in the price
  history store below.
- Set `PERSIST_CACHE_DB=false` (§12) to keep the cache in memory only. The **Data cache** card on the
  Settings page shows what is held, how often answers were reused since the app started, and has a
  **Clear cache** button (30-second cooldown) that empties both the memory and the file.

### Price history store (for research)
A backtest needs years of daily prices for hundreds of stocks. Asking Yahoo for that every time would be
thousands of requests, so the app can **download each stock's history once and keep it** in
`runtime/history.db`. After the first download, a later top-up only fetches the few newest days.

- **Load it** with `backend/.venv/Scripts/python scripts/preload_history.py AAPL MSFT` (also
  `--benchmarks` for SPY and ^VIX, `--sp500` for the bundled list, `--years N`, `--report` to see what is
  held). It pauses between requests and can be re-run safely.
- **Only real, finished days.** A missing day stays missing: nothing is filled forward or guessed. A day's
  bar is saved only once the day is over (the half-formed bar Yahoo returns during a session is never
  kept), and a bar with a blank price is dropped.
- **A failed download changes nothing.** What is already stored stays, and the failure is reported.
- **No peeking at the future.** A request for prices up to a date returns nothing later than it. Inside a
  simulated run the end date is also limited to the last day finished at the simulated moment.
- **No seams.** Yahoo adjusts old prices after each dividend, so new days can't simply be added to old
  ones. Every top-up overlaps the stored days by about a week and compares them; if they disagree the
  whole history is downloaded again. Prices from a different source than the stored ones (say Nasdaq while
  Yahoo is down) are not mixed in.
- Only **daily** prices for now; intraday bars are planned. The live scan and exit check do **not** use
  this store: they always use fresh data.
- It is not part of the cache: **Clear cache** leaves it alone. The Data cache card shows its size and range.

### The stock list
- `backend/data/sp500.csv`: **506 rows**, with columns `symbol`, `name` and `sector`. The top **64** are
  hand-picked: BTC-USD, ETH-USD and SOL-USD, then 61 large US stocks. After them come **every other
  current S&P 500 member** (442 rows), sorted A to Z by symbol. Some companies have two share classes
  (Alphabet's GOOG and GOOGL, for example); each class is its own row, as in the index. Symbols use the
  Yahoo format, so Berkshire's class B is `BRK-B`.
- The **scan size** setting (50) takes the first *N* symbols. That is why the hand-picked block stays at
  the top and in the same order: a normal scan, the dashboard counts and the Docker test filter all see
  the same names as before. The other ~440 names are there for picking one company in the dropdowns, for
  the sector lookup used by the per-sector position cap, and for the S&P 500 backtest (`--sp500` in the
  price preload script). A scan of the whole list is not offered: `GET /api/scan?symbols=` accepts at
  most 200 symbols, so keep Scan size at 200 or below.
- **Sector names** follow the usual 11 market sectors, written as Technology (the source's "Information
  Technology"), Healthcare ("Health Care") and so on; crypto rows have the sector Crypto. Alphabet and
  Meta are in Technology in the hand-picked block, though the official index files them under
  Communication Services (Alphabet's second share class, GOOG, is listed there, so GOOGL and GOOG have
  different sectors).
- **Refreshing it:** `backend/.venv/Scripts/python scripts/refresh_universe.py` downloads the current
  member list from the public-domain *S&P 500 Companies* dataset on GitHub (rebuilt from Wikipedia's list
  on a schedule). It keeps the hand-picked 64 rows exactly as they are, replaces everything after them,
  and checks that every symbol is also in the SEC's own ticker file, so a typo or a delisted ticker is
  caught. It refuses to write the file on a blank sector, a duplicate, a symbol in the wrong format or a
  row count outside the normal range. `--dry-run` checks without writing. The last run found all 503 stock
  symbols in the SEC file.
- **Caveat (survivorship):** the list is *today's* members. Companies that were in the index in earlier
  years and have since been removed are not in it, so a backtest over past years looks only at the
  survivors, and its results will look better than a real trader's would have. A dated membership history
  is not built.
- The **test filter** `STRATEGEIA_DEV_TICKERS` narrows *every* screen to a few symbols. It's set to
  **NVDA and AAPL** in `docker-compose.yml`, which survives Docker rebuilds.

**Your own watchlist (Settings -> Watchlist).** The bundled file needs a rebuild to change, so the app
also lets you keep your own list. It is saved to `runtime/universe.json`, next to `settings.json`, in the
volume that survives `docker compose up --build`. A save takes effect at once: no restart, and the
scheduled auto-scan reads the new list on its next run.

Which list is used, strongest first:

| Layer | Used when | What it means |
|---|---|---|
| Test filter `STRATEGEIA_DEV_TICKERS` | The variable is set on the server | Exactly those symbols (in the order written), everywhere. Your saved list is kept but ignored, and the card says so |
| Your saved watchlist | You have saved one | Your symbols, in your order |
| Bundled `sp500.csv` | Neither of the above | The default list |

- **Order matters.** A scan takes the first *N* symbols (the Scan universe size setting) and the
  Dashboard's top setups come from that scan. The card shows "scanning the first N of M" and marks the
  rows that fall past the cut. The Scan universe size choices are 25, 50, 100 and 200, and the setting
  says how many of your symbols that covers.
- **Adding a symbol** is checked first: the app asks the data providers for a quote and refuses a
  symbol that returns none, so a typo is caught before saving. Symbols are capital letters, digits and
  `. - ^`, up to 12 characters (`BRK-B`, `BTC-USD`, `^VIX`). Duplicates are dropped. A list holds 1 to 200
  symbols.
- **Names and sectors.** The bundled file is still the catalogue: a symbol it knows keeps its name and
  sector. For a symbol it doesn't know, the company name is asked from the data provider once when you
  save (and kept in the file); if that fails, the ticker is shown as the name. Its sector is **unknown**
  (a `-USD` pair is Crypto), and the card says "sector unknown". The per-sector position cap simply
  doesn't count a symbol with no sector, instead of grouping all of them together.
- **Held positions keep their sector.** The sector lookup uses the catalogue and the saved list, never
  the list in use, so a position stays correctly counted after its symbol is removed from the watchlist
  or the test filter hides it.
- **If the file is damaged** (not valid JSON, empty, a bad symbol) the app logs a warning once, uses the
  bundled list and shows the reason on the card. It never stops working. Saving a new list replaces the
  file. Writes are atomic (a temporary file, then a rename), so a crash mid-save leaves the old list.
- **Reset to the bundled list** deletes the file. You can also delete `runtime/universe.json` by hand.
- A watchlist saved through the API (`PUT /api/watchlist`) is not checked against the market data
  providers; only the Add box does that.

### Money
Every source above is free. Finnhub's free tier allows 60 requests a minute. The practical limit is
**Yahoo temporarily blocking** heavy use, and the cache is what prevents that. The only cost is the AI,
if you use a paid provider.

### What the app knew, and when (dated facts)
A backtest replays the past: "on 1 March 2024, would the app have bought?". It's only honest if the app
sees **only what was public on 1 March 2024**. If it can peek at a news story, an insider trade or a
report that came out later, the result looks better than anything a real trader could have done. This
mistake is called **look-ahead**.

So every piece of information the app stores (prices aside) is saved as a **dated fact** with three
times:

| Time | Meaning | Example: a Congress member's trade |
|---|---|---|
| **Known at** | When it became public: the first moment a trader could have acted on it | The day the report was filed |
| **About** (effective) | The date the fact describes | The day of the trade, often weeks earlier |
| **Fetched at** | When the app downloaded it | When our watcher found it |

- **Known at** comes from the source's own time stamp when it has one (the SEC stamps each filing with
  the exact second it was accepted). With no time stamp, it's the moment we fetched it. When a source
  only gives a date, the app counts it as known at the **end** of that day, never the start. Times are
  always stored in UTC.
- **Live,** the app reads everything known up to now.
- **In a backtest,** the app is set to a past moment and can only read facts known by then. Asking for a
  later one is refused with an error. Only an admin/debug view can switch this check off, and only
  on purpose.
- The same fact saved twice is kept once. If better evidence shows it was public *earlier*, its
  "known at" moves earlier; it never moves later.

**The news and fundamentals archive.** The first thing written to this table is the app's own record of
what it fetches. Yahoo and the other free sources only show **today's** news and **today's**
fundamentals; none of them can say what the headlines were on 12 March. The only way a future backtest
can replay a past day is if the app wrote that day down when it happened. So, whenever the app loads
the news and company numbers for a stock (the Analysis page's research panel, or when it evaluates a
stock for a trade plan), it also saves them:

| Saved | Known at | Saved again when |
|---|---|---|
| **News item** (headline, publisher, link, the source's own time stamp) | The item's own publish time if the source gave one with its time zone; otherwise the moment we fetched it | Never: the same link for the same stock is kept once |
| **Fundamentals snapshot** (company name, trailing revenue and EPS, market cap, P/E, 52-week range, the yearly revenue and net income history) | The moment we fetched it (no free source says when a figure first became public) | A slow-moving number changed: trailing revenue or EPS, the company name, or the yearly history (a new fiscal year or a revision) |

- A snapshot is not re-saved just because the price moved. Market cap, P/E and the 52-week range change
  with every price tick, so they are stored with each snapshot but don't count as a change by themselves.
- The archive **adds nothing to a stock's score and makes no extra downloads**: it only keeps what was
  already fetched. It is **best-effort**: if saving fails, the page or trade plan carries on as if
  nothing happened (the failure is written to the log). It also never writes during a backtest.
- **Honest limit:** the archive only exists from the day the app first saved something. A backtest
  of an earlier day has no recorded news for it, and must say so rather than treat "nothing saved"
  as "nothing happened". The Saved history card on the Analysis page shows that start date.
- It stays small: a news item and a snapshot are a few hundred bytes each, repeated fetches add nothing,
  and nothing is deleted automatically.
- `GET /api/archive/{symbol}` shows what's saved (§16). It only reads.

**Press releases (PR Newswire).** A company's own announcement (an earnings date, a deal, a new director)
is often on a news wire before it reaches Yahoo's list. The app can read PR Newswire's two public RSS feeds
(all releases, and financial services) and save the releases that are about a watchlist stock as news, with
the source "PR Newswire" (`backend/app/data_providers/pr_newswire.py`). Each feed holds only the newest
20 or so releases, so this collects from now on; it is not a backfill.

- **Known at:** the release's own time stamp (the feeds give it with a time zone). It is never later than
  the moment the app fetched it. A release with no usable time is saved at the fetch time instead.
- **Matching is strict**, because a wrong match would put another company's news under your stock. A release
  matches a symbol only when it names it with an exchange tag ("(NASDAQ: NVDA)", "NYSE: IBM", or a
  parenthesised list such as "(NASDAQ: CHI, CHY and CSQ)") or contains the company's **exact full name**
  from the watchlist (for example "Apple Inc.", same capital letters, whole words; a short or similar name
  does not count, nor does a name shorter than 5 characters). A release that matches nothing is not saved.
- **Polite:** the app identifies itself, waits at least a second between requests, keeps a feed in memory
  for 5 minutes, retries a failed request a couple of times with growing pauses, and reads only the title,
  link, time and the short summary in the feed (never the release page). It makes 2 requests per collection.
- **Failures degrade:** a feed that cannot be fetched or read is reported and skipped; the other feed still
  runs and nothing is invented. A feed that declares a DOCTYPE or entity (which a plain RSS feed never does)
  is refused.
- **How it runs:** the **Collect press releases** button on the Analysis page's News tab, or
  `POST /api/news/collect` (60-second cooldown). A later watcher can call the same function. It never runs
  during a backtest.

**News cards (AI labels on headlines).** When switched on, an AI labels each saved headline **once**
(`backend/app/services/news_card_service.py`, the card form in `backend/app/knowledge/news_cards.py`). The
form has: the event type (earnings, guidance, mna, product, legal, regulatory, leadership, analyst, macro,
other), the sentiment (positive, negative, neutral, mixed), the materiality (high, medium, low), the
companies mentioned, a one-line summary and whether the headline is mainly about this company.

- **Which AI, which model:** the provider chosen in Settings, on its normal (routine) model. With no AI
  set up nothing is labelled and no label is made up.
- **Batches and limits:** about 10 headlines per AI call, at most 3 calls per run, and at most the "Most
  headlines labelled per run" setting (default 20, up to 30). A headline that already has a label is never
  sent again.
- **The AI only extracts.** The prompt marks every headline as untrusted text pulled from third-party feeds
  (never instructions) and forbids views on the stock. The reply must match the form exactly (a wrong word,
  a missing or extra field, a repeated or out-of-range headline number): if any part does not, **nothing**
  from that batch is saved and those headlines stay unlabelled for next time. An AI error saves nothing
  either.
- **What a label does:** it is shown as chips on the News tab and recorded as the silent signal `news_cards`
  (§5). It changes no score, direction or trade today.
- **Not point-in-time (an honest limit).** A label is saved with the time it was **made**, not the time the
  headline was published, and each label records the model and the moment (`label_model`, `labelled_at`).
  A model asked about an old headline may already know how the story ended, so its label can quietly carry
  that knowledge. Labels are therefore only meant to be judged going forward (or on headlines published after
  the model's training), and a backtest simulating a past moment sees none of them.
- **Switched on in Settings** (News cards card; off by default because it spends AI calls). **Label now** on
  the News tab, or `POST /api/news/label/{symbol}` (20-second cooldown per symbol), runs one labelling pass.

**FINRA short-sale volume (a silent signal, §5 "Silent signals").** The Analysis page's Overview tab shows a "Short volume (FINRA)" card for the symbol (recent vs baseline ratio, last 10 days) with a Refresh button. FINRA posts one free file per
trading day listing, for every US stock, how much of that day's volume was a short sale. The app
downloads those files for your watchlist symbols only (`backend/app/data_providers/finra_provider.py`),
keeps each downloaded file under `runtime/finra/` (a posted file never changes, so it is fetched once),
and stores one dated fact per symbol per day. The dated-fact time rule is conservative: a day's file counts
as public at the **end of that trading day, New York time**. (FINRA's own file stamps showed about 5:18 pm
New York on the trading day for the two days checked, so this is a few hours later than observed.) Fill the
history with `scripts/backfill_finra.py`, or press the refresh route (§16), which looks back about ten days.
Short-sale **volume** is not short **interest**: a large share of it is market makers providing liquidity.

**Insider trades with their true public time (SEC Form 4).** When a company's insider (an officer,
a director or a 10% owner) trades the company's stock, the SEC requires a Form 4 within 2 business
days. The app can read these filings and store **every transaction row as a dated fact**
(`backend/app/data_providers/sec_form4.py`):

| Time | What it is for an insider trade |
|---|---|
| **Known at** | The moment the SEC **accepted** the filing, to the second (the SEC lists it in UTC; 22:30 UTC in summer is 6:30 pm New York). Before that moment nobody could have known |
| **About** (effective) | The **transaction date**. A purchase made on the 22nd and filed on the 24th at 6:30 pm was **not** knowable on the 22nd or 23rd |

- **What is stored per row:** the insider's name and SEC number (when there is one), whether they are an
  officer, director, 10% owner or other (and the officer title), the code and its meaning (P = open-market
  purchase, S = sale, A = grant, M = option exercise, F = shares withheld for tax, G = gift, and the rest of
  the SEC's list), bought or sold, shares, price, total value, shares owned afterwards, direct or indirect
  ownership, whether it was made under a **10b5-1 plan** (a pre-arranged trading plan, from the filing's
  checkbox or its footnote text), the footnotes, and the link to the filing. Both the stock table and the
  options/RSU table are read. A row that gives no price (some list only a footnote) is stored with **no
  price**, never as 0. A line that only reports what is owned (no transaction) is not a trade and is skipped.
- **Amendments (Form 4/A)** restate the original in full. Each filing's rows are kept as they were, and the
  readers swap in the amendment **from the moment it was accepted**: before that, the original is what was
  known. If one person filed two original forms for the same period on the same day, an amendment can't be
  matched to one of them, so nothing is replaced (a possible double count rather than a lost trade).
- **Filling it in:** `backend/.venv/Scripts/python scripts/backfill_insider_trades.py AAPL MSFT --since 2016-01-01`
  (`--db` picks the database file; without it the app's own database is used; `--report` only prints what
  is stored). It lists each company's filings from the SEC (the recent list and the older pages), downloads
  each one, and stores the rows. Re-running is safe: filings already stored are skipped. A filing that
  fails is reported and skipped; the rest continue. `fetch_new_insider_trades` does the same for just the
  newest filings, and the SEC watcher (§19) uses it to keep the table current.
- **Being polite to the SEC:** all of this shares one client (`sec_client.py`) that identifies itself with
  your SEC contact (§12), makes **at most 5 requests per second** across the whole app (the SEC allows 10),
  waits and retries when the SEC says "slow down" or has a hiccup, and gives up with a clear error after four
  tries. Downloaded filings never change, so they are kept in the `sec_archive` folder beside the database
  (capped at 300 MB, safe to delete); a re-run then needs no downloads.
- **Reading it back (as of any moment):** `app/knowledge/insider_trades.py` has `insider_trades_as_of`
  (the rows in filings accepted in the last N days, by default only open-market purchases),
  `insider_activity_as_of` (the same buy/sell counts and dollars the live scoring uses) and
  `insider_clusters_as_of` (at least 2 different insiders buying within 14 days of each other, with the
  count, value and roles). All three read only what was known at the chosen moment. The "last 90 days"
  window counts **filings accepted** in those days, as the live check does.
- **This does not change today's scoring.** The live insider point (§5) still comes from the quick check of
  the latest filings. Differences from it, on purpose: the dated reader counts only the stock table (the
  live check also counts option rows coded P or S), reads every filing in the window (the live check stops at
  12), and counts an amended filing once. On a real company's last 90 days the two agree exactly once the
  live check's 12-filing limit is applied.
- **Honest limit:** a company with no stored insider rows gives "no data" from `insider_activity_as_of`
  (not "no insider trades"), because the history may simply not have been loaded.

**Company announcements (SEC 8-K).** A company files an 8-K within four business days of a material event.
Each filing lists numbered **item codes** (2.02 results, 5.02 officer changes, 1.01 a material agreement,
8.01 other events, and so on), which the SEC's company index already carries, so no filing has to be
downloaded to classify it (`backend/app/data_providers/sec_8k.py`). Each filing is stored as one dated fact:

| Time | What it is for an 8-K |
|---|---|
| **Known at** | The moment the SEC **accepted** the filing |
| **About** (effective) | The filing's **report date** (the event it describes, often a day or two earlier) |

- **What is stored:** the filing number, the form (8-K or 8-K/A), the item codes, a plain title for each, a
  coarse **category** for each (results, leadership, agreement, restructuring, regulatory, other), and links
  to the filing's page and main document. A code the app does not know is kept and called "Unlisted item".
- **Filling it in:** `backend/.venv/Scripts/python scripts/backfill_8k.py AAPL MSFT --since 2016-01-01`
  (`--db` picks the database file; `--report` only prints what is stored). One request per company plus one
  per older index page. Re-running is safe: filings already stored are skipped, and a symbol that fails is
  reported and skipped. The SEC watcher (§19) keeps it current afterwards.
- **Reading it back:** `filings_8k_as_of(session, symbol, as_of, window_days, items)` returns the filings
  accepted in the window as they were known at that moment, optionally only those listing given item codes.
  A filing accepted after the moment is invisible.
- **Scoring:** none yet. The only use is the silent signal in §5 ("Silent signals").

**House trade reports (Congress).** Members of the House must report stock trades within 45 days. The House Clerk publishes a
yearly index (a zip with the filing type, date and document number of every disclosure) and one PDF per trade report; both
are read by `backend/app/data_providers/house_disclosures.py` with a polite client (one request a second, the index cached
for an hour, PDFs cached on disk because a filed report never changes). The PDF text is extracted with the pure-Python
`pypdf` library and each table row is read into: owner (self, spouse, dependent child, joint), asset name, ticker (when the
name carries one in brackets), type (purchase, sale, partial sale, exchange), trade date, notice date and amount range.
Each row is stored as one `congress_trade` dated fact, and each report as one `congress_filing` fact that records whether it
was read in full, in part, or not at all.

- **Two dates:** `known_at` is the **filing date** (the end of that day in New York, never later than our own fetch time);
  `effective_at` is the trade date. Anything that tests or decides must use the filing date, because the trade was not
  public until the report existed.
- **Reading it back:** `congress_trades_as_of`, `congress_clusters_as_of`, `net_buyers_as_of` and the member summaries (all in
  `backend/app/knowledge/congress_trades.py`) go through the same dated-fact readers as every other source, so a report filed
  after the moment is invisible. Windows count reports filed in the window.
- **Fund holdings and 5% owners** (`fund_holding`, `fund_filing`, `ownership_filing`): each security in a 13F is one `fund_holding` fact and each 13F filing one `fund_filing` fact, with `known_at` the moment SEC accepted the filing and `effective_at` the quarter end; each structured Schedule 13D/13G is one `ownership_filing` fact, with `known_at` its acceptance time and `effective_at` the event date. The readers (`fund_changes_as_of`, `fund_holders_of_symbol`, `fund_positions_as_of`, `ownership_filings_as_of`, in `backend/app/knowledge/fund_holdings.py`) hide anything accepted after the moment, and an amendment takes effect only from its own acceptance time. Loading is idempotent by accession number.
- **Failures are visible, not guessed:** a scanned or damaged PDF is recorded as `unreadable`; a row the reader could not
  parse makes the report `partial`. No OCR or AI is used.
- **Loading it:** the watcher (section 19), the refresh button (section 20) and `scripts/backfill_house.py` (resumable;
  `--year`, `--since`, `--until`, `--max-filings`, `--db`).
- **Not covered:** the Senate. Its eFD site returns 403 to scripts and is not scraped. A third-party source could be added
  behind the same fact kind later.

**The Fed (statements, speeches, testimony).** The Fed publishes three public RSS feeds (monetary policy
releases, speeches, testimony), read by `backend/app/data_providers/fed_feed.py`. Each item is stored as one
dated fact of kind `fed_speech`, market-wide (no symbol):

| Time | What it is for a Fed item |
|---|---|
| **Known at** | The item's publication time in the feed, converted to UTC. An item whose time has no time zone is skipped, not guessed |
| **About** (effective) | The day of the event |

- **What is stored:** the title, the link, the category (monetary policy, speech, testimony), a plain **type**
  worked out from the title by rules (policy statement, projections, meeting minutes, other), the speaker for
  speeches and testimony, whether that speaker is a Chair, and the feed's one-line summary.
- **Who counts:** a small list in the code of the Chair and the Board members. A speech by anyone else is stored and
  never alerts. The list is edited by hand when the Board changes.
- **Depth:** each feed lists only its newest 15 items, which reaches back a few months. Older history is not loaded.
  `backend/.venv/Scripts/python scripts/backfill_fed.py` stores what the feeds list now (`--db` picks the database
  file; `--report` only prints what is stored). Re-running is safe, and a feed that failed gets another try. The
  Fed watcher (§19) keeps it current.
- **Tone is not read.** Whether a speech is hawkish or dovish needs a language model, and such a reading can only
  be tested honestly on speeches made after the model's training cut-off. It is a planned, separate step. Nothing
  here uses an AI.
- **Scoring:** none. The only use is the silent signal in §5.

**Posts (Donald Trump, Truth Social).** Truth Social refuses scripts (HTTP 403), so the posts are read from
**trumpstruth.org, an unofficial third-party archive** that republishes them as an RSS feed
(`backend/app/data_providers/posts_feed.py`). Every place the app shows a post says so. The archive can lag
behind the real post, drop or change items, change its format, or stop. In a live check its post times matched the
posting times to the second, but nothing guarantees that. Each post is stored as one dated fact of kind `post`
(market-wide: which companies it names is worked out by rules when it is read, not stored):

| Time | What it is for a post |
|---|---|
| **Known at** | The post time the feed gives, converted to UTC |
| **About** (effective) | The same moment |

- **What is stored:** the post's id, its text (HTML removed), the Truth Social link, the archive's link, and whether
  it is a repost ("RT ...") or has no words (an image or a video).
- **Depth:** the feed lists its newest 100 posts, a day or two at the current pace.
  `backend/.venv/Scripts/python scripts/backfill_posts.py` stores what it lists now (same options as the Fed
  script). His older tweets (2009 to 2021) are kept by thetrumparchive.com as a JSON export; they are not loaded,
  because they say nothing about how the live feed behaves.
- **No AI reads a post.** An AI may only ever extract facts from words; what a post means for a stock is decided
  by rules (§19, "The posts watcher"). Nothing here uses an AI.
- **Scoring:** none. The only use is the silent signal in §5.

Other planned sources (Congress trades and big funds' holdings) will use the same table, and each new signal is
backtested on these dates before it may earn points.

### Macro series: US (FRED) and euro-area (ECB)

Two free sources that need no key give plain data series, not per-stock quotes. They are not part of the
fallback chain above. Nothing in the scoring uses them yet; they are there for the Market Terminal and later pages.

| Source | What it gives | How |
|---|---|---|
| **FRED** (St. Louis Fed) | Treasury yields (3-month, 2-year, 10-year), the 10y minus 2y spread, the fed funds rate, the VIX close, CPI, unemployment, the broad dollar index | The public CSV download, no key |
| **ECB** (European Central Bank) | The euro-area deposit and refinancing rates, EUR/USD, EUR/GBP and EUR/JPY reference rates, euro-area inflation | The ECB Data Portal, CSV, no key |

- **Missing values are skipped, never turned into zero.** FRED writes "." for a day with no reading (a holiday);
  the ECB leaves the value blank. Both become a gap.
- **Kept for 6 hours** in the data cache (the cache card above). If a refresh fails, the last good copy is shown
  instead of an error, and the response says when it was fetched.
- **Policy rates are one point per change**, not one per day.
- **The ECB's older inflation dataset stopped at December 2025** (the ECB moved to a new one). The series is still
  offered, but its `last_observation_date` shows how old it is. A replacement was not found.
- **Where to read it:** `GET /api/macro/series` lists what is offered; `GET /api/macro/series/{id}?start=YYYY-MM-DD`
  returns the points (FRED series default to the last five years, ECB series to their newest 520 points).

### The Data sources card (Settings)

Settings shows every source with its live health, so a slow or broken source is visible before it hurts a scan.
Every call the app makes to a source is counted once, in one place, since the server started.

| Column | Meaning |
|---|---|
| **Dot** | Healthy (recent calls worked), degraded (some failed, or old cached data had to be shown), failing (most failed, or three in a row), unused (not called yet) |
| **Calls** | Calls since the server started. A cached answer counts as a call but not as a latency sample |
| **Success** | Share of the last 100 calls that worked |
| **p50 / p95** | Median and slow-end (95th percentile) time of real requests among the last 100 |
| **Last success / last error** | When it last worked, and the last error text (shortened; web addresses and keys are removed) |
| **Probe** | One tiny real request (a SPY quote, or a small series) that skips the cache. 10-second wait per source |

- **The chain is listed in its fallback order**, then the other sources: SEC filings, FINRA, ECB, FRED and the House Clerk.
  FINRA has no probe, because its files exist only for trading days.
- **Nothing is invented.** Before any call has been made the card says so and shows dashes.
- **Backtests and replays are not counted**, so live health is not coloured by simulated runs.
- **Numbers reset when the server restarts**; they are not saved.

---

## 12. Settings reference

### In the app (Settings page, saved to `runtime/settings.json`)

| Setting | Default | What it does |
|---|---|---|
| AI provider | none | Which AI writes the narratives (§9) |
| Model and key per provider | per provider | For example OpenRouter `anthropic/claude-3.5-haiku`, OpenAI `gpt-4o-mini`, Gemini `gemini-1.5-flash`, OrcaRouter `orcarouter/auto` |
| Decision model, per provider (`claude_cli_decision_model`, `openrouter_decision_model`, `orcarouter_decision_model`, `openai_decision_model`, `gemini_decision_model`) | blank | The model that answers the AI overlay's verdict. Blank = the same model as the provider's normal one. Same characters as the normal model; Gemini's can't contain `/` (§9) |
| Claude CLI model | `sonnet` | The model the Claude Code CLI is pinned to (`--model`). An alias or a full id; letters, digits and `. _ - : @ [ ]` only, up to 64 characters. Blank = don't pin, use whatever the CLI is set to (§9) |
| Finnhub enabled, and key | off | Adds Finnhub as the first source for what it offers |
| AI Trading Overlay | off | The second opinion (§9) |
| Count disagreement in the confidence score | on | The overlay can cost 0 to −3 points |
| Overlay objection action | cancel | cancel, hold or none (§9) |
| Research mode | Our data only | Our data only, or Allow web search for research calls (§9). Never affects narration or the overlay |
| Minimum confidence for a trade | 30% | The trade / no-trade bar |
| Telegram bot token, and chat ID | empty | Messages (§10) |
| Scan universe size | 50 | How many symbols from the top of the watchlist to scan (25, 50, 100 or 200 in the app) |
| News cards on / off (`news_cards_enabled`) | off | Lets the AI label saved headlines (§11, "News cards"). Spends AI calls, so it is off until you switch it on |
| Most headlines labelled per run (`news_card_batch_limit`) | 20 | 1 to 30. A run also never makes more than 3 AI calls |
| Watchlist | the bundled list | The symbols to scan, saved separately in `runtime/universe.json` (§11), not in `settings.json` |
| Starting cash | $100,000 | Only applies to a fresh or reset account |
| Default risk per trade | 1% | Sets the share count (§6) |
| Exit-check interval | 15 minutes | How often positions are checked |
| Slippage | 5 bps | The cost added to market fills (entries and stops) |
| Commission per trade | $0 | — |
| Watchers on / off (`watchers_enabled`) | off | Master switch for the background watchers (§19). Installed: the SEC filings watcher, the Fed watcher and the posts watcher |
| What a watcher event does (`watchers_action`) | record, alert and re-evaluate | `record`, `alert` or `alert_and_reevaluate` (§19) |
| Congress follow mode (`smart_money_follow_congress`) | all | `all` follows every House member; `list` limits the Congress tab's follow filter and the House watcher's alerts to the names below (section 20) |
| Followed funds (`smart_money_followed_funds`) | empty | Up to 50 fund manager CIK numbers (digits only, at most 10 each) whose 13F reports the Funds tab loads and the fund filings watcher checks. Empty means the built-in starting list: Berkshire Hathaway, Pershing Square, Scion Asset Management, Duquesne Family Office, Appaloosa and Bridgewater (section 20) |
| Followed members (`smart_money_followed_members`) | empty | Up to 50 member names, each 1 to 80 characters, matched ignoring case and punctuation. Only used in `list` mode |
| Watcher check interval (`watchers_poll_minutes`) | 5 | How often the app looks for watchers that are due, 1 to 60. Takes effect after a restart |
| Auto-execute trade plans | on | Opens a position as soon as a plan is made, if the market is open (otherwise the plan is redone at the next open, §8) |
| Unattended auto-scan | off | The 3-times-a-day loop (§8) |
| Max open positions | 5 | — |
| Maximum holding time | 20 trading days | A position still open after this many trading days is closed at that day's close (§7). 0 = no limit. Up to 60 |
| Morning note on / off (`morning_note_enabled`) | off | The daily Telegram brief (§23) |
| Morning note time (`morning_note_time_et`) | 08:45 | When it is sent, as HH:MM New York time. Checked every 5 minutes, so a change applies at once |
| Weekly digest on / off (`weekly_digest_enabled`) | off | The Friday Telegram review (§23) |
| Alerts on open positions (`price_alert_positions_enabled`) | on | A message when an open position gets close to its stop or first target. Without Telegram it only records the event (§23) |
| Close to the stop (`price_alert_stop_atr`) | 1 ATR | How near the stop counts as close, in multiples of the average daily range. Above 0, up to 10 |
| Close to the first target (`price_alert_tp1_pct`) | 1% | How near the first target counts as close, as a % of the price. Above 0, up to 20 |
| Max positions per sector | 2 | — |
| Max position, as a % of average daily volume | 1% | — |
| Thesis alerts | on | One Telegram message when an open position's thesis first becomes broken |
| AI Committee: most AI calls per run | 14 (6 to 40) | A hard cap on the AI calls of one committee run (§25) |
| AI Committee: bull / bear rounds, risk debate rounds | 1 and 1 (each 1 to 3) | How many rounds each debate may run. Never more than 3, whatever is saved |

### On the server (the `backend/.env` file, or Docker environment variables)

| Variable | Default | What it does |
|---|---|---|
| `AUTH_USERNAME` | admin | The login name |
| `AUTH_PASSWORD` | auto-generated | The login password. If it's empty, one is created and saved to `runtime/auth_password.txt` |
| `API_SHARED_SECRET` | auto-generated | The key for scripts and Swagger (the `X-API-Key` header), saved to `runtime/api_key.txt` |
| `ALLOW_UNAUTHENTICATED_API` | false | Set to true to switch the login off. **Local use only.** |
| `SESSION_LIFETIME_DAYS`, `MAX_LOGIN_ATTEMPTS`, `LOGIN_LOCKOUT_MINUTES` | 7, 3, 15 | Login rules |
| `SEC_EDGAR_USER_AGENT` | a made-up contact (`contact@strategeia.example`) | The contact the SEC sees on each insider-trade download. Write it as `Name/1.0 (purpose; you@example.com)`: plain English letters, no link (the SEC refuses links). **In Docker**, put it in the `.env` file next to `docker-compose.yml`, which passes it in. Blank means the made-up contact (§14) |
| `CORS_ORIGINS` | `http://localhost:5173` | Which web address may call the API |
| `STRATEGEIA_DEV_TICKERS` | unset | The test filter (§11): overrides the watchlist you saved in the app until it is removed |
| `PERSIST_CACHE_DB` | true | Save fetched market data to `runtime/cache.db` so a restart starts warm (§11). Set to false for a memory-only cache |
| `CLAUDE_CODE_OAUTH_TOKEN` | empty | Lets the Claude CLI work inside Docker |

---

## 13. Login and security

- **One user.** There's no sign-up. The login issues a signed cookie that lasts 7 days; logging out
  clears it in the browser.
- **Lockout:** 3 wrong passwords lock that device (by IP address) out for 15 minutes. Other devices
  aren't affected.
- **Scripts** can use the `X-API-Key` header instead of the login.
- **Secure by default:** if no password or key is set, the app creates random ones and saves them in
  `runtime/`.
- **Open mode:** `ALLOW_UNAUTHENTICATED_API=true` switches all of this off. The current
  `docker-compose.yml` uses open mode, which is only safe on your own computer or home network.
- Security headers are added to every response, and settings keys are never sent back unmasked.

---

## 14. Limitations and known bugs

### Limitations
- **Fund and 5% owner data is limited by the forms.** A 13F is long positions only, a quarter-end snapshot filed up to 45 days late, with no trade dates; a stock split shows as a large add. Tickers come from exact name matching, so some holdings stay without one. A fund's thousands-versus-dollars unit is inferred from the implied share price for periods after 2022. Schedule 13D/13G filings before 2024 are free text and are not read. The 13D/13G refresh covers the first 15 watchlist companies per click, plus the followed funds' own filings.
- **Data source health is in memory.** The Data sources card counts calls since the server started and forgets
  them on restart. A bad symbol counts as a failed call for every source asked about it, which can make a healthy
  source look worse than it is.
- **The ECB's euro-area inflation series is frozen at December 2025** (the ECB replaced the dataset); the macro
  endpoint still serves it and shows its last date.
- **Congress data is House only, late, and in ranges** (§20). The Senate is not available. Reports come up to 45 days
  after the trade, amounts are bands, spouse and child trades are included, and scanned reports are counted but not
  read. Names are matched as the Clerk prints them, so a member listed under two spellings must be followed under both.
- **Insider history is only as complete as what has been loaded** (§11). It exists for the companies and
  dates you ran the backfill for; the SEC's ticker list has no delisted companies (pass their SEC number
  instead), and the transaction-level price is sometimes missing in the filing itself. A filing that
  reports no transaction rows leaves no trace and is simply re-read (from the local copy) on the next run.
- **The posts source is unofficial and slow for what it measures** (§11, §19). Trump's posts come from a
  third-party archive that can lag, drop posts, change format or stop, and a post moves a stock in minutes while
  the watcher polls every 5 minutes and the scans run 3 times a day. Testing it needs hourly bars (about two
  years exist). Only the newest ~100 posts and the Fed feeds' newest ~15 items per feed can be loaded; older
  history is not.
- **Fed speakers are a hand-kept list** (§11). When the Board changes the list in `fed_feed.py` must be
  edited, or a new member's speeches are stored but never alert. The tone of a speech is not read.
- **Missed-trade results are hypothetical** (§5): entry at the last known daily close, daily bars only,
  today's rules, no cash or position limits, and the quote a plan used is not stored. A small count
  proves nothing; the card says "too early to tell" below 20 resolved trades.
- **The "Check these figures" test (§9) is cautious on purpose.** It only looks at dollar amounts and
  decimal percentages, so a made-up whole-number percentage, a round price like "$550" or a wrong date
  won't be caught. It also can't tell whether a number the AI *did* quote correctly is being used sensibly.
- **The stock list is today's S&P 500 only** (§11). A backtest over past years sees the survivors and
  not the companies that left the index, which flatters results. A scan covers the first 50 rows by
  default and at most 200 symbols.
- **Closing by hand isn't tied to market hours.** Opening is (§7), but a manual close at night fills
  at the last price, which nobody could trade at either.
- **One-off market closures** (a national day of mourning, a storm) must be added to the calendar by
  hand when announced (§8).
- **Exits:** a position closes fully at the stop, TP1 or the time limit (§7). There's no partial exit
  and no trailing stop.
- **The time limit counts bars it can see.** A missing bar in the data delays it by a day, and a
  position whose entry day is older than the 3 months of history the exit check reads is never
  time-limited (its age can't be counted from real bars). A new limit applies to positions that are
  already open.
- **Hourly bars are a finer rule, not a perfect one.** A day touching both levels is settled by that
  day's hourly bars, but two levels inside one hour still count as a stop, and where the hourly bars
  can't be fetched (or are older than about a year) the whole day counts as a stop. The same hourly
  bars check the rest of the entry day; when they never arrive that day stays unchecked (the row says
  "daily_only"). See §7.
- **Best/worst price figures (MFE/MAE) are daily-bar approximations.** The exit day counts only its
  open and the exit price, so the true range can be a little wider; a stop-hit day's real low and a
  target-hit day's real high are never counted (§7). Trades closed before the figures existed, or with
  no price history at the time, show a dash until the backfill is run by hand. The best price *after*
  an exit is unknowable, so "were the targets too near?" cannot be answered from these numbers.
- **Slippage is a flat 5 bps.** It doesn't grow for thin stocks or big orders.
- **Intraday charts (1D, 1W)** only come from Yahoo. No honest free second source exists: Stooq now
  blocks scripted requests, Finnhub's free plan has no candles, and Nasdaq's public chart only gives
  today's per-minute prices (no open/high/low, no volume, no earlier days). When Yahoo is
  rate-limited the chart falls back to the daily 1M view with a notice and a Retry button (§3).
- **The macro calendar is partly 2026-only.** Fed decisions are listed through the end of 2027, but
  CPI and jobs-report dates stop at the end of 2026 because the Bureau of Labor Statistics had not
  published its 2027 schedule when this was last checked (2026-10-01). Past the end of a series the
  macro penalty is simply 0 (and one warning is logged), which means "not tracked", not "calm". The
  dates are typed in by hand; a test starts failing 60 days before any series runs out.
- **The calibration report needs many trades.** With a few dozen closed trades its intervals are still
  wide and it can only say "no clear link". It also looks at all closed trades together, whatever
  market mood or settings were in force when they opened (§5).
- **Strategy versions only see named numbers.** The version fingerprint covers the settings and the
  named constants in the code (§4). A number typed directly inside a function, or a rule changed
  without touching any constant, doesn't change it by itself; a developer bumps a manual "rules
  revision" number for that. Plans from before versioning have no version.
- **The cache file holds recent answers only.** A restart starts warm, but answers past their time limit
  are refetched, and old "last good" values are dropped after 14 days. Multi-year downloads aren't saved
  in the cache file at all (they go in the price history store, §11).
- **The price history store is only as good as its source.** Yahoo's daily prices are adjusted for
  splits and dividends as of the day they're fetched, so the store re-downloads a stock's whole history
  when it notices a shift (§11). If Yahoo is down, other sources' prices are not mixed in, so a stock's
  history can lag until Yahoo is back (or until a forced reload). Only daily prices are stored, and a
  stock with fewer years of history than asked for simply has fewer bars.
- **News and fundamentals history only exists from the day the archive began** (§11). Backtests of
  earlier days have no recorded news or past fundamentals.
- **News card labels are not point-in-time** (§11): they are written when the headline is labelled and the AI may
  know how an older story ended. They are only meant for news from now on, and they change no score yet.
- **PR Newswire collection is shallow and strict** (§11): each feed holds only the newest 20 or so releases, so
  press releases are collected from the day you start; releases that name a company only loosely are skipped on
  purpose. The feeds are a third party's and can change or stop.
- **Yahoo can temporarily block** heavy use.
- **The SEC contact** is still the made-up address, by choice for now. The SEC asks for a
  real contact, so set a separate email (not your personal one) as `SEC_EDGAR_USER_AGENT` before the
  S&P 500 backtest run (§12). Docker passes it in from the `.env` file next to `docker-compose.yml`.
- **The first plan for a stock** can take about 20 seconds on a cold cache (SEC and earnings
  downloads).
- **The backtester tests a smaller strategy than the live one** (§18): by default only the price-based
  part of the score. Fundamentals, insider buying and the earnings surprise record can be switched on
  per run, but only where their facts were stored first. News, options, the earnings-date penalty,
  macro events and the AI always count as 0 in it. Revenue is dated by the SEC filing **day** (public
  from its end) and earnings reports by the report day, so some information appears a little later than
  it really did. Its results are **probably optimistic**: it trades today's winners (§11)
  and it does not see a stop or target touched later on the day a position opens.
- **Lessons are one AI's reading of one trade.** A single small trade proves little, so a lesson can be
  wrong or over-fitted, and the overlay is told to treat it as a note. Lessons are written only for
  recent closes automatically (§9), and there is no setting to switch the automatic job off other than
  choosing no AI provider.
- **Paper trading only:** no real broker.


---

## 15. Running it, and where things are stored

**Locally** (Windows / PowerShell):
- **Backend:** in `backend/`, run
  `.venv/Scripts/python -m uvicorn app.main:app --reload --port 8000`. The API docs are at
  http://localhost:8000/docs.
- **Frontend:** in `frontend/`, run `npm run dev` and open http://localhost:5173.

**Checking a change (for developers):** from the repo root, run
`backend/.venv/Scripts/python scripts/verify.py`. It runs the backend tests, a backend lint, the
frontend type-check and the frontend lint, then prints pass or fail for each with timings. Add
`--build` to also build the frontend. Add `--ui` to also open the real pages in a headless Chrome
on a throwaway copy of the app (its own database, other ports) and take screenshots. It never
touches `backend/runtime/` or the Docker app.

**Docker:** `docker compose up --build -d` from the repo root.
- The backend runs on port 8000.
- The frontend runs on port 5173, served by nginx.
- Each page loads as its own small file, so the login screen and pages without charts download far less (the biggest file is about 230 kB, 74 kB compressed). The next page is fetched in the background while you work. A tab left open across an update shows "The app was updated" with a Reload button.
- Two optional values come from a gitignored `.env` file next to `docker-compose.yml`:
  `CLAUDE_CODE_OAUTH_TOKEN` (the Claude CLI) and `SEC_EDGAR_USER_AGENT` (the SEC contact, §12).
  `backend/.env` isn't copied into the image.

**Where things are stored**

| Folder | What's in it | Survives a Docker rebuild? |
|---|---|---|
| `backend/data/` | The stock list (`sp500.csv`), built into the image | No: it's rebuilt from the repo |
| `backend/runtime/` (in Docker, the `backend_runtime` volume) | The cache file `cache.db` (§11; safe to delete), the price history file `history.db` (§11; deleting it means downloading again), the database (`strategeia.db`: plans, positions (with each closed trade's AI lesson), equity points, account, the queue of plans waiting to be redone at the market open, the dated facts table `knownfact` from §11, which holds the saved news items (including PR Newswire press releases), the AI labels on them (news cards), fundamentals snapshots, annual revenue from SEC filings, past earnings reports and FINRA short-volume days (the downloaded FINRA files themselves sit in the `finra` folder beside it), and the insider trades from SEC Form 4 filings and the 8-K announcements (the downloaded filings sit in the `sec_archive` folder beside it; safe to delete), the strategy-version table `strategyversion` from §4, the three backtest result tables (plus `backtestvalidation`, one row per walk-forward validation) `backtestrun` (which also holds the random-entry baseline's numbers), `backtesttrade` and `backtestequitypoint` from §18, and the missed-trade results `missedtradeoutcome` from §5, the saved state of each watcher `watcherstate` from §19, which can be rebuilt from the plans and the price history), your price alerts `pricealert` and the "already sent today" records `notificationlog` from §23, `settings.json`, your watchlist `universe.json` (§11; delete it to go back to the bundled list), and the generated password, API key and session secret | **Yes** |

---

## 16. API endpoints

Every route except login, auth status and health needs you logged in (or an API key).

| Method and path | What it does |
|---|---|
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/status` | Log in, log out, check the session |
| `GET /api/health` | "Is the backend up?" |
| `GET /api/dashboard/summary` | Everything the Dashboard shows |
| `GET /api/scan`, `GET /api/universe` | Scan results; the stock list in use (your watchlist, the test filter's symbols or the bundled list) |
| `GET /api/watchlist`, `PUT /api/watchlist`, `DELETE /api/watchlist`, `POST /api/watchlist/validate` | The Watchlist card (§11). GET: the list in use, which layer decides it (`dev_filter`, `custom` or `bundled`), the list the editor shows, the test filter's value, the size limits, when it was saved, and how many symbols a scan covers. PUT: save an ordered list (1 to 200 symbols, otherwise a 422 with the reason). DELETE: back to the bundled list. POST validate: does this symbol return a quote (saves nothing) |
| `POST /api/committee/runs` | Start one committee run for a symbol in the background (202). One run at a time (409 otherwise); 400 when no real AI provider is set |
| `GET /api/committee/runs?symbol=&limit=` | Saved committee runs, newest first. Read-only |
| `GET /api/committee/runs/{id}` | One run with every report written so far. Read-only; the page polls this for the live view |
| `GET /api/library/{symbol}?kind=&q=&since=&limit=` | One ticker's dated history (news, fundamentals, filings, insider and Congress trades, watcher events, lessons) with counts per kind; `kind` can repeat. Read-only |
| `GET /api/valuation/{symbol}?growth=&margin=&discount=&terminal=&years=&explain=` | The discounted earnings estimate (editable assumptions, sensitivity grid) and peer P/E and P/S multiples; `explain=true` adds a short paragraph. `available: false` and a reason for crypto or too little data. Read-only, informational |
| `GET /api/options/{symbol}?expiration=` | The options chain for one expiration (default: nearest) with the summary figures; `available: false` and a reason for crypto or symbols with no options. Read-only |
| `GET /api/screener/fields`, `POST /api/screener/run` | The screener's field list, and a run (rules, sort, row limit, how many symbols to read; read-only) |
| `GET/POST /api/screener/saved`, `DELETE /api/screener/saved/{id}` | Saved screens |
| `GET /api/scan/presets`, `GET /api/scan/presets/{name}?limit=` | The screen presets, and one preset run over the first N symbols of your list (default 25, at most 100): matches, not-judged symbols, the rules that could not be answered |
| `GET /api/portfolio/positions/{id}/thesis` | An open position's thesis (read only) |
| `POST /api/portfolio/positions/{id}/thesis/recheck`, `.../thesis/review` | Re-check the pillars now; ask the AI for a short review (rate limited) |
| `POST .../thesis/notes`, `POST .../thesis/items`, `PUT` and `DELETE .../thesis/items/{item_id}` | Add a note; add, edit or remove a pillar, risk or catalyst |
| `POST /api/scan/auto-trade` | Runs the evaluation loop now (60-second cooldown) |
| `GET /api/analysis/{symbol}?range=` | Chart data and indicators |
| `GET /api/research/{symbol}` | The research panel data. Also saves the news and fundamentals it just fetched into the archive (§11) |
| `GET /api/archive/{symbol}?limit=` | What the news and fundamentals archive holds for a stock: counts, when saving began, the most recent items (with the time each became public) and the archive's overall size. Read-only |
| `GET /api/news/{symbol}` | The stock's saved headlines (PR Newswire releases included) with their AI label when one exists, the News cards switch, whether an AI is usable, and how many are labelled and unlabelled. Read-only |
| `POST /api/news/collect` | Reads PR Newswire's public feeds once and saves releases that match the watchlist (or the symbols in the body). 60-second cooldown |
| `POST /api/news/label/{symbol}?limit=` | Has the AI label the stock's unlabelled headlines (400 while News cards is off; 20-second cooldown per symbol; at most 3 AI calls) |
| `POST /api/trade-plans/generate`, `GET /api/trade-plans`, `GET /api/trade-plans/{id}` | Make a plan, list plans, get one plan. Each carries its `strategy_version` (empty for plans from before versioning) |
| `GET /api/strategy/versions` | Every strategy version (§4), newest first: its number, date, the full settings and rules snapshot, what changed from the version before, and how many plans, no-trade decisions, opened positions and closed trades were made under it. Also the number of plans with no version, and which version the current settings map to (empty until a plan is generated under them). Read-only: it never creates a version |
| `GET /api/portfolio/positions`, `POST /api/portfolio/positions` | List positions (each shows its plan's `strategy_version`, and its best and worst price as `mfe_pct`, `mae_pct`, `mfe_r`, `mae_r`: stored for a closed position, live for an open one; a closed one also carries `exit_resolution` and `entry_day_check`, §7); open a position from a plan (refused with a 400 while the market is closed, or while the plan waits for its redo) |
| `POST /api/portfolio/positions/{id}/close` | Close at the market |
| `POST /api/portfolio/positions/{id}/lesson` | Write (or rewrite) the AI lesson for one closed position (§9). `404` unknown position, `400` if it is still open or no AI provider is set up, `409` if a lesson is already being written for it, `429` for a repeat within 20 seconds. If the AI fails this is still a `200`: the position comes back with `lesson_error` set and any earlier lesson kept. Positions carry `lesson_text`, `lesson_provider`, `lesson_model`, `lesson_at` and `lesson_error` |
| `GET /api/portfolio/stats`, `GET /api/portfolio/equity-curve` | Statistics (including the `excursions` block, §7); the equity curve |
| `GET /api/portfolio/calibration` | The confidence report card (§5): win rate and average R per confidence band, the information coefficient overall, by direction and per score part, each with its trade count and intervals, plus how many closed trades were left out. Read-only; it runs no exit check |
| `POST /api/portfolio/reset` | Reset the paper account |
| `GET /api/settings`, `PUT /api/settings`, `GET /api/settings/status`, `POST /api/settings/test-connection` | Read or save settings, check status, test a provider. The test accepts an optional `tier` (`routine`, the default, or `decision`) to test the decision model, and optional `overrides` (unsaved form values: provider, models, keys, Telegram token and chat ID; unknown names are rejected, masked keys mean "unchanged") |
| `GET /api/cache/status`, `POST /api/cache/clear` | The Data cache card (§11): entries in memory and on disk, how often answers were reused since the app started, the price history store's symbols, bar count, date range and size (read-only, writes nothing); and empty the cache, memory and file (30-second cooldown; the price history store is not touched) |
| `GET /api/data-sources`, `POST /api/data-sources/{name}/probe` | The Data sources card (§11): every source with calls, success rate, latency, last success and last error (read-only, never calls a source), and one real probe request per source (10-second wait per source; `404` unknown source, `400` no probe for it, `429` too soon) |
| `GET /api/macro/series`, `GET /api/macro/series/{id}?start=&end=` | The FRED and ECB series on offer, and one series' points (§11); `404` unknown id, `502` when the source has nothing |
| `GET /api/research/{symbol}/earnings-preview` | The earnings preview (§22): facts from our own data plus an optional AI paragraph. Read-only; cached 15 minutes. |
| `GET /api/calendar?from=&to=&symbols=` | The merged calendar (§22): economic releases, Fed / CPI / jobs dates, earnings, position flags, per-source status and the date check. Range up to 62 days (default 14 from today). Read-only. |
| `GET /api/market/session` | The US market right now: its state (open, pre-market, after hours, closed, holiday), the next open and close (1:00 pm early closes included), and the holiday's name. The Dashboard badge and the Execute button read it. |
| `GET /api/backtests/{id}/metrics`, `.../benchmarks`, `.../baseline`, `.../scorecard`, `GET /api/backtests/history-coverage?symbols=A,B` | Read-only statistics for a finished or partly finished run, worked out from its stored rows (§18); `409` while a run has no results yet; the scorecard takes its criteria as query parameters. History-coverage lists what price history is stored for the symbols and for SPY and ^VIX, and the exact preload command when something is missing |
| `GET /api/smart-money/congress/status`, `.../trades`, `.../clusters`, `.../members`, `.../member-names?q=`, `POST /api/smart-money/congress/refresh` | The Congress tab and the "who to follow" search (section 20). Trades take `days` (1 to 365), `symbol`, `side` (`buys`, `sells`, `all`), `member` and `followed_only`. Every amount is a range plus a labelled midpoint. Status counts reports that could not be read. All GETs only read. The POST reads up to 15 new reports from the House Clerk per call and answers `429` if used in the last 60 seconds |
| `GET /api/smart-money/insiders`, `.../insiders/clusters`, `.../insiders/summary/{symbol}`, `GET /api/smart-money/status`, `POST /api/smart-money/insiders/refresh` | The Smart Money page (§20). The first two take `days` (1 to 365, default 90), `symbol`, and for trades `min_value` and `side` (`buys`, `sells`, `all`); the summary gives one symbol's 90-day numbers and what the score would do with them. Status says what is stored (`has_data` is false when nothing was loaded). All GETs only read. The POST loads Form 4 filings from SEC for a few watchlist symbols per call (`symbols_remaining` says how many are left) and answers `429` if used in the last 60 seconds |
| `GET /api/smart-money/funds`, `.../funds/{cik}/changes`, `.../funds/holders/{symbol}`, `GET /api/smart-money/ownership`, `POST /api/smart-money/funds/refresh` | The Funds and 5% owners tabs (§20). The overview lists the followed funds with their latest stored quarter; changes compares a fund's two newest quarters (`status` filters to `new`, `added`, `trimmed`, `sold_out` or `unchanged`; unchanged rows show only when asked for); holders says which followed funds hold a symbol; ownership lists 13D/13G filings (`days` 1 to 365, `schedule` `all`, `13D` or `13G`, `symbol`). GETs only read. The POST loads 13F and 13D/13G filings from SEC EDGAR and answers `429` if used in the last 60 seconds |
| `GET /api/terminal/heatmap`, `GET /api/terminal/macro`, `GET /api/terminal/recap` | The Market Terminal page (§21). Heatmap takes `window` (`1d`, `5d`, `1m`) and `limit` (1 to 200 symbols, default 60). Recap takes `ai` (default false) and `limit`. All three only read, are cached for 5 minutes, and carry an `as_of` time |
| `POST /api/backtests/validate`, `GET /api/backtests/validation-options`, `GET /api/backtests/validations`, `GET /api/backtests/validations/{id}`, `POST /api/backtests/validations/{id}/cancel` | Walk-forward validation (§18). POST takes the symbols, dates, `folds`, `mode` (`rolling` or `anchored`), `train_ratio`, `embargo_days`, a `grid` of settings to try and fixed `overrides`; it answers `202` with an id (`409` while any backtest or validation is active, `400` when the folds do not fit the period or history is missing, `422` for an invalid body). The options endpoint lists the settings a grid may vary with your live value for each, and the limits. GET one returns the folds, the stitched out-of-sample curve, the deflated Sharpe block and the scorecard |
| `POST /api/backtests`, `GET /api/backtests`, `GET /api/backtests/{id}`, `GET /api/backtests/{id}/trades`, `GET /api/backtests/{id}/equity`, `POST /api/backtests/{id}/cancel` | The backtester (§18). The POST body also takes `include_fundamentals`, `include_insiders` and `include_earnings` (default false: the dated parts of §18), `run_baseline` (default true) and `baseline_runs` (default 20, 0 to 50); a run's progress says whether the main run or which baseline seed is going. POST starts a run in the background and answers `202` with its id at once (`409` while another run is active, `429` if one was started in the last 5 seconds, `400` for a request that can't work, such as missing price history, `422` for an invalid body); GET list and GET one give the status, progress, settings used, coverage and summary; trades and equity give the stored results; cancel stops a run at its next simulated day and keeps what it did so far |
| `GET /api/watchers`, `GET /api/watchers/events?limit=`, `PUT /api/watchers/{name}`, `POST /api/watchers/{name}/run` | Watchers (§19). The first two only read. PUT turns one watcher on or off. POST polls one watcher now (`429` for a repeat within 30 seconds, `404` for an unknown name) |
| `POST /api/replay` | The What if? card (Trade Plans page, §18). Body: `{"overrides": {...}}` with one or more replayable settings (`min_confidence_for_trade`, `ai_overlay_objection_action`, `ai_overlay_scores_confidence`, `allowed_directions`). Re-decides every stored plan (newest 5000) and returns before and after trades taken, win rate and average R with their 95% intervals, the list of decisions that flip (newest 300) with the rules version of each, and the caveats. Sizing, cap, slippage, commission and exit settings are refused with `422` and the reason. Read-only: nothing is written or fetched |
| `GET /api/missed-trades`, `POST /api/missed-trades/refresh` | The Missed trades card (§5). GET: per kind of missed trade (AI veto, under the confidence bar, held by the AI, written but never filled, no trend, other) the number of plans, resolved and open hypothetical trades, win rate and average R with their 95% intervals, total R; the closed trades for comparison; the two headline questions with their verdicts; every declined plan (newest 300); and the caveats. Read-only: it never computes, fetches prices or writes. POST: compute the results that are new or still open (at most 200 per call; `429` for a repeat within 30 seconds; results already final are never recomputed) |
| `POST /api/signals/finra/refresh`, `GET /api/signals/finra/{symbol}` | FINRA short volume (§5 "Silent signals"). POST downloads the last days for the given symbols (default: the watchlist) and stores them; days already stored are not downloaded again (`429` for a repeat within 60 seconds). GET shows what is stored for one symbol (recent days, recent ratio, baseline) and what the silent signal reads for an optional `?direction=long|short`. Read-only: it never downloads or writes. Plans carry a `shadow_signals` list |
| `GET /api/alerts?status=`, `POST /api/alerts`, `DELETE /api/alerts/{id}` | Price alerts (§23). GET lists them newest first with the active count and the cap of 100. POST saves one (`symbol`, `condition`, `threshold`, `unit` for stop and target alerts, `repeat`, `cooldown_minutes`, `note`; a `422` with the reason for a bad symbol, a threshold at or below 0, a stop or target alert with no open position, or a 101st active alert). DELETE cancels an active alert, or removes one that already triggered or was cancelled (`404` unknown id). Alerts only ever send a message |
| `POST /api/notes/morning/preview`, `POST /api/notes/weekly/preview` | The morning note or weekly digest as it would be sent now (§23): the text, whether an AI paragraph was added, which parts were unavailable and how many Telegram messages it takes. `?ai=true` adds the AI paragraph (one model call). Sends and stores nothing |
| `POST /api/notes/morning/send`, `POST /api/notes/weekly/send` | Send the note to Telegram now, on any day (`409` when Telegram is not configured, `429` for a repeat within 60 seconds; the two notes have separate waits) |
| `GET /api/sleeves`, `POST /api/sleeves`, `PATCH /api/sleeves/{key}`, `DELETE /api/sleeves/{key}` | Sleeves (§24). GET lists every sleeve with its live statistics (read-only). POST creates one (`name`, `style`, `starting_cash`; `409` for a duplicate name, `400` past 12 sleeves). PATCH renames, relabels, adds notes or enables and disables (`400` for disabling core). DELETE only works for a sleeve that never had a position or plan (`409` otherwise) |
| `?sleeve=key` on `GET /api/portfolio/positions`, `/stats`, `/equity-curve`, `GET /api/dashboard/summary`, `POST /api/portfolio/reset`; `sleeve` in the body of `POST /api/trade-plans/generate` | Pick the sleeve (§24); omitted means core. `all` works for positions and stats, and for reset together with `confirm=true`; the equity curve refuses it with `400`. An unknown key is `404` |
| `POST /api/risk/calculate` | Position-size calculator (only reachable from the API docs) |

---

## 17. Glossary

| Term | Meaning |
|---|---|
| **Paper trading** | Simulated trades with fake money at real prices |
| **Session** | The hours a market trades. US stocks: 09:30–16:00 New York time, Monday to Friday, except US market holidays |
| **Early close** | A short session ending at 1:00 pm New York time: the day after Thanksgiving, and 3 July or 24 December when they fall Monday to Thursday |
| **Market-open redo** | A fresh evaluation, at 09:45 New York time, of a stock whose plan was made while the market was closed. Only the redo's plan can execute (§8). |
| **Long / short** | Betting the price goes up / goes down |
| **EMA20 / EMA50** | Average price over 20 / 50 days, weighted towards recent days. Used for the trend. |
| **RSI** | A 0–100 momentum gauge. Above 75 or below 25 means stretched. |
| **ATR** | Average True Range: how much a stock normally moves in a day |
| **Support / resistance** | Price levels where the stock turned before |
| **Stop (SL)** | The price where a losing trade is closed |
| **TP1 / TP2** | First / second target price |
| **R** | The amount one trade risks (entry to stop). +2R means it made twice what it risked. |
| **R:R** | Reward-to-risk ratio of a plan |
| **Avg R** | The average result per closed trade, in R (also called expectancy) |
| **MFE / MAE** | Maximum favourable / adverse excursion: the best and worst price a trade reached while it was open, as % of entry and in R (§7). Win rate can't show whether stops were too tight; these can. |
| **Watcher** | A small background poller that checks one source and reports events (§19) |
| **Watcher event** | One thing a watcher found: a symbol (or none), a headline, a link and the time it became public. Always saved first (§19) |
| **Missed trade** | A plan the app did not take: declined, vetoed by the AI, held back or refused by the engine. The Missed trades card replays each one on past prices to see what it would have earned (§5). |
| **Resolved / open (hypothetical trade)** | Resolved: the replay reached a stop, TP1 or the time limit, so the result is final. Open: it has not, so it is marked at the latest close and left out of the statistics. |
| **Lesson** | A 2 to 4 sentence AI note written after a paper trade closes: right or wrong against SPY, which reasons held, one takeaway. Stored with the trade; never templated; fed back to the AI Trading Overlay as notes (§9). |
| **Calibration** | Checking whether the confidence score means what it appears to: do higher-scoring plans really win more or earn more per unit of risk? The Portfolio card does it from closed trades (§5). |
| **IC (information coefficient)** | A number from -1 to +1 saying whether higher scores tend to come with better results (the Spearman rank correlation between a score and the trade's R). 0 means no link. Always read it with its trade count and interval (§5). |
| **Wilson interval** | A 95% range for a win rate that stays honest for small samples: 3 wins in 5 trades could really be anything from about 23% to 88% |
| **p-value** | How often pure luck would produce a result at least this strong. Small (under 0.05) means luck is an unlikely explanation. |
| **Slippage / bps** | The small cost of filling at a worse price. 1 bps = 0.01%. |
| **Exit resolution** | How a closed trade's exit was placed in time: on a daily bar, on an hourly bar, or by the cautious stop-first rule when the order could not be known (§7). |
| **Hourly bars** | Price bars one hour long. They show which of two levels was touched first inside a day, and what happened later on a position's first day (§7). |
| **Time limit** | The maximum number of trading days a position may stay open. After it, the position closes at that day's close (§7). |
| **VIX** | The market's "fear gauge" |
| **SPY** | The fund that tracks the S&P 500, used as "the market" |
| **Discounted earnings estimate** | A rough value today of a company's projected future profits |
| **Put/call ratio** | Bets on a fall vs bets on a rise in the options market |
| **Implied move** | The size of move the options market expects |
| **Silent signal** | A new signal that is recorded on every plan with the points it would have added, but is not scored until a backtest shows it beats luck (§5) |
| **Short volume** | The share of a day's trading volume that was a short sale. Not the same as short interest, the number of shares currently sold short (§11) |
| **8-K** | The SEC form a company files within four business days of a material event (results, a major contract, an officer leaving, a delisting notice). It lists numbered item codes (§11) |
| **Form 4** | The SEC filing an insider must submit within 2 business days of trading their company's stock |
| **Acceptance time** | The second the SEC accepted a filing. The app treats a Form 4 as public from then, not from the date of the trade |
| **10b5-1 plan** | A trading plan an insider sets up in advance, so later sales follow a schedule rather than a fresh decision. Form 4 rows made under one are flagged |
| **Drawdown** | The biggest fall from a peak |
| **Strategy version** | A number (v1, v2, ...) naming one exact combination of the decision-relevant settings and the rules in the code. Every plan records the one it was made under, so results before and after a change can be told apart (§4). |
| **Known at** | The moment a piece of information became public. A backtest may only use facts known before its simulated moment (§11). |
| **News card** | An AI's label for one saved headline: event type, sentiment, materiality, companies mentioned, a one-line summary and whether it is about this company. Not point-in-time (§11). |
| **Archive** | The app's own dated record of the news and fundamentals it has loaded. It is the only source of past news a backtest can use (§11). |
| **Sharpe / Sortino / Calmar** | Return per unit of risk: risk as day-to-day jumpiness, as down days only, and as the worst fall from a peak (§18) |
| **Beta / alpha** | How much a strategy moves with SPY, and the return left over after that (§18) |
| **Random-entry baseline** | The same run repeated with entries picked by chance, to see whether the real entries beat luck (§18) |
| **Deflated Sharpe ratio** | A Sharpe ratio corrected for how many things were tried and for fat tails, as the chance it beats what luck alone would give (§18). |
| **Walk-forward** | Testing on days that come after the days the settings were chosen on, fold by fold (§18). |
| **Backtest** | A replay of the app's own rules over past days, to see what they would have done (§18). Not a promise of future results. |
| **Look-ahead** | A backtest mistake: using information that wasn't public yet at the simulated moment, which makes results look better than they could have been |
| **Bollinger bands, MACD, VWAP** | Common chart indicators. VWAP is only shown on intraday ranges. |

---

## 18. Backtesting

A **backtest** replays the app's own rules over past days to see what they would have done. It is a
"time machine" built on the same code that makes live decisions, not a separate copy of the rules:

- the **decision** (the score, direction, entry, stop, targets and size) is made by the same function
  that makes a live trade plan, and
- the **paper-trading engine** (§7) opens, marks and closes the positions, with all its rules: the
  1% risk size, slippage, the position and sector caps, the stop checked before the target, gap fills
  and the time limit.

Only the world underneath is swapped: the **data** (stored price history, cut off at the simulated
moment), the **clock** (a simulated one), the **database** (a throwaway one in memory, discarded when
the run ends) and the **AI** (switched off). A run never reads or changes your saved settings, never
touches your real plans and positions, never sends a Telegram message and never saves news to the archive.

### What one simulated day looks like
The same convention as the live market-open pass (§8):

| When (New York time) | What happens |
|---|---|
| **09:45** | Every stock without an open position gets a full evaluation. It knows the prices **through the previous close** and the **opening price** of this day. A trade, if there is one, fills at that open plus slippage. Position slots work like the live auto-scan: once the cap is reached, stocks are still evaluated but can't open. |
| **16:30** (13:30 on an early-close day) | The day's bar is final. The engine's exit check runs against the bars through today, and the day's **equity** is recorded, with every open position valued at **today's close**. |

Weekends and US holidays are skipped, and early closes are respected (§8). A stock needs about a year
of earlier prices (252 daily bars) before it is evaluated; a newer listing joins the run when it gets there.

### What the backtest can see at each moment
It can't see the future by construction: the data provider answers only from the simulated moment and
refuses (raises an error) if asked for anything later or if there is no simulated moment.

- **Daily bars:** only bars that are final. At 09:45 that is everything up to yesterday.
- **Weekly bars:** built from those same daily bars. The week still in progress is a **partial** bar made
  from the days already known, like a live chart in mid-week.
- **The price:** this day's **open** at 09:45; this day's close at 16:30. The day's **volume** is 0 at 09:45
  (it has barely started), so the "volume spike" point can't be earned. The 20-day average volume comes
  from known bars.
- **SPY and the VIX** follow the same rules.

### What is in the score, and what isn't (the "price-only core")
Only the parts built from prices can be rebuilt for past days:

| Part | In the backtest |
|---|---|
| Daily chart (trend, momentum, near a key level, RSI) | **Yes**, up to 5 of its 6 points (not the volume point) |
| Weekly chart and SPY agreeing | **Yes**, up to 2 points |
| VIX regime | **Yes**, a penalty only |
| Fundamentals (revenue growth, nearness to the 52-week high or low) | **Only if switched on for the run**, up to 2 points (see below); otherwise 0 |
| Insider buying | **Only if switched on**, up to 1 point; otherwise 0 |
| Earnings surprise record | **Only if switched on**, up to 1 point; otherwise 0 |
| News, options | **No**: 0 |
| Expected-move and macro-event penalties, the earnings-date penalty, the AI overlay | **No**: 0 |

With nothing switched on, a backtested plan can earn at most **7 of the 16 points** (44%), not 16. The **confidence bar is not
changed**: the live bar of 30% still means 5 points, which here is 5 of the 7 that can be earned, a
tougher bar than live. Every run stores this explanation (its **coverage**) next to its results: the
bar as a percentage and as points of the achievable maximum. A run may override the bar (see below);
the override is recorded beside the live value. The coverage also lists which dated parts the run
switched on (next section), and the reachable points grow by what those parts can really add.

### Dated company data (switched on per run)
Three parts of the score can be rebuilt for a past day, because their data comes with a date. A run
switches each on with `include_fundamentals`, `include_insiders` and `include_earnings` (all off by
default; the Backtest Lab form has three checkboxes). They read **facts stored beforehand** (§11); a run
never downloads anything.

| Part | Where the data comes from | When it counts as public |
|---|---|---|
| **Fundamentals** | Revenue by year from the company's 10-K filings (SEC's XBRL data), and the 52-week high and low worked out from the prices known at that moment | A year's revenue from the end of the day its 10-K was filed (the data gives the filing day, not the time, so the later reading is used) |
| **Insider buying** | SEC Form 4 filings (§11) | The moment SEC accepted the filing |
| **Earnings surprise record** | Past reported quarters (estimate, actual, surprise) from yfinance | The end of the report day (the source does not say whether a company reported before the open or after the close) |

How the dates are handled:
- **A restated year shows its old number until the restating filing is public.** Each 10-K reports the
  last three years, so one year can appear in several filings. At any moment the app uses the **latest
  filing public by then** for each year. Years are lined up by their end date; only the newest unbroken run
  of years is used, and a history whose newest year is more than two years old counts as missing.
- **The 52-week range** uses only bars already final at the moment, over the last 365 days, and is
  refused when fewer than 200 bars fall inside it. Market cap, P/E and revenue over the last twelve
  months stay empty.
- **Fundamentals** can earn at most **2 points** here (revenue growth +1, near the 52-week high +1; near
  the low is the opposite case, not an extra point). Their third live point is the earnings-date
  penalty, which is not rebuilt.
- **The next earnings date is never used.** Companies announce it weeks ahead, but the data has no
  announcement time, and a backtest may only use a date it can show was already public. So the
  "earnings within 3 days" penalty is missing from a backtest, which therefore trades into reports a
  little more freely than the live app does.
- **A symbol with nothing stored scores 0 for that part.** A finished run's summary has a `dated_data`
  block per switched-on part: how many lookups were made, how many found data, and for how many symbols.
  Check it: a part that was switched on but never downloaded looks like "no signal", not like an error.
- With everything on, a plan can earn up to **11 of the 16 points** (69%), and the live bar of 5 points
  is then 5 of 11.

Filling the facts in (each is safe to repeat; run it from the repository folder):
- `backend/.venv/Scripts/python scripts/backfill_fundamentals.py AAPL MSFT` downloads annual revenue from
  SEC (about three requests per symbol, at most 5 per second, identified by `SEC_EDGAR_USER_AGENT`).
- `backend/.venv/Scripts/python scripts/backfill_insider_trades.py AAPL MSFT --since 2016-01-01` (§11).
- `backend/.venv/Scripts/python scripts/backfill_earnings.py AAPL MSFT` downloads past earnings reports
  from yfinance, a few symbols at a time to stay inside Yahoo's limits.
All three accept `--db FILE` (a scratch database) and `--report` (show what is stored, no network).

### Starting a run
`POST /api/backtests` (§16) takes the **symbols** (their prices must already be stored: run
`scripts/preload_history.py` for them and for `--benchmarks`, SPY and ^VIX, first; a run never
downloads anything), a **start** and **end** date, `decision_every_n_days` (default 1) and optional
**overrides** of these settings only: `slippage_bps`, `commission_per_trade`, `default_risk_pct`,
`min_confidence_for_trade`, `max_concurrent_positions`, `max_positions_per_sector`,
`max_position_pct_of_adv`, `max_holding_days`, `paper_starting_cash`. Everything else follows your
saved settings, except the fixed choices: no AI, no Telegram, no Finnhub, auto-execute on. Crypto
symbols are not supported yet.

One run goes at a time, in the background; you can poll its progress and cancel it. A run is not
resumable: if the app stops while one is running, it is marked **failed** ("interrupted") the next time
the app starts. Each run stores its exact settings and a strategy fingerprint (the same fingerprint the
versions in §4 use) so it can be repeated and compared.

### What a run keeps
Three small tables in the main database (§15): the run (status, settings used, coverage, summary), its
trades (entry and exit dates and prices, shares, profit and R, the exit reason, best/worst price, and the
score points and parts that let the trade in) and its daily equity points. The summary has the trade
count, win rate, total return, average R, final equity, biggest fall from a peak, days simulated, stocks
with data, and stocks skipped (and why). A position still open when the run ends is listed as "open at
end" and is left out of the win rate and the average R. The statistics below are worked out again from
these rows every time they are asked for, so they can never disagree with them; only the random-entry
baseline's per-run numbers (and the run's own columns for its progress and phase) are stored on top.

### The statistics (`GET /api/backtests/{id}/metrics`)
Every figure comes from the run's daily equity points and its closed trades. Conventions: daily returns,
a risk-free rate of 0, a 252-trading-day year, and drawdown measured on daily closes (an intraday dip is
not seen). A run shorter than about a month is not annualised, and says so. Anything that cannot be worked
out (no trades, no losing day) is shown as a dash, never as a made-up zero.

| Figure | Plain meaning |
|---|---|
| **Total return** | Final equity compared with the starting cash |
| **CAGR** | The steady yearly growth rate that would give the same total over the same years |
| **Volatility** | How much the daily returns jump around, scaled to a year |
| **Sharpe** | Average daily return divided by its jumpiness, scaled to a year. Higher is a smoother ride per unit of return |
| **Sortino** | Like Sharpe, but only the down days count as risk |
| **Max drawdown** | The biggest fall from a high point to a later low, with the days it lasted, the dates, and whether it recovered |
| **Calmar** | CAGR divided by max drawdown |
| **Profit factor** | Money won on winning trades divided by money lost on losing ones (a dash when nothing was lost) |
| **Win rate** | Share of closed trades that made money, with a 95% range (Wilson interval, the same one the confidence report uses) |
| **Average R** | Mean profit per trade in units of the money risked, with a 95% range (bootstrap: the trades are resampled many times) |
| **Average win, average loss, payoff** | The mean winning and losing trade in dollars, and their ratio |
| **Trades per year, average holding days** | How busy the strategy is, and how long a position lasts |
| **Exposure** | Share of days with at least one position open |
| **Longest losing streak** | Most closed trades in a row that did not make money |
| **Year and month tables** | Equity change over each calendar year and month. A year or month the run only partly covers is marked |
| **How trades ended, long against short** | Count, share, average R and profit for stop, target, time limit and still open; and the same for each direction |

### Benchmarks (`GET /api/backtests/{id}/benchmarks`)
- **SPY buy-and-hold:** the same starting cash put into SPY at the close before the first simulated day
  and held to the last, over exactly the days the run simulated, read from the stored history. No costs.
- **Compared with SPY:** excess return, **beta** (how much the strategy moves when SPY moves), **alpha** (the
  part of the return beta does not explain, per year), each with the number of days and a t-statistic,
  the **correlation**, and the share of days the strategy beat SPY. A beta near 0 means a different kind
  of return from the market's; a t-statistic under about 2 means the figure is not clearly different from 0.
- **Equal-weight basket:** the run's symbols bought in equal parts on day one and held. It is labelled
  **survivor-biased**: it only holds today's names.
If SPY history is gone the benchmark lines say so and the rest of the page still works.

### The random-entry baseline (`GET /api/backtests/{id}/baseline`)
A strategy can make money simply because the market went up. To test that, the app repeats the run **K
times** (default 20, 0 to 50; set `baseline_runs`, or `run_baseline: false`, in the POST body) with the
entries chosen by chance. Everything else is the same as the real run: symbols, days, settings, the
way stops, targets and size are worked out (the very same functions the live plans use), the position caps
and the exit rules. Random runs may use only data known at that moment, like the real one.
- Each day and symbol gets an entry with the probability the real run had (its trade plans per evaluated
  stock-day), long or short with equal odds. Every choice depends only on the seed, the symbol and the
  day, so a seed always gives the same run.
- The page shows where the real run falls among the random ones for total return, average R and Sharpe:
  the **percentile** (the share of random runs it beat; a tie counts half), and the **chance luck does
  this well** ((1 + random runs at least as good) / (K + 1)). With K = 10 that can never go below 9%.
- It is a sanity check, not a significance test; the page repeats the small-K caveat, and the scorecard
  line reads "not enough data" below 10 random runs. Random shorts lose in a rising market, which makes
  the baseline easier to beat than a long-only one.
The random runs run after the main run. The main results are readable the moment it finishes; if a cancel
or an error stops the baseline, the finished random runs are kept and the run still counts as done.

### The scorecard (`GET /api/backtests/{id}/scorecard`)
A checklist, **not a verdict**: nothing in the app acts on it. Each line shows the criterion, the actual
value and **pass**, **fail** or **not enough data**. The defaults (change them in the request: `min_trades`,
`max_drawdown_pct`, `year_share`, `baseline_percentile`): at least 200 closed trades; profitable after
costs (not judged when the run charged no costs); positive in most full calendar years (needs two); max
drawdown under 20% (a yardstick only, no cap is applied anywhere); beats SPY on return and on Sharpe; at or
above the 75th percentile of the random runs. Standing banners are shown for every run: probably optimistic
(today's index members only), price-only core (how many of the 16 points the run could earn, from its
coverage), how the trade count compares with the sample size wanted, and that daily bars flatter fills.

### How to read the Lab
Start with the banners and the trade count: under about 200 trades the ranges on win rate and average
R are wide. Then compare with SPY: a strategy that lags buy-and-hold with a low beta may still be worth
having for its smoother ride, but it is not beating the market. Then the baseline: a percentile near 50
means the entries added nothing the dice did not. Finally look at the drawdown and the years, not only
the total.

### How fast
About 12 to 15 milliseconds per stock-day on a typical PC, with all the real scoring and engine work.
Two stocks over nine years (about 2,200 days) took 25 seconds. Sixty stocks over ten years should take
roughly half an hour, and the full S&P 500 several hours (an estimate from that measurement).

### Why the results are probably optimistic
- **Today's winners.** The stock list is today's big companies; those that fell out of the index aren't in
  it (§11, §14). Results on NVDA and AAPL especially say little about the strategy.
- **The entry day isn't fully watched.** The engine skips a position's first bar (its range before the
  entry must not stop it out), and the backtest has no hourly bars, so a stop or target touched later
  on the day a position opens is only seen from the next day's bar.
- **Gap-ups are skipped.** If a stock opens more than 2% away from the price the plan was made at, the
  engine refuses the trade as stale, exactly as live.
- **Adjusted prices.** The stored prices are adjusted for splits and dividends, so old prices differ
  from what was quoted then; percentages and R are not affected, dollar figures are.
- **Price-only.** The 3 missing news and options points, the insiders, and the AI can't be tested this
  way; they are measured going forward instead (§5).

### Walk-forward validation and the deflated Sharpe ratio (the Validation tab)
A single backtest judges a strategy on the same days its settings were chosen on, which flatters it.
**Walk-forward validation** checks that honestly. The period is cut into **folds** (2 to 12). Each fold
has a **train** window and a later **test** window, with an **embargo** (a gap of trading days, default
5) between them. Test windows follow each other and never overlap. The train window is either
**rolling** (a fixed length that slides forward) or **anchored** (it always starts at the first day).
The train window is a chosen multiple (default 2) of one test window, and each window needs at least
20 trading days.

Optionally you give a small **grid** of settings to try: the confidence bar, the risk per trade, the
holding limit and slippage (up to 5 values each, 16 combinations, 100 runs in all). In every fold each
combination ("variant") is run on the train days, the one with the best **in-sample Sharpe** is
**selected**, and only that one is run on the test days it has never seen. Without a grid there is a
single variant and nothing is selected. Every window is a separate run of the same backtester on a
throwaway database, starting with the full starting cash and no open positions, and the data provider
shows each simulated day only what was known then. Because the windows are independent, nothing carries
over from a train window into a test window; the embargo is an extra safety gap.

What you get (`GET /api/backtests/validations/{id}`):

| Part | Meaning |
|---|---|
| **Fold table** | For each fold: the train and test dates, the selected settings, and return, Sharpe and trade count in-sample against out-of-sample. A big gap between the two sides is the sign of tuning to noise |
| **Out-of-sample equity** | The test windows only, joined end to end (each continues from where the last one ended), plus the combined return, Sharpe, drawdown and trades |
| **Probabilistic Sharpe ratio (PSR)** | The chance that the true Sharpe is above zero, given the Sharpe measured, how many days it was measured on, and how fat-tailed and skewed the returns are |
| **Expected luck** | The best Sharpe that the number of tries would give by chance alone. It grows with the number of tries and with how far apart the tries' Sharpe ratios were |
| **Deflated Sharpe ratio (DSR)** | The PSR measured against that expected luck instead of zero: the chance the strategy is better than what trying that many times would produce by luck |
| **Tries** | Variants times folds when a grid is used (every variant was tried in every fold); 1 without a grid |
| **Scorecard** | Two lines: out-of-sample return positive in more than half the folds (needs 3 or more folds), and DSR of at least 0.95. Plus the same kind of honesty notes as a run's scorecard |

How to read it: a DSR near 100% means the out-of-sample Sharpe is hard to explain by luck; under 95% means
it could plausibly be luck. It is evidence, not proof. It assumes the tries were independent, which
neighbouring settings are not, so it can understate the luck. The sample is the out-of-sample days only,
and with fewer than 60 of them no DSR is given. A validation is a job on the same single worker as a
run: one at a time, with progress counted in window runs, cancel (the finished folds are kept, but a
partial validation gets no combined result), and "failed (interrupted)" if the app stops. It stores its
result in one row of the `backtestvalidation` table and never writes backtest rows.

---

### Look-ahead guard tests

A backtest is only honest if no decision can see the future. A dedicated test file guards that for every
reader of dated data, and any new one has to pass it:

- A **probe** wraps a data source and records, for every answer, the decision moment against the latest
  date the answer carries (a bar's day, a fact's "known at" time, a news time). Any value at or after the
  moment fails the test. The probe is itself tested against a deliberately leaky source to prove it
  can fail.
- **Future-change tests**: for many random decision moments on made-up prices and facts, everything after
  the moment is rewritten and the answer must be identical. This covers the backtest data source, the
  dated-fact readers, the insider, filing, fundamentals, earnings and FINRA readers, the weekly
  resample and a whole mini backtest (a shorter run must also be the start of a longer one).
- **Date-edge tests**: the clock change in March and November, half-day closes, weekend and holiday
  decision days, midnight in UTC versus New York, and a filing accepted at 17:59 versus 18:01 New York
  time against a decision at 09:45 the next morning.
- **Every reader must be listed**: the tests find every public function whose name ends in `_as_of`
  and fail if one is not on the guarded list, so a new reader cannot ship unchecked.

### What if? (replaying a settings change)

Before switching a setting on, the **What if?** card (Trade Plans page) re-decides the plans already made
under it and shows what would have changed. Only settings that decide take or skip can be replayed,
because only their inputs are stored on every plan:

| Replayable | Not replayable (use a backtest) |
|---|---|
| Minimum confidence to trade | Risk per trade, caps, starting cash |
| What an AI objection does (cancel, hold, nothing) | Slippage, commission |
| Whether an objection costs confidence points | Stops, targets, holding limit |
| Long only or short only | |

How it works: for each stored plan the take/skip verdict is recomputed from the stored confidence points
and AI opinion. A decision flips only if the new setting gives a different verdict from today's setting
on the same stored inputs. A trade that is now taken uses its hypothetical result from the Missed trades
list (§5); a trade that is now skipped loses its real result. A flipped trade with no result yet is
listed but not counted in the rates. The card shows before and after trades taken, win rate and average R
with 95% intervals, the flipped decisions with the rules version each was made under, and the limits:
taking an extra trade would change cash and caps for later trades, which the replay does not model.
It writes nothing.

## 19. Watchers

A **watcher** checks one source in the background (a new filing, a headline, a spike in short selling)
and reports **events**. Several watchers are installed today, among them the SEC filings watcher, the Fed watcher, the
posts watcher and the fund filings watcher (below). More are planned, each added on its own.

**What a watcher is.** A name, how often it should be polled, a cooldown, a daily cap, and one function
that fetches its source and returns events. An event holds the symbol (or none for a market-wide item),
a type, a headline, a link or other stable reference, a severity, extra details and the time the
information became public.

**What happens to an event, in this order:**

1. **It is saved first**, as a dated fact (§11) with the time it became public. A repeat of an item
   already saved is dropped, so nothing fires twice. Because it is saved first, a failed alert or
   evaluation never loses it.
2. **Cooldown and daily cap.** After an event fires for a symbol, more events for the same symbol from
   that watcher are saved but do nothing else until the cooldown ends. Each watcher also has a daily cap
   (counted on the New York date). A held-back event is still saved, with the reason.
3. **The action you chose** (Settings, Watchers card):

| Action | What it does |
|---|---|
| **Record only** | Nothing more |
| **Record and alert** | Also a Telegram message (§10) with the headline and the link. The text never contains your tokens |
| **Record, alert and re-evaluate** (default) | Also a full evaluation of the symbol, the same one the auto-scan runs |

**The market-closed rule.** If the symbol's market is open, the evaluation runs at once and follows
every normal rule: the same confidence bar, caps and AI second-opinion veto, and auto-execute only when
you have auto-execute on and a free position slot. A symbol you already hold is skipped. If the market
is **closed**, nothing is evaluated, because the plan would be priced off the last close and could fill
at a stale price. The evaluation is queued instead, in the same queue as off-hours plans (§8), and done
from fresh data at 09:45 New York time on the next trading day. Crypto (`-USD`) never closes, so it is
evaluated at once. An event never changes a trade's direction, size or levels. At most it asks for an
ordinary evaluation, and a plan made that way says "Triggered by <watcher>: <headline>".

**Failures.** If a watcher's source fails, the error is saved and shown, and the app waits before
trying that watcher again (its normal interval, doubled after each failure in a row, never more than
6 hours). A failing watcher never stops the others or the scheduler.

**Switches and safety.** Two switches must be on: the master switch and the watcher's own. Watchers
never run during a backtest or any simulated moment, because they read live sources. Their state
(last run, last success, last error, failures, fires today) is saved in the database and survives a
restart.

**On the Settings page:** the master switch, the action, the check interval, a table of installed
watchers (with a Run now button and an on/off switch each), and the latest events. With none installed
it would say "No watchers are installed yet".

### The House trade reports watcher (`house_ptr`)

Watches new **stock-trade reports from House members** (`backend/app/watchers/house_watcher.py`). Polled every
**6 hours**, with a **24-hour cooldown** per symbol and a daily cap of 10 alerts. Each poll reads the Clerk's yearly index
(also last year's in January and February), finds trade reports filed in the last 14 days that are not stored yet, reads up
to 25 of them, stores every row as a dated fact first (section 11), then returns events.

| Alert | When |
|---|---|
| **Purchase by a followed member** (notable) | A new report shows a stock purchase, in a watchlist symbol, by a member you follow (everyone in "all" mode). The alert says whose trade it was (a spouse's is marked), the amount range, the trade date, the filed date and the delay |
| **Cluster** (urgent) | The new report is the one that makes two or more followed members' purchases of the same watchlist stock fall within 30 days of each other |

**Sales never alert**: members sell for taxes and liquidity far more often than for a view. A report filed more than 5 days
before the poll is stored but never alerts, so the first run on an empty database stays quiet. Events exist only for
watchlist symbols, so a trade in a company you do not follow never starts an evaluation. An alert never changes direction,
size or levels; with the watchers action on re-evaluate it only asks for the ordinary full evaluation. A report that cannot
be downloaded is skipped and tried again next poll; if the index itself cannot be read the watcher backs off.

### The fund filings watcher (`fund_filings`)

Watches **new 13F reports from the funds you follow and new 13D/13G filings** (`backend/app/watchers/fund_watcher.py`).
Polled every **6 hours**, with **no cooldown** (several funds file on the same deadline day) and a daily cap of 30 alerts. It
stores every filing it finds as dated facts (section 11) before returning events.

| Alert | When |
|---|---|
| **A followed fund filed its 13F** (notable, no symbol) | A new report whose filing was accepted in the last 10 days: how many positions are new, added, trimmed and sold out against the quarter before, and the three biggest moves. Long positions only, as of the quarter end. If only one quarter is stored it says so instead of inventing changes |
| **New position in a watchlist stock** (notable) | A followed fund's new report shows a position in a watchlist symbol that the fund did not hold the quarter before, with its weight in the fund's portfolio (at most 10 per report) |
| **Schedule 13D on a watchlist company** (notable) | A new 13D accepted in the last 72 hours. An amended 13D is "info" |
| **Schedule 13G on a watchlist company** (info) | A new passive 13G. An amended 13G is stored and never alerts: index managers re-file them for nearly every large company every year |

Filings made **by a followed fund** about a watchlist company alert the same way; about other companies they are stored for the
5% owners tab and stay quiet. The summary has no symbol, so it is only an alert and never starts an evaluation. A new fund
position or a 13D in a watchlist symbol can, with the watchers action on re-evaluate, ask for the ordinary full evaluation;
nothing here changes direction, size or levels. With up to 40 watchlist companies each company's own filing list is read;
with more, SEC's market-wide latest-filings feeds are read first and only the companies found there are asked about. A
request SEC refuses or rate-limits ends that part of the poll: if nothing else was found the watcher records the error and
backs off, otherwise what was found is returned.

### The SEC filings watcher (`sec_filings`)

Looks for **new Form 4 and 8-K filings** for your watchlist companies (`backend/app/watchers/sec_watcher.py`).
Polled every **5 minutes**, with a **6-hour cooldown per company** and a daily cap of 20 alerts. It runs only
while the master switch (Settings, Watchers) is on.

**Every poll, in this order:**

1. Find filings the app has not stored yet. "Already stored" is read from the saved facts themselves (a
   filing number with stored rows is not fetched again), so there is no separate bookmark to lose.
2. **Store them first** as dated facts (§11): Form 4 rows and 8-Ks, each known from its SEC acceptance time.
3. Only then return events, which the framework saves and (if you chose so) alerts on.

**What alerts, and what never does:**

| Alert | When |
|---|---|
| **Insider buy** (notable) | One new Form 4 whose open-market purchases minus its sales come to at least **$100,000** |
| **Insider cluster** (urgent) | A new Form 4 completes a group of **2 or more different insiders** buying within 14 days of each other (looked for over the last 30 days) |
| **8-K** (notable) | The filing lists one of: 1.01, 1.02, 1.03, 2.01, 2.02, 2.05, 2.06, 3.01, 4.01, 4.02, 5.01, 5.02. Urgent for 1.03 (bankruptcy), 3.01 (delisting) and 4.02 (restated financials) |

**Insider sales never alert.** Insiders sell for many reasons (taxes, diversifying, pre-set plans) that say
little about the company. Sales, grants, option exercises, 8-Ks with only routine items (8.01 "other events",
9.01 exhibits) and amendments (4/A, 8-K/A) are stored and stay quiet. A filing accepted more than **48 hours**
before the poll is stored but never alerts, so the first run on an empty database does not announce a week
of old filings. The alert's link is the SEC's page for the filing.

**How it finds filings.** With up to **40** companies on the watchlist it asks each company's filing list
(one request per company per poll). With more (the bundled list has about 500) it reads the SEC's
market-wide "latest filings" feeds for Form 4 and 8-K, keeps the entries for watchlist companies, and asks
only those companies' filing lists. It reads the feed back to the last successful poll (plus 30 minutes),
at most 6 pages of 100 entries per feed. Crypto pairs, indexes and symbols the SEC does not list are skipped.
All requests go through the shared SEC client (§11): your SEC contact, at most 5 requests per second.

**If the SEC refuses.** A refusal (403 or 429) ends the poll at once. If nothing had been found yet the
failure is saved and shown like any watcher failure and the watcher backs off (see Failures). Events already
found in that poll are kept. A single filing that cannot be read is skipped and tried again next poll.

**Limits.** A poll that crashes after storing a filing but before returning its event loses that alert (the
filing itself is kept). A watchlist company missing from the SEC's company file is not watched.

**The scheduler** wakes every `watchers_poll_minutes` (5 by default, with a small random delay) and runs
the watchers that are past their own interval.

**Limitation:** changing the check interval takes effect after the app restarts.

### The Fed watcher (`fed`)

Watches the Fed's public feeds for **policy statements and speeches** (`backend/app/watchers/fed_watcher.py`).
Polled every **15 minutes**, with a **1-hour cooldown** (all Fed events share one, because they name no company)
and a daily cap of 10 alerts. Every poll stores each item as a dated fact first (§11, "The Fed"), then returns events:

| Alert | When |
|---|---|
| **FOMC statement** (urgent) | The Fed issues its rate-decision statement |
| **Economic projections, meeting minutes** (notable) | The projections or the minutes of a Committee meeting are released |
| **Chair speech or testimony** (urgent) | A Chair speaks or testifies |
| **Board member speech or testimony** (notable) | A Board member on the code's list speaks or testifies |

Everything else (discount-rate minutes, task-force notices, a staff member's testimony) is stored and stays quiet.
An item published more than **48 hours** before the poll is stored but never alerts.

**It is a risk flag, not a trade idea.** A Fed event names no company, so it is saved and alerted but can **never
start an evaluation**, whatever the action setting says. What a statement means for any one stock is not decided:
nothing reads the words (the tone is not read, §11). If no feed can be read, the failure is saved and shown and the
watcher backs off; if only some can, the others still count.

### The posts watcher (`trump_posts`)

Watches **Donald Trump's Truth Social posts** through an **unofficial archive feed** (§11, "Posts")
(`backend/app/watchers/posts_watcher.py`). Polled every **5 minutes**, with a **30-minute cooldown** per company
(and one shared cooldown for market-wide topics) and a daily cap of 30 alerts. Every poll stores each post as a
dated fact first, then returns events. **Rules decide, there is no AI.**

| Alert | When |
|---|---|
| **Names a watchlist company** (notable) | The post contains a watchlist company's exact name (without "Inc." or "Corporation"), or its ticker as `$NVDA`, `NASDAQ: NVDA`, or a bare ticker of 4 or more capital letters. One event per company. This is the only kind that may start an ordinary full evaluation of the company (queued for the open if the market is shut, like any watcher event) |
| **Market topic** (notable or info) | The post touches tariffs, China, the Fed or interest rates, semiconductors, oil and gas, drug makers, banks, electric vehicles or crypto, and names no watchlist company. Alert only. Tariffs, China and the Fed are notable, the others info |

Names that are also ordinary words ("Target", "Visa", "Block", "Fox", "Dow", "News", ...) match only with their full
legal name, so "Fox News" or "the Dow" names no company. A short ticker ("F", "T", "ALL") is never matched bare.
Crypto pairs are not matched by name; crypto is a topic. Reposts and posts with no words are stored and never alert.
A post older than **6 hours** is stored but never alerts. The post does not choose a direction: whether it is good
or bad for the company is not read.

**Honest limits.** A post can move a stock within **minutes**; this watcher checks every 5 minutes and the scans
run three times a day, so most of the move is usually over by the time anything reacts. The source is unofficial
and can lag, drop posts or stop (a feed that cannot be read, or whose format changed so that no post can be read,
is saved as an error and produces no events). Testing whether posts predict anything needs **hourly price bars**,
and only about two years of those exist, so any result will rest on a short, thin sample.

---

## 20. Smart Money

The **Smart Money** page (`/smart-money`) shows what people with inside knowledge have done with a
company's stock, read from public filings. **Insiders**, **Congress**, **Funds** and **5% owners** work today.

**Where the numbers come from.** SEC Form 4 filings, stored one row per trade as dated facts (§11). Each row has
two dates, and the page shows both: the **trade date** (what happened) and **public since** (the moment SEC accepted
the filing, the first time anyone could see it), with the delay between them. Every row links to its SEC filing.
A filing only appears from the moment it was accepted.

**What is listed.** Open-market buys (SEC code P) and open-market sells (code S). Grants, option exercises and tax
withholding are left out because they are not market decisions. A row flagged as made under a pre-arranged
**10b5-1 plan** carries a chip. Roles show as badges (CEO, CFO, Officer, Director, 10% owner).

**How it relates to the score (§5).** Only open-market **buys** can ever add a point, at most one, and only when net
buying is large enough. **Sells are never scored.** The page says so on its face. The Analysis page's Overview tab has
a compact "Insider activity" card for the shown symbol: 90-day buy and sell counts and values, cluster count, what
today's rules would add to a long (for information only, it changes nothing), and the latest trades.

**What the Insiders tab holds.**

| Part | What it shows |
|---|---|
| Summary cards | Trades in the window, value bought, value sold, number of cluster buys |
| Cluster buys | Two or more different insiders buying within 14 days of each other, with value, roles, trade dates and the moment the cluster became visible |
| Trades table | Newest filing first, up to 500 rows. Filters: side, minimum value, days (30 to 365) and symbol. A minimum value hides rows whose filing gave no price |

**Nothing loaded is not "quiet".** With no stored filings the page says so and shows the command to load them
(`python scripts/backfill_insider_trades.py AAPL MSFT --since 2025-01-01`) and a **Load / refresh from SEC** button.
The button asks SEC for the watchlist's filings, a few symbols per click (symbols with nothing stored first; 90 days for
a new symbol, 14 days for one already stored). Click again while it says symbols are waiting. If the install still uses the
placeholder SEC contact the page notes it (set `SEC_EDGAR_USER_AGENT`).

**Limits.** Only symbols you have loaded appear. Buys by an insider with no price in the filing add to the count but not
to the value. Two buys by the same person never form a cluster.

### The Congress tab

The **Congress** tab lists stock trades that **members of the US House of Representatives** have reported. It is read from
the House Clerk's public disclosure site (section 11, "House trade reports"). It is **House only**: the Senate's disclosure
site refuses automated access, so **Senate trades are not available**, and the page says so.

**Things to know before reading the numbers** (the tab shows them on its face):

| Fact | What it means |
|---|---|
| **Amounts are ranges** | The form makes members pick a band such as $1,001 - $15,000 or $250,001 - $500,000. The tab always shows the band. It also shows the **range midpoint**, labelled as such: only the middle of the band, never the real amount. A top band ("Over $50,000,000") has no upper end and no midpoint |
| **Reports are late** | A member has up to **45 days** to file, and some file later. Each row shows the trade date, the **filed date** and the delay. Rows are dated by the filing day, because that is when anyone could first see them |
| **Spouse and child trades** | Included, with a badge saying whose they were. They are not necessarily the member's own decision |
| **Some reports cannot be read** | Scanned, image-only reports have no text. They are counted ("Unreadable reports"), listed with a link to the PDF and never guessed. No OCR or AI is used |
| **Not every row has a ticker** | Bonds, funds and private assets are stored and shown with "no ticker", and never scored |

**What the tab holds.**

| Part | What it shows |
|---|---|
| Summary cards | Trades in the window, stock purchases, cluster buys, unreadable reports |
| Cluster buys | Two or more different members buying the same stock within 30 days of each other (stocks only, not options), with the sum of the ranges and the day the cluster became public |
| Trades table | Newest filing first, up to 500 rows. Filters: side, days (30 to 365), symbol and, when you follow a list, "only members I follow". Each row links to the PDF |

**Who to follow.** Settings has a **Congress: who to follow** card. **Follow every member** is the default. **Only the
members below** limits the "only members I follow" filter and the House watcher's alerts to a list you build (at most 50
names). You can search names seen in the reports already loaded, or type one: names match as the Clerk prints them,
ignoring capitals and punctuation. Following nobody in list mode means no member alerts (every report is still stored).

**Loading data.** With nothing stored the tab says so and shows the command (`python scripts/backfill_house.py --year 2025
--since 2025-01-01`) and a **Load / refresh from the House Clerk** button. The button reads at most 15 new reports per
click, looking back 60 days, and asks you to click again while reports are waiting. It answers `429` if used in the last
60 seconds. The backfill script is resumable: stored reports are skipped without a request.

**How it relates to the score (section 5).** One silent signal, `congress_buying`, is recorded on every trade plan with 0
points: the number of different members who bought minus those who sold the symbol in reports filed over the last 45 days.
It needs a net of at least 2 members to score at all, is signed by the trade direction, and is capped at plus or minus 1.
Until a backtest shows it beats luck it adds nothing to confidence. With no Congress data stored it reads "not available".

### The Funds tab (13F holdings)

Large investment managers must report what they hold every quarter on **Form 13F**. The **Funds** tab shows the funds
you follow (a built-in starting list of six well-known managers until you choose your own, section 12), what each held at
its latest quarter end, and what changed against the quarter before. It reads SEC EDGAR (section 11).

**What a 13F can and cannot say** (the tab repeats this on its face):

| Fact | What it means |
|---|---|
| **Long positions only** | Short positions and most hedges are invisible. A fund that looks bullish may be hedged |
| **A snapshot of the last day of a quarter** | The list is for the quarter-end date, not for any day since |
| **Filed up to 45 days late** | Each row is dated by the moment SEC accepted the filing. A quarter is invisible to any test or simulation before then |
| **No trade dates** | "Added" means more shares at this quarter end than at the last one; it says nothing about when. A stock split looks like a large add |
| **Tickers are matched by exact name** | The filing gives a company name and a CUSIP, not a ticker. The app matches names to its own stock list and SEC's company list, with a short hand-checked table for companies whose share classes share one name. A holding that does not match exactly one company is shown by name with "no ticker" and is never guessed |

**Reading the filing.** Each fund's filing is read with a real XML parser. Several rows for one security (one per sub-manager)
are added together; options (puts and calls) and bond principal stay separate positions. Filings for quarters before 2023 give
values in thousands of dollars and later ones in whole dollars, but a few managers still file thousands; the app checks the
implied share price to tell which, and shows dollars either way. A **13F-HR/A** amendment either replaces the quarter
(restatement) or adds lines (new holdings) from the moment SEC accepted it; before that, the original stands. A **13F-NT**
notice means the fund's holdings are reported inside another manager's filing: it is listed as such and holds no data.

**What the tab holds.**

| Part | What it shows |
|---|---|
| Fund cards | Each followed fund: latest quarter, filing date, number of positions, total value, quarters stored, how many holdings got a ticker |
| Changes | For the selected fund: counts of **new**, **added**, **trimmed** and **sold out** positions (share count up or down by at least 1%) and a table with shares now and before, the change and the weight in the fund's reported portfolio. A warning shows if the earlier stored quarter is not the quarter right before |
| Largest positions | The fund's top 15 by value |

**Held by followed funds (Analysis page).** The Analysis page's overview shows which followed funds hold the symbol at their
latest quarter end, with the change against the quarter before. With no filings stored it says it cannot tell.

**Loading data.** With nothing stored the tab says so and shows the command (`python scripts/backfill_13f.py --quarters 4`)
and a **Load / refresh from SEC EDGAR** button. The button loads the latest two quarters of a fund with nothing stored, and
only new filings for the others; it also looks up 5% owner filings (below) for up to 15 watchlist companies and for the
funds themselves. It answers `429` if used in the last 60 seconds and the button counts the wait down. The script is
resumable: stored filings are skipped without a request. Requests share the SEC client (section 11), so the contact address
for SEC (`SEC_EDGAR_USER_AGENT`) matters; the tab warns when it is still the placeholder.

**How it relates to the score (section 5).** One silent signal, `fund_accumulation`, is recorded on every trade plan with 0
points: among the followed funds with a filing accepted in the last 120 days that report the symbol, how many newly hold it
or hold more than the quarter before, minus how many hold less or have sold out. It is signed by the trade direction (net
buying supports a long and argues against a short) and capped at plus or minus 1. With no filings stored it reads "not
available"; with filings but no fund holding the symbol it reads "none".

The SEC also publishes a quarterly bulk file of every manager's 13F data. The app does not use it; it is a possible later
source for covering the whole market.

### The 5% owners tab (Schedule 13D and 13G)

Anyone who ends up holding **more than 5%** of a listed company's shares must file a **Schedule 13D** (the holder may want to
influence the company: an activist, within 5 business days) or a **Schedule 13G** (a passive holder such as an index manager).
The **5% owners** tab lists these filings, newest first, for the companies you have loaded.

| Column | What it shows |
|---|---|
| Public | The day SEC accepted the filing and the event date the filing is about |
| Company, Holder | The company and the largest reporting person (a 13D often has several; the count of others is shown) |
| Form | 13D or 13G, with "/A" for an amendment |
| Of the class | The percent of the share class and the number of shares |
| Purpose (13D) | The opening of Item 4, where a 13D says what the holder intends. A 13G shows the rule it was filed under instead |

Filters: days (30 to 365), schedule and symbol. The cards count 13D and 13G filings in the window.

**Things to know:** for a **large company this is rare and mostly routine**: the 13Gs on a mega-cap are nearly all index
managers re-filing, and an activist crossing 5% of such a company almost never happens, so the list is often short or empty
for them. A filing says what the holder owns now, not whether it is buying or selling (an amendment can report either).
Only the **structured filings (2024 onward)** are read; older free-text filings are counted on refresh and skipped, never
guessed. Filings by the **funds you follow** are loaded too (for example a 13D from an activist fund), with the company's
ticker looked up from SEC's company list.

**How it relates to the score (section 5).** One silent signal, `ownership_5pct_filing`, is recorded on every trade plan with
0 points: a **new Schedule 13D** (not an amendment, and not a 13G) filed in the last 30 days. A new 13D is a stake being
built, so it would add 1 to a long and subtract 1 from a short; with no clear direction it reads 0.

## 21. Market Terminal

The **Market Terminal** page (`/terminal`) is a read-only overview of the market. It places no trades and
changes nothing. It has three parts, top to bottom.

**Market recap.** A short summary written by fixed rules from the numbers on the page: how many of the
scanned symbols rose and fell, how many closed at a 52-week high or low, the top gainers and decliners,
the strongest and weakest sectors, and the VIX. It is labelled **rule-based**. If an AI provider is
connected, an **Add an AI paragraph** button appears. The model is given only the listed numbers and told
not to add anything. That paragraph is labelled as AI-written, is only made when you press the button
(it costs one model call), and is kept for 30 minutes. If the call fails, the rule-based recap stands alone.

**Sector heatmap.** One tile per symbol, grouped by sector. Tile **area** is market cap (a symbol whose cap
is unknown gets the median; if most caps are unknown, all tiles are equal and the page says so). Tile
**colour** is the price change over 1 day, 5 days or 1 month, from green to red. Clicking a tile opens that
symbol in Analysis. Below it is a row of the eleven sector ETFs (XLK, XLF, XLE, XLV, XLY, XLP, XLI, XLB,
XLU, XLRE, XLC), which show each sector across the whole market, not just your list. To avoid hundreds of
requests, the page reads only the **first 60** symbols of your universe by default (you can pick 30 to
200). The page says how many it read out of how many exist. Symbols with no data are listed, not drawn.

**Macro panel.** Tiles for the S&P 500, Nasdaq Composite, Dow Jones, Russell 2000, VIX, the US dollar
index and the 3-month, 5-year, 10-year and 30-year Treasury yields. Each tile shows the last value, the
change since the previous close, a sparkline of about 30 daily closes, the date of the last bar and the
source. A yield curve chart shows the **10-year minus 3-month spread** and marks the curve **inverted**
when it is negative. The VIX is marked **elevated** from 25, the same level the scoring uses to lower
confidence. A series the data providers cannot supply shows **not available**. A 2-year yield is not
available from the free sources, so it is shown as missing and never estimated.

All three come from daily bars through the normal data providers (so they share the 15-minute price
cache) and are also kept for 5 minutes by the page's own cache. The page shows an "as of" time on each
part. Nothing here is stored in the database.

---

## 22. Calendar and earnings preview

### The Calendar page (`/calendar`)
One list of dated events, by **week** or **month**, in US Eastern time. Three sources feed it, and each can fail on its own without hiding the others:

| Source | What it gives | Limits |
|---|---|---|
| **Economic feed** (Forex Factory's free weekly JSON, no key) | US releases with time, impact (Low / Medium / High / Holiday), **forecast** and **previous** value | Only the current Monday-to-Sunday week. It never has the released "actual" value, so the page says "not in this feed". It is unofficial and can answer with an empty page when asked too often. |
| **Built-in dates** (`analysis/macro_calendar.py`) | Fed decisions (to the end of 2027), CPI releases and jobs reports (2026 only so far) | Kept by hand from the Fed and BLS schedules. |
| **Market data provider** | The next earnings date of every watchlist symbol (the first 60) plus every open position and any symbols named in the request | Crypto pairs have none. One lookup per symbol, cached for a day. |

**How the lists merge.** When the feed lists a Fed / CPI / jobs release on the same day as the built-in table, the feed row (with its forecast) is shown and the table row is dropped. When the feed does not list it on a day it covers, the built-in row stays and says so. Fed decisions show 2:00 pm and CPI / jobs reports 8:30 am, the agencies' fixed times.

**My positions.** An earnings row for a symbol you hold is flagged. A Fed / CPI / jobs row, or a High-impact economic row, in the next **14 days** is flagged for every open position, since it hits the whole market. A card at the top lists, per open position, its earnings date and the macro days inside that window. The "My positions" chip filters to the flagged rows.

**When a source is down** the page says so, for example "economic calendar source unavailable: showing the built-in Fed/CPI/jobs dates only". The feed is cached 3 hours; if a refresh fails the last good copy is shown (marked stale) for up to a week; after a failure it is not asked again for 10 minutes. The page never invents events.

**Date check.** The footer compares the feed with the built-in table for the days the feed covers and lists any disagreement (a Fed / CPI / jobs date in one and not the other). It only reports: the table is never changed automatically. A feed title the app does not recognise is simply not compared.

### Earnings preview (Analysis page, Earnings tab)
Below the upcoming-earnings card. **Facts first, computed by rules from our own data; the paragraph on top is optional.**

| Part | How it is made |
|---|---|
| Report date and estimates | The data provider's next earnings date, EPS and revenue estimate |
| Median past move | Median absolute % move around the last reports (close before to first close after), from up to 5 years of prices |
| Options-implied move | From the options' at-the-money implied volatility to the nearest expiry. It is **compared** with the median past move (ratio, "rich" at 1.25x or more, "cheap" at 0.8x or less) only when that expiry is on or after the report date; otherwise it does not price the report and is shown without a ratio |
| Scenarios (Bull / Base / Bear) | The 75th / 50th / 25th percentile of the stock's own **signed** past reactions. They are how it reacted before, never a forecast or a price target. Needs at least 4 past reactions |
| Surprise record | Beats and misses of EPS consensus (by 1% or more) and the last 4 quarters with the stock's reaction |
| What to watch | Short bullets, each from a number above (rich or cheap options, beat streak, estimate vs last EPS, RSI, 52-week range). Nothing generic |

The paragraph is written from those facts by the configured AI (routine model, facts only, marked as data, no web search, no price target, no direction guess). With no AI, or if it fails, a rule-based paragraph is shown. The finished preview is cached 15 minutes. A section whose data cannot be read is listed as missing instead of guessed. Crypto symbols have no preview.

---

## 23. Notifications: morning note, weekly digest and price alerts

Three kinds of Telegram message, all built from the app's own data. **They only inform: none of them can open, change or close a paper trade or move a stop.** They need a bot token and chat ID (§10). Switches and times are in the **Notifications** card on the Settings page, which also has **Preview** buttons (build the note from live data, send nothing, no AI call) and **Send now** buttons (deliver it right away, any day). Each send-now button waits 60 seconds between uses.

### Morning note
Sent on **US trading days** at the time you choose (default 08:45 New York time; weekends and market holidays are skipped). The app looks every 5 minutes, so a note missed because the app was down at 08:45 is still sent if it starts within 3 hours, and it is never sent twice in a day. After a failed send it waits 30 minutes before trying again.

| Part | Where it comes from |
|---|---|
| **Overnight and market** | Fresh quotes for S&P 500 and Nasdaq 100 futures, the VIX and SPY. Any level without a fresh quote is left out and named as unavailable |
| **Open positions** | Each open paper position: result in % and money, result in R, distance to the stop and to the first target. Thesis status is added when the position has one |
| **Plans waiting** | Tradeable plans from the last 3 days that are still pending, and stocks queued for the market-open redo |
| **Scheduled today and tomorrow** | The merged calendar (§22): High-impact releases, Fed / CPI / jobs dates, and earnings of held stocks and the top setups |
| **Watchlist: top setups** | A rule-based scan of the first 40 watchlist stocks: up to 5 potential setups you do not already hold |
| **Watcher events and insider clusters** | Watcher events from the last 24 hours and insider-buying clusters from the last 7 days (§19) |

Every part can fail on its own: a part that could not be built is listed at the bottom under "Not available this time" and the rest is still sent. If there is nothing to report it says so in one line. **The text is rule-based and always complete.** If an AI provider is configured, one short paragraph that only restates those facts is added and labelled "AI summary"; the AI gets the facts only (marked as data, headlines included, so a hostile headline cannot give it orders) and supplies no number. With no AI, the note ends "No AI summary: this is the rule-based note." A note longer than Telegram's limit is split at line breaks into at most 3 messages.

Example (shortened):

```
Strategeia morning note - Tue 29 Sep 08:45 ET
Paper trading only: nothing here places or changes a trade.

OVERNIGHT AND MARKET
- S&P 500 futures: 5,800.00 (+0.40%)
- VIX: 15.20 (-3.00%)

OPEN POSITIONS (1)
- AAA long 10 sh from 100.00, now 103.00 (+3.0%, +$30, +0.6R); 7.8% to stop 95.00; 6.8% to first target 110.00

SCHEDULED TODAY AND TOMORROW
- today 08:30 ET: CPI release

No AI summary: this is the rule-based note.
```

### Weekly digest
Sent at **16:30 New York time on the last trading day of the week** (Friday, or Thursday when Friday is a market holiday), once.

| Part | What it says |
|---|---|
| Trades closed this week | How many won, total result in money and R, the best and worst trade, each trade. With fewer than the minimum number of trades it says so plainly |
| Equity | Equity at the start of the week (the last snapshot before it) and now |
| Plans this week | Evaluations taken, tradeable but not taken, and no-trade decisions; and, from the missed-trades report (§5), what the declined plans that already have a final result would have earned (idealised fills, not real results) |
| Calibration | The headline of the calibration report (§5), with its own warning about small samples |
| Lessons | Which symbols got an AI lesson this week (the text stays on the Portfolio page) |
| Watchers | How many watcher events came in |
| Next week | High-impact releases and earnings of held stocks |

Same build as the morning note: rule-based text, an optional labelled AI paragraph, parts named when unavailable.

### Price alerts
Alerts are saved in the database and checked **every 5 minutes**. Stocks are only checked while the US market is open; crypto (`-USD`) always. Quotes are read fresh: if a fresh price cannot be fetched the symbol is skipped that round, never judged on an old price.

| Condition | Fires when |
|---|---|
| Price at or above / at or below | The live price is at or beyond your number |
| Day move | The price has moved at least that many percent since the previous close, up or down |
| Close to my stop / first target | An **open position** in that stock is within your distance of its stop or first target, measured in % of the price or in ATRs. A price already at or past the level counts |

An alert is **one-shot** (becomes "triggered" the first time) or **repeating** (fires again after its cooldown, 4 hours by default, 5 minutes to 7 days). It can be cancelled; a cancelled or triggered alert can be removed. At most **100 active alerts**; the symbol must be 1 to 15 letters, digits or `. - ^ =`; the number must be above 0; a stop or target alert needs an open position in that symbol.

Create one from the **Analysis** page ("Alert me" next to the stock's name); every alert is listed on the **Portfolio** page with cancel and remove buttons. When one fires, the app records a dated fact and sends a message such as:

```
Price alert - NVDA
Price above your level
price 151.20 is at or above 150.00
Your note: breakout
Paper trading only: nothing was traded or changed.
```

**Alerts on open positions** (a setting, on by default) need no setup: each open position gets a message when it comes within 1 ATR of its stop or within 1% of its first target (both distances are settings), at most once a day for each. Without Telegram configured, alerts are still evaluated and recorded; nothing is sent.

---

## 24. Sleeves: one paper account per trading style

A **sleeve** is a separate paper account for one trading style. Each sleeve has its **own cash, positions, statistics and equity curve**. The strategy settings (risk per trade, caps, slippage, exits) are shared by all sleeves.

There is always one built-in sleeve, **core** ("Swing (rules)"). It is the account the app always had: every position, plan and equity point from before sleeves existed belongs to it, with no change and no conversion. The automatic scan, the backtester, the replay and the missed-trades study all work on core only.

| Question | Answer |
|---|---|
| Where is a sleeve chosen? | Tabs at the top of the Dashboard and the Portfolio page. The choice is kept in the address (`?sleeve=key`). Core is the default. |
| Can two sleeves hold the same symbol? | Yes. They are separate accounts. Inside one sleeve the one-position-per-symbol rule still holds. |
| Do the caps apply together? | No. The maximum number of positions and the sector cap are checked **per sleeve**. |
| What does a disabled sleeve do? | It opens nothing new. Its open positions are still marked and closed by their stops and targets. Core cannot be disabled. |
| Which sleeve does a plan trade in? | The one it was made for. Pressing Execute opens the position in the plan's sleeve, and an off-hours plan is redone in that sleeve. |
| Which starting cash? | Core follows Settings, Paper Account. Another sleeve starts with the cash it was created with. |
| What does "Reset" do? | It wipes one sleeve (positions, curve, cash). `all` wipes every sleeve and needs `confirm=true`. The sleeves themselves stay. |
| Can a sleeve be deleted? | Only if it never held a position or a plan. Otherwise disable it, so the history that the statistics came from is kept. |

The Portfolio page also shows a **Sleeves** table with value, return, open positions, win rate and average R for each sleeve. The calibration card and the other studies on that page cover core only, and say so.

Reads never write: opening the page marks positions to market without adding an equity point, as before. The only thing a read can create is the core sleeve row, the first time it is looked at.

**Combined view.** `?sleeve=all` on positions and statistics shows every sleeve together (cash and starting cash are added; win rate and average R are over all closed trades). There is **no combined equity curve**: sleeves are sampled at different moments, so adding their points would invent values nobody measured. Ask for each sleeve's curve instead.

---

## 25. AI Committee

The **AI Committee** page (`/committee`) gives a second, written opinion on one symbol. It is an **opinion only**: it never opens, sizes, stops or cancels a trade, it never changes the rules engine's direction or size, and the AI overlay and the rules never read it. It needs a real AI provider (the "none" provider only echoes text, so the page refuses to start).

**The steps, in order**

| Step | Who | Model tier | What it does |
|---|---|---|---|
| 1 | Market, fundamentals, news and insider analysts | routine | Each writes a report from a data block the app computed (no AI). An analyst whose data could not be fetched is skipped and costs no AI call |
| 2 | Bull and bear | routine | They argue for and against, one or more rounds |
| 3 | Research manager | decision | Weighs the debate and gives a first rating |
| 4 | Trader | decision | States the action (Buy, Hold or Sell) it leans toward. A leaning, not an order |
| 5 | Aggressive, conservative and neutral risk analysts | decision | A three-way debate about the trader's leaning |
| 6 | Portfolio manager | decision | The final rating: **Buy, Overweight, Hold, Underweight or Sell**, with a conviction (low, medium, high), a summary and the key risks |

The "routine" and "decision" models are the ones set in Settings (a blank decision model means the routine one). With the research mode set to "Allow web search" the analysts (and only they) may search the web if the provider can; otherwise everything stays on the app's own data.

**Live view.** The page shows the whole path as soon as a run starts. Each step turns from "waiting" to "writing" to "done" and its text appears the moment it is finished. The page asks the server every second and a half; there is no push connection.

**Cost control.** A run has a cap on AI calls (default 14; Settings, AI Committee limits). Optional steps (analysts and debate turns) run only while enough calls are left for the three required ones (research manager, trader, final rating); the rest are shown as skipped with the reason. Debate rounds are capped at 3 whatever is saved. One run at a time. Every report is saved with the run, so reading an old run costs nothing.

**What it is given.** The same computed numbers the AI overlay gets (price, trend, RSI, EMAs, support and resistance, ATR, volume, 52-week range, earnings date), but **not** the rules engine's verdict, so the committee forms its own view. Headlines are fenced as outside text to weigh, never to obey. Any dollar amount or decimal percentage a step quotes that is not in the data it was given is listed under that step as a warning. The warning never changes the rating.

**When something goes wrong.** If a required step fails, the run stops, later steps show as skipped, and the run is saved as failed. If the final reply cannot be read as one of the five ratings, **no rating is recorded** and the card says so. A run left running when the app stops is marked failed at the next start.

**Limits.** Runs for several symbols at once, "top of a scan" runs, a stop button, a trade made from the rating, and use of the research library as context are not built. The rating is not backtested and says nothing about how right the committee is: treat it as reading material.

**Kill switches and drift alarms.** Off until `Kill switches` is switched on in Settings. Every 10 minutes the app checks each sleeve, and if one trips a switch it is **paused**: it opens no new positions, a Telegram message is sent, and nothing is closed (open positions keep being managed by their stops and targets). A pause never lifts itself; resume with the **Resume** button on the Portfolio page (it appears under the sleeve tabs with the reason; a "Pause history" card lists every pause) or `POST /api/sleeves/{key}/resume`. The two limits are edited in Settings > Kill switches. Two switches:

| Switch | Setting | Meaning |
|---|---|---|
| Drawdown | `kill_switch_drawdown_pct` (default 15, 0 = off) | Pause when the sleeve's equity is this many percent below its peak. After a resume the peak is measured from the resume on. |
| Drift | `kill_switch_drift_psi` (default 0.25, 0 = off) | Core sleeve only. Compares the spread of live trade results (in R) with the latest finished backtest (PSI; below 0.10 stable, above 0.25 act). Needs 20 trades on each side. |

`GET /api/sleeves/pauses/history` lists every pause. There is no Settings or Sleeves screen for these yet; use the API.
