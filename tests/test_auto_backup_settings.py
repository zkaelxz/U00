"""Settings, the due check, the hourly tick and the API startup hooks of the automatic backup."""

import contextlib
import datetime
import os
import threading
import time

import pytest

import background_jobs
from jobs import job_launch, job_runner
import db
from services import auto_backup_copies as abc_
from services import auto_backup_service as abs_
from services import auto_backup_settings as abt_
from services import drama_service
from services import library_admin_service as las
from services import library_backup_service as lbs
from lib.errors import InvalidInputError, ServiceError
from tests.auto_backup_helpers import (
    UTC, _conn, _copy_names, _default_dir, _default_path, _delete_drama, _enable, _patch_now,
    _put_job, _read, _restore, _snap, _state, _wait_job,
)


class TestSettings:
    def test_defaults_off_daily_db_only_default_folder(self, isolated_db):
        s = abt_.get_settings()
        assert s == {"enabled": False, "frequency": "daily", "include_media": False,
                     "folder": ""}
        ov = abs_.settings_overview()
        assert ov["enabled"] is False and ov["next_run_at"] is None
        assert ov["last_run_at"] is None and ov["last_error"] is None
        assert ov["running"] is False and ov["copies"] == []
        assert set(ov["frequencies"]) == {"daily", "weekly", "monthly"}

    def test_saved_weekly_is_kept_after_the_default_changed(self, isolated_db):
        """Settings saved while the default was weekly keep their value."""
        db.set_app_setting(abt_.SETTINGS_KEY, {"enabled": True, "frequency": "weekly",
                                              "include_media": False, "folder": ""})
        assert abt_.get_settings()["frequency"] == "weekly"
        abs_.set_settings(include_media=True)
        assert abt_.get_settings()["frequency"] == "weekly"

    def test_frequency_days(self):
        assert abt_.FREQUENCIES == {"daily": 1, "weekly": 7, "monthly": 30}

    def test_set_and_partial_update(self, isolated_db):
        abs_.set_settings(enabled=True, frequency="daily", include_media=True)
        abs_.set_settings(frequency="monthly")
        assert abt_.get_settings() == {"enabled": True, "frequency": "monthly",
                                       "include_media": True, "folder": ""}

    @pytest.mark.parametrize("kw", [
        {"enabled": "yes"}, {"enabled": 1}, {"frequency": "hourly"}, {"frequency": "Weekly"},
        {"include_media": "true"}, {"folder": 5}, {"folder": "a\x00b"},
    ])
    def test_invalid_values_refused_nothing_saved(self, isolated_db, kw):
        with pytest.raises(InvalidInputError):
            abs_.set_settings(**kw)
        assert db.get_app_setting(abt_.SETTINGS_KEY, None) is None

    def test_corrupt_stored_settings_fall_back_to_defaults(self, isolated_db):
        db.set_app_setting(abt_.SETTINGS_KEY, {"enabled": "yes", "frequency": "hourly",
                                              "include_media": 1, "folder": 3})
        assert abt_.get_settings() == abt_.DEFAULT_SETTINGS
        db.set_app_setting(abt_.SETTINGS_KEY, ["not", "a", "dict"])
        assert abt_.get_settings() == abt_.DEFAULT_SETTINGS


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
        assert abt_.get_settings()["folder"] == ""

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
        assert abt_.get_settings()["folder"] == os.path.normpath(path)

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
        assert las.latest_admin_artifact(kind) is None

    def test_empty_resets_to_default(self, isolated_db, tmp_path):
        abs_.set_settings(folder=str(tmp_path))
        abs_.set_settings(folder="")
        assert abt_.get_settings()["folder"] == ""
        assert abt_._target_dir(create=False) == _default_dir()


@pytest.fixture
def started(monkeypatch):
    calls = []
    monkeypatch.setattr(abs_, "_start",
                        lambda include_media, scheduled=False: calls.append(include_media) or True)
    return calls


