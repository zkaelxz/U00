"""Package install and GPU PyTorch setup as a background job
(services/diagnostics_installs_service): Cancel kills pip's process tree and
releases the library hold; output reaches the job message redacted. Child
processes are real python one-liners; pip itself is never run."""
import os
import sys
import time

import pytest

import background_jobs
import diagnostics_torch
import install_plan
import pending_install
from services import diagnostics_gaps_service as gaps
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
def env(isolated_db, monkeypatch):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)
    monkeypatch.setattr(diagnostics_torch, "torch_pin_lines", lambda: [])
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys: ({"install": []}, None))
    monkeypatch.setattr(svc.os, "access", lambda path, mode: True)
    background_jobs.clear_job(JOB)
    svc._DEPENDENCY.update(kind=None, package=None, last=None)
    yield
    background_jobs.request_cancel(JOB)
    _wait(lambda: not background_jobs.exclusive_active() or _finished())
    background_jobs.clear_job(JOB)


def _commands(monkeypatch, *scripts, timeout=60):
    monkeypatch.setattr(gaps, "_install_commands",
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
    assert background_jobs.exclusive_active() is True
    # While it runs nothing else may start, and a second install is refused.
    assert background_jobs.start_job("other_job", lambda: None) is False
    with pytest.raises(gaps.AdminActionJobsRunning):
        svc.start_dependency_install("jieba", confirm=True)

    background_jobs.request_cancel(JOB)
    job = _wait(_finished)
    assert job["status"] == "cancelled"
    assert background_jobs.exclusive_active() is False
    _wait(lambda: _dead(int(marker.read_text())))
    state = svc.get_dependency_install()
    assert state["result"]["cancelled"] is True and state["result"]["ok"] is False
    assert state["result"]["hint"] == "Cancelled. Nothing else was changed."
    assert background_jobs.start_job("other_job", lambda: None) is True


def test_cancel_between_commands_runs_no_further_command(env, monkeypatch):
    ran = []

    def fake_tree(cmd, timeout, cancel=None, **_kw):
        ran.append(cmd[-1])
        background_jobs.request_cancel(JOB)       # Cancel arrives as the first command ends
        yield {"returncode": 0, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(gaps, "stream_tree", fake_tree)
    _commands(monkeypatch, "first", "second")
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "cancelled"
    assert ran == ["first"] and background_jobs.exclusive_active() is False


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
    assert job["status"] == "error" and background_jobs.exclusive_active() is False
    result = svc.get_dependency_install()["result"]
    assert result["ok"] is False and "no matching distribution" in " ".join(result["output_tail"])


def test_an_unexpected_error_is_not_echoed_and_releases_the_hold(env, monkeypatch):
    def boom(cmd, timeout, cancel=None, **_kw):
        raise RuntimeError(f"secret {SECRET} at {ABS_PATH}")
        yield  # noqa
    monkeypatch.setattr(gaps, "stream_tree", boom)
    _commands(monkeypatch, "pass")
    svc.start_dependency_install("jieba", confirm=True)
    job = _wait(_finished)
    assert job["status"] == "error" and background_jobs.exclusive_active() is False
    shown = str(svc.get_dependency_install()) + str(job)
    assert SECRET not in shown and "/home/someone" not in shown
    assert "RuntimeError" in svc.get_dependency_install()["result"]["hint"]


def test_start_checks_run_before_a_job_exists(env, monkeypatch):
    with pytest.raises(gaps.AdminActionUnconfirmed):
        svc.start_dependency_install("jieba", confirm=False)
    with pytest.raises(gaps.AdminActionUnknownPackage):
        svc.start_dependency_install("fastapi", confirm=True)
    with pytest.raises(gaps.AdminActionUnconfirmed):
        svc.start_gpu_torch_setup("cpu", confirm=False)
    assert background_jobs.get_status(JOB) is None and background_jobs.exclusive_active() is False


def test_the_install_refuses_while_another_job_runs(env, monkeypatch):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: True)
    with pytest.raises(gaps.AdminActionJobsRunning):
        svc.start_dependency_install("jieba", confirm=True)
    assert background_jobs.get_status(JOB) is None


def test_a_job_that_appears_after_the_start_checks_stops_the_install(env, monkeypatch):
    """The start checks passed, then another process's job showed up: the
    re-check under the hold refuses and nothing is installed."""
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)
    monkeypatch.setattr(svc, "_other_job_running", lambda own: True)
    monkeypatch.setattr(gaps, "stream_tree",
                        lambda *a, **k: pytest.fail("pip must not run"))
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "error"
    assert background_jobs.exclusive_active() is False


def test_other_job_check_ignores_the_install_itself(env, monkeypatch):
    monkeypatch.setattr(background_jobs, "list_all_jobs",
                        lambda: {JOB: {"status": "running"}, "x": {"status": "done"}})
    monkeypatch.setattr(svc.db, "list_job_records",
                        lambda: [{"job_id": JOB, "status": "running", "updated_at": time.time()}])
    assert svc._other_job_running(JOB) is False
    monkeypatch.setattr(background_jobs, "list_all_jobs",
                        lambda: {JOB: {"status": "running"}, "x": {"status": "running"}})
    assert svc._other_job_running(JOB) is True


