"""Logs go to the console AND the database, so the frontend log tape can replay them."""
import logging
import sys

from . import config
from .db import LogEntry, SessionLocal


class DBHandler(logging.Handler):
    def emit(self, record: logging.LogRecord) -> None:
        try:
            with SessionLocal() as s:
                s.add(LogEntry(level=record.levelname, source=record.name,
                               message=record.getMessage()[:2000],
                               session_id=getattr(record, "session_id", None)))
                s.commit()
        except Exception:  # logging must never crash the engine
            pass


def setup_logging() -> logging.Logger:
    logger = logging.getLogger("quant")
    if logger.handlers:
        return logger
    logger.setLevel(config.LOG_LEVEL)
    logger.propagate = False
    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logger.addHandler(console)
    logger.addHandler(DBHandler())
    return logger
