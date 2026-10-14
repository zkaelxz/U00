"""Package install and GPU PyTorch setup as a background job
(services/diagnostics_installs_service): Cancel kills pip's process tree and
releases the library hold; output reaches the job message redacted. Child
processes are real python one-liners; pip itself is never run."""
import os
import sys
import time

import pytest

import background_jobs
from jobs import job_launch
import db
import diagnostics_torch
import install_plan
import pending_install
from services import diagnostics_admin_service as admin_svc
from services import diagnostics_packages_service as packages_svc
from services import diagnostics_gpu_torch_service as torch_svc
from services import diagnostics_installs_service as svc

SECRET = "sk-ant-api03-SECRETSECRETSECRET123456"
ABS_PATH = "/home/someone/private/library/drama.mp4"
JOB = svc.DEPENDENCY_JOB_ID


def _wait(predicate, seconds=15):
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        value = predicate()
        if value:
            return value
        time.sleep(0.02)
    raise AssertionError("timed out")


def _finished():
    job = background_jobs.get_status(JOB)
    return job if job and job["status"] not in ("running", "queued") else None


@pytest.fixture
def env(isolated_db, monkeypatch, tmp_path):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)
    monkeypatch.setattr(diagnostics_torch, "torch_pin_lines", lambda: [])
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys, specs=None: ({"install": []}, None))
    site = tmp_path / "site-packages"
    site.mkdir()
    monkeypatch.setattr(svc.sysconfig, "get_paths",
                        lambda: {"purelib": str(site), "platlib": str(site)})
    background_jobs.clear_job(JOB)
    svc._DEPENDENCY.update(kind=None, package=None, last=None)
    yield
    background_jobs.request_cancel(JOB)
    _wait(lambda: not job_launch.exclusive_active() or _finished())
    background_jobs.clear_job(JOB)


def _commands(monkeypatch, *scripts, timeout=60):
    monkeypatch.setattr(packages_svc, "_install_commands",
                        lambda name: [([sys.executable, "-c", s], timeout) for s in scripts])


def _grandchild_script(marker):
    return ("import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
            f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
            "print('Downloading wheel', flush=True)\n"
            "time.sleep(60)\n")


