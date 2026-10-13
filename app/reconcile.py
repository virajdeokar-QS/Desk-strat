"""Live vs backtest reconciliation: replay the stored closed bars through the backtester from the
live session's first signal, then compare signals, fill prices and returns."""
from __future__ import annotations

import json

from sqlalchemy import select

from .backtest import run_backtest
from .data import fmt_ts, to_utc
from .db import EquityPoint, FillRow, LiveSession, SignalRow, load_bars, session_scope
from .strategy import make_strategy


def reconcile(session_id: int) -> dict:
    with session_scope() as s:
        ls = s.get(LiveSession, session_id)
        if ls is None:
            raise LookupError(f"Live session {session_id} not found")
        cfg = json.loads(ls.config)
        sigs = s.scalars(select(SignalRow).where(SignalRow.session_id == session_id)
                         .order_by(SignalRow.bar_ts)).all()
        fills = s.scalars(select(FillRow).where(FillRow.session_id == session_id)).all()
        last_eq = s.scalars(select(EquityPoint).where(EquityPoint.session_id == session_id)
                            .order_by(EquityPoint.ts.desc()).limit(1)).first()
        symbol, interval, strat_name, params, initial = ls.symbol, ls.interval, ls.strategy, json.loads(ls.params), ls.initial_cash
    if len(sigs) < 2:
        return {"ready": False, "message": "Need at least 2 closed-bar signals before a comparison is meaningful."}
    bars = load_bars(symbol, interval, cfg["data_source"])
    try:
        bt = run_backtest(bars, make_strategy(strat_name, params), symbol, interval, initial,
                          cfg["fee_bps"], cfg["slippage_bps"], start_ts=to_utc(sigs[0].bar_ts))
    except ValueError as ex:
        return {"ready": False, "message": str(ex)}
    fill_by_bar = {f.bar_ts: f for f in fills}
    rows, adverse = [], []
    for sg in sigs:
        ts = to_utc(sg.bar_ts)
        bt_pos = int(bt.pos.loc[ts]) if ts in bt.pos.index else None
        f = fill_by_bar.get(sg.bar_ts)
        bps = None
        if f:
            bps = ((f.price / sg.bar_close - 1) if f.side == "BUY" else (sg.bar_close / f.price - 1)) * 1e4
            adverse.append(bps)
        rows.append({"bar_t": fmt_ts(ts), "live_target": sg.target_pos, "backtest_target": bt_pos,
                     "match": bt_pos == sg.target_pos, "bar_close": sg.bar_close,
                     "live_fill": f.price if f else None, "adverse_bps": bps})
    compared = [r for r in rows if r["backtest_target"] is not None]
    matches = sum(r["match"] for r in compared)
    live_ret = (last_eq.equity / initial - 1) if last_eq else None
    return {
        "ready": True, "n_signals": len(rows), "n_compared": len(compared), "n_match": matches,
        "match_rate": matches / len(compared) if compared else None,
        "avg_adverse_bps": sum(adverse) / len(adverse) if adverse else None,
        "modelled_cost_bps_per_side": cfg["slippage_bps"],
        "live_return": live_ret, "backtest_return": float(bt.equity.iloc[-1] / initial - 1),
        "note": "Live equity is marked to the still-forming bar; the backtest to the last closed bar. "
                "Small return gaps are expected; signal mismatches are not.",
        "rows": rows[-200:],
    }
