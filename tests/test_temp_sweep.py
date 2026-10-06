"""The library temp folder: job work folders and partial files live in one
place, a startup sweep clears stale ones, and backups skip it."""

import os
import time
import zipfile

import pytest

import background_jobs
import db
import storage
from services import library_admin_service as las


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_old_entries_removed_fresh_kept(isolated_db):
    old_dir = storage.new_workdir("gone_job")
    open(os.path.join(old_dir, "f.bin"), "w").close()
    old_file = storage.new_partial_file("export")
    fresh = storage.new_workdir("other_job")
    _age(old_dir, 2 * 86400)
    _age(old_file, 2 * 86400)
    assert storage.sweep_stale_temp() == 2
    assert not os.path.exists(old_dir) and not os.path.exists(old_file)
    assert os.path.isdir(fresh)


def test_running_job_entry_kept(isolated_db):
    mine = storage.new_workdir("urlmedia_7")
    _age(mine, 3 * 86400)
    with background_jobs._lock:
        background_jobs._jobs["urlmedia_7"] = {"status": "queued"}
    try:
        assert storage.sweep_stale_temp() == 0
        assert os.path.isdir(mine)
    finally:
        with background_jobs._lock:
            background_jobs._jobs.pop("urlmedia_7", None)
    assert storage.sweep_stale_temp() == 1


def test_symlink_not_followed(isolated_db, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    link = os.path.join(storage.temp_root(), "evil~1")
    try:
        os.symlink(outside, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    inner = storage.new_workdir("j")
    os.symlink(outside, os.path.join(inner, "inner_link"), target_is_directory=True)
    storage.sweep_stale_temp(now=time.time() + 10 * 86400)
    assert (outside / "keep.txt").exists()
    assert os.path.islink(link)
    assert not os.path.exists(inner)


def test_temp_root_symlink_is_not_swept(isolated_db, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "a~1").write_text("x")
    os.rmdir(storage.temp_root())
    try:
        os.symlink(outside, os.path.join(db.LIBRARY_DIR, storage.TEMP_DIRNAME),
                   target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    assert storage.sweep_stale_temp(now=time.time() + 10 * 86400) == 0
    assert (outside / "a~1").exists()


def test_backup_excludes_temp_folder(isolated_db, tmp_path):
    wd = storage.new_workdir("job")
    with open(os.path.join(wd, "partial.bin"), "w") as f:
        f.write("x")
    with open(os.path.join(db.LIBRARY_DIR, "keep.txt"), "w") as f:
        f.write("k")
    dest = str(tmp_path / "b.zip")
    las.write_backup_zip(dest, include_media=True)
    names = zipfile.ZipFile(dest).namelist()
    assert "keep.txt" in names
    assert not any(n.split("/")[0] == storage.TEMP_DIRNAME for n in names)


def test_restore_keeps_temp_name_excluded():
    from services import workspace_job_service as wjs
    assert storage.TEMP_DIRNAME in wjs.restore_kept_names()
