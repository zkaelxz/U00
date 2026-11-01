"""sources.db integrity: a damaged file is reported at startup and kept out
of the automatic backup rotation, and nothing here deletes or repairs it."""

import os
import sqlite3

import pytest

from lib import sqlite_check
from sources import store


def _damage(path):
    """A sources.db SQLite still opens (and the backup API still copies)
    but whose quick_check fails: one page of a filled table overwritten."""
    store.set_setting("x", 1)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE filler (id INTEGER PRIMARY KEY, v TEXT)")
    conn.execute("CREATE INDEX filler_v ON filler (v)")
    conn.executemany("INSERT INTO filler (v) VALUES (?)",
                     [("x" * 50 + str(i),) for i in range(3000)])
    conn.commit()
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    page = conn.execute("PRAGMA page_size").fetchone()[0]
    pages = conn.execute("PRAGMA page_count").fetchone()[0]
    conn.close()
    with open(path, "r+b") as fh:
        fh.seek(page * (pages - 4) + 100)
        fh.write(b"\xff" * 200)
    with open(path, "rb") as fh:
        return fh.read()


def _read(path):
    with open(path, "rb") as fh:
        return fh.read()


class TestIntegrityProblem:
    def test_a_sound_file_has_none(self, isolated_db):
        store.set_setting("x", 1)
        assert store.integrity_problem() is None

    def test_a_missing_file_has_none_and_is_not_created(self, isolated_db):
        assert not os.path.exists(store.db_path())
        assert store.integrity_problem() is None
        assert not os.path.exists(store.db_path())

    def test_a_damaged_page_is_reported_and_the_file_left_as_is(self, isolated_db):
        before = _damage(store.db_path())
        assert store.integrity_problem() == store.CORRUPT_MESSAGE
        assert _read(store.db_path()) == before

    def test_a_file_that_is_not_a_database_is_reported(self, isolated_db):
        os.makedirs(os.path.dirname(store.db_path()), exist_ok=True)
        with open(store.db_path(), "wb") as fh:
            fh.write(b"not a database at all " * 200)
        assert store.integrity_problem() == store.CORRUPT_MESSAGE
        assert _read(store.db_path()) == b"not a database at all " * 200

    def test_a_locked_file_is_not_called_damaged(self, isolated_db, monkeypatch):
        store.set_setting("x", 1)

        def locked(*a, **k):
            raise sqlite3.OperationalError("database is locked")
        monkeypatch.setattr(sqlite_check.sqlite3, "connect", locked)
        assert store.integrity_problem() is None

    def test_the_message_names_no_path(self, isolated_db):
        _damage(store.db_path())
        assert isolated_db.LIBRARY_DIR not in store.integrity_problem()


class TestStartupWarning:
    def _serve(self, monkeypatch):
        import uvicorn
        from api import __main__ as main_mod
        from services import settings_service
        monkeypatch.setattr(settings_service, "read_env_file", lambda *a, **k: {})
        for k in ("BAIHE_API_HOUSEHOLD_PORT", "BAIHE_API_PORT", "BAIHE_API_HOST",
                  "BAIHE_API_AUTH", "BAIHE_PUBLIC_URL"):
            monkeypatch.delenv(k, raising=False)
        monkeypatch.setenv("BAIHE_API_ENV", "development")
        started = []
        monkeypatch.setattr(uvicorn, "run", lambda *a, **k: started.append(1))
        main_mod._serve()
        return started

    def test_a_damaged_sources_db_is_reported_and_the_server_still_starts(
            self, isolated_db, monkeypatch, capsys):
        before = _damage(store.db_path())
        assert self._serve(monkeypatch) == [1]
        assert f"WARNING: {store.CORRUPT_MESSAGE}" in capsys.readouterr().err
        assert _read(store.db_path()) == before

    def test_a_sound_sources_db_prints_nothing(self, isolated_db, monkeypatch, capsys):
        store.set_setting("x", 1)
        assert self._serve(monkeypatch) == [1]
        assert store.CORRUPT_MESSAGE not in capsys.readouterr().err


