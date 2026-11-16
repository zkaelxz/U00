"""Tests for the library.db file as a whole: the version stamp check, snapshots, full reset and recovery from an interrupted restore."""

import contextlib
import sqlite3
import sys
import os

import pytest
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import db
from core import Line


class TestFullLibraryReset:
    """The Diagnostics 'Reset everything' button, used to wipe test data
    and start over. Real deletion, so this is checked directly against a
    populated library rather than trusted from a smaller unit test."""

    def test_wipes_dramas_series_and_files(self, isolated_db):
        did = isolated_db.create_drama(title_en="X")
        ddir = isolated_db.drama_dir(did)
        with open(os.path.join(ddir, "source.mp3"), "wb") as f:
            f.write(b"audio")
        sid = isolated_db.get_or_create_series("S")
        isolated_db.upsert_glossary_term(sid, "a", "b")

        isolated_db.reset_library()

        assert isolated_db.list_dramas() == []
        assert isolated_db.list_series() == []
        assert not os.path.exists(ddir)

    def test_reset_closes_a_connection_this_thread_left_open(self, isolated_db):
        leftover = isolated_db.get_conn()
        isolated_db.reset_library()
        try:
            leftover.execute("SELECT 1")
        except sqlite3.ProgrammingError:
            return
        raise AssertionError("the leftover connection was still open")

    def test_schema_is_immediately_usable_after_reset(self, isolated_db):
        isolated_db.reset_library()
        did = isolated_db.create_drama(title_en="Fresh")
        assert isolated_db.get_drama(did)["title_en"] == "Fresh"

    def test_dramas_directory_exists_but_empty_after_reset(self, isolated_db):
        isolated_db.create_drama(title_en="X")
        isolated_db.reset_library()
        assert os.path.isdir(isolated_db.DRAMAS_DIR)
        assert isolated_db.list_dramas() == []

    def test_reset_on_an_already_empty_library_does_not_crash(self, isolated_db):
        isolated_db.reset_library()  # nothing to wipe
        assert isolated_db.list_dramas() == []

    def test_wipes_the_cached_cedict_dictionary_too(self, isolated_db):
        cedict_path = os.path.join(isolated_db.LIBRARY_DIR, "cedict.txt")
        with open(cedict_path, "w", encoding="utf-8") as f:
            f.write("fake cedict data")

        isolated_db.reset_library()

        assert not os.path.exists(cedict_path)

    def test_reset_without_a_cedict_file_present_does_not_crash(self, isolated_db):
        cedict_path = os.path.join(isolated_db.LIBRARY_DIR, "cedict.txt")
        assert not os.path.exists(cedict_path)
        isolated_db.reset_library()  # must not raise just because there's nothing to remove


class TestSnapshotDatabase:
    """Regression coverage for a real gap: the backup feature used to
    copy library.db as a plain file while the database runs in WAL mode
    (see get_conn) -- a write still sitting in library.db-wal could be
    missing from that copy, or the copy could be mid-write. snapshot_database
    uses SQLite's own backup API instead, which is what actually guarantees
    a consistent, complete copy regardless of the WAL file's state."""

    def test_snapshot_is_written_to_disk(self, isolated_db, tmp_path_str):
        isolated_db.create_drama(title_en="Test Drama")
        dest = os.path.join(tmp_path_str, "snapshot.db")

        isolated_db.snapshot_database(dest)

        assert os.path.exists(dest)
        assert os.path.getsize(dest) > 0

    def test_snapshot_includes_a_write_made_just_before_it(self, isolated_db, tmp_path_str):
        did = isolated_db.create_drama(title_en="Before Snapshot")
        dest = os.path.join(tmp_path_str, "snapshot.db")

        isolated_db.snapshot_database(dest)

        import sqlite3
        conn = sqlite3.connect(dest)
        row = conn.execute("SELECT title_en FROM dramas WHERE id = ?", (did,)).fetchone()
        conn.close()
        assert row is not None
        assert row[0] == "Before Snapshot"

    def test_snapshot_is_independent_of_the_live_database(self, isolated_db, tmp_path_str):
        """The snapshot is a standalone file -- writing to the live
        database afterward must not change it."""
        did = isolated_db.create_drama(title_en="Original")
        dest = os.path.join(tmp_path_str, "snapshot.db")
        isolated_db.snapshot_database(dest)

        isolated_db.update_drama(did, title_en="Changed After Snapshot")

        import sqlite3
        conn = sqlite3.connect(dest)
        row = conn.execute("SELECT title_en FROM dramas WHERE id = ?", (did,)).fetchone()
        conn.close()
        assert row[0] == "Original"


