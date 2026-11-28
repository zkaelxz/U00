"""Owns the process-wide counts of listener reopens (tried, failed, succeeded) that the daily stats line reports."""

import threading

_lock = threading.Lock()
_counts = {"tried": 0, "failed": 0, "succeeded": 0}


def record(outcome: str) -> None:
    """`outcome` is "failed" or "succeeded"; each also counts as tried."""
    with _lock:
        _counts["tried"] += 1
        _counts[outcome] += 1


def peek() -> dict:
    with _lock:
        return dict(_counts)


def take() -> dict:
    """The counts since the last take, then zero."""
    with _lock:
        taken = dict(_counts)
        for key in _counts:
            _counts[key] = 0
        return taken


def text(counts: dict) -> str:
    return f"listener reopens: tried {counts['tried']}, failed {counts['failed']}, succeeded {counts['succeeded']}"
