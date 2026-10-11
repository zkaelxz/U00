"""Cross-process app settings and the metadata-research stores: the
research cache, applied research results and field provenance. The tables
are created by db.init_db."""

import contextlib
import datetime
import json

import db


# ---------------------------------------------------------------------------
# App settings and metadata research cache
# ---------------------------------------------------------------------------

def get_app_setting(key: str, default=None):
    """A general-purpose, cross-process app setting -- distinct from sources/store.py's own settings table,
    which is scoped to the source-adapter system only."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
    if row is None:
        return default
    return json.loads(row["value"])


def set_app_setting(key: str, value):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO app_settings (key, value) VALUES (?, ?)
            ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """, (key, json.dumps(value)))
        conn.commit()


def get_research_cache(cache_key: str):
    """A cached grounded-research result (the decoded dict), or None."""
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT result_json FROM metadata_research_cache WHERE cache_key = ?",
                           (cache_key,)).fetchone()
    return json.loads(row["result_json"]) if row else None


def put_research_cache(cache_key: str, result: dict):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO metadata_research_cache (cache_key, result_json, created_at)
            VALUES (?, ?, ?)
            ON CONFLICT(cache_key) DO UPDATE SET result_json = excluded.result_json,
                                                 created_at = excluded.created_at
        """, (cache_key, json.dumps(result), datetime.datetime.utcnow().isoformat()))
        conn.commit()


RESEARCH_RESULT_TTL_DAYS = 7


def put_research_result(research_id: str, drama_id: int, result: dict):
    """The snapshot a research_id applies; rows older than
    RESEARCH_RESULT_TTL_DAYS are pruned on each insert."""
    now = datetime.datetime.utcnow()
    cutoff = (now - datetime.timedelta(days=db.RESEARCH_RESULT_TTL_DAYS)).isoformat()
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM metadata_research_results WHERE created_at < ?", (cutoff,))
        conn.execute("INSERT INTO metadata_research_results (research_id, drama_id, result_json, "
                     "created_at) VALUES (?, ?, ?, ?)",
                     (research_id, drama_id, json.dumps(result), now.isoformat()))
        conn.commit()


def get_research_result(research_id: str, drama_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        row = conn.execute("SELECT result_json FROM metadata_research_results "
                           "WHERE research_id = ? AND drama_id = ?",
                           (research_id, drama_id)).fetchone()
    return json.loads(row["result_json"]) if row else None


def add_field_provenance(drama_id: int, field: str, value: str, status: str, *,
                         sources=None, retrieved_at: str = None, confidence: float = None,
                         last_verified: str = None):
    """One researched field value with its own evidence. `sources`
    is a list of {"title", "url"}; the first is also stored as source /
    source_url."""
    sources = list(sources or [])
    first = sources[0] if sources else {}
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("""
            INSERT INTO metadata_field_provenance (drama_id, field, value, source, source_url,
                sources_json, retrieved_at, confidence, last_verified, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (drama_id, field, value, first.get("title"), first.get("url"), json.dumps(sources),
              retrieved_at, confidence, last_verified, status))
        conn.commit()


def list_field_provenance(drama_id: int):
    with contextlib.closing(db.get_conn()) as conn:
        rows = conn.execute("""
            SELECT id, field, value, source, source_url, sources_json, retrieved_at, confidence,
                   last_verified, status
            FROM metadata_field_provenance WHERE drama_id = ? ORDER BY id
        """, (drama_id,)).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["sources"] = json.loads(d.pop("sources_json") or "[]")
        out.append(d)
    return out
