"""Market data providers. Both return the same shape: UTC-indexed DataFrame[open, high, low, close, volume]."""
from __future__ import annotations

import re
import zlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from . import config

INTERVAL_MIN = {"1m": 1, "5m": 5, "15m": 15, "30m": 30, "1h": 60, "1d": 1440}
# How much history the live engine pulls each poll (Yahoo limits: 1m<=7d, intraday<=60d, 1h<=730d).
LIVE_LOOKBACK = {"1m": "5d", "5m": "30d", "15m": "30d", "30m": "45d", "1h": "120d", "1d": "2y"}
COLS = ["open", "high", "low", "close", "volume"]
SYNTH_ANCHOR = datetime(2024, 1, 1, tzinfo=timezone.utc)


class DataError(Exception):
    """Anything wrong with fetching or validating market data (shown to the user as-is)."""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def to_utc(ts) -> pd.Timestamp:
    t = pd.Timestamp(ts)
    return t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")


def fmt_ts(ts) -> str:
    return to_utc(ts).strftime("%Y-%m-%dT%H:%M:%SZ")


def period_days(period: str) -> int:
    m = re.fullmatch(r"(\d+)(d|wk|mo|y)", period.strip())
    if not m:
        raise DataError(f"Unsupported period '{period}'. Use forms like 30d, 6mo, 2y.")
    return int(m.group(1)) * {"d": 1, "wk": 7, "mo": 30, "y": 365}[m.group(2)]


def bars_per_year(symbol: str, interval: str) -> float:
    """Annualisation factor for Sharpe/vol. Crypto trades 24/7, equities only in sessions."""
    m = INTERVAL_MIN[interval]
    s = symbol.upper()
    if s.endswith(("-USD", "-USDT")):
        return 365.0 * 1440 / m
    if s.endswith((".NS", ".BO")):          # NSE/BSE: ~248 days, 375-minute session
        return 248.0 if m == 1440 else 248.0 * 375 / m
    return 252.0 if m == 1440 else 252.0 * 390 / m


def fetch_bars(symbol: str, interval: str, period: Optional[str] = None, start: Optional[str] = None,
               end: Optional[str] = None, source: Optional[str] = None) -> pd.DataFrame:
    """Fetch bars. The last row may be a still-forming bar: call drop_forming() before trusting it."""
    if interval not in INTERVAL_MIN:
        raise DataError(f"Unsupported interval '{interval}'. Choose from {', '.join(INTERVAL_MIN)}.")
    source = source or config.DATA_SOURCE
    now = now_utc()
    if start:
        s = to_utc(start).to_pydatetime()
        e = (to_utc(end).to_pydatetime() + timedelta(days=1)) if end else now + timedelta(days=1)
    else:
        s = now - timedelta(days=period_days(period or LIVE_LOOKBACK[interval]))
        e = now + timedelta(days=1)
    if source == "yfinance":
        df = _yfinance(symbol, interval, s, e)
    elif source == "synthetic":
        df = _synthetic(symbol, interval, s, min(e, now))
    else:
        raise DataError(f"Unknown data source '{source}'.")
    if len(df) < 2:
        raise DataError(f"Not enough bars returned for {symbol} ({interval}).")
    return df


def _yfinance(symbol: str, interval: str, s: datetime, e: datetime) -> pd.DataFrame:
    try:
        import yfinance as yf
    except ImportError as ex:
        raise DataError("yfinance is not installed (pip install yfinance).") from ex
    kwargs = dict(interval=interval, auto_adjust=True, progress=False, threads=False)
    if interval == "1d":
        kwargs.update(start=s.strftime("%Y-%m-%d"), end=e.strftime("%Y-%m-%d"))
    else:
        kwargs.update(start=s, end=e)
    try:
        df = yf.download(symbol, **kwargs)
    except Exception as ex:
        raise DataError(f"yfinance failed for {symbol}: {ex}") from ex
    if df is None or df.empty:
        raise DataError(f"No data for {symbol} at {interval}. Check the symbol, the period limits "
                        f"(1m<=7d, 5m/15m/30m<=60d) and whether the market is open.")
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    df = df.rename(columns=str.lower)
    missing = [c for c in COLS if c not in df.columns]
    if missing:
        raise DataError(f"yfinance response missing columns: {missing}")
    df = df[COLS].dropna()
    df.index = pd.DatetimeIndex(df.index)
    df.index = df.index.tz_localize("UTC") if df.index.tz is None else df.index.tz_convert("UTC")
    return df[~df.index.duplicated(keep="last")].sort_index().astype(float)


def _synthetic(symbol: str, interval: str, s: datetime, e: datetime) -> pd.DataFrame:
    """Deterministic fake prices on a fixed time grid: same timestamp => same price, whenever you call.
    That property is what lets us test the live engine and the reconciliation offline.
    It contains random regimes but NO real edge: backtest numbers on it prove nothing."""
    minutes = INTERVAL_MIN[interval]
    step = timedelta(minutes=minutes)
    k_end = int((e - SYNTH_ANCHOR) / step)
    k_start = max(0, int((s - SYNTH_ANCHOR) / step))
    n = k_end + 1
    if n - k_start < 2:
        raise DataError("Synthetic range too short.")
    if n - k_start > 200_000:
        raise DataError("Synthetic range too long; use a shorter period.")
    seed = zlib.crc32(f"{symbol}|{interval}".encode())
    sigma = 0.02 * np.sqrt(minutes / 1440)
    block = max(30, int(2 * 1440 / minutes))
    regime = np.random.default_rng(seed).integers(-1, 2, size=n // block + 1)
    mu = np.repeat(regime * 0.05 * sigma, block)[:n]
    eps = np.random.default_rng(seed + 1).standard_normal(n)
    close = 100.0 * np.exp(np.cumsum(mu + sigma * eps))
    open_ = np.concatenate([[100.0], close[:-1]])
    wick_hi = np.abs(np.random.default_rng(seed + 2).standard_normal(n)) * sigma * 0.4
    wick_lo = np.abs(np.random.default_rng(seed + 3).standard_normal(n)) * sigma * 0.4
    vol = np.random.default_rng(seed + 4).integers(100, 1000, size=n).astype(float)
    idx = pd.date_range(SYNTH_ANCHOR + k_start * step, periods=n - k_start, freq=f"{minutes}min", tz="UTC")
    sl = slice(k_start, n)
    return pd.DataFrame({"open": open_[sl], "high": np.maximum(open_, close)[sl] * (1 + wick_hi[sl]),
                         "low": np.minimum(open_, close)[sl] * (1 - wick_lo[sl]), "close": close[sl],
                         "volume": vol[sl]}, index=idx)


def drop_forming(df: pd.DataFrame, interval: str, now: Optional[datetime] = None) -> pd.DataFrame:
    """Remove the bar that is still being built. Trading on it would be look-ahead in disguise."""
    now = now or now_utc()
    step = pd.Timedelta(minutes=INTERVAL_MIN[interval])
    return df[df.index + step <= now]
