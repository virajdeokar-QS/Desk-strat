import json
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from app import data as data_mod
from app import live as live_mod
from app.db import LiveSession, init_db, session_scope
from app.live import LiveEngine
from app.main import app
from app.reconcile import reconcile


def _cfg(**over):
    cfg = dict(symbol="TESTCOIN", interval="5m", strategy="sma_cross", params={"fast": 5, "slow": 20},
               cash=10_000, fee_bps=5, slippage_bps=2, poll_seconds=5, data_source="synthetic")
    cfg.update(over)
    return cfg


def test_live_engine_ticks_are_idempotent_and_reconcile(monkeypatch):
    init_db()
    cfg = _cfg()
    with session_scope() as s:
        row = LiveSession(symbol=cfg["symbol"], interval="5m", strategy="sma_cross", params=json.dumps(cfg["params"]),
                          config=json.dumps(cfg), initial_cash=cfg["cash"])
        s.add(row); s.flush(); sid = row.id
    t = datetime(2026, 10, 12, 10, 2, tzinfo=timezone.utc)
    clock = {"now": t}
    monkeypatch.setattr(data_mod, "now_utc", lambda: clock["now"])
    monkeypatch.setattr(live_mod, "now_utc", lambda: clock["now"])
    eng = LiveEngine(sid, cfg)
    for _ in range(8):                      # 8 new closed bars
        eng.tick()
        eng.tick()                          # second tick in the same bar must not duplicate the signal
        clock["now"] += timedelta(minutes=5)
    r = reconcile(sid)
    assert r["ready"] and r["n_signals"] == 8
    assert r["match_rate"] == 1.0           # live and backtest agree on every signal
    assert r["avg_adverse_bps"] is None or abs(r["avg_adverse_bps"]) < 5


def test_api_end_to_end():
    with TestClient(app) as c:
        assert c.get("/api/health").json() == {"ok": True}
        cfg = c.get("/api/config").json()
        assert {s["name"] for s in cfg["strategies"]} == {"sma_cross", "zscore_mr"}
        r = c.post("/api/backtests", json={"symbol": "TESTCOIN", "interval": "1h", "period": "60d",
                                           "strategy": "sma_cross", "params": {"fast": 10, "slow": 30},
                                           "data_source": "synthetic"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["metrics"]["bars"] > 100 and len(body["series"]["equity"]) == body["metrics"]["bars"]
        assert c.get(f"/api/backtests/{body['id']}").json()["metrics"] == body["metrics"]
        assert c.get("/api/backtests").json()[0]["id"] == body["id"]
        bad = c.post("/api/backtests", json={"interval": "7m"})
        assert bad.status_code == 400
        bad = c.post("/api/backtests", json={"strategy": "sma_cross", "params": {"fast": 99, "slow": 5},
                                             "data_source": "synthetic"})
        assert bad.status_code == 400
        live = c.post("/api/live", json={"symbol": "TESTCOIN", "interval": "5m", "poll_seconds": 5,
                                         "params": {"fast": 5, "slow": 20}, "data_source": "synthetic"})
        assert live.status_code == 200, live.text
        sid = live.json()["id"]
        for _ in range(40):                 # wait for the first tick
            d = c.get(f"/api/live/{sid}").json()
            if d["state"]:
                break
            time.sleep(0.25)
        assert d["state"] and d["session"]["running"]
        assert c.post(f"/api/live/{sid}/stop").status_code == 200
        assert c.get(f"/api/live/{sid}").json()["session"]["status"] == "stopped"
        logs = c.get("/api/logs").json()
        assert any("Backtest #" in l["message"] for l in logs)
        assert any("started" in l["message"] for l in logs)
