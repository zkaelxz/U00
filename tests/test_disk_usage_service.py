"""
services/disk_usage_service.py: scan, protected paths, Recycle Bin clear and
the backup-folder move. Temp folders only; the Recycle Bin call is replaced.
"""

import os

import pytest

import background_jobs
import db
from services import auto_backup_service as abs_
from services import disk_usage_service as dus
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError, UnsupportedOperationError)


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


_REAL_REASON = dus.recycle_unavailable_reason


@pytest.fixture
def tree(isolated_db, monkeypatch):
    """root/ (the data folder) holding library/ (the isolated library) plus
    some media, a cache and a .env."""
    lib = db.LIBRARY_DIR
    root = os.path.dirname(lib)
    _write(os.path.join(lib, "dramas", "1", "audio.mp3"), 5000)
    _write(os.path.join(lib, "dramas", "1", "dub_clips", "a.wav"), 700)
    _write(os.path.join(lib, "dramas", "1", "pages", "typeset_0001.png"), 300)
    _write(os.path.join(lib, "dramas", "2", "audio.mp3"), 1000)
    _write(os.path.join(lib, "source_cache", "x.html"), 400)
    _write(os.path.join(lib, "backups", "auto", "baihe_snapshot-20260101-000000.zip"), 200)
    _write(os.path.join(root, ".env"), 20)
    _write(os.path.join(root, "model_cache", "m.bin"), 3000)
    # An interactive Windows session; the session tests below put the real check back.
    monkeypatch.setattr(dus, "recycle_unavailable_reason", lambda: None)
    return root


@pytest.fixture
def recycled(monkeypatch):
    calls = []

    def fake(path, size_bytes=0):
        calls.append((path, size_bytes))
        if os.path.isdir(path):
            import shutil
            shutil.rmtree(path)
        else:
            os.remove(path)
    monkeypatch.setattr(dus, "send_to_recycle_bin", fake)
    return calls


def _item(result, name):
    return next(i for i in result["items"] if i["name"] == name)


