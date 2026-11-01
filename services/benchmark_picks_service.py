"""
services/benchmark_picks_service.py -- the owner's blind A/B choices between two
Benchmark Lab runs. UI-free.

The owner sees one case at a time with two outputs, in random order, and says
which reads better or that they are equal. Nothing shown names a run, engine or
model; the ticket that comes back with the pick is the only key to which output
was which. A pick is stored once per case and pair of runs (the lower run id is
"a"), and shown beside the similarity score and the judge's scores, with how
often the judge agreed with the owner.
"""
import base64
import json
import random

import db
from lib.errors import InvalidInputError, NotFoundError

CHOICES = ("left", "right", "tie")
MAX_ROW_ID = 2 ** 63 - 1   # what SQLite can bind as an id


def _ticket(case_id: int, left: int, right: int) -> str:
    return base64.urlsafe_b64encode(f"{case_id}:{left}:{right}".encode()).decode()


def _read_ticket(ticket: str) -> tuple:
    try:
        case_id, left, right = (int(p) for p in base64.urlsafe_b64decode(ticket.encode()).decode().split(":"))
    except (ValueError, UnicodeError):
        raise InvalidInputError("This pick does not match a pair that was shown.")
    if max(case_id, left, right) > MAX_ROW_ID or min(case_id, left, right) < 1:
        raise InvalidInputError("This pick does not match a pair that was shown.")
    return case_id, left, right


def _usable(result: dict) -> bool:
    return bool((result.get("output_text") or "").strip()) and not result.get("error")


def _runs(run_a: int, run_b: int) -> tuple:
    if run_a == run_b:
        raise InvalidInputError("Pick two different runs.")
    a, b = db.get_benchmark_session(run_a), db.get_benchmark_session(run_b)
    if not a or not b:
        raise NotFoundError("Benchmark run not found.")
    if a["stage"] != b["stage"]:
        raise InvalidInputError("These runs are for different stages, so their outputs can't be paired.")
    return a, b


def _pairs(run_a: int, run_b: int) -> list:
    """Cases both runs answered with different text, in the first run's order."""
    by_b = {r["case_id"]: r for r in db.list_benchmark_results(run_b) if r["case_id"] is not None}
    return [(r, by_b[r["case_id"]]) for r in db.list_benchmark_results(run_a)
            if r["case_id"] in by_b and _usable(r) and _usable(by_b[r["case_id"]])
            and r["output_text"].strip() != by_b[r["case_id"]]["output_text"].strip()]


def next_pair(run_a: int, run_b: int, rng=None) -> dict:
    """The next case this pair of runs has no pick for, outputs shuffled and
    unlabelled, or `done` when none is left."""
    _runs(run_a, run_b)
    lo, hi = sorted((run_a, run_b))
    picked = {p["case_id"] for p in db.list_benchmark_picks([lo, hi])
              if (p["session_a"], p["session_b"]) == (lo, hi)}
    pairs = _pairs(run_a, run_b)
    open_pairs = [p for p in pairs if p[0]["case_id"] not in picked]
    out = {"total": len(pairs), "remaining": len(open_pairs), "done": not open_pairs}
    if not open_pairs:
        return out
    first, second = open_pairs[0]
    case = db.get_benchmark_case(first["case_id"]) or {}
    left, right = (first, second) if (rng or random).random() < 0.5 else (second, first)
    return {**out, "case_id": first["case_id"], "label": case.get("label") or first.get("case_label") or "",
            "source_text": case.get("source_text"), "reference_text": case.get("reference_text"),
            "left_text": left["output_text"], "right_text": right["output_text"],
            "ticket": _ticket(first["case_id"], left["session_id"], right["session_id"])}


