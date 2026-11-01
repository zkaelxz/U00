"""Usage and cost logging: the usage log, monthly spend and its reset,
re-costing, the per-drama and library totals, and the standalone translate
tool's history. The tables are created by db.init_db."""

import contextlib
import datetime
from lib import clock
from typing import List

import db


# ---------------------------------------------------------------------------
# Usage/cost logging
# ---------------------------------------------------------------------------

# Called with each logged cost. A higher layer registers here because db
# cannot import it (job_timing_service counts spend toward a job's stage).
_spend_listeners = []


def on_spend(listener):
    _spend_listeners.append(listener)


def log_usage(drama_id: int | None, engine: str, model: str, operation: str,
              input_tokens: int = 0, output_tokens: int = 0, estimated_cost_usd: float = 0.0,
              cache_read_tokens: int = 0):
    """input_tokens is the whole prompt; cache_read_tokens is the part of
    it a provider served from its prompt cache."""
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO usage_log (drama_id, engine, model, operation, input_tokens,
                                    output_tokens, estimated_cost_usd, created_at, cache_read_tokens)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (drama_id, engine, model, operation, input_tokens, output_tokens,
              estimated_cost_usd, clock.utcnow().isoformat(), cache_read_tokens or 0))
        conn.commit()
    for listener in _spend_listeners:
        try:
            listener(estimated_cost_usd)
        except Exception:
            pass


MONTHLY_SPEND_RESET_KEY = "monthly_spend_reset_at"


def get_month_spend_reset_at(now: datetime.datetime | None = None):
    """The saved reset time (UTC ISO) while it applies to this month, else None.
    A reset from an earlier month is ignored so the new month starts at its
    own first day without anyone having to undo it. A marker that is not a
    datetime or lies after `now` (clock was ahead when Reset was pressed, or a
    restored library.db) is ignored too: honouring it would count ~nothing and
    silently switch the monthly cap off."""
    now = now or clock.utcnow()
    saved = db.get_app_setting(db.MONTHLY_SPEND_RESET_KEY)
    if not isinstance(saved, str):
        return None
    try:
        when = datetime.datetime.fromisoformat(saved)
    except ValueError:
        return None
    if when.tzinfo is not None:
        when = when.astimezone(datetime.timezone.utc).replace(tzinfo=None)
    month_start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return when.isoformat() if month_start <= when <= now else None


def get_month_spend(now: datetime.datetime | None = None, since_reset: bool = True) -> float:
    """Estimated spend logged so far in the current calendar month (UTC),
    across the whole library. By default only rows at or after an active
    reset count: this is what the monthly cap is checked against.
    `since_reset=False` is the full month, for stats and display."""
    now = now or clock.utcnow()
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    if since_reset:
        start = db.get_month_spend_reset_at(now) or start
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(estimated_cost_usd), 0) AS spent FROM usage_log WHERE created_at >= ?",
            (start,)).fetchone()
    return float(row["spent"])


def usage_recost_candidates(models) -> list:
    """Usage rows of exactly `models` with a real stored cost. Rows already
    re-costed are included: a later price correction may lower them again,
    and the apply step skips any whose figure already matches."""
    marks = ",".join("?" * len(models))
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(
            "SELECT id, model, operation, input_tokens, output_tokens, cache_read_tokens, "
            "estimated_cost_usd, created_at FROM usage_log "
            f"WHERE estimated_cost_usd > 0 AND model IN ({marks})",
            tuple(models)).fetchall()
    return [dict(r) for r in rows]


def apply_usage_recost(updates) -> int:
    """`updates` is [(row_id, expected_old_cost, new_cost), ...]. A row's
    first original is kept in estimated_cost_usd_before_recost (never
    overwritten by a later re-cost, so Undo returns the logged figure), and
    a row that changed since it was read is skipped. All in one transaction;
    returns how many rows were written."""
    written = 0
    conn = db.get_conn()
    try:
        # IMMEDIATE, not a deferred BEGIN: see the WAL note in get_conn.
        conn.execute("BEGIN IMMEDIATE")
        for row_id, old, new in updates:
            written += conn.execute(
                "UPDATE usage_log SET estimated_cost_usd_before_recost = "
                "COALESCE(estimated_cost_usd_before_recost, estimated_cost_usd), "
                "estimated_cost_usd = ? WHERE id = ? AND estimated_cost_usd = ?",
                (new, row_id, old)).rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    return written


