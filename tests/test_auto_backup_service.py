"""
Roadmap Step 43 (redefined by the user 2026-09-29): the automatic backup
snapshot and single-drama restore in services/auto_backup_service.py, plus
the pieces it relies on (library_admin_service's zip writer/validator,
drama_service.cleanup_stale_tombstones, api/background.py's startup sweep and
hourly tick).

Everything runs against an isolated temp library (isolated_db): no network,
no GPU, no models. Most snapshots are written by calling the backup job body
directly (synchronous, no thread); the tests that go through the real job
thread wait for it to finish and join it before asserting (#259).

Test-authored, independent of the implementer; written from the spec, not
from the code's own comments.
"""

import collections
import contextlib
import datetime
import json
import os
import re
import shutil
import sqlite3
import threading
import time
import zipfile

import pytest

import background_jobs
import db
from services import auto_backup_service as abs_
from services import drama_service
from services import library_admin_service as las
from services import workspace_job_service as wjs
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError)

UTC = datetime.timezone.utc


# --------------------------------------------------------------------------
# helpers
# --------------------------------------------------------------------------

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
    return abs_._list_copies(abs_._target_dir(create=False))[0]["path"]


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
    with background_jobs._lock:
        background_jobs._jobs[job_id] = {
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


def _files_under(path):
    out = set()
    for root, dirs, files in os.walk(path):
        for d in dirs:
            out.add(os.path.relpath(os.path.join(root, d), path) + "/")
        for f in files:
            out.add(os.path.relpath(os.path.join(root, f), path))
    return out


def _insert(c, table, **row):
    cols = ", ".join(f'"{k}"' for k in row)
    marks = ", ".join("?" for _ in row)
    return c.execute(f'INSERT INTO "{table}" ({cols}) VALUES ({marks})', list(row.values())).lastrowid


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


# From the spec, deliberately not read from the module under test.
SKIPPED = {"usage_log", "bulk_jobs", "metadata_research_results", "speaker_merge_undos"}
LINE_JSON = {"translation_versions": "lines_json", "line_history": "snapshot_json"}
LINE_REF_TABLES = ("translation_notes", "line_emotions", "reading_history", "bug_reports")
PROFILE_TABLES = ("progress", "personal_notes", "reading_history")
SERIES_CHILDREN = ("glossary_terms", "series_characters", "translation_memory",
                   "glossary_dismissals")


def _fk_child_tables():
    """Live tables with a foreign key to dramas(id), minus the ones the
    service deliberately skips -- read from the schema, not from the module under test."""
    with contextlib.closing(_conn()) as c:
        return sorted({t for t in _tables(c)
                       for fk in c.execute(f'PRAGMA foreign_key_list("{t}")')
                       if fk["table"] == "dramas"} - SKIPPED)


def _canon(did):
    """Drama `did`'s row, every child table, its bubbles and its series,
    with every id replaced by the thing it points at (line zh, series
    character name, profile name, page filename, series name), so rows can
    be compared across a restore that gives them new ids."""
    with contextlib.closing(_conn()) as c:
        line_zh = {r["id"]: r["zh"] for r in c.execute("SELECT id, zh FROM lines WHERE drama_id = ?",
                                                        (did,))}
        sc_name = {r["id"]: r["character_name"] for r in c.execute(
            "SELECT id, character_name FROM series_characters")}
        prof = {r["id"]: r["name"] for r in c.execute("SELECT id, name FROM profiles")}
        page_fn = {r["id"]: r["filename"] for r in c.execute(
            "SELECT id, filename FROM pages WHERE drama_id = ?", (did,))}

        def line_ref(v):
            return None if v is None else ("line", line_zh.get(v, ("DANGLING", v)))

        out = {}
        for t in _fk_child_tables():
            rows = []
            for r in c.execute(f'SELECT * FROM "{t}" WHERE drama_id = ?', (did,)):
                d = dict(r)
                d.pop("id", None)
                d.pop("drama_id")
                if "line_id" in d:
                    d["line_id"] = line_ref(d["line_id"])
                if d.get("series_character_id") is not None:
                    d["series_character_id"] = ("sc", sc_name.get(d["series_character_id"],
                                                                  "DANGLING"))
                if "profile_id" in d:
                    d["profile_id"] = ("profile", prof.get(d["profile_id"], "DANGLING"))
                if t in LINE_JSON:
                    col = LINE_JSON[t]
                    items = json.loads(d[col])
                    for it in items:
                        it["id"] = line_ref(it["id"])
                    d[col] = json.dumps(items, sort_keys=True)
                rows.append(repr(sorted(d.items())))
            out[t] = sorted(rows)
        bubbles = []
        for pid, fn in page_fn.items():
            for r in c.execute("SELECT * FROM bubbles WHERE page_id = ?", (pid,)):
                d = dict(r)
                d.pop("id")
                d["page_id"] = fn
                bubbles.append(repr(sorted(d.items())))
        out["bubbles"] = sorted(bubbles)
        drama = dict(c.execute("SELECT * FROM dramas WHERE id = ?", (did,)).fetchone())
        drama.pop("id")
        drama.pop("updated_at")
        series_id = drama.pop("series_id")
        if series_id is not None:
            s = dict(c.execute("SELECT * FROM series WHERE id = ?", (series_id,)).fetchone())
            s.pop("id")
            out["series"] = repr(sorted(s.items()))
            for t in SERIES_CHILDREN:
                rows = []
                for r in c.execute(f'SELECT * FROM "{t}" WHERE series_id = ?', (series_id,)):
                    d = dict(r)
                    d.pop("id")
                    d.pop("series_id")
                    rows.append(repr(sorted(d.items())))
                out[t] = sorted(rows)
        else:
            out["series"] = None
        out["dramas"] = repr(sorted(drama.items()))
    return out


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
    db.set_app_setting(abs_.STATE_KEY, kw)


def _restore(did):
    return abs_.restore_drama(did, confirm=True, confirm_text="RESTORE")


# --------------------------------------------------------------------------
# settings
# --------------------------------------------------------------------------

class TestSettings:
    def test_defaults_off_daily_db_only_default_folder(self, isolated_db):
        s = abs_.get_settings()
        assert s == {"enabled": False, "frequency": "daily", "include_media": False,
                     "folder": ""}
        ov = abs_.settings_overview()
        assert ov["enabled"] is False and ov["next_run_at"] is None
        assert ov["last_run_at"] is None and ov["last_error"] is None
        assert ov["running"] is False and ov["copies"] == []
        assert set(ov["frequencies"]) == {"daily", "weekly", "monthly"}

    def test_saved_weekly_is_kept_after_the_default_changed(self, isolated_db):
        """Settings saved while the default was weekly keep their value."""
        db.set_app_setting(abs_.SETTINGS_KEY, {"enabled": True, "frequency": "weekly",
                                              "include_media": False, "folder": ""})
        assert abs_.get_settings()["frequency"] == "weekly"
        abs_.set_settings(include_media=True)
        assert abs_.get_settings()["frequency"] == "weekly"

    def test_frequency_days(self):
        assert abs_.FREQUENCIES == {"daily": 1, "weekly": 7, "monthly": 30}

    def test_set_and_partial_update(self, isolated_db):
        abs_.set_settings(enabled=True, frequency="daily", include_media=True)
        abs_.set_settings(frequency="monthly")
        assert abs_.get_settings() == {"enabled": True, "frequency": "monthly",
                                       "include_media": True, "folder": ""}

    @pytest.mark.parametrize("kw", [
        {"enabled": "yes"}, {"enabled": 1}, {"frequency": "hourly"}, {"frequency": "Weekly"},
        {"include_media": "true"}, {"folder": 5}, {"folder": "a\x00b"},
    ])
    def test_invalid_values_refused_nothing_saved(self, isolated_db, kw):
        with pytest.raises(InvalidInputError):
            abs_.set_settings(**kw)
        assert db.get_app_setting(abs_.SETTINGS_KEY, None) is None

    def test_corrupt_stored_settings_fall_back_to_defaults(self, isolated_db):
        db.set_app_setting(abs_.SETTINGS_KEY, {"enabled": "yes", "frequency": "hourly",
                                              "include_media": 1, "folder": 3})
        assert abs_.get_settings() == abs_.DEFAULT_SETTINGS
        db.set_app_setting(abs_.SETTINGS_KEY, ["not", "a", "dict"])
        assert abs_.get_settings() == abs_.DEFAULT_SETTINGS


class TestFolderSetting:
    def test_relative_refused(self, isolated_db):
        with pytest.raises(InvalidInputError):
            abs_.set_settings(folder="backups/auto")
        with pytest.raises(InvalidInputError):
            abs_.set_settings(folder="relative")

    @pytest.mark.parametrize("sub", ["", "dramas", "other"])
    def test_inside_library_but_not_backups_refused(self, isolated_db, sub):
        path = os.path.join(db.LIBRARY_DIR, sub) if sub else db.LIBRARY_DIR
        os.makedirs(path, exist_ok=True)
        with pytest.raises(InvalidInputError) as e:
            abs_.set_settings(folder=path)
        assert db.LIBRARY_DIR not in str(e.value)
        assert abs_.get_settings()["folder"] == ""

    def test_symlink_outside_pointing_into_library_refused(self, isolated_db, tmp_path):
        inside = os.path.join(db.LIBRARY_DIR, "dramas")
        os.makedirs(inside, exist_ok=True)
        link = tmp_path / "sneaky"
        os.symlink(inside, link)
        with pytest.raises(InvalidInputError):
            abs_.set_settings(folder=str(link))

    def test_inside_library_backups_ok(self, isolated_db):
        path = os.path.join(db.LIBRARY_DIR, "backups", "mine")
        os.makedirs(path)
        abs_.set_settings(folder=path)
        assert abs_.get_settings()["folder"] == os.path.normpath(path)

    def test_missing_dir_refused(self, isolated_db, tmp_path):
        with pytest.raises(InvalidInputError):
            abs_.set_settings(folder=str(tmp_path / "nope"))
        with pytest.raises(InvalidInputError):
            abs_.set_settings(folder=os.path.join(db.LIBRARY_DIR, "backups", "nope"))

    def test_outside_ok_and_snapshot_written_there(self, isolated_db, tmp_path):
        out = tmp_path / "ext"
        out.mkdir()
        abs_.set_settings(folder=str(out))
        _snap()
        assert os.listdir(out) == _copy_names(out) and len(_copy_names(out)) == 1
        assert _default_path() is None

    @pytest.mark.parametrize("kind,sub", [("backup", ("backups",)),
                                          ("export", ("backups", "exports"))])
    def test_snapshot_never_listed_as_manual_artifact(self, isolated_db, kind, sub):
        db.create_drama(title_en="A")
        folder = os.path.join(db.LIBRARY_DIR, *sub)
        os.makedirs(folder, exist_ok=True)
        # The manual backup/export folders are refused, so the snapshot can
        # never be served as the "latest backup"/"latest export".
        with pytest.raises(InvalidInputError):
            abs_.set_settings(folder=folder)
        _snap()
        with pytest.raises(NotFoundError):
            las.latest_admin_artifact(kind)

    def test_empty_resets_to_default(self, isolated_db, tmp_path):
        abs_.set_settings(folder=str(tmp_path))
        abs_.set_settings(folder="")
        assert abs_.get_settings()["folder"] == ""
        assert abs_._target_dir(create=False) == _default_dir()


# --------------------------------------------------------------------------
# due-check / scheduler
# --------------------------------------------------------------------------

@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(abs_, "_start", lambda include_media: calls.append(include_media) or True)
    return calls


class TestDueCheck:
    def test_disabled_by_default(self, isolated_db, started):
        assert abs_.check_and_run() == "disabled"
        assert abs_.is_due() is False
        assert abs_.next_run_at() is None
        assert started == []

    def test_never_run_is_due(self, isolated_db, started):
        _enable()
        assert abs_.is_due() is True
        assert abs_.check_and_run() == "started"
        assert started == [False]

    @pytest.mark.parametrize("freq,days", [("daily", 1), ("weekly", 7), ("monthly", 30)])
    def test_boundary(self, isolated_db, started, freq, days):
        _enable(frequency=freq, include_media=True)
        last = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        _state(last_success_at=last.isoformat())
        period = datetime.timedelta(days=days)
        just_before = last + period - datetime.timedelta(seconds=1)
        assert abs_.check_and_run(now=just_before) == "not_due"
        assert started == []
        assert abs_.check_and_run(now=last + period) == "started"
        assert abs_.check_and_run(now=last + period + datetime.timedelta(seconds=1)) == "started"
        assert started == [True, True]
        assert abs_.next_run_at() == last + period

    def test_disabled_even_when_overdue(self, isolated_db, started):
        _state(last_success_at="2000-01-01T00:00:00+00:00")
        assert abs_.check_and_run() == "disabled"
        assert started == []

    def test_failed_attempt_waits_a_day_before_retry(self, isolated_db, started):
        """After a failed run the scheduled check waits RETRY_AFTER_FAILURE
        (1 day) from the failed attempt; a failure never counts as a run."""
        assert abs_.RETRY_AFTER_FAILURE == datetime.timedelta(days=1)
        _enable(frequency="daily")
        attempt = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        _state(last_attempt_at=attempt.isoformat(), last_error=abs_._FAILED)
        day = datetime.timedelta(days=1)
        assert abs_.is_due(now=attempt + day - datetime.timedelta(seconds=1)) is False
        assert abs_.check_and_run(now=attempt + day - datetime.timedelta(seconds=1)) == "not_due"
        assert started == []
        assert abs_.check_and_run(now=attempt + day) == "started"   # never succeeded: due

    def test_failed_attempt_with_old_success(self, isolated_db, started):
        _enable(frequency="weekly")
        success = datetime.datetime(2026, 3, 1, tzinfo=UTC)
        attempt = success + datetime.timedelta(days=8)
        _state(last_success_at=success.isoformat(), last_attempt_at=attempt.isoformat(),
               last_error=abs_._FAILED)
        assert abs_.check_and_run(now=attempt + datetime.timedelta(hours=23)) == "not_due"
        assert abs_.check_and_run(now=attempt + datetime.timedelta(days=1)) == "started"

    def test_future_last_success_is_due(self, isolated_db, started, monkeypatch):
        """A last run dated in the future (the clock ran ahead, then was
        corrected) doesn't hold the next backup off until that date."""
        _enable(frequency="daily")
        now = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(abs_, "_now", lambda: now)
        _state(last_success_at=(now + datetime.timedelta(days=300)).isoformat())
        assert abs_.is_due(now=now) is True
        assert abs_.next_run_at() == now
        assert abs_.check_and_run(now=now) == "started"
        assert started == [False]

    def test_future_failed_attempt_does_not_block(self, isolated_db, started):
        _enable(frequency="daily")
        now = datetime.datetime(2026, 3, 1, tzinfo=UTC)
        _state(last_attempt_at=(now + datetime.timedelta(days=30)).isoformat(),
               last_error=abs_._FAILED)
        assert abs_.check_and_run(now=now) == "started"

    def test_no_error_recent_attempt_does_not_block(self, isolated_db, started):
        _enable(frequency="daily")
        now = datetime.datetime(2026, 3, 1, tzinfo=UTC)
        _state(last_attempt_at=now.isoformat(), last_error=None)
        assert abs_.check_and_run(now=now) == "started"

    def test_busy_while_any_job_runs(self, isolated_db, started):
        _enable()
        _put_job("translate_7")
        assert abs_.check_and_run() == "busy"
        assert started == []
        background_jobs.clear_job("translate_7")
        assert abs_.check_and_run() == "started"   # retried at the next check

    def test_busy_while_queued_job(self, isolated_db, started):
        _enable()
        _put_job("dub_3", status="queued")
        assert abs_.check_and_run() == "busy"
        assert started == []

    def test_busy_while_other_process_job_record_is_fresh(self, isolated_db, started):
        _enable()
        db.save_job_record("elsewhere_1", "running")
        assert abs_.check_and_run() == "busy"
        assert started == []

    def test_busy_during_maintenance(self, isolated_db, started):
        _enable()
        assert background_jobs.enter_maintenance()
        try:
            assert abs_.check_and_run() == "busy"
        finally:
            background_jobs.exit_maintenance()
        assert started == []

    def test_busy_during_exclusive_restore(self, isolated_db, started):
        _enable()
        assert background_jobs.acquire_exclusive("Library restore")
        try:
            assert abs_.check_and_run() == "busy"
        finally:
            background_jobs.release_exclusive()
        assert started == []

    def test_start_refused_is_busy(self, isolated_db, monkeypatch):
        _enable()
        monkeypatch.setattr(abs_, "_start", lambda include_media: False)
        assert abs_.check_and_run() == "busy"

    def test_never_raises(self, isolated_db, monkeypatch):
        def boom():
            raise RuntimeError("db gone")
        monkeypatch.setattr(abs_, "get_settings", boom)
        assert abs_.check_and_run() == "error"
        assert abs_._get_state()["last_error"] == abs_._CHECK_FAILED

    def test_real_scheduled_run_then_not_due(self, isolated_db):
        a = db.create_drama(title_en="A")
        _enable(frequency="daily")
        assert abs_.check_and_run() == "started"
        st = _wait_job()
        assert st["status"] == "done", st
        assert os.path.isfile(_default_path())
        info = abs_.snapshot_info()
        assert info["exists"] and info["kind"] == "db-only" and info["drama_count"] == 1
        assert abs_.check_and_run() == "not_due"
        ov = abs_.settings_overview()
        assert ov["last_run_at"] and ov["last_error"] is None
        assert abs_._parse(ov["next_run_at"]) == abs_._parse(ov["last_run_at"]) + \
            datetime.timedelta(days=1)
        assert db.get_drama(a) is not None


class TestPeriodicTick:
    def test_throttled(self, isolated_db, monkeypatch):
        calls = []
        monkeypatch.setattr(abs_, "check_and_run", lambda now=None: calls.append(1) or "x")
        monkeypatch.setattr(abs_, "_last_check", None)
        assert abs_.periodic_tick() is True
        assert abs_.periodic_tick() is False
        assert abs_.periodic_tick(interval=3600) is False
        assert calls == [1]
        # an hour later it checks again
        monkeypatch.setattr(abs_, "_last_check", time.monotonic() - 3601)
        assert abs_.periodic_tick() is True
        assert calls == [1, 1]
        assert abs_.CHECK_INTERVAL_SECONDS == 3600.0

    def test_zero_interval_always_checks(self, isolated_db, monkeypatch):
        calls = []
        monkeypatch.setattr(abs_, "check_and_run", lambda now=None: calls.append(1))
        monkeypatch.setattr(abs_, "_last_check", None)
        assert abs_.periodic_tick(interval=0) and abs_.periodic_tick(interval=0)
        assert len(calls) == 2


class TestApiBackgroundHooks:
    def test_startup_runs_tombstone_sweep_and_due_check(self, isolated_db, monkeypatch):
        from api import background
        from sources import chapter_check
        calls = []
        monkeypatch.setattr(background, "_started", None)
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda: None)
        monkeypatch.setattr(drama_service, "cleanup_stale_tombstones",
                            lambda *a, **k: calls.append("sweep") or 0)
        monkeypatch.setattr(abs_, "cleanup_stale_leftovers",
                            lambda *a, **k: calls.append("leftovers") or 0)
        monkeypatch.setattr(abs_, "periodic_tick", lambda *a, **k: calls.append("tick") or True)
        background.start_background_services()
        assert calls == ["sweep", "leftovers", "tick"]

    def test_startup_survives_failures(self, isolated_db, monkeypatch):
        from api import background
        from sources import chapter_check
        monkeypatch.setattr(background, "_started", None)
        monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda: None)

        def boom(*a, **k):
            raise RuntimeError("x")
        monkeypatch.setattr(drama_service, "cleanup_stale_tombstones", boom)
        monkeypatch.setattr(abs_, "cleanup_stale_leftovers", boom)
        monkeypatch.setattr(abs_, "periodic_tick", boom)
        state = background.start_background_services()
        assert state["chapter_scheduler"] is True

    def test_poller_ticks_and_survives_errors(self, isolated_db, monkeypatch):
        from api import background
        ticks = []
        done = threading.Event()

        def tick(*a, **k):
            ticks.append(1)
            if len(ticks) >= 3:
                done.set()
            raise RuntimeError("tick failed")
        monkeypatch.setattr(background_jobs, "recheck_gpu_queue", lambda: None)
        monkeypatch.setattr(abs_, "periodic_tick", tick)
        monkeypatch.setattr(background, "_gpu_poller", None)
        assert background.start_gpu_queue_poller(interval=0.01)
        try:
            assert done.wait(10), ticks
        finally:
            background.stop_gpu_queue_poller()


