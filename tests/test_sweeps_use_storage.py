"""The drama tombstone, lncrawl work-folder and auto-backup leftover sweeps go through
storage._sweep_folder, so a folder another Baihe process marked as in use is kept."""
import os
import subprocess
import sys
import time

import pytest

import db
import storage
from services import auto_backup_service as abs_
from services import drama_service, lncrawl_service

DAY = 24 * 3600


def _age(path, seconds):
    old = time.time() - seconds
    os.utime(path, (old, old))


def _mark_held(folder, pid):
    with open(os.path.join(folder, storage.HOLD_MARKER), "w", encoding="utf-8") as fh:
        fh.write(str(pid))
    _age(folder, 3 * DAY)       # the folder is old; only the marker should save it


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def _tombstone(isolated_db):
    drama = db.create_drama(title_en="gone")
    os.makedirs(db.DRAMAS_DIR, exist_ok=True)
    path = os.path.join(db.DRAMAS_DIR, f"{drama + 1000}.deleting-0123abcd")
    os.makedirs(path)
    return path


def _workdir(isolated_db):
    drama = db.create_drama(title_en="D")
    path = os.path.join(db.drama_dir(drama), ".lncrawl_old")
    os.makedirs(path)
    return path


def _restore_staging(isolated_db):
    os.makedirs(db.DRAMAS_DIR, exist_ok=True)
    path = os.path.join(db.DRAMAS_DIR, ".restoring-0123abcd")
    os.makedirs(path)
    return path


SWEEPS = [
    ("tombstone", _tombstone, drama_service.cleanup_stale_tombstones),
    ("lncrawl", _workdir, lncrawl_service.cleanup_stale_workdirs),
    ("restore staging", _restore_staging, abs_.cleanup_stale_leftovers),
]


@pytest.mark.parametrize("name, make, sweep", SWEEPS, ids=[s[0] for s in SWEEPS])
class TestHoldMarker:
    def test_a_folder_another_live_process_holds_is_kept(self, isolated_db, name, make, sweep):
        path = make(isolated_db)
        _mark_held(path, os.getppid())
        sweep()
        assert os.path.isdir(path)

    def test_a_marker_of_a_dead_process_does_not_protect_the_folder(self, isolated_db, name, make, sweep):
        path = make(isolated_db)
        _mark_held(path, _dead_pid())
        sweep()
        assert not os.path.exists(path)

    def test_a_folder_this_process_is_holding_is_kept(self, isolated_db, name, make, sweep):
        path = make(isolated_db)
        with storage.holding(path):
            _age(path, 3 * DAY)     # the marker write just refreshed the folder's mtime
            sweep()
            assert os.path.isdir(path)


def test_a_linked_dramas_folder_is_still_swept(isolated_db, tmp_path):
    """Moving the library's dramas to another drive with a link must not switch the sweeps off."""
    real = tmp_path / "real_dramas"
    real.mkdir()
    old = real / "5.deleting-0123abcd"
    old.mkdir()
    _age(old, 3 * DAY)
    if os.path.isdir(db.DRAMAS_DIR):
        os.rmdir(db.DRAMAS_DIR)
    try:
        os.symlink(real, db.DRAMAS_DIR, target_is_directory=True)
    except OSError:
        pytest.skip("symlinks need privileges here")
    assert drama_service.cleanup_stale_tombstones() == 1
    assert not old.exists()