def _dead(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    with open(f"/proc/{pid}/status") as fh:
        return "zombie" in fh.read().lower()


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_cancel_kills_the_pip_tree_and_releases_the_hold(env, monkeypatch, tmp_path):
    marker = tmp_path / "gc.pid"
    _commands(monkeypatch, _grandchild_script(marker))
    assert svc.start_dependency_install("jieba", confirm=True) == {
        "job_id": JOB, "started": True}
    _wait(lambda: marker.exists() and "Downloading" in (
        background_jobs.get_status(JOB) or {}).get("message", ""))
    assert job_launch.exclusive_active() is True
    # While it runs nothing else may start, and a second install is refused.
    assert job_launch.start_job("other_job", lambda: None) is False
    with pytest.raises(admin_svc.AdminActionJobsRunning):
        svc.start_dependency_install("jieba", confirm=True)

    background_jobs.request_cancel(JOB)
    job = _wait(_finished)
    assert job["status"] == "cancelled"
    assert job_launch.exclusive_active() is False
    _wait(lambda: _dead(int(marker.read_text())))
    state = svc.get_dependency_install()
    assert state["result"]["cancelled"] is True and state["result"]["ok"] is False
    assert state["result"]["hint"] == "Cancelled. Nothing else was changed."
    assert job_launch.start_job("other_job", lambda: None) is True


def test_cancel_between_commands_runs_no_further_command(env, monkeypatch):
    ran = []

    def fake_tree(cmd, timeout, cancel=None, **_kw):
        ran.append(cmd[-1])
        background_jobs.request_cancel(JOB)       # Cancel arrives as the first command ends
        yield {"returncode": 0, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(packages_svc, "stream_tree", fake_tree)
    _commands(monkeypatch, "first", "second")
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "cancelled"
    assert ran == ["first"] and job_launch.exclusive_active() is False


def test_output_reaches_the_job_message_redacted(env, monkeypatch):
    messages = []
    real = background_jobs.update_progress

    def record(job_id, frac, message=""):
        messages.append(message)
        return real(job_id, frac, message)
    monkeypatch.setattr(background_jobs, "update_progress", record)
    _commands(monkeypatch,
              f"print('failed with {SECRET} at {ABS_PATH}')")
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "done"
    text = " ".join(messages) + str(svc.get_dependency_install())
    assert any("failed with" in m for m in messages)
    assert SECRET not in text and "/home/someone" not in text


def test_a_pip_failure_ends_the_job_as_an_error_with_the_output(env, monkeypatch):
    _commands(monkeypatch, "import sys; print('ERROR: no matching distribution'); sys.exit(1)")
    svc.start_dependency_install("jieba", confirm=True)
    job = _wait(_finished)
    assert job["status"] == "error" and job_launch.exclusive_active() is False
    result = svc.get_dependency_install()["result"]
    assert result["ok"] is False and "no matching distribution" in " ".join(result["output_tail"])


def test_an_unexpected_error_is_not_echoed_and_releases_the_hold(env, monkeypatch):
    def boom(cmd, timeout, cancel=None, **_kw):
        raise RuntimeError(f"secret {SECRET} at {ABS_PATH}")
        yield  # noqa
    monkeypatch.setattr(packages_svc, "stream_tree", boom)
    _commands(monkeypatch, "pass")
    svc.start_dependency_install("jieba", confirm=True)
    job = _wait(_finished)
    assert job["status"] == "error" and job_launch.exclusive_active() is False
    shown = str(svc.get_dependency_install()) + str(job)
    assert SECRET not in shown and "/home/someone" not in shown
    assert "RuntimeError" in svc.get_dependency_install()["result"]["hint"]


def test_start_checks_run_before_a_job_exists(env, monkeypatch):
    with pytest.raises(admin_svc.AdminActionUnconfirmed):
        svc.start_dependency_install("jieba", confirm=False)
    with pytest.raises(admin_svc.AdminActionUnknownPackage):
        svc.start_dependency_install("fastapi", confirm=True)
    with pytest.raises(admin_svc.AdminActionUnconfirmed):
        svc.start_gpu_torch_setup("cpu", confirm=False)
    assert background_jobs.get_status(JOB) is None and job_launch.exclusive_active() is False


def test_the_install_refuses_while_another_job_runs(env, monkeypatch):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: True)
    with pytest.raises(admin_svc.AdminActionJobsRunning):
        svc.start_dependency_install("jieba", confirm=True)
    assert background_jobs.get_status(JOB) is None


def test_a_job_that_appears_after_the_start_checks_stops_the_install(env, monkeypatch):
    """The start checks passed, then another process's job showed up: the
    re-check under the hold refuses and nothing is installed."""
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)
    monkeypatch.setattr(svc, "_other_job_running", lambda own: True)
    monkeypatch.setattr(packages_svc, "stream_tree",
                        lambda *a, **k: pytest.fail("pip must not run"))
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "error"
    assert job_launch.exclusive_active() is False


def test_other_job_check_ignores_the_install_itself(env, monkeypatch):
    monkeypatch.setattr(background_jobs, "list_all_jobs",
                        lambda: {JOB: {"status": "running"}, "x": {"status": "done"}})
    monkeypatch.setattr(db, "list_job_records",
                        lambda: [{"job_id": JOB, "status": "running", "updated_at": time.time()}])
    assert svc._other_job_running(JOB) is False
    monkeypatch.setattr(background_jobs, "list_all_jobs",
                        lambda: {JOB: {"status": "running"}, "x": {"status": "running"}})
    assert svc._other_job_running(JOB) is True


def test_gpu_torch_setup_runs_as_a_job_and_cancel_skips_the_check(env, monkeypatch):
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: None)
    monkeypatch.setattr(svc.torch_svc, "_python_supported", lambda: True)
    monkeypatch.setattr(torch_svc, "_torch_setup_commands",
                        lambda variant: [([sys.executable, "-c", "import time; print('x', flush=True); time.sleep(60)"], 60)])
    monkeypatch.setattr(torch_svc, "verify_torch", lambda **k: pytest.fail("no verify after Cancel"))
    assert svc.start_gpu_torch_setup("cpu", confirm=True)["started"] is True
    _wait(lambda: "x" in (background_jobs.get_status(JOB) or {}).get("message", ""))
    assert svc.get_dependency_install()["kind"] == "gpu_torch"
    background_jobs.request_cancel(JOB)
    assert _wait(_finished)["status"] == "cancelled"
    assert job_launch.exclusive_active() is False