class TestDueCheck:
    def test_disabled_by_default(self, isolated_db, started):
        assert abs_.check_and_run() == "disabled"
        assert abt_.is_due() is False
        assert abt_.next_run_at() is None
        assert started == []

    def test_never_run_is_due(self, isolated_db, started):
        _enable()
        assert abt_.is_due() is True
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
        assert abt_.next_run_at() == last + period

    def test_disabled_even_when_overdue(self, isolated_db, started):
        _state(last_success_at="2000-01-01T00:00:00+00:00")
        assert abs_.check_and_run() == "disabled"
        assert started == []

    def test_failed_attempt_waits_a_day_before_retry(self, isolated_db, started):
        """After a failed run the scheduled check waits RETRY_AFTER_FAILURE
        (1 day) from the failed attempt; a failure never counts as a run."""
        assert abt_.RETRY_AFTER_FAILURE == datetime.timedelta(days=1)
        _enable(frequency="daily")
        attempt = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        _state(last_attempt_at=attempt.isoformat(), last_error=abs_._FAILED)
        day = datetime.timedelta(days=1)
        assert abt_.is_due(now=attempt + day - datetime.timedelta(seconds=1)) is False
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

    def _cancelled_run(self, monkeypatch, scheduled):
        def cancel(*a, **k):
            raise background_jobs.JobCancelled(abs_.JOB_ID)
        monkeypatch.setattr(lbs, "write_backup_zip", cancel)
        with pytest.raises(background_jobs.JobCancelled):
            abs_._backup_job(abs_.JOB_ID, False, scheduled)
        return datetime.datetime.fromisoformat(abt_._get_state()["last_attempt_at"])

    def test_cancelled_run_waits_a_day_like_a_failed_one(self, isolated_db, started,
                                                         monkeypatch):
        _enable(frequency="daily")
        self._cancelled_run(monkeypatch, scheduled=True)
        state = abt_._get_state()
        assert not state.get("last_error")
        attempt = datetime.datetime.fromisoformat(state["last_attempt_at"])
        assert abs_.check_and_run(now=attempt + datetime.timedelta(hours=1)) == "not_due"
        assert started == []
        assert abs_.check_and_run(now=attempt + datetime.timedelta(days=1)) == "started"

    def test_cancelled_back_up_now_does_not_hold_off_the_schedule(self, isolated_db, started,
                                                                  monkeypatch):
        _enable(frequency="daily")
        attempt = self._cancelled_run(monkeypatch, scheduled=False)
        assert abs_.check_and_run(now=attempt + datetime.timedelta(hours=1)) == "started"

    def test_run_stopped_by_closing_baihe_does_not_hold_off_the_schedule(
            self, isolated_db, started, monkeypatch):
        _enable(frequency="daily")
        monkeypatch.setattr(job_launch, "_stopping", True)
        attempt = self._cancelled_run(monkeypatch, scheduled=True)
        monkeypatch.setattr(job_launch, "_stopping", False)
        assert abs_.check_and_run(now=attempt + datetime.timedelta(hours=1)) == "started"

    def test_the_due_check_starts_a_scheduled_run(self, isolated_db, monkeypatch):
        _enable(frequency="daily")
        calls = []
        monkeypatch.setattr(job_launch, "start_job",
                            lambda job_id, target, *args, **k: calls.append(args) or True)
        assert abs_.check_and_run() == "started"
        assert calls == [(abs_.JOB_ID, abt_.get_settings()["include_media"], True)]

    def test_future_last_success_is_due(self, isolated_db, started, monkeypatch):
        """A last run dated in the future (the clock ran ahead, then was
        corrected) doesn't hold the next backup off until that date."""
        _enable(frequency="daily")
        now = datetime.datetime(2026, 3, 1, 12, 0, 0, tzinfo=UTC)
        _patch_now(monkeypatch, lambda: now)
        _state(last_success_at=(now + datetime.timedelta(days=300)).isoformat())
        assert abt_.is_due(now=now) is True
        assert abt_.next_run_at() == now
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
        assert job_launch.enter_maintenance()
        try:
            assert abs_.check_and_run() == "busy"
        finally:
            job_launch.exit_maintenance()
        assert started == []

    def test_busy_during_exclusive_restore(self, isolated_db, started):
        _enable()
        assert job_launch.acquire_exclusive("Library restore")
        try:
            assert abs_.check_and_run() == "busy"
        finally:
            job_launch.release_exclusive()
        assert started == []

    def test_start_refused_is_busy(self, isolated_db, monkeypatch):
        _enable()
        monkeypatch.setattr(abs_, "_start", lambda include_media, scheduled=False: False)
        assert abs_.check_and_run() == "busy"

    def test_never_raises(self, isolated_db, monkeypatch):
        def boom():
            raise RuntimeError("db gone")
        monkeypatch.setattr(abs_, "get_settings", boom)
        assert abs_.check_and_run() == "error"
        assert abt_._get_state()["last_error"] == abs_._CHECK_FAILED

    def test_real_scheduled_run_then_not_due(self, isolated_db):
        a = db.create_drama(title_en="A")
        _enable(frequency="daily")
        assert abs_.check_and_run() == "started"
        st = _wait_job()
        assert st["status"] == "done", st
        assert os.path.isfile(_default_path())
        info = abc_.snapshot_info()
        assert info["exists"] and info["kind"] == "db-only" and info["drama_count"] == 1
        assert abs_.check_and_run() == "not_due"
        ov = abs_.settings_overview()
        assert ov["last_run_at"] and ov["last_error"] is None
        assert abt_._parse(ov["next_run_at"]) == abt_._parse(ov["last_run_at"]) + \
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
        monkeypatch.setattr(job_runner, "recheck_gpu_queue", lambda: None)
        monkeypatch.setattr(abs_, "periodic_tick", tick)
        monkeypatch.setattr(background, "_gpu_poller", None)
        assert background.start_gpu_queue_poller(interval=0.01)
        try:
            assert done.wait(10), ticks
        finally:
            background.stop_gpu_queue_poller()