# --------------------------------------------------------------------------
# writing the snapshot
# --------------------------------------------------------------------------

class TestSnapshotWrite:
    def test_manifest_contents(self, isolated_db):
        a = db.create_drama(title_en="Alpha", media_type="audio")
        b = db.create_drama(title_zh="只有中文", media_type="comic")
        with contextlib.closing(_conn()) as c:
            for i in range(3):
                _insert(c, "lines", drama_id=a, idx=i, start=0, end=1, zh=f"z{i}", en="")
            c.commit()
        before = abs_._now().replace(microsecond=0)
        path = _snap()
        after = abs_._now()
        with zipfile.ZipFile(path) as zf:
            m = json.loads(zf.read("manifest.json"))
            db_size = zf.getinfo("library.db").file_size
        assert m["format"] == 1
        assert m["kind"] == "db-only"
        assert isinstance(m["app_version"], str) and m["app_version"]
        assert m["database_size"] == db_size
        created = datetime.datetime.fromisoformat(m["created_at"])
        assert created.tzinfo is not None and before <= created <= after
        assert m["dramas"] == [
            {"id": a, "title": "Alpha", "media_type": "audio", "line_count": 3},
            {"id": b, "title": "只有中文", "media_type": "comic", "line_count": 0}]

    def test_db_only_contents(self, isolated_db):
        a = db.create_drama(title_en="A")
        os.makedirs(_ddir(a), exist_ok=True)
        with open(os.path.join(_ddir(a), "audio.wav"), "wb") as fh:
            fh.write(b"RIFF")
        path = _snap()
        with zipfile.ZipFile(path) as zf:
            assert sorted(zf.namelist()) == ["library.db", "manifest.json"]
        assert abs_.snapshot_info()["kind"] == "db-only"

    def test_full_contents(self, isolated_db):
        a = db.create_drama(title_en="A")
        os.makedirs(os.path.join(_ddir(a), "clips"), exist_ok=True)
        with open(os.path.join(_ddir(a), "audio.wav"), "wb") as fh:
            fh.write(b"RIFF-audio")
        with open(os.path.join(_ddir(a), "clips", "c1.wav"), "wb") as fh:
            fh.write(b"clip")
        path = _snap(include_media=True)
        with zipfile.ZipFile(path) as zf:
            names = set(zf.namelist())
            assert {"library.db", "manifest.json", f"dramas/{a}/audio.wav",
                    f"dramas/{a}/clips/c1.wav"} <= names
            assert zf.read(f"dramas/{a}/audio.wav") == b"RIFF-audio"
            assert json.loads(zf.read("manifest.json"))["kind"] == "full"
        assert abs_.snapshot_info()["kind"] == "full"

    def test_snapshot_db_has_no_auth_sessions(self, isolated_db, tmp_path):
        from services import auth_service
        u = auth_service.add_user("kid@example.com")
        auth_service.create_session(u["id"], "pytest", "203.0.113.9")
        path = _snap()
        with zipfile.ZipFile(path) as zf:
            zf.extract("library.db", tmp_path)
        with contextlib.closing(sqlite3.connect(tmp_path / "library.db")) as c:
            assert c.execute("SELECT COUNT(*) FROM auth_sessions").fetchone()[0] == 0

    def test_snapshot_db_has_no_device_tokens(self, isolated_db, tmp_path):
        import time
        import device_tokens
        from services import auth_service
        u = auth_service.add_user("kid@example.com")
        assert device_tokens.insert_under_cap(u["id"], "laptop", "a" * 64, time.time(),
                                              time.time() + 3600, 10)
        path = _snap()
        with zipfile.ZipFile(path) as zf:
            zf.extract("library.db", tmp_path)
        with contextlib.closing(sqlite3.connect(tmp_path / "library.db")) as c:
            assert c.execute("SELECT COUNT(*) FROM extension_device_tokens").fetchone()[0] == 0
        assert len(device_tokens.list_for_user(u["id"])) == 1

    def test_snapshot_excluded_from_full_backup_and_next_snapshot_no_env(self, isolated_db,
                                                                          tmp_path):
        a = db.create_drama(title_en="A")
        os.makedirs(_ddir(a), exist_ok=True)
        with open(os.path.join(db.LIBRARY_DIR, ".env"), "w") as fh:
            fh.write("ANTHROPIC_API_KEY=sk-ant-secret\n")
        with open(os.path.join(_ddir(a), ".env"), "w") as fh:
            fh.write("X=1\n")
        with open(os.path.join(_ddir(a), "a.wav"), "wb") as fh:
            fh.write(b"x")
        first = _snap(include_media=True)
        assert os.path.isfile(first)
        # a manual full backup never contains the snapshot
        dest = tmp_path / "manual.zip"
        las.write_backup_zip(str(dest), include_media=True)
        with zipfile.ZipFile(dest) as zf:
            names = zf.namelist()
        assert f"dramas/{a}/a.wav" in names
        assert not [n for n in names if n.startswith("backups")]
        assert not [n for n in names if n.endswith(".env")]
        # nor does the next snapshot contain the previous one
        second = _snap(include_media=True)
        with zipfile.ZipFile(second) as zf:
            names = zf.namelist()
        assert f"dramas/{a}/a.wav" in names
        assert not [n for n in names if n.startswith("backups") or "baihe_snapshot" in n]
        assert not [n for n in names if n.endswith(".env")]

    def test_custom_folder_inside_backups_also_excluded(self, isolated_db, tmp_path):
        folder = os.path.join(db.LIBRARY_DIR, "backups", "mine")
        os.makedirs(folder)
        abs_.set_settings(folder=folder)
        _snap(include_media=True)
        assert _newest(folder) is not None
        second = _snap(include_media=True)
        with zipfile.ZipFile(second) as zf:
            assert not [n for n in zf.namelist() if n.startswith("backups")]

    def test_second_run_adds_a_copy_and_keeps_the_first(self, isolated_db):
        a = db.create_drama(title_en="A")
        first = _snap()
        first_bytes = _read(first)
        first_state = abs_._get_state()
        time.sleep(1.05)   # created_at has one-second resolution
        c = db.create_drama(title_en="C")
        path = _snap()
        assert sorted(os.listdir(_default_dir())) == _copy_names()
        assert _copy_names() == [os.path.basename(first), os.path.basename(path)]
        assert _read(first) == first_bytes
        dramas = abs_.list_snapshot_dramas()["dramas"]
        assert [d["id"] for d in dramas] == [a, c]
        older = abs_.list_snapshot_dramas(os.path.basename(first))
        assert [d["id"] for d in older["dramas"]] == [a]
        state = abs_._get_state()
        assert state["last_error"] is None
        assert state["last_success_at"] > first_state["last_success_at"]
        assert path == _default_path()

    def test_two_runs_in_the_same_second_get_two_names(self, isolated_db, monkeypatch):
        fixed = datetime.datetime(2026, 3, 2, 8, 0, 0, tzinfo=UTC)
        monkeypatch.setattr(abs_, "_now", lambda: fixed)
        _snap()
        _snap()
        assert _copy_names() == ["baihe_snapshot-20260302-080000.zip",
                                 "baihe_snapshot-20260302-080001.zip"]

    def test_back_up_now_via_job(self, isolated_db):
        db.create_drama(title_en="A")
        assert abs_.start_now() == {"job_id": abs_.JOB_ID}
        st = _wait_job()
        assert st["status"] == "done", st
        assert st["result"]["kind"] == "db-only" and st["result"]["size"] > 0
        assert abs_.snapshot_info()["exists"]

    def test_back_up_now_adds_a_copy_replace_ignored(self, isolated_db):
        db.create_drama(title_en="A")
        first = _snap()
        before = _read(first)
        with pytest.raises(InvalidInputError):
            abs_.start_now(replace="yes")
        assert background_jobs.get_status(abs_.JOB_ID) is None
        time.sleep(1.05)
        assert abs_.start_now() == {"job_id": abs_.JOB_ID}   # no replace needed
        assert _wait_job()["status"] == "done"
        assert len(_copy_names()) == 2 and _read(first) == before
        background_jobs.clear_job(abs_.JOB_ID)
        abs_.start_now(replace=True, include_media=True)     # still accepted
        st = _wait_job()
        assert st["status"] == "done", st
        assert abs_.snapshot_info()["kind"] == "full"
        # a third run on one day replaces that day's second copy; the first
        # stays as the week's first copy
        assert len(_copy_names()) == 2 and _read(first) == before

    def test_back_up_now_include_media_defaults_to_setting(self, isolated_db):
        abs_.set_settings(include_media=True)
        abs_.start_now()
        assert _wait_job()["status"] == "done"
        assert abs_.snapshot_info()["kind"] == "full"

    def test_back_up_now_refused_when_busy(self, isolated_db):
        with pytest.raises(InvalidInputError):
            abs_.start_now(include_media="no")
        assert background_jobs.enter_maintenance()
        try:
            with pytest.raises(ConflictError):
                abs_.start_now()
        finally:
            background_jobs.exit_maintenance()
        assert background_jobs.acquire_exclusive("Library restore")
        try:
            with pytest.raises(ConflictError):
                abs_.start_now()
        finally:
            background_jobs.release_exclusive()
        _put_job(abs_.JOB_ID)
        with pytest.raises(ConflictError):
            abs_.start_now()
        assert _default_path() is None


class TestBackupFailureKeepsOldSnapshot:
    def _old(self):
        db.create_drama(title_en="A")
        _snap()
        return _read(_default_path()), abs_._get_state()

    def _assert_kept(self, old_bytes, old_state):
        assert _read(_default_path()) == old_bytes
        # no partial left behind, no new copy
        assert os.listdir(_default_dir()) == _copy_names() and len(_copy_names()) == 1
        state = abs_._get_state()
        assert state["last_error"] == abs_._FAILED
        assert state["last_success_at"] == old_state["last_success_at"]
        assert state["last_attempt_at"]
        assert abs_.settings_overview()["last_error"] == abs_._FAILED

    def test_writer_raises_after_partial_write(self, isolated_db, monkeypatch):
        old, st = self._old()

        def broken(dest, include_media=True, manifest=None):
            with open(dest, "wb") as fh:
                fh.write(b"PK\x03\x04half a zip")
            raise OSError(f"disk full at {db.LIBRARY_DIR}")
        monkeypatch.setattr(las, "write_backup_zip", broken)
        with pytest.raises(RuntimeError) as e:
            abs_._backup_job(abs_.JOB_ID, False)
        assert db.LIBRARY_DIR not in str(e.value)
        self._assert_kept(old, st)

    def test_verify_raises(self, isolated_db, monkeypatch):
        old, st = self._old()
        db.create_drama(title_en="New since")

        def bad(path):
            raise InvalidInputError("nope")
        monkeypatch.setattr(abs_, "_verify_snapshot", bad)
        with pytest.raises(RuntimeError):
            abs_._backup_job(abs_.JOB_ID, False)
        self._assert_kept(old, st)

    def test_real_verification_rejects_bad_manifest(self, isolated_db, monkeypatch):
        old, st = self._old()
        monkeypatch.setattr(abs_, "_manifest_bytes", lambda snap, media: b"{not json")
        with pytest.raises(RuntimeError):
            abs_._backup_job(abs_.JOB_ID, False)
        self._assert_kept(old, st)

    def test_real_verification_rejects_manifest_db_mismatch(self, isolated_db, monkeypatch):
        old, st = self._old()
        real = abs_._manifest_bytes

        def lying(snap, media):
            m = json.loads(real(snap, media))
            m["dramas"] = []
            return json.dumps(m).encode()
        monkeypatch.setattr(abs_, "_manifest_bytes", lying)
        with pytest.raises(RuntimeError):
            abs_._backup_job(abs_.JOB_ID, False)
        self._assert_kept(old, st)

    def test_failure_via_job_thread(self, isolated_db, monkeypatch):
        old, st = self._old()
        monkeypatch.setattr(las, "write_backup_zip",
                            lambda *a, **k: (_ for _ in ()).throw(OSError(db.LIBRARY_DIR)))
        abs_.start_now(replace=True)
        job = _wait_job()
        assert job["status"] == "error"
        assert db.LIBRARY_DIR not in (job["error"] or "")
        self._assert_kept(old, st)

    def test_first_backup_failure_leaves_nothing(self, isolated_db, monkeypatch):
        def broken(dest, include_media=True, manifest=None):
            with open(dest, "wb") as fh:
                fh.write(b"junk")
            raise OSError("x")
        monkeypatch.setattr(las, "write_backup_zip", broken)
        with pytest.raises(RuntimeError):
            abs_._backup_job(abs_.JOB_ID, False)
        assert os.listdir(_default_dir()) == []
        assert abs_.snapshot_info() == {"exists": False, "copies": []}

    def test_success_after_failure_clears_error(self, isolated_db, monkeypatch):
        old, _ = self._old()
        with monkeypatch.context() as m:
            m.setattr(abs_, "_verify_snapshot", lambda p: (_ for _ in ()).throw(OSError()))
            with pytest.raises(RuntimeError):
                abs_._backup_job(abs_.JOB_ID, False)
        _snap()
        assert abs_._get_state()["last_error"] is None


