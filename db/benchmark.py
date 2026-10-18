"""Benchmark persistence: accuracy cases and runs, and Benchmark Lab
sessions and results. The tables are created by db.init_db."""

import contextlib
import datetime
import json

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


def update_benchmark_case_texts(case_id: int, source_text: str, reference_text: str) -> int:
    """Sets a case's texts. A text that changed is logged in
    benchmark_case_edits and the case's ref_version goes up by one (once per
    call), so results scored on the old text can be told apart. Returns the
    case's version after the call; nothing changes when both texts match."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT source_text, reference_text, ref_version FROM benchmark_cases "
                           "WHERE id = ?", (case_id,)).fetchone()
        if row is None:
            return 0
        version = row["ref_version"] or 1
        changed = [(f, row[f], new) for f, new in (("source_text", source_text),
                                                   ("reference_text", reference_text))
                   if (row[f] or "") != (new or "")]
        if not changed:
            return version
        version += 1
        now = datetime.datetime.utcnow().isoformat()
        conn.executemany(
            "INSERT INTO benchmark_case_edits (case_id, field, old_text, new_text, version, edited_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            [(case_id, f, old, new, version, now) for f, old, new in changed])
        conn.execute("UPDATE benchmark_cases SET source_text = ?, reference_text = ?, ref_version = ? "
                     "WHERE id = ?", (source_text, reference_text, version, case_id))
        conn.commit()
    return version


def list_benchmark_case_edits(case_id: int):
    """A case's reference changes, newest first."""
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM benchmark_case_edits WHERE case_id = ? ORDER BY id DESC",
                            (case_id,)).fetchall()
    return [dict(r) for r in rows]


def set_benchmark_case_excluded(case_id: int, excluded: bool):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE benchmark_cases SET excluded = ? WHERE id = ?", (int(bool(excluded)), case_id))
        conn.commit()


def create_benchmark_clip_cases(rows: list) -> list:
    """Inserts every row of an import in one transaction (a clip import is
    all or nothing) and returns the new case ids in order. Each row carries
    the benchmark_cases columns it sets."""
    cols = ("label", "stage", "content_type", "source_language", "input_filename", "source_text",
            "reference_text", "tier", "set_name", "title", "tags", "scene_order", "clip_key",
            "origin_drama_id", "origin_line_id", "ref_start", "ref_end")
    now = datetime.datetime.utcnow().isoformat()
    ids = []
    with contextlib.closing(db.get_conn()) as conn:
        for row in rows:
            cur = conn.execute(
                f"INSERT INTO benchmark_cases ({', '.join(cols)}, created_at) "
                f"VALUES ({', '.join('?' for _ in cols)}, ?)",
                tuple(row.get(c) for c in cols) + (now,))
            ids.append(cur.lastrowid)
        conn.commit()
    return ids


def list_benchmark_cases_by_clip(clip_key: str):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM benchmark_cases WHERE clip_key = ? ORDER BY id",
                            (clip_key,)).fetchall()
    return [dict(r) for r in rows]


def count_benchmark_cases_with_file(input_filename: str) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        return conn.execute("SELECT COUNT(*) FROM benchmark_cases WHERE input_filename = ?",
                            (input_filename,)).fetchone()[0]


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
            "metric, scorer, passed, duration_seconds, cost_usd, error, ref_version, timing, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (session_id, case.get("id"), case.get("label"), result.get("output_text", ""),
             result.get("score"), result.get("metric"), result.get("scorer"),
             None if result.get("passed") is None else int(bool(result["passed"])),
             result.get("duration_seconds"), result.get("cost_usd", 0.0), result.get("error"),
             case.get("ref_version") or 1,
             json.dumps(result["timing"]) if result.get("timing") else None, datetime.datetime.utcnow().isoformat()))
        conn.commit()


def list_benchmark_results(session_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM benchmark_results WHERE session_id = ? ORDER BY id",
                            (session_id,)).fetchall()
    return [dict(r) for r in rows]
