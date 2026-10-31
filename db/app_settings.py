"""Cross-process app settings and the metadata-research stores: the
research cache, applied research results and field provenance. The tables
are created by db.init_db."""

import contextlib
import datetime
from lib import clock
import json
import threading

import db


# ---------------------------------------------------------------------------
# App settings and metadata research cache
# ---------------------------------------------------------------------------

_read_memo = threading.local()


@contextlib.contextmanager
def app_settings_read_memo():
    """Inside the block each setting is fetched from the database once on this
    thread. A read-only view that consults the same settings dozens of times
    (the Translate stage reads the model overrides once per engine and tier)
    otherwise opens a connection, and parses the schema, for every read. A
    write through set_app_setting on this thread empties the memo; wrap only
    code that writes settings no other way."""
    if getattr(_read_memo, "values", None) is not None:
        yield
        return
    _read_memo.values = {}
    try:
        yield
    finally:
        _read_memo.values = None


def get_app_setting(key: str, default=None):
    """A general-purpose, cross-process app setting -- distinct from sources/store.py's own settings table,
    which is scoped to the source-adapter system only."""
    memo = getattr(_read_memo, "values", None)
    if memo is not None and key in memo:
        raw = memo[key]
    else:
        with contextlib.closing(db.get_conn()) as conn:
            row = conn.execute("SELECT value FROM app_settings WHERE key = ?", (key,)).fetchone()
        raw = None if row is None else row["value"]
        if memo is not None:
            memo[key] = raw
    # Decoded on every read, so a caller that changes the value it got back
    # can't change what the next read in the same block returns.
    return default if raw is None else json.loads(raw)


def set_app_setting(key: str, value):
    if getattr(_read_memo, "values", None):
        _read_memo.values.clear()
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
        """, (cache_key, json.dumps(result), clock.utcnow().isoformat()))
        conn.commit()


RESEARCH_RESULT_TTL_DAYS = 7


def put_research_result(research_id: str, drama_id: int, result: dict):
    """The snapshot a research_id applies; rows older than
    RESEARCH_RESULT_TTL_DAYS are pruned on each insert."""
    now = clock.utcnow()
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
