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
  provider API keys) as a named volume so it survives container restarts/redeploys.
- `frontend` on port 5173, served by nginx.

**For a real public deployment**, edit `docker-compose.yml` before building:
- `frontend.build.args.VITE_API_BASE_URL` → your backend's public URL (baked in at build
  time; Vite env vars aren't runtime-configurable, so this requires a rebuild if it
  changes)
- `backend.environment.CORS_ORIGINS` → your frontend's public URL (otherwise the browser
  will block API calls with a CORS error)

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

45 tests cover the technical-analysis math, risk/position-sizing formulas, scanner
scoring, the paper-trading engine (open → mark-to-market → close, with real assertions
on realized P&L/R), data-provider fallback behavior, and the LLM provider abstraction.

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

notes/              build log in Obsidian-flavored markdown (Decisions/Issues/Progress) —
                    not read by the app, just a record of why things are the way they are
```

## Known limitations (v1)

- Paper-trading exit rule: a position closes fully at whichever of stop-loss or TP1 hits
  first (checked against each bar's high/low). TP2 is informational only — no partial
  scale-out yet.
- The bundled `backend/data/sp500.csv` is a curated ~60-symbol large-cap subset, not the
  full S&P 500 — expand the CSV if you want a bigger scan universe.
- Free-tier data can rate-limit or go stale under heavy use; the composite provider falls
  back automatically but surfaces an error rather than fabricating data if everything
  fails.