class TestScan:
    def test_sizes_sorted_and_percent(self, tree):
        res = dus.scan("")
        names = [i["name"] for i in res["items"]]
        assert names[0] == "library"            # biggest first
        assert res["total_bytes"] == sum(i["size_bytes"] for i in res["items"])
        assert abs(sum(i["percent_of_parent"] for i in res["items"]) - 100) < 0.5
        mc = _item(res, "model_cache")
        assert (mc["size_bytes"], mc["file_count"], mc["kind"]) == (3000, 1, "folder")
        assert mc["regenerable"]["label"] == "Downloaded models"
        assert res["partial"] is False and res["parent"] is None

    def test_drill_down_and_regenerable_labels(self, tree):
        d1 = dus.scan("library/dramas/1")
        clips = _item(d1, "dub_clips")
        assert clips["regenerable"]["label"] == "Per-line dub clips"
        assert clips["irreplaceable"] is False
        audio = _item(d1, "audio.mp3")
        assert audio["irreplaceable"] is True and audio["irreplaceable_note"]
        assert d1["parent"] == "library/dramas"
        pages = dus.scan("library/dramas/1/pages")
        assert _item(pages, "typeset_0001.png")["regenerable"]["label"] == "Rendered typeset pages"
        lib = dus.scan("library")
        assert _item(lib, "source_cache")["regenerable"]
        assert _item(lib, "dramas")["irreplaceable"] is True

    def test_responses_never_hold_the_data_folder_path(self, tree):
        import json
        for rel in ("", "library", "library/dramas/1"):
            assert tree not in json.dumps(dus.scan(rel))

    def test_symlink_is_counted_as_itself_not_followed(self, tree, tmp_path):
        outside = tmp_path / "big"
        outside.mkdir()
        (outside / "huge.bin").write_bytes(b"y" * 100_000)
        link = os.path.join(tree, "library", "dramas", "1", "link")
        try:
            os.symlink(str(outside), link, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        res = dus.scan("library/dramas/1")
        item = _item(res, "link")
        assert item["is_link"] and item["protected"]
        assert item["size_bytes"] < 10_000          # the link, not 100 kB
        # the folder holding it did not grow either
        assert dus.scan("library/dramas")["total_bytes"] < 20_000
        with pytest.raises(InvalidInputError):
            dus.scan("library/dramas/1/link")

    def test_partial_when_entry_cap_hit(self, tree, monkeypatch):
        monkeypatch.setattr(dus, "MAX_ENTRIES", 3)
        res = dus.scan("")
        assert res["partial"] is True and res["partial_reason"] == "entries"

    def test_partial_when_time_cap_hit(self, tree, monkeypatch):
        monkeypatch.setattr(dus, "MAX_SECONDS", -1.0)
        monkeypatch.setattr(dus, "_TIME_CHECK_EVERY", 1)
        res = dus.scan("library")
        assert res["partial"] is True and res["partial_reason"] == "time"
        assert any(not i["complete"] for i in res["items"])

    def test_file_vanishing_mid_scan_is_skipped(self, tree, monkeypatch):
        real_scandir = os.scandir
        victim = os.path.join(tree, "library", "dramas", "2", "audio.mp3")

        def racing(path):
            if os.path.abspath(str(path)).endswith(os.path.join("dramas", "2")) and os.path.exists(victim):
                os.remove(victim)
            return real_scandir(path)
        monkeypatch.setattr(os, "scandir", racing)
        dus.scan("library/dramas")           # does not raise

    def test_a_file_is_not_a_folder_and_missing_is_404(self, tree):
        with pytest.raises(InvalidInputError):
            dus.scan(".env")
        with pytest.raises(NotFoundError):
            dus.scan("library/nope")


class TestProtection:
    def test_flags(self, tree):
        res = dus.scan("")
        assert _item(res, ".env")["protected"]
        lib = _item(res, "library")
        assert lib["protected"] and "open it" in lib["protected_reason"].lower()
        assert lib["movable"]["supported"] is False
        inner = dus.scan("library")
        assert _item(inner, "library.db")["protected"]
        assert not _item(inner, "dramas")["protected"]

    def test_db_sidecars_and_markers_protected(self, tree):
        lib = os.path.join(tree, "library")
        for name in ("library.db-wal", "library.db-shm", "library.db-journal"):
            _write(os.path.join(lib, "old", name), 5)    # SQLite resets a bogus sidecar by the live db
        _write(os.path.join(tree, "INSTALLED"), 5)
        _write(os.path.join(lib, "notes", "api.key"), 5)
        assert all(_item(dus.scan("library/old"), n)["protected"]
                   for n in ("library.db-wal", "library.db-shm", "library.db-journal"))
        assert _item(dus.scan(""), "INSTALLED")["protected"]
        assert _item(dus.scan("library"), "notes")["protected"]      # holds a key file

    def test_backup_copies_of_the_database_are_not_the_live_one(self, tree):
        _write(os.path.join(tree, "library", "backups", "database", "library_20260101.db"), 50)
        assert not _item(dus.scan("library"), "backups")["protected"]

    def test_program_folder_is_protected(self, tree, monkeypatch):
        monkeypatch.setattr(dus, "_program_dir", lambda: os.path.realpath(os.path.join(tree, "prog")))
        _write(os.path.join(tree, "prog", "run.py"), 5)
        item = _item(dus.scan(""), "prog")
        assert item["protected"] and "program" in item["protected_reason"].lower()

    def test_source_checkout_root_protects_everything_but_data(self, tree, monkeypatch):
        monkeypatch.setattr(dus, "_program_dir", lambda: os.path.realpath(tree))
        _write(os.path.join(tree, "frontend", "app.tsx"), 5)
        res = dus.scan("")
        assert _item(res, "frontend")["protected"]
        assert not _item(res, "model_cache")["protected"]

    @pytest.mark.parametrize("path", ["", ".env", "library", "library/library.db", "model_cache/.."])
    def test_clear_refuses_protected(self, tree, recycled, path):
        with pytest.raises(InvalidInputError):
            dus.clear(path, confirm=True, expected_size_bytes=0, expected_file_count=0)
        assert recycled == []

    def test_move_refuses_protected(self, tree, tmp_path):
        with pytest.raises(InvalidInputError):
            dus.move("library/library.db", str(tmp_path), confirm=True)


class TestPathValidation:
    @pytest.mark.parametrize("bad", [
        "..", "../x", "library/../..", "/etc", "\\Windows", "C:\\Users", "C:", "library/a:b",
        "library/..", "a//b", "library/x.", "library/x ", "li*brary", "a\x00b", "library/.",
    ])
    def test_traversal_and_odd_names_refused(self, tree, recycled, bad):
        with pytest.raises(InvalidInputError):
            dus.scan(bad)
        with pytest.raises(InvalidInputError):
            dus.clear(bad, confirm=True, expected_size_bytes=0, expected_file_count=0)
        assert recycled == []

    def test_symlink_out_of_the_data_folder_refused(self, tree, tmp_path, recycled):
        outside = tmp_path / "outside"
        (outside / "sub").mkdir(parents=True)
        (outside / "sub" / "f.txt").write_bytes(b"data")
        try:
            os.symlink(str(outside), os.path.join(tree, "library", "evil"),
                       target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        for rel in ("library/evil", "library/evil/sub", "library/evil/sub/f.txt"):
            with pytest.raises(InvalidInputError):
                dus.clear(rel, confirm=True, expected_size_bytes=4, expected_file_count=1)
        assert recycled == [] and (outside / "sub" / "f.txt").exists()


class TestClear:
    def _seen(self, rel):
        item = next(i for i in dus.scan(os.path.dirname(rel))["items"] if i["path"] == rel)
        return item["size_bytes"], item["file_count"]

    def test_sends_to_recycle_bin_and_reports_freed(self, tree, recycled):
        size, files = self._seen("library/source_cache")
        res = dus.clear("library/source_cache", confirm=True, expected_size_bytes=size,
                        expected_file_count=files)
        assert res == {"freed_bytes": 400, "file_count": 1, "kind": "folder", "name": "source_cache"}
        assert len(recycled) == 1 and recycled[0][1] == 400
        assert recycled[0][0].endswith("source_cache")
        assert not os.path.exists(recycled[0][0])

    def test_needs_confirm_and_the_seen_sizes(self, tree, recycled):
        with pytest.raises(InvalidInputError):
            dus.clear("library/source_cache", confirm=False, expected_size_bytes=400,
                      expected_file_count=1)
        with pytest.raises(InvalidInputError):
            dus.clear("library/source_cache", confirm=True)
        assert recycled == []

    def test_changed_since_seen_is_a_409(self, tree, recycled):
        _write(os.path.join(tree, "library", "source_cache", "new.html"), 50)
        with pytest.raises(ConflictError) as exc:
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert exc.value.details["reason"] == "changed"
        assert exc.value.details["size_bytes"] == 450
        assert recycled == []

    def test_title_media_needs_the_extra_confirm(self, tree, recycled):
        args = dict(confirm=True, expected_size_bytes=1000, expected_file_count=1)
        with pytest.raises(ConflictError) as exc:
            dus.clear("library/dramas/2", **args)
        assert exc.value.details["reason"] == "needs_irreplaceable_confirm"
        assert recycled == []
        assert dus.clear("library/dramas/2", confirm_irreplaceable=True, **args)["freed_bytes"] == 1000

    def test_regenerable_inside_a_title_needs_no_extra_confirm(self, tree, recycled):
        res = dus.clear("library/dramas/1/dub_clips", confirm=True, expected_size_bytes=700,
                        expected_file_count=1)
        assert res["freed_bytes"] == 700

    def test_refused_while_a_job_runs(self, tree, recycled, monkeypatch):
        monkeypatch.setattr(background_jobs, "active_job_ids", lambda: ["transcribe_1"])
        with pytest.raises(ConflictError) as exc:
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert "running" in exc.value.message
        assert recycled == []
        assert dus.scan("")["busy_reason"]

    def test_refused_during_maintenance(self, tree, recycled, monkeypatch):
        monkeypatch.setattr(background_jobs, "maintenance_active", lambda: True)
        with pytest.raises(ConflictError):
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)

    def test_a_link_swapped_in_before_the_call_is_not_followed(self, tree, tmp_path, monkeypatch):
        outside = tmp_path / "keep"
        outside.mkdir()
        (outside / "f").write_bytes(b"1234")
        target = os.path.join(tree, "library", "source_cache")
        real_resolve = dus._resolve
        state = {"n": 0}

        def swapping(parts):
            out = real_resolve(parts)
            state["n"] += 1
            if state["n"] == 1:             # after the first look, swap in a link
                import shutil
                shutil.rmtree(target)
                os.symlink(str(outside), target, target_is_directory=True)
            return out
        monkeypatch.setattr(dus, "_resolve", swapping)
        called = []
        monkeypatch.setattr(dus, "send_to_recycle_bin", lambda p, s=0: called.append(p))
        try:
            with pytest.raises((ConflictError, InvalidInputError)):
                dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                          expected_file_count=1)
        except OSError:
            pytest.skip("no symlinks here")
        assert called == [] and (outside / "f").exists()

    @pytest.mark.skipif(os.name == "nt", reason="the real call exists on Windows")
    def test_no_recycle_bin_off_windows_deletes_nothing(self, tree, monkeypatch):
        monkeypatch.setattr(dus, "recycle_unavailable_reason", _REAL_REASON)
        with pytest.raises(UnsupportedOperationError) as exc:
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert "No Recycle Bin" in exc.value.message
        assert os.path.exists(os.path.join(tree, "library", "source_cache", "x.html"))

    def test_item_still_there_after_the_call_is_an_error(self, tree, monkeypatch):
        monkeypatch.setattr(dus, "send_to_recycle_bin", lambda p, s=0: None)
        with pytest.raises(ServiceError):
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)

    def test_oserror_from_the_platform_call_has_no_path(self, tree, monkeypatch):
        def boom(p, s=0):
            raise OSError(5, "denied " + p)
        monkeypatch.setattr(dus, "send_to_recycle_bin", boom)
        with pytest.raises(ServiceError) as exc:
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert tree not in exc.value.message


