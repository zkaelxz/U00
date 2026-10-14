"""
services/benchmark_runs_service.py -- how Benchmark Lab runs are read back:
one run's results, the Model Arena (runs compared on the cases they share),
and deleting a run. UI-free. Starting and scoring runs stays in
benchmark_lab_service, which re-exports get_run and arena.
"""
import json

import db
from services.benchmark_case_service import redact
from services.benchmark_script_service import add_counts, count_scripts, empty_counts
from services.service_errors import InvalidInputError, NotFoundError

MAX_CONFIGS = 4

_SESSION_FIELDS = ("id", "label", "stage", "engine", "model", "prompt_version", "arena_group",
                   "status", "case_count", "scored_count", "passed_count", "error_count",
                   "aggregate_score", "avg_latency_seconds", "total_cost_usd", "peak_vram_mb",
                   "note", "created_at", "finished_at")


def session_out(s: dict) -> dict:
    out = {k: s.get(k) for k in _SESSION_FIELDS}
    for k in ("context_settings", "case_filter"):
        try:
            out[k] = json.loads(s.get(k) or "{}")
        except ValueError:
            out[k] = {}
    out["note"] = redact(out["note"])
    return out


def _script_counts(stage, language, text):
    """Script counts for a Chinese transcript, else None. Display only: CER
    converts both sides to Simplified, so it does not see script."""
    if stage != "transcription" or language != "zh":
        return None
    return count_scripts(text)


def _result_out(r: dict, stage=None, language=None) -> dict:
    return {"script_counts": _script_counts(stage, language, r.get("output_text") or ""),
            "case_id": r.get("case_id"), "case_label": r.get("case_label") or "",
            "output_text": r.get("output_text") or "", "score": r.get("score"),
            "metric": r.get("metric"), "scorer": r.get("scorer"),
            "passed": None if r.get("passed") is None else bool(r["passed"]),
            "duration_seconds": r.get("duration_seconds"), "cost_usd": r.get("cost_usd") or 0.0,
            "error": redact(r.get("error")), "ref_version": _version(r.get("ref_version"))}


def _version(value) -> int:
    # Results saved before references were versioned were scored on version 1.
    return value or 1


def _results_with_scripts(session: dict, rows: list) -> list:
    languages = {c["id"]: c.get("source_language")
                 for c in db.list_benchmark_cases(session["stage"])}
    return [_result_out(r, session["stage"], languages.get(r.get("case_id"))) for r in rows]


def _run_with_scripts(run: dict, results: list) -> dict:
    """The arm's total, present only when some result is a Chinese transcript."""
    total, any_counts = empty_counts(), False
    for r in results:
        if r and r["script_counts"]:
            total, any_counts = add_counts(total, r["script_counts"]), True
    return dict(run, script_counts=total if any_counts else None)


def get_run(run_id: int) -> dict:
    s = db.get_benchmark_session(run_id)
    if not s:
        raise NotFoundError("Benchmark run not found.")
    results = _results_with_scripts(s, db.list_benchmark_results(run_id))
    return {"run": _run_with_scripts(session_out(s), results), "results": results}


def delete_run(run_id: int) -> dict:
    """The run and, through ON DELETE CASCADE, its results. Its judge scores
    are kept apart (app_settings) and removed by the caller."""
    if not db.get_benchmark_session(run_id):
        raise NotFoundError("Benchmark run not found.")
    db.delete_benchmark_sessions([run_id])
    return {"deleted": True, "id": run_id}


def _compared_cases(runs: list, cases: dict):
    """(case ids in the first run's order that every run scored on the same
    reference version, how many other cases were left out and why). A case
    that was deleted or excluded, edited between two runs, or missing from
    one run is left out: comparing it would score the runs on different
    ground. A deleted case's result has no id, so each counts on its own."""
    by_run = [{(r["case_id"] if r["case_id"] is not None else f"r{r['id']}"): r
               for r in db.list_benchmark_results(s["id"])} for s in runs]
    seen = []
    for results in by_run:
        seen.extend(cid for cid in results if cid not in seen)
    shared, why = [], {"removed": 0, "excluded": 0, "edited": 0, "not_in_every_run": 0}
    for cid in seen:
        case = cases.get(cid)
        present = [res[cid] for res in by_run if cid in res]
        if case is None:
            why["removed"] += 1
        elif case.get("excluded"):
            why["excluded"] += 1
        elif len(present) < len(by_run):
            why["not_in_every_run"] += 1
        elif len({_version(r.get("ref_version")) for r in present}) > 1:
            why["edited"] += 1
        else:
            shared.append(cid)
    return shared, by_run, why


def _recount(run: dict, cells: list) -> dict:
    """The run's score over only the compared cases, so two runs that scored
    different cases are never compared on their stored totals."""
    scores = [c["score"] for c in cells if c["score"] is not None]
    run = dict(run, stored_aggregate_score=run.get("aggregate_score"), case_count=len(cells),
               scored_count=len(scores), passed_count=sum(1 for c in cells if c["passed"]),
               error_count=sum(1 for c in cells if c["error"]),
               aggregate_score=(sum(scores) / len(scores)) if scores else None)
    return run


def arena(run_ids: list) -> dict:
    """Model Arena: two or more runs side by side, one row per case both
    scored on the same reference version, with each run's output and score,
    each run's totals over those cases, and the score delta of every run
    against the first. `left_out` counts the cases not compared."""
    if not isinstance(run_ids, list) or not 2 <= len(run_ids) <= MAX_CONFIGS:
        raise InvalidInputError(f"Pick 2 to {MAX_CONFIGS} runs to compare.")
    if len(set(run_ids)) != len(run_ids):
        raise InvalidInputError("The same run is picked twice.")
    runs = []
    for rid in run_ids:
        s = db.get_benchmark_session(rid)
        if not s:
            raise NotFoundError("Benchmark run not found.")
        runs.append(s)
    if len({s["stage"] for s in runs}) > 1:
        raise InvalidInputError("Only runs of the same stage can be compared.")
    stage = runs[0]["stage"]
    cases = {c["id"]: c for c in db.list_benchmark_cases(stage)}
    shared, by_run, why = _compared_cases(runs, cases)
    rows = []
    for cid in shared:
        c = cases[cid]
        cells = [_result_out(res[cid], stage, c.get("source_language")) for res in by_run]
        rows.append({"case_id": cid, "label": c.get("label") or cells[0]["case_label"],
                     "source_text": c.get("source_text"), "reference_text": c.get("reference_text"),
                     "tier": c.get("tier"), "ref_version": cells[0]["ref_version"],
                     "results": cells})
    summary = []
    for i, s in enumerate(runs):
        cells = [row["results"][i] for row in rows]
        summary.append(_run_with_scripts(_recount(session_out(s), cells), cells))
    base = summary[0]["aggregate_score"]
    for out in summary:
        agg = out["aggregate_score"]
        out["delta_vs_first"] = (agg - base) if agg is not None and base is not None else None
    return {"runs": summary, "rows": rows,
            "left_out": {"total": sum(why.values()), **why}}
