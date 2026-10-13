"""FastAPI app: JSON API under /api, static frontend at /."""
from __future__ import annotations

import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import select

from . import config
from .backtest import run_backtest
from .data import INTERVAL_MIN, DataError, drop_forming, fetch_bars
from .db import (BacktestRun, EquityPoint, FillRow, LiveSession, LogEntry, SignalRow, init_db, iso,
                 save_bars, session_scope)
from .live import manager, mark_interrupted
from .logging_setup import setup_logging
from .reconcile import reconcile
from .strategy import describe_strategies, make_strategy

log = logging.getLogger("quant.api")
STATIC = Path(__file__).parent / "static"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    setup_logging()
    n = mark_interrupted()
    log.info("Strategy Desk started (data source: %s, db: %s)%s", config.DATA_SOURCE,
             config.DATABASE_URL.split("@")[-1].split("///")[-1],
             f"; marked {n} orphaned live session(s) as interrupted" if n else "")
    yield
    manager.stop_all()


app = FastAPI(title="Strategy Desk", version="1.0.0", lifespan=lifespan)


class RunRequest(BaseModel):
    symbol: str = Field(default_factory=lambda: config.DEFAULT_SYMBOL, min_length=1, max_length=32)
    interval: str = "1h"
    strategy: str = "sma_cross"
    params: Dict[str, Any] = {}
    cash: float = Field(100_000, gt=0)
    fee_bps: float = Field(5, ge=0, le=500)
    slippage_bps: float = Field(2, ge=0, le=500)
    data_source: Optional[str] = None


class BacktestRequest(RunRequest):
    period: Optional[str] = "90d"
    start: Optional[str] = None
    end: Optional[str] = None


class LiveRequest(RunRequest):
    interval: str = "5m"
    poll_seconds: int = Field(default_factory=lambda: config.POLL_SECONDS, ge=5, le=3600)


def _check_interval(i: str) -> None:
    if i not in INTERVAL_MIN:
        raise HTTPException(400, f"Unsupported interval '{i}'. Choose from {', '.join(INTERVAL_MIN)}.")


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/config")
def get_config():
    return {"strategies": describe_strategies(), "intervals": list(INTERVAL_MIN), "data_source": config.DATA_SOURCE,
            "default_symbol": config.DEFAULT_SYMBOL, "poll_seconds": config.POLL_SECONDS}


@app.post("/api/backtests")
def create_backtest(req: BacktestRequest):
    _check_interval(req.interval)
    sym, source = req.symbol.strip().upper(), req.data_source or config.DATA_SOURCE
    try:
        strat = make_strategy(req.strategy, req.params)
        df = drop_forming(fetch_bars(sym, req.interval, req.period, req.start, req.end, source), req.interval)
        save_bars(sym, req.interval, df, source)
        res = run_backtest(df, strat, sym, req.interval, req.cash, req.fee_bps, req.slippage_bps)
    except (DataError, ValueError) as ex:
        raise HTTPException(400, str(ex))
    payload = res.payload()
    cfg = req.model_dump()
    cfg.update(symbol=sym, data_source=source)
    with session_scope() as s:
        row = BacktestRun(symbol=sym, interval=req.interval, strategy=req.strategy, params=json.dumps(req.params),
                          config=json.dumps(cfg), metrics=json.dumps(payload["metrics"]), result=json.dumps(payload))
        s.add(row)
        s.flush()
        rid = row.id
    m = payload["metrics"]
    log.info("Backtest #%d %s %s %s: return %.2f%%, sharpe %.2f, maxDD %.2f%%, %d trades (%d bars)", rid, sym,
             req.interval, req.strategy, m["total_return"] * 100, m["sharpe"], m["max_drawdown"] * 100,
             m["n_trades"], m["bars"])
    return {"id": rid, "config": cfg, **payload}


@app.get("/api/backtests")
def list_backtests(limit: int = 30):
    with session_scope() as s:
        rows = s.scalars(select(BacktestRun).order_by(BacktestRun.id.desc()).limit(limit)).all()
        return [{"id": r.id, "created_at": iso(r.created_at), "symbol": r.symbol, "interval": r.interval,
                 "strategy": r.strategy, "params": json.loads(r.params), "metrics": json.loads(r.metrics)}
                for r in rows]