class TestRecycleBlocker:
    GB = 1024 ** 3

    def test_not_a_fixed_drive(self):
        assert dus.recycle_blocker(10, 4, None, None, 100 * self.GB)
        assert dus.recycle_blocker(10, 2, None, None, 100 * self.GB)

    def test_delete_immediately_setting(self):
        assert dus.recycle_blocker(10, 3, 1, 5000, 100 * self.GB)

    def test_explicit_limit(self):
        assert dus.recycle_blocker(2000 * 1024 ** 2, 3, 0, 1000, 100 * self.GB)
        assert dus.recycle_blocker(500 * 1024 ** 2, 3, 0, 1000, 100 * self.GB) is None

    def test_default_limit_is_a_fraction_of_the_drive(self):
        assert dus.recycle_blocker(10 * self.GB, 3, 0, None, 100 * self.GB)
        assert dus.recycle_blocker(1 * self.GB, 3, 0, None, 100 * self.GB) is None

    def test_unknown_size_refuses(self):
        assert dus.recycle_blocker(10, 3, 0, None, None)


class TestMove:
    def test_only_the_backup_folder_is_movable(self, tree):
        lib = dus.scan("library")
        assert _item(dus.scan("library/backups"), "auto")["movable"] == {
            "supported": True, "reason": None, "what": "backups"}
        assert _item(lib, "dramas")["movable"]["supported"] is False
        assert "fixed place" in _item(lib, "dramas")["movable"]["reason"]
        assert "restart" in _item(dus.scan(""), "model_cache")["movable"]["reason"]
        with pytest.raises(UnsupportedOperationError):
            dus.move("model_cache", str(os.path.dirname(tree)), confirm=True)

    def test_moves_the_folder_and_repoints_the_setting(self, tree, tmp_path):
        dest = tmp_path / "newbackups"
        dest.mkdir()
        res = dus.move("library/backups/auto", str(dest), confirm=True)
        assert res["what"] == "backups" and res["name"] == "auto"
        assert abs_.get_settings()["folder"] == os.path.normpath(str(dest))
        # The item is no longer the active backup folder.
        assert _item(dus.scan("library/backups"), "auto")["movable"]["supported"] is False

    def test_failure_leaves_the_original_and_the_setting(self, tree, tmp_path, monkeypatch):
        dest = tmp_path / "newbackups"
        dest.mkdir()
        before = abs_.get_settings()["folder"]

        def failing(**kw):
            raise ServiceError("The existing backup copies could not be moved.")
        monkeypatch.setattr(abs_, "set_settings", failing)
        with pytest.raises(ServiceError):
            dus.move("library/backups/auto", str(dest), confirm=True)
        assert abs_.get_settings()["folder"] == before
        assert os.path.exists(os.path.join(tree, "library", "backups", "auto",
                                           "baihe_snapshot-20260101-000000.zip"))

    @pytest.mark.parametrize("where", ["inside_item", "inside_data", "relative", "missing", "file"])
    def test_bad_destinations(self, tree, tmp_path, where):
        dest = {"inside_item": os.path.join(tree, "library", "backups", "auto"),
                "inside_data": os.path.join(tree, "model_cache"),
                "relative": "somewhere",
                "missing": str(tmp_path / "nope"),
                "file": str(_touch(tmp_path / "f.txt"))}[where]
        before = abs_.get_settings()["folder"]
        with pytest.raises(InvalidInputError):
            dus.move("library/backups/auto", dest, confirm=True)
        assert abs_.get_settings()["folder"] == before

    def test_destination_symlink_refused(self, tree, tmp_path):
        real = tmp_path / "real"
        real.mkdir()
        link = tmp_path / "link"
        try:
            os.symlink(str(real), str(link), target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        with pytest.raises(InvalidInputError):
            dus.move("library/backups/auto", str(link), confirm=True)

    def test_needs_confirm_and_refused_while_a_job_runs(self, tree, tmp_path, monkeypatch):
        with pytest.raises(InvalidInputError):
            dus.move("library/backups/auto", str(tmp_path), confirm=False)
        monkeypatch.setattr(background_jobs, "active_job_ids", lambda: ["x"])
        with pytest.raises(ConflictError):
            dus.move("library/backups/auto", str(tmp_path), confirm=True)


def _touch(path):
    path.write_bytes(b"x")
    return path


# --------------------------------------------------------------------------
# Security review fixes
# --------------------------------------------------------------------------

USER_SID = "S-1-5-21-1111111111-2222222222-3333333333-1001"


class TestServiceSession:                                  # H1
    def test_pure_check(self):
        assert dus.session_blocker(1, USER_SID, "Ana") is None
        assert dus.session_blocker(2, "S-1-12-1-1-2-3-4", "Ana") is None
        assert dus.session_blocker(0, USER_SID, "Ana") == dus.NOT_INTERACTIVE
        for sid in ("S-1-5-80-123-456", "S-1-5-18", "S-1-5-19", "S-1-5-20", "S-1-5-82-1", "S-1-5-90-0-1"):
            assert dus.session_blocker(1, sid, "BaiheStudio") == dus.NOT_INTERACTIVE, sid
        assert dus.session_blocker(1, USER_SID, "PC$") == dus.NOT_INTERACTIVE
        for bad in ((None, USER_SID, "a"), (-1, USER_SID, "a"), (True, USER_SID, "a"),
                    (1, None, "a"), (1, "", "a")):
            assert dus.session_blocker(*bad) == dus.SESSION_UNKNOWN, bad

    def test_reason_uses_injected_platform_facts(self, monkeypatch):
        monkeypatch.setattr(dus, "_is_windows", lambda: True)
        monkeypatch.setattr(dus, "_windows_session_facts", lambda: (0, "S-1-5-80-1-2", "BaiheStudio"))
        assert dus.recycle_unavailable_reason() == dus.NOT_INTERACTIVE
        monkeypatch.setattr(dus, "_windows_session_facts", lambda: (3, USER_SID, "Ana"))
        assert dus.recycle_unavailable_reason() is None

    def test_undeterminable_fails_closed(self, monkeypatch):
        monkeypatch.setattr(dus, "_is_windows", lambda: True)

        def boom():
            raise OSError("no token")
        monkeypatch.setattr(dus, "_windows_session_facts", boom)
        assert dus.recycle_unavailable_reason() == dus.SESSION_UNKNOWN

    def test_service_session_refuses_clear_and_reports_it_but_move_works(self, tree, recycled,
                                                                         monkeypatch, tmp_path):
        monkeypatch.setattr(dus, "recycle_unavailable_reason", lambda: dus.NOT_INTERACTIVE)
        res = dus.scan("")
        assert res["recycle_available"] is False and res["recycle_reason"] == dus.NOT_INTERACTIVE
        with pytest.raises(UnsupportedOperationError) as exc:
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert "not as a Windows service" in exc.value.message
        assert recycled == []
        dest = tmp_path / "moved"
        dest.mkdir()
        assert dus.move("library/backups/auto", str(dest), confirm=True)["what"] == "backups"

    def test_the_platform_call_checks_again(self, tree, monkeypatch):
        monkeypatch.setattr(dus, "recycle_unavailable_reason", lambda: dus.NOT_INTERACTIVE)
        with pytest.raises(UnsupportedOperationError):
            dus.send_to_recycle_bin(os.path.join(tree, "model_cache"), 1)


class TestExclusiveHold:                                   # M1
    def _clear(self):
        return dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                         expected_file_count=1)

    def test_the_hold_is_taken_during_the_change_and_released_after(self, tree, monkeypatch):
        seen = {}

        def fake(path, size_bytes=0):
            seen["label"] = background_jobs._exclusive_label
            import shutil
            shutil.rmtree(path)
        monkeypatch.setattr(dus, "send_to_recycle_bin", fake)
        self._clear()
        assert seen["label"] == "Disk usage clear"
        assert background_jobs.exclusive_active() is False

    def test_released_when_it_fails(self, tree, recycled, monkeypatch):
        with pytest.raises(ConflictError):
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=1,
                      expected_file_count=1)
        assert background_jobs.exclusive_active() is False

    def test_someone_else_holding_it_refuses(self, tree, recycled):
        assert background_jobs.acquire_exclusive("Restore")
        try:
            with pytest.raises(ConflictError):
                self._clear()
        finally:
            background_jobs.release_exclusive()
        assert recycled == []

    def test_a_job_in_another_process_is_seen_under_the_hold(self, tree, recycled, monkeypatch):
        from services import library_admin_service
        monkeypatch.setattr(library_admin_service, "_any_job_running", lambda: True)
        with pytest.raises(ConflictError):
            self._clear()
        assert recycled == [] and background_jobs.exclusive_active() is False

    def test_a_cli_run_holding_the_gpu_lock_is_seen_under_the_hold(self, tree, recycled,
                                                                    monkeypatch):
        """The CLI writes no job_records; its GPU steps leave a gpu_lock row."""
        from services import library_admin_service
        monkeypatch.setattr(library_admin_service, "_any_job_running", lambda: False)
        assert dus._busy_under_hold() is False
        assert db.try_acquire_gpu_lock("cli-test-holder", "CLI transcribe")
        try:
            assert dus._busy_under_hold() is True
            with pytest.raises(ConflictError):
                self._clear()
            with pytest.raises(ConflictError):
                dus.move("library/backups/auto", str(tree), confirm=True)
        finally:
            db.release_gpu_lock("cli-test-holder")
        assert recycled == [] and background_jobs.exclusive_active() is False
        assert dus._busy_under_hold() is False

    def test_an_unreadable_gpu_lock_table_counts_as_busy(self, tree, monkeypatch):
        from services import library_admin_service
        monkeypatch.setattr(library_admin_service, "_any_job_running", lambda: False)

        def boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "gpu_lock_status", boom)
        assert dus._busy_under_hold() is True

    def test_a_job_that_appears_after_the_look_stops_the_recycle(self, tree, recycled, monkeypatch):
        from services import library_admin_service
        answers = iter([False, True])        # under the hold, then the last look
        monkeypatch.setattr(library_admin_service, "_any_job_running", lambda: next(answers, True))
        with pytest.raises(ConflictError):
            self._clear()
        assert recycled == [] and background_jobs.exclusive_active() is False

    def test_move_takes_the_hold_and_rechecks_other_processes(self, tree, tmp_path, monkeypatch):
        from services import library_admin_service
        monkeypatch.setattr(library_admin_service, "_any_job_running", lambda: True)
        with pytest.raises(ConflictError):
            dus.move("library/backups/auto", str(tmp_path), confirm=True)
        assert background_jobs.exclusive_active() is False

    def test_move_inspects_under_the_hold_and_releases_it_before_the_setting_call(
            self, tree, tmp_path, monkeypatch):
        labels = []
        real_inspect, real_set = dus._inspect_for_change, abs_.set_settings

        def inspect(*a, **k):
            labels.append(background_jobs._exclusive_label)
            return real_inspect(*a, **k)

        def set_settings(**kw):
            labels.append(background_jobs._exclusive_label)
            return real_set(**kw)
        monkeypatch.setattr(dus, "_inspect_for_change", inspect)
        monkeypatch.setattr(abs_, "set_settings", set_settings)
        dest = tmp_path / "d"
        dest.mkdir()
        dus.move("library/backups/auto", str(dest), confirm=True)
        assert labels == ["Disk usage move", None]
        assert background_jobs.exclusive_active() is False

    def test_move_is_refused_while_someone_else_holds_the_library(self, tree, tmp_path):
        assert background_jobs.acquire_exclusive("Restore")
        try:
            with pytest.raises(ConflictError):
                dus.move("library/backups/auto", str(tmp_path), confirm=True)
        finally:
            background_jobs.release_exclusive()


