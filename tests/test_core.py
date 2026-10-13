import numpy as np
import pandas as pd
import pytest

from app.backtest import run_backtest
from app.data import _synthetic, SYNTH_ANCHOR
from app.portfolio import Portfolio
from app.strategy import make_strategy
from datetime import timedelta


def make_df(n=600, seed=1):
    rng = np.random.default_rng(seed)
    close = 100 * np.exp(np.cumsum(rng.normal(0, 0.01, n)))
    idx = pd.date_range("2024-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1.0}, index=idx)


@pytest.mark.parametrize("name", ["sma_cross", "zscore_mr"])
def test_signals_have_no_lookahead(name):
    """Signal at t computed on the truncated history must equal the one computed with the future present."""
    df = make_df()
    strat = make_strategy(name, {"allow_short": True})
    full = strat.signals(df)
    for t in (80, 150, 301, 450, 599):
        assert strat.signals(df.iloc[: t + 1]).iloc[-1] == full.iloc[t]


def test_portfolio_round_trip_long():
    pf = Portfolio(1000, fee_bps=0, slippage_bps=0)
    pf.rebalance("t0", 100.0, 1)
    assert pf.qty == pytest.approx(10.0)
    pf.rebalance("t1", 110.0, 0)
    assert pf.equity(110.0) == pytest.approx(1100.0)
    assert pf.trades[0]["return_pct"] == pytest.approx(0.10)


def test_portfolio_short_profits_when_price_falls():
    pf = Portfolio(1000, fee_bps=0, slippage_bps=0)
    pf.rebalance("t0", 100.0, -1)
    pf.rebalance("t1", 90.0, 0)
    assert pf.equity(90.0) == pytest.approx(1100.0)


def test_costs_reduce_equity():
    df = make_df()
    strat = make_strategy("sma_cross", {"fast": 5, "slow": 20})
    free = run_backtest(df, strat, fee_bps=0, slippage_bps=0)
    costly = run_backtest(df, strat, fee_bps=20, slippage_bps=10)
    assert costly.metrics["final_equity"] < free.metrics["final_equity"]
    assert free.metrics["n_trades"] > 0


def test_backtest_metrics_are_finite_and_json_safe():
    import json
    res = run_backtest(make_df(), make_strategy("zscore_mr"))
    json.dumps(res.payload(), allow_nan=False)


def test_strategy_validation():
    with pytest.raises(ValueError):
        make_strategy("sma_cross", {"fast": 50, "slow": 20})
    with pytest.raises(ValueError):
        make_strategy("nope")


def test_synthetic_prices_are_stable_over_time():
    """Same timestamp must give the same price no matter when we ask (needed to test live vs backtest)."""
    e1 = SYNTH_ANCHOR + timedelta(days=200)
    e2 = e1 + timedelta(days=30)
    a = _synthetic("TEST", "1h", e1 - timedelta(days=20), e1)
    b = _synthetic("TEST", "1h", e1 - timedelta(days=20), e2)
    common = a.index.intersection(b.index)
    assert len(common) > 400
    np.testing.assert_allclose(a.loc[common, "close"], b.loc[common, "close"])