@app.get("/api/backtests/{rid}")
def get_backtest(rid: int):
    with session_scope() as s:
        r = s.get(BacktestRun, rid)
        if not r:
            raise HTTPException(404, "Backtest not found")
        return {"id": r.id, "config": json.loads(r.config), **json.loads(r.result)}


@app.post("/api/live")
def start_live(req: LiveRequest):
    _check_interval(req.interval)
    cfg = req.model_dump()
    cfg.update(symbol=req.symbol.strip().upper(), data_source=req.data_source or config.DATA_SOURCE)
    try:
        sid = manager.start(cfg)
    except ValueError as ex:
        raise HTTPException(400, str(ex))
    return {"id": sid, "status": "running"}


@app.post("/api/live/{sid}/stop")
def stop_live(sid: int):
    if not manager.stop(sid):
        raise HTTPException(404, "Session is not running")
    return {"id": sid, "status": "stopped"}


def _session_dict(r: LiveSession) -> dict:
    return {"id": r.id, "symbol": r.symbol, "interval": r.interval, "strategy": r.strategy,
            "params": json.loads(r.params), "status": r.status, "running": manager.is_running(r.id),
            "started_at": iso(r.started_at), "stopped_at": iso(r.stopped_at), "last_tick_at": iso(r.last_tick_at),
            "initial_cash": r.initial_cash, "last_error": r.last_error}


@app.get("/api/live")
def list_live():
    with session_scope() as s:
        return [_session_dict(r) for r in s.scalars(select(LiveSession).order_by(LiveSession.id.desc()).limit(30))]


@app.get("/api/live/{sid}")
def live_detail(sid: int):
    with session_scope() as s:
        ls = s.get(LiveSession, sid)
        if not ls:
            raise HTTPException(404, "Live session not found")
        eq = list(reversed(s.scalars(select(EquityPoint).where(EquityPoint.session_id == sid)
                                     .order_by(EquityPoint.ts.desc()).limit(3000)).all()))
        sigs = s.scalars(select(SignalRow).where(SignalRow.session_id == sid).order_by(SignalRow.id.desc()).limit(100)).all()
        fills = s.scalars(select(FillRow).where(FillRow.session_id == sid).order_by(FillRow.id.desc()).limit(100)).all()
        info = _session_dict(ls)
    last = eq[-1] if eq else None
    state = None
    if last:
        state = {"price": last.price, "qty": last.qty, "cash": last.cash, "equity": last.equity,
                 "pnl_pct": last.equity / info["initial_cash"] - 1,
                 "side": 1 if last.qty > 0 else (-1 if last.qty < 0 else 0)}
    return {"session": info, "state": state,
            "equity": {"t": [iso(p.ts) for p in eq], "v": [round(p.equity, 4) for p in eq],
                       "price": [p.price for p in eq]},
            "signals": [{"bar_t": iso(x.bar_ts), "bar_close": x.bar_close, "target": x.target_pos, "prev": x.prev_pos}
                        for x in sigs],
            "fills": [{"t": iso(f.ts), "side": f.side, "qty": f.qty, "price": f.price, "fee": f.fee} for f in fills]}


@app.get("/api/live/{sid}/reconcile")
def live_reconcile(sid: int):
    try:
        return reconcile(sid)
    except LookupError as ex:
        raise HTTPException(404, str(ex))


@app.get("/api/logs")
def get_logs(after_id: int = 0, limit: int = Query(300, le=1000), session_id: Optional[int] = None):
    with session_scope() as s:
        q = select(LogEntry)
        if session_id is not None:
            q = q.where(LogEntry.session_id == session_id)
        if after_id:
            rows = s.scalars(q.where(LogEntry.id > after_id).order_by(LogEntry.id).limit(limit)).all()
        else:
            rows = list(reversed(s.scalars(q.order_by(LogEntry.id.desc()).limit(limit)).all()))
        return [{"id": r.id, "t": iso(r.ts), "level": r.level, "source": r.source.replace("quant.", ""),
                 "message": r.message, "session_id": r.session_id} for r in rows]


app.mount("/", StaticFiles(directory=STATIC, html=True), name="static")