class TestRecycleBlockerFailsClosed:                       # M2
    GB = 1024 ** 3

    @pytest.mark.parametrize("nuke", [None, "0", "unreadable", 2, -1, True])
    def test_unknown_nuke_on_delete(self, nuke):
        assert dus.recycle_blocker(10, 3, nuke, 1000, 100 * self.GB)

    @pytest.mark.parametrize("cap", ["unreadable", "100", -5, 1.5])
    def test_non_integer_max_capacity(self, cap):
        assert dus.recycle_blocker(10, 3, 0, cap, 100 * self.GB)

    @pytest.mark.parametrize("policy", [1, 2, "unreadable", None])
    def test_no_recycle_files_policy(self, policy):
        assert dus.recycle_blocker(10, 3, 0, 1000, 100 * self.GB, no_recycle_policy=policy)
        assert dus.recycle_blocker(10, 3, 0, 1000, 100 * self.GB, no_recycle_policy=0) is None

    def test_size_policy_caps_the_limit(self):
        assert dus.recycle_blocker(10 * self.GB, 3, 0, 50_000, 100 * self.GB,
                                   policy_size_percent=5)
        assert dus.recycle_blocker(1 * self.GB, 3, 0, 50_000, 100 * self.GB,
                                   policy_size_percent=5) is None
        for bad in ("unreadable", 101, -1):
            assert dus.recycle_blocker(1, 3, 0, 50_000, 100 * self.GB, policy_size_percent=bad)
        assert dus.recycle_blocker(1, 3, 0, 50_000, None, policy_size_percent=5)

    def test_windows_reader_without_winreg_or_volume_key_refuses(self, monkeypatch):
        """Registry read errors map to values the blocker refuses."""
        class Key:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Reg:
            HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = 1, 2
            tree = {}

            @classmethod
            def OpenKey(cls, hive, sub):
                if (hive, sub) not in cls.tree:
                    raise FileNotFoundError
                return Key(cls.tree[(hive, sub)])

            @staticmethod
            def QueryValueEx(key, name):
                if name not in key.data:
                    raise FileNotFoundError
                v = key.data[name]
                if isinstance(v, OSError):
                    raise v
                return v, 4
        vol = r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{AB-12}"
        pol = r"Software\Microsoft\Windows\CurrentVersion\Policies\Explorer"
        assert dus._read_reg(Reg, 1, vol, "NukeOnDelete") == (False, None)
        Reg.tree = {(1, vol): {"NukeOnDelete": 0, "MaxCapacity": PermissionError(5, "x")}, (2, pol): {}}
        assert dus._read_reg(Reg, 1, vol, "NukeOnDelete") == (True, 0)
        assert dus._read_reg(Reg, 1, vol, "MaxCapacity") == (True, dus._UNREADABLE_VALUE)
        assert dus._read_reg(Reg, 1, vol, "Nope") == (True, None)
        assert dus.recycle_blocker(1, 3, 0, dus._UNREADABLE_VALUE, 100 * self.GB)
        assert dus.recycle_blocker(1, 3, 0, None, 100 * self.GB, dus._UNREADABLE_VALUE)


