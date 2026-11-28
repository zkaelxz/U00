"""services/log_copy_service.py: the weekly scrubbed log copy is off until a folder is set, writes once a
week, keeps four and refuses folders it must not use."""
import os

import pytest

import db
from lib.errors import InvalidInputError
from services import diagnostics_gaps_service as gaps
from services import log_copy_service as svc

WEEK = svc.INTERVAL_SECONDS


@pytest.fixture
def folder(isolated_db, tmp_path, monkeypatch):
    monkeypatch.setattr(gaps, "build_support_bundle", lambda: b"PK-fake-zip")
    out = tmp_path / "copies"
    out.mkdir()
    return out


def _zips(folder):
    return sorted(p.name for p in folder.iterdir() if p.suffix == ".zip" and p.name.startswith("baihe-weekly-logs-"))


def test_off_until_a_folder_is_picked(folder):
    assert svc.overview()["enabled"] is False
    assert svc.periodic_tick(now=10 * WEEK) is False
    assert _zips(folder) == []


def test_writes_once_a_week(folder):
    svc.set_folder(str(folder))
    assert svc.periodic_tick(now=1_000_000) is True
    assert svc.periodic_tick(now=1_000_000 + WEEK - 1) is False
    assert svc.periodic_tick(now=1_000_000 + WEEK + 5) is True
    assert len(_zips(folder)) == 2
    assert (folder / _zips(folder)[0]).read_bytes() == b"PK-fake-zip"


def test_keeps_the_newest_four(folder):
    svc.set_folder(str(folder))
    for i in range(6):
        svc.write_copy(now=1_700_000_000 + i * 86400)
    assert len(_zips(folder)) == svc.KEEP
    assert _zips(folder)[-1].startswith("baihe-weekly-logs-") and not any(n.endswith(".part") for n in os.listdir(folder))


def test_turning_it_off_keeps_the_copies(folder):
    svc.set_folder(str(folder))
    svc.write_copy(now=1_700_000_000)
    svc.set_folder("")
    assert svc.overview()["enabled"] is False
    assert len(_zips(folder)) == 1


@pytest.mark.parametrize("bad", ["relative/dir", "/does/not/exist/at/all", "a\x00b", 5])
def test_bad_folders_are_refused(folder, bad):
    with pytest.raises(InvalidInputError):
        svc.set_folder(bad)


def test_the_library_and_program_folders_are_refused(folder):
    with pytest.raises(InvalidInputError):
        svc.set_folder(os.path.join(db.LIBRARY_DIR))
    with pytest.raises(InvalidInputError):
        svc.set_folder(os.path.dirname(os.path.dirname(os.path.abspath(svc.__file__))))


def test_a_failing_build_never_raises(folder, monkeypatch):
    svc.set_folder(str(folder))
    monkeypatch.setattr(gaps, "build_support_bundle", lambda: (_ for _ in ()).throw(OSError("disk")))
    assert svc.periodic_tick(now=1_000_000) is False


def test_the_overview_names_the_folder_but_never_its_path(folder):
    svc.set_folder(str(folder))
    shown = svc.overview()
    assert shown["folder_name"] == "copies" and str(folder) not in repr(shown)


def test_hand_saved_send_logs_zips_are_never_rotated(folder):
    (folder / "baihe-logs-20260101-000000.zip").write_bytes(b"mine")
    svc.set_folder(str(folder))
    for i in range(6):
        svc.write_copy(now=1_700_000_000 + i * 86400)
    assert (folder / "baihe-logs-20260101-000000.zip").exists()


def test_an_unreachable_folder_is_retried_hourly_and_builds_nothing(folder, monkeypatch):
    svc.set_folder(str(folder))
    built = []
    monkeypatch.setattr(gaps, "build_support_bundle", lambda: built.append(1) or b"x")
    folder.rmdir()
    assert svc.periodic_tick(now=1_000_000) is False
    assert svc.periodic_tick(now=1_000_000 + 60) is False
    assert built == []
    folder.mkdir()
    assert svc.periodic_tick(now=1_000_000 + svc.RETRY_SECONDS + 1) is True
    assert built == [1]


def test_a_verbatim_windows_prefix_is_not_a_way_around_the_guards(folder, monkeypatch):
    monkeypatch.setattr(svc.os.path, "realpath", lambda p: "\\\\?\\" + str(folder))
    monkeypatch.setattr(svc.os.path, "isdir", lambda p: p == str(folder))
    assert svc.set_folder(str(folder)) and svc.get_folder() == str(folder)