class TestLeadReviewFollowUps:
    def test_stored_folder_inside_library_falls_back_to_default(self, isolated_db):
        """L1: a folder setting brought in by a whole-library restore is
        re-checked on every read."""
        inside = os.path.join(db.LIBRARY_DIR, "dramas")
        db.set_app_setting(abt_.SETTINGS_KEY, {**abt_.DEFAULT_SETTINGS, "folder": inside})
        assert abt_.get_settings()["folder"] == ""
        assert abt_._target_dir(create=False) == _default_dir()
        db.set_app_setting(abt_.SETTINGS_KEY, {**abt_.DEFAULT_SETTINGS, "folder": "relative"})
        assert abt_.get_settings()["folder"] == ""

    def test_stored_missing_folder_outside_library_is_kept(self, isolated_db, tmp_path):
        missing = str(tmp_path / "unplugged-drive")
        db.set_app_setting(abt_.SETTINGS_KEY, {**abt_.DEFAULT_SETTINGS, "folder": missing})
        assert abt_.get_settings()["folder"] == os.path.normpath(missing)

    def test_start_waits_for_the_snapshot_lock(self, isolated_db, monkeypatch):
        """L2: starting a backup and a folder change are serialized."""
        import threading as _t
        started = []
        monkeypatch.setattr(abs_.job_launch, "start_job",
                            lambda *a, **k: started.append(1) or True)
        abc_._snapshot_lock.acquire()
        try:
            th = _t.Thread(target=abs_._start, args=(False,))
            th.start()
            th.join(0.3)
            assert th.is_alive() and not started
        finally:
            abc_._snapshot_lock.release()
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
        assert abt_.get_settings()["folder"] == ""

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
