"""Temp folders Baihe must not leave behind: the legacy-prefix sweep of the
system temp, Playwright's driver temp folder, and "Clean temp files now"."""

import os
import sys
import time
import types

import pytest

import background_jobs
import db
import page_fetch
import storage
from services import temp_cleanup_service
from services.service_errors import ConflictError

DAY = 86400


def _make(path, age=2 * DAY, size=0):
    os.makedirs(path)
    if size:
        with open(os.path.join(path, "f.bin"), "wb") as fh:
            fh.write(b"x" * size)
    t = time.time() - age
    os.utime(path, (t, t))
    return path


# --- legacy folders in the system temp ------------------------------------

def test_legacy_sweep_removes_only_stale_own_prefix_folders(tmp_path):
    root = tmp_path / "systemtemp"
    root.mkdir()
    stale = _make(str(root / "baihe_live_abc123"))
    fresh = _make(str(root / "baihe_live_fresh"), age=60)
    foreign = [_make(str(root / n)) for n in
               ("playwright_chromiumdev_profile-XyZ", "playwright-artifacts-1", "tmpab12cd", "other_live_x")]
    assert storage.sweep_legacy_system_temp(system_temp=str(root)) == 1
    assert not os.path.exists(stale)
    assert os.path.isdir(fresh)
    assert all(os.path.isdir(p) for p in foreign)


