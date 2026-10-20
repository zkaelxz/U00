"""Helpers shared by the tests/test_auto_backup_*.py files: seeding a library, reading snapshots, and a frozen clock."""

import collections
import contextlib
import datetime
import json
import os
import re
import sqlite3
import threading
import time
import zipfile

import background_jobs
from jobs import job_table
import db
from services import auto_backup_copies as abc_
from services import auto_backup_files as abf_
from services import auto_backup_service as abs_
from services import auto_backup_settings as abt_
from services import drama_restore_service as drs


def _patch_now(monkeypatch, fn):
    for mod in (abs_, abt_, abc_, abf_):
        monkeypatch.setattr(mod, "_now", fn)


UTC = datetime.timezone.utc


def _ddir(did):
    """The drama's folder path (db.drama_dir would create it)."""
    return os.path.join(db.DRAMAS_DIR, str(did))


def _default_dir():
    return os.path.join(db.LIBRARY_DIR, "backups", "auto")


_COPY_RE = re.compile(r"baihe_snapshot-[0-9]{8}-[0-9]{6}\.zip")


def _copy_names(folder=None):
    """The rotating copies' names in `folder` (default folder), oldest first."""
    folder = _default_dir() if folder is None else str(folder)
    if not os.path.isdir(folder):
        return []
    return sorted(n for n in os.listdir(folder) if _COPY_RE.fullmatch(n))


def _newest(folder=None):
    """The newest copy's path in `folder` (default folder), or None."""
    folder = _default_dir() if folder is None else str(folder)
    names = _copy_names(folder)
    return os.path.join(folder, names[-1]) if names else None


def _default_path():
    return _newest()


def _snap(include_media=False):
    """Writes a copy synchronously through the backup job body (the
    progress/result calls are no-ops for an unregistered job id) and
    returns the newest copy's path in the configured folder."""
    abs_._backup_job(abs_.JOB_ID, include_media)
    return abc_._list_copies(abt_._target_dir(create=False))[0]["path"]


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


def _wait_job(job_id=abs_.JOB_ID, timeout=30):
    """Polls until the job is terminal, then joins its thread (#259)."""
    end = time.time() + timeout
    st = None
    while time.time() < end:
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error", "cancelled"):
            break
        time.sleep(0.02)
    else:
        raise AssertionError(f"job {job_id} did not finish: {st}")
    for t in threading.enumerate():
        if t.name == f"job:{job_id}":
            t.join(15)
    return st


def _put_job(job_id, status="running"):
    with job_table._lock:
        job_table._jobs[job_id] = {
            "status": status, "progress": 0.0, "message": "", "result": None,
            "error": None, "started_at": time.time(), "finished_at": None}


