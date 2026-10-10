"""
services/disk_usage_service.py: scan, protected paths, clear into the Trash
folder, restore and permanent delete from it, and the backup-folder move.
Temp folders only.
"""

import json
import os

import pytest

import background_jobs
import db
from services import auto_backup_service as abs_
from services import disk_usage_service as dus
from services import disk_usage_trash_service as dts
from services.service_errors import (ConflictError, InvalidInputError, NotFoundError,
                                     ServiceError, UnsupportedOperationError)


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(b"x" * size)


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
    return root


@pytest.fixture
def renames(monkeypatch):
    """Every atomic move into or out of Trash, recorded; the move still happens."""
    calls = []
    real = dus._rename

    def spy(src, dst):
        calls.append((src, dst))
        real(src, dst)
    monkeypatch.setattr(dus, "_rename", spy)
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

    def test_kept_media_is_listed_as_title_media(self, tree):
        # A replaced or failed upload is the user's original: never offered
        # as rebuildable, and Clear needs the title-media confirm.
        _write(os.path.join(db.LIBRARY_DIR, "dramas", "1", "kept_media", "replaced-20261006-120000.mp4"), 50)
        kept = _item(dus.scan("library/dramas/1"), "kept_media")
        assert kept["regenerable"] is None and kept["irreplaceable"] is True
        item = _item(dus.scan("library/dramas/1/kept_media"), "replaced-20261006-120000.mp4")
        assert item["regenerable"] is None and item["irreplaceable"] is True

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
    def test_clear_refuses_protected(self, tree, renames, path):
        with pytest.raises(InvalidInputError):
            dus.clear(path, confirm=True, expected_size_bytes=0, expected_file_count=0)
        assert renames == []

    def test_move_refuses_protected(self, tree, tmp_path):
        with pytest.raises(InvalidInputError):
            dus.move("library/library.db", str(tmp_path), confirm=True)


class TestPathValidation:
    @pytest.mark.parametrize("bad", [
        "..", "../x", "library/../..", "/etc", "\\Windows", "C:\\Users", "C:", "library/a:b",
        "library/..", "a//b", "library/x.", "library/x ", "li*brary", "a\x00b", "library/.",
    ])
    def test_traversal_and_odd_names_refused(self, tree, renames, bad):
        with pytest.raises(InvalidInputError):
            dus.scan(bad)
        with pytest.raises(InvalidInputError):
            dus.clear(bad, confirm=True, expected_size_bytes=0, expected_file_count=0)
        assert renames == []

    def test_symlink_out_of_the_data_folder_refused(self, tree, tmp_path, renames):
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
        assert renames == [] and (outside / "sub" / "f.txt").exists()


class TestClear:
    def _seen(self, rel):
        item = next(i for i in dus.scan(os.path.dirname(rel))["items"] if i["path"] == rel)
        return item["size_bytes"], item["file_count"]

    def test_moves_into_trash_with_a_manifest_and_frees_nothing(self, tree, renames):
        size, files = self._seen("library/source_cache")
        res = dus.clear("library/source_cache", confirm=True, expected_size_bytes=size,
                        expected_file_count=files)
        entry = os.path.join(tree, dus.TRASH_DIRNAME, res["trash_id"])
        assert res["moved_bytes"] == 400 and res["name"] == "source_cache"
        assert res["file_count"] == 1 and res["kind"] == "folder"
        assert dus._TRASH_ID_RE.fullmatch(res["trash_id"])
        assert len(renames) == 1 and renames[0][1] == os.path.join(entry, "payload")
        assert not os.path.exists(os.path.join(tree, "library", "source_cache"))
        assert os.path.getsize(os.path.join(entry, "payload", "x.html")) == 400
        manifest = json.load(open(os.path.join(entry, "manifest.json"), encoding="utf-8"))
        assert manifest["original_path"] == "library/source_cache"
        assert (manifest["kind"], manifest["size_bytes"], manifest["file_count"]) == ("folder", 400, 1)
        assert manifest["trashed_at"]
        assert "baihe_trash" not in json.dumps(res) and tree not in json.dumps(res)

    def test_a_file_goes_to_trash_too(self, tree):
        res = dus.clear("library/source_cache/x.html", confirm=True, expected_size_bytes=400,
                        expected_file_count=1)
        listed = dts.trash_list()["items"]
        assert [(i["id"], i["kind"], i["original_path_relative"]) for i in listed] == [
            (res["trash_id"], "file", "library/source_cache/x.html")]

    def test_a_failed_rename_leaves_the_item_and_no_empty_entry(self, tree, monkeypatch):
        def boom(src, dst):
            raise OSError(18, "cross-device " + src)
        monkeypatch.setattr(dus, "_rename", boom)
        with pytest.raises(ServiceError) as exc:
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert tree not in exc.value.message
        assert os.path.exists(os.path.join(tree, "library", "source_cache", "x.html"))
        assert os.listdir(os.path.join(tree, dus.TRASH_DIRNAME)) == []

    def test_another_volume_is_refused_without_copying(self, tree, renames, monkeypatch):
        monkeypatch.setattr(dus, "_same_volume", lambda a, b: False)
        with pytest.raises(ServiceError):
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert renames == [] and os.path.exists(os.path.join(tree, "library", "source_cache"))

    def test_a_path_that_would_reach_max_path_in_trash_is_refused(self, tree, renames, monkeypatch):
        monkeypatch.setattr(dus, "MAX_PATH", len(os.path.join(tree, "library", "source_cache",
                                                              "x.html")) + 1)
        monkeypatch.setattr(dus, "_unopenable_name", lambda p, n: False)
        with pytest.raises(ServiceError):
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert renames == []

    def test_needs_confirm_and_the_seen_sizes(self, tree, renames):
        with pytest.raises(InvalidInputError):
            dus.clear("library/source_cache", confirm=False, expected_size_bytes=400,
                      expected_file_count=1)
        with pytest.raises(InvalidInputError):
            dus.clear("library/source_cache", confirm=True)
        assert renames == []

    def test_changed_since_seen_is_a_409(self, tree, renames):
        _write(os.path.join(tree, "library", "source_cache", "new.html"), 50)
        with pytest.raises(ConflictError) as exc:
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert exc.value.details["reason"] == "changed"
        assert exc.value.details["size_bytes"] == 450
        assert renames == []

    def test_title_media_needs_the_extra_confirm(self, tree, renames):
        args = dict(confirm=True, expected_size_bytes=1000, expected_file_count=1)
        with pytest.raises(ConflictError) as exc:
            dus.clear("library/dramas/2", **args)
        assert exc.value.details["reason"] == "needs_irreplaceable_confirm"
        assert renames == []
        assert dus.clear("library/dramas/2", confirm_irreplaceable=True, **args)["moved_bytes"] == 1000

    def test_regenerable_inside_a_title_needs_no_extra_confirm(self, tree, renames):
        res = dus.clear("library/dramas/1/dub_clips", confirm=True, expected_size_bytes=700,
                        expected_file_count=1)
        assert res["moved_bytes"] == 700

    def test_refused_while_a_job_runs(self, tree, renames, monkeypatch):
        monkeypatch.setattr(background_jobs, "active_job_ids", lambda: ["transcribe_1"])
        with pytest.raises(ConflictError) as exc:
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                      expected_file_count=1)
        assert "running" in exc.value.message
        assert renames == []
        assert dus.scan("")["busy_reason"]

    def test_refused_during_maintenance(self, tree, renames, monkeypatch):
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
        monkeypatch.setattr(dus, "_rename", lambda a, b: called.append(a))
        try:
            with pytest.raises((ConflictError, InvalidInputError)):
                dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                          expected_file_count=1)
        except OSError:
            pytest.skip("no symlinks here")
        assert called == [] and (outside / "f").exists()


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