def test_snapshot_waits_for_a_concurrent_writer_instead_of_failing(isolated_db):
    """The snapshot reads the lines' words before it writes; a deferred BEGIN
    would fail at once with "database is locked" when another connection
    commits in between."""
    did = db.create_drama(title_zh="D")
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 0.5, zh="甲") for i in range(50)])
    lines = db.load_line_objects(did)
    stop, errors = threading.Event(), []

    def writer():
        while not stop.is_set():
            db.set_app_setting("busy", 1)
    t = threading.Thread(target=writer)
    t.start()
    try:
        for _ in range(150):
            try:
                db.save_line_history_snapshot(did, lines, "t")
            except sqlite3.OperationalError as e:
                errors.append(e)
    finally:
        stop.set()
        t.join()
    assert errors == []


def _stored_version():
    return db.get_app_setting(db.APP_VERSION_SETTING)


def _open_as(monkeypatch, running):
    monkeypatch.setattr(db, "_running_app_version", lambda: running)
    db.init_db()


def test_init_db_refuses_a_library_last_opened_by_a_newer_version(isolated_db, monkeypatch):
    from lib.errors import NewerDatabaseError
    db.set_app_setting(db.APP_VERSION_SETTING, "2.1.0")
    with pytest.raises(NewerDatabaseError) as err:
        _open_as(monkeypatch, "2.0.5")
    message = err.value.message
    assert "2.1.0" in message and "2.0.5" in message and "backups" in message
    assert db.LIBRARY_DIR not in message and os.sep not in message
    assert _stored_version() == "2.1.0"


def test_a_refused_library_stays_refused_on_the_next_use(isolated_db, monkeypatch):
    from lib.errors import NewerDatabaseError
    db.set_app_setting(db.APP_VERSION_SETTING, "2.1.0")
    monkeypatch.setattr(db, "_running_app_version", lambda: "2.0.5")
    db.configure_library_dir(db.LIBRARY_DIR)   # forget readiness, as a fresh start
    with pytest.raises(NewerDatabaseError):
        db.get_conn()
    with pytest.raises(NewerDatabaseError):
        db.get_conn()


def test_a_second_thread_waits_for_the_first_use_to_finish_migrating(isolated_db, monkeypatch):
    # Without the wait, the second thread gets a connection while the first is
    # still migrating, and can read a table or column that isn't there yet.
    real_init_db = db.init_db
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow_init_db():
        calls.append(threading.current_thread().name)
        started.set()
        assert release.wait(5)
        real_init_db()

    monkeypatch.setattr(db, "init_db", slow_init_db)
    db.configure_library_dir(db.LIBRARY_DIR)   # forget readiness, as a fresh start
    first = threading.Thread(target=lambda: db.get_conn().close(), name="first")
    first.start()
    assert started.wait(5)
    second_done = threading.Event()
    second = threading.Thread(target=lambda: (db.get_conn().close(), second_done.set()),
                              name="second")
    second.start()
    try:
        assert not second_done.wait(0.3)
    finally:
        release.set()
        first.join(5)
        second.join(5)
    assert second_done.is_set()
    assert calls == ["first"]


