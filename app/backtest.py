"""Historical backtest. Signal at the close of bar t -> fill at the close of bar t (+slippage, +fee).
Same Portfolio class as the live engine; only the data source differs."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import numpy as np
import pandas as pd

from .data import bars_per_year, fmt_ts, to_utc
from .portfolio import Fill, Portfolio
from .strategy import Strategy


def _num(x) -> Optional[float]:
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    return None if (math.isnan(f) or math.isinf(f)) else f


@dataclass
class BacktestResult:
    equity: pd.Series
    price: pd.Series
    bh: pd.Series
    pos: pd.Series
    fills: List[Fill]
    trades: List[Dict[str, Any]]
    metrics: Dict[str, Any]

    def payload(self) -> Dict[str, Any]:
        r = lambda s: [round(float(v), 6) for v in s]
        return {
            "metrics": self.metrics,
            "series": {"t": [fmt_ts(t) for t in self.equity.index], "equity": r(self.equity),
                       "bh": r(self.bh), "price": r(self.price), "pos": [int(v) for v in self.pos]},
            "fills": [{"t": fmt_ts(f.ts), "side": f.side, "price": round(f.price, 6),
                       "qty": round(f.qty, 8), "fee": round(f.fee, 4), "target_pos": f.target_pos}
                      for f in self.fills],
            "trades": [{"entry_t": fmt_ts(t["entry_ts"]), "exit_t": fmt_ts(t["exit_ts"]), "side": t["side"],
                        "entry_price": round(t["entry_price"], 6), "exit_price": round(t["exit_price"], 6),
                        "return_pct": round(t["return_pct"], 6)} for t in self.trades],
        }


def max_drawdown(curve: pd.Series) -> float:
    return float((curve / curve.cummax() - 1).min())


def compute_metrics(equity: pd.Series, bh: pd.Series, pos: pd.Series, pf: Portfolio, ann: float) -> Dict[str, Any]:
    cash = pf.initial_cash
    curve = pd.concat([pd.Series([cash]), equity.reset_index(drop=True)], ignore_index=True)
    rets = curve.pct_change().dropna()
    total = float(equity.iloc[-1] / cash - 1)
    years = len(rets) / ann
    cagr = None
    if years >= 0.25 and total > -1:
        cagr = (1 + total) ** (1 / years) - 1
    sd = float(rets.std())
    sharpe = float(rets.mean() / sd * np.sqrt(ann)) if sd > 0 else 0.0
    down = rets[rets < 0]
    dsd = float(np.sqrt((down ** 2).mean())) if len(down) else 0.0
    sortino = float(rets.mean() / dsd * np.sqrt(ann)) if dsd > 0 else 0.0
    mdd = max_drawdown(curve)
    tr = [t["return_pct"] for t in pf.trades]
    wins = [x for x in tr if x > 0]
    losses = [x for x in tr if x <= 0]
    pf_ratio = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else None
    warnings = []
    if len(tr) < 30:
        warnings.append(f"Only {len(tr)} closed trades. Under ~30 trades the statistics are noise, not evidence.")
    if years < 1:
        warnings.append(f"Only {years:.2f} years of data: CAGR/Sharpe are not annualised-reliable. "
                        "Test across several regimes and out-of-sample.")
    if pf.fees_paid > 0 and total > 0 and pf.fees_paid / cash > total * 0.5:
        warnings.append("Fees and slippage consume over half of gross profit: the edge is fragile.")
    m = {
        "total_return": total, "cagr": cagr, "ann_vol": sd * float(np.sqrt(ann)), "sharpe": sharpe,
        "sortino": sortino, "max_drawdown": mdd, "calmar": (cagr / abs(mdd)) if cagr is not None and mdd < 0 else None,
        "exposure": float((pos != 0).mean()), "n_trades": len(tr),
        "win_rate": (len(wins) / len(tr)) if tr else None,
        "avg_trade": float(np.mean(tr)) if tr else None, "profit_factor": pf_ratio,
        "fees_paid": pf.fees_paid, "final_equity": float(equity.iloc[-1]), "initial_cash": cash,
        "bh_return": float(bh.iloc[-1] / cash - 1), "bh_max_drawdown": max_drawdown(bh),
        "bars": int(len(equity)), "years": years, "bars_per_year": ann,
        "start": fmt_ts(equity.index[0]), "end": fmt_ts(equity.index[-1]), "warnings": warnings,
    }
    return {k: (_num(v) if isinstance(v, (float, np.floating)) or v is None else v) for k, v in m.items()}


def run_backtest(df: pd.DataFrame, strategy: Strategy, symbol: str = "", interval: str = "1d",
                 cash: float = 100_000.0, fee_bps: float = 5.0, slippage_bps: float = 2.0,
                 start_ts=None, ann: Optional[float] = None) -> BacktestResult:
    if len(df) < strategy.min_bars + 5:
        raise ValueError(f"Not enough bars ({len(df)}) for this strategy; it needs at least {strategy.min_bars + 5}.")
    sig = strategy.signals(df)
    if start_ts is not None:
        mask = df.index >= to_utc(start_ts)
    else:  # skip warm-up so the benchmark and the strategy start on the same bar
        mask = np.arange(len(df)) >= strategy.min_bars
    ev_idx = df.index[mask]
    if len(ev_idx) < 2:
        raise ValueError("Evaluation window has fewer than 2 bars.")
    prices = df["close"][mask].to_numpy(dtype=float)
    targets = sig[mask].to_numpy(dtype=int)
    pf = Portfolio(cash, fee_bps, slippage_bps)
    fills, eq = [], []
    for ts, px, tgt in zip(ev_idx, prices, targets):
        f = pf.rebalance(ts, float(px), int(tgt))
        if f:
            fills.append(f)
        eq.append(pf.equity(float(px)))
    equity = pd.Series(eq, index=ev_idx)
    price = pd.Series(prices, index=ev_idx)
    bh = cash * price / price.iloc[0]
    pos = pd.Series(targets, index=ev_idx)
    ann = ann or bars_per_year(symbol or "X", interval)
    return BacktestResult(equity, price, bh, pos, fills, pf.trades, compute_metrics(equity, bh, pos, pf, ann))
