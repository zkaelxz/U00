"""
applog.py -- a single rotating log file for the whole app
(library/logs/app.log), so a background job's failure leaves more than
whatever happened to be shown on screen at the time. Before this, the
app had no logging at all.

get_logger() re-derives the log path from db.LIBRARY_DIR on every call
and reconfigures if it changed, rather than caching it once -- tests
redirect LIBRARY_DIR per-test via db.configure_library_dir(), and a
cached path from an earlier test's already-deleted temp directory would
make later log writes fail.
"""

import logging
import logging.handlers
import os

import db

_LOGGER_NAME = "baihe"


def get_logger():
    logger = logging.getLogger(_LOGGER_NAME)
    log_path = os.path.join(db.LIBRARY_DIR, "logs", "app.log")
    if getattr(logger, "_baihe_log_path", None) != log_path:
        for h in list(logger.handlers):
            logger.removeHandler(h)
            h.close()
        os.makedirs(os.path.dirname(log_path), exist_ok=True)
        handler = logging.handlers.RotatingFileHandler(
            log_path, maxBytes=5_000_000, backupCount=3, encoding="utf-8")
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s %(name)s: %(message)s"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False
        logger._baihe_log_path = log_path
    return logger


def tail(n: int = 50):
    """Returns the last n lines of the current log file (oldest first),
    or [] if nothing has been logged yet. Used by Diagnostics."""
    log_path = os.path.join(db.LIBRARY_DIR, "logs", "app.log")
    if not os.path.exists(log_path):
        return []
    with open(log_path, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return [ln.rstrip("\n") for ln in lines[-n:]]


def filter_lines(lines: list, keyword: str = "") -> list:
    """Returns only the entries of `lines` containing `keyword`
    (case-insensitive) -- every line unchanged if `keyword` is blank. Used
    by Diagnostics' log keyword filter box (Step 18 item 4), so finding
    "what happened with drama X" doesn't mean reading every line by eye."""
    keyword = (keyword or "").strip()
    if not keyword:
        return list(lines)
    keyword_lower = keyword.lower()
    return [ln for ln in lines if keyword_lower in ln.lower()]
