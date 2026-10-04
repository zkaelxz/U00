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

Every record is secret-redacted on its way to the file (_RedactSecretsFilter).
"""

import logging
import logging.handlers
import os

import db

_LOGGER_NAME = "baihe"


class _RedactSecretsFilter(logging.Filter):
    """Runs translate_engines.redact_secrets over every record before it's
    written: the formatted message and any traceback text. A caller that
    redacts its own message but passes exc_info=True would otherwise still
    log the raw exception (and any key in it) in the traceback."""

    def filter(self, record):
        # A filter runs outside the handler's own error handling, so nothing
        # here may raise into the logging call (some run under a lock).
        try:
            from translate_engines import redact_secrets
            record.msg = redact_secrets(record.getMessage())
            record.args = None
            if record.exc_info and not record.exc_text:
                record.exc_text = logging.Formatter().formatException(record.exc_info)
            if record.exc_text:
                record.exc_text = redact_secrets(record.exc_text)
            if record.stack_info:
                record.stack_info = redact_secrets(record.stack_info)
        except Exception as exc:
            # Never write the unredacted original: a placeholder instead.
            record.msg = (f"(log message from {record.pathname}:{record.lineno} could not "
                          f"be formatted: {type(exc).__name__})")
            record.args = None
            record.exc_info = record.exc_text = record.stack_info = None
        return True


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
        handler.addFilter(_RedactSecretsFilter())
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
    by Diagnostics' log keyword filter box, so finding
    "what happened with drama X" doesn't mean reading every line by eye."""
    keyword = (keyword or "").strip()
    if not keyword:
        return list(lines)
    keyword_lower = keyword.lower()
    return [ln for ln in lines if keyword_lower in ln.lower()]