class TestExclusiveHold:                                   # M1
    def _clear(self):
        return dus.clear("library/source_cache", confirm=True, expected_size_bytes=400,
                         expected_file_count=1)

    def test_the_hold_is_taken_during_the_change_and_released_after(self, tree, monkeypatch):
        seen = {}
        real = dus._rename

        def spy(src, dst):
            seen["label"] = background_jobs._exclusive_label
            real(src, dst)
        monkeypatch.setattr(dus, "_rename", spy)
        self._clear()
        assert seen["label"] == "Disk usage clear"
        assert background_jobs.exclusive_active() is False

    def test_released_when_it_fails(self, tree, renames, monkeypatch):
        with pytest.raises(ConflictError):
            dus.clear("library/source_cache", confirm=True, expected_size_bytes=1,
                      expected_file_count=1)
        assert background_jobs.exclusive_active() is False

    def test_someone_else_holding_it_refuses(self, tree, renames):
        assert background_jobs.acquire_exclusive("Restore")
        try:
            with pytest.raises(ConflictError):
                self._clear()
        finally:
            background_jobs.release_exclusive()
        assert renames == []

    def test_a_job_in_another_process_is_seen_under_the_hold(self, tree, renames, monkeypatch):
        from services import library_admin_service
        monkeypatch.setattr(library_admin_service, "any_job_running", lambda: True)
        with pytest.raises(ConflictError):
            self._clear()
        assert renames == [] and background_jobs.exclusive_active() is False

    def test_a_cli_run_holding_the_gpu_lock_is_seen_under_the_hold(self, tree, renames,
                                                                    monkeypatch):
        """The CLI writes no job_records; its GPU steps leave a gpu_lock row."""
        from services import library_admin_service
        monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)
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
        assert renames == [] and background_jobs.exclusive_active() is False
        assert dus._busy_under_hold() is False

    def test_an_unreadable_gpu_lock_table_counts_as_busy(self, tree, monkeypatch):
        from services import library_admin_service
        monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)

        def boom():
            raise RuntimeError("locked")
        monkeypatch.setattr(db, "gpu_lock_status", boom)
        assert dus._busy_under_hold() is True

    def test_a_job_that_appears_after_the_look_stops_the_move(self, tree, renames, monkeypatch):
        from services import library_admin_service
        answers = iter([False, True])        # under the hold, then the last look
        monkeypatch.setattr(library_admin_service, "any_job_running", lambda: next(answers, True))
        with pytest.raises(ConflictError):
            self._clear()
        assert renames == [] and background_jobs.exclusive_active() is False

    def test_move_takes_the_hold_and_rechecks_other_processes(self, tree, tmp_path, monkeypatch):
        from services import library_admin_service
        monkeypatch.setattr(library_admin_service, "any_job_running", lambda: True)
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


