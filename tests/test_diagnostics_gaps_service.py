"""Tests for services/diagnostics_gaps_service.py (mocked, no network)."""

import getpass
import json
import os

import pytest

import applog
import background_jobs
import db
import diagnostics
from services import diagnostics_gaps_service as svc
from services import settings_service

SECRET_KEY = "sk-ant-api03-SECRETSECRETSECRET123456"
HF_TOKEN = "hf_AbCdEfGhIjKlMnOpQrStUvWxYz0123"
ABS_PATH = "/home/someone/private/library/drama.mp4"
WIN_PATH = r"C:\Users\someone\private\drama.mp4"
DIRTY = f"failed with {SECRET_KEY} token {HF_TOKEN} at {ABS_PATH} and {WIN_PATH}"


def _assert_clean(obj):
    text = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    for bad in (SECRET_KEY, HF_TOKEN, ABS_PATH, "/home/someone", "someone\\\\private",
                "Users\\\\someone", r"C:\Users"):
        assert bad not in text, bad
    user = getpass.getuser()
    if user and len(user) > 3:
        assert user not in text


@pytest.fixture
def dirty_jobs(monkeypatch):
    jobs = {
        "emotion_999999": {"status": "failed", "message": DIRTY, "error": DIRTY,
                           "description": DIRTY, "started_at": 10.0, "finished_at": 12.5},
        "custom_job": {"status": "done", "message": "ok", "started_at": 1.0, "finished_at": 2.0},
        "live_capture": {"status": "running", "message": DIRTY},
    }
    monkeypatch.setattr(background_jobs, "list_all_jobs", lambda: dict(jobs))
    return jobs


@pytest.fixture
def dirty_log(isolated_db):
    log_dir = os.path.join(db.LIBRARY_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "app.log"), "w", encoding="utf-8") as f:
        for i in range(500):
            f.write(f"INFO line {i}\n")
        f.write(f"ERROR {DIRTY}\n")


def test_describe_job_moved():
    assert svc.describe_job("x_y") == "x_y"


class TestDescribeJob:
    """describe_job() turns a raw job_id like 'emotion_42' into a
    human-readable line for the Running jobs panel (moved from
    tests/test_diagnostics_and_export.py)."""

    def test_known_prefix_includes_the_drama_title(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama")
        result = svc.describe_job(f"emotion_{did}")
        assert "Detecting emotional register" in result
        assert "Test Drama" in result

    def test_deleted_drama_says_so_instead_of_crashing(self, isolated_db):
        result = svc.describe_job("flag_999999")
        assert "deleted" in result

    def test_unrecognized_job_id_falls_back_to_the_raw_string(self):
        assert svc.describe_job("some_custom_thing") == "some_custom_thing"


def test_setup_checks_have_no_paths(isolated_db, monkeypatch):
    monkeypatch.setattr(diagnostics, "check_ffmpeg",
                        lambda: {"found": True, "path": ABS_PATH, "version": "ffmpeg 6 " + ABS_PATH})
    monkeypatch.setattr(diagnostics, "check_js_runtime",
                        lambda: {"found": True, "name": "deno", "path": ABS_PATH})
    monkeypatch.setattr(diagnostics, "check_cuda",
                        lambda: {"torch_installed": False, "cuda_available": None})
    out = svc.get_setup_checks()
    assert out["ffmpeg"]["found"] is True and out["js_runtime"]["name"] == "deno"
    assert out["library_writable"] is True
    assert isinstance(out["files"]["all_present"], bool)
    assert "path" not in out["ffmpeg"] and "path" not in out["js_runtime"]
    _assert_clean(out)


def test_model_versions_shape():
    rows = svc.get_model_versions("qwen2.5:7b")
    assert rows and rows[-1]["version"] == "qwen2.5:7b"
    assert all(isinstance(r["installed"], bool) for r in rows)


def test_model_cache_names_and_sizes_only(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "scan_hf_cache", lambda d=None: [
        {"repo_id": "org/model", "repo_type": "model", "revision": "abc", "size_bytes": 100}])
    import audio_preprocess
    checkpoints = tmp_path / "torch" / "hub" / "checkpoints"
    checkpoints.mkdir(parents=True)
    (checkpoints / "htdemucs.th").write_bytes(b"x" * 7)
    monkeypatch.setenv("TORCH_HOME", str(tmp_path / "torch"))
    sep = tmp_path / "sep"
    sep.mkdir()
    (sep / "vocals_mel_band_roformer.ckpt").write_bytes(b"x" * 20)
    monkeypatch.setattr(audio_preprocess, "MODEL_DIR", str(sep))
    out = svc.get_model_cache()
    assert out["hf_total_bytes"] == 100
    assert "piper_voices" not in out and "piper_total_bytes" not in out
    assert out["model_files"] == [
        {"folder": "torch", "name": "htdemucs.th", "size_bytes": 7},
        {"folder": "audio_separator", "name": "vocals_mel_band_roformer.ckpt", "size_bytes": 20}]
    assert out["model_files_total_bytes"] == 27
    assert str(tmp_path) not in json.dumps(out)


def test_pyannote_readiness_booleans_no_token(monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, env_path=None: HF_TOKEN)
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
    out = svc.get_pyannote_readiness()
    assert out == {"pyannote_installed": True, "hf_token_configured": True,
                   "models": None, "ready": True}

    class FakeApi:
        def model_info(self, model, token=None):
            assert token == HF_TOKEN
            raise RuntimeError(f"403 for {token} at {ABS_PATH}")

    out = svc.get_pyannote_readiness(check_access=True, api=FakeApi())
    assert out["models"] and all(m["accessible"] is False for m in out["models"])
    assert out["ready"] is False
    _assert_clean(out)


def test_pyannote_readiness_without_hf_hub_returns_models_none(monkeypatch):
    """Check requested but huggingface_hub can't be imported: models is None
    (the UI's "can't check" message), not an empty list."""
    import sys
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, env_path=None: HF_TOKEN)
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
    monkeypatch.setitem(sys.modules, "huggingface_hub", None)  # import raises ImportError
    out = svc.get_pyannote_readiness(check_access=True)
    assert out["models"] is None
    assert out["ready"] is True