def _report(*pairs):
    return {"install": [{"metadata": {"name": n, "version": v}} for n, v in pairs]}


def test_install_now_refuses_a_blocked_plan(env, monkeypatch):
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys, specs=None: (None, "conflict"))
    with pytest.raises(admin_svc.AdminActionNotPossible, match="can't be installed together"):
        svc.start_dependency_install("jieba", confirm=True)
    assert background_jobs.get_status(JOB) is None


RISK = "numpy 2.0 -> 1.0 is a downgrade of a package Baihe itself needs."


def _downgrade_plan(monkeypatch):
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys, specs=None: (_report(("numpy", "1.0")), None))
    monkeypatch.setattr(pending_install, "snapshot", lambda: {"numpy": "2.0"})
    monkeypatch.setattr(install_plan, "app_required_names", lambda: {"numpy"})


def test_install_now_needs_the_downgrade_accepted(env, monkeypatch):
    _downgrade_plan(monkeypatch)
    with pytest.raises(admin_svc.AdminActionRefused, match="downgrade") as exc:
        svc.start_dependency_install("jieba", confirm=True)
    assert getattr(exc.value, "http_status", 409) == 409
    assert background_jobs.get_status(JOB) is None
    _commands(monkeypatch, "pass")
    assert svc.start_dependency_install("jieba", confirm=True, accept_risk=True,
                                        accepted_risks=[RISK])["started"] is True
    assert _wait(_finished)["status"] == "done"


def test_accepting_covers_only_the_risks_that_were_shown(env, monkeypatch):
    """A plan that changed between the preview and the POST asks again."""
    _downgrade_plan(monkeypatch)
    for shown in (None, [], ["setuptools 80 -> 70 is a downgrade of a package Baihe itself needs."]):
        with pytest.raises(svc.PlanNeedsConfirm, match="numpy 2.0 -> 1.0"):
            svc.start_dependency_install("jieba", confirm=True, accept_risk=True, accepted_risks=shown)
    with pytest.raises(svc.PlanNeedsConfirm):
        svc.start_dependency_install("jieba", confirm=True, accept_risk=False, accepted_risks=[RISK])
    assert background_jobs.get_status(JOB) is None


def test_install_now_without_a_preview_needs_that_accepted(env, monkeypatch):
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys, specs=None: (None, "this pip cannot preview an install."))
    monkeypatch.setattr(svc.os, "name", "posix")
    monkeypatch.setattr(install_plan.os, "name", "posix")
    with pytest.raises(svc.PlanNeedsConfirm, match="couldn't check"):
        svc.start_dependency_install("jieba", confirm=True)
    assert background_jobs.get_status(JOB) is None
    _commands(monkeypatch, "pass")
    assert svc.start_dependency_install("jieba", confirm=True, accept_risk=True,
                                        accepted_risks=[install_plan.UNCHECKED_RISK])["started"]
    assert _wait(_finished)["status"] == "done"


def test_install_now_refuses_while_another_plan_or_hold_is_live(env, monkeypatch):
    """Two near-simultaneous Install now presses must not both plan against
    the environment the first is about to change."""
    from services import pending_install_service
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys, specs=None: pytest.fail("no second plan"))
    with pending_install_service._PREVIEW_LOCK:
        with pytest.raises(svc.ConflictError, match="in progress"):
            svc.start_dependency_install("jieba", confirm=True)
    assert job_launch.acquire_exclusive("Library restore")
    try:
        with pytest.raises(svc.ConflictError, match="in progress"):
            svc.start_dependency_install("jieba", confirm=True)
    finally:
        job_launch.release_exclusive()
    assert background_jobs.get_status(JOB) is None
    assert pending_install_service._PREVIEW_LOCK.acquire(blocking=False)
    pending_install_service._PREVIEW_LOCK.release()


def test_install_now_refuses_what_must_wait_for_a_restart(env, monkeypatch):
    real = install_plan.build_plan
    monkeypatch.setattr(install_plan, "build_plan", lambda keys, specs=None: real(
        keys, windows=True, report=_report(("numpy", "2.1")), installed={"numpy": "2.0"},
        in_use=lambda name: True, mins={}, required=set()))
    with pytest.raises(svc.InstallNeedsRestart, match="Queue it for the next start"):
        svc.start_dependency_install("jieba", confirm=True)
    assert background_jobs.get_status(JOB) is None