def test_gpu_torch_setup_runs_as_a_job_and_cancel_skips_the_check(env, monkeypatch):
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: None)
    monkeypatch.setattr(svc.gaps, "_python_supported", lambda: True)
    monkeypatch.setattr(gaps, "_torch_setup_commands",
                        lambda variant: [([sys.executable, "-c", "import time; print('x', flush=True); time.sleep(60)"], 60)])
    monkeypatch.setattr(gaps, "verify_torch", lambda **k: pytest.fail("no verify after Cancel"))
    assert svc.start_gpu_torch_setup("cpu", confirm=True)["started"] is True
    _wait(lambda: "x" in (background_jobs.get_status(JOB) or {}).get("message", ""))
    assert svc.get_dependency_install()["kind"] == "gpu_torch"
    background_jobs.request_cancel(JOB)
    assert _wait(_finished)["status"] == "cancelled"
    assert background_jobs.exclusive_active() is False


def _report(*pairs):
    return {"install": [{"metadata": {"name": n, "version": v}} for n, v in pairs]}


def test_install_now_refuses_a_blocked_plan(env, monkeypatch):
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys: (None, "conflict"))
    with pytest.raises(gaps.AdminActionNotPossible, match="can't be installed together"):
        svc.start_dependency_install("jieba", confirm=True)
    assert background_jobs.get_status(JOB) is None


def test_install_now_needs_the_downgrade_accepted(env, monkeypatch):
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys: (_report(("numpy", "1.0")), None))
    monkeypatch.setattr(pending_install, "snapshot", lambda: {"numpy": "2.0"})
    monkeypatch.setattr(install_plan, "app_required_names", lambda: {"numpy"})
    with pytest.raises(gaps.AdminActionRefused, match="downgrade") as exc:
        svc.start_dependency_install("jieba", confirm=True)
    assert getattr(exc.value, "http_status", 409) == 409
    assert background_jobs.get_status(JOB) is None
    _commands(monkeypatch, "pass")
    assert svc.start_dependency_install("jieba", confirm=True, accept_risk=True)["started"] is True
    assert _wait(_finished)["status"] == "done"


def test_install_now_refuses_what_must_wait_for_a_restart(env, monkeypatch):
    real = install_plan.build_plan
    monkeypatch.setattr(install_plan, "build_plan", lambda keys: real(
        keys, windows=True, report=_report(("numpy", "2.1")), installed={"numpy": "2.0"},
        in_use=lambda name: True, mins={}, required=set()))
    with pytest.raises(svc.InstallNeedsRestart, match="Queue it for the next start"):
        svc.start_dependency_install("jieba", confirm=True)
    assert background_jobs.get_status(JOB) is None


def test_a_read_only_install_folder_refuses_before_a_job_exists(env, monkeypatch):
    monkeypatch.setattr(svc.os, "access", lambda path, mode: False)
    monkeypatch.setattr(install_plan, "run_dry_run", lambda keys: pytest.fail("no preview"))
    with pytest.raises(gaps.AdminActionNotPossible, match="Stop the service"):
        svc.start_dependency_install("jieba", confirm=True)
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: None)
    monkeypatch.setattr(svc.gaps, "_python_supported", lambda: True)
    with pytest.raises(gaps.AdminActionNotPossible, match="Stop the service"):
        svc.start_gpu_torch_setup("cpu", confirm=True)
    assert background_jobs.get_status(JOB) is None


@pytest.fixture
def restore(monkeypatch):
    """`pip` (a fake runner) replaces numpy 2.0 with 2.1, then fails or is
    cancelled; the restore is asked to put 2.0 back."""
    state = {"numpy": "2.0", "calls": []}
    monkeypatch.setattr(pending_install, "snapshot", lambda: {"numpy": state["numpy"]})

    def fake_restore(before, now_versions, budget, echo):
        state["calls"].append((before, now_versions))
        return ["numpy"], []
    monkeypatch.setattr(pending_install, "_restore", fake_restore)
    _commands(monkeypatch, "pip")
    return state


def test_a_pip_failure_puts_the_earlier_packages_back(env, monkeypatch, restore):
    def fake_tree(cmd, timeout, cancel=None, **_kw):
        restore["numpy"] = "2.1"
        yield {"returncode": 1, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(gaps, "stream_tree", fake_tree)
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "error"
    assert restore["calls"] == [({"numpy": "2.0"}, {"numpy": "2.1"})]
    assert "Your earlier packages were put back." in svc.get_dependency_install()["result"]["hint"]
    assert background_jobs.exclusive_active() is False


def test_cancel_puts_the_earlier_packages_back(env, monkeypatch, restore):
    def fake_tree(cmd, timeout, cancel=None, **_kw):
        restore["numpy"] = "2.1"
        background_jobs.request_cancel(JOB)
        yield {"returncode": None, "timed_out": False, "cancelled": True}
    monkeypatch.setattr(gaps, "stream_tree", fake_tree)
    svc.start_dependency_install("jieba", confirm=True)
    assert _wait(_finished)["status"] == "cancelled"
    assert restore["calls"] == [({"numpy": "2.0"}, {"numpy": "2.1"})]
    result = svc.get_dependency_install()["result"]
    assert result["hint"] == "Cancelled. Your earlier packages were put back."
    assert background_jobs.exclusive_active() is False


def test_a_package_that_could_not_be_put_back_is_named(env, monkeypatch):
    monkeypatch.setattr(pending_install, "_restore",
                        lambda before, now, budget, echo: ([], ["numpy"]))
    _commands(monkeypatch, "import sys; sys.exit(1)")
    svc.start_dependency_install("jieba", confirm=True)
    _wait(_finished)
    hint = svc.get_dependency_install()["result"]["hint"]
    assert "could not be put back: numpy" in hint