def test_pyannote_readiness_with_hf_hub_returns_list(monkeypatch):
    """An installed huggingface_hub still yields one row per gated model."""
    import sys
    import types
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, env_path=None: HF_TOKEN)
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)

    class FakeHfApi:
        def model_info(self, model, token=None, timeout=None):
            return object()

    fake_hub = types.ModuleType("huggingface_hub")
    fake_hub.HfApi = FakeHfApi
    monkeypatch.setitem(sys.modules, "huggingface_hub", fake_hub)
    out = svc.get_pyannote_readiness(check_access=True)
    assert out["models"] == [{"model": m, "accessible": True}
                             for m in diagnostics.diarize.DIARIZATION_MODELS]
    assert out["ready"] is True


def test_job_history_redacted(isolated_db, dirty_jobs):
    hist = svc.get_job_history()
    assert [h["job_id"] for h in hist] == ["emotion_999999", "custom_job"]
    assert hist[0]["duration_seconds"] == 2.5
    assert "[REDACTED]" in hist[0]["error"]
    _assert_clean(hist)


def test_log_tail_capped_and_redacted(dirty_log):
    assert len(svc.get_log_tail(10_000)) == svc.LOG_TAIL_MAX
    assert len(svc.get_log_tail(5)) == 5
    assert svc.get_log_tail(-3) == []
    errs = svc.get_log_tail(5, keyword="ERROR")
    assert len(errs) == 1
    _assert_clean(svc.get_log_tail(10_000))


def test_support_report_clean(dirty_log, monkeypatch):
    monkeypatch.setattr(settings_service, "key_status",
                        lambda env_path=None: {"claude": True, "hf_token": False})
    monkeypatch.setattr(diagnostics, "scan_hf_cache", lambda d=None: [])
    report = svc.build_support_report()
    assert "OS:" in report and "Python:" in report and "Recent errors:" in report
    assert "API keys set: claude" in report
    _assert_clean(report)