def test_files_in_use_since_the_preview_stop_the_install_before_pip(env, monkeypatch):
    """The plan found nothing loaded; by the time the job holds the library
    something has imported numpy, which pip would fail to replace on Windows."""
    real = install_plan.build_plan
    monkeypatch.setattr(install_plan, "build_plan", lambda keys, specs=None: real(
        keys, windows=True, report=_report(("numpy", "2.1")), installed={"numpy": "2.0"},
        in_use=lambda name: False, mins={}, required=set()))
    monkeypatch.setattr(install_plan, "_is_windows", lambda: True)
    monkeypatch.setattr(install_plan, "dist_in_use", lambda name: name == "numpy")
    monkeypatch.setattr(packages_svc, "stream_tree", lambda *a, **k: pytest.fail("pip must not run"))
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "error"
    assert svc.get_dependency_install()["result"]["hint"] == (
        "Nothing was changed: Baihe started using numpy after the preview, and Windows can't "
        "replace files in use. Queue it for the next start instead.")
    assert job_launch.exclusive_active() is False


def test_files_in_use_are_rechecked_only_on_windows(env, monkeypatch):
    monkeypatch.setattr(install_plan, "_is_windows", lambda: False)
    monkeypatch.setattr(install_plan, "dist_in_use", lambda name: pytest.fail("not asked off Windows"))
    _commands(monkeypatch, "pass")
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "done"


@pytest.mark.parametrize("which", ["purelib", "platlib"])
def test_a_read_only_install_folder_refuses_before_a_job_exists(env, monkeypatch, tmp_path, which):
    """os.access would say yes on a Windows folder whose ACL denies writing,
    so the check really creates a file, in both install folders."""
    read_only = str(tmp_path / which)
    os.mkdir(read_only)
    paths = {"purelib": str(tmp_path / "site-packages"), "platlib": str(tmp_path / "site-packages"),
             which: read_only}
    monkeypatch.setattr(svc.sysconfig, "get_paths", lambda: dict(paths))
    monkeypatch.setattr(svc.os, "access", lambda path, mode: True)
    real_tf = svc.tempfile.TemporaryFile

    def denied(*a, dir=None, **k):
        if dir == read_only:
            raise PermissionError(13, "Access is denied")
        return real_tf(*a, dir=dir, **k)
    monkeypatch.setattr(svc.tempfile, "TemporaryFile", denied)
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys, specs=None: pytest.fail("no preview"))
    with pytest.raises(admin_svc.AdminActionNotPossible, match="Stop the service"):
        svc.start_dependency_install("jieba", confirm=True)
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: None)
    monkeypatch.setattr(svc.torch_svc, "_python_supported", lambda: True)
    with pytest.raises(admin_svc.AdminActionNotPossible, match="Stop the service"):
        svc.start_gpu_torch_setup("cpu", confirm=True)
    assert background_jobs.get_status(JOB) is None
    assert os.listdir(paths["purelib" if which == "platlib" else "platlib"]) == []   # probe cleaned up


