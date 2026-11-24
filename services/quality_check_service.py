"""The newest real-hardware release check result in the data folder, for Diagnostics > Quality."""
import datetime
import glob
import json
import math
import os

import portable

RESULT_PREFIX = "release-check-"
# A release needs a passing run this recent (docs/runbook.md, section 1); promote-release.yml applies
# the same number to the stamp it is given.
GATE_MAX_AGE_DAYS = 7
# The results file is written by tools/release_check.py on this PC; a cap keeps a stray huge file
# from being read whole on every Diagnostics visit.
MAX_RESULT_BYTES = 1024 * 1024
MAX_TEXT = 80
STATUSES = ("pass", "fail", "none")


def _text(value):
    return str(value)[:MAX_TEXT] if isinstance(value, str) and value else None


def _number(value):
    # NaN or Infinity would make the JSON answer unsendable.
    ok = isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)
    return float(value) if ok else None


def _newest_result(folder: str):
    paths = sorted(glob.glob(os.path.join(glob.escape(folder), f"{RESULT_PREFIX}*.json")))
    return paths[-1] if paths else None


def _clip(row: dict) -> dict:
    scores = row.get("scores")
    status = row.get("status")
    return {
        "id": _text(row.get("id")) or "?",
        "kind": _text(row.get("kind")) or "?",
        "metric": _text(row.get("metric")),
        "score": _number(row.get("score")),
        "baseline": _number(row.get("baseline")),
        # A file from before baselines existed has no status: it shows as having no baseline.
        "status": status if status in STATUSES else "none",
        "failed": bool(row.get("error")),
        "runs": len(scores) if isinstance(scores, list) and scores else 1,
        "fixed_decoding": row.get("fixed_decoding") is True,
    }


def _ran_at(run: dict):
    try:
        return datetime.datetime.strptime(str(run.get("stamp") or ""), "%Y%m%d-%H%M%S")
    except ValueError:
        pass
    try:
        return datetime.datetime.strptime(str(run.get("when") or ""), "%Y-%m-%d %H:%M")
    except ValueError:
        return None


def latest(folder: str | None = None, now: datetime.datetime | None = None) -> dict:
    """The newest result as numbers, fixed words and booleans; never a path or an error text."""
    empty = {"found": False, "unreadable": False, "when": None, "age_days": None, "fresh": False,
             "passed": False, "machine": None, "gpu": None, "repeats": None, "margin": None,
             "max_age_days": GATE_MAX_AGE_DAYS, "clips": []}
    path = _newest_result(folder or portable.data_dir())
    if not path:
        return empty
    try:
        if os.path.getsize(path) > MAX_RESULT_BYTES:
            raise ValueError("too large")
        with open(path, "r", encoding="utf-8") as fh:
            run = json.load(fh)
        if not isinstance(run, dict) or not isinstance(run.get("results"), list):
            raise ValueError("not a results file")
    except (OSError, ValueError):
        return {**empty, "found": True, "unreadable": True}
    ran_at = _ran_at(run)
    # The run writes local time with no zone; it runs on the PC this app reads it from. Calendar days,
    # as promote-release.yml counts them, so the card never calls fresh a stamp Promote refuses.
    age = max(0, ((now or datetime.datetime.now()).date() - ran_at.date()).days) if ran_at else None
    repeats = run.get("repeats")
    return {
        **empty,
        "found": True,
        "when": ran_at.strftime("%Y-%m-%d %H:%M") if ran_at else None,
        "age_days": age,
        "fresh": age is not None and age <= GATE_MAX_AGE_DAYS,
        "passed": run.get("passed") is True,
        "machine": _text(run.get("machine")),
        "gpu": _text(run.get("gpu")),
        "repeats": repeats if isinstance(repeats, int) and not isinstance(repeats, bool) else None,
        "margin": _number(run.get("margin")),
        "clips": [_clip(r) for r in run["results"] if isinstance(r, dict)],
    }
