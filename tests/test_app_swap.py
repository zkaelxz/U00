"""
installer/app_swap.py: the app-only update's unpack checks, the folder
switch, rollback, crash recovery and pruning. Folders are real (tmp_path);
the launcher is a fake, so no server or process starts.
"""

import json
import os
import stat
import zipfile
from pathlib import Path

import pytest

from installer import app_swap as sw

OLD, NEW = "0.1.0", "0.2.0"


def _manifest(version=NEW, **over):
    doc = {"product": "Baihe Studio", "layer": "app", "version": version,
           "min_base_version": "0.1.0", "base": {"wheels_sha256": "w"}, "db_schema_sha256": "d"}
    doc.update(over)
    return doc


def _zip(path, entries=None, extra=(), drop=(), manifest=None):
    """A minimal valid app zip, plus `extra` (name, data[, ZipInfo tweak])."""
    entries = dict(entries or {r: f"# {r}\n" for r in sw.REQUIRED_ENTRIES})
    entries[sw.APP_MANIFEST_NAME] = json.dumps(manifest or _manifest())
    for name in drop:
        entries.pop(name)
    with zipfile.ZipFile(path, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
        for item in extra:
            name, data = item[0], item[1]
            info = zipfile.ZipInfo(name)
            if len(item) > 2:
                item[2](info)
            zf.writestr(info, data)
    return path


def _install(root, version=OLD, base_version=None):
    """An install folder: app\\ with a marker file, and manifest.json."""
    app = root / "app"
    (app / "installer").mkdir(parents=True)
    (app / "VERSION").write_text(version)
    manifest = {"product": "Baihe Studio", "app_version": version,
                "python": {"embed_sha256": "p"}, "wheels": [{"file": "a.whl", "sha256": "1"}],
                "services": {"caddy": {"sha256": "c"}, "winsw": {"sha256": "s"}}}
    if base_version:
        manifest["base_version"] = base_version
    (root / "manifest.json").write_text(json.dumps(manifest))
    return root


def _stage(root, version=NEW):
    folder = root / f"app-{version}"
    folder.mkdir()
    (folder / "VERSION").write_text(version)
    sw.write_journal(root, sw.STAGED, OLD, version, OLD)
    return folder


def _current(root):
    return (root / "app" / "VERSION").read_text()


def _app_version(root):
    return json.loads((root / "manifest.json").read_text())["app_version"]


class FakeLauncher:
    def __init__(self, healthy=(True,)):
        self.healthy = list(healthy)
        self.calls = []

    def stop(self):
        self.calls.append("stop")
        return True

    def start_and_check(self, root=None):
        self.calls.append("start")
        return self.healthy.pop(0) if self.healthy else True

    def open_window(self, defer=False):
        self.calls.append("window-defer" if defer else "window")


@pytest.fixture
def root(tmp_path):
    return _install(tmp_path / "Baihe Studio")


# --- the zip --------------------------------------------------------------------

def test_a_good_zip_unpacks_into_a_new_folder(tmp_path):
    z = _zip(tmp_path / "a.zip", extra=[("api/routers/x.py", "x"), ("frontend/dist/assets/", "")])
    dest = tmp_path / "app-0.2.0"
    sw.unpack(z, dest)
    assert (dest / "api" / "routers" / "x.py").read_text() == "x"
    assert (dest / "frontend" / "dist" / "assets").is_dir()
    assert not (tmp_path / "app-0.2.0.partial").exists()
    with zipfile.ZipFile(z) as zf:
        assert sw.read_manifest(zf)["version"] == NEW


@pytest.mark.parametrize("name", [
    "../evil.py", "api/../../evil.py", "/etc/evil", "C:/evil.py", "api\\evil.py",
    "api/./x.py", "api//x.py", "file.py:stream", "aux.py", "con", "api/name. ",
    ".env", "services/.env.local", "library/library.db", "model_cache/x.bin",
    "venv/Scripts/python.exe", "INSTALLED", "sub/PORTABLE"])
def test_unsafe_or_foreign_entries_are_refused(tmp_path, name):
    z = _zip(tmp_path / "a.zip", extra=[(name, "x")])
    with pytest.raises(sw.SwapError, match="refused"):
        sw.unpack(z, tmp_path / "app-0.2.0")
    assert not (tmp_path / "app-0.2.0").exists()
    assert not (tmp_path / "evil.py").exists()
    assert not (tmp_path / "app-0.2.0.partial").exists()


def test_a_symlink_entry_is_refused(tmp_path):
    def as_link(info):
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
    z = _zip(tmp_path / "a.zip", extra=[("api/link", "/etc/passwd", as_link)])
    with pytest.raises(sw.SwapError, match="link"):
        sw.unpack(z, tmp_path / "app-0.2.0")
    assert not (tmp_path / "app-0.2.0").exists()


def test_a_name_twice_in_another_case_is_refused(tmp_path):
    z = _zip(tmp_path / "a.zip", extra=[("API/__main__.py", "x")])
    with pytest.raises(sw.SwapError, match="twice"):
        sw.unpack(z, tmp_path / "app-0.2.0")


def test_missing_required_files_are_refused(tmp_path):
    z = _zip(tmp_path / "a.zip", drop=["installer/launcher.py"])
    with pytest.raises(sw.SwapError, match="missing"):
        sw.unpack(z, tmp_path / "app-0.2.0")


def test_oversize_is_refused_by_the_bytes_written_not_the_declared_size(tmp_path, monkeypatch):
    z = _zip(tmp_path / "a.zip", extra=[("big.bin", b"x" * 5000)])
    with pytest.raises(sw.SwapError, match="larger"):
        sw.unpack(z, tmp_path / "app-0.2.0", cap=4000)
    assert not (tmp_path / "app-0.2.0").exists()
    monkeypatch.setattr(sw, "MAX_UNPACKED_BYTES", 4000)
    with zipfile.ZipFile(z) as zf, pytest.raises(sw.SwapError, match="larger"):
        sw.check_zip(zf)


def test_unpack_never_writes_into_an_existing_folder(tmp_path):
    z = _zip(tmp_path / "a.zip")
    (tmp_path / "app-0.2.0").mkdir()
    with pytest.raises(sw.SwapError, match="already exists"):
        sw.unpack(z, tmp_path / "app-0.2.0")


@pytest.mark.parametrize("over", [{"product": "Other"}, {"layer": "installer"},
                                  {"version": "../x"}, {"base": None}])
def test_a_manifest_that_isnt_an_app_layer_is_refused(tmp_path, over):
    z = _zip(tmp_path / "a.zip", manifest=_manifest(**over))
    with zipfile.ZipFile(z) as zf, pytest.raises(sw.SwapError, match="manifest"):
        sw.read_manifest(zf)


@pytest.mark.parametrize("version", ["", ".", "..", "-1", "1/2", "1\\2", "1 2", "a" * 41, None])
def test_versions_that_cant_be_folder_names(version):
    assert not sw.valid_version(version)


def test_base_fingerprint_ignores_wheel_order_and_sees_any_change():
    one = {"python": {"embed_sha256": "p"}, "services": {"caddy": {"sha256": "c"},
                                                         "winsw": {"sha256": "s"}},
           "wheels": [{"file": "a.whl", "sha256": "1"}, {"file": "b.whl", "sha256": "2"}]}
    two = json.loads(json.dumps(one))
    two["wheels"].reverse()
    assert sw.base_fingerprint(one) == sw.base_fingerprint(two)
    two["wheels"][0]["sha256"] = "3"
    assert sw.base_fingerprint(one) != sw.base_fingerprint(two)
    three = json.loads(json.dumps(one))
    three["services"]["caddy"]["sha256"] = "other"
    assert sw.base_fingerprint(one) != sw.base_fingerprint(three)
    assert sw.base_fingerprint({"wheels": []}) is None


def test_schema_fingerprint_sees_a_new_column_but_not_column_order(tmp_path):
    import sqlite3
    a, b = tmp_path / "a.db", tmp_path / "b.db"
    with sqlite3.connect(a) as c:
        c.execute("CREATE TABLE t (x, y)")
    with sqlite3.connect(b) as c:
        c.execute("CREATE TABLE t (y, x)")
    assert sw.schema_fingerprint_of(a) == sw.schema_fingerprint_of(b)
    with sqlite3.connect(b) as c:
        c.execute("ALTER TABLE t ADD COLUMN z")
    assert sw.schema_fingerprint_of(a) != sw.schema_fingerprint_of(b)


# --- switch and rollback --------------------------------------------------------

def test_a_healthy_update_switches_and_keeps_the_previous_version(root):
    _stage(root)
    launcher = FakeLauncher([True])
    assert sw.apply(root, launcher, sleep=lambda s: None) == "updated"
    assert _current(root) == NEW
    assert (root / "app-0.1.0" / "VERSION").read_text() == OLD
    assert _app_version(root) == NEW
    assert json.loads((root / "manifest.json").read_text())["base_version"] == OLD
    assert sw.read_journal(root) is None
    assert sw.read_result(root)["code"] == "updated"
    assert launcher.calls == ["stop", "start", "window"]


def test_a_failed_health_check_rolls_back_and_starts_the_old_version(root):
    _stage(root)
    launcher = FakeLauncher([False, True])
    assert sw.apply(root, launcher, sleep=lambda s: None) == "rolled_back_health"
    assert _current(root) == OLD
    assert _app_version(root) == OLD
    # The failed version stays aside for the owner to look at, until the next update.
    assert (root / "app-0.2.0" / "VERSION").read_text() == NEW
    assert not (root / "app-0.1.0").exists()
    assert sw.read_journal(root) is None
    assert sw.read_result(root)["code"] == "rolled_back_health"
    assert launcher.calls == ["stop", "start", "stop", "start", "window"]


def test_when_the_old_version_fails_too_it_says_so(root, monkeypatch):
    _stage(root)
    shown = []
    monkeypatch.setattr(sw, "_message", shown.append)
    assert sw.apply(root, FakeLauncher([False, False]), sleep=lambda s: None) == "broken"
    assert _current(root) == OLD
    assert sw.read_result(root)["code"] == "broken" and shown == [sw.BROKEN_MESSAGE]


def test_a_locked_app_folder_queues_the_switch_for_the_next_start(root, monkeypatch):
    _stage(root)
    real = os.rename

    def locked(src, dst):
        if Path(src).name == "app":
            raise PermissionError("in use")
        real(src, dst)
    monkeypatch.setattr(sw.os, "rename", locked)
    launcher = FakeLauncher()
    assert sw.apply(root, launcher, sleep=lambda s: None) == "queued"
    assert _current(root) == OLD and (root / "app-0.2.0").is_dir()
    assert sw.read_journal(root)["state"] == sw.QUEUED
    assert launcher.calls == ["stop", "window-defer"]
    # The next start finds it queued; once nothing holds the folder, it goes through.
    assert sw.recover(root) == "queued"
    monkeypatch.setattr(sw.os, "rename", real)
    assert sw.apply(root, FakeLauncher([True]), sleep=lambda s: None) == "updated"
    assert _current(root) == NEW


def test_a_second_rename_failure_puts_the_first_back(root, monkeypatch):
    _stage(root)
    real = os.rename

    def second_fails(src, dst):
        if Path(src).name == "app-0.2.0":
            raise PermissionError("in use")
        real(src, dst)
    monkeypatch.setattr(sw.os, "rename", second_fails)
    assert sw.apply(root, FakeLauncher(), sleep=lambda s: None) == "queued"
    assert _current(root) == OLD and not (root / "app-0.1.0").exists()


def test_a_missing_staged_folder_rolls_back_without_touching_app(root):
    sw.write_journal(root, sw.STAGED, OLD, NEW, OLD)
    assert sw.apply(root, FakeLauncher(), sleep=lambda s: None) == "rolled_back_switch"
    assert _current(root) == OLD and sw.read_journal(root) is None


def test_nothing_happens_without_a_staged_journal(root):
    launcher = FakeLauncher()
    assert sw.apply(root, launcher) == "not_started"
    assert launcher.calls == [] and _current(root) == OLD


# --- crash recovery -------------------------------------------------------------

def _crash_after(root, steps):
    """The journal and folders as a helper killed after `steps` renames left them."""
    _stage(root)
    sw.write_journal(root, sw.SWITCHING, OLD, NEW, OLD)
    if steps >= 1:
        os.rename(root / "app", root / "app-0.1.0")
    if steps >= 2:
        os.rename(root / "app-0.2.0", root / "app")
        sw.write_journal(root, sw.CHECKING, OLD, NEW, OLD)


@pytest.mark.parametrize("steps", [0, 1, 2])
def test_a_crash_between_switch_steps_recovers_to_the_old_version(root, steps):
    _crash_after(root, steps)
    stopped = []
    assert sw.recover(root, stop=lambda: stopped.append(1), sleep=lambda s: None) == "recovered"
    assert _current(root) == OLD
    assert (root / "app-0.2.0" / "VERSION").read_text() == NEW
    assert not (root / "app-0.1.0").exists()
    assert _app_version(root) == OLD
    assert sw.read_journal(root) is None
    assert sw.read_result(root)["code"] == "recovered"
    assert stopped == [1]


def test_a_crash_after_health_passed_finishes_the_update(root):
    _crash_after(root, 2)
    sw.write_journal(root, sw.DONE, OLD, NEW, OLD)
    assert sw.recover(root) == "updated"
    assert _current(root) == NEW and _app_version(root) == NEW


def test_a_crash_after_the_manifest_was_written_still_rolls_back_cleanly(root):
    _crash_after(root, 2)
    sw.set_installed_version(root, NEW, OLD)
    assert sw.recover(root, sleep=lambda s: None) == "recovered"
    assert _app_version(root) == OLD


def test_a_crash_before_anything_was_switched_just_forgets_the_update(root):
    _stage(root)
    sw.write_journal(root, sw.STOPPING, OLD, NEW, OLD)
    assert sw.recover(root) == "not_started"
    assert _current(root) == OLD and sw.read_journal(root) is None


def test_recovery_leaves_a_running_helper_alone(root):
    _crash_after(root, 1)
    # The lock is per open file, so a second open in this process conflicts
    # just as the helper's would.
    with sw.swap_lock(root):
        assert sw.recover(root, sleep=lambda s: None) == "busy"
    assert not (root / "app").exists()
    assert sw.recover(root, sleep=lambda s: None) == "recovered"


def test_an_unreadable_journal_with_app_in_place_is_dropped(root):
    (root / "app-update").mkdir()
    (root / "app-update" / "swap.json").write_text("{not json")
    assert sw.recover(root) is None
    assert sw.read_journal(root) is None


def test_an_unreadable_journal_without_app_is_reported_broken(root):
    os.rename(root / "app", root / "app-0.1.0")
    (root / "app-update").mkdir()
    (root / "app-update" / "swap.json").write_text(json.dumps({"state": "switching", "old": ".."}))
    assert sw.recover(root) == "broken"


# --- keeping and pruning --------------------------------------------------------

def test_the_next_update_deletes_older_copies_and_keeps_the_previous_one(root):
    _stage(root)
    sw.apply(root, FakeLauncher([True]), sleep=lambda s: None)
    (root / "app-0.2.0.partial").mkdir()
    stage3 = root / "app-0.3.0"
    stage3.mkdir()
    (stage3 / "VERSION").write_text("0.3.0")
    sw.write_journal(root, sw.STAGED, NEW, "0.3.0", OLD)
    assert sw.apply(root, FakeLauncher([True]), sleep=lambda s: None) == "updated"
    assert _current(root) == "0.3.0"
    assert sorted(p.name for p in root.iterdir() if p.name.startswith("app")) == [
        "app", "app-0.2.0", "app-update"]
    # The base installer's version is kept across app-only updates.
    assert json.loads((root / "manifest.json").read_text())["base_version"] == OLD


def test_prune_never_follows_or_deletes_a_link(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("x")
    link = root / "app-9.9.9"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("this system can't make symlinks")
    sw.prune(root, keep=[])
    assert (outside / "keep.txt").exists()
    with pytest.raises(sw.SwapError):
        sw.remove_folder(link, root)


def test_remove_folder_refuses_anything_but_its_own_folders(root):
    for target in (root / "app", root / "python", root.parent / "app-0.1.0"):
        with pytest.raises(sw.SwapError):
            sw.remove_folder(target, root)


def test_data_folders_are_never_touched(root):
    data = root / "data"
    for name in ("library", "model_cache"):
        (data / name).mkdir(parents=True)
    (data / ".env").write_text("KEY=1")
    _stage(root)
    sw.apply(root, FakeLauncher([False, True]), sleep=lambda s: None)
    _stage(root, "0.3.0")
    sw.apply(root, FakeLauncher([True]), sleep=lambda s: None)
    assert (data / ".env").read_text() == "KEY=1"
    assert (data / "library").is_dir() and (data / "model_cache").is_dir()


def test_main_runs_from_the_work_folder(root, monkeypatch):
    seen = []
    monkeypatch.setattr(sw, "recover", lambda r: seen.append(os.getcwd()) or None)
    cwd = os.getcwd()
    try:
        assert sw.main(["recover", "--root", str(root)]) == 0
    finally:
        os.chdir(cwd)
    assert Path(seen[0]) == (root / "app-update").resolve()


def test_helper_command_isolates_the_interpreter(root):
    cmd = sw.helper_command(root, "apply")
    assert cmd[1] == "-I" and cmd[2].endswith(os.path.join("app-update", "app_swap.py"))
    assert cmd[3:] == ["apply", "--root", str(root)]


# --- review fixes: names, steps, stopping, rollback -----------------------------

@pytest.mark.parametrize("version", ["backup", "notes", "1", "1.2.3.4", "v1.2", "1.2-", "1..2"])
def test_only_version_numbers_are_folder_versions(version):
    assert not sw.valid_version(version)


@pytest.mark.parametrize("version", ["0.2", "0.2.0", "1.2.3-rc.1", "1.2.3+build.5"])
def test_release_versions_are_folder_versions(version):
    assert sw.valid_version(version)


def test_prune_leaves_app_folders_that_arent_versions(root):
    for name in ("app-backup", "app-notes", "app-1", "app-backup.partial"):
        (root / name).mkdir()
        (root / name / "mine.txt").write_text("x")
    (root / "app-0.0.9").mkdir()
    sw.prune(root, keep=[])
    for name in ("app-backup", "app-notes", "app-1", "app-backup.partial"):
        assert (root / name / "mine.txt").exists()
    assert not (root / "app-0.0.9").exists()


@pytest.mark.parametrize("name", [
    "Library/library.db", "MODEL_CACHE/x.bin", "Venv/x", ".ENV", "Installed", "sub/Portable",
    "conin$", "CONOUT$.txt", "api/com¹.py", "LPT²", "Com0.py", "lpt9"])
def test_reserved_names_are_refused_in_any_case(tmp_path, name):
    z = _zip(tmp_path / "a.zip", extra=[(name, "x")])
    with pytest.raises(sw.SwapError, match="refused"):
        sw.unpack(z, tmp_path / "app-0.2.0")
    assert not (tmp_path / "app-0.2.0").exists()


def test_the_runonce_entry_also_runs_in_safe_mode():
    # "!" makes Windows delete the value only after the command ran, and run
    # it in Safe Mode as well.
    assert sw.RUNONCE_VALUE.startswith("!")


def test_an_app_that_wont_stop_is_left_running_and_unswitched(root):
    _stage(root)

    class WontStop(FakeLauncher):
        def stop(self):
            self.calls.append("stop")
            return False
    launcher = WontStop()
    assert sw.apply(root, launcher, sleep=lambda s: None) == "not_started"
    assert launcher.calls == ["stop", "window"]
    assert _current(root) == OLD and (root / "app-0.2.0").is_dir()
    assert sw.read_journal(root) is None
    assert sw.read_result(root)["code"] == "not_started"


def test_the_journal_records_each_switch_step(root, monkeypatch):
    _stage(root)
    seen = []
    real = os.rename

    def watch(src, dst):
        journal = sw.read_journal(root)
        seen.append((journal["state"], journal["step"]))
        real(src, dst)
    monkeypatch.setattr(sw.os, "rename", watch)
    assert sw.apply(root, FakeLauncher([True]), sleep=lambda s: None) == "updated"
    assert seen == [("switching", 0), ("switching", 1)]


def test_an_undone_switch_records_step_zero(root, monkeypatch):
    _stage(root)
    real = os.rename
    steps = []

    def second_fails(src, dst):
        steps.append(sw.read_journal(root)["step"])
        if Path(src).name == "app-0.2.0":
            raise OSError("bad")
        real(src, dst)
    monkeypatch.setattr(sw.os, "rename", second_fails)
    assert sw.apply(root, FakeLauncher(), sleep=lambda s: None) == "rolled_back_switch"
    assert steps == [0, 1, 1]
    assert _current(root) == OLD and not (root / "app-0.1.0").exists()


def test_rollback_with_all_three_folders_present_refuses_and_deletes_nothing(root):
    _stage(root)
    (root / "app-0.1.0").mkdir()
    (root / "app-0.1.0" / "VERSION").write_text(OLD)
    sw.write_journal(root, sw.SWITCHING, OLD, NEW, OLD, step=1)
    assert sw.recover(root) == "broken"
    for name in ("app", "app-0.1.0", "app-0.2.0"):
        assert (root / name / "VERSION").exists()


def test_a_journal_one_step_behind_still_rolls_back(root):
    # The crash fell between the second rename and its journal entry.
    _stage(root)
    os.rename(root / "app", root / "app-0.1.0")
    os.rename(root / "app-0.2.0", root / "app")
    sw.write_journal(root, sw.SWITCHING, OLD, NEW, OLD, step=1)
    assert sw.recover(root) == "recovered"
    assert _current(root) == OLD and (root / "app-0.2.0").is_dir()


def test_a_failed_rollback_after_a_failed_switch_is_reported_broken(root, monkeypatch):
    _stage(root)

    def fails(*a, **k):
        raise OSError("no")

    def cant_undo(*a, **k):
        raise sw.SwapError("no")
    monkeypatch.setattr(sw, "switch_in", fails)
    monkeypatch.setattr(sw, "roll_back", cant_undo)
    monkeypatch.setattr(sw, "_message", lambda text: None)
    assert sw.apply(root, FakeLauncher(), sleep=lambda s: None) == "broken"
    assert sw.read_result(root)["code"] == "broken"


@pytest.mark.parametrize("bad", [0, 70000, "8600", True, 1.5])
def test_a_journal_with_a_bad_port_or_step_is_unusable(root, bad):
    for field in ("port", "step"):
        sw.write_journal(root, sw.STAGED, OLD, NEW, OLD)
        doc = json.loads((root / "app-update" / "swap.json").read_text())
        doc[field] = bad
        (root / "app-update" / "swap.json").write_text(json.dumps(doc))
        if field == "step" and bad == 0:
            assert sw.read_journal(root)["step"] == 0
        else:
            assert sw.read_journal(root) == {"state": None}


def test_the_helper_waits_on_the_port_the_journal_names(root, monkeypatch):
    _stage(root)
    sw.write_journal(root, sw.STAGED, OLD, NEW, OLD, port=8611)
    made = []

    class Recording(FakeLauncher):
        def __init__(self, root, port=None):
            super().__init__()
            made.append(port)
    monkeypatch.setattr(sw, "Launcher", Recording)
    assert sw.apply(root, sleep=lambda s: None) == "updated"
    assert made == [8611]


def test_stop_waits_until_the_old_server_stops_answering(root, monkeypatch):
    answers = [True, True, False]
    monkeypatch.setattr(sw, "_health_answers", lambda port: answers.pop(0))
    monkeypatch.setattr(sw.Launcher, "_run", lambda self, flag, timeout: True)
    monkeypatch.setattr(sw.time, "sleep", lambda s: None)
    assert sw.Launcher(root, 8611).stop() is True
    assert answers == []


def test_stop_gives_up_when_the_old_server_keeps_answering(root, monkeypatch):
    monkeypatch.setattr(sw, "_health_answers", lambda port: True)
    monkeypatch.setattr(sw.Launcher, "_run", lambda self, flag, timeout: True)
    monkeypatch.setattr(sw.time, "sleep", lambda s: None)
    monkeypatch.setattr(sw, "STOP_SECONDS", 0)
    assert sw.Launcher(root, 8611).stop() is False
