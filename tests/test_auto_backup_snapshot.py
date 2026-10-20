"""Writing, reading, listing and deleting the automatic backup snapshot, including its failure and disk-space behaviour."""

import collections
import contextlib
import datetime
import json
import os
import sqlite3
import threading
import time
import zipfile

import pytest

import background_jobs
from jobs import job_launch
import db
from tests.clock_helpers import frozen_clock
from services import auto_backup_copies as abc_
from services import auto_backup_files as abf_
from services import auto_backup_service as abs_
from services import auto_backup_settings as abt_
from services import drama_restore_service as drs
from services import library_admin_service as las
from services import library_backup_service as lbs
from services.service_errors import ConflictError, InvalidInputError, NotFoundError
from tests.auto_backup_helpers import (
    UTC, _conn, _copy_names, _ddir, _default_dir, _default_path, _delete_drama, _dump_all,
    _enable, _insert, _newest, _patch_now, _put_job, _read, _restore, _snap, _wait_job,
)


def _clock(monkeypatch):
    return frozen_clock(monkeypatch, abs_, abt_, abc_, abf_)


class TestSnapshotWrite:
    def test_manifest_contents(self, isolated_db):
        a = db.create_drama(title_en="Alpha", media_type="audio")
        b = db.create_drama(title_zh="只有中文", media_type="comic")
        with contextlib.closing(_conn()) as c:
            for i in range(3):
                _insert(c, "lines", drama_id=a, idx=i, start=0, end=1, zh=f"z{i}", en="")
            c.commit()
        before = abt_._now().replace(microsecond=0)
        path = _snap()
        after = abt_._now()
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
        assert abc_.snapshot_info()["kind"] == "db-only"

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
        assert abc_.snapshot_info()["kind"] == "full"

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
        lbs.write_backup_zip(str(dest), include_media=True)
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

    def test_second_run_adds_a_copy_and_keeps_the_first(self, isolated_db, monkeypatch):
        clock = _clock(monkeypatch)
        a = db.create_drama(title_en="A")
        first = _snap()
        first_bytes = _read(first)
        first_state = abt_._get_state()
        clock.advance(1.05)   # created_at has one-second resolution
        c = db.create_drama(title_en="C")
        path = _snap()
        assert sorted(os.listdir(_default_dir())) == _copy_names()
        assert _copy_names() == [os.path.basename(first), os.path.basename(path)]
        assert _read(first) == first_bytes
        dramas = abs_.list_snapshot_dramas()["dramas"]
        assert [d["id"] for d in dramas] == [a, c]
        older = abs_.list_snapshot_dramas(os.path.basename(first))
        assert [d["id"] for d in older["dramas"]] == [a]
        state = abt_._get_state()
        assert state["last_error"] is None
        assert state["last_success_at"] > first_state["last_success_at"]
        assert path == _default_path()

    def test_two_runs_in_the_same_second_get_two_names(self, isolated_db, monkeypatch):
        fixed = datetime.datetime(2026, 3, 2, 8, 0, 0, tzinfo=UTC)
        _patch_now(monkeypatch, lambda: fixed)
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
        assert abc_.snapshot_info()["exists"]

    def test_back_up_now_adds_a_copy_replace_ignored(self, isolated_db, monkeypatch):
        clock = _clock(monkeypatch)
        db.create_drama(title_en="A")
        first = _snap()
        before = _read(first)
        with pytest.raises(InvalidInputError):
            abs_.start_now(replace="yes")
        assert background_jobs.get_status(abs_.JOB_ID) is None
        clock.advance(1.05)
        assert abs_.start_now() == {"job_id": abs_.JOB_ID}   # no replace needed
        assert _wait_job()["status"] == "done"
        assert len(_copy_names()) == 2 and _read(first) == before
        background_jobs.clear_job(abs_.JOB_ID)
        abs_.start_now(replace=True, include_media=True)     # still accepted
        st = _wait_job()
        assert st["status"] == "done", st
        assert abc_.snapshot_info()["kind"] == "full"
        # a third run on one day replaces that day's second copy; the first
        # stays as the week's first copy
        assert len(_copy_names()) == 2 and _read(first) == before

    def test_back_up_now_include_media_defaults_to_setting(self, isolated_db):
        abs_.set_settings(include_media=True)
        abs_.start_now()
        assert _wait_job()["status"] == "done"
        assert abc_.snapshot_info()["kind"] == "full"

    def test_back_up_now_refused_when_busy(self, isolated_db):
        with pytest.raises(InvalidInputError):
            abs_.start_now(include_media="no")
        assert job_launch.enter_maintenance()
        try:
            with pytest.raises(ConflictError):
                abs_.start_now()
        finally:
            job_launch.exit_maintenance()
        assert job_launch.acquire_exclusive("Library restore")
        try:
            with pytest.raises(ConflictError):
                abs_.start_now()
        finally:
            job_launch.release_exclusive()
        _put_job(abs_.JOB_ID)
        with pytest.raises(ConflictError):
            abs_.start_now()
        assert _default_path() is None