class TestIncompleteMeasure:                               # M3
    def _break_scandir(self, monkeypatch, needle, exc=PermissionError):
        real = os.scandir

        def broken(path):
            if os.path.abspath(str(path)).endswith(needle):
                raise exc(13, "denied")
            return real(path)
        monkeypatch.setattr(os, "scandir", broken)

    def test_unreadable_subfolder_is_incomplete_and_protected(self, tree, recycled, monkeypatch):
        _write(os.path.join(tree, "library", "tmp", "deep", "f.bin"), 10)
        self._break_scandir(monkeypatch, os.path.join("tmp", "deep"))
        item = _item(dus.scan("library"), "tmp")
        assert item["complete"] is False and item["protected"] is True
        with pytest.raises(InvalidInputError):
            dus.clear("library/tmp", confirm=True, expected_size_bytes=item["size_bytes"],
                      expected_file_count=item["file_count"])
        assert recycled == []

    def test_unreadable_entry_stat_marks_incomplete(self, tree, monkeypatch):
        _write(os.path.join(tree, "library", "tmp", "a.bin"), 10)
        calls = {}

        class Entry:
            name, path = "a.bin", "x"

            def stat(self, follow_symlinks=True):
                raise PermissionError(13, "denied")

        class It:
            def __iter__(self):
                return self

            def __next__(self):
                if calls:
                    raise StopIteration
                calls["n"] = 1
                return Entry()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        monkeypatch.setattr(os, "scandir", lambda p: It())
        m = dus._measure("somewhere", ("library", "tmp"), dus._Budget())
        assert m.unreadable is True

    def test_iteration_error_marks_incomplete(self, tree, monkeypatch):
        class It:
            def __iter__(self):
                return self

            def __next__(self):
                raise OSError(5, "io")

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        monkeypatch.setattr(os, "scandir", lambda p: It())
        assert dus._measure("somewhere", (), dus._Budget()).unreadable is True

    def test_a_vanished_file_is_not_an_error(self, tree):
        assert dus._measure(os.path.join(tree, "nope"), (), dus._Budget()).unreadable is False