def test_log_tail_and_support_report_strip_ansi(isolated_db, monkeypatch):
    """yt-dlp colours its errors; neither the Log view nor the support
    report should show the raw escape codes."""
    log_dir = os.path.join(db.LIBRARY_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "app.log"), "w", encoding="utf-8") as f:
        f.write("INFO started\n")
        f.write("ERROR download failed: \x1b[0;31mERROR:\x1b[0m [youtube] abc123: Sign in\n")
    tail = svc.get_log_tail(10)
    assert tail[-1].endswith("download failed: ERROR: [youtube] abc123: Sign in")
    assert not any("\x1b" in ln for ln in tail)
    monkeypatch.setattr(settings_service, "key_status", lambda env_path=None: {})
    monkeypatch.setattr(diagnostics, "scan_hf_cache", lambda d=None: [])
    report = svc.build_support_report()
    assert "ERROR: [youtube] abc123: Sign in" in report
    assert "\x1b" not in report


def _no_jobs(monkeypatch, running=False):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: running)


def _fake_pip(monkeypatch, returncode=0, timed_out=False, seen=None):
    def fake(cmd, timeout, cwd=None, env=None, **_kw):
        if seen is not None:
            seen.append((cmd, timeout))
        yield {"line": DIRTY}
        yield {"returncode": returncode, "timed_out": timed_out}
    monkeypatch.setattr(svc, "stream_tree", fake)


def test_log_keyword_filter_runs_on_redacted_text(dirty_log):
    """A keyword must not act as an oracle for redacted text."""
    assert svc.get_log_tail(200, keyword="someone") == []
    assert svc.get_log_tail(200, keyword="SECRETSECRET") == []
    assert svc.get_log_tail(200, keyword="private") == []
    assert len(svc.get_log_tail(200, keyword="ERROR")) == 1


@pytest.mark.parametrize("call", [
    lambda c: svc.install_dependency("pydub", confirm=c),
    lambda c: svc.upgrade_dependency("pydub", confirm=c),
    lambda c: svc.reset_library(confirm=c),
])
def test_admin_requires_confirm(call, monkeypatch):
    monkeypatch.setattr(db, "reset_library", lambda: pytest.fail("must not run"))
    monkeypatch.setattr(svc, "stream_tree", lambda *a, **k: pytest.fail("must not run"))
    _no_jobs(monkeypatch)
    for bad in (False, None, "yes", 1):
        with pytest.raises(svc.AdminActionRefused):
            call(bad)


