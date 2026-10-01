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
 STOCK LIST  backend/data/sp500.csv (64 symbols); the test filter narrows it to NVDA, AAPL
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
 EXIT CHECKS every 15 min during US market hours ─ stop or TP1 touched ─▶ position CLOSED
     │
     ▼
 STATISTICS AND EQUITY CURVE ─ computed from the real simulated trades
```

Behind all of it, every 15 minutes, a **health check** confirms the market data, the AI and Finnhub
still answer, and messages you on Telegram if one goes down or comes back.

---

## 3. The pages

The sidebar holds Dashboard, Market Scan, Analysis, Trade Plans, Portfolio and Settings, plus status
pills (AI, AI overlay, Finnhub, Telegram) that expand when something is down, and a log-out button.

### Dashboard
- **Stat cards:** Portfolio Value, Total Return, Win Rate, Total Trades, Avg R, Active Positions.
  Rate-style numbers show how many trades they're based on. Sparklines appear only where a real
  history exists (the equity curve).
- **Market status:** the US market's state (open, pre-market, after hours, closed, or closed for a
  named holiday), a countdown to the next open or close (1:00 pm early closes included), and how
  fresh the data is. It comes from the backend's market calendar (§8), the same one that decides
  whether a position may open.
- **Top Setups Today:** the 5 highest-scoring stocks from a fresh scan of the whole list.
- **Top Pick:** the latest plan for the #1 setup, with its technical read, AI take and risk.
- **Latest Trade Plan:** the most recent plan for any stock.
- Opening the dashboard scans the list (answered from the cache when fresh) and runs the exit check,
  so a position whose stop or TP1 was hit since the last check closes straight away. Opening it
  **never adds a point to the equity curve** (§7).

### Market Scan (Market Scanner)
- A table of every stock in the list: price, 24-hour change, trend, momentum, score (0–6), signal and
  a small price trend line.
- **Signals:** score ≥ 4 is a **potential setup**, 2–3 is **watching**, and below 2 is **no signal**.
- A filter to show a single symbol.
- **Auto-trade button:** runs the full evaluation loop now (see §8), at most once every 60 seconds.

### Analysis
- **Price chart** (candles) with EMA20, EMA50, Bollinger bands, support and resistance lines, RSI and
  MACD. A legend under your cursor shows the exact numbers for that bar.
- **Ranges:**
  - **1D** uses 5-minute bars and **1W** uses 15-minute bars. Both also show VWAP.
  - **1M, 3M, 6M and 1Y** use daily bars. The indicators are always computed on a full year, then
    trimmed to the view.
- **Potential Breakout** badge: shown when there's a clear trend and price is within 2% of the next
  level.
- **AI Insight:** a short explanation of the chart.
- **The research panel** (the old Research page now opens here):
  - **AI Summary**,
  - **Key Financials** (a revenue chart),
  - **Key Catalysts**,
  - **Upcoming Earnings**, with analyst estimates under "About These Estimates",
  - **Recent News**. Links are only clickable if they're normal web links, for safety.
- **Generate Trade Plan →** button.

### Trade Plans
- **Generate Trade Plan** for any symbol.
- **Each plan card shows:**
  - direction, entry, stop, TP1, TP2, **R:R ratio**, share count, money at risk and potential gain,
  - confidence as a % *and* in points (for example "7/16 pts"), with a chip per scoring part (§5),
  - the list of reasons behind the score,
  - the ATR and how many ATRs away the stop is (amber if it's under 1×, which means it's inside normal
    daily noise),
  - the options-implied move next to the stock's typical move after past earnings,
  - the AI take (with the provider named, or marked as rule-based),
  - the AI overlay's opinion, if that's switched on,
  - the "why it didn't auto-execute" note,
  - a chart with the entry, stop and target lines.
- **Pending plans** can be executed by hand, but only while the US market is open (crypto: any
  time). While it's closed, the **Execute** button is greyed out and says why.
- **A plan made while the market was closed** reads **"Waiting for the next open"**. It can't be
  executed, even in the first minutes after the bell: it is redone from fresh data at 09:45 New
  York time (the card also shows that time on your own clock), and only the new plan can be
  executed.
- **History:** every plan, including **"no trade"** decisions with their reason. Plans waiting for
  their redo are marked "redo at the open".

### Portfolio
- **Stat cards**, **Active Positions** (entry, planned entry, stop, TP1, TP2, shares, current P&L) with
  a **close** button that closes at the market.
- **Closed Positions:** exit price, reason (stop, TP1 or manual), P&L and result in R.
- **Equity Curve.**
- **Reset** button: wipes positions, equity history and cash, and restarts at the Starting Cash
  setting. Plan history is kept.

### Settings
Grouped into cards. Every setting is listed in §12:
- **AI Narrative Provider**,
- **AI Trading Overlay**,
- **Finnhub (optional, free tier)**,
- **Paper Account**,
- **Telegram Notifications**,
- **Unattended Auto-Scan**.

Keys are shown masked (the last 4 characters only), and there's a **Test Connection** button for each
provider.

### Login
Username and password. Three wrong tries lock that device out for 15 minutes, and a login lasts 7
days. (In the current `docker-compose.yml` the login is switched off; see §13.)

---

## 4. How one stock is evaluated

`generate_trade_plan` in `backend/app/services/trade_plan_service.py` runs these steps in order:

1. **Prices:** 1 year of daily bars, plus the current quote.
2. **Chart analysis:** EMA20, EMA50, RSI14, trend, momentum, and support/resistance levels (§5).
3. **Direction:** Bullish trend means **long**, Bearish means **short**, and Neutral means no
   direction.
4. **Every other check,** scored *for the trade's direction*, so evidence that supports a short counts
   *for* a short:
   - fundamentals and news,
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
| **Macro events** | 0 or −1 | A **Fed decision, CPI or jobs report today or tomorrow**. The dates are hand-kept for **2026 only** (§14). |
| **AI overlay** | 0 to −3 | Only if the overlay is on and would not take the trade: **−1** under 45% conviction, **−2** from 45–64%, **−3** at 65% or more |

**The bar:** a plan needs a trend **and** at least **30%** confidence (the `min_confidence_for_trade`
setting), which is 5 of 16 points.

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
- **Time horizon** is labelled "1–4 weeks". It's a label only: there's no time-based exit yet.

---

## 7. The paper-trading engine

`backend/app/portfolio/engine.py`, which is what makes every statistic real.

### Opening a position: the checks
- **Refused** if:
  - **the stock's market is closed.** US stocks only fill during the session (09:30–16:00 New York
    time on trading days, 13:00 on early-close days; §8). Off-hours the only price is the last
    close, which nobody could actually trade at. Crypto never closes. This check sits inside the
    engine, so every way a position opens obeys it: auto-execute, the Execute button, auto-scan,
    and later the watchers and the backtest. (The engine's clock can be swapped for a simulated
    one, which the backtest will use.)
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
  recovered isn't missed. **The entry day's bar is skipped.**
- A position closes **fully** at whichever comes first, **stop** or **TP1**. TP2 is for information
  only.
- **If one day touches both** the stop and TP1, it's counted as a **stop**. A daily bar can't show
  which came first, so the engine takes the cautious answer.
- **Gaps:** a day that opens past the stop fills at the **open** (worse). A day that opens past the
  target fills at the **open** (better).
- Stop exits pay slippage (a market order). Target exits don't (a resting limit order).
- A **manual close** from the Portfolio page closes at the current market price.

### Statistics
Portfolio value, total return (including open positions at current prices), win rate, total closed
trades, **Avg R** (the average result per closed trade, in R), active positions and cash. The
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
| **Market-open redo** | Checked every 15 minutes through the US session; acts from **09:45 New York time** on trading days | Always |
| **Exit check** | Every **15 minutes** (a setting), only during US hours plus 30 minutes, unless crypto is held | Always |
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
  tool switched off**, so it can only read what the app sends it.
- **OpenRouter, OrcaRouter, OpenAI, Gemini:** with your own key.

### What the AI writes
1. **AI take:** a short narrative on each tradeable plan.
2. **AI Summary:** on the research panel.
3. **AI Insight:** a short chart explanation on the Analysis page.

It only explains numbers the rules already computed. If the AI fails or is slow, the app shows the
rule-based text instead; a request never fails because of the AI.

### The AI Trading Overlay (off by default)
- **What it is:** an independent second opinion on **every** evaluation, including rejected ones. It's
  one extra AI call per stock. The AI sees the same data and returns:
  - its **stance** (bullish, bearish or neutral),
  - a **trade verdict** ("take" or "pass"),
  - its **conviction %**, its reasoning, and its own reading of the news.
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

---

## 10. Telegram notifications

Needs a bot token and a chat ID in Settings, and the Settings page has a test button.
- **Every tradeable plan:** symbol, direction, entry, stop, TP1, confidence, and whether it was
  auto-executed (or why not).
- **Provider down or back up:** market data, AI or Finnhub, sent only when the state changes.

---

## 11. Where the data comes from

### Sources, tried in this order

For each kind of data, the app asks the first source that offers it. If that fails, it asks the next.

| Order | Source | Key needed? | What the app gets from it |
|---|---|---|---|
| 1 (only when enabled) | **Finnhub** | Free key | Quotes, company profile, news, earnings dates and estimates |
| 2 | **Yahoo Finance** (`yfinance`) | No | Everything except insider trades: daily, weekly and intraday prices; quotes; company info; financials; news; earnings dates, estimates and history; options |
| 3 | **Nasdaq** | No | Quotes, daily prices and company info, when Yahoo fails |
| 4 | **Stooq** | No | Daily prices only; the last fallback |
| 5 | **SEC EDGAR** | No (it needs a contact label; see §14) | **Insider trades** (Form 4): the last 90 days, up to 12 filings per stock |

**Other sources:**
- **Stock logos:** Elbstream (by ticker), with coloured letters when there's no logo.
- **Crypto logos:** the `spothq/cryptocurrency-icons` set, via jsDelivr.
- **Macro calendar:** a hand-kept table of 2026 Fed decisions, CPI and jobs reports, checked against
  federalreserve.gov and bls.gov.
- **Telegram:** for messages.
- **The AI providers** from §9.

**Credits and licenses:** the terms for these sources, and for any code copied from other open-source
projects, are in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

### When a source fails
- If **every** source fails, the app answers "No data available" (HTTP 502). It never invents numbers.
- For **display**, the app can show the **last good value** it received. For **decisions** (the exit
  check and the re-quote at execution) it never uses old values; that part fails safely instead.

### Cache: how long answers are reused
Answers are kept in memory, so refreshing a page doesn't call the sources again:

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
- The cache is **emptied on every backend restart**.

### The stock list
- `backend/data/sp500.csv`: **64 symbols** (BTC-USD, ETH-USD and SOL-USD at the top, then large US
  stocks), with columns `symbol`, `name` and `sector`.
- The **scan size** setting (50) takes the first *N* symbols.
- The **test filter** `STRATEGEIA_DEV_TICKERS` narrows *every* screen to a few symbols. It's set to
  **NVDA and AAPL** in `docker-compose.yml`, which survives Docker rebuilds.

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

**Today this is the foundation only:** the table and its rules exist, but no source writes to it yet.
The planned sources are the news and fundamentals archive, insider trades, company
announcements (8-K), FINRA short selling, Fed speeches, posts, Congress trades and big funds' holdings, and the watchers that collect them. Each new signal is backtested on these dates before
it may earn points.

---

## 12. Settings reference

### In the app (Settings page, saved to `runtime/settings.json`)

| Setting | Default | What it does |
|---|---|---|
| AI provider | none | Which AI writes the narratives (§9) |
| Model and key per provider | per provider | For example OpenRouter `anthropic/claude-3.5-haiku`, OpenAI `gpt-4o-mini`, Gemini `gemini-1.5-flash`, OrcaRouter `orcarouter/auto` |
| Finnhub enabled, and key | off | Adds Finnhub as the first source for what it offers |
| AI Trading Overlay | off | The second opinion (§9) |
| Count disagreement in the confidence score | on | The overlay can cost 0 to −3 points |
| Overlay objection action | cancel | cancel, hold or none (§9) |
| Minimum confidence for a trade | 30% | The trade / no-trade bar |
| Telegram bot token, and chat ID | empty | Messages (§10) |
| Scan universe size | 50 | How many symbols from the list to scan |
| Starting cash | $100,000 | Only applies to a fresh or reset account |
| Default risk per trade | 1% | Sets the share count (§6) |
| Exit-check interval | 15 minutes | How often positions are checked |
| Slippage | 5 bps | The cost added to market fills (entries and stops) |
| Commission per trade | $0 | — |
| Auto-execute trade plans | on | Opens a position as soon as a plan is made, if the market is open (otherwise the plan is redone at the next open, §8) |
| Unattended auto-scan | off | The 3-times-a-day loop (§8) |
| Max open positions | 5 | — |
| Max positions per sector | 2 | — |
| Max position, as a % of average daily volume | 1% | — |

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
| `STRATEGEIA_DEV_TICKERS` | unset | The test filter (§11) |
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
- **Closing by hand isn't tied to market hours.** Opening is (§7), but a manual close at night fills
  at the last price, which nobody could trade at either.
- **One-off market closures** (a national day of mourning, a storm) must be added to the calendar by
  hand when announced (§8).
- **Exits:** a position closes fully at the stop or TP1. There's no partial exit, no trailing stop and
  no time limit.
- **One day touching both levels** always counts as a stop (the cautious choice).
- **A position's first day isn't exit-checked.** The exit check skips the whole daily bar a position
  opened on. Now that US stocks only open during the session, a stop touched later on the entry day
  is missed if the next day's prices stay above it.
- **Slippage is a flat 5 bps.** It doesn't grow for thin stocks or big orders.
- **Intraday charts (1D, 1W)** only come from Yahoo, with no fallback, and their time axis is in UTC.
- **The macro calendar covers 2026 only.** It needs extending before the year runs out.
- **The cache is in memory,** so it's lost on every restart.
- **Yahoo can temporarily block** heavy use.
- **The SEC contact** is still the made-up address, by choice for now. The SEC asks for a
  real contact, so set a separate email (not your personal one) as `SEC_EDGAR_USER_AGENT` before the
  S&P 500 backtest run (§12). Docker passes it in from the `.env` file next to `docker-compose.yml`.
- **The first plan for a stock** can take about 20 seconds on a cold cache (SEC and earnings
  downloads).
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
- Two optional values come from a gitignored `.env` file next to `docker-compose.yml`:
  `CLAUDE_CODE_OAUTH_TOKEN` (the Claude CLI) and `SEC_EDGAR_USER_AGENT` (the SEC contact, §12).
  `backend/.env` isn't copied into the image.

**Where things are stored**

| Folder | What's in it | Survives a Docker rebuild? |
|---|---|---|
| `backend/data/` | The stock list (`sp500.csv`), built into the image | No: it's rebuilt from the repo |
| `backend/runtime/` (in Docker, the `backend_runtime` volume) | The database (`strategeia.db`: plans, positions, equity points, account, the queue of plans waiting to be redone at the market open, and the dated facts table `knownfact` from §11, still empty until its first source is built), `settings.json`, and the generated password, API key and session secret | **Yes** |

---

## 16. API endpoints

Every route except login, auth status and health needs you logged in (or an API key).

| Method and path | What it does |
|---|---|
| `POST /api/auth/login`, `POST /api/auth/logout`, `GET /api/auth/status` | Log in, log out, check the session |
| `GET /api/health` | "Is the backend up?" |
| `GET /api/dashboard/summary` | Everything the Dashboard shows |
| `GET /api/scan`, `GET /api/universe` | Scan results; the stock list |
| `POST /api/scan/auto-trade` | Runs the evaluation loop now (60-second cooldown) |
| `GET /api/analysis/{symbol}?range=` | Chart data and indicators |
| `GET /api/research/{symbol}` | The research panel data |
| `POST /api/trade-plans/generate`, `GET /api/trade-plans`, `GET /api/trade-plans/{id}` | Make a plan, list plans, get one plan |
| `GET /api/portfolio/positions`, `POST /api/portfolio/positions` | List positions; open a position from a plan (refused with a 400 while the market is closed, or while the plan waits for its redo) |
| `POST /api/portfolio/positions/{id}/close` | Close at the market |
| `GET /api/portfolio/stats`, `GET /api/portfolio/equity-curve` | Statistics; the equity curve |
| `POST /api/portfolio/reset` | Reset the paper account |
| `GET /api/settings`, `PUT /api/settings`, `GET /api/settings/status`, `POST /api/settings/test-connection` | Read or save settings, check status, test a provider |
| `GET /api/market/session` | The US market right now: its state (open, pre-market, after hours, closed, holiday), the next open and close (1:00 pm early closes included), and the holiday's name. The Dashboard badge and the Execute button read it. |
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
| **Slippage / bps** | The small cost of filling at a worse price. 1 bps = 0.01%. |
| **VIX** | The market's "fear gauge" |
| **SPY** | The fund that tracks the S&P 500, used as "the market" |
| **Put/call ratio** | Bets on a fall vs bets on a rise in the options market |
| **Implied move** | The size of move the options market expects |
| **Form 4** | The SEC filing an insider must submit within 2 business days of trading their company's stock |
| **Drawdown** | The biggest fall from a peak |
| **Known at** | The moment a piece of information became public. A backtest may only use facts known before its simulated moment (§11). |
| **Look-ahead** | A backtest mistake: using information that wasn't public yet at the simulated moment, which makes results look better than they could have been |
| **Bollinger bands, MACD, VWAP** | Common chart indicators. VWAP is only shown on intraday ranges. |