def test_switching_the_library_mid_migration_still_migrates_the_new_one(
        isolated_db, monkeypatch, tmp_path):
    # Without the lock, the old path's migration finishing after the switch
    # would mark the new, unmigrated library ready.
    real_init_db = db.init_db
    started, release = threading.Event(), threading.Event()
    calls = []

    def slow_init_db():
        calls.append(db.DB_PATH)
        if len(calls) == 1:
            started.set()
            assert release.wait(5)
        real_init_db()

    monkeypatch.setattr(db, "init_db", slow_init_db)
    old = db.LIBRARY_DIR
    db.configure_library_dir(old)
    first = threading.Thread(target=lambda: db.get_conn().close())
    first.start()
    assert started.wait(5)
    new = str(tmp_path / "other_library")
    switched = threading.Event()
    switch = threading.Thread(target=lambda: (db.configure_library_dir(new), switched.set()))
    switch.start()
    try:
        assert not switched.wait(0.3)
    finally:
        release.set()
        first.join(5)
        switch.join(5)
    assert switched.is_set()
    try:
        db.get_conn().close()
        assert calls == [os.path.join(old, "library.db"), os.path.join(new, "library.db")]
    finally:
        db.configure_library_dir(old)


def test_a_failed_first_use_lets_the_next_one_retry(isolated_db, monkeypatch):
    real_init_db = db.init_db
    attempts = []

    def failing_once():
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("disk I/O error")
        real_init_db()

    monkeypatch.setattr(db, "init_db", failing_once)
    db.configure_library_dir(db.LIBRARY_DIR)
    with pytest.raises(sqlite3.OperationalError):
        db.get_conn()
    db.get_conn().close()
    assert len(attempts) == 2


@pytest.mark.parametrize("stored", ["1.9.0", "2.0.5"])
def test_init_db_opens_an_older_or_equal_version_and_records_the_running_one(
        isolated_db, monkeypatch, stored):
    db.set_app_setting(db.APP_VERSION_SETTING, stored)
    _open_as(monkeypatch, "2.0.5")
    assert _stored_version() == "2.0.5"


def test_init_db_opens_when_no_version_was_recorded(isolated_db, monkeypatch):
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("DELETE FROM app_settings WHERE key = ?", (db.APP_VERSION_SETTING,))
        conn.commit()
    _open_as(monkeypatch, "2.0.5")
    assert _stored_version() == "2.0.5"


@pytest.mark.parametrize("stored", ["dev", "banana", "", 7, None])
def test_init_db_opens_when_the_stored_version_is_malformed(isolated_db, monkeypatch, stored):
    db.set_app_setting(db.APP_VERSION_SETTING, stored)
    _open_as(monkeypatch, "2.0.5")
    assert _stored_version() == "2.0.5"


def test_a_source_checkout_opens_a_newer_library_and_keeps_its_stamp(isolated_db, monkeypatch):
    db.set_app_setting(db.APP_VERSION_SETTING, "9.9.9")
    _open_as(monkeypatch, None)
    assert _stored_version() == "9.9.9"


@pytest.mark.parametrize("stored", [None, "0.0.1", "dev"])
def test_a_source_checkout_stamps_its_library_with_the_release_it_works_towards(
        isolated_db, monkeypatch, stored):
    import portable
    if stored is None:
        with contextlib.closing(db.get_conn()) as conn:
            conn.execute("DELETE FROM app_settings WHERE key = ?", (db.APP_VERSION_SETTING,))
            conn.commit()
    else:
        db.set_app_setting(db.APP_VERSION_SETTING, stored)
    _open_as(monkeypatch, None)
    assert _stored_version() == portable.SOURCE_VERSION


def test_a_checkout_library_is_refused_by_the_release_before_and_opened_by_its_own(
        isolated_db, monkeypatch):
    import portable
    from lib.errors import NewerDatabaseError
    monkeypatch.setattr(portable, "SOURCE_VERSION", "2.1.0-dev")
    _open_as(monkeypatch, None)
    with pytest.raises(NewerDatabaseError):
        _open_as(monkeypatch, "2.0.9")
    _open_as(monkeypatch, "2.1.0")
    assert _stored_version() == "2.1.0"
    _open_as(monkeypatch, None)   # the release's stamp is not lowered back to -dev
    assert _stored_version() == "2.1.0"