def test_admin_refuses_while_jobs_run_here_or_elsewhere(monkeypatch):
    _no_jobs(monkeypatch, running=True)
    monkeypatch.setattr(db, "reset_library", lambda: pytest.fail("must not run"))
    monkeypatch.setattr(svc, "stream_tree", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(svc.AdminActionJobsRunning):
        svc.reset_library(confirm=True)
    with pytest.raises(svc.AdminActionJobsRunning):
        svc.install_dependency("pydub", confirm=True)


def test_guard_sees_queued_jobs_and_other_process_records(isolated_db, monkeypatch):
    monkeypatch.setattr(db, "reset_library", lambda: pytest.fail("must not run"))
    monkeypatch.setattr(background_jobs, "list_all_jobs",
                        lambda: {"translate_1": {"status": "queued"}})
    with pytest.raises(svc.AdminActionJobsRunning):
        svc.reset_library(confirm=True)
    import time as _t
    monkeypatch.setattr(background_jobs, "list_all_jobs", lambda: {})
    monkeypatch.setattr(db, "list_job_records", lambda: [
        {"job_id": "dub_3", "status": "running", "updated_at": _t.time()}])
    with pytest.raises(svc.AdminActionJobsRunning):
        svc.reset_library(confirm=True)


def test_install_rejects_unknown_package(monkeypatch):
    _no_jobs(monkeypatch)
    with pytest.raises(svc.AdminActionRefused):
        svc.install_dependency("evil-package; rm -rf /", confirm=True)
    with pytest.raises(svc.AdminActionRefused):
        svc.install_dependency("fastapi", confirm=True)  # required tier


def test_install_and_upgrade_run_with_timeout_and_redact(monkeypatch):
    _no_jobs(monkeypatch)
    # The command line depends on whether torch is installed on the machine
    # running the tests (it then gets a `-c <pins>` file); pin that down.
    monkeypatch.setattr(svc.gpu_torch, "torch_pin_lines", lambda: [])
    seen = []
    _fake_pip(monkeypatch, seen=seen)
    for fn in (svc.install_dependency, svc.upgrade_dependency):
        out = fn("pydub", confirm=True)
        assert out["ok"] is True and out["package"] == "pydub"
        _assert_clean(out)
    assert all(t == svc.PIP_TIMEOUT_SECONDS for _c, t in seen)
    assert seen[0][0][3:] == ["install", "--no-cache-dir", "--disable-pip-version-check",
                              "pydub", *CONSTRAINTS]


def test_install_pins_the_installed_torch_family_with_a_temporary_constraints_file(monkeypatch):
    _no_jobs(monkeypatch)
    monkeypatch.setattr(svc.gpu_torch, "torch_pin_lines",
                        lambda: ["torch==2.11.0+cpu", "torchaudio==2.11.0+cpu"])
    contents = {}

    def fake(cmd, timeout, cwd=None, env=None, **_kw):
        path = cmd[-1]     # the pins file is appended after constraints.txt
        with open(path, encoding="utf-8") as f:
            contents["pins"] = f.read().split()
        contents["path"] = path
        yield {"returncode": 0, "timed_out": False}
    monkeypatch.setattr(svc, "stream_tree", fake)
    assert svc.install_dependency("pydub", confirm=True)["ok"] is True
    assert contents["pins"] == ["torch==2.11.0+cpu", "torchaudio==2.11.0+cpu"]
    assert not os.path.exists(contents["path"])      # removed after the run


def test_pip_timeout_or_failure_is_not_ok(monkeypatch):
    _no_jobs(monkeypatch)
    _fake_pip(monkeypatch, returncode=-9, timed_out=True)
    out = svc.install_dependency("pydub", confirm=True)
    assert out["ok"] is False and "took too long" in out["output_tail"][-1]


def test_gpu_torch_install_never_uninstalls_first(monkeypatch):
    """L6: a killed download must leave the old torch in place, so no
    `pip uninstall`; force-reinstall (no deps) from the CUDA index, then a
    plain install for missing deps, both with the long timeout."""
    _no_jobs(monkeypatch)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(svc, "installable_packages", lambda: {"torch"})
    seen = []
    _fake_pip(monkeypatch, seen=seen)
    assert svc.install_dependency("torch", confirm=True)["ok"] is True
    assert not any("uninstall" in cmd for cmd, _t in seen)
    first, second = seen[0][0], seen[1][0]
    assert first[3:6] == ["install", "--no-cache-dir", "--disable-pip-version-check"]
    assert first[6:11] == ["--force-reinstall", "--no-deps", "torch==2.11.0+cu128",
                           "torchvision==0.26.0+cu128", "torchaudio==2.11.0+cu128"]
    assert "--index-url" in first and "--index-url" in second
    assert "--force-reinstall" not in second
    assert all(t == svc.GPU_TORCH_TIMEOUT_SECONDS == 3600 for _c, t in seen)


@pytest.mark.parametrize("name", ["torch", "torchaudio", "torchvision"])
def test_torch_family_installs_the_matched_triple(monkeypatch, name):
    """A plain `pip install torchaudio` could swap a CUDA torch for a CPU
    one, so on an NVIDIA machine any of the three installs all three,
    pinned together, from the fixed CUDA index."""
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    cmds = svc._install_commands(name)
    assert len(cmds) == 2
    for cmd, timeout in cmds:
        assert cmd[cmd.index("--index-url") + 1] == "https://download.pytorch.org/whl/cu128"
        assert {"torch==2.11.0+cu128", "torchvision==0.26.0+cu128",
                "torchaudio==2.11.0+cu128"} <= set(cmd)
        assert "uninstall" not in cmd
        assert timeout == svc.GPU_TORCH_TIMEOUT_SECONDS


def test_torchaudio_is_a_plain_install_without_a_gpu(monkeypatch):
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: None)
    ((cmd, timeout),) = svc._install_commands("torchaudio")
    assert cmd[3:] == ["install", "--no-cache-dir", "--disable-pip-version-check",
                       "torchaudio", *CONSTRAINTS]
    assert "--index-url" not in cmd
    assert timeout == svc.PIP_TIMEOUT_SECONDS


