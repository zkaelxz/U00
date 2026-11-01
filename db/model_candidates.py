"""Model re-evaluation: candidate models proposed per capability, and the promote/reject
decisions recorded for each after benchmarking."""

import contextlib
from lib import clock

import db


def create_model_candidate(capability: str, engine: str, model: str, note: str = "") -> int:
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO model_candidates (capability, engine, model, note, status, created_at) "
            "VALUES (?, ?, ?, ?, 'candidate', ?)",
            (capability, engine, model, note, clock.utcnow().isoformat()))
        conn.commit()
        return db.inserted_id(cur)


def get_model_candidate(candidate_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM model_candidates WHERE id = ?", (candidate_id,)).fetchone()
    return dict(row) if row else None


def find_model_candidate(capability: str, engine: str, model: str):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute(
            "SELECT * FROM model_candidates WHERE capability = ? AND engine = ? AND model IS ?",
            (capability, engine, model)).fetchone()
    return dict(row) if row else None


def list_model_candidates(capability: str):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("SELECT * FROM model_candidates WHERE capability = ? ORDER BY id",
                            (capability,)).fetchall()
    return [dict(r) for r in rows]


def set_model_candidate_status(candidate_id: int, status: str):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE model_candidates SET status = ? WHERE id = ?", (status, candidate_id))
        conn.commit()


def record_model_decision(candidate_id: int, decision: str, reason: str, scores_json: str) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute(
            "INSERT INTO model_decisions (candidate_id, decision, reason, scores, decided_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (candidate_id, decision, reason, scores_json, clock.utcnow().isoformat()))
        conn.commit()
        return db.inserted_id(cur)


def latest_model_decision(candidate_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT * FROM model_decisions WHERE candidate_id = ? "
                           "ORDER BY id DESC LIMIT 1", (candidate_id,)).fetchone()
    return dict(row) if row else None


def list_model_decisions(capability: str):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT d.* FROM model_decisions d JOIN model_candidates c ON d.candidate_id = c.id "
            "WHERE c.capability = ? ORDER BY d.id DESC", (capability,)).fetchall()
    return [dict(r) for r in rows]