@pytest.fixture
def restore(monkeypatch):
    """`pip` (a fake runner) replaces numpy 2.0 with 2.1, then fails or is
    cancelled; the restore pip (also faked) puts back what it is asked for."""
    state = {"env": {"numpy": "2.0"}, "calls": [], "skip": set()}
    monkeypatch.setattr(pending_install, "snapshot", lambda: dict(state["env"]))
    monkeypatch.setattr(svc, "_non_index_dists", lambda: set(state["skip"]))

    def fake_restore_pip(argv, timeout, cancel=None, **_kw):
        if state.get("cancel_again"):
            background_jobs.request_cancel(JOB)
        state["calls"].append({"specs": [a for a in argv if "==" in a],
                               "cancel": cancel is not None,
                               "stopped": cancel is not None and cancel()})
        if state.get("restore_cancelled") or state["calls"][-1]["stopped"]:
            yield {"returncode": None, "timed_out": False, "cancelled": True}
            return
        for spec in argv:
            if "==" in spec and spec not in state.get("unavailable", ()):
                name, version = spec.split("==")
                state["env"][name] = version
        yield {"returncode": 0, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(svc.proc_run, "stream_tree", fake_restore_pip)
    _commands(monkeypatch, "pip")
    return state


def _pip_moves(restore, monkeypatch, changes, cancel=False):
    def fake_tree(cmd, timeout, cancel_probe=None, **_kw):
        restore["env"].update(changes)
        for name in [n for n, v in changes.items() if v is None]:
            del restore["env"][name]
        if cancel:
            background_jobs.request_cancel(JOB)
            yield {"returncode": None, "timed_out": False, "cancelled": True}
        else:
            yield {"returncode": 1, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(packages_svc, "stream_tree", fake_tree)


def test_a_pip_failure_puts_the_earlier_packages_back(env, monkeypatch, restore):
    _pip_moves(restore, monkeypatch, {"numpy": "2.1"})
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "error"
    assert [c["specs"] for c in restore["calls"]] == [["numpy==2.0"]]
    assert restore["calls"][0]["cancel"] is True        # Cancel can stop this restore
    assert restore["env"] == {"numpy": "2.0"}
    assert "Your earlier packages were put back." in svc.get_dependency_install()["result"]["hint"]
    assert job_launch.exclusive_active() is False


def test_cancel_puts_the_earlier_packages_back(env, monkeypatch, restore):
    _pip_moves(restore, monkeypatch, {"numpy": "2.1"}, cancel=True)
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "cancelled"
    # The Cancel that stopped pip must not also stop the restore it asked for.
    assert restore["calls"] == [{"specs": ["numpy==2.0"], "cancel": True, "stopped": False}]
    result = svc.get_dependency_install()["result"]
    assert result["hint"] == "Cancelled. Your earlier packages were put back."
    assert job_launch.exclusive_active() is False


def test_a_second_cancel_stops_the_restore_the_first_one_asked_for(env, monkeypatch, restore):
    restore["cancel_again"] = True
    _pip_moves(restore, monkeypatch, {"numpy": "2.1"}, cancel=True)
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "cancelled"
    assert restore["calls"][0]["stopped"] is True
    hint = svc.get_dependency_install()["result"]["hint"]
    assert "Putting your earlier packages back was cancelled." in hint


def test_cancel_during_the_restore_is_reported(env, monkeypatch, restore):
    restore["restore_cancelled"] = True
    _pip_moves(restore, monkeypatch, {"numpy": "2.1"})
    svc.start_dependency_install("jieba", confirm=True)
    _wait(_finished)
    hint = svc.get_dependency_install()["result"]["hint"]
    assert "Putting your earlier packages back was cancelled." in hint
    assert "could not be put back: numpy" in hint


def test_packages_pip_added_are_named_not_hidden(env, monkeypatch, restore):
    _pip_moves(restore, monkeypatch, {"jieba": "0.42", "regex": "2026.1"})
    svc.start_dependency_install("jieba", confirm=True)
    _wait(_finished)
    hint = svc.get_dependency_install()["result"]["hint"]
    assert "pip added jieba, regex, which were left in place." in hint
    assert "Nothing else was changed" not in hint
    assert restore["calls"] == []


def test_a_package_from_outside_the_index_is_not_put_back_from_it(env, monkeypatch, restore):
    restore["env"]["mylib"] = "1.0"
    restore["skip"] = {"mylib"}
    _pip_moves(restore, monkeypatch, {"numpy": "2.1", "mylib": "1.2"})
    svc.start_dependency_install("jieba", confirm=True)
    _wait(_finished)
    assert [c["specs"] for c in restore["calls"]] == [["numpy==2.0"]]
    hint = svc.get_dependency_install()["result"]["hint"]
    assert "mylib came from outside the package index" in hint
    assert "Your earlier packages were put back" not in hint


def test_a_kept_core_wheel_is_put_back_on_its_own_before_the_index(env, monkeypatch, restore, tmp_path):
    """Setup's kept numpy wheel goes back with no index (so with the network
    down too); pyyaml has no kept wheel and comes from the index after it."""
    import hashlib
    folder = tmp_path / "wheels"
    folder.mkdir()
    (folder / "numpy-2.0-cp312-cp312-win_amd64.whl").write_bytes(b"numpy")
    (folder / "wheels.lock.txt").write_text(
        f"numpy==2.0 \\\n    --hash=sha256:{hashlib.sha256(b'numpy').hexdigest()}\n")
    monkeypatch.setattr(pending_install, "kept_wheels_dir", lambda: str(folder))
    restore["env"]["pyyaml"] = "6.0"
    _pip_moves(restore, monkeypatch, {"numpy": "2.1", "pyyaml": "6.1"})
    svc.start_dependency_install("jieba", confirm=True)
    _wait(_finished)
    assert [c["specs"] for c in restore["calls"]] == [["numpy==2.0"], ["pyyaml==6.0"]]
    assert restore["env"] == {"numpy": "2.0", "pyyaml": "6.0"}


def test_a_package_that_could_not_be_put_back_is_named(env, monkeypatch, restore):
    restore["unavailable"] = {"numpy==2.0"}
    _pip_moves(restore, monkeypatch, {"numpy": "2.1"})
    svc.start_dependency_install("jieba", confirm=True)
    _wait(_finished)
    hint = svc.get_dependency_install()["result"]["hint"]
    assert "could not be put back: numpy. Install them again from Packages." in hint


def test_a_failed_gpu_setup_points_back_to_the_setup(env, monkeypatch, restore):
    restore["unavailable"] = {"torch==2.5.0"}
    restore["env"]["torch"] = "2.5.0"
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: None)
    monkeypatch.setattr(svc.torch_svc, "_python_supported", lambda: True)
    monkeypatch.setattr(torch_svc, "_torch_setup_commands", lambda variant: [([sys.executable, "-c", "pip"], 60)])
    _pip_moves(restore, monkeypatch, {"torch": "2.11.0+cpu"})
    svc.start_gpu_torch_setup("cpu", confirm=True)
    _wait(_finished)
    hint = svc.get_dependency_install()["result"]["hint"]
    assert "could not be put back: torch. Set up PyTorch again under GPU PyTorch." in hint


# ---- Upgrade: the same plan, write probe and restore as an install ----

def _no_update_check(monkeypatch):
    # No "Check for updates" ran: the update is `--upgrade jieba`.
    monkeypatch.setattr(packages_svc, "cached_update", lambda name: None)


def test_upgrade_plans_exactly_the_pip_command_it_runs(env, monkeypatch):
    _no_update_check(monkeypatch)
    asked = []
    monkeypatch.setattr(install_plan, "run_dry_run",
                        lambda keys, specs=None: asked.append(specs) or (_report(("jieba", "0.43")), None))
    monkeypatch.setattr(pending_install, "snapshot", lambda: {"jieba": "0.42"})
    plan = svc.preview_upgrade("jieba")
    assert plan["changes"] == [{"name": "jieba", "from_version": "0.42", "to_version": "0.43",
                                "kind": "upgrade"}]
    seen = []

    def fake_tree(cmd, timeout, **_kw):
        seen.append(cmd)
        yield {"returncode": 0, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(packages_svc, "stream_tree", fake_tree)
    assert svc.start_dependency_upgrade("jieba", confirm=True)["started"] is True
    assert _wait(_finished)["status"] == "done"
    assert asked == [["--upgrade", "jieba"], ["--upgrade", "jieba"]]
    assert seen[0][seen[0].index("install") + 1:][-4:-2] == ["--upgrade", "jieba"]
    state = svc.get_dependency_install()
    assert state["kind"] == "upgrade" and state["package"] == "jieba"


def test_upgrade_refuses_an_unaccepted_risk_and_honours_accepted_risks(env, monkeypatch):
    _no_update_check(monkeypatch)
    _downgrade_plan(monkeypatch)
    with pytest.raises(svc.PlanNeedsConfirm, match="numpy 2.0 -> 1.0") as exc:
        svc.start_dependency_upgrade("jieba", confirm=True, accept_risk=True,
                                     accepted_risks=["some other risk"])
    assert getattr(exc.value, "http_status", 409) == 409
    with pytest.raises(admin_svc.AdminActionNotPossible, match="can't be installed together"):
        monkeypatch.setattr(install_plan, "run_dry_run", lambda keys, specs=None: (None, "conflict"))
        svc.start_dependency_upgrade("jieba", confirm=True, accept_risk=True, accepted_risks=[RISK])
    assert background_jobs.get_status(JOB) is None
    _downgrade_plan(monkeypatch)
    monkeypatch.setattr(packages_svc, "stream_tree",
                        lambda cmd, timeout, **_kw: iter([{"returncode": 0, "timed_out": False}]))
    assert svc.start_dependency_upgrade("jieba", confirm=True, accept_risk=True,
                                        accepted_risks=[RISK])["started"] is True
    assert _wait(_finished)["status"] == "done"


def test_upgrade_that_must_wait_for_a_restart_is_refused_without_a_queue_hint(env, monkeypatch):
    _no_update_check(monkeypatch)
    real = install_plan.build_plan
    monkeypatch.setattr(install_plan, "build_plan", lambda keys, specs=None: real(
        keys, windows=True, report=_report(("jieba", "0.43")), installed={"jieba": "0.42"},
        in_use=lambda name: True, mins={}, required=set()))
    with pytest.raises(svc.InstallNeedsRestart, match="Restart Baihe") as exc:
        svc.start_dependency_upgrade("jieba", confirm=True)
    assert "Queue" not in str(exc.value)
    assert background_jobs.get_status(JOB) is None


def test_upgrade_refuses_a_read_only_install_folder_before_anything_runs(env, monkeypatch, tmp_path):
    _no_update_check(monkeypatch)
    monkeypatch.setattr(svc, "_writable", lambda folder: False)
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys, specs=None: pytest.fail("no preview"))
    monkeypatch.setattr(packages_svc, "stream_tree", lambda *a, **k: pytest.fail("no pip"))
    with pytest.raises(admin_svc.AdminActionNotPossible, match="Stop the service"):
        svc.start_dependency_upgrade("jieba", confirm=True)
    assert background_jobs.get_status(JOB) is None


def test_upgrade_never_assumes_the_install_only_opencv_swap(env, monkeypatch):
    """An update runs no `pip uninstall opencv-python` first, so contrib
    arriving next to the plain build is a clash, not a planned swap."""
    _no_update_check(monkeypatch)
    monkeypatch.setattr(pending_install, "snapshot",
                        lambda: {"paddleocr": "3.0.0", "opencv-python": "4.10.0"})
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys, specs=None: (_report(
        ("paddleocr", "3.2.0"), ("opencv-contrib-python", "4.10.0")), None))
    monkeypatch.setattr(packages_svc, "stream_tree", lambda *a, **k: pytest.fail("no pip"))
    plan = svc.preview_upgrade("paddleocr")
    assert plan["blocked"] and "two OpenCV packages" in plan["blocked"][0]
    assert not any("Remove opencv-python" in line for line in plan["summary"])
    with pytest.raises(admin_svc.AdminActionNotPossible, match="two OpenCV packages"):
        svc.start_dependency_upgrade("paddleocr", confirm=True)
    assert background_jobs.get_status(JOB) is None


