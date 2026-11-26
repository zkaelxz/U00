"""The synced extension folder: the one place a browser loads the extension from, kept
equal to the copy the app ships so the extension's self-reload turns an app update into an
extension update."""
import json
import os

import pytest

pytest.importorskip("fastapi")

from services import extension_service as svc
from lib import file_manager


@pytest.fixture
def shipped(tmp_path, monkeypatch):
    src = tmp_path / "shipped"
    (src / "icons").mkdir(parents=True)
    (src / "manifest.json").write_text(json.dumps({"version": "1.0.1"}))
    (src / "background.js").write_text("// new")
    (src / "icons" / "icon-16.png").write_bytes(b"png")
    (src / "verify_end_to_end.py").write_text("dev only")
    monkeypatch.setattr(svc, "_EXTENSION_DIR", str(src))
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path / "data"))
    return src


def test_first_sync_copies_what_a_browser_loads_and_skips_dev_files(shipped):
    assert svc.sync_extension_folder() is True
    target = svc.synced_folder()
    assert sorted(os.listdir(target)) == ["background.js", "icons", "manifest.json"]
    assert os.path.isfile(os.path.join(target, "icons", "icon-16.png"))


def test_an_unchanged_folder_is_left_alone(shipped):
    svc.sync_extension_folder()
    assert svc.sync_extension_folder() is False


def test_an_app_update_replaces_changed_files_and_drops_removed_ones(shipped):
    svc.sync_extension_folder()
    target = svc.synced_folder()
    (shipped / "manifest.json").write_text(json.dumps({"version": "1.0.2"}))
    (shipped / "background.js").unlink()
    (shipped / "popup.js").write_text("// added")
    assert svc.sync_extension_folder() is True
    assert json.load(open(os.path.join(target, "manifest.json")))["version"] == "1.0.2"
    assert sorted(os.listdir(target)) == ["icons", "manifest.json", "popup.js"]
    assert not any(n.endswith(".tmp") for n in os.listdir(target))


def test_nothing_is_made_when_the_app_has_no_extension_files(shipped, tmp_path, monkeypatch):
    monkeypatch.setattr(svc, "_EXTENSION_DIR", str(tmp_path / "missing"))
    assert svc.sync_extension_folder() is False
    assert not os.path.exists(svc.synced_folder())


def test_open_folder_opens_it_or_hands_the_path_to_the_pc_owner(shipped, monkeypatch):
    opened = []
    monkeypatch.setattr(file_manager, "open_folder", opened.append)
    assert svc.open_synced_folder() == {"opened": True}
    assert opened == [svc.synced_folder()]

    def no_desktop(_folder):
        raise file_manager.NoDesktopError("no desktop session")
    monkeypatch.setattr(file_manager, "open_folder", no_desktop)
    assert svc.open_synced_folder() == {"opened": False, "folder": svc.synced_folder()}