class TestBackupFailureKeepsOldSnapshot:
    def _old(self):
        db.create_drama(title_en="A")
        _snap()
        return _read(_default_path()), abt_._get_state()

    def _assert_kept(self, old_bytes, old_state):
        assert _read(_default_path()) == old_bytes
        # no partial left behind, no new copy
        assert os.listdir(_default_dir()) == _copy_names() and len(_copy_names()) == 1
        state = abt_._get_state()
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
        monkeypatch.setattr(lbs, "write_backup_zip", broken)
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
        real = abf_._manifest_bytes

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
        monkeypatch.setattr(lbs, "write_backup_zip",
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
        monkeypatch.setattr(lbs, "write_backup_zip", broken)
        with pytest.raises(RuntimeError):
            abs_._backup_job(abs_.JOB_ID, False)
        assert os.listdir(_default_dir()) == []
        assert abc_.snapshot_info() == {"exists": False, "copies": []}

    def test_success_after_failure_clears_error(self, isolated_db, monkeypatch):
        old, _ = self._old()
        with monkeypatch.context() as m:
            m.setattr(abs_, "_verify_snapshot", lambda p: (_ for _ in ()).throw(OSError()))
            with pytest.raises(RuntimeError):
                abs_._backup_job(abs_.JOB_ID, False)
        _snap()
        assert abt_._get_state()["last_error"] is None


class TestSnapshotInfoAndDelete:
    def test_no_snapshot(self, isolated_db):
        assert abc_.snapshot_info() == {"exists": False, "copies": []}
        with pytest.raises(NotFoundError):
            abs_.list_snapshot_dramas()
        with pytest.raises(NotFoundError):
            abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True)
        with pytest.raises(NotFoundError):
            _restore(1)

    def test_info_no_path(self, isolated_db):
        db.create_drama(title_en="A")
        _snap()
        info = abc_.snapshot_info()
        assert info["exists"] and info["readable"] and info["drama_count"] == 1
        assert info["size"] == os.path.getsize(_default_path())
        assert db.LIBRARY_DIR not in json.dumps(info)
        [copy] = info["copies"]
        assert copy == {"name": os.path.basename(_default_path()),
                        "created_at": info["created_at"], "size": info["size"],
                        "readable": True, "kind": "db-only", "drama_count": 1,
                        "kept_as": "daily", "managed": True, "sequence": 1,
                        "sources_left_out": False}
        assert info["default_copy"] == copy["name"] and info["choose_copy"] is False
        assert db.LIBRARY_DIR not in json.dumps(abs_.settings_overview())
        assert abs_.settings_overview()["copies"] == info["copies"]

    def test_unreadable_snapshot(self, isolated_db):
        os.makedirs(_default_dir())
        name = "baihe_snapshot-20260301-120000.zip"
        with open(os.path.join(_default_dir(), name), "wb") as fh:
            fh.write(b"not a zip")
        info = abc_.snapshot_info()
        assert info == {"exists": True, "readable": False, "copies": [
            {"name": name, "created_at": "2026-03-01T12:00:00+00:00", "size": 9,
             "readable": False, "kind": None, "drama_count": None, "kept_as": None,
             "managed": False, "sequence": None, "sources_left_out": False}]}
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
        assert abc_.snapshot_info() == {"exists": False, "copies": []}
        with pytest.raises(NotFoundError):
            _restore(1)
        with pytest.raises(NotFoundError):
            drs.restore_drama(1, confirm=True, confirm_text="RESTORE",
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
        assert abc_.snapshot_info() == {"exists": False, "copies": []}

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
        info = abc_.snapshot_info()
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
        info = abc_.snapshot_info()
        assert info["exists"] and info["readable"] is False
        assert [c["readable"] for c in info["copies"]] == [False]

    @pytest.mark.parametrize("fn", ["snapshot_info", "list_snapshot_dramas"])
    def test_reads_wait_for_snapshot_lock(self, isolated_db, fn):
        db.create_drama(title_en="A")
        _snap()
        out = []
        with abc_._snapshot_lock:
            t = threading.Thread(target=lambda: out.append(getattr(abs_, fn)()))
            t.start()
            t.join(0.3)
            assert t.is_alive() and out == []
        t.join(10)
        assert not t.is_alive() and len(out) == 1


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
                drs.stage_media(zf, 7, str(staging))
            assert asked == [db.DRAMAS_DIR]
            assert list(staging.iterdir()) == []
            free += 1
            folder = drs.stage_media(zf, 7, str(staging))
        assert _read_bytes(os.path.join(folder, "a.txt")) == b"x" * 100

    def test_stage_media_goes_ahead_when_free_space_is_unreadable(self, isolated_db, tmp_path,
                                                                 monkeypatch):
        def unreadable(path):
            raise OSError("not reported")
        monkeypatch.setattr(las.shutil, "disk_usage", unreadable)
        staging = tmp_path / "staging"
        staging.mkdir()
        with self._snapshot(tmp_path) as zf:
            folder = drs.stage_media(zf, 7, str(staging))
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


class TestDiskSpaceCheck:
    """Before writing a copy the backup compares the target drive's free
    space with 1.2x the expected size (the last successful copy's size, or
    the library's when there is none); short of it the run is skipped, the
    warning is recorded like a failure and nothing is pruned."""

    Usage = collections.namedtuple("usage", "total used free")

    def _free(self, monkeypatch, free):
        from services import backup_space_check
        monkeypatch.setattr(backup_space_check.shutil, "disk_usage",
                            lambda p: self.Usage(10 ** 12, 0, free))

    def _folder(self):
        return sorted(os.listdir(_default_dir()))

    def test_too_little_space_skips_records_and_prunes_nothing(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        _snap()
        last = abt_._get_state()
        size = os.path.getsize(_default_path())
        before = {n: _read(os.path.join(_default_dir(), n)) for n in self._folder()}
        self._free(monkeypatch, int(size * 1.19))
        writes = []
        monkeypatch.setattr(lbs, "write_backup_zip", lambda *a, **k: writes.append(a))
        monkeypatch.setattr(abs_, "_prune", lambda *a: writes.append("prune"))
        with pytest.raises(RuntimeError) as e:
            abs_._backup_job(abs_.JOB_ID, False)
        assert writes == []
        assert {n: _read(os.path.join(_default_dir(), n)) for n in self._folder()} == before
        state = abt_._get_state()
        from services import backup_space_check
        assert state["last_error"] == backup_space_check.LOW_SPACE == str(e.value)
        assert state["last_success_at"] == last["last_success_at"]
        assert abs_.settings_overview()["last_error"] == backup_space_check.LOW_SPACE
        assert db.LIBRARY_DIR not in backup_space_check.LOW_SPACE
        _enable()
        assert not abt_.is_due()          # retried like a failed run, not every hour

    def test_enough_space_backs_up_and_records_the_copy_size(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        _snap()
        size = os.path.getsize(_default_path())
        self._free(monkeypatch, int(size * 1.21) + 1)
        _snap()
        state = abt_._get_state()
        assert state["last_error"] is None
        assert state["last_copy_size"] == os.path.getsize(_default_path())
        assert state["last_copy_kind"] == "db-only"

    def test_first_copy_is_measured_against_the_database(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        self._free(monkeypatch, os.path.getsize(db.DB_PATH))
        with pytest.raises(RuntimeError):
            abs_._backup_job(abs_.JOB_ID, False)
        assert os.listdir(_default_dir()) == []

    def test_a_copy_of_the_other_kind_is_measured_against_the_library(self, isolated_db,
                                                                      monkeypatch):
        did = db.create_drama(title_en="A")
        _snap()
        with open(os.path.join(db.drama_dir(did), "audio.wav"), "wb") as fh:
            fh.write(b"x" * 2_000_000)
        self._free(monkeypatch, 2_000_000)
        with pytest.raises(RuntimeError):
            abs_._backup_job(abs_.JOB_ID, True)
        assert len(_copy_names()) == 1

    def test_unreadable_free_space_never_blocks(self, isolated_db, monkeypatch):
        from services import backup_space_check
        db.create_drama(title_en="A")

        def no_info(path):
            raise OSError("not supported")
        monkeypatch.setattr(backup_space_check.shutil, "disk_usage", no_info)
        _snap()
        assert len(_copy_names()) == 1
