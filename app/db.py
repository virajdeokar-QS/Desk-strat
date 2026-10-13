"""Database layer (SQLAlchemy 2.0). All datetimes are stored as naive UTC."""
from __future__ import annotations

import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
from sqlalchemy import (DateTime, Float, Index, Integer, String, Text, UniqueConstraint,
                        create_engine, event, select)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker

from . import config


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def to_naive_utc(ts) -> datetime:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.tz_localize(None).to_pydatetime()


def iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


_url = config.DATABASE_URL.replace("postgres://", "postgresql://", 1)
_sqlite = _url.startswith("sqlite")
engine = create_engine(
    _url,
    pool_pre_ping=True,
    connect_args={"check_same_thread": False, "timeout": 30} if _sqlite else {},
)

if _sqlite:
    @event.listens_for(engine, "connect")
    def _sqlite_pragmas(dbapi_conn, _record):  # WAL lets the web thread read while the engine writes
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()

SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


class Bar(Base):
    """Every OHLCV bar we ever used - the audit trail that makes live vs backtest comparable."""
    __tablename__ = "bars"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(16))
    symbol: Mapped[str] = mapped_column(String(32))
    interval: Mapped[str] = mapped_column(String(8))
    ts: Mapped[datetime] = mapped_column(DateTime)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float, default=0.0)
    __table_args__ = (UniqueConstraint("source", "symbol", "interval", "ts", name="uq_bar"),
                      Index("ix_bar_lookup", "source", "symbol", "interval", "ts"))


class BacktestRun(Base):
    __tablename__ = "backtest_runs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    symbol: Mapped[str] = mapped_column(String(32))
    interval: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str] = mapped_column(String(32))
    params: Mapped[str] = mapped_column(Text, default="{}")
    config: Mapped[str] = mapped_column(Text, default="{}")
    metrics: Mapped[str] = mapped_column(Text, default="{}")
    result: Mapped[str] = mapped_column(Text, default="{}")  # full payload: series, trades, fills


class LiveSession(Base):
    __tablename__ = "live_sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    stopped_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_tick_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    symbol: Mapped[str] = mapped_column(String(32))
    interval: Mapped[str] = mapped_column(String(8))
    strategy: Mapped[str] = mapped_column(String(32))
    params: Mapped[str] = mapped_column(Text, default="{}")
    config: Mapped[str] = mapped_column(Text, default="{}")
    status: Mapped[str] = mapped_column(String(16), default="running")  # running | stopped | interrupted
    initial_cash: Mapped[float] = mapped_column(Float)
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)


class SignalRow(Base):
    __tablename__ = "signals"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    bar_ts: Mapped[datetime] = mapped_column(DateTime)
    bar_close: Mapped[float] = mapped_column(Float)
    live_price: Mapped[float] = mapped_column(Float)
    target_pos: Mapped[int] = mapped_column(Integer)
    prev_pos: Mapped[int] = mapped_column(Integer)


class FillRow(Base):
    __tablename__ = "fills"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    bar_ts: Mapped[datetime] = mapped_column(DateTime)
    side: Mapped[str] = mapped_column(String(4))
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float)
    target_pos: Mapped[int] = mapped_column(Integer)


class EquityPoint(Base):
    __tablename__ = "equity_points"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    session_id: Mapped[int] = mapped_column(Integer)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    price: Mapped[float] = mapped_column(Float)
    qty: Mapped[float] = mapped_column(Float)
    cash: Mapped[float] = mapped_column(Float)
    equity: Mapped[float] = mapped_column(Float)
    __table_args__ = (Index("ix_equity_session_ts", "session_id", "ts"),)


class LogEntry(Base):
    __tablename__ = "logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=utcnow)
    level: Mapped[str] = mapped_column(String(10))
    source: Mapped[str] = mapped_column(String(48))
    message: Mapped[str] = mapped_column(Text)
    session_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True, index=True)


def init_db() -> None:
    Base.metadata.create_all(engine)


@contextmanager
def session_scope():
    s = SessionLocal()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()


_bar_lock = threading.Lock()
_BAR_COLS = ["open", "high", "low", "close", "volume"]


def save_bars(symbol: str, interval: str, df: pd.DataFrame, source: str) -> int:
    """Insert bars we have not stored yet. Returns the number inserted."""
    if df.empty:
        return 0
    stamps = [to_naive_utc(t) for t in df.index]
    with _bar_lock, session_scope() as s:
        existing = set(s.scalars(select(Bar.ts).where(
            Bar.source == source, Bar.symbol == symbol, Bar.interval == interval,
            Bar.ts >= min(stamps))).all())
        rows = [Bar(source=source, symbol=symbol, interval=interval, ts=t, open=float(o), high=float(h),
                    low=float(l), close=float(c), volume=float(v))
                for t, (o, h, l, c, v) in zip(stamps, df[_BAR_COLS].itertuples(index=False, name=None))
                if t not in existing]
        s.add_all(rows)
    return len(rows)


def load_bars(symbol: str, interval: str, source: str) -> pd.DataFrame:
    with session_scope() as s:
        rows = s.execute(select(Bar.ts, Bar.open, Bar.high, Bar.low, Bar.close, Bar.volume).where(
            Bar.source == source, Bar.symbol == symbol, Bar.interval == interval).order_by(Bar.ts)).all()
    if not rows:
        return pd.DataFrame(columns=_BAR_COLS, index=pd.DatetimeIndex([], tz="UTC"))
    df = pd.DataFrame(rows, columns=["ts"] + _BAR_COLS)
    df.index = pd.DatetimeIndex(df.pop("ts")).tz_localize("UTC")
    return df
