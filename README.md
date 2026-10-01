# Strategeia

A personal, paper-trading stock dashboard: market scanner → chart/technical analysis →
fundamentals & news research → risk/position sizing → an AI-narrated trade plan you can
execute as a simulated position. Every stat on the dashboard (win rate, return, R:R) is
computed from real simulated trade history — nothing is hardcoded.

**This is not investment advice, and it does not place real trades.** v1 is analysis +
paper trading only. Market data comes from free tiers (`yfinance`, `stooq`, optional
Finnhub) which can be delayed or rate-limited — don't rely on it for time-sensitive
decisions.

## Stack

- **Backend**: Python 3.14, FastAPI, SQLite (via SQLModel), APScheduler
- **Frontend**: React 19 + TypeScript (Vite), react-query, `lightweight-charts` (candles),
  Chart.js (bar/line charts)
- **Data**: `yfinance` + `stooq` (free, no signup); Finnhub (free tier, optional, needs a
  free API key) for enhanced quotes/news/earnings
- **AI narratives**: pluggable — None (rule-based text, default), Claude Code CLI (uses
  your existing Claude Code login, no key), OpenRouter, OpenAI, or Gemini (bring your own
  key, set in Settings)

## Running locally (Windows / PowerShell)

**Backend:**

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
.venv\Scripts\python -m uvicorn app.main:app --reload --port 8000
```

Swagger UI at http://localhost:8000/docs.

**Frontend** (separate terminal):

```powershell
cd frontend
npm install
npm run dev
```

Dashboard at http://localhost:5173.

Both read config from `.env` files (`backend/.env.example` and `frontend/.env.example`
have the defaults — copy them to `.env` if you need to change anything; neither is
required to get started).

## Running with Docker (for a VPS / 24-7 deployment)

```bash
docker compose up --build -d
```

This builds and runs both services:
- `backend` on port 8000, with `runtime/` (the SQLite db + `settings.json`, holding your
  provider API keys, plus `cache.db`, the saved market-data cache that lets a redeploy start warm
  instead of refetching everything, and `history.db` if you load price history with
  `scripts/preload_history.py`) as a named volume so it survives container restarts/redeploys.
- `frontend` on port 5173, served by nginx.

**Auth**: the dashboard has its own login screen — one user, one username+password, no
signup (`api/routers/auth.py`). Set `AUTH_USERNAME` (defaults to `admin` if left unset)
and `AUTH_PASSWORD` in the backend's `environment:`; leave the password unset and the
backend generates a random one on first start and logs it prominently to
`runtime/auth_password.txt`. Three wrong attempts in a row locks out further tries for 15
minutes (per client — one attacker hammering the login can't lock out the real operator).
Every API route also still accepts a real `X-API-Key` header as an alternative
(`InfraSettings.api_shared_secret`, same auto-generate-if-unset behavior into
`runtime/api_key.txt`) for scripts/Swagger/anything with no browser to hold a session
cookie in. This compose file keeps the previous zero-config behavior via
`ALLOW_UNAUTHENTICATED_API=true` in the backend's `environment:` block (no login screen
at all, meant for local/LAN use). **For a real public deployment**, edit
`docker-compose.yml` before building:
- `backend.environment.ALLOW_UNAUTHENTICATED_API` → `false`
- `backend.environment.AUTH_USERNAME` → your own username (add this line — it isn't
  present by default; leaving it unset uses `admin`)
- `backend.environment.AUTH_PASSWORD` → your own password (or leave unset and read the
  auto-generated one from `runtime/auth_password.txt` after first start)
- `frontend.build.args.VITE_API_BASE_URL` → your backend's public URL (baked in at
  build time; Vite env vars aren't runtime-configurable, so this requires a rebuild if
  it changes)
- `backend.environment.CORS_ORIGINS` → your frontend's public URL (otherwise the browser
  will block API calls with a CORS error)
- Only needed if you also want script/Swagger access: `backend.environment.API_SHARED_SECRET`
  → a real generated value, and `frontend.build.args.VITE_API_SHARED_SECRET` → the *same*
  value (a mismatch locks the frontend's fallback key out with 401s, but not the login
  screen, which doesn't use it)

This app has no built-in TLS termination or reverse proxy — put it behind whatever you
already run for that (nginx/Caddy/Traefik, or your platform's own ingress), or bind
`HOST` to a VPN/Tailscale-only interface instead of a public one.

**SEC contact (insider trades)**: the backend downloads insider-trade filings (Form 4)
from SEC EDGAR, and the SEC asks every program that does that to say who runs it, with a
contact address in the request's User-Agent. `backend/.env` never reaches the container
(it isn't copied into the image), so for Docker put the value in a gitignored `.env` file
next to `docker-compose.yml` (the same file that holds `CLAUDE_CODE_OAUTH_TOKEN`), then
run `docker compose up -d` to recreate the backend with it:

```
SEC_EDGAR_USER_AGENT="Strategeia/1.0 (personal research; you@example.com)"
```

Plain ASCII only, and no link: the SEC answers `403 Forbidden` to a user-agent with a URL
in it, which quietly switches insider scoring off (the backend logs a warning at startup
if it spots one). Left unset or blank, the built-in made-up placeholder
(`contact@strategeia.example`) is sent instead. That's fine while testing with a couple of tickers, where the SEC traffic is tiny. Before the S&P 500 backtest run, whose
one-time insider download is the heavy part, set a separate address that isn't your
personal one: a new free account or a relay alias (a Gmail `+something` address still
shows your real one). For a local, non-Docker run, the same line goes in `backend/.env`.

## Configuring AI / data providers

Everything works out of the box with zero configuration (free data, rule-based text).
To add an AI provider or Finnhub, open the app's **Settings** page — no need to edit
files or restart. Keys are stored in `backend/runtime/settings.json` (gitignored,
never committed, never echoed back by the API — the Settings GET only reports whether a
key is set, not its value).

The **Claude Code CLI** provider is a novelty option: it shells out to your local
`claude -p` instead of calling a metered API, so it's free if you already have Claude
Code — but it has no SLA, meaningfully higher latency than a direct API call, and
requires the CLI installed and logged in on whatever machine runs the backend. It's not
the recommended default for a long-running deployment; OpenRouter is, if you have credit
there.

## Testing

```powershell
cd backend
.venv\Scripts\python -m pytest tests -v
```

The backend tests cover the technical-analysis math, risk/position-sizing formulas, scanner
scoring, the paper-trading engine (open → mark-to-market → close, with real assertions
on realized P&L/R), data-provider fallback behavior, the LLM provider abstraction, and
the auth/login/lockout behavior above.

## Project layout

```
backend/app/
  data_providers/   yfinance + stooq + optional Finnhub, with fallback + caching
  analysis/         EMA/RSI/support-resistance, trend/momentum classification, scanner scoring
  risk/             position sizing + target derivation
  llm_providers/    pluggable AI narrative layer (none/claude_code_cli/openrouter/openai/gemini)
  portfolio/        paper-trading engine + SQLite models + stats
  api/routers/      REST endpoints per screen
  services/         orchestration combining the above per screen

frontend/src/
  pages/            one per dashboard screen
  components/chart/ CandlestickChart (lightweight-charts) + RevenueChart/EquityCurveChart (Chart.js)
  api/               fetch client, TS types, react-query hooks

scripts/            verify.py (tests + type-check + lint in one command) and ui_check.py
                    (screenshots the app in a real browser against a throwaway database)
```

## Known limitations (v1)

- Paper-trading exit rule: a position closes fully at whichever of stop-loss or TP1 hits
  first (checked against each bar's high/low). TP2 is informational only — no partial
  scale-out yet. A position that touches neither is closed at the close of its 20th
  trading day (Settings -> Maximum Holding Time; 0 turns it off).
- The bundled `backend/data/sp500.csv` is a curated ~60-symbol large-cap subset, not the
  full S&P 500 — expand the CSV if you want a bigger scan universe.
- Free-tier data can rate-limit or go stale under heavy use; the composite provider falls
  back automatically but surfaces an error rather than fabricating data if everything
  fails.

## More documentation

[FEATURES.md](FEATURES.md) describes what the app does, page by page, how every decision is
scored, and where the data comes from.

## Credits and licenses

Code copied or adapted from other open-source projects, their licenses, and the outside data/logo
services the app uses are recorded in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