class TestAutomaticBackup:
    def _good_copy(self):
        import db
        from services import auto_backup_copies as abc_
        from services import auto_backup_service as abs_
        from services import auto_backup_settings as abt_
        db.create_drama(title_en="A")
        store.set_setting("x", 1)
        abs_._backup_job(abs_.JOB_ID, False)
        folder = abt_._target_dir(create=False)
        return folder, abc_._list_copies(folder)[0]["path"]

    def test_a_damaged_sources_db_is_left_out_and_library_db_still_backed_up(
            self, isolated_db, monkeypatch):
        import zipfile
        import background_jobs
        from services import auto_backup_copies as abc_
        from services import auto_backup_service as abs_
        from services import auto_backup_settings as abt_
        folder, good = self._good_copy()
        good_bytes = _read(good)
        damaged = _damage(store.db_path())
        isolated_db.create_drama(title_en="B")
        pruned, messages, results = [], [], []
        monkeypatch.setattr(abs_, "_prune", lambda *a: pruned.append(a))
        monkeypatch.setattr(background_jobs, "update_progress",
                            lambda job_id, frac, msg: messages.append(msg))
        monkeypatch.setattr(background_jobs, "set_result",
                            lambda job_id, result: results.append(result))
        abs_._backup_job(abs_.JOB_ID, False)
        copies = [c["path"] for c in abc_._list_copies(folder)]
        assert len(copies) == 2 and good in copies
        new = next(p for p in copies if p != good)
        with zipfile.ZipFile(new) as zf:
            assert "library.db" in zf.namelist() and "sources.db" not in zf.namelist()
        with zipfile.ZipFile(good) as zf:
            assert "sources.db" in zf.namelist()
        assert _read(good) == good_bytes
        assert abc_._read_copy(new)[1]["sources_left_out"] is True
        assert "sources_left_out" not in abc_._read_copy(good)[1]
        assert abc_._identity_info()["sources_keep"] == [os.path.basename(good)]
        assert len(pruned) == 1   # the rotation still runs; the marked copy is spared
        assert results[-1]["sources_left_out"] is True
        assert messages[-1].endswith(abs_.SOURCES_LEFT_OUT)
        state = abt_._get_state()
        assert state["last_error"] is None and state["last_sources_left_out"] is True
        assert abs_.settings_overview()["last_note"] == abs_.SOURCES_LEFT_OUT
        info = abc_.snapshot_info()
        assert [(c["sources_left_out"], c["kept_as"]) for c in info["copies"]] == [
            (True, "daily"), (False, "weekly")]
        assert info["sources_left_out"] is True
        assert _read(store.db_path()) == damaged

    def test_a_damaged_header_still_backs_up_library_db(self, isolated_db):
        import zipfile
        from services import auto_backup_copies as abc_
        from services import auto_backup_service as abs_
        from services import auto_backup_settings as abt_
        isolated_db.create_drama(title_en="A")
        store.set_setting("x", 1)
        # Frames still in the -wal would be written into the zeroed file by any
        # connection another test left open when it closes, which is not the backup's doing.
        conn = sqlite3.connect(store.db_path())
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        with open(store.db_path(), "r+b") as fh:
            fh.write(b"\x00" * 100)    # the SQLite header: "file is not a database"
        damaged = _read(store.db_path())
        abs_._backup_job(abs_.JOB_ID, False)
        [copy] = abc_._list_copies(abt_._target_dir(create=False))
        with zipfile.ZipFile(copy["path"]) as zf:
            assert "library.db" in zf.namelist() and "sources.db" not in zf.namelist()
        assert abt_._get_state()["last_sources_left_out"] is True
        assert _read(store.db_path()) == damaged

    def test_a_busy_sources_db_still_fails_the_run(self, isolated_db, monkeypatch):
        from services import auto_backup_copies as abc_
        from services import auto_backup_service as abs_
        from services import auto_backup_settings as abt_
        isolated_db.create_drama(title_en="A")
        store.set_setting("x", 1)

        def busy(dest):
            raise OSError("sources snapshot could not be prepared")
        monkeypatch.setattr(store, "backup_snapshot", busy)
        with pytest.raises(RuntimeError):
            abs_._backup_job(abs_.JOB_ID, False)
        assert abt_._get_state()["last_error"] == abs_._FAILED
        assert abc_._list_copies(abt_._target_dir(create=False)) == []

    def test_the_last_copy_with_sources_db_outlives_a_long_spell_and_the_fix(
            self, isolated_db, monkeypatch):
        import datetime
        import zipfile
        from services import auto_backup_copies as abc_
        from services import auto_backup_files as abf_
        from services import auto_backup_service as abs_
        from services import auto_backup_settings as abt_

        def run(day):
            when = datetime.datetime(2026, 3, 1, 3, tzinfo=datetime.timezone.utc) \
                + datetime.timedelta(days=day)
            for mod in (abs_, abt_, abc_, abf_):
                monkeypatch.setattr(mod, "_now", lambda: when)
            abs_._backup_job(abs_.JOB_ID, False)
            return f"baihe_snapshot-{when:%Y%m%d-%H%M%S}.zip"

        def names():
            return {c["name"] for c in abc_._list_copies(abt_._target_dir(create=False))}

        isolated_db.create_drama(title_en="A")
        store.set_setting("followed", "series")
        run(0)
        last_good = run(1)
        _damage(store.db_path())
        for day in range(2, 24):           # three weeks of damaged runs
            run(day)
        assert last_good in names() and len(names()) <= 5
        # The owner moves the damaged file aside, as the warning says; the
        # app starts a new, empty one and the runs see a sound sources.db.
        os.replace(store.db_path(), store.db_path() + ".damaged")
        store._initialised.clear()
        store.set_setting("x", 1)
        for day in range(24, 45):
            run(day)
        assert last_good in names() and len(names()) <= 5
        kept = {c["name"]: c["kept_as"] for c in abc_.snapshot_info()["copies"]}
        assert kept[last_good] == "sources"
        with zipfile.ZipFile(os.path.join(abt_._target_dir(create=False), last_good)) as zf:
            assert "sources.db" in zf.namelist()
        # Deleting it by name is the owner's call; the mark goes with it.
        abs_.delete_snapshot(confirm=True, confirm_text="DELETE", snapshot=last_good)
        run(45)
        assert last_good not in names()
        assert abc_._identity_info()["sources_keep"] == []

    def test_without_the_mark_a_long_spell_would_lose_it(self, isolated_db, monkeypatch):
        """The rotation on its own: what the mark prevents."""
        import datetime
        from services import auto_backup_copies as abc_
        from services import auto_backup_files as abf_
        from services import auto_backup_service as abs_
        from services import auto_backup_settings as abt_
        monkeypatch.setattr(abs_, "_keep_last_sources_copy", lambda *a: None)

        def run(day):
            when = datetime.datetime(2026, 3, 1, 3, tzinfo=datetime.timezone.utc) \
                + datetime.timedelta(days=day)
            for mod in (abs_, abt_, abc_, abf_):
                monkeypatch.setattr(mod, "_now", lambda: when)
            abs_._backup_job(abs_.JOB_ID, False)
            return f"baihe_snapshot-{when:%Y%m%d-%H%M%S}.zip"

        isolated_db.create_drama(title_en="A")
        store.set_setting("x", 1)
        last_good = run(0)
        _damage(store.db_path())
        for day in range(1, 24):
            run(day)
        names = {c["name"] for c in abc_._list_copies(abt_._target_dir(create=False))}
        assert last_good not in names

    def test_a_sound_sources_db_is_in_the_copy_and_rotation_runs(self, isolated_db, monkeypatch):
        import zipfile
        import background_jobs
        from services import auto_backup_service as abs_
        from services import auto_backup_settings as abt_
        pruned, results = [], []
        monkeypatch.setattr(abs_, "_prune", lambda *a: pruned.append(a))
        monkeypatch.setattr(background_jobs, "set_result",
                            lambda job_id, result: results.append(result))
        _folder, path = self._good_copy()
        with zipfile.ZipFile(path) as zf:
            assert "sources.db" in zf.namelist()
        assert len(pruned) == 1
        assert results[-1]["sources_left_out"] is False
        assert abt_._get_state()["last_sources_left_out"] is False

    def test_a_manual_backup_still_holds_the_file_as_it_was(self, isolated_db, tmp_path):
        import zipfile
        from services import library_backup_service as lbs
        _damage(store.db_path())
        dest = str(tmp_path / "b.zip")
        lbs.write_backup_zip(dest, include_media=False)
        with zipfile.ZipFile(dest) as zf:
            assert "sources.db" in zf.namelist()

    def test_a_copy_holding_a_damaged_sources_db_fails_verification(self, isolated_db, tmp_path):
        from services import auto_backup_files as abf_
        from services import library_backup_service as lbs
        from lib.errors import InvalidInputError
        def write(name):
            dest = str(tmp_path / name)
            lbs.write_backup_zip(dest, include_media=False,
                                 manifest=lambda snap: abf_._manifest_bytes(snap, False,
                                                                            ("lib", 1)))
            return dest
        store.set_setting("x", 1)
        abf_._verify_snapshot(write("sound.zip"))
        _damage(store.db_path())
        with pytest.raises(InvalidInputError):
            abf_._verify_snapshot(write("damaged.zip"))
