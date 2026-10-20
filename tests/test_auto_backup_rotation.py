"""Rotating copies and the named copy of the automatic backup."""

import contextlib
import datetime
import os
import sqlite3
import zipfile

import pytest

import db
from services import auto_backup_copies as abc_
from services import auto_backup_files as abf_
from services import auto_backup_service as abs_
from services import auto_backup_settings as abt_
from services import drama_restore_service as drs
from services import library_backup_service as lbs
from services.service_errors import ConflictError, InvalidInputError, NotFoundError
from tests.auto_backup_helpers import (
    UTC, _at, _conn, _copy_names, _default_dir, _delete_drama, _dump_all, _make_legacy, _newest,
    _patch_now, _read, _restore, _run_at, _snap, _wait_job,
)


def _name(when):
    return f"baihe_snapshot-{when:%Y%m%d-%H%M%S}.zip"


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
        info = abc_.snapshot_info()
        assert [(c["name"], c["kept_as"]) for c in info["copies"]] == [
            (_name(_at(20)), "daily"), (_name(_at(18)), "daily"),
            (_name(_at(16)), "weekly"), (_name(_at(10)), "weekly")]
        assert info["created_at"] == abt_._iso(_at(20))
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
        with abc_._snapshot_lock:
            assert abc_._prune(_default_dir(), _name(_at(20))) == 6
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
        assert [(c["name"], c["kept_as"]) for c in abc_.snapshot_info()["copies"]] == [
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
        assert [(c["name"], c["kept_as"]) for c in abc_.snapshot_info()["copies"]] == [
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
            abc_.LEGACY_SNAPSHOT_NAME, _name(_at(2)), _name(_at(4)), _name(_at(5))])
        kept = {c["name"]: c["kept_as"] for c in abc_.snapshot_info()["copies"]}
        assert kept == {abc_.LEGACY_SNAPSHOT_NAME: None, _name(_at(5)): "daily",
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
        info = abc_.snapshot_info()
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
        _patch_now(monkeypatch, lambda: _at(2))
        assert abc_.snapshot_info()["created_at"] == abt_._iso(future)
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
        real_zipfile = abc_.zipfile.ZipFile

        def locked(file, *args, **kw):
            if isinstance(file, str) and os.path.abspath(file) == oldest:
                raise PermissionError(13, "in use")
            return real_zipfile(file, *args, **kw)
        monkeypatch.setattr(abc_.zipfile, "ZipFile", locked)
        assert abc_._read_copy(oldest) == (abc_._UNREADABLE, None)
        # on the 23rd (week 13) week 10's copy is the third week back
        _run_at(monkeypatch, _at(23))
        assert _read(oldest) == data
        info = {c["name"]: (c["readable"], c["kept_as"]) for c in abc_.snapshot_info()["copies"]}
        assert info == {_name(_at(23)): (True, "daily"), _name(_at(17)): (True, "daily"),
                        _name(_at(16)): (True, "weekly"), _name(_at(9)): (True, "weekly"),
                        _name(_at(2)): (False, None)}
        # once it can be read again, the rotation drops it as usual
        monkeypatch.setattr(abc_.zipfile, "ZipFile", real_zipfile)
        _run_at(monkeypatch, _at(24))
        assert not os.path.exists(oldest)
        assert sorted(os.listdir(_default_dir())) == [
            _name(_at(9)), _name(_at(16)), _name(_at(23)), _name(_at(24))]

    def test_unreadable_copy_gets_no_slot(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        for day in (2, 9, 16, 17):
            _run_at(monkeypatch, _at(day))
        week11 = os.path.join(_default_dir(), _name(_at(9)))
        real_zipfile = abc_.zipfile.ZipFile

        def locked(file, *args, **kw):
            if isinstance(file, str) and os.path.abspath(file) == week11:
                raise PermissionError(13, "in use")
            return real_zipfile(file, *args, **kw)
        monkeypatch.setattr(abc_.zipfile, "ZipFile", locked)
        _run_at(monkeypatch, _at(23))
        # week 11's copy is skipped, so week 10's first copy takes the second
        # weekly slot; the skipped copy itself is kept
        assert sorted(os.listdir(_default_dir())) == [
            _name(_at(2)), _name(_at(9)), _name(_at(16)), _name(_at(17)), _name(_at(23))]

    def test_read_copy_tells_damage_from_an_os_error(self, isolated_db, tmp_path):
        bad = tmp_path / "bad.zip"
        bad.write_bytes(b"not a zip")
        assert abc_._read_copy(str(bad)) == (abc_._DAMAGED, None)
        no_manifest = tmp_path / "nomanifest.zip"
        with zipfile.ZipFile(no_manifest, "w") as zf:
            zf.writestr("library.db", b"x")
        assert abc_._read_copy(str(no_manifest)) == (abc_._DAMAGED, None)
        assert abc_._read_copy(str(tmp_path / "missing.zip")) == (abc_._UNREADABLE, None)

    def test_new_copy_is_on_disk_before_old_ones_are_pruned(self, isolated_db, monkeypatch):
        db.create_drama(title_en="A")
        _run_at(monkeypatch, _at(2))
        events = []
        real_file, real_dir, real_prune = abf_._fsync_file, abs_._fsync_dir, abc_._prune
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
        assert abt_._get_state()["last_error"] is None

    @pytest.mark.parametrize("breakage", ["writer", "verify", "manifest", "job_thread"])
    def test_a_failed_run_never_deletes_anything(self, isolated_db, monkeypatch, breakage):
        db.create_drama(title_en="A")
        for day in (2, 9, 16, 17):
            _run_at(monkeypatch, _at(day))
        before = {n: _read(os.path.join(_default_dir(), n)) for n in _copy_names()}
        assert len(before) == 4     # a successful run on the 18th would delete one
        _patch_now(monkeypatch, lambda: _at(18))
        if breakage == "writer":
            monkeypatch.setattr(lbs, "write_backup_zip",
                                lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
        elif breakage == "verify":
            monkeypatch.setattr(abs_, "_verify_snapshot",
                                lambda p: (_ for _ in ()).throw(InvalidInputError("bad")))
        elif breakage == "manifest":
            monkeypatch.setattr(abs_, "_manifest_bytes", lambda snap, media: b"{not json")
        else:
            monkeypatch.setattr(lbs, "write_backup_zip",
                                lambda *a, **k: (_ for _ in ()).throw(OSError("x")))
        if breakage == "job_thread":
            abs_.start_now()
            assert _wait_job()["status"] == "error"
        else:
            with pytest.raises(RuntimeError):
                abs_._backup_job(abs_.JOB_ID, False)
        assert sorted(os.listdir(_default_dir())) == sorted(before)
        assert {n: _read(os.path.join(_default_dir(), n)) for n in before} == before
        assert abt_._get_state()["last_error"] == abs_._FAILED

    def test_failed_prune_keeps_the_new_copy_and_reports_success(self, isolated_db,
                                                                 monkeypatch):
        db.create_drama(title_en="A")
        _run_at(monkeypatch, _at(2))
        monkeypatch.setattr(abs_, "_prune", lambda folder, new_name: 1 / 0)
        path = _run_at(monkeypatch, _at(3))
        assert os.path.isfile(path) and len(_copy_names()) == 2
        assert abt_._get_state()["last_error"] is None

    def test_legacy_snapshot_is_a_copy_and_ages_out(self, isolated_db, monkeypatch):
        a = db.create_drama(title_en="A")
        # 2026-03-01 is a Sunday: ISO week 9.
        legacy = _make_legacy(_at(1))
        info = abc_.snapshot_info()
        assert info["exists"] and info["readable"]
        assert [c["name"] for c in info["copies"]] == [abc_.LEGACY_SNAPSHOT_NAME]
        assert info["copies"][0]["managed"] is True
        # no copy numbered by this library yet: the owner names one
        assert info["choose_copy"] is True
        with pytest.raises(ConflictError):
            abs_.list_snapshot_dramas()
        assert [d["id"] for d in abs_.list_snapshot_dramas(
            abc_.LEGACY_SNAPSHOT_NAME)["dramas"]] == [a]
        # upgrading loses nothing: the first rotating run keeps it
        _run_at(monkeypatch, _at(2))
        _run_at(monkeypatch, _at(3))
        assert os.path.isfile(legacy)                   # weekly slot for week 9
        _run_at(monkeypatch, _at(9))
        _run_at(monkeypatch, _at(10))
        assert os.path.isfile(legacy)                   # weeks 10 and 9 still in reach
        assert sorted(os.listdir(_default_dir())) == sorted(
            [abc_.LEGACY_SNAPSHOT_NAME, _name(_at(2)), _name(_at(9)), _name(_at(10))])
        _run_at(monkeypatch, _at(16))
        assert not os.path.exists(legacy)               # now the third week back
        assert sorted(os.listdir(_default_dir())) == [
            _name(_at(2)), _name(_at(9)), _name(_at(10)), _name(_at(16))]

    def test_restore_from_a_copy_older_than_the_repeat_guard_resets_the_old_silence_default(
            self, isolated_db):
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
        res = drs.restore_drama(a, confirm=True, confirm_text="RESTORE",
                                 snapshot=os.path.basename(path))
        assert db.get_drama(res["drama_id"])["hallucination_silence_sec"] == 0

    def test_restore_from_the_legacy_snapshot_by_name(self, isolated_db, monkeypatch):
        a = db.create_drama(title_en="Legacy drama")
        _make_legacy(_at(1))
        db.update_drama(a, title_en="Renamed")
        _run_at(monkeypatch, _at(2))
        _delete_drama(a)
        res = drs.restore_drama(a, confirm=True, confirm_text="RESTORE",
                                 snapshot=abc_.LEGACY_SNAPSHOT_NAME)
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
        info = abc_.snapshot_info()
        assert [c["name"] for c in info["copies"]] == [
            _name(_at(4)), _name(_at(3, hour=12)), _name(_at(3)), _name(_at(2)),
            "baihe_snapshot-20260101-000000.zip"]
        assert [c["readable"] for c in info["copies"]] == [True, False, True, True, False]
        assert [c["managed"] for c in info["copies"]] == [True, False, True, True, False]
        assert info["created_at"] == abt_._iso(_at(4))
        # a damaged file dated before the default doesn't matter; one dated
        # after it may have been the newest copy, so the owner chooses
        assert abs_.list_snapshot_dramas()["name"] == _name(_at(4))
        put(_name(_at(5)))
        with pytest.raises(ConflictError) as e:
            abs_.list_snapshot_dramas()
        assert e.value.details["reason"] == "choose_copy"
        assert _name(_at(5)) not in [c["name"] for c in e.value.details["candidates"]]
        assert abc_.snapshot_info()["choose_copy"] is True
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
            drs.restore_drama(b, confirm=True, confirm_text="RESTORE", snapshot=older)
        _delete_drama(a)
        res = drs.restore_drama(a, confirm=True, confirm_text="RESTORE", snapshot=older)
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
                     lambda: drs.restore_drama(a, confirm=True, confirm_text="RESTORE",
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
        assert name not in [c["name"] for c in abc_.snapshot_info()["copies"]]
        _delete_drama(a)
        with pytest.raises(NotFoundError):
            drs.restore_drama(a, confirm=True, confirm_text="RESTORE", snapshot=name)
        with pytest.raises(NotFoundError):
            abs_.delete_snapshot(confirm=True, confirm_text="DELETE", snapshot=name)
        _run_at(monkeypatch, _at(20))                # a prune never touches it
        abs_.delete_snapshot(confirm=True, confirm_text="DELETE", all_copies=True)
        assert os.path.islink(os.path.join(_default_dir(), name))
        assert outside.read_bytes() == data
        assert db.get_drama(a) is None
