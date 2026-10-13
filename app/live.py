"""Live PAPER trading engine. Polls real bars, runs the same strategy + Portfolio as the backtest,
records every signal, fill and equity point in the database. No real orders are ever sent."""
from __future__ import annotations

import json
import logging
import threading
from typing import Dict, Optional

from sqlalchemy import select

from .data import INTERVAL_MIN, drop_forming, fetch_bars, now_utc
from .db import (EquityPoint, FillRow, LiveSession, SignalRow, save_bars, session_scope, to_naive_utc, utcnow)
from .portfolio import Portfolio
from .strategy import make_strategy
from . import config

base_log = logging.getLogger("quant.live")


class LiveEngine:
    def __init__(self, session_id: int, cfg: dict):
        self.id = session_id
        self.cfg = cfg
        self.strategy = make_strategy(cfg["strategy"], cfg["params"])
        self.pf = Portfolio(cfg["cash"], cfg["fee_bps"], cfg["slippage_bps"])
        self.last_bar_ts = None
        self.stop_event = threading.Event()
        self.log = logging.LoggerAdapter(base_log, {"session_id": session_id})

    def tick(self) -> None:
        c = self.cfg
        raw = fetch_bars(c["symbol"], c["interval"], source=c["data_source"])
        price = float(raw["close"].iloc[-1])                       # latest traded price (forming bar)
        now = now_utc()
        closed = drop_forming(raw, c["interval"], now)              # decisions use CLOSED bars only
        save_bars(c["symbol"], c["interval"], closed, c["data_source"])
        need = self.strategy.min_bars + 2
        msgs = []
        if len(closed) < need:
            self.log.warning("Warming up: %d closed bars, need %d", len(closed), need)
            return
        sig = self.strategy.signals(closed)
        target, bar_ts = int(sig.iloc[-1]), closed.index[-1]
        bar_close = float(closed["close"].iloc[-1])
        with session_scope() as s:
            if self.last_bar_ts is None or bar_ts > self.last_bar_ts:
                prev = self.pf.side
                s.add(SignalRow(session_id=self.id, bar_ts=to_naive_utc(bar_ts), bar_close=bar_close,
                                live_price=price, target_pos=target, prev_pos=prev))
                msgs.append(("info", f"New closed bar {bar_ts:%Y-%m-%d %H:%M} close={bar_close:.4f} "
                                     f"-> target position {target:+d} (was {prev:+d})"))
                fill = self.pf.rebalance(now, price, target)
                if fill:
                    s.add(FillRow(session_id=self.id, ts=to_naive_utc(now), bar_ts=to_naive_utc(bar_ts),
                                  side=fill.side, qty=fill.qty, price=fill.price, fee=fill.fee,
                                  target_pos=target))
                    msgs.append(("info", f"FILL {fill.side} {fill.qty:.6f} @ {fill.price:.4f} fee={fill.fee:.2f}"))
                self.last_bar_ts = bar_ts
            eq = self.pf.equity(price)
            s.add(EquityPoint(session_id=self.id, ts=to_naive_utc(now), price=price, qty=self.pf.qty,
                              cash=self.pf.cash, equity=eq))
            ls = s.get(LiveSession, self.id)
            if ls:
                ls.last_tick_at = utcnow()
                ls.last_error = None
        for level, text in msgs:
            getattr(self.log, level)(text)

    def _set_status(self, status: Optional[str] = None, error: Optional[str] = None) -> None:
        try:
            with session_scope() as s:
                ls = s.get(LiveSession, self.id)
                if ls:
                    if status:
                        ls.status = status
                        if status != "running":
                            ls.stopped_at = utcnow()
                    if error is not None:
                        ls.last_error = error
        except Exception:
            pass

    def run_loop(self) -> None:
        c = self.cfg
        self.log.info("Session %d started: %s %s %s %s (poll %ss, fee %sbps, slippage %sbps, source=%s)",
                      self.id, c["symbol"], c["interval"], c["strategy"], json.dumps(c["params"]),
                      c["poll_seconds"], c["fee_bps"], c["slippage_bps"], c["data_source"])
        while not self.stop_event.is_set():
            try:
                self.tick()
            except Exception as ex:  # keep the loop alive across network blips
                self.log.error("Tick failed: %s", ex)
                self._set_status(error=str(ex)[:300])
            if self.stop_event.wait(c["poll_seconds"]):
                break
        self._set_status("stopped")
        self.log.info("Session %d stopped", self.id)


class LiveManager:
    def __init__(self) -> None:
        self._engines: Dict[int, tuple] = {}
        self._lock = threading.Lock()

    def start(self, cfg: dict) -> int:
        make_strategy(cfg["strategy"], cfg["params"])  # validate before creating a row
        with session_scope() as s:
            row = LiveSession(symbol=cfg["symbol"], interval=cfg["interval"], strategy=cfg["strategy"],
                              params=json.dumps(cfg["params"]), config=json.dumps(cfg),
                              initial_cash=cfg["cash"], status="running")
            s.add(row)
            s.flush()
            sid = row.id
        engine = LiveEngine(sid, cfg)
        th = threading.Thread(target=engine.run_loop, name=f"live-{sid}", daemon=True)
        with self._lock:
            self._engines[sid] = (engine, th)
        th.start()
        return sid

    def stop(self, sid: int) -> bool:
        with self._lock:
            item = self._engines.get(sid)
        if not item:
            return False
        engine, th = item
        engine.stop_event.set()
        th.join(timeout=8)
        with self._lock:
            if not th.is_alive():
                self._engines.pop(sid, None)
        return True

    def is_running(self, sid: int) -> bool:
        with self._lock:
            item = self._engines.get(sid)
        return bool(item and item[1].is_alive())

    def stop_all(self) -> None:
        for sid in list(self._engines):
            self.stop(sid)


manager = LiveManager()


def mark_interrupted() -> int:
    """Sessions left 'running' by a crash/restart cannot resume (state lives in memory): mark them."""
    with session_scope() as s:
        rows = s.scalars(select(LiveSession).where(LiveSession.status == "running")).all()
        for r in rows:
            r.status, r.stopped_at = "interrupted", utcnow()
        return len(rows)