class TestProtectionDepth:                                 # M4
    @pytest.mark.parametrize("rel", ["library/profiles/site", "library/profiles/site/Default",
                                     "library/profiles/site/Default/Cookies", "launcher/baihe.pid",
                                     "launcher/logs", "launcher/logs/run.log"])
    def test_everything_below_is_protected(self, tree, recycled, rel):
        _write(os.path.join(tree, *rel.split("/"), "f.txt") if rel.count("/") < 1 or "." not in rel.rsplit("/", 1)[1]
               else os.path.join(tree, *rel.split("/")), 5)
        parent, name = rel.rsplit("/", 1)
        item = _item(dus.scan(parent), name)
        assert item["protected"] is True
        with pytest.raises(InvalidInputError):
            dus.clear(rel, confirm=True, expected_size_bytes=item["size_bytes"],
                      expected_file_count=item["file_count"])
        assert recycled == []

    def test_launcher_folder_itself_protected(self, tree):
        _write(os.path.join(tree, "launcher", "x.lock"), 5)
        assert _item(dus.scan(""), "launcher")["protected"] is True


class TestScanLimits:                                      # L1
    def test_context_is_built_once_per_scan(self, tree, monkeypatch):
        calls = {"prot": 0, "abs": 0}
        real_prot, real_get = dus._protected_paths, abs_.get_settings

        def prot():
            calls["prot"] += 1
            return real_prot()

        def get():
            calls["abs"] += 1
            return real_get()
        monkeypatch.setattr(dus, "_protected_paths", prot)
        monkeypatch.setattr(abs_, "get_settings", get)
        for i in range(20):
            _write(os.path.join(tree, "library", "dramas", f"d{i}", "a.bin"), 1)
        dus.scan("library/dramas")
        assert calls["prot"] == 1 and calls["abs"] == 1

    def test_items_are_capped(self, tree, monkeypatch):
        monkeypatch.setattr(dus, "MAX_ITEMS", 3)
        for i in range(8):
            _write(os.path.join(tree, "library", "tmp", f"f{i}.bin"), 1)
        res = dus.scan("library/tmp")
        assert len(res["items"]) == 3 and res["not_shown"] == 5
        assert res["partial"] is True and res["partial_reason"] == "items"

    def test_children_are_not_described_after_the_budget_is_hit(self, tree, monkeypatch):
        for i in range(6):
            _write(os.path.join(tree, "library", "tmp", f"d{i}", "a.bin"), 1)
        monkeypatch.setattr(dus, "MAX_ENTRIES", 2)
        described = []
        real = dus._describe
        monkeypatch.setattr(dus, "_describe", lambda *a, **k: described.append(1) or real(*a, **k))
        res = dus.scan("library/tmp")
        assert res["partial_reason"] == "entries"
        assert len(described) < 6 and res["not_shown"] == 6 - len(described)

    def test_only_one_scan_at_a_time(self, tree):
        assert dus._scan_lock.acquire(blocking=False)
        try:
            with pytest.raises(ConflictError) as exc:
                dus.scan("")
            assert exc.value.message == dus.SCAN_BUSY
        finally:
            dus._scan_lock.release()
        assert dus.scan("")["items"]            # released again


class TestLinksInside:                                     # L3
    def test_folder_with_a_link_inside_is_not_cleared(self, tree, tmp_path, recycled):
        outside = tmp_path / "out"
        outside.mkdir()
        link = os.path.join(tree, "library", "source_cache", "ln")
        try:
            os.symlink(str(outside), link, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        item = _item(dus.scan("library"), "source_cache")
        assert item["contains_link"] is True
        with pytest.raises(InvalidInputError) as exc:
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=item["size_bytes"],
                      expected_file_count=item["file_count"])
        assert "link or junction" in exc.value.message
        assert recycled == [] and outside.exists()

    def test_reparse_point_attribute_counts_as_a_link(self, tree, monkeypatch):
        st_obj = type("S", (), {"st_mode": 0o040755, "st_size": 0, "st_file_attributes": 0x400})()
        assert dus._is_link_stat(st_obj)


class TestIrreplaceableBackups:                            # L4
    def test_backups_need_the_extra_confirm(self, tree, recycled):
        _write(os.path.join(tree, "library", "backups", "manual", "m.zip"), 30)
        lib = dus.scan("library")
        b = _item(lib, "backups")
        assert b["irreplaceable"] is True and "backups" in b["irreplaceable_note"].lower()
        sub = _item(dus.scan("library/backups"), "manual")
        assert sub["irreplaceable"] is True
        with pytest.raises(ConflictError) as exc:
            dus.clear("library/backups/manual", confirm=True, expected_size_bytes=sub["size_bytes"],
                      expected_file_count=sub["file_count"])
        assert exc.value.details["reason"] == "needs_irreplaceable_confirm"
        assert recycled == []
        res = dus.clear("library/backups/manual", confirm=True, expected_size_bytes=sub["size_bytes"],
                        expected_file_count=sub["file_count"], confirm_irreplaceable=True)
        assert res["freed_bytes"] == 30

    def test_exports_stay_regenerable(self, tree):
        _write(os.path.join(tree, "library", "backups", "exports", "e.zip"), 9)
        ex = _item(dus.scan("library/backups"), "exports")
        assert ex["irreplaceable"] is False and ex["regenerable"]
        assert _item(dus.scan("library/backups/exports"), "e.zip")["irreplaceable"] is False