def test_source_version_is_not_behind_any_release_note():
    # A release note for a newer version with SOURCE_VERSION left behind means
    # its bump after the last tag was forgotten, and that release would open
    # libraries made by checkouts newer than it.
    import glob
    import portable
    notes = glob.glob(os.path.join(os.path.dirname(portable.__file__), "release", "v*-notes.md"))
    versions = [db._version_tuple(os.path.basename(p)[1:-len("-notes.md")]) for p in notes]
    assert versions and all(versions)
    assert db._version_tuple(portable.SOURCE_VERSION) >= max(versions)


def test_installed_app_version_comes_from_the_install_manifest(tmp_path, monkeypatch):
    import portable
    app = tmp_path / "app"
    app.mkdir()
    monkeypatch.setattr(portable, "APP_DIR", str(app))
    monkeypatch.setattr(portable, "is_installed", lambda: True)
    manifest = tmp_path / "manifest.json"
    manifest.write_text('{"product": "Baihe Studio", "app_version": "0.1.0"}')
    assert portable.installed_app_version() == "0.1.0"
    manifest.write_text('{"product": "Other", "app_version": "0.1.0"}')
    assert portable.installed_app_version() is None
    manifest.write_text("not json")
    assert portable.installed_app_version() is None
    monkeypatch.setattr(portable, "is_installed", lambda: False)
    assert portable.installed_app_version() is None


