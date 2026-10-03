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


@pytest.fixture
def tree(isolated_db):
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
    def test_no_recycle_bin_off_windows_deletes_nothing(self, tree):
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
        assert dus.recycle_blocker(10 * self.GB, 3, None, None, 100 * self.GB)
        assert dus.recycle_blocker(1 * self.GB, 3, None, None, 100 * self.GB) is None

    def test_unknown_size_refuses(self):
        assert dus.recycle_blocker(10, 3, None, None, None)


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