class TestBroadDataRoot:                                   # L5
    def test_home_folder_as_data_root_is_read_only(self, tree, recycled, monkeypatch, tmp_path):
        monkeypatch.setattr(dus, "_home_dirs", lambda: [os.path.realpath(tree)])
        res = dus.scan("library")
        assert res["items"] and all(i["protected"] for i in res["items"])
        assert dus.ROOT_TOO_BROAD in {i["protected_reason"] for i in res["items"]}
        with pytest.raises(InvalidInputError) as exc:
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert exc.value.message == dus.ROOT_TOO_BROAD
        with pytest.raises(InvalidInputError):
            dus.move("library/backups/auto", str(tmp_path), confirm=True)
        assert recycled == []

    def test_drive_root_as_data_root(self, tmp_path):
        assert dus._root_blocker(os.path.abspath(os.sep)) == dus.ROOT_TOO_BROAD
        assert dus._root_blocker(str(tmp_path)) is None


class TestSecondReview:
    GB = 1024 ** 3

    # item 2: paths Windows can't open are unreadable, only a confirmed-gone entry is skipped
    def test_name_ending_in_space_or_dot_is_unreadable(self, tree, recycled):
        for name in ("trail ", "dot."):
            _write(os.path.join(tree, "library", "tmp", name, "f.bin"), 3)
            m = dus._measure(os.path.join(tree, "library", "tmp"), ("library", "tmp"),
                             dus._Budget())
            assert m.unreadable is True
            item = _item(dus.scan("library"), "tmp")
            assert item["complete"] is False and item["protected"] is True
            with pytest.raises(InvalidInputError):
                dus.clear("library/tmp", confirm=True, expected_size_bytes=item["size_bytes"],
                          expected_file_count=item["file_count"])
            import shutil
            shutil.rmtree(os.path.join(tree, "library", "tmp", name))
        assert recycled == []

    def test_path_of_max_path_length_is_unreadable(self, tree, monkeypatch):
        _write(os.path.join(tree, "library", "tmp", "a.bin"), 3)
        folder = os.path.join(tree, "library", "tmp")
        assert dus._unopenable_name("x" * 260, "x") and not dus._unopenable_name("x" * 259, "x")
        assert dus._measure(folder, ("library", "tmp"), dus._Budget()).unreadable is False
        monkeypatch.setattr(dus, "MAX_PATH", len(os.path.join(folder, "a.bin")))
        assert dus._measure(folder, ("library", "tmp"), dus._Budget()).unreadable is True

    def _fake_scandir(self, monkeypatch, path, name):
        class Entry:
            def __init__(self):
                self.name, self.path = name, path

            def stat(self, follow_symlinks=True):
                raise FileNotFoundError(2, "not found")

        class It:
            done = False

            def __iter__(self):
                return self

            def __next__(self):
                if self.done:
                    raise StopIteration
                self.done = True
                return Entry()

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False
        monkeypatch.setattr(os, "scandir", lambda p: It())

    def test_not_found_on_an_entry_that_still_exists_is_unreadable(self, tree, monkeypatch):
        _write(os.path.join(tree, "library", "tmp", "here.bin"), 3)
        self._fake_scandir(monkeypatch, os.path.join(tree, "library", "tmp", "here.bin"),
                           "here.bin")
        m = dus._measure(os.path.join(tree, "library", "tmp"), ("library", "tmp"), dus._Budget())
        assert m.unreadable is True

    def test_not_found_on_an_entry_confirmed_gone_is_skipped(self, tree, monkeypatch):
        os.makedirs(os.path.join(tree, "library", "tmp"), exist_ok=True)
        self._fake_scandir(monkeypatch, os.path.join(tree, "library", "tmp", "gone.bin"),
                           "gone.bin")
        m = dus._measure(os.path.join(tree, "library", "tmp"), ("library", "tmp"), dus._Budget())
        assert m.unreadable is False

    def test_not_found_on_scandir_of_an_existing_folder_is_unreadable(self, tree, monkeypatch):
        folder = os.path.join(tree, "library", "tmp")
        os.makedirs(folder, exist_ok=True)

        def nf(p):
            raise FileNotFoundError(2, "nf")
        monkeypatch.setattr(os, "scandir", nf)
        assert dus._measure(folder, ("library", "tmp"), dus._Budget()).unreadable is True

    def test_recycle_flags_warn_before_a_permanent_delete(self):
        assert dus.RECYCLE_FLAGS & 0x4000           # FOF_WANTNUKEWARNING
        assert dus.RECYCLE_FLAGS & 0x0040           # FOF_ALLOWUNDO
        assert dus.RECYCLE_FLAGS == (0x0040 | 0x0010 | 0x0004 | 0x0400 | 0x2000 | 0x4000)

    # item 3
    def test_max_capacity_zero_refuses(self):
        assert dus.recycle_blocker(1, 3, 0, 0, 100 * self.GB)
        assert dus.recycle_blocker(1, 3, 0, 1000, 100 * self.GB) is None
        assert dus.recycle_blocker(1, 3, 0, None, 100 * self.GB) is None

    def _bin_env(self, vol_guid, mounted):
        seen = {"roots": []}

        class K32:
            def GetDriveTypeW(self, root):
                seen["roots"].append(("type", root))
                return 3

            def GetVolumeNameForVolumeMountPointW(self, root, buf, n):
                seen["roots"].append(("name", root))
                buf.value = "\\\\?\\Volume" + vol_guid + "\\"
                return 1

        class Key:
            def __init__(self, data):
                self.data = data

            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

        class Reg:
            HKEY_CURRENT_USER, HKEY_LOCAL_MACHINE = 1, 2
            tree = {(1, r"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume"
                        "\\" + vol_guid): {"NukeOnDelete": 0, "MaxCapacity": 2048}}

            @classmethod
            def OpenKey(cls, hive, sub):
                if (hive, sub) not in cls.tree:
                    raise FileNotFoundError
                return Key(cls.tree[(hive, sub)])

            @staticmethod
            def QueryValueEx(key, name):
                if name not in key.data:
                    raise FileNotFoundError
                return key.data[name], 4
        return seen, K32(), Reg

    def test_bin_facts_come_from_the_volume_mounted_into_a_folder(self, monkeypatch):
        seen, k32, reg = self._bin_env("{AB-12}", True)
        monkeypatch.setattr(dus.shutil, "disk_usage",
                            lambda r: seen["roots"].append(("size", r)) or
                            type("U", (), {"total": 7 * self.GB})())
        facts = dus._windows_bin_facts("D:\\Mounts\\Data\\x", lambda p: "D:\\Mounts\\Data\\",
                                       k32, reg)
        assert facts == (3, 0, 2048, 7 * self.GB, 0, None)
        assert {r for _, r in seen["roots"]} == {"D:\\Mounts\\Data\\"}

    def test_bin_facts_fail_closed_when_the_volume_cant_be_resolved(self):
        seen, k32, reg = self._bin_env("{AB-12}", False)

        def boom(p):
            raise OSError("no")
        facts = dus._windows_bin_facts("D:\\x", boom, k32, reg)
        assert dus.recycle_blocker(1, *facts)
        assert dus.recycle_blocker(1, *dus._windows_bin_facts("D:\\x", lambda p: "", k32, reg))
        assert seen["roots"] == []

    # item 4
    def test_data_root_holding_home_or_a_shell_folder_is_refused(self, tmp_path, monkeypatch):
        home = tmp_path / "Users" / "me"
        (home / "Documents" / "Baihe").mkdir(parents=True)
        (home / "Other").mkdir()
        monkeypatch.setattr(dus, "_home_dirs", lambda: [os.path.realpath(home)])
        for root in (tmp_path / "Users", tmp_path / "Users" / "me", home / "Documents",
                     home / "Downloads", home / "Desktop"):
            assert dus._root_blocker(os.path.realpath(root)) == dus.ROOT_TOO_BROAD, root
        assert dus._root_blocker(os.path.realpath(home / "Documents" / "Baihe")) is None
        assert dus._root_blocker(os.path.realpath(home / "Other")) is None

    def test_top_level_entries_baihe_did_not_create_are_protected(self, tree, recycled):
        _write(os.path.join(tree, "Photos", "holiday", "a.jpg"), 50)
        _write(os.path.join(tree, "saved_comics", "ch1.cbz"), 40)
        res = dus.scan("")
        photos = _item(res, "Photos")
        assert photos["protected"] is True and photos["protected_reason"] == dus.NOT_BAIHE
        assert photos["movable"]["supported"] is False
        assert _item(dus.scan("Photos"), "holiday")["protected"] is True
        for rel in ("Photos", "Photos/holiday"):
            with pytest.raises(InvalidInputError):
                dus.clear(rel, confirm=True, expected_size_bytes=50, expected_file_count=1)
        for name in ("library", "model_cache", "saved_comics"):
            assert _item(res, name)["protected_reason"] != dus.NOT_BAIHE
        assert recycled == []

    def test_installer_entries_are_in_the_allow_list(self):
        from installer import service as installer_service
        assert installer_service.DATA_FOLDER_ENTRIES <= dus._baihe_top_level_names()
        assert "saved_comics" in dus._baihe_top_level_names()

    # item 5
    def test_source_profiles_and_saved_comics_need_the_irreplaceable_confirm(self, tree, recycled):
        _write(os.path.join(tree, "library", "source_profiles", "example.com.json"), 12)
        _write(os.path.join(tree, "saved_comics", "ch1.cbz"), 40)
        prof = _item(dus.scan("library"), "source_profiles")
        comics = _item(dus.scan(""), "saved_comics")
        assert prof["irreplaceable_note"] == dus.SOURCE_PROFILES_NOTE
        assert comics["irreplaceable_note"] == dus.SAVED_COMICS_NOTE
        assert _item(dus.scan("saved_comics"), "ch1.cbz")["irreplaceable"] is True
        for rel, item in (("library/source_profiles", prof), ("saved_comics", comics)):
            args = dict(confirm=True, expected_size_bytes=item["size_bytes"],
                        expected_file_count=item["file_count"])
            with pytest.raises(ConflictError) as exc:
                dus.clear(rel, **args)
            assert exc.value.details["reason"] == "needs_irreplaceable_confirm"
            assert recycled == [] or all(not c[0].endswith(rel.split("/")[-1]) for c in recycled)
        assert dus.clear("saved_comics", confirm=True, confirm_irreplaceable=True,
                         expected_size_bytes=comics["size_bytes"],
                         expected_file_count=comics["file_count"])["freed_bytes"] == 40

    def test_save_folder_name_matches_the_save_service(self):
        from services import sources_save_service
        assert dus.SAVED_COMICS_DIRNAME == sources_save_service.SAVE_DIRNAME

    # item 6
    def test_listing_stops_at_the_budget_before_sorting_everything(self, tree, monkeypatch):
        monkeypatch.setattr(dus, "MAX_ITEMS", 2)
        monkeypatch.setattr(dus, "MAX_ENTRIES", 6)
        for i in range(40):
            _write(os.path.join(tree, "library", "tmp", f"f{i:02}.bin"), 1)
        res = dus.scan("library/tmp")
        assert len(res["items"]) == 2 and res["partial_reason"] == "entries"
        assert 1 <= res["not_shown"] <= 6

    def test_an_item_gone_before_the_inspect_is_a_404(self, tree, monkeypatch):
        monkeypatch.setattr(dus, "_resolve", lambda parts: os.path.join(tree, "library", "nope"))
        with pytest.raises(NotFoundError):
            dus._inspect_for_change("library/nope", "clearing")

    def test_an_item_gone_at_the_last_look_is_a_409(self, tree, recycled, monkeypatch):
        import shutil
        target = os.path.join(tree, "library", "source_cache")
        real_resolve, calls = dus._resolve, {"n": 0}

        def resolve(parts):
            calls["n"] += 1
            return real_resolve(parts) if calls["n"] == 1 else target

        real_busy, busy = dus._busy_under_hold, {"n": 0}

        def last_look():
            busy["n"] += 1
            if busy["n"] == 2:
                shutil.rmtree(target)
            return real_busy()
        monkeypatch.setattr(dus, "_resolve", resolve)
        monkeypatch.setattr(dus, "_busy_under_hold", last_look)
        with pytest.raises(ConflictError) as exc:
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert exc.value.details["reason"] == "changed"
        assert recycled == [] and background_jobs.exclusive_active() is False