def record_pick(ticket: str, choice: str) -> dict:
    if choice not in CHOICES:
        raise InvalidInputError("Pick left, right or tie.")
    case_id, left, right = _read_ticket(ticket)
    _runs(left, right)
    if not any(r["case_id"] == case_id for r in db.list_benchmark_results(left)) or not any(
            r["case_id"] == case_id for r in db.list_benchmark_results(right)):
        raise NotFoundError("Benchmark result not found.")
    lo, hi = sorted((left, right))
    winner = "tie" if choice == "tie" else ("a" if (left if choice == "left" else right) == lo else "b")
    db.save_benchmark_pick(case_id, lo, hi, winner)
    return {"saved": True}


def _outcome(pick: dict, run_id: int) -> str:
    if pick["winner"] == "tie":
        return "tie"
    return "won" if (pick["winner"] == "a") == (pick["session_a"] == run_id) else "lost"


def _totals(outcomes: list):
    if not outcomes:
        return None
    return {k: outcomes.count(k) for k in ("won", "lost", "tie")}


def annotate_runs(runs: list) -> list:
    """Adds `picks` (won/lost/tie counts over every pair, or None) to each run."""
    for run in runs:
        run["picks"] = _totals([_outcome(p, run["id"]) for p in db.list_benchmark_picks([run["id"]])])
    return runs


def annotate_results(run: dict, results: list) -> None:
    """Adds `picks` (that case's counts for this run, or None) to each result."""
    by_case = {}
    for p in db.list_benchmark_picks([run["id"]]):
        by_case.setdefault(p["case_id"], []).append(_outcome(p, run["id"]))
    for r in results or []:
        r["picks"] = _totals(by_case.get(r.get("case_id"), [])) if r else None


def _judge_overall(scores_by_case: dict, case_id: int):
    entry = scores_by_case.get(case_id)
    return entry.get("overall") if isinstance(entry, dict) else None


def _judge_pass(run: dict):
    """(arena group, judge engine, judge model) of a run's judge pass, or None. One judge pass
    covers the runs started together, so only runs sharing all three were scored side by side."""
    try:
        meta = json.loads(run.get("judge_meta") or "{}")
    except ValueError:
        return None
    if not isinstance(meta, dict) or not run.get("arena_group") or not meta.get("engine"):
        return None
    return run["arena_group"], meta["engine"], meta.get("model")


def summary(run_a: int, run_b: int) -> dict:
    """Totals of the owner's picks between two runs and how the judge's higher
    overall score compared with them. A tie, a case the judge left unscored
    for either run, or runs that were not judged together by the same judge
    (whose scores are not on one scale) is counted as `judge_undecided`."""
    a, b = _runs(run_a, run_b)
    same_judge = _judge_pass(a) is not None and _judge_pass(a) == _judge_pass(b)
    lo, hi = sorted((run_a, run_b))
    picks = [p for p in db.list_benchmark_picks([lo, hi]) if (p["session_a"], p["session_b"]) == (lo, hi)]

    def judged(run_id):
        out = {}
        for r in db.list_benchmark_results(run_id):
            try:
                out[r["case_id"]] = json.loads(r["judge_scores"]) if r.get("judge_scores") else None
            except ValueError:
                out[r["case_id"]] = None
        return out
    ja, jb = judged(lo), judged(hi)
    out = {"run_a": lo, "run_b": hi, "total": len(_pairs(lo, hi)), "picked": len(picks),
           "a_wins": 0, "b_wins": 0, "ties": 0,
           "judge_agrees": 0, "judge_disagrees": 0, "judge_undecided": 0}
    for p in picks:
        out[{"a": "a_wins", "b": "b_wins", "tie": "ties"}[p["winner"]]] += 1
        sa, sb = _judge_overall(ja, p["case_id"]), _judge_overall(jb, p["case_id"])
        if not same_judge or p["winner"] == "tie" or sa is None or sb is None or sa == sb:
            out["judge_undecided"] += 1
        elif (sa > sb) == (p["winner"] == "a"):
            out["judge_agrees"] += 1
        else:
            out["judge_disagrees"] += 1
    return out