def undo_usage_recost() -> int:
    """Puts every re-costed row's saved cost back; returns how many rows."""
    with contextlib.closing(db.get_conn()) as conn:
        n = conn.execute(
            "UPDATE usage_log SET estimated_cost_usd = estimated_cost_usd_before_recost, "
            "estimated_cost_usd_before_recost = NULL "
            "WHERE estimated_cost_usd_before_recost IS NOT NULL").rowcount
        conn.commit()
    return n


def get_usage_summary(drama_id: int | None = None, visible_to: int | None = None):
    """Returns {"input_tokens", "output_tokens", "estimated_cost_usd", "call_count"} --
    totals for one drama, or the whole library if drama_id is None.
    `visible_to` (auth B2, library totals only): only calls logged against
    a drama that user may see (calls with no drama are left out)."""
    with contextlib.closing(db.get_conn()) as conn:
        if not drama_id and visible_to is not None:
            clause, params = db.drama_visible_sql("d", visible_to)
            row = conn.execute(f"""
                SELECT COALESCE(SUM(input_tokens),0) as input_tokens,
                       COALESCE(SUM(output_tokens),0) as output_tokens,
                       COALESCE(SUM(cache_read_tokens),0) as cache_read_tokens,
                       COALESCE(SUM(estimated_cost_usd),0) as estimated_cost_usd,
                       COUNT(*) as call_count
                FROM usage_log u JOIN dramas d ON d.id = u.drama_id WHERE {clause}
            """, params).fetchone()
        elif drama_id:
            row = conn.execute("""
                SELECT COALESCE(SUM(input_tokens),0) as input_tokens,
                       COALESCE(SUM(output_tokens),0) as output_tokens,
                       COALESCE(SUM(cache_read_tokens),0) as cache_read_tokens,
                       COALESCE(SUM(estimated_cost_usd),0) as estimated_cost_usd,
                       COUNT(*) as call_count
                FROM usage_log WHERE drama_id = ?
            """, (drama_id,)).fetchone()
        else:
            row = conn.execute("""
                SELECT COALESCE(SUM(input_tokens),0) as input_tokens,
                       COALESCE(SUM(output_tokens),0) as output_tokens,
                       COALESCE(SUM(cache_read_tokens),0) as cache_read_tokens,
                       COALESCE(SUM(estimated_cost_usd),0) as estimated_cost_usd,
                       COUNT(*) as call_count
                FROM usage_log
            """).fetchone()
    return dict(row)


def get_usage_by_drama(visible_to: int | None = None):
    """Per-drama cost breakdown, joined with drama titles, for the dashboard.
    Includes translation_engine so the dashboard can show "$0.00 (free)"
    for a free engine instead of a bare, ambiguous-looking $0.00.
    `visible_to` (auth B2): only dramas that user may see."""
    where, params = "", []
    if visible_to is not None:
        clause, params = db.drama_visible_sql("d", visible_to)
        where = "WHERE " + clause
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute(f"""
            SELECT d.id, d.title_en, d.title_zh, d.translation_engine,
                   COALESCE(SUM(u.input_tokens),0) as input_tokens,
                   COALESCE(SUM(u.output_tokens),0) as output_tokens,
                   COALESCE(SUM(u.cache_read_tokens),0) as cache_read_tokens,
                   COALESCE(SUM(u.estimated_cost_usd),0) as estimated_cost_usd,
                   COUNT(u.id) as call_count
            FROM dramas d LEFT JOIN usage_log u ON u.drama_id = d.id
            {where}
            GROUP BY d.id ORDER BY estimated_cost_usd DESC
        """, params).fetchall()
    return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Standalone translate tool history
# ---------------------------------------------------------------------------

def save_translate_history(source_language: str, target_language: str, engine: str,
                           source_text: str, translated_text: str, user_id: int | None = None) -> int:
    with contextlib.closing(db.get_conn()) as conn:
        cur = conn.execute("""
            INSERT INTO translate_history (source_language, target_language, engine,
                                            source_text, translated_text, created_at, user_id)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (source_language, target_language, engine, source_text, translated_text,
              clock.utcnow().isoformat(), user_id))
        conn.commit()
        new_id = db.inserted_id(cur)
    return new_id


def list_translate_history(limit: int = 50, visible_to: int | None = None) -> List[dict]:
    """Most recent first. `visible_to` (auth B2): only that user's own rows
    (rows with no user -- the PC, the extension -- are for admins and the
    local owner, who pass None)."""
    with contextlib.closing(db.get_conn()) as conn:
        if visible_to is None:
            rows = conn.execute(
                "SELECT * FROM translate_history ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM translate_history WHERE user_id = ? "
                "ORDER BY created_at DESC LIMIT ?", (visible_to, limit)).fetchall()
    return [dict(r) for r in rows]


def clear_translate_history():
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM translate_history")
        conn.commit()