# --------------------------------------------------------------------------
# snapshot info / list / delete
# --------------------------------------------------------------------------

class TestSnapshotInfoAndDelete:
    def test_no_snapshot(self, isolated_db):
        assert abs_.snapshot_info() == {"exists": False, "copies": []}
        with pytest.raises(NotFoundError):
            abs_.list_snapshot_dramas()
        with pytest.raises(NotFoundError):
            abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True)
        with pytest.raises(NotFoundError):
            _restore(1)

    def test_info_no_path(self, isolated_db):
        db.create_drama(title_en="A")
        _snap()
        info = abs_.snapshot_info()
        assert info["exists"] and info["readable"] and info["drama_count"] == 1
        assert info["size"] == os.path.getsize(_default_path())
        assert db.LIBRARY_DIR not in json.dumps(info)
        [copy] = info["copies"]
        assert copy == {"name": os.path.basename(_default_path()),
                        "created_at": info["created_at"], "size": info["size"],
                        "readable": True, "kind": "db-only", "drama_count": 1,
                        "kept_as": "daily", "managed": True, "sequence": 1}
        assert info["default_copy"] == copy["name"] and info["choose_copy"] is False
        assert db.LIBRARY_DIR not in json.dumps(abs_.settings_overview())
        assert abs_.settings_overview()["copies"] == info["copies"]

    def test_unreadable_snapshot(self, isolated_db):
        os.makedirs(_default_dir())
        name = "baihe_snapshot-20260301-120000.zip"
        with open(os.path.join(_default_dir(), name), "wb") as fh:
            fh.write(b"not a zip")
        info = abs_.snapshot_info()
        assert info == {"exists": True, "readable": False, "copies": [
            {"name": name, "created_at": "2026-03-01T12:00:00+00:00", "size": 9,
             "readable": False, "kind": None, "drama_count": None, "kept_as": None,
             "managed": False, "sequence": None}]}
        with pytest.raises(InvalidInputError):
            abs_.list_snapshot_dramas()
        with pytest.raises(InvalidInputError):
            abs_.list_snapshot_dramas(name)

    def test_symlink_snapshot_ignored(self, isolated_db, tmp_path):
        db.create_drama(title_en="A")
        real = _snap()
        target = tmp_path / "elsewhere.zip"
        os.replace(real, target)
        os.symlink(target, real)
        assert abs_.snapshot_info() == {"exists": False, "copies": []}
        with pytest.raises(NotFoundError):
            _restore(1)
        with pytest.raises(NotFoundError):
            abs_.restore_drama(1, confirm=True, confirm_text="RESTORE",
                               snapshot=os.path.basename(real))
        with pytest.raises(NotFoundError):
            abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True)
        with pytest.raises(NotFoundError):
            abs_.delete_snapshot(confirm=True, confirm_text="DELETE",
                                 snapshot=os.path.basename(real))
        assert target.exists() and os.path.islink(real)

    def test_list_exists_now(self, isolated_db):
        a = db.create_drama(title_en="A")
        b = db.create_drama(title_en="B")
        _snap()
        _delete_drama(a)
        out = abs_.list_snapshot_dramas()
        assert out["kind"] == "db-only"
        assert {d["id"]: d["exists_now"] for d in out["dramas"]} == {a: False, b: True}

    @pytest.mark.parametrize("confirm,text", [(False, "DELETE"), (True, "delete"), (True, ""),
                                              ("true", "DELETE"), (True, "RESTORE")])
    def test_delete_needs_typed_word(self, isolated_db, confirm, text):
        db.create_drama(title_en="A")
        _snap()
        with pytest.raises(InvalidInputError):
            abs_.delete_snapshot(confirm=confirm, confirm_text=text, all_copies=True)
        assert os.path.isfile(_default_path())

    def test_delete(self, isolated_db):
        db.create_drama(title_en="A")
        _snap()
        _snap()
        assert abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True) == \
            {"deleted": True, "count": 2, "kept_unmanaged": 0}
        assert _default_path() is None
        assert abs_.snapshot_info() == {"exists": False, "copies": []}

    def test_delete_one_named_copy(self, isolated_db):
        db.create_drama(title_en="A")
        older = _snap()
        newer = _snap()
        other = os.path.join(_default_dir(), "notes.zip")
        with open(other, "wb") as fh:
            fh.write(b"mine")
        assert abs_.delete_snapshot(confirm=True, confirm_text="DELETE",
                                    snapshot=os.path.basename(older)) == \
            {"deleted": True, "count": 1, "kept_unmanaged": 0}
        assert not os.path.exists(older) and os.path.isfile(newer)
        with pytest.raises(NotFoundError):     # already gone
            abs_.delete_snapshot(confirm=True, confirm_text="DELETE",
                                 snapshot=os.path.basename(older))
        # delete-all touches only copies
        abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True)
        assert os.listdir(_default_dir()) == ["notes.zip"]

    def test_delete_refused_while_backup_runs(self, isolated_db):
        db.create_drama(title_en="A")
        _snap()
        _put_job(abs_.JOB_ID)
        with pytest.raises(ConflictError):
            abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True)
        assert os.path.isfile(_default_path())

    @pytest.mark.parametrize("kw", [
        {}, {"all_copies": False}, {"all_copies": "true"}, {"all_copies": 1},
        {"all_copies": True, "snapshot": "NAME"}])
    def test_delete_all_needs_an_explicit_all(self, isolated_db, kw):
        """A delete that names no copy never means "every copy"."""
        db.create_drama(title_en="A")
        path = _snap()
        kw = {k: (os.path.basename(path) if v == "NAME" else v) for k, v in kw.items()}
        with pytest.raises(InvalidInputError):
            abs_.delete_snapshot(confirm=True, confirm_text="DELETE", **kw)
        assert os.path.isfile(path)


# --------------------------------------------------------------------------
# rotation: 2 daily + 2 weekly copies
# --------------------------------------------------------------------------

def _at(day, hour=3, month=3):
    return datetime.datetime(2026, month, day, hour, 0, 0, tzinfo=UTC)


def _run_at(monkeypatch, when):
    """One real backup run with _now faked to `when`."""
    monkeypatch.setattr(abs_, "_now", lambda: when)
    return _snap()


def _name(when):
    return f"baihe_snapshot-{when:%Y%m%d-%H%M%S}.zip"


def _untag(path, when):
    """Rewrites the copy as an app from before copies carried a library id
    wrote it: no library_id or sequence, created_at `when`."""
    with zipfile.ZipFile(path) as zf:
        members = {i.filename: zf.read(i) for i in zf.infolist()}
    manifest = json.loads(members["manifest.json"])
    manifest.pop("library_id")
    manifest.pop("sequence")
    manifest["created_at"] = abs_._iso(when)
    members["manifest.json"] = json.dumps(manifest).encode()
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)


def _make_legacy(when):
    """A pre-rotation baihe_snapshot.zip (a real snapshot, as the app wrote
    it before copies carried a library id) dated `when`."""
    path = _snap()
    legacy = os.path.join(os.path.dirname(path), abs_.LEGACY_SNAPSHOT_NAME)
    os.replace(path, legacy)
    _untag(legacy, when)
    ts = when.timestamp()
    os.utime(legacy, (ts, ts))
    return legacy


