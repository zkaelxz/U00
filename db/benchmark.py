"""Benchmark persistence: accuracy cases and runs, and Benchmark Lab
sessions and results. The tables are created by db.init_db."""

import contextlib
import datetime

import db


# ---------------------------------------------------------------------------
# Accuracy benchmark cases & runs -- see benchmark.py for what actually
# runs a case; this is just the persistence for it, the same "lives in
# the same backed-up sqlite file as everything else" reasoning as the
# rest of this app.
# ---------------------------------------------------------------------------

def create_benchmark_case(label: str, stage: str, content_type: str, source_language: str = "zh",
                           input_filename: str = None, source_text: str = None,
                           reference_text: str = None) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO benchmark_cases (label, stage, content_type, source_language, "
            "input_filename, source_text, reference_text, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (label, stage, content_type, source_language, input_filename, source_text, reference_text,
             datetime.datetime.utcnow().isoformat()))
        conn.commit()
        case_id = cur.lastrowid
    return case_id


def list_benchmark_cases(stage: str = None):
    """All cases, or only those for one stage ('transcription'/'translation'/'ocr')."""
    with contextlib.closing(db.get_conn()) as conn:
        if stage:
            rows = conn.execute(
                "SELECT * FROM benchmark_cases WHERE stage = ? ORDER BY id", (stage,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM benchmark_cases ORDER BY id").fetchall()
    return [dict(r) for r in rows]


def delete_benchmark_case(case_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM benchmark_cases WHERE id = ?", (case_id,))
        conn.commit()


def save_benchmark_run(case_id: int, result: dict, run_label: str = ""):
    """result: one entry from benchmark.run_suite()'s own return list --
    {"output_text", "score", "duration_seconds", "error", "cost_usd"}
    (cost_usd only present for translation-stage results)."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute(
            "INSERT INTO benchmark_runs (case_id, run_label, output_text, score, duration_seconds, "
            "cost_usd, error, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (case_id, run_label, result.get("output_text", ""), result.get("score"),
             result.get("duration_seconds"), result.get("cost_usd", 0.0), result.get("error"),
             datetime.datetime.utcnow().isoformat()))
        conn.commit()


def list_benchmark_runs(case_id: int):
    """A case's own run history, oldest first -- the last two entries are
    what a "did this get better or worse" comparison reads."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT * FROM benchmark_runs WHERE case_id = ? ORDER BY id", (case_id,)).fetchall()
    return [dict(r) for r in rows]


def latest_benchmark_run_per_case(stage: str = None):
    """The most recent run for every case (optionally scoped to one
    stage) -- what a fresh "run everything" comparison is checked
    against. Returns {case_id: run_dict}."""
    with contextlib.closing(db.get_conn()) as conn:
        if stage:
            rows = conn.execute(
                "SELECT r.* FROM benchmark_runs r JOIN benchmark_cases c ON r.case_id = c.id "
                "WHERE c.stage = ? ORDER BY r.id", (stage,)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM benchmark_runs ORDER BY id").fetchall()
    latest = {}
    for r in rows:
        latest[r["case_id"]] = dict(r)  # later rows overwrite earlier ones -- id order
    return latest


# ---------------------------------------------------------------------------
# Benchmark Lab -- golden-set cases and persistent per-run records.
# services/benchmark_lab_service.py holds the logic; this is persistence.
# ---------------------------------------------------------------------------

def create_benchmark_lab_case(label: str, stage: str, source_language: str, source_text: str,
                              reference_text: str, tier: str, set_name: str,
                              content_type: str = "novel", origin_drama_id: int = None,
                              origin_line_id: int = None) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO benchmark_cases (label, stage, content_type, source_language, source_text, "
            "reference_text, tier, set_name, origin_drama_id, origin_line_id, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (label, stage, content_type, source_language, source_text, reference_text, tier,
             set_name, origin_drama_id, origin_line_id, datetime.datetime.utcnow().isoformat()))
        conn.commit()
        return cur.lastrowid


def update_benchmark_case_texts(case_id: int, source_text: str, reference_text: str):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE benchmark_cases SET source_text = ?, reference_text = ? WHERE id = ?",
                     (source_text, reference_text, case_id))
        conn.commit()


def get_benchmark_case(case_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM benchmark_cases WHERE id = ?", (case_id,)).fetchone()
    return dict(row) if row else None


def find_benchmark_case(set_name: str, source_text: str, origin_line_id: int = None):
    """An existing case in `set_name` with this source text (or, for a
    regression case, from this line) -- so an import or "add as regression
    test" run twice doesn't store the same example twice."""
    with contextlib.closing(db.get_conn()) as conn:
        if origin_line_id is not None:
            row = conn.execute(
                "SELECT * FROM benchmark_cases WHERE set_name = ? AND origin_line_id = ?",
                (set_name, origin_line_id)).fetchone()
        else:
            row = conn.execute(
                "SELECT * FROM benchmark_cases WHERE set_name = ? AND source_text = ?",
                (set_name, source_text)).fetchone()
    return dict(row) if row else None


def create_benchmark_session(fields: dict) -> int:
    cols = ("label", "stage", "engine", "model", "prompt_version", "context_settings",
            "case_filter", "arena_group", "status", "case_count", "note")
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            f"INSERT INTO benchmark_sessions ({', '.join(cols)}, created_at) "
            f"VALUES ({', '.join('?' for _ in cols)}, ?)",
            tuple(fields.get(c) for c in cols) + (datetime.datetime.utcnow().isoformat(),))
        conn.commit()
        return cur.lastrowid


_BENCHMARK_SESSION_UPDATABLE = {"status", "scored_count", "passed_count", "error_count",
                                "aggregate_score", "avg_latency_seconds", "total_cost_usd",
                                "peak_vram_mb", "note", "finished_at", "case_count"}


def update_benchmark_session(session_id: int, **fields):
    bad = set(fields) - _BENCHMARK_SESSION_UPDATABLE
    if bad:
        raise ValueError(f"not updatable: {sorted(bad)}")
    if not fields:
        return
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute(f"UPDATE benchmark_sessions SET {', '.join(f'{k} = ?' for k in fields)} "
                     "WHERE id = ?", tuple(fields.values()) + (session_id,))
        conn.commit()


def delete_benchmark_sessions(session_ids):
    with contextlib.closing(db.get_conn()) as conn:
        conn.executemany("DELETE FROM benchmark_sessions WHERE id = ?", [(i,) for i in session_ids])
        conn.commit()


def get_benchmark_session(session_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM benchmark_sessions WHERE id = ?", (session_id,)).fetchone()
    return dict(row) if row else None


def list_benchmark_sessions(limit: int = 50, stage: str = None):
    with contextlib.closing(db.get_conn()) as conn:
        if stage:
            rows = conn.execute("SELECT * FROM benchmark_sessions WHERE stage = ? "
                                "ORDER BY id DESC LIMIT ?", (stage, limit)).fetchall()
        else:
            rows = conn.execute("SELECT * FROM benchmark_sessions ORDER BY id DESC LIMIT ?",
                                (limit,)).fetchall()
    return [dict(r) for r in rows]


def save_benchmark_result(session_id: int, case: dict, result: dict):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute(
            "INSERT INTO benchmark_results (session_id, case_id, case_label, output_text, score, "
            "metric, scorer, passed, duration_seconds, cost_usd, error, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, case.get("id"), case.get("label"), result.get("output_text", ""),
             result.get("score"), result.get("metric"), result.get("scorer"),
             None if result.get("passed") is None else int(bool(result["passed"])),
             result.get("duration_seconds"), result.get("cost_usd", 0.0), result.get("error"),
             datetime.datetime.utcnow().isoformat()))
        conn.commit()


def list_benchmark_results(session_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM benchmark_results WHERE session_id = ? ORDER BY id",
                            (session_id,)).fetchall()
    return [dict(r) for r in rows]