def test_a_failed_upgrade_puts_the_earlier_packages_back(env, monkeypatch, restore):
    _no_update_check(monkeypatch)
    restore["env"]["jieba"] = "0.42"
    _pip_moves(restore, monkeypatch, {"numpy": "2.1", "jieba": "0.43"})
    svc.start_dependency_upgrade("jieba", confirm=True)
    assert _wait(_finished)["status"] == "error"
    assert [c["specs"] for c in restore["calls"]] == [["jieba==0.42", "numpy==2.0"]]
    assert restore["env"] == {"numpy": "2.0", "jieba": "0.42"}
    assert "Your earlier packages were put back." in svc.get_dependency_install()["result"]["hint"]
    assert job_launch.exclusive_active() is False


def test_non_index_dists_reads_direct_url(monkeypatch):
    class Dist:
        def __init__(self, name, direct):
            self.metadata = {"Name": name}
            self._direct = direct

        def read_text(self, fname):
            return '{"url": "file:///x"}' if self._direct and fname == "direct_url.json" else None
    monkeypatch.setattr(svc.importlib.metadata, "distributions",
                        lambda: [Dist("My_Lib", True), Dist("numpy", False)])
    assert svc._non_index_dists() == {"my-lib"}


def test_put_back_leaves_a_plain_opencv_out_when_contrib_is_installed_now(monkeypatch):
    from services import diagnostics_installs_service as inst
    import pending_install
    ran = []
    monkeypatch.setattr(pending_install, "snapshot", lambda: {"opencv-contrib-python": "4.10.0.84"})
    monkeypatch.setattr(inst.proc_run, "stream_tree", lambda *a, **k: ran.append(a) or iter(()))
    restored, failed, _sk, _st, _added, left_out = inst._restore(
        {"opencv-python": "4.10.0.84"}, set(), None)
    assert ran == [] and left_out == ["opencv-python"] and failed == []
    monkeypatch.setattr(inst, "_restore", lambda *a: ([], [], [], False, [], ["opencv-python"]))
    assert "opencv-python was not put back" in inst._put_back("package", {}, set())