def test_legacy_sweep_leaves_links_and_what_they_point_at(tmp_path):
    root = tmp_path / "systemtemp"
    root.mkdir()
    target = _make(str(tmp_path / "precious"), size=10)
    link = root / "baihe_live_link"
    try:
        os.symlink(target, link, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    t = time.time() - 3 * DAY
    os.utime(link, (t, t), follow_symlinks=False)
    assert storage.sweep_legacy_system_temp(system_temp=str(root)) == 0
    assert os.path.islink(link)
    assert os.path.exists(os.path.join(target, "f.bin"))


def test_legacy_sweep_ignores_plain_files(tmp_path):
    root = tmp_path / "systemtemp"
    root.mkdir()
    f = root / "baihe_live_file"
    f.write_text("x")
    t = time.time() - 3 * DAY
    os.utime(f, (t, t))
    assert storage.sweep_legacy_system_temp(system_temp=str(root)) == 0
    assert f.exists()


def test_legacy_sweep_respects_the_removal_cap(tmp_path, monkeypatch):
    root = tmp_path / "systemtemp"
    root.mkdir()
    for i in range(5):
        _make(str(root / f"baihe_live_{i}"))
    monkeypatch.setattr(storage, "SWEEP_MAX_REMOVED", 2)
    assert storage.sweep_legacy_system_temp(system_temp=str(root)) == 2
    assert len(os.listdir(root)) == 3


def test_legacy_sweep_respects_the_examined_cap(tmp_path, monkeypatch):
    root = tmp_path / "systemtemp"
    root.mkdir()
    for i in range(6):
        _make(str(root / f"baihe_live_{i}"))
    monkeypatch.setattr(storage, "SWEEP_MAX_EXAMINED", 3)
    assert storage.sweep_legacy_system_temp(system_temp=str(root)) == 3


def test_legacy_sweep_stops_at_the_time_budget(tmp_path, monkeypatch):
    root = tmp_path / "systemtemp"
    root.mkdir()
    _make(str(root / "baihe_live_a"))
    monkeypatch.setattr(storage, "SWEEP_MAX_SECONDS", -1.0)
    assert storage.sweep_legacy_system_temp(system_temp=str(root)) == 0


def test_legacy_sweep_is_silent_when_the_folder_is_unreadable(tmp_path):
    assert storage.sweep_legacy_system_temp(system_temp=str(tmp_path / "missing")) == 0


def test_legacy_sweep_survives_a_delete_error(tmp_path, monkeypatch):
    root = tmp_path / "systemtemp"
    root.mkdir()
    _make(str(root / "baihe_live_a"))

    def locked(path):
        raise PermissionError("in use")

    monkeypatch.setattr(storage.shutil, "rmtree", locked)
    assert storage.sweep_legacy_system_temp(system_temp=str(root)) == 0


# --- library temp: held folders and live jobs -----------------------------

def test_held_workdir_is_not_swept(isolated_db):
    with storage.job_workdir("signin") as path:
        os.utime(path, (0, 0))
        assert storage.sweep_library_temp(max_age=0)["removed"] == 0
        assert os.path.isdir(path)
    assert not os.path.exists(path)


def test_held_workdir_survives_a_differently_spelled_library_root(isolated_db, monkeypatch):
    # A hand-edited portable marker can give the root other separators/case
    # than mkdtemp's return value.
    with storage.job_workdir("signin") as path:
        os.utime(path, (0, 0))
        alt = db.LIBRARY_DIR
        monkeypatch.setattr(db, "LIBRARY_DIR", os.path.join(alt, "sub", "..") + os.sep)
        assert storage.sweep_library_temp(max_age=0)["removed"] == 0
        assert os.path.isdir(path)


def test_holding_protects_a_folder_without_removing_it(isolated_db):
    path = _make(os.path.join(storage.temp_root(), "live_x~1"))
    with storage.holding(path):
        assert storage.sweep_library_temp(max_age=0)["removed"] == 0
    assert os.path.isdir(path)
    assert not os.path.exists(os.path.join(path, storage.HOLD_MARKER))
    assert storage.sweep_library_temp(max_age=0)["removed"] == 1


def test_folder_marked_by_a_live_other_process_is_kept(isolated_db, monkeypatch):
    path = _make(os.path.join(storage.temp_root(), "other~1"))
    with open(os.path.join(path, storage.HOLD_MARKER), "w") as fh:
        fh.write("424242")
    alive = {"v": True}
    monkeypatch.setattr(background_jobs, "owner_process_alive", lambda pid: alive["v"])
    assert storage.sweep_library_temp(max_age=0)["removed"] == 0
    alive["v"] = False
    assert storage.sweep_library_temp(max_age=0)["removed"] == 1


def test_scanlate_import_staging_is_held_for_the_whole_import(isolated_db, monkeypatch):
    from services import scanlate_pages_service as sps
    seen = {}

    def commit(drama_id, staged):
        seen["removed"] = storage.sweep_library_temp(max_age=0)["removed"]
        seen["dirs"] = [d for d in os.listdir(storage.temp_root())]
        raise RuntimeError("stop")

    monkeypatch.setattr(sps, "require_drama", lambda _id: None)
    monkeypatch.setattr(sps, "_commit_pages", commit)
    png = (b"\x89PNG\r\n\x1a\n" + b"\0" * 64)

    def stage(raw, staging, tag, slice_strips):
        out = os.path.join(staging, tag + ".png")
        with open(out, "wb") as fh:
            fh.write(b"x")
        return [out]

    monkeypatch.setattr(sps, "_stage_image", stage)
    import io
    with pytest.raises(RuntimeError):
        sps.add_page_images(1, [("a.png", io.BytesIO(png))])
    assert seen["removed"] == 0 and seen["dirs"]
    assert os.listdir(storage.temp_root()) == []


def test_sweep_measures_freed_bytes_on_request(isolated_db):
    _make(os.path.join(storage.temp_root(), "gone~1"), size=2048)
    result = storage.sweep_library_temp(max_age=0, measure=True)
    assert result == {"removed": 1, "freed_bytes": 2048}


# --- Playwright --------------------------------------------------------------

@pytest.fixture
def fake_transport(monkeypatch):
    """A stand-in for playwright._impl._transport so the driver env hook runs."""
    seen = []
    mod = types.ModuleType("playwright._impl._transport")
    mod.get_driver_env = lambda: {"KEEP": "1"}
    pkg = types.ModuleType("playwright")
    impl = types.ModuleType("playwright._impl")
    impl._transport = mod
    pkg._impl = impl
    monkeypatch.setitem(sys.modules, "playwright", pkg)
    monkeypatch.setitem(sys.modules, "playwright._impl", impl)
    monkeypatch.setitem(sys.modules, "playwright._impl._transport", mod)
    return mod, seen


def _fake_sync_playwright(mod, log):
    class _P:
        def __enter__(self):
            log.append(("start", mod.get_driver_env()))
            return self

        def __exit__(self, *a):
            log.append(("stop", None))
            return False

        def start(self):
            log.append(("start", mod.get_driver_env()))
            return self

        def stop(self):
            log.append(("stop", None))

    return lambda: _P()


def test_driver_gets_a_private_temp_and_the_server_environment_is_untouched(isolated_db, fake_transport):
    mod, _ = fake_transport
    original = mod.get_driver_env
    before = dict(os.environ)
    log = []
    with storage.playwright_session(_fake_sync_playwright(mod, log)):
        env = log[0][1]
        work = env["TMPDIR"]
        assert env["TEMP"] == env["TMP"] == work
        assert env["KEEP"] == "1"
        assert os.path.dirname(work) == storage.temp_root()
        assert os.path.isdir(work)
        assert os.environ.get("TMPDIR") != work
    assert not os.path.exists(work)
    assert mod.get_driver_env is original
    assert dict(os.environ) == before


def test_driver_stops_and_folder_goes_when_the_body_raises(isolated_db, fake_transport):
    mod, _ = fake_transport
    log = []
    with pytest.raises(RuntimeError):
        with storage.playwright_session(_fake_sync_playwright(mod, log)):
            work = log[0][1]["TMPDIR"]
            raise RuntimeError("page crashed")
    assert log[-1][0] == "stop"
    assert not os.path.exists(work)
    assert os.listdir(storage.temp_root()) == []


def test_driver_start_failure_removes_the_folder(isolated_db, fake_transport):
    mod, _ = fake_transport

    class _Bad:
        def __enter__(self):
            raise RuntimeError("no driver")

        def __exit__(self, *a):
            return False

    with pytest.raises(RuntimeError):
        with storage.playwright_session(lambda: _Bad()):
            pass
    assert os.listdir(storage.temp_root()) == []
    assert mod.get_driver_env() == {"KEEP": "1"}


def test_without_the_driver_hook_a_launch_still_works(isolated_db, monkeypatch):
    monkeypatch.setitem(sys.modules, "playwright", None)
    ran = []

    class _P:
        def __enter__(self):
            ran.append(1)
            return self

        def __exit__(self, *a):
            return False

    with storage.playwright_session(lambda: _P()):
        pass
    assert ran == [1]


class _Browser:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def test_page_fetch_closes_the_browser_and_driver_when_the_page_fails(isolated_db, fake_transport, monkeypatch):
    mod, _ = fake_transport
    log, browsers = [], []
    class _Chromium:
        def launch(self, **kw):
            b = _Browser()
            browsers.append(b)
            return b

    class _Proxy:
        proxied = 1

        def launch_kwargs(self):
            return {}

        def stop(self):
            pass

    monkeypatch.setattr(page_fetch, "_PinningProxy", _Proxy)
    monkeypatch.setattr(page_fetch, "_guarded_page", lambda b: (_ for _ in ()).throw(RuntimeError("boom")))
    class _Play:
        chromium = _Chromium()

        def __enter__(self):
            log.append(("start", mod.get_driver_env()))
            return self

        def __exit__(self, *a):
            log.append(("stop", None))
            return False

    monkeypatch.setattr(page_fetch, "_require_playwright", lambda: (lambda: _Play()))
    with pytest.raises(RuntimeError):
        with page_fetch.rendered_session("https://public.example/", wait_ms=0):
            pass
    assert browsers and all(b.closed for b in browsers)
    assert log[-1][0] == "stop"
    assert os.listdir(storage.temp_root()) == []


def test_persistent_launch_frees_its_folder_on_close_and_on_failure(isolated_db, fake_transport, monkeypatch):
    mod, _ = fake_transport
    log = []

    class _Ctx:
        closed = False

        def close(self):
            self.closed = True

    ctx = _Ctx()

    class _Play:
        def __init__(self):
            self.chromium = types.SimpleNamespace(launch_persistent_context=lambda *a, **k: ctx)

        def start(self):
            log.append(("start", mod.get_driver_env()))
            return self

        def stop(self):
            log.append(("stop", None))

    class _Proxy:
        def launch_kwargs(self):
            return {}

        def stop(self):
            pass

    monkeypatch.setattr(page_fetch, "_require_playwright", lambda: (lambda: _Play()))
    monkeypatch.setattr(page_fetch, "_PinningProxy", _Proxy)
    pw, context = page_fetch._launch_persistent(str(isolated_db), True)
    work = log[0][1]["TMPDIR"]
    assert os.path.isdir(work)
    page_fetch._shut(pw, context)
    assert ctx.closed and log[-1][0] == "stop" and not os.path.exists(work)

    log.clear()

    def refuse(*a, **k):
        raise RuntimeError("no browser")

    monkeypatch.setattr(page_fetch, "_launch_chromium", refuse)
    with pytest.raises(RuntimeError):
        page_fetch._launch_persistent(str(isolated_db), True)
    assert log[-1][0] == "stop"
    assert os.listdir(storage.temp_root()) == []


# --- Clean temp files now --------------------------------------------------

def test_clean_now_removes_everything_and_reports_counts_only(isolated_db):
    root = storage.temp_root()
    _make(os.path.join(root, "a~1"), age=10, size=1024 * 1024)
    open(os.path.join(root, "b~2.part"), "wb").write(b"y" * 1024 * 1024)
    result = temp_cleanup_service.clean_now()
    assert result == {"removed": 2, "freed_mb": 2.0}
    assert os.listdir(root) == []


def test_clean_now_refuses_while_a_job_runs(isolated_db):
    keep = _make(os.path.join(storage.temp_root(), "job_1~x"), age=10)
    with background_jobs._lock:
        background_jobs._jobs["job_1"] = {"status": "running"}
    try:
        with pytest.raises(ConflictError):
            temp_cleanup_service.clean_now()
    finally:
        with background_jobs._lock:
            background_jobs._jobs.pop("job_1", None)
    assert os.path.isdir(keep)
    assert background_jobs.acquire_exclusive("probe")   # the hold was released
    background_jobs.release_exclusive()


def test_clean_now_refuses_while_the_gpu_lock_is_held(isolated_db, monkeypatch):
    import db
    monkeypatch.setattr(db, "gpu_lock_status", lambda: ("cli", 1))
    with pytest.raises(ConflictError):
        temp_cleanup_service.clean_now()