class TestIncompleteMeasure:                               # M3
    def _break_scandir(self, monkeypatch, needle, exc=PermissionError):
        real = os.scandir

        def broken(path):
            if os.path.abspath(str(path)).endswith(needle):
                raise exc(13, "denied")
            return real(path)
        monkeypatch.setattr(os, "scandir", broken)

    def test_unreadable_subfolder_is_incomplete_and_protected(self, tree, renames, monkeypatch):
        _write(os.path.join(tree, "library", "tmp", "deep", "f.bin"), 10)
        self._break_scandir(monkeypatch, os.path.join("tmp", "deep"))
        item = _item(dus.scan("library"), "tmp")
        assert item["complete"] is False and item["protected"] is True
        with pytest.raises(InvalidInputError):
            dus.clear("library/tmp", confirm=True, expected_size_bytes=item["size_bytes"],
                      expected_file_count=item["file_count"])
        assert renames == []

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
    def test_everything_below_is_protected(self, tree, renames, rel):
        _write(os.path.join(tree, *rel.split("/"), "f.txt") if rel.count("/") < 1 or "." not in rel.rsplit("/", 1)[1]
               else os.path.join(tree, *rel.split("/")), 5)
        parent, name = rel.rsplit("/", 1)
        item = _item(dus.scan(parent), name)
        assert item["protected"] is True
        with pytest.raises(InvalidInputError):
            dus.clear(rel, confirm=True, expected_size_bytes=item["size_bytes"],
                      expected_file_count=item["file_count"])
        assert renames == []

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
    def test_folder_with_a_link_inside_is_not_cleared(self, tree, tmp_path, renames):
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
        assert renames == [] and outside.exists()

    def test_reparse_point_attribute_counts_as_a_link(self, tree, monkeypatch):
        st_obj = type("S", (), {"st_mode": 0o040755, "st_size": 0, "st_file_attributes": 0x400})()
        assert dus._is_link_stat(st_obj)


class TestIrreplaceableBackups:                            # L4
    def test_backups_need_the_extra_confirm(self, tree, renames):
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
        assert renames == []
        res = dus.clear("library/backups/manual", confirm=True, expected_size_bytes=sub["size_bytes"],
                        expected_file_count=sub["file_count"], confirm_irreplaceable=True)
        assert res["moved_bytes"] == 30

    def test_exports_stay_regenerable(self, tree):
        _write(os.path.join(tree, "library", "backups", "exports", "e.zip"), 9)
        ex = _item(dus.scan("library/backups"), "exports")
        assert ex["irreplaceable"] is False and ex["regenerable"]
        assert _item(dus.scan("library/backups/exports"), "e.zip")["irreplaceable"] is False


class TestBroadDataRoot:                                   # L5
    def test_home_folder_as_data_root_is_read_only(self, tree, renames, monkeypatch, tmp_path):
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
        assert renames == []

    def test_drive_root_as_data_root(self, tmp_path):
        assert dus._root_blocker(os.path.abspath(os.sep)) == dus.ROOT_TOO_BROAD
        assert dus._root_blocker(str(tmp_path)) is None


class TestSecondReview:
    GB = 1024 ** 3

    # item 2: paths Windows can't open are unreadable, only a confirmed-gone entry is skipped
    def test_name_ending_in_space_or_dot_is_unreadable(self, tree, renames):
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
        assert renames == []

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

    def test_top_level_entries_baihe_did_not_create_are_protected(self, tree, renames):
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
        assert renames == []

    def test_installer_entries_are_in_the_allow_list(self):
        from installer import service as installer_service
        assert installer_service.DATA_FOLDER_ENTRIES <= dus._baihe_top_level_names()
        assert "saved_comics" in dus._baihe_top_level_names()

    # item 5
    def test_source_profiles_and_saved_comics_need_the_irreplaceable_confirm(self, tree, renames):
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
            assert renames == [] or all(not c[0].endswith(rel.split("/")[-1]) for c in renames)
        assert dus.clear("saved_comics", confirm=True, confirm_irreplaceable=True,
                         expected_size_bytes=comics["size_bytes"],
                         expected_file_count=comics["file_count"])["moved_bytes"] == 40

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

    def test_an_item_gone_at_the_last_look_is_a_409(self, tree, renames, monkeypatch):
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
        assert renames == [] and background_jobs.exclusive_active() is False


# --------------------------------------------------------------------------
# Trash: protection, restore, permanent delete
# --------------------------------------------------------------------------

def _trash_one(rel="library/source_cache", size=400, files=1, **kw):
    return dus.clear(rel, confirm=True, expected_size_bytes=size, expected_file_count=files, **kw)


def _trash_dir(tree):
    return os.path.join(tree, dus.TRASH_DIRNAME)


def _abs(path, kw):
    """The full path of an os.unlink/os.rmdir call, also when the POSIX walk
    names the entry relative to an open folder (dir_fd)."""
    fd = kw.get("dir_fd")
    return path if fd is None else os.path.join(os.readlink(f"/proc/self/fd/{fd}"), path)


def _empty(**over):
    """trash_empty with the count and size the list shows."""
    seen = dts.trash_list()
    args = {"expected_item_count": seen["item_count"], "expected_size_bytes": seen["size_bytes"]}
    args.update(over)
    return dts.trash_empty("DELETE", **args)