def _conn():
    c = sqlite3.connect(db.DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _tables(c):
    return [r[0] for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name")]


def _dump_all():
    """Every row of every table (sqlite_sequence aside), as a multiset of
    reprs per table."""
    with contextlib.closing(_conn()) as c:
        return {t: collections.Counter(repr(tuple(r)) for r in c.execute(f'SELECT * FROM "{t}"'))
                for t in _tables(c)}


def _insert(c, table, **row):
    cols = ", ".join(f'"{k}"' for k in row)
    marks = ", ".join("?" for _ in row)
    return c.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({marks})', list(row.values())).lastrowid


# The table a library made before it left the schema still has; the backup and
# restore code must keep carrying its rows.
_LEGACY_BUG_REPORTS = """CREATE TABLE IF NOT EXISTS bug_reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT, drama_id INTEGER NOT NULL, line_id INTEGER,
    label TEXT, input_json TEXT, engine TEXT, model TEXT, produced_output TEXT, flag TEXT,
    flag_note TEXT, replay_output TEXT, replayed INTEGER DEFAULT 0, reproduced INTEGER,
    created_at TEXT, FOREIGN KEY (drama_id) REFERENCES dramas(id) ON DELETE CASCADE)"""


def _seed_series(c, name="Saga"):
    sid = _insert(c, "series", name=name, created_at="2026-01-01T00:00:00",
                  instructions="series instructions")
    sc = {n: _insert(c, "series_characters", series_id=sid, character_name=n, aliases=f"{n}-alias",
                     notes=f"{n} notes", created_at="2026-01-02", gender="f")
          for n in ("Lin", "Mei")}
    _insert(c, "glossary_terms", series_id=sid, term_original="师父", term_translation="Master",
            notes="n", category="title", policy="preserve")
    _insert(c, "glossary_terms", series_id=sid, term_original="剑", term_translation="sword")
    _insert(c, "translation_memory", series_id=sid, source_text="你好", translation="Hello",
            use_count=3, updated_at="2026-01-03")
    _insert(c, "glossary_dismissals", series_id=sid, term_original="路人", created_at="2026-01-04")
    return sid, sc


def _seed_drama(c, title, series_id=None, sc=None, profiles=(), tag="A"):
    """A drama with at least one row in every _CHILD_TABLES table (and the
    two skipped ones), plus bubbles under its pages. Line zh texts are
    unique per drama (tag prefix) so rows can be matched across ids."""
    did = _insert(c, "dramas", title_en=title, title_zh=f"{title}中", status="translated",
                  media_type="audio", series_id=series_id, summary=f"{title} summary",
                  custom_tags="Favorite", created_at="2026-01-01T00:00:00",
                  updated_at="2026-01-01T00:00:00", source_language="zh")
    lines = {}
    for i in range(4):
        zh = f"{tag}-zh-{i}"
        lines[i] = _insert(c, "lines", drama_id=did, idx=i, start=float(i), end=i + 0.5, zh=zh,
                           en=f"{tag}-en-{i}", speaker="Lin" if i % 2 else "Mei",
                           flag=1 if i == 2 else 0, flag_note="check" if i == 2 else None)
    for i in range(2):
        pid = _insert(c, "pages", drama_id=did, idx=i, filename=f"{tag}-page{i}.png",
                      width=800, height=1200)
        for j in range(2):
            _insert(c, "bubbles", page_id=pid, idx=j, x=10 * j, y=20, w=30, h=40,
                    source_text=f"{tag}-b{i}{j}", translated_text=f"{tag}-t{i}{j}")
    sc = sc or {}
    _insert(c, "characters", drama_id=did, speaker_label="S0", character_name="Lin",
            series_character_id=sc.get("Lin"), tts_voice="v1")
    _insert(c, "characters", drama_id=did, speaker_label="S1", character_name="Nobody",
            series_character_id=None)
    _insert(c, "translation_notes", drama_id=did, line_id=lines[1], line_idx=1, term="t",
            note_type="culture", note=f"{tag} note", created_at="x")
    _insert(c, "line_emotions", drama_id=did, line_id=lines[2], line_idx=2, emotion="angry",
            intensity=0.7, note="e", created_at="x")
    _insert(c, "consistency_issues", drama_id=did, term="剑", variants="sword|blade",
            note="c", created_at="x")
    _insert(c, "vocab_lookups", drama_id=did, word="剑", reading="jian", definitions="sword",
            language="zh", first_seen_line_idx=0, created_at="x")
    snap_items = [{"id": lines[i], "idx": i, "zh": f"{tag}-zh-{i}", "en": "old"} for i in range(4)]
    _insert(c, "line_history", drama_id=did, label="before merge",
            snapshot_json=json.dumps(snap_items), created_at="x")
    _insert(c, "translation_versions", drama_id=did, label="v1", engine="claude", model="m",
            is_active=1, lines_json=json.dumps(snap_items), created_at="x")
    c.execute(_LEGACY_BUG_REPORTS)
    _insert(c, "bug_reports", drama_id=did, line_id=lines[3], label="bad", input_json="{}",
            engine="claude", model="m", produced_output="o", created_at="x")
    _insert(c, "wiki_entries", drama_id=did, entry_type="character", name="Lin",
            description="d", updated_at="x")
    _insert(c, "edit_samples", drama_id=did, zh="z", ai_version="a", user_version="u",
            created_at="x")
    _insert(c, "metadata_field_provenance", drama_id=did, field="author", value=f"Au {tag}",
            source="grounded", status="applied")
    if sc.get("Mei"):
        _insert(c, "voice_suggestion_dismissals", drama_id=did, speaker_label="S1",
                series_character_id=sc["Mei"], created_at="x")
    for p in profiles:
        _insert(c, "progress", drama_id=did, profile_id=p, last_line_idx=2, percent_complete=50.0,
                last_accessed_at="x")
        _insert(c, "personal_notes", profile_id=p, drama_id=did, notes=f"notes {p}",
                updated_at="x")
        _insert(c, "reading_history", drama_id=did, profile_id=p, line_id=lines[2], line_idx=2,
                percent_complete=50.0, accessed_at="x")
    _insert(c, "usage_log", drama_id=did, engine="claude", model="m", operation="translate",
            input_tokens=1, output_tokens=1, estimated_cost_usd=0.5, created_at="x")
    _insert(c, "bulk_jobs", drama_id=did, engine="claude", model="m", provider_batch_id="b1",
            status="submitted", submitted_at="x", updated_at="x")
    _insert(c, "metadata_research_results", research_id=f"r-{tag}", drama_id=did,
            result_json="{}", created_at="2099-01-01T00:00:00")
    return did


def _seed_world():
    """Drama A (in series "Saga", every child table, two profiles) and an
    unrelated drama B created after it (so A's restored rows can't reuse A's
    old ids)."""
    with contextlib.closing(_conn()) as c:
        sid, sc = _seed_series(c)
        p1 = _insert(c, "profiles", name="P1", color="red", created_at="x")
        p2 = _insert(c, "profiles", name="P2", color="blue", created_at="x")
        a = _seed_drama(c, "Alpha", series_id=sid, sc=sc, profiles=(p1, p2), tag="A")
        b = _seed_drama(c, "Beta", profiles=(p1,), tag="B")
        c.commit()
    return {"a": a, "b": b, "series": sid, "sc": sc, "p1": p1, "p2": p2}


SERIES_CHILDREN = ("glossary_terms", "series_characters", "translation_memory",
                   "glossary_dismissals")


def _delete_drama(did):
    db.delete_drama(did)
    assert db.get_drama(did) is None


def _delete_series(series_id):
    with contextlib.closing(_conn()) as c:
        for t in SERIES_CHILDREN:
            c.execute(f'DELETE FROM "{t}" WHERE series_id = ?', (series_id,))
        c.execute("DELETE FROM series WHERE id = ?", (series_id,))
        c.commit()


def _enable(**kw):
    abs_.set_settings(enabled=True, **kw)


def _state(**kw):
    db.set_app_setting(abt_.STATE_KEY, kw)


def _restore(did):
    return drs.restore_drama(did, confirm=True, confirm_text="RESTORE")


def _at(day, hour=3, month=3):
    return datetime.datetime(2026, month, day, hour, 0, 0, tzinfo=UTC)


def _run_at(monkeypatch, when):
    """One real backup run with _now faked to `when`."""
    _patch_now(monkeypatch, lambda: when)
    return _snap()


def _untag(path, when):
    """Rewrites the copy as an app from before copies carried a library id
    wrote it: no library_id or sequence, created_at `when`."""
    with zipfile.ZipFile(path) as zf:
        members = {i.filename: zf.read(i) for i in zf.infolist()}
    manifest = json.loads(members["manifest.json"])
    manifest.pop("library_id")
    manifest.pop("sequence")
    manifest["created_at"] = abt_._iso(when)
    members["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _make_legacy(when):
    """A pre-rotation baihe_snapshot.zip (a real snapshot, as the app wrote
    it before copies carried a library id) dated `when`."""
    path = _snap()
    legacy = os.path.join(os.path.dirname(path), abc_.LEGACY_SNAPSHOT_NAME)
    os.replace(path, legacy)
    _untag(legacy, when)
    ts = when.timestamp()
    os.utime(legacy, (ts, ts))
    return legacy


def _series_rows():
    with contextlib.closing(_conn()) as c:
        return {r["id"]: dict(r) for r in c.execute("SELECT * FROM series")}
