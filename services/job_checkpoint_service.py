"""
services/job_checkpoint_service.py -- Step 41: resume long jobs instead of
starting over, and an opt-in result cache. UI-free; the tables are
`job_checkpoints` and `result_cache` in db.py.

Checkpoints (item 2's pattern, for any job with countable units):

    scope = checkpoint_scope("narration_tag", drama_id,
                             input_hash=hash_text(text), model="claude:x",
                             settings={"chunk": 900})
    done = done_units(scope)            # {unit_id: payload} from an earlier run
    ...skip those units, and after each new one:
    record_unit(scope, unit_id, payload)
    ...when the whole job has saved its real output:
    clear(scope)

The scope folds in the input, the model and the settings, so a run over
different text or with a different engine never reuses another run's
units. A crash, a cancel or a failed save leaves the units in place, and
the next run over the same input picks up where that one stopped. The
user still starts the re-run (nothing restarts by itself on launch).

Result cache (item 1): `cache_get(kind, input_hash, model, settings)` /
`cache_put(...)`. A hit needs all of kind, input, model and settings to
match; change any one and it's a miss. Values are JSON. Each kind keeps at
most CACHE_MAX_PER_KIND rows (oldest dropped). Callers opt in per feature.

Nothing here stores a key or token: callers pass a model/engine name, never
credentials, and `settings` must not carry secrets (it is hashed, but keep
it clean anyway).
"""

import contextlib
import hashlib
import json
import time

import db

CACHE_MAX_PER_KIND = 5000


@contextlib.contextmanager
def _conn():
    """One connection, committed on success and always closed."""
    with contextlib.closing(db.get_conn()) as conn:
        with conn:
            yield conn


def hash_text(text) -> str:
    return hashlib.sha256(str(text or "").encode("utf-8")).hexdigest()


def settings_hash(settings) -> str:
    """Stable hash of a JSON-able settings value (dict key order ignored)."""
    blob = json.dumps(settings if settings is not None else {}, sort_keys=True,
                      ensure_ascii=False, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def checkpoint_scope(job_kind: str, owner, input_hash: str, model: str, settings=None) -> str:
    """`owner` is what the job works on (a drama id, a run's own key)."""
    digest = hashlib.sha256("\x1f".join(
        (str(job_kind), str(owner), str(input_hash), str(model or ""),
         settings_hash(settings))).encode("utf-8")).hexdigest()[:40]
    return f"{job_kind}:{owner}:{digest}"


def record_unit(scope: str, unit_id, payload) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO job_checkpoints (scope, unit_id, payload_json, created_at) "
            "VALUES (?, ?, ?, ?)",
            (scope, str(unit_id), json.dumps(payload, ensure_ascii=False), time.time()))


def done_units(scope: str) -> dict:
    """{unit_id (str): payload} recorded for this scope so far."""
    with _conn() as conn:
        rows = conn.execute("SELECT unit_id, payload_json FROM job_checkpoints WHERE scope = ?",
                            (scope,)).fetchall()
    out = {}
    for unit_id, payload in rows:
        try:
            out[unit_id] = json.loads(payload)
        except ValueError:
            continue   # a damaged row is simply redone
    return out


def clear(scope: str) -> None:
    with _conn() as conn:
        conn.execute("DELETE FROM job_checkpoints WHERE scope = ?", (scope,))


def clear_prefix(job_kind: str, owner) -> None:
    """Drops every scope of `job_kind` for `owner` (e.g. a deleted drama, or
    a finished run whose older, differently-keyed attempts are now moot)."""
    prefix = f"{job_kind}:{owner}:"
    with _conn() as conn:
        conn.execute("DELETE FROM job_checkpoints WHERE substr(scope, 1, ?) = ?",
                     (len(prefix), prefix))


def _cache_key(kind, input_hash, model, settings) -> str:
    return hashlib.sha256("\x1f".join(
        (str(kind), str(input_hash), str(model or ""), settings_hash(settings))
    ).encode("utf-8")).hexdigest()


_MISS = object()


def cache_get(kind: str, input_hash: str, model: str, settings=None, default=None):
    with _conn() as conn:
        row = conn.execute("SELECT value_json FROM result_cache WHERE cache_key = ?",
                           (_cache_key(kind, input_hash, model, settings),)).fetchone()
    if row is None:
        return default
    try:
        return json.loads(row[0])
    except ValueError:
        return default


def cache_has(kind: str, input_hash: str, model: str, settings=None) -> bool:
    return cache_get(kind, input_hash, model, settings, default=_MISS) is not _MISS


def cache_put(kind: str, input_hash: str, model: str, settings, value) -> None:
    with _conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO result_cache (cache_key, kind, input_hash, model, "
            "settings_hash, value_json, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (_cache_key(kind, input_hash, model, settings), kind, input_hash, str(model or ""),
             settings_hash(settings), json.dumps(value, ensure_ascii=False), time.time()))
        conn.execute(
            "DELETE FROM result_cache WHERE kind = ? AND cache_key NOT IN ("
            "SELECT cache_key FROM result_cache WHERE kind = ? "
            "ORDER BY created_at DESC LIMIT ?)", (kind, kind, CACHE_MAX_PER_KIND))


def cache_clear(kind: str = None) -> int:
    with _conn() as conn:
        cur = (conn.execute("DELETE FROM result_cache WHERE kind = ?", (kind,)) if kind
               else conn.execute("DELETE FROM result_cache"))
        return cur.rowcount
