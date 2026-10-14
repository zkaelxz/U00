"""
services/benchmark_export_service.py -- a Benchmark Lab run, or a comparison
of runs, as CSV or JSON: the setup, the scores and each case's output. UI-free.

Cells are written as they are (no spreadsheet formula hardening, by the
owner's decision). Errors and notes arrive already redacted from
benchmark_runs_service. The CSV is UTF-8 with a byte-order mark so a
spreadsheet reads the Chinese, Japanese and Korean text correctly.
"""
import csv
import io
import json

import db
from services import benchmark_judge_service as judge_svc
from services import benchmark_runs_service as runs_svc
from services.service_errors import InvalidInputError

FORMATS = ("csv", "json")
_RUN_COLUMNS = ("run_id", "run_label", "stage", "engine", "model", "prompt_version", "settings")
_RESULT_COLUMNS = ("case_id", "case_label", "language", "reference_version", "source_text",
                   "reference_text", "output_text", "score", "metric", "scorer", "passed",
                   "duration_seconds", "cost_usd", "judge_overall", "error")


def _case_info(stage) -> dict:
    return {c["id"]: c for c in db.list_benchmark_cases(stage)}


def _result_fields(result: dict, case: dict) -> dict:
    return {"case_id": result.get("case_id"), "case_label": result.get("case_label"),
            "language": case.get("source_language"), "reference_version": result.get("ref_version"),
            "source_text": case.get("source_text"), "reference_text": case.get("reference_text"),
            "output_text": result.get("output_text"), "score": result.get("score"),
            "metric": result.get("metric"), "scorer": result.get("scorer"),
            "passed": result.get("passed"), "duration_seconds": result.get("duration_seconds"),
            "cost_usd": result.get("cost_usd"),
            "judge_overall": (result.get("judge") or {}).get("overall"), "error": result.get("error")}


def run_data(run_id: int) -> dict:
    out = runs_svc.get_run(run_id)
    judge_svc.annotate_runs([out["run"]])
    judge_svc.annotate_results(out["run"], out["results"])
    cases = _case_info(out["run"]["stage"])
    return {"kind": "run", "run": out["run"],
            "results": [_result_fields(r, cases.get(r["case_id"]) or {}) for r in out["results"]]}


def comparison_data(run_ids: list) -> dict:
    out = runs_svc.arena(run_ids)
    judge_svc.annotate_runs(out["runs"])
    for row in out["rows"]:
        for run, cell in zip(out["runs"], row["results"]):
            judge_svc.annotate_results(run, [cell])
    cases = _case_info(out["runs"][0]["stage"])
    rows = [{"case_id": row["case_id"], "label": row["label"], "reference_version": row["ref_version"],
             "results": [{"run_id": run["id"], **_result_fields(cell, cases.get(row["case_id"]) or {})}
                         for run, cell in zip(out["runs"], row["results"])]}
            for row in out["rows"]]
    return {"kind": "comparison", "runs": out["runs"], "compared_cases": len(rows),
            "left_out": out["left_out"], "rows": rows}


def _csv_rows(data: dict):
    if data["kind"] == "run":
        yield from ((data["run"], r) for r in data["results"])
        return
    runs = {run["id"]: run for run in data["runs"]}
    for row in data["rows"]:
        for r in row["results"]:
            yield runs[r["run_id"]], r


def _render_csv(data: dict) -> bytes:
    buf = io.StringIO(newline="")
    writer = csv.writer(buf)
    writer.writerow(_RUN_COLUMNS + _RESULT_COLUMNS)
    for run, result in _csv_rows(data):
        setup = [run["id"], run.get("label"), run.get("stage"), run.get("engine"), run.get("model"),
                 run.get("prompt_version"), json.dumps(run.get("context_settings") or {}, ensure_ascii=False)]
        writer.writerow(setup + [result.get(c) for c in _RESULT_COLUMNS])
    return buf.getvalue().encode("utf-8-sig")


def render(data: dict, fmt: str):
    """(file name, media type, bytes) for the data in `fmt`."""
    if fmt not in FORMATS:
        raise InvalidInputError("format must be csv or json.")
    if data["kind"] == "run":
        stem = f"benchmark-run-{data['run']['id']}"
    else:
        stem = "benchmark-comparison-" + "-".join(str(run["id"]) for run in data["runs"])
    if fmt == "csv":
        return f"{stem}.csv", "text/csv; charset=utf-8", _render_csv(data)
    body = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
    return f"{stem}.json", "application/json", body
