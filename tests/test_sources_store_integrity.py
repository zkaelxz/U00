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
    def test_a_damaged_sources_db_never_replaces_the_good_copies(self, isolated_db):
        import db
        from services import auto_backup_copies as abc_
        from services import auto_backup_service as abs_
        from services import auto_backup_settings as abt_
        db.create_drama(title_en="A")
        store.set_setting("x", 1)
        abs_._backup_job(abs_.JOB_ID, False)
        folder = abt_._target_dir(create=False)
        good = [c["path"] for c in abc_._list_copies(folder)]
        good_bytes = _read(good[0])
        damaged = _damage(store.db_path())
        with pytest.raises(RuntimeError):
            abs_._backup_job(abs_.JOB_ID, False)
        assert [c["path"] for c in abc_._list_copies(folder)] == good
        assert _read(good[0]) == good_bytes
        assert sorted(os.listdir(folder)) == sorted(os.path.basename(p) for p in good)
        assert abt_._get_state()["last_error"] == abs_._FAILED
        assert _read(store.db_path()) == damaged

    def test_a_sound_sources_db_is_in_the_copy(self, isolated_db):
        import zipfile
        import db
        from services import auto_backup_copies as abc_
        from services import auto_backup_service as abs_
        from services import auto_backup_settings as abt_
        db.create_drama(title_en="A")
        store.set_setting("x", 1)
        abs_._backup_job(abs_.JOB_ID, False)
        path = abc_._list_copies(abt_._target_dir(create=False))[0]["path"]
        with zipfile.ZipFile(path) as zf:
            assert "sources.db" in zf.namelist()