def test_gpu_torch_timeout_stops_before_the_second_step(monkeypatch):
    _no_jobs(monkeypatch)
    import shutil
    monkeypatch.setattr(shutil, "which", lambda name: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(svc, "installable_packages", lambda: {"torch"})
    seen = []
    _fake_pip(monkeypatch, returncode=-9, timed_out=True, seen=seen)
    assert svc.install_dependency("torch", confirm=True)["ok"] is False
    assert len(seen) == 1


def test_pip_holds_the_library_exclusively(monkeypatch):
    """L7: no job, restore, reset or second install can start mid-upgrade."""
    _no_jobs(monkeypatch)
    seen = {}

    def fake(cmd, timeout, **_kw):
        seen["exclusive"] = background_jobs.exclusive_active()
        seen["job_started"] = background_jobs.start_job("l7_probe", lambda: None)
        seen["maintenance"] = background_jobs.enter_maintenance()
        yield {"returncode": 0, "timed_out": False}
    monkeypatch.setattr(svc, "stream_tree", fake)
    assert svc.install_dependency("pydub", confirm=True)["ok"] is True
    assert seen == {"exclusive": True, "job_started": False, "maintenance": False}
    assert background_jobs.exclusive_active() is False
    background_jobs.clear_job("l7_probe")


def test_pip_refused_while_another_hold_is_active(monkeypatch):
    _no_jobs(monkeypatch)
    _fake_pip(monkeypatch)
    # a hold taken between _guard and the acquire (e.g. a restore) -> 409
    monkeypatch.setattr(background_jobs, "acquire_exclusive", lambda label: False)
    with pytest.raises(svc.AdminActionJobsRunning):
        svc.install_dependency("pydub", confirm=True)


def test_pip_releases_the_hold_when_it_fails(monkeypatch):
    _no_jobs(monkeypatch)

    def boom(cmd, timeout, **_kw):
        raise OSError("no pip")
        yield  # noqa
    monkeypatch.setattr(svc, "stream_tree", boom)
    with pytest.raises(OSError):
        svc.install_dependency("pydub", confirm=True)
    assert background_jobs.exclusive_active() is False


@pytest.mark.skipif(os.name == "nt", reason="POSIX process groups")
def test_stream_tree_kills_the_whole_tree_on_timeout(tmp_path):
    """L7: a child that pip started (here: a grandchild sleeper) dies too."""
    import sys
    import time as _t
    marker = tmp_path / "grandchild.pid"
    script = ("import subprocess, sys, time\n"
              "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'])\n"
              f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
              "print('started', flush=True)\n"
              "time.sleep(60)\n")
    t0 = _t.monotonic()
    items = list(svc.stream_tree([sys.executable, "-c", script], timeout=1.0))
    assert _t.monotonic() - t0 < 30
    assert items[-1]["timed_out"] is True and items[0] == {"line": "started"}
    gpid = int(marker.read_text())
    for _ in range(100):
        try:
            os.kill(gpid, 0)
        except ProcessLookupError:
            break
        # a zombie reparented to init is reaped quickly; poll briefly
        _t.sleep(0.05)
    else:
        # killed but not reaped (no init reaper in some containers): a zombie
        with open(f"/proc/{gpid}/status") as fh:
            assert "zombie" in fh.read().lower(), "grandchild still alive"


def test_reset_runs_when_confirmed(monkeypatch):
    calls = []
    _no_jobs(monkeypatch)
    monkeypatch.setattr(db, "reset_library", lambda: calls.append("reset"))
    monkeypatch.setattr(background_jobs, "clear_all_jobs", lambda: calls.append("clear"))
    assert svc.reset_library(confirm=True)["ok"] is True
    assert calls == ["reset", "clear"]


def test_admin_refuses_during_exclusive_hold_or_maintenance(monkeypatch):
    _no_jobs(monkeypatch)
    monkeypatch.setattr(db, "reset_library", lambda: pytest.fail("must not run"))
    monkeypatch.setattr(svc, "stream_tree", lambda *a, **k: pytest.fail("must not run"))
    assert background_jobs.acquire_exclusive("Library restore")
    try:
        for call in (lambda: svc.reset_library(confirm=True, confirm_text="RESET"),
                     lambda: svc.install_dependency("pydub", confirm=True)):
            with pytest.raises(svc.AdminActionJobsRunning):
                call()
    finally:
        background_jobs.release_exclusive()
    assert background_jobs.enter_maintenance()
    try:
        with pytest.raises(svc.AdminActionJobsRunning):
            svc.reset_library(confirm=True, confirm_text="RESET")
    finally:
        background_jobs.exit_maintenance()


def test_reset_holds_the_library_exclusively(monkeypatch):
    _no_jobs(monkeypatch)
    seen = {}

    def fake_reset():
        seen["exclusive"] = background_jobs.exclusive_active()
        seen["started"] = background_jobs.start_job("m3_probe", lambda: None)
    monkeypatch.setattr(db, "reset_library", fake_reset)
    assert svc.reset_library(confirm=True, confirm_text="RESET")["ok"] is True
    assert seen == {"exclusive": True, "started": False}
    assert background_jobs.exclusive_active() is False
    background_jobs.clear_job("m3_probe")


def test_reset_waits_for_a_finished_jobs_thread_to_exit(isolated_db, monkeypatch):
    """A job reads as done while its thread still writes to the database
    (notification, timing row). Deleting and recreating the database under
    it failed the reset with "database is locked"; the reset must wait."""
    import threading
    order = []
    in_tail, release, waiting = threading.Event(), threading.Event(), threading.Event()

    def blocked_notify(*args, **kwargs):
        in_tail.set()
        release.wait(10)
        order.append("job thread done with the database")

    real_wait = background_jobs.wait_for_job_threads

    def spy_wait(timeout):
        waiting.set()
        return real_wait(timeout)

    monkeypatch.setattr(background_jobs, "_notify_job_finished", blocked_notify)
    monkeypatch.setattr(background_jobs, "wait_for_job_threads", spy_wait)
    monkeypatch.setattr(db, "reset_library", lambda: order.append("reset"))
    assert background_jobs.start_job("translate_1", lambda: None)
    assert in_tail.wait(10)
    assert background_jobs.get_status("translate_1")["status"] == "done"

    result = {}
    resetter = threading.Thread(
        target=lambda: result.update(svc.reset_library(confirm=True, confirm_text="RESET")))
    resetter.start()
    try:
        assert waiting.wait(10), "the reset did not wait for the job's thread"
    finally:
        release.set()
    resetter.join(10)
    assert result.get("ok") is True
    assert order == ["job thread done with the database", "reset"]


def test_reset_releases_the_hold_when_it_fails(monkeypatch):
    _no_jobs(monkeypatch)

    def boom():
        raise OSError("disk")
    monkeypatch.setattr(db, "reset_library", boom)
    with pytest.raises(OSError):
        svc.reset_library(confirm=True, confirm_text="RESET")
    assert background_jobs.exclusive_active() is False


def _pipe_holder_script(marker, child_sleeps):
    # the grandchild gets its own session, so killing pip's process group
    # misses it (as when taskkill fails), and it inherits stdout
    return ("import subprocess, sys, time\n"
            "p = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'],"
            " start_new_session=True)\n"
            f"open({str(marker)!r}, 'w').write(str(p.pid))\n"
            "print('started', flush=True)\n"
            f"time.sleep({child_sleeps})\n")


def _kill_pid_from(marker):
    import signal
    try:
        os.kill(int(marker.read_text()), signal.SIGKILL)
    except (OSError, ValueError):
        pass


@pytest.mark.skipif(os.name == "nt", reason="POSIX sessions")
def test_stream_tree_drain_is_bounded_when_a_survivor_holds_the_pipe(tmp_path):
    """A grandchild outside the killed group keeps stdout open after the
    timeout kill: the stream still ends within the drain bound."""
    import sys
    import time as _t
    marker = tmp_path / "gc.pid"
    try:
        t0 = _t.monotonic()
        items = list(svc.stream_tree([sys.executable, "-c", _pipe_holder_script(marker, 60)],
                                      timeout=1.0, drain_seconds=1.0))
        assert _t.monotonic() - t0 < 15
        assert items[0] == {"line": "started"} and items[-1]["timed_out"] is True
    finally:
        _kill_pid_from(marker)


@pytest.mark.skipif(os.name == "nt", reason="POSIX sessions")
def test_stream_tree_returns_when_pip_exits_but_a_child_holds_the_pipe(tmp_path):
    import sys
    import time as _t
    marker = tmp_path / "gc.pid"
    try:
        t0 = _t.monotonic()
        items = list(svc.stream_tree([sys.executable, "-c", _pipe_holder_script(marker, 0)],
                                      timeout=60.0, drain_seconds=1.0))
        assert _t.monotonic() - t0 < 15
        assert items[-1] == {"returncode": 0, "timed_out": False, "cancelled": False}
    finally:
        _kill_pid_from(marker)


def test_hold_released_when_a_hung_install_is_cut_off(monkeypatch, tmp_path):
    """End to end through install_dependency: the survivor can't keep the
    exclusive hold."""
    import sys
    if os.name == "nt":
        pytest.skip("POSIX sessions")
    _no_jobs(monkeypatch)
    marker = tmp_path / "gc.pid"
    real = svc.stream_tree
    monkeypatch.setattr(svc, "_install_commands", lambda n: [
        ([sys.executable, "-c", _pipe_holder_script(marker, 60)], 1.0)])
    monkeypatch.setattr(svc, "stream_tree",
                        lambda cmd, timeout, **kw: real(cmd, timeout, drain_seconds=1.0, **kw))
    try:
        out = svc.install_dependency("pydub", confirm=True)
        assert out["ok"] is False
        assert background_jobs.exclusive_active() is False
    finally:
        _kill_pid_from(marker)


def test_pip_rechecks_other_process_jobs_under_the_hold(monkeypatch):
    from services import library_admin_service
    answers = iter([False, True])      # _guard: none; under the hold: one appeared
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: next(answers))
    monkeypatch.setattr(svc, "stream_tree", lambda *a, **k: pytest.fail("must not run"))
    with pytest.raises(svc.AdminActionJobsRunning):
        svc.install_dependency("pydub", confirm=True)
    assert background_jobs.exclusive_active() is False