@pytest.mark.parametrize("damage", ["truncated", "not_sqlite"])
def test_the_server_refuses_a_damaged_library_db_with_a_plain_message(
        tmp_path, monkeypatch, damage):
    import api.__main__ as entry
    library = tmp_path / "library"
    library.mkdir()
    good = tmp_path / "good.db"
    conn = sqlite3.connect(good)
    conn.execute("CREATE TABLE t (x)")
    conn.executemany("INSERT INTO t VALUES (?)", [("x" * 500,)] * 200)
    conn.commit()
    conn.close()
    raw = good.read_bytes()
    damaged = raw[:len(raw) // 3] if damage == "truncated" else b"not a database" * 100
    (library / "library.db").write_bytes(damaged)
    previous = db.LIBRARY_DIR
    db.configure_library_dir(str(library))
    try:
        with pytest.raises(SystemExit) as err:
            entry._serve()
    finally:
        db.configure_library_dir(previous)
    assert str(err.value) == f"ERROR: {db.DAMAGED_LIBRARY_MESSAGE}"
    assert (library / "library.db").read_bytes() == damaged


class TestInterruptedRestoreRecovery:
    """A restore renames the library aside before putting the new one in
    place; a crash between the two renames must not start on an empty one."""

    @staticmethod
    def _aside(parent, name, marker=b"x"):
        path = parent / name
        path.mkdir()
        (path / "library.db").write_bytes(marker)
        return path

    def test_the_one_library_set_aside_is_put_back(self, tmp_path):
        self._aside(tmp_path, "library.pre_restore_100", b"old")
        assert db.recover_interrupted_restore(str(tmp_path / "library")) == "library.pre_restore_100"
        assert (tmp_path / "library" / "library.db").read_bytes() == b"old"
        assert sorted(p.name for p in tmp_path.iterdir()
                      if not p.name.endswith(".restore-lock")) == ["library"]

    def test_a_look_alike_folder_is_not_taken_for_the_library(self, tmp_path):
        self._aside(tmp_path, "library.pre_restore_copy")
        assert db.recover_interrupted_restore(str(tmp_path / "library")) is None
        assert db.library_startup_notes(str(tmp_path / "library")) == []
        assert not (tmp_path / "library").exists()
        assert (tmp_path / "library.pre_restore_copy" / "library.db").exists()

    def test_a_look_alike_folder_does_not_count_toward_several(self, tmp_path):
        self._aside(tmp_path, "library.pre_restore_100", b"old")
        self._aside(tmp_path, "library.pre_restore_100 (2)")
        assert db.recover_interrupted_restore(str(tmp_path / "library")) == "library.pre_restore_100"
        assert (tmp_path / "library" / "library.db").read_bytes() == b"old"

    def test_a_present_library_is_left_alone(self, tmp_path):
        (tmp_path / "library").mkdir()
        (tmp_path / "library" / "library.db").write_bytes(b"current")
        self._aside(tmp_path, "library.pre_restore_100")
        assert db.recover_interrupted_restore(str(tmp_path / "library")) is None
        assert (tmp_path / "library.pre_restore_100").is_dir()

    def test_several_set_aside_refuse_to_start(self, tmp_path):
        from lib.errors import UnfinishedRestoreError
        self._aside(tmp_path, "library.pre_restore_100")
        self._aside(tmp_path, "library.pre_restore_200")
        with pytest.raises(UnfinishedRestoreError) as err:
            db.recover_interrupted_restore(str(tmp_path / "library"))
        assert "library.pre_restore_100" in err.value.message
        assert str(tmp_path) not in err.value.message
        assert not (tmp_path / "library").exists()

    def test_the_first_database_use_recovers_it(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="Kept")
        os.rename(isolated_db.LIBRARY_DIR, isolated_db.LIBRARY_DIR + ".pre_restore_100")
        monkeypatch.setattr(isolated_db, "_db_ready", False)
        assert isolated_db.get_drama(did)["title_en"] == "Kept"

    def test_the_server_refuses_to_start_with_a_plain_message(self, tmp_path, monkeypatch):
        import api.__main__ as entry
        self._aside(tmp_path, "library.pre_restore_100")
        self._aside(tmp_path, "library.pre_restore_200")
        monkeypatch.setattr(db, "LIBRARY_DIR", str(tmp_path / "library"))
        with pytest.raises(SystemExit) as err:
            entry._serve()
        assert "library.pre_restore_100" in str(err.value)
        # Nothing was written where the library belongs (no log folder):
        # that would make the next start take it for a library.
        assert not (tmp_path / "library").exists()

    def test_a_folder_holding_only_logs_counts_as_missing(self, tmp_path):
        (tmp_path / "library" / "logs").mkdir(parents=True)
        (tmp_path / "library" / "logs" / "app.log").write_text("start failed")
        (tmp_path / "library" / "dramas").mkdir()
        self._aside(tmp_path, "library.pre_restore_100", b"old")
        assert db.recover_interrupted_restore(str(tmp_path / "library")) == "library.pre_restore_100"
        assert (tmp_path / "library" / "library.db").read_bytes() == b"old"
        assert sorted(p.name for p in tmp_path.iterdir()
                      if not p.name.endswith(".restore-lock")) == ["library"]

    def test_a_folder_with_content_is_never_replaced(self, tmp_path):
        (tmp_path / "library" / "dramas" / "1").mkdir(parents=True)
        (tmp_path / "library" / "dramas" / "1" / "audio.wav").write_bytes(b"a")
        self._aside(tmp_path, "library.pre_restore_100")
        assert db.recover_interrupted_restore(str(tmp_path / "library")) is None
        assert (tmp_path / "library" / "dramas" / "1" / "audio.wav").exists()

    def test_recovery_then_a_second_start_does_nothing(self, tmp_path):
        self._aside(tmp_path, "library.pre_restore_100", b"old")
        notes = db.library_startup_notes(str(tmp_path / "library"))
        assert any("library.pre_restore_100" in n for n in notes)
        assert db.library_startup_notes(str(tmp_path / "library")) == []
        assert (tmp_path / "library" / "library.db").read_bytes() == b"old"

    def test_a_folder_left_beside_a_present_library_is_named(self, tmp_path):
        (tmp_path / "library").mkdir()
        (tmp_path / "library" / "library.db").write_bytes(b"new")
        (self._aside(tmp_path, "library.pre_restore_100") / "backups").mkdir()
        [note] = db.library_startup_notes(str(tmp_path / "library"))
        assert "library.pre_restore_100" in note and "backups" in note
        assert "media" in note and "previous library" in note and "check" in note
        assert str(tmp_path) not in note

    def test_a_restore_in_progress_is_left_alone(self, tmp_path):
        self._aside(tmp_path, "library.pre_restore_100", b"old")
        holder = db.hold_restore_lock(str(tmp_path / "library"))
        try:
            assert db.recover_interrupted_restore(str(tmp_path / "library")) is None
            assert not (tmp_path / "library").exists()
        finally:
            db.release_restore_lock(holder)
        assert db.recover_interrupted_restore(str(tmp_path / "library")) == "library.pre_restore_100"

    def test_the_lock_file_goes_at_a_clean_finish(self, tmp_path):
        lock = tmp_path / "library.restore-lock"
        holder = db.hold_restore_lock(str(tmp_path / "library"))
        assert lock.exists()
        db.release_restore_lock(holder)
        assert not lock.exists()
        self._aside(tmp_path, "library.pre_restore_100", b"old")
        db.library_startup_notes(str(tmp_path / "library"))
        assert not lock.exists()

    def test_a_lock_file_left_by_a_crashed_restore_blocks_nothing(self, tmp_path):
        """A restore that died keeps its lock file but no OS lock: the next
        recovery and restore take it and remove it at their clean finish."""
        lock = tmp_path / "library.restore-lock"
        lock.write_bytes(b"")
        self._aside(tmp_path, "library.pre_restore_100", b"old")
        assert db.recover_interrupted_restore(str(tmp_path / "library")) == "library.pre_restore_100"
        assert not lock.exists()
        lock.write_bytes(b"")
        holder = db.hold_restore_lock(str(tmp_path / "library"))
        assert holder is not None
        db.release_restore_lock(holder)
        assert not lock.exists()

    def test_the_lock_file_stays_while_a_restore_holds_it(self, tmp_path):
        lock = tmp_path / "library.restore-lock"
        holder = db.hold_restore_lock(str(tmp_path / "library"))
        try:
            assert db.hold_restore_lock(str(tmp_path / "library")) is None
            db.library_startup_notes(str(tmp_path / "library"))
            assert lock.exists()
        finally:
            db.release_restore_lock(holder)
        again = db.hold_restore_lock(str(tmp_path / "library"))
        assert again is not None and lock.exists()
        db.release_restore_lock(again)

    def test_a_lock_on_a_file_its_last_holder_removed_is_retried(self, tmp_path, monkeypatch):
        """The first open locked a file the last holder had just removed (the
        name no longer points at it): that lock counts for nothing, and the
        second attempt locks the file now at the name."""
        from db import library_folder
        if os.name != "posix":
            pytest.skip("the name check is POSIX-only")
        real_still_named, real_lock = library_folder._still_named, db._lock_file
        answers, locks = [False], []
        monkeypatch.setattr(library_folder, "_still_named",
                            lambda fd, path: answers.pop() if answers else real_still_named(fd, path))
        monkeypatch.setattr(db, "_lock_file",
                            lambda path, create: locks.append(path) or real_lock(path, create))
        holder = db.hold_restore_lock(str(tmp_path / "library"))
        assert holder is not None and len(locks) == 2
        db.release_restore_lock(holder)
        assert not (tmp_path / "library.restore-lock").exists()

    def test_fsync_dir_does_nothing_on_windows(self, tmp_path, monkeypatch):
        opened = []
        monkeypatch.setattr(os, "name", "nt")
        monkeypatch.setattr(os, "open", lambda *a, **k: opened.append(a))
        db.fsync_dir(str(tmp_path))
        assert opened == []
