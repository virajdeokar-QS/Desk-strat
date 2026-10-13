"""Strategies. A strategy maps a bar history to a TARGET POSITION per bar: +1 long, 0 flat, -1 short.

Contract (this is what makes backtest and live agree):
  * signals(df)[t] may only use rows <= t  (no look-ahead; enforced by a unit test)
  * the same function is called by the backtester (whole history) and the live engine (latest window)
"""
from __future__ import annotations

from dataclasses import dataclass, fields
from typing import Dict, List, Type

import numpy as np
import pandas as pd


class Strategy:
    name = ""
    label = ""
    description = ""

    @property
    def min_bars(self) -> int:
        raise NotImplementedError

    def validate(self) -> None:
        pass

    def signals(self, df: pd.DataFrame) -> pd.Series:
        raise NotImplementedError


@dataclass
class SMACross(Strategy):
    """Trend following: long while the fast average is above the slow one."""
    fast: int = 20
    slow: int = 50
    allow_short: bool = False

    name = "sma_cross"
    label = "SMA crossover (trend)"
    description = "Long when the fast SMA is above the slow SMA, otherwise flat (or short if enabled)."

    @property
    def min_bars(self) -> int:
        return self.slow + 1

    def validate(self) -> None:
        if self.fast < 1 or self.fast >= self.slow:
            raise ValueError("SMA crossover needs 1 <= fast < slow.")

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        fast, slow = c.rolling(self.fast).mean(), c.rolling(self.slow).mean()
        down = -1 if self.allow_short else 0
        raw = np.where(fast > slow, 1, down)
        raw = np.where(fast.isna() | slow.isna(), 0, raw)
        return pd.Series(raw, index=df.index).astype(int)


@dataclass
class ZScoreMR(Strategy):
    """Mean reversion: fade moves that are far from the rolling mean, exit when they normalise."""
    window: int = 40
    entry: float = 2.0
    exit: float = 0.5
    allow_short: bool = True

    name = "zscore_mr"
    label = "Z-score mean reversion"
    description = "Buy when the price is `entry` std-devs below its rolling mean, sell when above; exit near the mean."

    @property
    def min_bars(self) -> int:
        return self.window + 1

    def validate(self) -> None:
        if self.window < 5:
            raise ValueError("Z-score window must be at least 5.")
        if not (self.entry > self.exit >= 0):
            raise ValueError("Z-score needs entry > exit >= 0.")

    def signals(self, df: pd.DataFrame) -> pd.Series:
        c = df["close"]
        mean = c.rolling(self.window).mean()
        std = c.rolling(self.window).std(ddof=0).replace(0, np.nan)
        z = ((c - mean) / std).to_numpy()
        out = np.zeros(len(z), dtype=int)
        pos = 0
        for i, zi in enumerate(z):
            if np.isnan(zi):
                pos = 0
            elif pos == 0:
                if zi <= -self.entry:
                    pos = 1
                elif zi >= self.entry and self.allow_short:
                    pos = -1
            elif pos == 1 and zi >= -self.exit:
                pos = 0
            elif pos == -1 and zi <= self.exit:
                pos = 0
            out[i] = pos
        return pd.Series(out, index=df.index)


REGISTRY: Dict[str, Type[Strategy]] = {SMACross.name: SMACross, ZScoreMR.name: ZScoreMR}


def _cast(value, default):
    if isinstance(default, bool):
        return value.strip().lower() in ("1", "true", "yes", "on") if isinstance(value, str) else bool(value)
    if isinstance(default, int):
        return int(value)
    return float(value)


def make_strategy(name: str, params: dict | None = None) -> Strategy:
    cls = REGISTRY.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy '{name}'. Available: {', '.join(REGISTRY)}.")
    kwargs = {}
    for f in fields(cls):
        if params and params.get(f.name) not in (None, ""):
            try:
                kwargs[f.name] = _cast(params[f.name], f.default)
            except (TypeError, ValueError):
                raise ValueError(f"Parameter '{f.name}' has an invalid value: {params[f.name]!r}")
    obj = cls(**kwargs)
    obj.validate()
    return obj


def describe_strategies() -> List[dict]:
    return [{"name": c.name, "label": c.label, "description": c.description,
             "params": [{"name": f.name, "type": type(f.default).__name__, "default": f.default}
                        for f in fields(c)]} for c in REGISTRY.values()]
