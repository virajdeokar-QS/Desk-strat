# Understanding Strategy Desk

Read this until you can explain every section without looking. If you cannot explain a part of this project, do not claim it in an interview.

## 1. The one-minute pitch

"I built a research and paper-trading platform. A strategy is a function from bar history to a target position. The same function and the same execution model run in two places: a historical backtester, and a live engine that polls market data and paper-trades. Everything is stored in a database, and a reconciliation step replays the live session through the backtester to check that the two agree. The main thing I cared about was making live and backtest comparable, because most backtests fail by quietly not being what runs in production."

## 2. Architecture

```
            Yahoo Finance / synthetic
                       |
                  app/data.py  --- drop_forming() removes the unfinished bar
                   |        |
        history    |        |   latest window every N seconds
                   v        v
        app/backtest.py   app/live.py (thread per session)
                   \        /
                    v      v
             app/strategy.py   signals(df) -> target position per bar (-1, 0, +1)
                    |
             app/portfolio.py  rebalance(): fees, slippage, shorting, trade log
                    |
                 app/db.py  (SQLite / Postgres)
   bars | backtest_runs | live_sessions | signals | fills | equity_points | logs
                    |
              app/main.py (FastAPI /api)  <--- app/static (UI + log tape)
                    ^
             app/reconcile.py: replay stored bars through the backtester, compare
```

## 3. The ideas you must be able to explain

**Target-position strategies.** A strategy does not place orders; it says what position it wants at each bar close (+1 long, 0 flat, -1 short). The `Portfolio` turns a change in target into a trade. This keeps strategies pure and testable.