class TestTrashProtection:
    def test_the_trash_folder_is_in_the_baihe_allow_list(self):
        assert dus.TRASH_DIRNAME in dus._baihe_top_level_names()

    def test_installer_allows_the_trash_folder(self):
        from installer import service as installer_service
        assert dus.TRASH_DIRNAME in installer_service.DATA_FOLDER_ENTRIES

    def test_trash_root_and_everything_in_it_is_protected_and_cannot_be_cleared_or_moved(
            self, tree, renames, tmp_path):
        done = _trash_one()
        res = dus.scan("")
        top = _item(res, dus.TRASH_DIRNAME)
        assert top["protected"] and top["protected_reason"] == dus.TRASH_PROTECTED
        assert top["movable"]["supported"] is False
        entry = dus.scan(dus.TRASH_DIRNAME)
        assert _item(entry, done["trash_id"])["protected"]
        inner = f"{dus.TRASH_DIRNAME}/{done['trash_id']}"
        before = len(renames)
        for rel in (dus.TRASH_DIRNAME, inner, inner + "/payload", inner + "/manifest.json",
                    inner + "/payload/x.html"):
            with pytest.raises(InvalidInputError):
                dus.clear(rel, confirm=True, expected_size_bytes=0, expected_file_count=0)
            with pytest.raises(InvalidInputError):
                dus.move(rel, str(tmp_path), confirm=True)
        assert len(renames) == before
        assert os.path.exists(os.path.join(_trash_dir(tree), done["trash_id"], "payload", "x.html"))

    def test_scan_reports_the_trash_block(self, tree):
        assert dus.scan("")["trash"] == {"size_bytes": 0, "item_count": 0, "partial": False}
        _trash_one()
        _trash_one("library/dramas/2", 1000, 1, confirm_irreplaceable=True)
        assert dus.scan("library")["trash"] == {"size_bytes": 1400, "item_count": 2,
                                                "partial": False}

    def test_a_link_as_the_trash_root_is_refused_everywhere(self, tree, tmp_path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        try:
            os.symlink(str(outside), _trash_dir(tree), target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        with pytest.raises(ServiceError):
            _trash_one()
        for call in (dts.trash_list, lambda: dts.trash_empty("DELETE", 0, 0)):
            with pytest.raises(ServiceError):
                call()
        assert os.listdir(outside) == []
        assert dus.scan("")["trash"]["partial"] is True


class TestTrashRestore:
    def test_restores_a_folder_to_its_old_place(self, tree, renames):
        done = _trash_one()
        item = dts.trash_list()["items"][0]
        assert item["restorable"] is True and item["size_bytes"] == 400
        res = dts.trash_restore(done["trash_id"], confirm=True)
        assert res == {"name": "source_cache", "kind": "folder", "size_bytes": 400, "file_count": 1}
        assert os.path.getsize(os.path.join(tree, "library", "source_cache", "x.html")) == 400
        assert os.listdir(_trash_dir(tree)) == []
        assert renames[-1][0].endswith("payload")

    def test_restores_a_file(self, tree):
        done = _trash_one("library/source_cache/x.html")
        dts.trash_restore(done["trash_id"], confirm=True)
        assert os.path.getsize(os.path.join(tree, "library", "source_cache", "x.html")) == 400

    def _conflict(self, trash_id):
        with pytest.raises(ConflictError) as exc:
            dts.trash_restore(trash_id, confirm=True)
        assert exc.value.details["reason"] == "cannot_restore"
        return exc.value.message

    def test_needs_confirm(self, tree):
        done = _trash_one()
        with pytest.raises(InvalidInputError):
            dts.trash_restore(done["trash_id"])
        assert not os.path.exists(os.path.join(tree, "library", "source_cache"))

    def test_the_old_folder_is_gone_409(self, tree):
        done = _trash_one("library/dramas/1/dub_clips", 700, 1)
        import shutil
        shutil.rmtree(os.path.join(tree, "library", "dramas", "1"))
        assert dts.trash_list()["items"][0]["restorable"] is False
        assert "folder it came from is gone" in self._conflict(done["trash_id"])
        assert os.path.exists(os.path.join(_trash_dir(tree), done["trash_id"], "payload"))

    def test_the_place_is_taken_409(self, tree):
        done = _trash_one()
        _write(os.path.join(tree, "library", "source_cache", "new.html"), 3)
        assert "same name" in self._conflict(done["trash_id"])
        assert os.path.getsize(os.path.join(tree, "library", "source_cache", "new.html")) == 3

    def test_the_old_parent_became_a_link_409(self, tree, tmp_path):
        done = _trash_one("library/dramas/1/dub_clips", 700, 1)
        import shutil
        outside = tmp_path / "out"
        outside.mkdir()
        shutil.rmtree(os.path.join(tree, "library", "dramas", "1"))
        try:
            os.symlink(str(outside), os.path.join(tree, "library", "dramas", "1"),
                       target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        self._conflict(done["trash_id"])
        assert os.listdir(outside) == []

    def test_a_job_running_409_and_nothing_moves(self, tree, monkeypatch):
        done = _trash_one()
        monkeypatch.setattr(background_jobs, "active_job_ids", lambda: ["transcribe_1"])
        with pytest.raises(ConflictError) as exc:
            dts.trash_restore(done["trash_id"], confirm=True)
        assert "running" in exc.value.message
        assert not os.path.exists(os.path.join(tree, "library", "source_cache"))
        assert background_jobs.exclusive_active() is False

    def test_a_tampered_manifest_cannot_aim_at_a_protected_or_outside_place(self, tree):
        done = _trash_one()
        manifest = os.path.join(_trash_dir(tree), done["trash_id"], "manifest.json")
        for original in ("library/library.db", ".env", "../escape", "/etc/x", "C:/x",
                         f"{dus.TRASH_DIRNAME}/{done['trash_id']}/payload/x", "Photos/a",
                         "library/profiles/a"):
            data = json.load(open(manifest, encoding="utf-8"))
            data["original_path"] = original
            json.dump(data, open(manifest, "w", encoding="utf-8"))
            with pytest.raises(ConflictError):
                dts.trash_restore(done["trash_id"], confirm=True)
        assert os.path.exists(os.path.join(_trash_dir(tree), done["trash_id"], "payload"))
        assert not os.path.exists(os.path.join(tree, "..", "escape"))

    def test_a_missing_or_damaged_manifest_cannot_be_restored_but_can_be_listed(self, tree):
        done = _trash_one()
        manifest = os.path.join(_trash_dir(tree), done["trash_id"], "manifest.json")
        open(manifest, "w").write("{not json")
        item = dts.trash_list()["items"][0]
        assert item["restorable"] is False and item["original_path_relative"] is None
        assert item["size_bytes"] == 400
        with pytest.raises(ConflictError):
            dts.trash_restore(done["trash_id"], confirm=True)

    @pytest.mark.parametrize("bad", ["..", "../x", "a/b", "a\\b", "/etc", "C:\\x", "",
                                     "20260101-000000-abcdefg1", "20260101-000000-abcdef12\n"])
    def test_bad_ids_are_refused(self, tree, bad):
        with pytest.raises((InvalidInputError, NotFoundError)):
            dts.trash_restore(bad, confirm=True)



class TestTrashPurge:
    def _purge(self, trash_id, size, word="DELETE"):
        return dts.trash_purge(trash_id, confirm_text=word, expected_size_bytes=size)

    def test_deletes_for_good_and_reports_freed(self, tree):
        done = _trash_one()
        res = self._purge(done["trash_id"], 400)
        assert res == {"freed_bytes": 400, "file_count": 1}
        assert os.listdir(_trash_dir(tree)) == []
        assert os.path.exists(os.path.join(tree, "library", "dramas", "2", "audio.mp3"))
        assert background_jobs.exclusive_active() is False

    @pytest.mark.parametrize("word", ["delete", "Delete", "DELETE ", " DELETE", "", "DELETED", None])
    def test_the_typed_word_must_be_exact(self, tree, word):
        done = _trash_one()
        with pytest.raises(InvalidInputError):
            self._purge(done["trash_id"], 400, word)
        with pytest.raises(InvalidInputError):
            dts.trash_empty(word)
        assert os.path.exists(os.path.join(_trash_dir(tree), done["trash_id"], "payload", "x.html"))

    def test_changed_size_is_a_409(self, tree):
        done = _trash_one()
        _write(os.path.join(_trash_dir(tree), done["trash_id"], "payload", "more.html"), 5)
        with pytest.raises(ConflictError) as exc:
            self._purge(done["trash_id"], 400)
        assert exc.value.details["reason"] == "changed" and exc.value.details["size_bytes"] == 405
        assert os.path.exists(os.path.join(_trash_dir(tree), done["trash_id"], "payload", "x.html"))

    @pytest.mark.parametrize("bad", ["..", "../library", "library", "a/b", "a\\b", "/etc",
                                     "C:\\Windows", "", ".", "baihe_trash",
                                     "20260101-000000-abcdef12/../..", "2026-1-1",
                                     "20260101-000000-ABCDEF12", "20260101-000000-abcdef12\n"])
    def test_ids_that_are_not_trash_entries_are_refused(self, tree, bad):
        keep = os.path.join(tree, "library", "source_cache", "x.html")
        with pytest.raises((InvalidInputError, NotFoundError)):
            self._purge(bad, 0)
        assert os.path.exists(keep)

    def test_a_missing_entry_is_404_and_a_missing_trash_folder_too(self, tree):
        with pytest.raises(NotFoundError):
            self._purge("20260101-000000-abcdef12", 0)
        _trash_one()
        with pytest.raises(NotFoundError):
            self._purge("20260101-000000-abcdef12", 0)

    def test_an_entry_that_is_a_link_is_refused_and_its_target_kept(self, tree, tmp_path):
        outside = tmp_path / "precious"
        outside.mkdir()
        (outside / "f.txt").write_bytes(b"keep")
        _trash_one()
        fake_id = "20260101-000000-abcdef12"
        try:
            os.symlink(str(outside), os.path.join(_trash_dir(tree), fake_id),
                       target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        with pytest.raises(InvalidInputError):
            self._purge(fake_id, 4)
        res = _empty()
        assert res["removed"] == 1 and res["failed"] == 0      # the real entry; a link isn't one
        assert (outside / "f.txt").read_bytes() == b"keep"
        assert os.path.islink(os.path.join(_trash_dir(tree), fake_id))

    def test_refused_while_a_job_runs(self, tree, monkeypatch):
        done = _trash_one()
        monkeypatch.setattr(background_jobs, "active_job_ids", lambda: ["x"])
        with pytest.raises(ConflictError):
            self._purge(done["trash_id"], 400)
        with pytest.raises(ConflictError):
            dts.trash_empty("DELETE", 1, 400)
        assert os.path.exists(os.path.join(_trash_dir(tree), done["trash_id"], "payload"))
        assert background_jobs.exclusive_active() is False

    def test_a_symlink_inside_a_trashed_folder_is_unlinked_and_its_target_untouched(
            self, tree, tmp_path):
        outside = tmp_path / "outside-the-data-folder"
        (outside / "sub").mkdir(parents=True)
        (outside / "sub" / "f.txt").write_bytes(b"precious")
        (outside / "top.txt").write_bytes(b"precious too")
        os.chmod(outside / "top.txt", 0o444)
        # Put the links inside the folder while it sits in Trash, as if one
        # had appeared there afterwards.
        done = _trash_one()
        payload = os.path.join(_trash_dir(tree), done["trash_id"], "payload")
        try:
            os.symlink(str(outside), os.path.join(payload, "dirlink"), target_is_directory=True)
            os.symlink(str(outside / "top.txt"), os.path.join(payload, "filelink"))
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        res = self._purge(done["trash_id"], dts.trash_list()["items"][0]["size_bytes"])
        assert res["freed_bytes"] >= 400
        assert not os.path.lexists(os.path.join(_trash_dir(tree), done["trash_id"]))
        assert (outside / "sub" / "f.txt").read_bytes() == b"precious"
        assert (outside / "top.txt").read_bytes() == b"precious too"
        assert oct(os.stat(outside / "top.txt").st_mode & 0o777) == oct(0o444)
        assert sorted(os.listdir(outside)) == ["sub", "top.txt"]

    def test_the_path_walk_never_enters_a_folder_swapped_for_a_link(self, tree, tmp_path,
                                                                    monkeypatch):
        monkeypatch.setattr(dts, "_fd_walk_supported", lambda: False)     # the Windows walk
        outside = tmp_path / "other"
        outside.mkdir()
        (outside / "f.txt").write_bytes(b"precious")
        _write(os.path.join(tree, "library", "tmp", "deep", "a.bin"), 3)
        done = _trash_one("library/tmp", 3, 1)
        payload = os.path.join(_trash_dir(tree), done["trash_id"], "payload")
        real_plain = dts._plain_dir
        state = {"n": 0}

        def swap_then_check(path):
            state["n"] += 1
            if path.endswith(os.path.join("payload", "deep")) and os.path.isdir(path) \
                    and not os.path.islink(path):
                import shutil
                shutil.rmtree(path)
                os.symlink(str(outside), path, target_is_directory=True)
            return real_plain(path)
        dts._plain_dir = swap_then_check
        try:
            with pytest.raises(OSError):
                dts._remove_tree(os.path.join(_trash_dir(tree), done["trash_id"]))
        except (NotImplementedError,):
            pytest.skip("no symlinks here")
        finally:
            dts._plain_dir = real_plain
        assert (outside / "f.txt").read_bytes() == b"precious"
        assert os.path.exists(payload)

    def test_read_only_files_and_folders_are_removed(self, tree, monkeypatch):
        _write(os.path.join(tree, "library", "tmp", "ro", "a.bin"), 3)
        done = _trash_one("library/tmp", 3, 1)
        entry = os.path.join(_trash_dir(tree), done["trash_id"])
        target_file = os.path.join(entry, "payload", "ro", "a.bin")
        target_dir = os.path.join(entry, "payload", "ro")
        real_unlink, real_rmdir = os.unlink, os.rmdir
        refused = []

        def unlink(path, *a, **k):          # what Windows does to a read-only file
            path = _abs(path, k)
            if path == target_file and not refused:
                refused.append(path)
                raise PermissionError(13, "read-only")
            return real_unlink(path, *a, **k)

        def rmdir(path, *a, **k):
            path = _abs(path, k)
            if path == target_dir and len(refused) == 1:
                refused.append(path)
                raise PermissionError(13, "read-only")
            return real_rmdir(path, *a, **k)
        monkeypatch.setattr(os, "unlink", unlink)
        monkeypatch.setattr(os, "rmdir", rmdir)
        os.chmod(target_file, 0o444)
        self._purge(done["trash_id"], 3)
        assert refused == [target_file, target_dir]
        assert not os.path.lexists(entry)

    def test_a_failure_keeps_what_is_left_and_says_nothing_about_paths(self, tree, monkeypatch):
        _write(os.path.join(tree, "library", "tmp", "b.bin"), 2)
        done = _trash_one("library/tmp", 2, 1)
        entry = os.path.join(_trash_dir(tree), done["trash_id"])
        real_unlink = os.unlink

        def unlink(path, *a, **k):
            if _abs(path, k).endswith("b.bin"):
                raise PermissionError(13, "in use " + path)
            return real_unlink(path, *a, **k)
        monkeypatch.setattr(os, "unlink", unlink)
        with pytest.raises(ServiceError) as exc:
            self._purge(done["trash_id"], 2)
        assert tree not in exc.value.message and "b.bin" not in exc.value.message
        assert os.path.exists(os.path.join(entry, "payload", "b.bin"))
        assert background_jobs.exclusive_active() is False
        # Its record went first, so a half-deleted payload can't be restored.
        item = dts.trash_list()["items"][0]
        assert item["restorable"] is False and item["original_path_relative"] is None
        monkeypatch.setattr(os, "unlink", real_unlink)
        assert self._purge(done["trash_id"], 2)["freed_bytes"] == 2


class TestTrashEmpty:
    def test_empties_every_entry_and_leaves_foreign_names_alone(self, tree):
        _trash_one()
        _trash_one("library/dramas/2", 1000, 1, confirm_irreplaceable=True)
        _write(os.path.join(_trash_dir(tree), "notes.txt"), 7)
        _write(os.path.join(_trash_dir(tree), "20260101-000000-abcdef12.bak", "x"), 1)
        res = _empty()
        assert res == {"freed_bytes": 1400, "removed": 2, "failed": 0}
        assert sorted(os.listdir(_trash_dir(tree))) == ["20260101-000000-abcdef12.bak", "notes.txt"]
        assert os.path.exists(os.path.join(tree, "library", "dramas", "1", "audio.mp3"))

    def test_nothing_to_empty(self, tree):
        assert _empty() == {"freed_bytes": 0, "removed": 0, "failed": 0}
        _trash_one()
        _empty()
        assert _empty()["removed"] == 0

    def test_one_failure_leaves_that_entry_and_removes_the_rest(self, tree, monkeypatch):
        a = _trash_one()
        b = _trash_one("library/dramas/2", 1000, 1, confirm_irreplaceable=True)
        real_unlink = os.unlink

        def unlink(path, *x, **k):
            if os.path.join(a["trash_id"], "payload") in _abs(path, k):
                raise PermissionError(13, "in use")
            return real_unlink(path, *x, **k)
        monkeypatch.setattr(os, "unlink", unlink)
        res = _empty()
        assert (res["removed"], res["failed"], res["freed_bytes"]) == (1, 1, 1000)
        assert os.listdir(_trash_dir(tree)) == [a["trash_id"]]
        assert b["trash_id"] not in os.listdir(_trash_dir(tree))


class TestTrashList:
    def test_newest_first_with_what_each_was(self, tree):
        a = _trash_one()
        b = _trash_one("library/dramas/2", 1000, 1, confirm_irreplaceable=True)
        res = dts.trash_list()
        assert [i["id"] for i in res["items"]] == sorted([a["trash_id"], b["trash_id"]], reverse=True)
        by = {i["id"]: i for i in res["items"]}
        assert by[a["trash_id"]]["original_path_relative"] == "library/source_cache"
        assert by[b["trash_id"]]["size_bytes"] == 1000
        assert res["size_bytes"] == 1400 and res["item_count"] == 2 and res["partial"] is False
        assert all(i["trashed_at"] for i in res["items"])
        assert tree not in json.dumps(res)

    def test_empty_when_there_is_no_trash_folder(self, tree):
        assert dts.trash_list()["items"] == []

    def test_a_running_scan_does_not_hide_the_list(self, tree):
        _trash_one()
        assert dus._scan_lock.acquire(blocking=False)
        try:
            res = dts.trash_list()
        finally:
            dus._scan_lock.release()
        assert res["item_count"] == 1 and res["items"][0]["size_bytes"] == 400


class TestTrashHardening:
    def test_a_trashed_backups_folder_with_db_copies_can_be_restored(self, tree):
        _write(os.path.join(tree, "library", "backups", "database", "library_20260101.db"), 50)
        for rel, size, files in (("library/backups/database", 50, 1), ("library/backups", 250, 2)):
            done = _trash_one(rel, size, files, confirm_irreplaceable=True)
            item = dts.trash_list()["items"][0]
            assert item["restorable"] is True
            dts.trash_restore(done["trash_id"], confirm=True)
            assert os.path.exists(os.path.join(tree, rel))
        assert os.path.exists(os.path.join(tree, "library", "backups", "database",
                                           "library_20260101.db"))

    def test_a_trashed_folder_holding_the_live_database_is_still_refused(self, tree):
        done = _trash_one()
        entry = os.path.join(_trash_dir(tree), done["trash_id"])
        _write(os.path.join(entry, "payload", "library.db"), 10)
        item = dts.trash_list()["items"][0]
        assert item["restorable"] is False
        with pytest.raises(ConflictError) as exc:
            dts.trash_restore(done["trash_id"], confirm=True)
        assert exc.value.details["reason"] == "cannot_restore"
        assert os.path.exists(os.path.join(entry, "payload", "library.db"))

    def test_empty_needs_the_count_and_size_that_were_shown(self, tree):
        _trash_one()
        _trash_one("library/dramas/2", 1000, 1, confirm_irreplaceable=True)
        for bad in ({"expected_item_count": 1}, {"expected_size_bytes": 1399},
                    {"expected_item_count": 3}):
            with pytest.raises(ConflictError) as exc:
                _empty(**bad)
            assert exc.value.details["reason"] == "changed"
        for missing in (None, True, -1):
            with pytest.raises(InvalidInputError):
                _empty(expected_item_count=missing)
        assert len(os.listdir(_trash_dir(tree))) == 2
        assert _empty()["removed"] == 2

    def test_a_manifest_of_nested_brackets_counts_as_damaged(self, tree):
        done = _trash_one()
        manifest = os.path.join(_trash_dir(tree), done["trash_id"], "manifest.json")
        with open(manifest, "w") as fh:
            fh.write("[" * 16000)
        assert dus._read_manifest(os.path.dirname(manifest)) is None
        assert dts.trash_list()["items"][0]["original_path_relative"] is None

    def test_an_id_collision_never_touches_the_other_entry(self, tree, monkeypatch):
        first = _trash_one()
        entry = os.path.join(_trash_dir(tree), first["trash_id"])
        before = open(os.path.join(entry, "manifest.json")).read()
        ids = iter([first["trash_id"]] * 3 + ["20260101-000000-aaaaaaaa"] * 20)
        monkeypatch.setattr(dus, "_new_trash_id", lambda: next(ids))
        second = _trash_one("library/dramas/2", 1000, 1, confirm_irreplaceable=True)
        assert second["trash_id"] == "20260101-000000-aaaaaaaa"
        assert open(os.path.join(entry, "manifest.json")).read() == before
        assert os.path.exists(os.path.join(entry, "payload", "x.html"))

    def test_collisions_on_every_try_fail_and_keep_the_existing_entry(self, tree, monkeypatch):
        first = _trash_one()
        entry = os.path.join(_trash_dir(tree), first["trash_id"])
        monkeypatch.setattr(dus, "_new_trash_id", lambda: first["trash_id"])
        with pytest.raises(ServiceError):
            _trash_one("library/dramas/2", 1000, 1, confirm_irreplaceable=True)
        assert os.path.exists(os.path.join(entry, "manifest.json"))
        assert os.path.exists(os.path.join(tree, "library", "dramas", "2", "audio.mp3"))

    @pytest.mark.skipif(os.name == "nt", reason="POSIX restore")
    def test_restore_does_not_overwrite_something_that_appears_at_the_last_moment(
            self, tree, monkeypatch):
        done = _trash_one("library/source_cache/x.html")
        real = dts._restore_target

        def target_then_squat(manifest, meas, ctx):
            dest, reason = real(manifest, meas, ctx)
            with open(dest, "wb") as fh:
                fh.write(b"someone else's")
            return dest, reason
        monkeypatch.setattr(dts, "_restore_target", target_then_squat)
        with pytest.raises(ConflictError):
            dts.trash_restore(done["trash_id"], confirm=True)
        assert open(os.path.join(tree, "library", "source_cache", "x.html"), "rb").read() \
            == b"someone else's"
        assert os.path.exists(os.path.join(_trash_dir(tree), done["trash_id"], "payload"))

    @pytest.mark.skipif(os.name == "nt", reason="POSIX restore")
    def test_a_folder_restore_does_not_replace_an_empty_folder_made_meanwhile(self, tree):
        src = os.path.join(tree, "src_dir")
        dst = os.path.join(tree, "dst_dir")
        os.makedirs(src)
        os.makedirs(dst)
        with pytest.raises(OSError):
            dus._rename_no_overwrite(src, dst, "folder")
        assert os.path.isdir(src) and os.path.isdir(dst)

    @pytest.mark.skipif(os.name == "nt", reason="POSIX walk")
    def test_a_folder_swapped_for_a_link_after_listing_is_not_followed(self, tree, tmp_path,
                                                                      monkeypatch):
        outside = tmp_path / "outside"
        outside.mkdir()
        (outside / "f.txt").write_bytes(b"precious")
        _write(os.path.join(tree, "library", "tmp", "deep", "a.bin"), 3)
        done = _trash_one("library/tmp", 3, 1)
        entry = os.path.join(_trash_dir(tree), done["trash_id"])
        real_open = os.open
        swapped = []

        def open_after_swap(path, flags, *a, **k):
            if path == "deep" and k.get("dir_fd") is not None and not swapped:
                fd = k["dir_fd"]
                base = os.readlink(f"/proc/self/fd/{fd}")
                import shutil
                shutil.rmtree(os.path.join(base, "deep"))
                os.symlink(str(outside), os.path.join(base, "deep"), target_is_directory=True)
                swapped.append(1)
            return real_open(path, flags, *a, **k)
        monkeypatch.setattr(os, "open", open_after_swap)
        with pytest.raises(OSError):
            dts._remove_tree(entry)
        monkeypatch.setattr(os, "open", real_open)
        assert swapped and (outside / "f.txt").read_bytes() == b"precious"

    def test_readonly_clearing_never_follows_a_link_and_skips_folders_without_nofollow(
            self, tree, tmp_path, monkeypatch):
        target = tmp_path / "t.txt"
        target.write_bytes(b"x")
        os.chmod(target, 0o444)
        link = tmp_path / "ln"
        try:
            os.symlink(str(target), str(link))
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        with pytest.raises(OSError):
            dts._clear_readonly(str(link))
        assert oct(os.stat(target).st_mode & 0o777) == oct(0o444)
        folder = tmp_path / "d"
        folder.mkdir()
        os.chmod(folder, 0o500)
        calls = []
        monkeypatch.setattr(os, "supports_follow_symlinks", set())
        monkeypatch.setattr(os, "chmod", lambda *a, **k: calls.append((a, k)))
        dts._clear_readonly(str(folder))
        assert calls == []
        dts._clear_readonly(str(target))
        assert len(calls) == 1 and "follow_symlinks" not in calls[0][1]
        monkeypatch.undo()
        os.chmod(folder, 0o700)

    def test_a_list_past_its_budget_shows_unknown_size_and_purge_still_works(
            self, tree, monkeypatch):
        a = _trash_one()
        b = _trash_one("library/dramas/2", 1000, 1, confirm_irreplaceable=True)
        monkeypatch.setattr(dus, "MAX_ENTRIES", 1)
        res = dts.trash_list()
        assert res["partial"] is True
        unknown = [i for i in res["items"] if i["size_bytes"] is None]
        assert len(unknown) == 1 and unknown[0]["file_count"] is None
        uid = unknown[0]["id"]
        with pytest.raises(ConflictError):          # a size was claimed that can't be verified
            dts.trash_purge(uid, confirm_text="DELETE", expected_size_bytes=1)
        assert dts.trash_purge(uid, confirm_text="DELETE", expected_size_bytes=None)
        assert uid in (a["trash_id"], b["trash_id"]) and uid not in os.listdir(_trash_dir(tree))

    def test_the_root_scan_walks_the_trash_once(self, tree, monkeypatch):
        _trash_one()
        trash = _trash_dir(tree)
        walked = []
        real = dus._measure
        monkeypatch.setattr(dus, "_measure", lambda p, *a, **k: (walked.append(p), real(p, *a, **k))[1])
        res = dus.scan("")
        assert trash not in walked
        top = _item(res, dus.TRASH_DIRNAME)
        assert top["size_bytes"] == 400 and top["file_count"] == 1

    def test_the_list_reads_nothing_through_a_link(self, tree, tmp_path, monkeypatch):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (outside / "manifest.json").write_text("{}")
        _trash_one()
        fake = os.path.join(_trash_dir(tree), "20260101-000000-abcdef12")
        try:
            os.symlink(str(outside), fake, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip("no symlinks here")
        read = []
        real = dus._read_manifest
        monkeypatch.setattr(dts, "_read_manifest", lambda e: (read.append(e), real(e))[1])
        res = dts.trash_list()
        assert res["item_count"] == 1 and fake not in read
