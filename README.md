# Strategy Desk

Backtest a trading strategy on historical data, then run **the same strategy code** as a live **paper-trading** engine against real market data. Every bar, signal, fill, equity point and log line is stored in a database and shown in a web UI. A reconciliation view replays the live session through the backtester and shows where the two disagree.

![tests](https://github.com/YOUR_USER/strategy-desk/actions/workflows/ci.yml/badge.svg)

**No real orders are ever sent.** This is a research and engineering project, not a trading product.

## What is in it

| Part | Where | What it does |
|---|---|---|
| Strategies | `app/strategy.py` | SMA crossover (trend) and z-score mean reversion. The signal at bar *t* uses data up to *t* only (unit-tested). |
| Execution model | `app/portfolio.py` | One `Portfolio` class (fees, slippage, shorting) used by BOTH backtest and live. |
| Backtester | `app/backtest.py` | Sharpe, Sortino, drawdown, win rate, profit factor, exposure, buy-and-hold benchmark, honesty warnings. |
| Live engine | `app/live.py` | One background thread per session: poll data, drop the forming bar, compute the signal, simulate the fill, persist everything. |
| Reconciliation | `app/reconcile.py` | Live signals and fills vs a backtest over the same window. |
| Data | `app/data.py` | Yahoo Finance provider plus a deterministic synthetic provider for offline demos and tests. |
| Database | `app/db.py` | SQLAlchemy 2.0. SQLite by default, Postgres via `DATABASE_URL`. Tables: bars, backtest_runs, live_sessions, signals, fills, equity_points, logs. |
| API | `app/main.py` | FastAPI, JSON under `/api`, interactive docs at `/docs`. |
| Frontend | `app/static/` | Plain HTML/CSS/JS + Chart.js. Backtest view, live view, and an always-visible log tape. |
| Tests | `tests/` | 10 tests: no look-ahead, accounting, cost impact, JSON safety, live idempotency, live-vs-backtest agreement, API end to end. |

## Run it

Python 3.10+.

```bash
python -m venv .venv
source .venv/bin/activate            # Windows: .venv\Scripts\activate
pip install -r requirements-dev.txt
python -m pytest -q                  # expect: 10 passed
uvicorn app.main:app --reload
```

Open http://localhost:8000.

Offline demo without Yahoo: `DATA_SOURCE=synthetic uvicorn app.main:app` (PowerShell: `$env:DATA_SOURCE="synthetic"; uvicorn app.main:app`). Synthetic data has no real edge; it only proves the plumbing.

### Postgres instead of SQLite

```bash
docker compose up --build
```

## Running a live paper session

1. Open **Live paper trading**.
2. Symbol `BTC-USD` (24/7, so you always get fresh bars). Equities only update during their session: US 9:30-16:00 New York, NSE 9:15-15:30 IST.
3. Bar size `5m`, strategy `sma_cross`, then **Start session**.
4. Watch the log tape at the bottom. A decision happens only when a bar **closes**: you will see `New closed bar ... -> target position ...` and, on a position change, a `FILL` line.
5. After at least two closed bars, press **Compare with backtest**. The signal match rate should be 100%. The gap between fill price and bar close is real execution drift, which is the interesting part.

## First-run checklist (do this before you rely on it)

The automated tests run on synthetic data. The Yahoo Finance path (`app/data.py::_yfinance`) was **not** exercised against the real service in the environment this was built in. On your machine:

- [ ] Run a backtest on `BTC-USD`, `1h`, `90d` with data source "Yahoo Finance". If it errors, read the message: it is almost always a symbol, period-limit or rate-limit problem.
- [ ] Start a live session on `BTC-USD` `5m` and confirm a `New closed bar` line appears within five minutes.
- [ ] Open `data/strategy_desk.db` and check that the `bars` table is filling.

Yahoo limits: 1m history <= 7 days, 5m/15m/30m <= 60 days. The data is unofficial and can be delayed or briefly wrong.

## API

| Method | Path | Purpose |
|---|---|---|
| POST | `/api/backtests` | Run and store a backtest |
| GET | `/api/backtests`, `/api/backtests/{id}` | List / load stored runs |
| POST | `/api/live` | Start a live paper session |
| POST | `/api/live/{id}/stop` | Stop it |
| GET | `/api/live`, `/api/live/{id}` | Sessions; one session's state, equity, signals, fills |
| GET | `/api/live/{id}/reconcile` | Live vs backtest comparison |
| GET | `/api/logs?after_id=` | Log tail (used by the tape) |
| GET | `/api/config`, `/api/health` | UI config, liveness |

## Known limitations

- **Paper fills are optimistic.** Real markets give you queue position, partial fills, spread and market impact. Here a fill is "latest price plus a fixed slippage in bps".
- **One asset, 1x leverage, 100% of equity per position.** No portfolio construction, no risk limits, no volatility targeting.
- **Live sessions live in memory.** If the server restarts, running sessions are marked `interrupted`; they do not resume.
- **Bars, not ticks.** Decisions happen at bar close. If the server is down across several bars, only the latest closed bar is acted on.
- **Parameters are not optimised and results are not evidence of edge.** See `docs/UNDERSTANDING.md` for how to talk about this honestly.

## Docs

- `docs/UNDERSTANDING.md`: how it works, why it is built this way, interview questions with answers.
- `docs/DEPLOY_GIT.md`: exact steps to push this to GitHub.
