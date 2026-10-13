"""Central configuration. Everything is overridable with environment variables or a .env file."""
import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")

DATA_DIR = BASE_DIR / "data"
DATA_DIR.mkdir(exist_ok=True)

# SQLite by default (zero setup). Set DATABASE_URL=postgresql://user:pass@host/db for Postgres.
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite:///{(DATA_DIR / 'strategy_desk.db').as_posix()}")

# "yfinance" = real market data, "synthetic" = deterministic fake data for demos and tests.
DATA_SOURCE = os.getenv("DATA_SOURCE", "yfinance")

# BTC-USD trades 24/7, so the live engine always has fresh bars to work with.
DEFAULT_SYMBOL = os.getenv("DEFAULT_SYMBOL", "BTC-USD")
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "30"))
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