# --- transformers (Qwen3-ASR's runtime) installs as plain pip; no qwen-asr special case ---

def _scripted_pip(monkeypatch, outputs):
    """stream_tree stand-in: each pip run takes the next (lines, returncode)."""
    seen, runs = [], iter(outputs)

    def fake(cmd, timeout, cwd=None, env=None, **_kw):
        seen.append(cmd)
        lines, rc = next(runs)
        for line in lines:
            yield {"line": line}
        yield {"returncode": rc, "timed_out": False}
    monkeypatch.setattr(svc, "stream_tree", fake)
    monkeypatch.setattr(svc.gpu_torch, "torch_pin_lines", lambda: [])
    return seen


CONSTRAINTS = ["-c", os.path.join(svc.default_project_root(), "constraints.txt")]


def test_transformers_install_is_one_plain_pip_run(monkeypatch):
    _no_jobs(monkeypatch)
    seen = _scripted_pip(monkeypatch, [(["Successfully installed transformers-5.19.0"], 0)])
    out = svc.install_dependency("transformers", confirm=True)
    assert out["ok"] is True and out["hint"] is None
    assert [c[3:] for c in seen] == [["install", "--no-cache-dir", "--disable-pip-version-check",
                                      "transformers", *CONSTRAINTS]]


def test_a_failed_install_is_not_retried_and_the_sox_fallback_is_gone(monkeypatch):
    _no_jobs(monkeypatch)
    seen = _scripted_pip(monkeypatch, [(["Collecting torch", "ERROR: no matching distribution"], 1)])
    out = svc.install_dependency("transformers", confirm=True)
    assert out["ok"] is False and out["hint"] is None and len(seen) == 1
    assert not hasattr(diagnostics, "SoxBuildWatch") and not hasattr(svc, "_install_qwen_asr")