class TestRotation:
    def test_ten_runs_across_three_weeks_keep_two_daily_and_two_weekly(
            self, isolated_db, monkeypatch):
        """2026-03-02 is a Monday. Runs every other day: ISO week 10 gets
        2, 4, 6, 8; week 11 gets 10, 12, 14; week 12 gets 16, 18, 20."""
        db.create_drama(title_en="A")
        days = [2, 4, 6, 8, 10, 12, 14, 16, 18, 20]
        for day in days:
            _run_at(monkeypatch, _at(day))
            assert len(os.listdir(_default_dir())) <= 4
        # daily: the new copy (20) and the newest copy of the latest earlier
        # day that has one (18: one run a day, so the same as the 2 newest
        # copies; same-day runs are covered below); weekly: the first copy of each of the
        # 2 most recent older weeks -- week 12's is 16, week 11's is 10 (12
        # and 14 went when they fell out of the daily slots); week 10's
        # first copy (2) is the third week back, so it is gone.
        assert sorted(os.listdir(_default_dir())) == [
            _name(_at(10)), _name(_at(16)), _name(_at(18)), _name(_at(20))]
        info = abs_.snapshot_info()
        assert [(c["name"], c["kept_as"]) for c in info["copies"]] == [
            (_name(_at(20)), "daily"), (_name(_at(18)), "daily"),
            (_name(_at(16)), "weekly"), (_name(_at(10)), "weekly")]
        assert info["created_at"] == abs_._iso(_at(20))
        assert all(c["readable"] and c["kind"] == "db-only" for c in info["copies"])

    def test_retention_rule_is_the_same_all_at_once(self, isolated_db, monkeypatch):
        """Pruning once over many copies gives the same answer as pruning
        after every run (the rule only looks at the copies' dates)."""
        db.create_drama(title_en="A")
        monkeypatch.setattr(abs_, "_prune", lambda folder, new_name: 0)
        for day in (2, 4, 6, 8, 10, 12, 14, 16, 18, 20):
            _run_at(monkeypatch, _at(day))
        assert len(os.listdir(_default_dir())) == 10
        monkeypatch.undo()
        with abs_._snapshot_lock:
            assert abs_._prune(_default_dir(), _name(_at(20))) == 6
        assert sorted(os.listdir(_default_dir())) == [
            _name(_at(10)), _name(_at(16)), _name(_at(18)), _name(_at(20))]

    def test_new_copy_is_never_pruned_even_with_a_clock_set_back(self, isolated_db,
                                                                 monkeypatch):
        db.create_drama(title_en="A")
        for day in (10, 11, 12):
            _run_at(monkeypatch, _at(day))
        new = _run_at(monkeypatch, _at(1))      # clock set back 11 days
        assert os.path.isfile(new)
        # the copies dated after the new one take no slot and are never deleted
        assert sorted(os.listdir(_default_dir())) == [
            _name(_at(1)), _name(_at(10)), _name(_at(11)), _name(_at(12))]

    def test_extra_runs_on_one_day_never_push_out_yesterdays_copy(self, isolated_db,
                                                                 monkeypatch):
        """2026-03-02 is a Monday (ISO week 10). Runs on the 2nd and 3rd,
        then three on the 4th (a manual db-only run among them)."""
        db.create_drama(title_en="A")
        _run_at(monkeypatch, _at(2))
        full = _run_at(monkeypatch, _at(3))
        for hour in (5, 9, 13):
            _run_at(monkeypatch, _at(4, hour=hour))
            # yesterday's copy keeps its daily slot through every extra run
            assert os.path.isfile(full)
        # daily: the new copy and the 3rd's; the 2nd's is kept only as week
        # 10's first copy; the 4th's earlier copies were replaced.
        assert sorted(os.listdir(_default_dir())) == [
            _name(_at(2)), _name(_at(3)), _name(_at(4, hour=13))]
        assert [(c["name"], c["kept_as"]) for c in abs_.snapshot_info()["copies"]] == [
            (_name(_at(4, hour=13)), "daily"), (_name(_at(3)), "daily"),
            (_name(_at(2)), "weekly")]

    def test_one_daily_slot_per_day_and_the_weekly_rule(self, isolated_db, monkeypatch):
        """Two runs a day: the 9th (Monday, week 11) and 10th."""
        db.create_drama(title_en="A")
        for day in (2, 3, 9, 10):
            for hour in (3, 15):
                _run_at(monkeypatch, _at(day, hour=hour))
        # daily: the 10th's newest, the 9th's newest; weekly: the first copy
        # of week 11 (9th 03:00) and of week 10 (2nd 03:00).
        assert [(c["name"], c["kept_as"]) for c in abs_.snapshot_info()["copies"]] == [
            (_name(_at(10, hour=15)), "daily"), (_name(_at(9, hour=15)), "daily"),
            (_name(_at(9, hour=3)), "weekly"), (_name(_at(2, hour=3)), "weekly")]
        assert len(os.listdir(_default_dir())) == 4

    def test_future_dated_legacy_file_takes_no_slot(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        legacy = _make_legacy(datetime.datetime(2099, 1, 1, tzinfo=UTC))
        for day in (2, 3, 4, 5):
            _run_at(monkeypatch, _at(day))
        # daily: the 5th and yesterday (the 4th); weekly: week 10's first (the
        # 2nd); the 3rd is rotated out; the 2099 file is left alone
        assert os.path.isfile(legacy)
        assert sorted(os.listdir(_default_dir())) == sorted([
            abs_.LEGACY_SNAPSHOT_NAME, _name(_at(2)), _name(_at(4)), _name(_at(5))])
        kept = {c["name"]: c["kept_as"] for c in abs_.snapshot_info()["copies"]}
        assert kept == {abs_.LEGACY_SNAPSHOT_NAME: None, _name(_at(5)): "daily",
                        _name(_at(4)): "daily", _name(_at(2)): "weekly"}

    def test_future_dated_copy_is_never_silently_picked(self, isolated_db, monkeypatch):
        """A copy written while the clock ran far ahead: the sequence says
        the later runs are newer, but the dates disagree by far more than a
        clock slip, so no copy is picked without the owner choosing; the
        rotation never deletes the future-dated one."""
        db.create_drama(title_en="A")
        future = datetime.datetime(2099, 1, 1, tzinfo=UTC)
        _run_at(monkeypatch, future)
        for day in (2, 3, 4, 5, 12, 19):
            _run_at(monkeypatch, _at(day))
        assert os.path.isfile(os.path.join(_default_dir(), _name(future)))
        info = abs_.snapshot_info()
        assert info["copies"][0]["name"] == _name(future)     # listed newest first
        assert info["choose_copy"] is True and info["default_copy"] is None
        assert "created_at" not in info
        with pytest.raises(ConflictError) as e:
            abs_.list_snapshot_dramas()
        assert e.value.details["reason"] == "choose_copy"
        assert _name(_at(19)) in [c["name"] for c in e.value.details["candidates"]]
        assert abs_.list_snapshot_dramas(_name(_at(19)))["name"] == _name(_at(19))

    def test_future_dated_copy_used_when_nothing_else_reads(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        future = datetime.datetime(2099, 1, 1, tzinfo=UTC)
        _run_at(monkeypatch, future)
        monkeypatch.setattr(abs_, "_now", lambda: _at(2))
        assert abs_.snapshot_info()["created_at"] == abs_._iso(future)
        assert abs_.list_snapshot_dramas()["name"] == _name(future)

    def test_copy_that_cannot_be_opened_now_is_never_deleted(self, isolated_db,
                                                              monkeypatch):
        """A PermissionError (locked by another program, say) is not damage:
        the copy takes no slot and is not deleted, even when the rotation
        would have dropped it."""
        db.create_drama(title_en="A")
        for day in (2, 9, 16, 17):
            _run_at(monkeypatch, _at(day))
        oldest = os.path.join(_default_dir(), _name(_at(2)))
        data = _read(oldest)
        real_zipfile = abs_.zipfile.ZipFile

        def locked(file, *args, **kw):
            if isinstance(file, str) and os.path.abspath(file) == oldest:
                raise PermissionError(13, "in use")
            return real_zipfile(file, *args, **kw)
        monkeypatch.setattr(abs_.zipfile, "ZipFile", locked)
        assert abs_._read_copy(oldest) == (abs_._UNREADABLE, None)
        # on the 23rd (week 13) week 10's copy is the third week back
        _run_at(monkeypatch, _at(23))
        assert _read(oldest) == data
        info = {c["name"]: (c["readable"], c["kept_as"]) for c in abs_.snapshot_info()["copies"]}
        assert info == {_name(_at(23)): (True, "daily"), _name(_at(17)): (True, "daily"),
                        _name(_at(16)): (True, "weekly"), _name(_at(9)): (True, "weekly"),
                        _name(_at(2)): (False, None)}
        # once it can be read again, the rotation drops it as usual
        monkeypatch.setattr(abs_.zipfile, "ZipFile", real_zipfile)
        _run_at(monkeypatch, _at(24))
        assert not os.path.exists(oldest)
        assert sorted(os.listdir(_default_dir())) == [
            _name(_at(9)), _name(_at(16)), _name(_at(23)), _name(_at(24))]

    def test_unreadable_copy_gets_no_slot(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        for day in (2, 9, 16, 17):
            _run_at(monkeypatch, _at(day))
        week11 = os.path.join(_default_dir(), _name(_at(9)))
        real_zipfile = abs_.zipfile.ZipFile

        def locked(file, *args, **kw):
            if isinstance(file, str) and os.path.abspath(file) == week11:
                raise PermissionError(13, "in use")
            return real_zipfile(file, *args, **kw)
        monkeypatch.setattr(abs_.zipfile, "ZipFile", locked)
        _run_at(monkeypatch, _at(23))
        # week 11's copy is skipped, so week 10's first copy takes the second
        # weekly slot; the skipped copy itself is kept
        assert sorted(os.listdir(_default_dir())) == [
            _name(_at(2)), _name(_at(9)), _name(_at(16)), _name(_at(17)), _name(_at(23))]

    def test_read_copy_tells_damage_from_an_os_error(self, isolated_db, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        assert abs_._read_copy(str(bad)) == (abs_._DAMAGED, None)
        no_manifest = tmp_path / "nomanifest.zip"
        with zipfile.ZipFile(no_manifest, "w") as zf:
            zf.writestr("library.db", b"x")
        assert abs_._read_copy(str(no_manifest)) == (abs_._DAMAGED, None)
        assert abs_._read_copy(str(tmp_path / "missing.zip")) == (abs_._UNREADABLE, None)

    def test_new_copy_is_on_disk_before_old_ones_are_pruned(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        _run_at(monkeypatch, _at(2))
        events = []
        real_file, real_dir, real_prune = abs_._fsync_file, abs_._fsync_dir, abs_._prune
        monkeypatch.setattr(abs_, "_fsync_file", lambda p: (
            events.append(("file", os.path.basename(p))), real_file(p)))
        monkeypatch.setattr(abs_, "_fsync_dir", lambda d: (
            events.append(("dir", d)), real_dir(d)))
        monkeypatch.setattr(abs_, "_prune", lambda folder, name: (
            events.append(("prune", name)), real_prune(folder, name))[1])
        _run_at(monkeypatch, _at(3))
        assert [e[0] for e in events] == ["file", "dir", "prune"]
        assert events[0][1].startswith(".baihe_snapshot.partial-")
        assert events[1][1] == _default_dir() and events[2][1] == _name(_at(3))

    def test_failed_directory_flush_skips_the_rotation_only(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        for day in (2, 3):
            _run_at(monkeypatch, _at(day))

        def fail(folder):
            raise OSError("flush failed")
        monkeypatch.setattr(abs_, "_fsync_dir", fail)
        _run_at(monkeypatch, _at(4, hour=5))
        _run_at(monkeypatch, _at(4, hour=9))        # would rotate the 5:00 copy out
        assert len(_copy_names()) == 4
        assert abs_._get_state()["last_error"] is None

    @pytest.mark.parametrize("breakage", ["writer", "verify", "manifest", "job_thread"])
    def test_a_failed_run_never_deletes_anything(self, isolated_db, monkeypatch, breakage):
        db.create_drama(title_en="A")
        for day in (2, 9, 16, 17):
            _run_at(monkeypatch, _at(day))
        before = {n: _read(os.path.join(_default_dir(), n)) for n in _copy_names()}
        assert len(before) == 4     # a successful run on the 18th would delete one
        monkeypatch.setattr(abs_, "_now", lambda: _at(18))
        if breakage == "writer":
            monkeypatch.setattr(las, "write_backup_zip",
                                lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        elif breakage == "verify":
            monkeypatch.setattr(abs_, "_verify_snapshot",
                                lambda p: (_ for _ in ()).throw(InvalidInputError("bad")))
        elif breakage == "manifest":
            monkeypatch.setattr(abs_, "_manifest_bytes", lambda snap, media: b"{not json")
        else:
            monkeypatch.setattr(las, "write_backup_zip",
                                lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
        if breakage == "job_thread":
            abs_.start_now()
            assert _wait_job()["status"] == "error"
        else:
            with pytest.raises(RuntimeError):
                abs_._backup_job(abs_.JOB_ID, False)
        assert sorted(os.listdir(_default_dir())) == sorted(before)
        assert {n: _read(os.path.join(_default_dir(), n)) for n in before} == before
        assert abs_._get_state()["last_error"] == abs_._FAILED

    def test_failed_prune_keeps_the_new_copy_and_reports_success(self, isolated_db,
                                                                 monkeypatch):
        db.create_drama(title_en="A")
        _run_at(monkeypatch, _at(2))
        monkeypatch.setattr(abs_, "_prune", lambda folder, new_name: 1 / 0)
        path = _run_at(monkeypatch, _at(3))
        assert os.path.isfile(path) and len(_copy_names()) == 2
        assert abs_._get_state()["last_error"] is None

    def test_legacy_snapshot_is_a_copy_and_ages_out(self, isolated_db, monkeypatch):
        a = db.create_drama(title_en="A")
        # 2026-03-01 is a Sunday: ISO week 9.
        legacy = _make_legacy(_at(1))
        info = abs_.snapshot_info()
        assert info["exists"] and info["readable"]
        assert [c["name"] for c in info["copies"]] == [abs_.LEGACY_SNAPSHOT_NAME]
        assert info["copies"][0]["managed"] is True
        # no copy numbered by this library yet: the owner names one
        assert info["choose_copy"] is True
        with pytest.raises(ConflictError):
            abs_.list_snapshot_dramas()
        assert [d["id"] for d in abs_.list_snapshot_dramas(
            abs_.LEGACY_SNAPSHOT_NAME)["dramas"]] == [a]
        # upgrading loses nothing: the first rotating run keeps it
        _run_at(monkeypatch, _at(2))
        _run_at(monkeypatch, _at(3))
        assert os.path.isfile(legacy)                   # weekly slot for week 9
        _run_at(monkeypatch, _at(9))
        _run_at(monkeypatch, _at(10))
        assert os.path.isfile(legacy)                   # weeks 10 and 9 still in reach
        assert sorted(os.listdir(_default_dir())) == sorted(
            [abs_.LEGACY_SNAPSHOT_NAME, _name(_at(2)), _name(_at(9)), _name(_at(10))])
        _run_at(monkeypatch, _at(16))
        assert not os.path.exists(legacy)               # now the third week back
        assert sorted(os.listdir(_default_dir())) == [
            _name(_at(2)), _name(_at(9)), _name(_at(10)), _name(_at(16))]

    def test_restore_from_a_copy_older_than_the_repeat_guard_resets_the_old_silence_default(
            self, isolated_db):
        import sqlite3
        a = db.create_drama(title_en="Old default")
        path = _snap()
        with zipfile.ZipFile(path) as zf:
            members = {n: zf.read(n) for n in zf.namelist()}
        inner = os.path.join(os.path.dirname(path), "_edit.db")
        with open(inner, "wb") as fh:
            fh.write(members["library.db"])
        conn = sqlite3.connect(inner)
        conn.execute("ALTER TABLE dramas DROP COLUMN whisper_repeat_guard")
        conn.execute("UPDATE dramas SET hallucination_silence_sec = 2.0")
        conn.commit()
        conn.close()
        with open(inner, "rb") as fh:
            members["library.db"] = fh.read()
        os.remove(inner)
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in members.items():
                zf.writestr(name, data)
        _delete_drama(a)
        res = abs_.restore_drama(a, confirm=True, confirm_text="RESTORE",
                                 snapshot=os.path.basename(path))
        assert db.get_drama(res["drama_id"])["hallucination_silence_sec"] == 0

    def test_restore_from_the_legacy_snapshot_by_name(self, isolated_db, monkeypatch):
        a = db.create_drama(title_en="Legacy drama")
        _make_legacy(_at(1))
        db.update_drama(a, title_en="Renamed")
        _run_at(monkeypatch, _at(2))
        _delete_drama(a)
        res = abs_.restore_drama(a, confirm=True, confirm_text="RESTORE",
                                 snapshot=abs_.LEGACY_SNAPSHOT_NAME)
        assert db.get_drama(res["drama_id"])["title_en"] == "Legacy drama"

    def test_unreadable_copies_skipped_and_only_old_ones_deleted(self, isolated_db,
                                                                monkeypatch, tmp_path):
        db.create_drama(title_en="A")
        _run_at(monkeypatch, _at(2))
        _run_at(monkeypatch, _at(3))
        folder = _default_dir()

        def put(name, data=b"not a zip"):
            with open(os.path.join(folder, name), "wb") as fh:
                fh.write(data)
            return os.path.join(folder, name)
        newer_bad = put(_name(_at(3, hour=12)))     # newer than the oldest kept copy
        older_bad = put("baihe_snapshot-20260101-000000.zip")
        other = put("other.zip", _read(_newest()))  # a valid snapshot under another name
        partial = put(".baihe_snapshot.partial-abcd.zip")
        os.makedirs(os.path.join(folder, "baihe_snapshot-20250101-000000.zip"))
        outside = tmp_path / "outside.zip"
        outside.write_bytes(_read(_newest()))
        link = os.path.join(folder, "baihe_snapshot-20240101-000000.zip")
        os.symlink(outside, link)

        _run_at(monkeypatch, _at(4))
        # readable copies 4, 3 (daily) and 2 (week 10's first) are kept; a
        # damaged file proves no owner, so the rotation never deletes it,
        # however old, and it takes no slot. Nothing else is touched.
        for p in (newer_bad, older_bad, other, partial, link):
            assert os.path.lexists(p), p
        assert os.path.isdir(os.path.join(folder, "baihe_snapshot-20250101-000000.zip"))
        assert os.path.islink(link) and outside.read_bytes()
        info = abs_.snapshot_info()
        assert [c["name"] for c in info["copies"]] == [
            _name(_at(4)), _name(_at(3, hour=12)), _name(_at(3)), _name(_at(2)),
            "baihe_snapshot-20260101-000000.zip"]
        assert [c["readable"] for c in info["copies"]] == [True, False, True, True, False]
        assert [c["managed"] for c in info["copies"]] == [True, False, True, True, False]
        assert info["created_at"] == abs_._iso(_at(4))
        # a damaged file dated before the default doesn't matter; one dated
        # after it may have been the newest copy, so the owner chooses
        assert abs_.list_snapshot_dramas()["name"] == _name(_at(4))
        put(_name(_at(5)))
        with pytest.raises(ConflictError) as e:
            abs_.list_snapshot_dramas()
        assert e.value.details["reason"] == "choose_copy"
        assert _name(_at(5)) not in [c["name"] for c in e.value.details["candidates"]]
        assert abs_.snapshot_info()["choose_copy"] is True
        # delete-all removes this library's copies only
        assert abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True) == \
            {"deleted": True, "count": 3, "kept_unmanaged": 3}
        assert os.path.lexists(newer_bad) and os.path.lexists(older_bad)
        # asked to include the unmanaged ones, the damaged copies go too,
        # and still nothing else
        assert abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True,
                                    include_unmanaged=True)["count"] == 3
        assert sorted(os.listdir(folder)) == sorted([
            ".baihe_snapshot.partial-abcd.zip", "baihe_snapshot-20240101-000000.zip",
            "baihe_snapshot-20250101-000000.zip", "other.zip"])
        assert outside.read_bytes()


class TestNamedCopy:
    def _two_copies(self, monkeypatch):
        a = db.create_drama(title_en="Old title")
        older = _run_at(monkeypatch, _at(2))
        db.update_drama(a, title_en="New title")
        b = db.create_drama(title_en="B")
        newer = _run_at(monkeypatch, _at(3))
        return a, b, os.path.basename(older), os.path.basename(newer)

    def test_list_and_restore_from_a_named_older_copy(self, isolated_db, monkeypatch):
        a, b, older, newer = self._two_copies(monkeypatch)
        listing = abs_.list_snapshot_dramas(older)
        assert listing["name"] == older and [d["id"] for d in listing["dramas"]] == [a]
        assert abs_.list_snapshot_dramas()["name"] == newer
        with pytest.raises(NotFoundError):          # B isn't in the older copy
            abs_.restore_drama(b, confirm=True, confirm_text="RESTORE", snapshot=older)
        _delete_drama(a)
        res = abs_.restore_drama(a, confirm=True, confirm_text="RESTORE", snapshot=older)
        assert res["drama_id"] == a and not res["restored_as_new"]
        assert res["snapshot"] == older
        assert db.get_drama(a)["title_en"] == "Old title"
        _delete_drama(a)
        res = _restore(a)                            # default: the newest copy
        assert db.get_drama(a)["title_en"] == "New title"
        # the result and the audit entry say which copy was used
        assert res["snapshot"] == newer
        with contextlib.closing(_conn()) as c:
            details = [r[0] for r in c.execute(
                "SELECT detail_redacted FROM audit_log WHERE action = "
                "'library.restore_drama' ORDER BY id")]
        assert newer in details[-1] and older in details[-2]

    @pytest.mark.parametrize("bad", [
        "../x", "..", "../baihe_snapshot-20260302-030000.zip",
        "./baihe_snapshot-20260302-030000.zip", "baihe_snapshot-20260302-030000.zip/",
        "/etc/passwd", "ABSOLUTE", "other.zip", "baihe_snapshot-20260302-030000.ZIP",
        ".baihe_snapshot.partial-abcd.zip", "baihe_snapshot-20990101-000000.zip",
        "", "x" * 65, 5, ["baihe_snapshot-20260302-030000.zip"]])
    def test_name_injection_refused_and_nothing_changes(self, isolated_db, monkeypatch,
                                                        tmp_path, bad):
        a, _, older, _ = self._two_copies(monkeypatch)
        folder = _default_dir()
        if bad == "ABSOLUTE":
            bad = os.path.join(folder, older)
        with open(os.path.join(folder, "other.zip"), "wb") as fh:
            fh.write(_read(os.path.join(folder, older)))
        with open(os.path.join(folder, ".baihe_snapshot.partial-abcd.zip"), "wb") as fh:
            fh.write(_read(os.path.join(folder, older)))
        (tmp_path / "x").write_bytes(b"keep")
        files = {n: _read(os.path.join(folder, n)) for n in os.listdir(folder)}
        _delete_drama(a)
        before = _dump_all()
        for call in (lambda: abs_.list_snapshot_dramas(bad),
                     lambda: abs_.restore_drama(a, confirm=True, confirm_text="RESTORE",
                                                snapshot=bad),
                     lambda: abs_.delete_snapshot(confirm=True, confirm_text="DELETE",
                                                  snapshot=bad)):
            with pytest.raises((NotFoundError, InvalidInputError)) as e:
                call()
            assert folder not in str(e.value) and db.LIBRARY_DIR not in str(e.value)
        assert {n: _read(os.path.join(folder, n)) for n in os.listdir(folder)} == files
        assert _dump_all() == before
        assert (tmp_path / "x").read_bytes() == b"keep"

    def test_symlinked_copy_name_is_not_a_copy(self, isolated_db, monkeypatch, tmp_path):
        a, _, older, _ = self._two_copies(monkeypatch)
        outside = tmp_path / "outside.zip"
        data = _read(os.path.join(_default_dir(), older))
        outside.write_bytes(data)
        name = "baihe_snapshot-20260301-000000.zip"
        os.symlink(outside, os.path.join(_default_dir(), name))
        assert name not in [c["name"] for c in abs_.snapshot_info()["copies"]]
        _delete_drama(a)
        with pytest.raises(NotFoundError):
            abs_.restore_drama(a, confirm=True, confirm_text="RESTORE", snapshot=name)
        with pytest.raises(NotFoundError):
            abs_.delete_snapshot(confirm=True, confirm_text="DELETE", snapshot=name)
        _run_at(monkeypatch, _at(20))                # a prune never touches it
        abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True)
        assert os.path.islink(os.path.join(_default_dir(), name))
        assert outside.read_bytes() == data
        assert db.get_drama(a) is None


# --------------------------------------------------------------------------
# single-drama restore
# --------------------------------------------------------------------------

def test_child_tables_cover_every_fk_to_dramas(isolated_db):
    """Guard: every live table with a foreign key to dramas(id) is either
    restored (_CHILD_TABLES) or deliberately skipped (_SKIPPED_TABLES)."""
    with contextlib.closing(_conn()) as c:
        fk_tables = {t for t in _tables(c)
                     for fk in c.execute(f'PRAGMA foreign_key_list("{t}")')
                     if fk["table"] == "dramas"}
    assert fk_tables == set(abs_.CHILD_TABLES) | set(abs_._SKIPPED_TABLES)
    assert not set(abs_.CHILD_TABLES) & set(abs_._SKIPPED_TABLES)
    assert set(abs_._SKIPPED_TABLES) == {"usage_log", "bulk_jobs", "metadata_research_results",
                                            "speaker_merge_undos"}


class TestRestoreRoundTrip:
    def test_full_round_trip_series_gone(self, isolated_db):
        w = _seed_world()
        a, b = w["a"], w["b"]
        expected = _canon(a)
        with contextlib.closing(_conn()) as c:
            old_line_ids = {r[0] for r in c.execute("SELECT id FROM lines WHERE drama_id = ?", (a,))}
        _snap()
        _delete_drama(a)
        _delete_series(w["series"])
        db.delete_profile(w["p2"])
        before = _dump_all()

        res = _restore(a)

        assert res["drama_id"] == a and res["restored_as_new"] is False
        assert res["title"] == "Alpha" and res["media_restored"] is False
        assert res["snapshot_kind"] == "db-only"
        assert res["skipped_tables"] == ["bulk_jobs", "metadata_research_results",
                                         "speaker_merge_undos", "usage_log"]
        # P2 is gone, so its profile rows are not restored
        for t in PROFILE_TABLES:
            expected[t] = [r for r in expected[t] if "'P2'" not in r]
        got = _canon(a)
        assert got == expected
        for t in _fk_child_tables() + ["bubbles"]:
            assert got[t], f"seed left {t} empty"
            assert res["counts"][t] == len(got[t]), t
        assert res["counts"]["series"] == 1
        for t in SERIES_CHILDREN:
            assert res["counts"][t] == len(got[t]) > 0
        # the lines really got new ids and every reference follows them
        with contextlib.closing(_conn()) as c:
            new_line_ids = {r[0] for r in c.execute("SELECT id FROM lines WHERE drama_id = ?",
                                                    (a,))}
            assert new_line_ids.isdisjoint(old_line_ids)
            for t in LINE_REF_TABLES:
                for (lid,) in c.execute(f'SELECT line_id FROM "{t}" WHERE drama_id = ?', (a,)):
                    assert lid in new_line_ids, t
            for t, col in LINE_JSON.items():
                for (js,) in c.execute(f'SELECT "{col}" FROM "{t}" WHERE drama_id = ?', (a,)):
                    assert {it["id"] for it in json.loads(js)} == new_line_ids, t
            series_id = c.execute("SELECT series_id FROM dramas WHERE id = ?", (a,)).fetchone()[0]
            assert series_id is not None and series_id != w["series"]
            new_sc = {r[0] for r in c.execute("SELECT id FROM series_characters WHERE series_id = ?",
                                              (series_id,))}
            for t in ("characters", "voice_suggestion_dismissals"):
                for (scid,) in c.execute(f'SELECT series_character_id FROM "{t}" WHERE drama_id = ? '
                                         "AND series_character_id IS NOT NULL", (a,)):
                    assert scid in new_sc, t
            # the skipped tables stay empty for the restored drama
            for t in SKIPPED:
                assert c.execute(f'SELECT COUNT(*) FROM "{t}" WHERE drama_id = ?',
                                 (a,)).fetchone()[0] == 0, t
            assert c.execute("PRAGMA foreign_key_check").fetchall() == []
        _assert_only_restored_rows_added(before, _dump_all(), a)
        assert db.get_drama(b) is not None

    def test_series_still_exists_is_linked_not_duplicated(self, isolated_db):
        w = _seed_world()
        a = w["a"]
        expected = _canon(a)
        _snap()
        _delete_drama(a)
        with contextlib.closing(_conn()) as c:
            n_series = c.execute("SELECT COUNT(*) FROM series").fetchone()[0]
            n_children = {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                          for t in SERIES_CHILDREN}
        res = _restore(a)
        assert "series" not in res["counts"]
        assert _canon(a) == expected
        with contextlib.closing(_conn()) as c:
            assert c.execute("SELECT COUNT(*) FROM series").fetchone()[0] == n_series
            assert {t: c.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0]
                    for t in SERIES_CHILDREN} == n_children
            assert c.execute("SELECT series_id FROM dramas WHERE id = ?",
                             (a,)).fetchone()[0] == w["series"]
            got_sc = {r[0] for r in c.execute(
                "SELECT series_character_id FROM characters WHERE drama_id = ? "
                "AND series_character_id IS NOT NULL", (a,))}
            assert got_sc == {w["sc"]["Lin"]}

    def test_id_in_use_restores_as_new_and_leaves_live_drama_alone(self, isolated_db):
        w = _seed_world()
        a = w["a"]
        expected = _canon(a)
        _snap()
        db.update_drama(a, title_en="Alpha edited", status="dubbed")
        with contextlib.closing(_conn()) as c:
            _insert(c, "lines", drama_id=a, idx=9, start=9, end=10, zh="new-live-line", en="")
            c.commit()
        live_before = _canon(a)
        before = _dump_all()

        res = _restore(a)

        assert res["restored_as_new"] is True
        new = res["drama_id"]
        assert new != a and db.get_drama(new) is not None
        today = datetime.date.today().isoformat()
        assert res["title"] == f"Alpha (restored {today})"
        assert db.get_drama(new)["title_en"] == f"Alpha (restored {today})"
        assert _canon(a) == live_before
        got = _canon(new)
        want = dict(expected)
        want["dramas"] = got["dramas"]   # title differs by design; compare the rest below
        assert got == want
        d_old = dict(eval(expected["dramas"]))   # noqa: S307 -- our own repr of a dict
        d_new = dict(eval(got["dramas"]))        # noqa: S307
        assert d_new.pop("title_en") == f"Alpha (restored {today})"
        d_old.pop("title_en")
        assert d_new == d_old
        _assert_only_restored_rows_added(before, _dump_all(), new)

    def test_title_zh_only_gets_suffix_on_title_zh(self, isolated_db):
        a = db.create_drama(title_zh="中文名")
        _snap()
        res = _restore(a)
        assert res["restored_as_new"] is True
        assert db.get_drama(res["drama_id"])["title_zh"].startswith("中文名 (restored ")

    def test_folder_exists_restores_as_new(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        os.makedirs(os.path.join(db.DRAMAS_DIR, str(a)))
        res = _restore(a)
        assert res["restored_as_new"] is True and res["drama_id"] != a
        assert db.get_drama(a) is None

    def test_restore_twice_second_is_new(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        first = _restore(a)
        second = _restore(a)
        assert first["drama_id"] == a and not first["restored_as_new"]
        assert second["drama_id"] not in (a,) and second["restored_as_new"]

    def test_unowned_user_cleared(self, isolated_db):
        from services import auth_service
        u = auth_service.add_user("kid@example.com")
        a = db.create_drama(title_en="A", owner_user_id=u["id"])
        _snap()
        _delete_drama(a)
        with contextlib.closing(_conn()) as c:
            c.execute("DELETE FROM users WHERE id = ?", (u["id"],))
            c.commit()
        _restore(a)
        assert db.get_drama(a)["owner_user_id"] is None

    def test_not_in_snapshot(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap()
        before = _dump_all()
        with pytest.raises(NotFoundError):
            _restore(a + 50)
        assert _dump_all() == before

    @pytest.mark.parametrize("did", [0, -1, "1", True, 1.0, None])
    def test_bad_id(self, isolated_db, did):
        with pytest.raises(InvalidInputError):
            abs_.restore_drama(did, confirm=True, confirm_text="RESTORE")

    @pytest.mark.parametrize("confirm,text", [(False, "RESTORE"), (True, "restore"), (True, ""),
                                              (1, "RESTORE"), (True, "DELETE")])
    def test_typed_confirm_required(self, isolated_db, confirm, text):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        before = _dump_all()
        with pytest.raises(InvalidInputError):
            abs_.restore_drama(a, confirm=confirm, confirm_text=text)
        assert _dump_all() == before

    def test_refused_while_backup_running(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        before = _dump_all()
        for job_id in (abs_.JOB_ID, las.BACKUP_JOB_ID, las.EXPORT_JOB_ID):
            _put_job(job_id)
            with pytest.raises(ConflictError):
                _restore(a)
            background_jobs.clear_job(job_id)
        assert _dump_all() == before

    def test_refused_during_library_restore(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        assert background_jobs.acquire_exclusive("Library restore")
        try:
            with pytest.raises(ConflictError):
                _restore(a)
        finally:
            background_jobs.release_exclusive()
        assert db.get_drama(a) is None

    def test_scheduled_check_busy_while_restore_holds_library(self, isolated_db, monkeypatch):
        a = db.create_drama(title_en="A")
        _snap()
        _delete_drama(a)
        _enable()
        _state()
        seen = []
        real = abs_._restore_from

        def spy(*args, **kw):
            seen.append(abs_.check_and_run())
            for start in (lambda: abs_.start_now(replace=True), las.start_backup,
                          las.start_database_backup):
                try:
                    start()
                    seen.append("started a backup during the restore")
                except ConflictError:
                    pass
            return real(*args, **kw)
        monkeypatch.setattr(abs_, "_restore_from", spy)
        monkeypatch.setattr(abs_, "_start", lambda media: pytest.fail("backup started"))
        _restore(a)
        assert seen == ["busy"]

    def test_failed_insert_rolls_back(self, isolated_db, monkeypatch):
        w = _seed_world()
        a = w["a"]
        _snap(include_media=True)
        _delete_drama(a)
        _delete_series(w["series"])
        before = _dump_all()
        real = abs_._insert

        def flaky(dst, table, row, cols):
            if table == "wiki_entries":
                raise sqlite3.IntegrityError(f"boom {db.LIBRARY_DIR}")
            return real(dst, table, row, cols)
        monkeypatch.setattr(abs_, "_insert", flaky)
        with pytest.raises(ServiceError) as e:
            _restore(a)
        assert db.LIBRARY_DIR not in str(e.value)
        assert _dump_all() == before
        assert not [n for n in os.listdir(db.DRAMAS_DIR)
                    if n.startswith((".restoring", db.MEDIA_STAGING_PREFIX))] \
            if os.path.isdir(db.DRAMAS_DIR) else True


def _assert_only_restored_rows_added(before, after, live_id):
    """Every row that existed before is still there, unchanged; the new
    rows belong to the restored drama (or its recreated series, bubbles on
    its pages, its audit entry)."""
    with contextlib.closing(_conn()) as c:
        pages = {r[0] for r in c.execute("SELECT id FROM pages WHERE drama_id = ?", (live_id,))}
        sid = c.execute("SELECT series_id FROM dramas WHERE id = ?", (live_id,)).fetchone()[0]
        cols = {t: [r[1] for r in c.execute(f'PRAGMA table_info("{t}")')] for t in after}
    for t, rows in before.items():
        missing = rows - after.get(t, collections.Counter())
        assert not missing, f"{t}: rows changed or removed: {list(missing)[:3]}"
    for t, rows in after.items():
        added = rows - before.get(t, collections.Counter())
        for rep in added:
            row = dict(zip(cols[t], eval(rep)))   # noqa: S307 -- repr of our own tuple
            if "drama_id" in row and t != "usage_log":
                assert row["drama_id"] == live_id, (t, row)
            elif t == "bubbles":
                assert row["page_id"] in pages, row
            elif t == "series":
                assert row["id"] == sid, row
            elif t in SERIES_CHILDREN:
                assert row["series_id"] == sid, (t, row)
            elif t == "dramas":
                assert row["id"] == live_id, row
            elif t == "audit_log":
                pass
            else:
                pytest.fail(f"unexpected new row in {t}: {row}")


class TestRestoreMedia:
    def _media_drama(self):
        a = db.create_drama(title_en="A")
        d = _ddir(a)
        os.makedirs(os.path.join(d, "clips"), exist_ok=True)
        with open(os.path.join(d, "audio.wav"), "wb") as fh:
            fh.write(b"AUDIO" * 100)
        with open(os.path.join(d, "clips", "c.wav"), "wb") as fh:
            fh.write(b"CLIP")
        return a

    def test_full_snapshot_restores_folder(self, isolated_db):
        a = self._media_drama()
        b = db.create_drama(title_en="B")
        os.makedirs(_ddir(b), exist_ok=True)
        with open(os.path.join(_ddir(b), "b.wav"), "wb") as fh:
            fh.write(b"B")
        _snap(include_media=True)
        _delete_drama(a)
        assert not os.path.exists(_ddir(a))
        with open(os.path.join(_ddir(b), "b.wav"), "wb") as fh:
            fh.write(b"B-changed")
        res = _restore(a)
        assert res["media_restored"] is True and res["snapshot_kind"] == "full"
        assert _read(os.path.join(_ddir(a), "audio.wav")) == b"AUDIO" * 100
        assert _read(os.path.join(_ddir(a), "clips", "c.wav")) == b"CLIP"
        assert _read(os.path.join(_ddir(b), "b.wav")) == b"B-changed"
        assert not [n for n in os.listdir(db.DRAMAS_DIR) if n.startswith(".")]

    def test_full_snapshot_restore_as_new_gets_its_own_folder(self, isolated_db):
        a = self._media_drama()
        _snap(include_media=True)
        with open(os.path.join(_ddir(a), "audio.wav"), "wb") as fh:
            fh.write(b"live")
        res = _restore(a)
        new = res["drama_id"]
        assert res["restored_as_new"] and res["media_restored"]
        assert _read(os.path.join(_ddir(new), "audio.wav")) == b"AUDIO" * 100
        assert _read(os.path.join(_ddir(a), "audio.wav")) == b"live"

    def test_db_only_snapshot_restores_no_media(self, isolated_db):
        a = self._media_drama()
        _snap(include_media=False)
        _delete_drama(a)
        res = _restore(a)
        assert res["media_restored"] is False
        assert not os.path.exists(_ddir(a))

    def test_full_snapshot_drama_without_files(self, isolated_db):
        a = db.create_drama(title_en="A")
        _snap(include_media=True)
        _delete_drama(a)
        res = _restore(a)
        assert res["media_restored"] is False and res["drama_id"] == a

    def test_commit_failure_removes_only_the_restored_folder(self, isolated_db, monkeypatch):
        a = self._media_drama()
        b = db.create_drama(title_en="B")
        os.makedirs(_ddir(b))
        with open(os.path.join(_ddir(b), "b.wav"), "wb") as fh:
            fh.write(b"B")
        _snap(include_media=True)
        _delete_drama(a)
        armed, real_move = [], abs_.move_media_in

        def move(*args, **kw):
            real_move(*args, **kw)
            assert os.path.isdir(_ddir(a))     # in place before the commit
            armed.append(1)

        def commit(self):
            if armed:
                armed.clear()
                raise sqlite3.OperationalError("disk I/O error")
            return sqlite3.Connection.commit(self)
        monkeypatch.setattr(abs_, "move_media_in", move)
        monkeypatch.setattr(db._TrackedConnection, "commit", commit, raising=False)
        with pytest.raises(ServiceError):
            _restore(a)
        monkeypatch.undo()
        assert db.get_drama(a) is None and not os.path.lexists(_ddir(a))
        assert sorted(os.listdir(db.DRAMAS_DIR)) == [str(b)]
        assert _read(os.path.join(_ddir(b), "b.wav")) == b"B"


# --------------------------------------------------------------------------
# zip validation of the snapshot before a restore
# --------------------------------------------------------------------------

class TestRestoreRejectsUnsafeSnapshot:
    def _craft(self, extra):
        """A real full snapshot of drama A, then rewritten at the snapshot
        path with `extra` members added: list of (ZipInfo|name, bytes)."""
        a = db.create_drama(title_en="A")
        os.makedirs(_ddir(a), exist_ok=True)
        with open(os.path.join(_ddir(a), "ok.wav"), "wb") as fh:
            fh.write(b"ok")
        path = _snap(include_media=True)
        with zipfile.ZipFile(path) as zf:
            members = [(i, zf.read(i)) for i in zf.infolist()]
        with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as zf:
            for info, data in members:
                zf.writestr(info, data)
            for info, data in extra(a):
                zf.writestr(info, data, compress_type=zipfile.ZIP_DEFLATED)
        _delete_drama(a)
        return a

    def _assert_refused(self, a):
        before = _dump_all()
        listing = _files_under(db.LIBRARY_DIR)
        with pytest.raises(InvalidInputError) as e:
            _restore(a)
        assert db.LIBRARY_DIR not in str(e.value)
        assert _dump_all() == before
        assert _files_under(db.LIBRARY_DIR) == listing
        assert db.get_drama(a) is None

    @pytest.mark.parametrize("name", [
        "dramas/{a}/../../../evil.txt", "../evil.txt", "/etc/evil.txt", "C:/evil.txt",
        "dramas/{a}/./x.txt", "dramas/{a}/x.txt.",
    ])
    def test_bad_member_names(self, isolated_db, name):
        a = self._craft(lambda a: [(zipfile.ZipInfo(name.format(a=a)), b"evil")])
        self._assert_refused(a)

    def test_symlink_entry(self, isolated_db):
        def extra(a):
            info = zipfile.ZipInfo(f"dramas/{a}/link")
            info.create_system = 3
            info.external_attr = (0o120777 << 16)
            return [(info, b"/etc/passwd")]
        self._assert_refused(self._craft(extra))

    def test_too_many_drama_members(self, isolated_db, monkeypatch):
        monkeypatch.setattr(las, "RESTORE_MAX_MEMBERS", 6)
        a = self._craft(lambda a: [(f"dramas/{a}/f{i}.txt", b"x") for i in range(10)])
        self._assert_refused(a)

    def test_oversized_drama_member(self, isolated_db, monkeypatch):
        a = self._craft(lambda a: [(f"dramas/{a}/big.bin", b"\0" * 3_000_000)])
        with zipfile.ZipFile(_default_path()) as zf:
            db_size = zf.getinfo("library.db").file_size
        assert db_size < 2_000_000
        monkeypatch.setattr(abs_, "_MAX_MEMBER_BYTES", 2_000_000)
        self._assert_refused(a)

    def test_oversized_library_db(self, isolated_db, monkeypatch):
        a = db.create_drama(title_en="A")
        path = _snap()
        _delete_drama(a)
        with zipfile.ZipFile(path) as zf:
            db_size = zf.getinfo("library.db").file_size
        monkeypatch.setattr(abs_, "_MAX_MEMBER_BYTES", db_size - 1)
        self._assert_refused(a)

    def test_upload_caps_still_reject_bomb_and_member_count(self, isolated_db, monkeypatch):
        """The whole-zip caps (expanded total, member count, member size)
        still apply to validate_backup_file's default (an upload); only
        the app's own snapshot skips them (check_limits=False)."""
        self._craft(lambda a: [(f"dramas/{a}/bomb.bin", b"\0" * 20_000_000)] +
                    [(f"dramas/{a}/f{i}", b"x") for i in range(10)])
        path = _default_path()
        assert os.path.getsize(path) < 2_000_000   # compresses hard
        las.validate_backup_file(path, check_disk=False)   # within the real caps
        las.validate_backup_file(path, check_disk=False, check_limits=False)
        with monkeypatch.context() as m:   # expanded total
            m.setattr(las, "_RESTORE_MIN_TOTAL_BYTES", 0)
            m.setattr(las, "_RESTORE_EXPANSION_FACTOR", 2)
            m.setattr(las, "_library_size", lambda: 0)
            with pytest.raises(InvalidInputError):
                las.validate_backup_file(path, check_disk=False)
            las.validate_backup_file(path, check_disk=False, check_limits=False)
            with open(path, "rb") as fh:
                with pytest.raises(InvalidInputError):
                    las.validate_backup_zip(fh.read())
        with monkeypatch.context() as m:   # member count
            m.setattr(las, "RESTORE_MAX_MEMBERS", 5)
            with pytest.raises(InvalidInputError):
                las.validate_backup_file(path, check_disk=False)
            las.validate_backup_file(path, check_disk=False, check_limits=False)
        with monkeypatch.context() as m:   # one member too large
            m.setattr(wjs, "MAX_RESTORE_MEMBER_BYTES", 10_000_000)
            with pytest.raises(InvalidInputError):
                las.validate_backup_file(path, check_disk=False)
            las.validate_backup_file(path, check_disk=False, check_limits=False)

    @pytest.mark.parametrize("name", ["../evil", "/abs", "C:/x"])
    def test_structural_checks_apply_without_limits(self, isolated_db, name):
        self._craft(lambda a: [(zipfile.ZipInfo(name), b"x")])
        with pytest.raises(InvalidInputError):
            las.validate_backup_file(_default_path(), check_disk=False, check_limits=False)

    def test_snapshot_larger_than_upload_caps_is_still_written(self, isolated_db, monkeypatch):
        a = db.create_drama(title_en="A")
        os.makedirs(_ddir(a), exist_ok=True)
        for i in range(8):
            with open(os.path.join(_ddir(a), f"f{i}.bin"), "wb") as fh:
                fh.write(b"\0" * 50_000)
        monkeypatch.setattr(las, "RESTORE_MAX_MEMBERS", 3)
        monkeypatch.setattr(wjs, "MAX_RESTORE_MEMBER_BYTES", 10_000)
        monkeypatch.setattr(las, "_RESTORE_MIN_TOTAL_BYTES", 0)
        monkeypatch.setattr(las, "_RESTORE_EXPANSION_FACTOR", 1)
        monkeypatch.setattr(las, "_library_size", lambda: 0)
        _snap(include_media=True)
        assert abs_._get_state()["last_error"] is None
        assert abs_.snapshot_info()["kind"] == "full"
        _delete_drama(a)
        monkeypatch.setattr(las, "RESTORE_MAX_MEMBERS", 100)
        res = _restore(a)
        assert res["media_restored"] and len(os.listdir(_ddir(a))) == 8

    def test_encrypted_flag(self, isolated_db):
        """zipfile clears flag_bits on write, so the encrypted bit is set in
        the central directory by hand afterwards."""
        import struct
        a = self._craft(lambda a: [(f"dramas/{a}/enc.bin", b"x")])
        target = f"dramas/{a}/enc.bin".encode()
        data = bytearray(_read(_default_path()))
        pos, hit = 0, False
        while True:
            pos = data.find(b"PK\x01\x02", pos)
            if pos < 0:
                break
            name_len = struct.unpack_from("<H", data, pos + 28)[0]
            if bytes(data[pos + 46:pos + 46 + name_len]) == target:
                flags = struct.unpack_from("<H", data, pos + 8)[0]
                struct.pack_into("<H", data, pos + 8, flags | 0x1)
                hit = True
            pos += 4
        assert hit
        with open(_default_path(), "wb") as fh:
            fh.write(data)
        with zipfile.ZipFile(_default_path()) as zf:
            assert zf.getinfo(target.decode()).flag_bits & 0x1
        self._assert_refused(a)

    def test_corrupt_library_db(self, isolated_db):
        a = db.create_drama(title_en="A")
        path = _snap()
        with zipfile.ZipFile(path) as zf:
            manifest = zf.read("manifest.json")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("library.db", b"SQLite format 3\x00" + b"\xff" * 4096)
            zf.writestr("manifest.json", manifest)
        _delete_drama(a)
        self._assert_refused(a)

    def test_missing_manifest(self, isolated_db):
        a = db.create_drama(title_en="A")
        path = _snap()
        with zipfile.ZipFile(path) as zf:
            dbb = zf.read("library.db")
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("library.db", dbb)
        _delete_drama(a)
        self._assert_refused(a)


# --------------------------------------------------------------------------
# B-14 tombstone sweep
# --------------------------------------------------------------------------

class TestTombstoneSweep:
    def _mk(self, name, age):
        path = os.path.join(db.DRAMAS_DIR, name)
        os.makedirs(path)
        with open(os.path.join(path, "f"), "w") as fh:
            fh.write("x")
        t = time.time() - age
        os.utime(path, (t, t))
        return path

    def test_removes_only_old_matching_dirs(self, isolated_db, tmp_path):
        os.makedirs(db.DRAMAS_DIR, exist_ok=True)
        day = 24 * 3600
        old = self._mk("5.deleting-0123abcd", day + 60)
        fresh = self._mk("6.deleting-0123abcd", 60)
        almost = self._mk("7.deleting-0123abcd", day - 60)
        bad_hex = self._mk("8.deleting-XYZ12345", 2 * day)
        no_id = self._mk("abc.deleting-0123abcd", 2 * day)
        live = self._mk("9", 2 * day)
        other = self._mk("9.deleting-0123abcd.bak", 2 * day)
        target = tmp_path / "target"
        target.mkdir()
        (target / "keep.txt").write_text("keep")
        t = time.time() - 3 * day
        os.utime(target, (t, t))
        link = os.path.join(db.DRAMAS_DIR, "10.deleting-0123abcd")
        os.symlink(target, link)
        os.utime(link, (t, t), follow_symlinks=False)
        file_tomb = os.path.join(db.DRAMAS_DIR, "11.deleting-0123abcd")
        with open(file_tomb, "w") as fh:
            fh.write("f")
        os.utime(file_tomb, (t, t))

        assert drama_service.cleanup_stale_tombstones() == 1
        assert not os.path.exists(old)
        for p in (fresh, almost, bad_hex, no_id, live, other, file_tomb):
            assert os.path.exists(p), p
        assert os.path.islink(link) and (target / "keep.txt").read_text() == "keep"

    def test_no_dramas_dir(self, isolated_db):
        if os.path.isdir(db.DRAMAS_DIR):
            os.rmdir(db.DRAMAS_DIR)
        assert drama_service.cleanup_stale_tombstones() == 0

    def test_hard_delete_stamps_tombstone_fresh(self, isolated_db, monkeypatch):
        a = db.create_drama(title_en="A")
        d = _ddir(a)
        os.makedirs(d, exist_ok=True)
        t = time.time() - 10 * 24 * 3600
        os.utime(d, (t, t))

        class NoRmtree:
            @staticmethod
            def rmtree(path, *a, **k):
                raise OSError("in use")
        with monkeypatch.context() as m:
            m.setattr(drama_service, "shutil", NoRmtree)
            assert drama_service.hard_delete_drama(a) is True   # leftover folder
        tombs = [n for n in os.listdir(db.DRAMAS_DIR) if ".deleting-" in n]
        assert len(tombs) == 1
        # the in-flight/leftover tombstone is fresh, so the sweep leaves it
        assert drama_service.cleanup_stale_tombstones() == 0
        assert os.path.isdir(os.path.join(db.DRAMAS_DIR, tombs[0]))
        # a day later it is swept
        later = time.time() + 24 * 3600 + 5
        assert drama_service.cleanup_stale_tombstones(now=later) == 1
        assert not os.path.exists(os.path.join(db.DRAMAS_DIR, tombs[0]))


# --------------------------------------------------------------------------
# series resolution on restore (lead's review change 1)
# --------------------------------------------------------------------------

def _series_rows():
    with contextlib.closing(_conn()) as c:
        return {r["id"]: dict(r) for r in c.execute("SELECT * FROM series")}


class TestRestoreSeriesResolution:
    def test_name_only_match_never_links(self, isolated_db):
        """Same name, different id (the series was deleted and a new one
        made with its name): the snapshot's series comes back as its own
        series, renamed "... (restored <date>)"."""
        w = _seed_world()
        a = w["a"]
        _snap()
        _delete_drama(a)
        _delete_series(w["series"])
        with contextlib.closing(_conn()) as c:
            other = _insert(c, "series", name="Saga", created_at="y")
            _insert(c, "series_characters", series_id=other, character_name="Lin")
            c.commit()
        res = _restore(a)
        sid = db.get_drama(a)["series_id"]
        assert sid not in (other, w["series"])
        today = datetime.date.today().isoformat()
        assert _series_rows()[sid]["name"] == f"Saga (restored {today})"
        assert _series_rows()[other]["name"] == "Saga"
        assert res["counts"]["series"] == 1
        with contextlib.closing(_conn()) as c:
            assert c.execute("SELECT COUNT(*) FROM series_characters WHERE series_id = ?",
                             (other,)).fetchone()[0] == 1
            names = {r[0] for r in c.execute(
                "SELECT character_name FROM series_characters WHERE series_id = ?", (sid,))}
            assert names == {"Lin", "Mei"}
            scids = [r[0] for r in c.execute(
                "SELECT series_character_id FROM characters WHERE drama_id = ? "
                "AND series_character_id IS NOT NULL", (a,))]
            own = {r[0] for r in c.execute("SELECT id FROM series_characters WHERE series_id = ?",
                                           (sid,))}
            assert scids and set(scids) <= own

    def test_restored_name_also_taken_gets_counter(self, isolated_db):
        w = _seed_world()
        a = w["a"]
        _snap()
        _delete_drama(a)
        _delete_series(w["series"])
        today = datetime.date.today().isoformat()
        with contextlib.closing(_conn()) as c:
            _insert(c, "series", name="Saga")
            _insert(c, "series", name=f"Saga (restored {today})")
            c.commit()
        _restore(a)
        sid = db.get_drama(a)["series_id"]
        assert _series_rows()[sid]["name"] == f"Saga (restored {today}, 2)"

    def test_private_series_of_someone_else_not_reused(self, isolated_db):
        from services import auth_service
        owner = auth_service.add_user("owner@example.com")
        kid = auth_service.add_user("kid@example.com")
        with contextlib.closing(_conn()) as c:
            sid = _insert(c, "series", name="Mine", owner_user_id=owner["id"], is_private=0)
            a = _insert(c, "dramas", title_en="Kid's", series_id=sid, owner_user_id=kid["id"],
                        is_private=0, status="aligned")
            c.commit()
        _snap()
        _delete_drama(a)
        with contextlib.closing(_conn()) as c:   # the owner made the series private since
            c.execute("UPDATE series SET is_private = 1 WHERE id = ?", (sid,))
            c.commit()
            c.execute("INSERT INTO glossary_terms (series_id, term_original, term_translation) "
                      "VALUES (?, '甲', 'A')", (sid,))
            c.commit()
        before = _series_rows()
        res = _restore(a)
        # Nothing of the owner's now-private series is copied or re-shared:
        # the drama comes back with no series and the result says why.
        assert db.get_drama(a)["series_id"] is None
        assert res["series"] == "dropped_private"
        assert "series" not in res["counts"]
        assert _series_rows() == before
        with contextlib.closing(_conn()) as c:
            assert c.execute("SELECT COUNT(*) FROM glossary_terms").fetchone()[0] == 1
        assert db.get_drama(a)["owner_user_id"] == kid["id"]

    @pytest.mark.parametrize("drama_owner", ["none", "series_owner"])
    def test_private_series_reused_for_owner_or_pc_drama(self, isolated_db, drama_owner):
        from services import auth_service
        owner = auth_service.add_user("owner@example.com")
        drama_uid = None if drama_owner == "none" else owner["id"]
        with contextlib.closing(_conn()) as c:
            sid = _insert(c, "series", name="Mine", owner_user_id=owner["id"], is_private=1)
            a = _insert(c, "dramas", title_en="D", series_id=sid, owner_user_id=drama_uid,
                        status="aligned")
            c.commit()
        _snap()
        _delete_drama(a)
        res = _restore(a)
        assert db.get_drama(a)["series_id"] == sid
        assert res["series"] == "linked"
        assert "series" not in res["counts"]
        assert len(_series_rows()) == 1

    def test_reused_series_maps_characters_by_id(self, isolated_db):
        """On reuse a series_character id is kept when it still exists in
        that series; a removed one becomes NULL (characters) or drops the
        row (voice_suggestion_dismissals)."""
        w = _seed_world()
        a = w["a"]
        _snap()
        _delete_drama(a)
        with contextlib.closing(_conn()) as c:
            # Mei removed from the series; Lin renamed (same id)
            c.execute("DELETE FROM series_characters WHERE id = ?", (w["sc"]["Mei"],))
            c.execute("UPDATE series_characters SET character_name = 'Lin Renamed' WHERE id = ?",
                      (w["sc"]["Lin"],))
            c.commit()
        res = _restore(a)
        assert db.get_drama(a)["series_id"] == w["series"]
        with contextlib.closing(_conn()) as c:
            chars = {r[0]: r[1] for r in c.execute(
                "SELECT speaker_label, series_character_id FROM characters WHERE drama_id = ?",
                (a,))}
            assert chars == {"S0": w["sc"]["Lin"], "S1": None}
            assert c.execute("SELECT COUNT(*) FROM voice_suggestion_dismissals WHERE drama_id = ?",
                             (a,)).fetchone()[0] == 0
        assert res["counts"]["voice_suggestion_dismissals"] == 0

    def test_drama_in_series_restored_not_private(self, isolated_db):
        w = _seed_world()
        a = w["a"]
        with contextlib.closing(_conn()) as c:   # an inconsistent old row
            c.execute("UPDATE dramas SET is_private = 1 WHERE id = ?", (a,))
            c.commit()
        _snap()
        _delete_drama(a)
        _restore(a)
        assert not db.get_drama(a)["is_private"]


# --------------------------------------------------------------------------
# folder change moves the snapshot (lead's review change 4)
# --------------------------------------------------------------------------

class TestFolderMove:
    def test_moves_existing_snapshot(self, isolated_db, tmp_path):
        db.create_drama(title_en="A")
        _snap()
        data = _read(_default_path())
        name = os.path.basename(_default_path())
        out = tmp_path / "ext"
        out.mkdir()
        # a file of that name this library didn't make is never replaced
        (out / name).write_bytes(b"stale file")
        with pytest.raises(ConflictError) as e:
            abs_.set_settings(folder=str(out))
        assert str(out) not in str(e.value) and name in str(e.value)
        assert (out / name).read_bytes() == b"stale file"
        assert _read(_default_path()) == data and abs_.get_settings()["folder"] == ""
        # this library's own copy of that name (left from an earlier move) is
        (out / name).write_bytes(data)
        abs_.set_settings(folder=str(out))
        assert (out / name).read_bytes() == data
        assert sorted(os.listdir(out)) == [name]
        assert _default_path() is None
        assert abs_.snapshot_info()["exists"]
        # and back to the default folder
        abs_.set_settings(folder="")
        assert _read(_default_path()) == data
        assert os.listdir(out) == []

    def test_moves_every_copy_and_the_legacy_file_only(self, isolated_db, tmp_path,
                                                       monkeypatch):
        db.create_drama(title_en="A")
        names = [os.path.basename(_run_at(monkeypatch, _at(day))) for day in (2, 3, 4)]
        legacy = os.path.join(_default_dir(), abs_.LEGACY_SNAPSHOT_NAME)
        with open(_newest(), "rb") as src, open(legacy, "wb") as dst:
            dst.write(src.read())
        _untag(legacy, _at(1))
        old = _at(1).timestamp()        # older than every copy
        os.utime(legacy, (old, old))
        with open(os.path.join(_default_dir(), "notes.txt"), "w") as fh:
            fh.write("mine")
        before = {n: _read(os.path.join(_default_dir(), n))
                  for n in names + [abs_.LEGACY_SNAPSHOT_NAME]}
        out = tmp_path / "ext"
        out.mkdir()
        abs_.set_settings(folder=str(out))
        assert {n: (out / n).read_bytes() for n in os.listdir(out)} == before
        assert os.listdir(_default_dir()) == ["notes.txt"]
        assert [c["name"] for c in abs_.snapshot_info()["copies"]] == \
            sorted(names, reverse=True) + [abs_.LEGACY_SNAPSHOT_NAME]

    def test_legacy_file_keeps_its_date_across_drives(self, isolated_db, tmp_path,
                                                      monkeypatch):
        db.create_drama(title_en="A")
        legacy = _make_legacy(_at(1))
        real_replace = os.replace

        def cross_device(a, b, *args, **kw):
            if os.path.abspath(a) == os.path.abspath(legacy):
                raise OSError(18, "Invalid cross-device link")
            return real_replace(a, b, *args, **kw)
        monkeypatch.setattr(abs_.os, "replace", cross_device)
        abs_.set_settings(folder=str(tmp_path))
        monkeypatch.undo()
        [copy] = abs_._list_copies(str(tmp_path))
        assert copy["name"] == abs_.LEGACY_SNAPSHOT_NAME
        assert abs(copy["at"] - _at(1)) < datetime.timedelta(seconds=2)

    def test_failed_move_of_a_later_copy_moves_the_earlier_ones_back(
            self, isolated_db, tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        paths = [_run_at(monkeypatch, _at(day)) for day in (2, 3, 4)]
        before = {p: _read(p) for p in paths}
        real_replace = os.replace
        moved = []

        def flaky(a, b, *args, **kw):
            if os.path.dirname(os.path.abspath(a)) == _default_dir() and len(moved) >= 2:
                raise OSError("in use")
            moved.append(a)
            return real_replace(a, b, *args, **kw)
        monkeypatch.setattr(abs_.os, "replace", flaky)
        monkeypatch.setattr(abs_.shutil, "copyfile",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("no copy")))
        with pytest.raises(ServiceError):
            abs_.set_settings(folder=str(tmp_path))
        monkeypatch.undo()
        assert {p: _read(p) for p in paths} == before
        assert os.listdir(tmp_path) == []
        assert abs_.get_settings()["folder"] == ""

    def test_no_snapshot_nothing_to_move(self, isolated_db, tmp_path):
        abs_.set_settings(folder=str(tmp_path))
        assert os.listdir(tmp_path) == []
        assert abs_.get_settings()["folder"] == str(tmp_path)

    def test_same_folder_is_noop(self, isolated_db):
        db.create_drama(title_en="A")
        _snap()
        data = _read(_default_path())
        abs_.set_settings(folder="", enabled=True)
        assert _read(_default_path()) == data

    def test_refused_while_backup_runs(self, isolated_db, tmp_path):
        db.create_drama(title_en="A")
        _snap()
        data = _read(_default_path())
        _put_job(abs_.JOB_ID)
        with pytest.raises(ConflictError):
            abs_.set_settings(folder=str(tmp_path), enabled=True)
        assert abs_.get_settings() == abs_.DEFAULT_SETTINGS
        assert _read(_default_path()) == data
        assert os.listdir(tmp_path) == []

    def test_copy_fallback_across_drives(self, isolated_db, tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        _snap()
        data = _read(_default_path())
        src = _default_path()
        real_replace = os.replace

        def cross_device(a, b, *args, **kw):
            if os.path.abspath(a) == os.path.abspath(src):
                raise OSError(18, "Invalid cross-device link")
            return real_replace(a, b, *args, **kw)
        monkeypatch.setattr(abs_.os, "replace", cross_device)
        abs_.set_settings(folder=str(tmp_path))
        monkeypatch.undo()
        name = os.path.basename(src)
        assert (tmp_path / name).read_bytes() == data
        assert os.listdir(tmp_path) == [name]    # no partial left behind
        assert not os.path.exists(src)

    @pytest.mark.parametrize("alias", ["symlink", "same_path"])
    def test_same_folder_under_another_path_keeps_every_copy(
            self, isolated_db, tmp_path, monkeypatch, alias):
        """The new folder is the current one under a different path string
        (a link to it, or the default folder typed out): nothing moves, and
        a failed same-file rename can't turn into a copy-then-remove that
        deletes the copies."""
        db.create_drama(title_en="A")
        for day in (2, 3):
            _run_at(monkeypatch, _at(day))
        before = {n: _read(os.path.join(_default_dir(), n)) for n in _copy_names()}
        assert len(before) == 2
        if alias == "symlink":
            folder = str(tmp_path / "alias")
            os.symlink(_default_dir(), folder)
        else:
            folder = _default_dir()
        real_replace = os.replace

        def rename_fails(a, b, *args, **kw):
            if _COPY_RE.fullmatch(os.path.basename(a)):
                raise OSError(18, "Invalid cross-device link")
            return real_replace(a, b, *args, **kw)
        monkeypatch.setattr(abs_.os, "replace", rename_fails)
        abs_.set_settings(folder=folder)
        monkeypatch.undo()
        assert abs_.get_settings()["folder"] == folder
        assert {n: _read(os.path.join(_default_dir(), n)) for n in _copy_names()} == before
        assert sorted(os.listdir(_default_dir())) == sorted(before)   # no partial left

    def test_move_failure_changes_nothing(self, isolated_db, tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        _snap()
        data = _read(_default_path())

        def fail(*a, **k):
            raise OSError("no")
        monkeypatch.setattr(abs_.os, "replace", fail)
        with pytest.raises(ServiceError) as e:
            abs_.set_settings(folder=str(tmp_path))
        monkeypatch.undo()
        assert str(tmp_path) not in str(e.value) and db.LIBRARY_DIR not in str(e.value)
        assert abs_.get_settings()["folder"] == ""
        assert _read(_default_path()) == data
        assert os.listdir(tmp_path) == []

    def test_symlink_at_destination_refused(self, isolated_db, tmp_path):
        db.create_drama(title_en="A")
        _snap()
        data = _read(_default_path())
        victim = tmp_path / "victim.txt"
        victim.write_text("keep")
        out = tmp_path / "ext"
        out.mkdir()
        os.symlink(victim, out / os.path.basename(_default_path()))
        with pytest.raises(ServiceError):
            abs_.set_settings(folder=str(out))
        assert victim.read_text() == "keep"
        assert _read(_default_path()) == data
        assert abs_.get_settings()["folder"] == ""

    def test_settings_saved_under_the_snapshot_lock(self, isolated_db, monkeypatch):
        """The read-modify-write of the settings happens under _snapshot_lock
        (so two saves can't drop each other's fields), and the overview is
        built after it is released (the lock isn't reentrant)."""
        held = []
        real_set = db.set_app_setting

        def spy(key, value):
            if key == abs_.SETTINGS_KEY:
                held.append(abs_._snapshot_lock.locked())
            return real_set(key, value)
        monkeypatch.setattr(db, "set_app_setting", spy)
        out = abs_.set_settings(enabled=True, frequency="weekly")
        assert held == [True] and not abs_._snapshot_lock.locked()
        assert out["enabled"] is True and out["frequency"] == "weekly"
        abs_.set_settings(include_media=True)
        assert abs_.get_settings() == {"enabled": True, "frequency": "weekly",
                                       "include_media": True, "folder": ""}


class _Crash(BaseException):
    """A process dying mid-way: not caught by the move's error handling."""


class TestMoveFileNeverLosesTheCopy:
    """_move_file across drives: copy to a temp name next to dest, flush,
    rename over dest, and only then remove the original."""

    def _setup(self, tmp_path, monkeypatch):
        src_dir, dest_dir = tmp_path / "a", tmp_path / "b"
        src_dir.mkdir()
        dest_dir.mkdir()
        src = src_dir / "baihe_snapshot-20260302-030000.zip"
        src.write_bytes(b"the only copy")
        real_replace = os.replace

        def cross_device(a, b, *args, **kw):
            if os.path.abspath(a) == str(src):
                raise OSError(18, "Invalid cross-device link")
            return real_replace(a, b, *args, **kw)
        monkeypatch.setattr(abs_.os, "replace", cross_device)
        return str(src), str(dest_dir / src.name), dest_dir

    def _copies(self, src, dest_dir):
        """Every file holding the copy's bytes, in either folder."""
        found = [src] if os.path.exists(src) and _read(src) == b"the only copy" else []
        return found + [str(p) for p in dest_dir.iterdir() if p.read_bytes() == b"the only copy"]

    def test_replace_into_dest_fails_then_copy_back_fails(self, tmp_path, monkeypatch):
        src, dest, dest_dir = self._setup(tmp_path, monkeypatch)
        real_copy = shutil.copyfile
        calls = []

        def copy_once(a, b, *args, **kw):
            calls.append(a)
            if len(calls) > 1:
                raise OSError("copy back failed")
            return real_copy(a, b, *args, **kw)
        monkeypatch.setattr(abs_.shutil, "copyfile", copy_once)
        monkeypatch.setattr(abs_.os, "replace",
                            lambda a, b, *x, **k: (_ for _ in ()).throw(OSError("replace")))
        with pytest.raises(OSError):
            abs_._move_file(src, dest)
        monkeypatch.undo()
        assert _read(src) == b"the only copy"
        assert list(dest_dir.iterdir()) == []       # no partial left behind

    @pytest.mark.parametrize("step", ["copy", "fsync", "replace", "remove_src"])
    def test_crash_at_any_step_leaves_the_original(self, tmp_path, monkeypatch, step):
        src, dest, dest_dir = self._setup(tmp_path, monkeypatch)
        real_replace, real_remove = abs_.os.replace, os.remove

        def crash(*a, **k):
            raise _Crash()
        if step == "copy":
            monkeypatch.setattr(abs_.shutil, "copyfile", crash)
        elif step == "fsync":
            monkeypatch.setattr(abs_, "_fsync_file", crash)
        elif step == "replace":
            def replace(a, b, *args, **kw):
                if os.path.basename(a).startswith(".baihe_snapshot.partial-"):
                    raise _Crash()
                return real_replace(a, b, *args, **kw)
            monkeypatch.setattr(abs_.os, "replace", replace)
        else:
            def remove(path, *args, **kw):
                if os.path.abspath(path) == src:
                    raise _Crash()
                return real_remove(path, *args, **kw)
            monkeypatch.setattr(abs_.os, "remove", remove)
        with pytest.raises(_Crash):
            abs_._move_file(src, dest)
        monkeypatch.undo()
        assert _read(src) == b"the only copy"
        assert self._copies(src, dest_dir)

    def test_original_that_cannot_be_removed_takes_dest_out_again(self, tmp_path,
                                                                  monkeypatch):
        src, dest, dest_dir = self._setup(tmp_path, monkeypatch)
        real_remove = os.remove

        def locked(path, *args, **kw):
            if os.path.abspath(path) == src:
                raise PermissionError("in use")
            return real_remove(path, *args, **kw)
        monkeypatch.setattr(abs_.os, "remove", locked)
        with pytest.raises(OSError):
            abs_._move_file(src, dest)
        monkeypatch.undo()
        assert _read(src) == b"the only copy"
        assert list(dest_dir.iterdir()) == []

    def test_dest_that_is_src_under_another_path_is_left_alone(self, tmp_path, monkeypatch):
        src, _dest, _dest_dir = self._setup(tmp_path, monkeypatch)
        alias = tmp_path / "alias"
        os.symlink(os.path.dirname(src), alias)
        abs_._move_file(src, str(alias / os.path.basename(src)))
        monkeypatch.undo()
        assert _read(src) == b"the only copy"
        assert os.listdir(os.path.dirname(src)) == [os.path.basename(src)]

    def test_directory_flush_failure_keeps_the_original(self, tmp_path, monkeypatch):
        """The original is removed only once the rename into dest is on
        disk; a failed flush raises, takes dest out again and keeps src."""
        src, dest, dest_dir = self._setup(tmp_path, monkeypatch)

        def fail(folder):
            raise OSError(5, "I/O error")
        monkeypatch.setattr(abs_, "_fsync_dir", fail)
        with pytest.raises(OSError):
            abs_._move_file(src, dest)
        monkeypatch.undo()
        assert _read(src) == b"the only copy"
        assert list(dest_dir.iterdir()) == []

    def test_success_moves_the_copy(self, tmp_path, monkeypatch):
        src, dest, dest_dir = self._setup(tmp_path, monkeypatch)
        abs_._move_file(src, dest)
        assert not os.path.exists(src)
        assert [p.name for p in dest_dir.iterdir()] == [os.path.basename(dest)]
        assert _read(dest) == b"the only copy"


# --------------------------------------------------------------------------
# startup sweep of partial snapshots / restore staging (lead's change 5)
# --------------------------------------------------------------------------

class TestCleanupStaleLeftovers:
    def _age(self, path, seconds):
        t = time.time() - seconds
        os.utime(path, (t, t), follow_symlinks=False)

    def test_removes_only_old_partials_and_staging(self, isolated_db, tmp_path):
        day = 24 * 3600
        bdir = _default_dir()
        os.makedirs(bdir)
        os.makedirs(db.DRAMAS_DIR, exist_ok=True)
        _snap()
        snap_bytes = _read(_default_path())
        self._age(_default_path(), 10 * day)

        def mkfile(folder, name, age):
            p = os.path.join(folder, name)
            with open(p, "wb") as fh:
                fh.write(b"x")
            self._age(p, age)
            return p

        def mkdir(folder, name, age):
            p = os.path.join(folder, name)
            os.makedirs(os.path.join(p, "sub"))
            self._age(p, age)
            return p
        old_partial = mkfile(bdir, ".baihe_snapshot.partial-abc.zip", day + 60)
        fresh_partial = mkfile(bdir, ".baihe_snapshot.partial-def.zip", 60)
        dir_partial = mkdir(bdir, ".baihe_snapshot.partial-dir.zip", 2 * day)
        other_file = mkfile(bdir, "notes.txt", 2 * day)
        old_staging = mkdir(db.DRAMAS_DIR, ".restoring-0123abcd", day + 60)
        fresh_staging = mkdir(db.DRAMAS_DIR, ".restoring-89abcdef", 60)
        file_staging = mkfile(db.DRAMAS_DIR, ".restoring-file", 2 * day)
        drama_folder = mkdir(db.DRAMAS_DIR, "3", 2 * day)
        target = tmp_path / "t"
        target.mkdir()
        (target / "keep").write_text("keep")
        link = os.path.join(db.DRAMAS_DIR, ".restoring-link")
        os.symlink(target, link)
        self._age(link, 2 * day)
        flink_target = tmp_path / "f.zip"
        flink_target.write_bytes(b"keep")
        flink = os.path.join(bdir, ".baihe_snapshot.partial-link.zip")
        os.symlink(flink_target, flink)
        self._age(flink, 2 * day)

        assert abs_.cleanup_stale_leftovers() == 2
        assert not os.path.exists(old_partial) and not os.path.exists(old_staging)
        for p in (fresh_partial, dir_partial, other_file, fresh_staging, file_staging,
                  drama_folder, link, flink):
            assert os.path.lexists(p), p
        assert (target / "keep").read_text() == "keep" and flink_target.read_bytes() == b"keep"
        assert _read(_default_path()) == snap_bytes

    def test_custom_folder_swept(self, isolated_db, tmp_path):
        abs_.set_settings(folder=str(tmp_path))
        p = tmp_path / ".baihe_snapshot.partial-x.zip"
        p.write_bytes(b"x")
        assert abs_.cleanup_stale_leftovers(now=time.time() + 2 * 24 * 3600) == 1
        assert not p.exists()

    def test_missing_folders_never_raise(self, isolated_db):
        if os.path.isdir(db.DRAMAS_DIR):
            os.rmdir(db.DRAMAS_DIR)
        assert abs_.cleanup_stale_leftovers() == 0


class TestTombstoneKeepsLiveDrama:
    def test_tombstone_of_existing_drama_kept(self, isolated_db):
        a = db.create_drama(title_en="A")
        os.makedirs(db.DRAMAS_DIR, exist_ok=True)
        tomb = os.path.join(db.DRAMAS_DIR, f"{a}.deleting-0123abcd")
        os.makedirs(tomb)
        t = time.time() - 10 * 24 * 3600
        os.utime(tomb, (t, t))
        assert drama_service.cleanup_stale_tombstones() == 0
        assert os.path.isdir(tomb)
        _delete_drama(a)
        assert drama_service.cleanup_stale_tombstones() == 1
        assert not os.path.exists(tomb)


# --------------------------------------------------------------------------
# reading the snapshot (lead's change 7)
# --------------------------------------------------------------------------

class TestSnapshotReadSafety:
    def _corrupt_manifest_stream(self):
        """A snapshot whose manifest.json deflate stream is garbage."""
        import struct
        a = db.create_drama(title_en="A")
        path = _snap()
        data = bytearray(_read(path))
        pos = 0
        while True:
            pos = data.find(b"PK\x03\x04", pos)
            assert pos >= 0
            n, e = struct.unpack_from("<HH", data, pos + 26)
            if bytes(data[pos + 30:pos + 30 + n]) == b"manifest.json":
                csize = struct.unpack_from("<I", data, pos + 18)[0]
                start = pos + 30 + n + e
                data[start:start + csize] = b"\xff" * csize
                break
            pos += 4
        with open(path, "wb") as fh:
            fh.write(data)
        return a

    def test_corrupt_manifest_stream_is_unreadable_not_crash(self, isolated_db):
        a = self._corrupt_manifest_stream()
        info = abs_.snapshot_info()
        assert info["exists"] and info["readable"] is False
        assert [c["readable"] for c in info["copies"]] == [False]
        with pytest.raises(InvalidInputError):
            abs_.list_snapshot_dramas()
        _delete_drama(a)
        before = _dump_all()
        with pytest.raises(InvalidInputError):
            _restore(a)
        assert _dump_all() == before

    def test_truncated_snapshot(self, isolated_db):
        db.create_drama(title_en="A")
        path = _snap()
        data = _read(path)
        with open(path, "wb") as fh:
            fh.write(data[: len(data) // 2])
        info = abs_.snapshot_info()
        assert info["exists"] and info["readable"] is False
        assert [c["readable"] for c in info["copies"]] == [False]

    @pytest.mark.parametrize("fn", ["snapshot_info", "list_snapshot_dramas"])
    def test_reads_wait_for_snapshot_lock(self, isolated_db, fn):
        db.create_drama(title_en="A")
        _snap()
        out = []
        with abs_._snapshot_lock:
            t = threading.Thread(target=lambda: out.append(getattr(abs_, fn)()))
            t.start()
            t.join(0.3)
            assert t.is_alive() and out == []
        t.join(10)
        assert not t.is_alive() and len(out) == 1


# --------------------------------------------------------------------------
# lead security review of PR #473 (L1-L3 and the NULL-profile check)
# --------------------------------------------------------------------------

class TestLeadReviewFollowUps:
    def test_stored_folder_inside_library_falls_back_to_default(self, isolated_db):
        """L1: a folder setting brought in by a whole-library restore is
        re-checked on every read."""
        inside = os.path.join(db.LIBRARY_DIR, "dramas")
        db.set_app_setting(abs_.SETTINGS_KEY, {**abs_.DEFAULT_SETTINGS, "folder": inside})
        assert abs_.get_settings()["folder"] == ""
        assert abs_._target_dir(create=False) == _default_dir()
        db.set_app_setting(abs_.SETTINGS_KEY, {**abs_.DEFAULT_SETTINGS, "folder": "relative"})
        assert abs_.get_settings()["folder"] == ""

    def test_stored_missing_folder_outside_library_is_kept(self, isolated_db, tmp_path):
        missing = str(tmp_path / "unplugged-drive")
        db.set_app_setting(abs_.SETTINGS_KEY, {**abs_.DEFAULT_SETTINGS, "folder": missing})
        assert abs_.get_settings()["folder"] == os.path.normpath(missing)

    def test_start_waits_for_the_snapshot_lock(self, isolated_db, monkeypatch):
        """L2: starting a backup and a folder change are serialized."""
        import threading as _t
        started = []
        monkeypatch.setattr(abs_.background_jobs, "start_job",
                            lambda *a, **k: started.append(1) or True)
        abs_._snapshot_lock.acquire()
        try:
            th = _t.Thread(target=abs_._start, args=(False,))
            th.start()
            th.join(0.3)
            assert th.is_alive() and not started
        finally:
            abs_._snapshot_lock.release()
        th.join(5)
        assert started == [1]

    def test_copy_fallback_cleans_up_when_original_cannot_be_removed(
            self, isolated_db, tmp_path, monkeypatch):
        db.create_drama(title_en="A")
        _snap()
        data = _read(_default_path())
        src = _default_path()
        real_replace, real_remove = os.replace, os.remove

        def cross_device(a, b, *args, **kw):
            if os.path.abspath(a) == os.path.abspath(src):
                raise OSError(18, "Invalid cross-device link")
            return real_replace(a, b, *args, **kw)

        def locked(path, *args, **kw):
            if os.path.abspath(path) == os.path.abspath(src):
                raise PermissionError("in use")
            return real_remove(path, *args, **kw)
        monkeypatch.setattr(abs_.os, "replace", cross_device)
        monkeypatch.setattr(abs_.os, "remove", locked)
        with pytest.raises(ServiceError):
            abs_.set_settings(folder=str(tmp_path))
        monkeypatch.undo()
        # The original is only removed once its copy is in place; when that
        # removal fails the copy is taken out again, so the copy is in
        # exactly one folder.
        assert os.listdir(tmp_path) == []
        assert _read(src) == data
        assert abs_.get_settings()["folder"] == ""

    def test_tombstone_sweep_survives_a_huge_numeric_name(self, isolated_db):
        """L3: an all-digit name too big for SQLite doesn't stop the sweep."""
        from services import drama_service
        os.makedirs(db.DRAMAS_DIR, exist_ok=True)
        old = time.time() - 3 * 24 * 3600
        paths = []
        for name in ("9" * 25 + ".deleting-0123abcd", "8.deleting-0123abcd"):
            path = os.path.join(db.DRAMAS_DIR, name)
            os.makedirs(path)
            os.utime(path, (old, old))
            paths.append(path)
        drama_service.cleanup_stale_tombstones()
        assert os.path.isdir(paths[0])          # skipped, not fatal
        assert not os.path.exists(paths[1])     # the sweep kept going

    def test_reading_history_with_null_profile_is_kept(self, isolated_db):
        did = db.create_drama(title_en="A")
        with contextlib.closing(_conn()) as c:
            c.execute("INSERT INTO reading_history (drama_id, profile_id, line_idx, "
                      "percent_complete, accessed_at) VALUES (?, NULL, 3, 10, 'x')", (did,))
            c.commit()
        _snap()
        _delete_drama(did)
        res = _restore(did)
        assert res["counts"]["reading_history"] == 1
        with contextlib.closing(_conn()) as c:
            row = c.execute("SELECT profile_id, line_idx FROM reading_history "
                            "WHERE drama_id = ?", (res["drama_id"],)).fetchone()
        assert tuple(row) == (None, 3)


class TestSeriesIdReuse:
    """Lead re-review: after a whole-library restore of an older library the
    auto snapshot survives, and a new series can reuse an id the snapshot
    used for a different series."""

    def _setup(self, private, other_owner):
        from services import auth_service
        owner = auth_service.add_user("owner@example.com")
        kid = auth_service.add_user("kid@example.com")
        with contextlib.closing(_conn()) as c:
            sid = _insert(c, "series", name="Old", owner_user_id=owner["id"], is_private=0)
            a = _insert(c, "dramas", title_en="D", series_id=sid, owner_user_id=kid["id"],
                        is_private=0, status="aligned")
            c.commit()
        _snap()
        _delete_drama(a)
        with contextlib.closing(_conn()) as c:   # the id now names an unrelated series
            c.execute("UPDATE series SET name = 'Unrelated', is_private = ?, owner_user_id = ? "
                      "WHERE id = ?", (int(private), owner["id"] if other_owner else kid["id"],
                                       sid))
            c.commit()
        return sid, a

    def test_same_id_other_name_is_recreated_not_linked(self, isolated_db):
        sid, a = self._setup(private=False, other_owner=True)
        before = _series_rows()
        res = _restore(a)
        new_sid = db.get_drama(a)["series_id"]
        assert res["series"] == "recreated" and new_sid not in (None, sid)
        assert _series_rows()[new_sid]["name"] == "Old"
        assert _series_rows()[sid] == before[sid]

    def test_same_id_other_name_private_is_still_dropped(self, isolated_db):
        sid, a = self._setup(private=True, other_owner=True)
        before = _series_rows()
        res = _restore(a)
        assert res["series"] == "dropped_private"
        assert db.get_drama(a)["series_id"] is None
        assert _series_rows() == before


class TestSharedBackupHelpers:
    """The directory flush and the restore free-space check are shared with
    db and library_admin_service; these pin how this module uses them."""

    @staticmethod
    def _snapshot(tmp_path):
        path = tmp_path / "snap.zip"
        with zipfile.ZipFile(path, "w") as zf:
            zf.writestr("dramas/7/a.txt", b"x" * 100)
        return zipfile.ZipFile(path)

    def test_stage_media_checks_the_dramas_folder_with_the_margin(self, isolated_db, tmp_path,
                                                                  monkeypatch):
        usage = collections.namedtuple("usage", "total used free")
        asked = []
        free = 100 + las.RESTORE_DISK_MARGIN_BYTES - 1
        monkeypatch.setattr(las.shutil, "disk_usage",
                            lambda p: (asked.append(p), usage(1, 1, free))[1])
        staging = tmp_path / "staging"
        staging.mkdir()
        with self._snapshot(tmp_path) as zf:
            with pytest.raises(InvalidInputError, match="disk space"):
                abs_.stage_media(zf, 7, str(staging))
            assert asked == [db.DRAMAS_DIR]
            assert list(staging.iterdir()) == []
            free += 1
            folder = abs_.stage_media(zf, 7, str(staging))
        assert _read_bytes(os.path.join(folder, "a.txt")) == b"x" * 100

    def test_stage_media_goes_ahead_when_free_space_is_unreadable(self, isolated_db, tmp_path,
                                                                 monkeypatch):
        def unreadable(path):
            raise OSError("not reported")
        monkeypatch.setattr(las.shutil, "disk_usage", unreadable)
        staging = tmp_path / "staging"
        staging.mkdir()
        with self._snapshot(tmp_path) as zf:
            folder = abs_.stage_media(zf, 7, str(staging))
        assert _read_bytes(os.path.join(folder, "a.txt")) == b"x" * 100

    def test_one_directory_flush_for_backups_and_the_media_journal(self, tmp_path, monkeypatch):
        assert abs_._fsync_dir is db.fsync_dir
        flushed = []
        monkeypatch.setattr(db, "fsync_dir", flushed.append)
        db.write_media_journal(str(tmp_path), {})
        assert flushed == [str(tmp_path)]

    @pytest.mark.skipif(os.name != "posix", reason="POSIX-only directory flush")
    def test_directory_flush_raises_for_a_missing_folder(self, tmp_path):
        db.fsync_dir(str(tmp_path))
        with pytest.raises(OSError):
            db.fsync_dir(str(tmp_path / "missing"))

    def test_directory_flush_is_skipped_off_posix(self, tmp_path, monkeypatch):
        monkeypatch.setattr(db.os, "name", "nt")
        db.fsync_dir(str(tmp_path / "missing"))     # no OSError: nothing is opened


def _read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()
