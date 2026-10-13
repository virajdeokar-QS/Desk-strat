"""One execution model used by BOTH the backtester and the live paper engine.
If this lived in two places, live and backtest results could never be compared honestly."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass
class Fill:
    ts: Any
    side: str          # BUY | SELL
    qty: float
    price: float       # after slippage
    fee: float
    target_pos: int


class Portfolio:
    """Single-asset, 1x leverage, always sized to 100% of current equity in the target direction."""

    def __init__(self, cash: float, fee_bps: float = 5.0, slippage_bps: float = 2.0):
        self.initial_cash = float(cash)
        self.cash = float(cash)
        self.qty = 0.0
        self.side = 0
        self.fee_bps = float(fee_bps)
        self.slippage_bps = float(slippage_bps)
        self.fees_paid = 0.0
        self.trades: List[Dict[str, Any]] = []
        self._entry: Optional[Dict[str, Any]] = None

    def equity(self, price: float) -> float:
        return self.cash + self.qty * price

    def rebalance(self, ts, price: float, target: int) -> Optional[Fill]:
        if target not in (-1, 0, 1):
            raise ValueError("target position must be -1, 0 or 1")
        if price <= 0:
            raise ValueError("price must be positive")
        eq = self.equity(price)
        if eq <= 0:
            target = 0  # wiped out: flatten, never lever up from negative equity
        if target == self.side:
            return None
        target_qty = target * eq / price
        delta = target_qty - self.qty
        slip = self.slippage_bps / 1e4
        fill_px = price * (1 + slip) if delta > 0 else price * (1 - slip)
        fee = abs(delta) * fill_px * self.fee_bps / 1e4
        self.cash -= delta * fill_px + fee
        self.qty = target_qty
        self.fees_paid += fee
        if self.side != 0 and self._entry is not None:  # close (or flip) the open trade
            e = self._entry
            gross = self.side * (fill_px / e["price"] - 1)
            self.trades.append({
                "entry_ts": e["ts"], "exit_ts": ts, "side": "LONG" if self.side > 0 else "SHORT",
                "entry_price": e["price"], "exit_price": fill_px,
                "return_pct": gross - 2 * self.fee_bps / 1e4,
            })
            self._entry = None
        if target != 0:
            self._entry = {"ts": ts, "price": fill_px}
        self.side = target
        return Fill(ts, "BUY" if delta > 0 else "SELL", abs(delta), fill_px, fee, target)
