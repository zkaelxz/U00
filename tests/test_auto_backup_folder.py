"""Moving the backup folder, never losing the copy on a crash, and cleaning stale leftovers and tombstones."""

import datetime
import os
import shutil
import time

import pytest

import db
from services import auto_backup_copies as abc_
from services import auto_backup_files as abf_
from services import auto_backup_service as abs_
from services import auto_backup_settings as abt_
from services import drama_service
from services import library_admin_service as las
from services.service_errors import ConflictError, ServiceError
from tests.auto_backup_helpers import (
    _COPY_RE, _at, _copy_names, _ddir, _default_dir, _default_path, _delete_drama, _make_legacy,
    _newest, _put_job, _read, _run_at, _snap, _untag,
)


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
        assert _read(_default_path()) == data and abt_.get_settings()["folder"] == ""
        # this library's own copy of that name (left from an earlier move) is
        (out / name).write_bytes(data)
        abs_.set_settings(folder=str(out))
        assert (out / name).read_bytes() == data
        assert sorted(os.listdir(out)) == [name]
        assert _default_path() is None
        assert abc_.snapshot_info()["exists"]
        # and back to the default folder
        abs_.set_settings(folder="")
        assert _read(_default_path()) == data
        assert os.listdir(out) == []

    def test_moves_every_copy_and_the_legacy_file_only(self, isolated_db, tmp_path,
                                                       monkeypatch):
        db.create_drama(title_en="A")
        names = [os.path.basename(_run_at(monkeypatch, _at(day))) for day in (2, 3, 4)]
        legacy = os.path.join(_default_dir(), abc_.LEGACY_SNAPSHOT_NAME)
        with open(_newest(), "rb") as src, open(legacy, "wb") as dst:
            dst.write(src.read())
        _untag(legacy, _at(1))
        old = _at(1).timestamp()        # older than every copy
        os.utime(legacy, (old, old))
        with open(os.path.join(_default_dir(), "notes.txt"), "w") as fh:
            fh.write("mine")
        before = {n: _read(os.path.join(_default_dir(), n))
                  for n in names + [abc_.LEGACY_SNAPSHOT_NAME]}
        out = tmp_path / "ext"
        out.mkdir()
        abs_.set_settings(folder=str(out))
        assert {n: (out / n).read_bytes() for n in os.listdir(out)} == before
        assert os.listdir(_default_dir()) == ["notes.txt"]
        assert [c["name"] for c in abc_.snapshot_info()["copies"]] == \
            sorted(names, reverse=True) + [abc_.LEGACY_SNAPSHOT_NAME]

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
        [copy] = abc_._list_copies(str(tmp_path))
        assert copy["name"] == abc_.LEGACY_SNAPSHOT_NAME
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
        monkeypatch.setattr(abf_.shutil, "copyfile",
                            lambda *a, **k: (_ for _ in ()).throw(OSError("no copy")))
        with pytest.raises(ServiceError):
            abs_.set_settings(folder=str(tmp_path))
        monkeypatch.undo()
        assert {p: _read(p) for p in paths} == before
        assert os.listdir(tmp_path) == []
        assert abt_.get_settings()["folder"] == ""

    def test_no_snapshot_nothing_to_move(self, isolated_db, tmp_path):
        abs_.set_settings(folder=str(tmp_path))
        assert os.listdir(tmp_path) == []
        assert abt_.get_settings()["folder"] == str(tmp_path)

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
        assert abt_.get_settings() == abt_.DEFAULT_SETTINGS
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
        assert abt_.get_settings()["folder"] == folder
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
        assert abt_.get_settings()["folder"] == ""
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
        assert abt_.get_settings()["folder"] == ""

    def test_settings_saved_under_the_snapshot_lock(self, isolated_db, monkeypatch):
        """The read-modify-write of the settings happens under _snapshot_lock
        (so two saves can't drop each other's fields), and the overview is
        built after it is released (the lock isn't reentrant)."""
        held = []
        real_set = db.set_app_setting

        def spy(key, value):
            if key == abt_.SETTINGS_KEY:
                held.append(abc_._snapshot_lock.locked())
            return real_set(key, value)
        monkeypatch.setattr(db, "set_app_setting", spy)
        out = abs_.set_settings(enabled=True, frequency="weekly")
        assert held == [True] and not abc_._snapshot_lock.locked()
        assert out["enabled"] is True and out["frequency"] == "weekly"
        abs_.set_settings(include_media=True)
        assert abt_.get_settings() == {"enabled": True, "frequency": "weekly",
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
        monkeypatch.setattr(abf_.shutil, "copyfile", copy_once)
        monkeypatch.setattr(abs_.os, "replace",
                            lambda a, b, *x, **k: (_ for _ in ()).throw(OSError("replace")))
        with pytest.raises(OSError):
            abf_._move_file(src, dest)
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
            monkeypatch.setattr(abf_.shutil, "copyfile", crash)
        elif step == "fsync":
            monkeypatch.setattr(abf_, "_fsync_file", crash)
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
            abf_._move_file(src, dest)
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
            abf_._move_file(src, dest)
        monkeypatch.undo()
        assert _read(src) == b"the only copy"
        assert list(dest_dir.iterdir()) == []

    def test_dest_that_is_src_under_another_path_is_left_alone(self, tmp_path, monkeypatch):
        src, _dest, _dest_dir = self._setup(tmp_path, monkeypatch)
        alias = tmp_path / "alias"
        os.symlink(os.path.dirname(src), alias)
        abf_._move_file(src, str(alias / os.path.basename(src)))
        monkeypatch.undo()
        assert _read(src) == b"the only copy"
        assert os.listdir(os.path.dirname(src)) == [os.path.basename(src)]

    def test_directory_flush_failure_keeps_the_original(self, tmp_path, monkeypatch):
        """The original is removed only once the rename into dest is on
        disk; a failed flush raises, takes dest out again and keeps src."""
        src, dest, dest_dir = self._setup(tmp_path, monkeypatch)

        def fail(folder):
            raise OSError(5, "I/O error")
        monkeypatch.setattr(abf_, "_fsync_dir", fail)
        with pytest.raises(OSError):
            abf_._move_file(src, dest)
        monkeypatch.undo()
        assert _read(src) == b"the only copy"
        assert list(dest_dir.iterdir()) == []

    def test_success_moves_the_copy(self, tmp_path, monkeypatch):
        src, dest, dest_dir = self._setup(tmp_path, monkeypatch)
        abf_._move_file(src, dest)
        assert not os.path.exists(src)
        assert [p.name for p in dest_dir.iterdir()] == [os.path.basename(dest)]
        assert _read(dest) == b"the only copy"


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

    def test_partial_manual_backups_and_exports_swept(self, isolated_db):
        stale, kept = [], []
        for kind in las.ARTIFACT_SUBDIRS:
            folder = las._artifact_dir(kind, create=True)
            old = os.path.join(folder, f".partial_{kind}.zip")
            new = os.path.join(folder, f".partial_{kind}_now.zip")
            done = os.path.join(folder, f"{kind}_20260101_000000.zip")
            for p in (old, new, done):
                with open(p, "wb") as fh:
                    fh.write(b"x")
            t = time.time() - 2 * 24 * 3600
            os.utime(old, (t, t))
            os.utime(done, (t, t))
            stale.append(old)
            kept += [new, done]
        assert abs_.cleanup_stale_leftovers() == len(stale)
        assert not any(os.path.exists(p) for p in stale)
        assert all(os.path.exists(p) for p in kept)

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