**Look-ahead bias.** Using information that was not available at decision time (for example, trading on a bar's close while that bar is still forming). Defences here: (a) rolling indicators only look backwards; (b) `test_signals_have_no_lookahead` checks that the signal at bar *t* computed on data truncated at *t* equals the one computed with the future present; (c) the live engine drops the still-forming bar before computing signals.

**Execution timing assumption.** Signal at the close of bar *t* is filled at the close of bar *t* plus slippage. That is a simplification: in reality you see the close and trade a moment later. The live engine fills at the latest price after the bar closes, and the reconciliation reports how far that is from the bar close in basis points. That measured gap is a real estimate of your timing cost.

**Costs.** Fee (bps per side) and slippage (bps against you on every fill) are charged in both backtest and live. `test_costs_reduce_equity` checks that costs move results the right way. Many strategies that look good with zero cost lose money with realistic cost, especially at short bar sizes.

**Metrics.**
- *Sharpe*: mean bar return / std of bar return x sqrt(bars per year). Bars per year depends on the market (crypto 24/7 vs equity sessions), see `bars_per_year()`.
- *Sortino*: like Sharpe but only penalises downside volatility.
- *Max drawdown*: worst peak-to-trough fall of the equity curve.
- *Profit factor*: gross wins / gross losses.
- *Exposure*: fraction of bars in a position. A strategy that is in the market 20% of the time and matches buy-and-hold has done something different from one that is in 100% of the time.
- Buy-and-hold is shown on every chart because "+30%" means nothing if the asset went up 60%.

**Paper trading vs real trading.** The engine never sends orders. It simulates fills at the latest price plus slippage. Real fills involve the order book, spread, queue position, partial fills and market impact. Paper results are an upper bound on what you would get live.

**Live vs backtest reconciliation.** `reconcile.py` loads the closed bars the live engine stored, runs the backtester from the first live signal, and compares (1) the target position at each bar, (2) fill price vs bar close, (3) total return over the window. Signal mismatches mean a bug or a data revision. Return differences are expected because live equity is marked to the forming bar.

**State-dependent strategies.** The z-score strategy keeps state (it is long until the z-score recovers). In live, the strategy sees a rolling window of recent bars, not the whole history, so its state can differ from a backtest that started earlier. The state converges after a window or two, but it is a real source of live/backtest divergence worth knowing about.

**Idempotency.** The live loop polls every N seconds but only acts when a **new** closed bar appears (`last_bar_ts`). Polling twice in the same bar produces one signal and at most one fill. Tested in `test_live_engine_ticks_are_idempotent_and_reconcile`.

**Persistence and audit trail.** Every bar used is stored (`bars`), so a run can be replayed later. Signals, fills and equity points are stored per session. Logs go to the console and to the `logs` table, which is what the frontend log tape reads.

## 4. Design decisions and the honest trade-offs

| Decision | Why | Cost |
|---|---|---|
| Shared `Portfolio` and strategy code for backtest and live | Results are comparable | Backtest loop is Python, slower than a vectorised one (fine for thousands of bars) |
| SQLite default, Postgres optional | Zero setup for reviewers | SQLite is single-writer; WAL mode is enabled to reduce lock contention |
| Thread per live session | Simple, easy to explain | Not horizontally scalable; no resume after restart |
| Polling bars, not streaming ticks | Works with free data, simple | Decisions only at bar close; Yahoo is delayed and unofficial |
| Plain HTML/JS frontend | No build step, cannot break at 11pm | Less impressive than React; fine for this scope |
| Only closed bars drive decisions | Avoids look-ahead and repainting | Reacts up to one bar late |

## 5. What the results do and do not mean

Be blunt about this in an interview, it earns more trust than a good-looking number.

- The strategies are textbook ones (moving-average trend, z-score mean reversion). They are here to demonstrate the **infrastructure**, not to claim alpha. Liquid assets after costs rarely reward simple retail strategies.
- A backtest on a few months of data is not evidence. The app prints a warning when there are fewer than 30 trades or less than a year of data.
- One day of live paper trading proves that the pipeline works. It says nothing about profitability.
- Parameters (20/50, window 40, entry 2.0) are defaults, not optimised. If you tune them on a window and report that window's result, that is overfitting. The honest approach is train/test splits or walk-forward testing.
- Never say "my strategy makes X% per year". Say "the project measures X, here is how I would validate it, here is what I know is missing".

## 6. Interview questions you should expect

**1. How do you avoid look-ahead bias?**
Signals only use rows up to the current bar; there is a unit test that truncates the history and checks the signal does not change. In live, the forming bar is removed before computing signals.

**2. Why do backtest and live share code?**
So that differences between them come from data and execution, not from two implementations drifting apart. The reconciliation view measures exactly those differences.

**3. What would make live results differ from the backtest?**
Fill price vs the assumed bar close, data revisions or delays, missed bars when the server is down, state-dependent strategies starting from a different window, fees or spread higher than modelled, and in real trading, market impact and partial fills.

**4. How do you model transaction costs?**
A fee in bps per side plus slippage in bps against every fill. Both are inputs to the UI. The metrics also warn when costs eat over half the gross profit.

**5. Your Sharpe is X. Is it any good?**
Not on its own. How many trades, how many years, how many parameter combinations did I try, what is the out-of-sample number, and what is it after costs? A Sharpe from 60 days of data is mostly noise.

**6. How would you test for overfitting?**
Hold out data the parameters never saw, walk-forward optimisation, parameter-stability plots (does performance collapse when 20 becomes 22?), and correcting for the number of trials (deflated Sharpe).

**7. Why bars and not ticks?**
Free data gives bars. Tick-level work needs order-book data and a different simulation (queue models, latency). I would say that is the next step for execution research, not for signal research at this horizon.

**8. How does the live engine handle failures?**
Each tick is wrapped in a try/except: errors are logged and stored on the session, and the loop continues. If the process dies, sessions are marked `interrupted` on startup. They do not resume, because state is in memory. A production system would persist positions and reconstruct from fills.

**9. How would you scale this?**
Move live engines to worker processes with a queue, persist portfolio state transactionally, use Postgres, stream data instead of polling, add per-strategy risk limits, and put metrics and alerting around the engine.

**10. What is the biggest weakness of this project?**
Fills are modelled, not observed. Everything downstream (live P&L, Sharpe) inherits that. The honest next step is to compare against a broker's paper account and measure fill quality.

**11. Why is the SQLite database safe with threads?**
`check_same_thread=False`, WAL mode, a 30-second busy timeout, and each unit of work uses its own short-lived session. Logging is emitted after the DB transaction closes so the logger never fights its own writer.

**12. What does Sharpe use as bars per year?**
Crypto: 365 x 1440 / bar minutes. US equities: 252 x 390 / bar minutes (252 for daily). NSE: 248 x 375 / bar minutes. Wrong annualisation inflates or deflates Sharpe by a factor of sqrt of the ratio.

**13. What is slippage vs fee?**
Fee is what the venue or broker charges. Slippage is the difference between the price you wanted and the price you got, caused by spread, latency and impact. Both are costs, they come from different places.

**14. Why a target position instead of orders?**
It makes strategies stateless with respect to execution and easy to test. The execution layer decides how to reach the target.

**15. How would you add a new strategy?**
Add a dataclass in `app/strategy.py` with parameters, `min_bars`, `validate()` and `signals(df)`, register it in `REGISTRY`. The API and the UI pick up its parameters automatically. Add it to the no-look-ahead test.

## 7. Ideas to extend it (pick one to discuss, do not claim them)

- Walk-forward optimisation and parameter heatmaps.
- Volatility-targeted position sizing and a max-drawdown kill switch.
- A second asset and a simple pairs strategy.
- Persist live portfolio state so sessions resume after a restart.
- Compare paper fills with a broker paper account (Alpaca, Interactive Brokers).
- Replace polling with a websocket feed.

## 8. Files to know cold

| File | Be able to explain |
|---|---|
| `app/portfolio.py` | How a target change becomes a trade, why slippage and fees are applied there |
| `app/strategy.py` | The no-look-ahead contract, the z-score state machine |
| `app/live.py` | `tick()`: fetch, drop forming bar, signal, fill, persist, log; why it is idempotent |
| `app/reconcile.py` | What is compared and why differences are expected |
| `tests/test_core.py` | What each test protects against |
