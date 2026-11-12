"""Windows primitives run for real (kernel32 mutexes, job objects, process handles) on the windows-scripts CI job; the rest of the suite fakes them."""
import os
import subprocess
import sys
import time

import pytest

from installer import setup_lock
from lib import file_lock, proc_kill

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="needs the Windows API")

SLEEPER = [sys.executable, "-c", "import time; time.sleep(60)"]


@pytest.fixture
def sleeper():
    proc = subprocess.Popen(SLEEPER)
    yield proc
    proc.kill()
    proc.wait(timeout=10)


@windows_only
def test_setup_mutexes_are_held_for_the_block_and_refuse_a_second_holder():
    first = setup_lock.WindowsMutexes()
    assert setup_lock.setup_is_open(first) is False
    with setup_lock.setup_lock(first):
        assert setup_lock.setup_is_open(setup_lock.WindowsMutexes()) is True
        with pytest.raises(setup_lock.SetupRunning):
            with setup_lock.setup_lock(setup_lock.WindowsMutexes()):
                pass
    # A refused attempt must not leave a name behind that blocks Setup later.
    assert setup_lock.setup_is_open(setup_lock.WindowsMutexes()) is False


@windows_only
def test_the_session_check_answers_for_this_process():
    # Session 0 is a service's; the CI job and a developer's shell are not in it.
    assert setup_lock.WindowsMutexes().shares_global_namespace() is False


@windows_only
def test_setup_mutexes_are_released_when_the_block_raises():
    with pytest.raises(RuntimeError):
        with setup_lock.setup_lock(setup_lock.WindowsMutexes()):
            raise RuntimeError("boom")
    assert setup_lock.setup_is_open(setup_lock.WindowsMutexes()) is False


def test_file_lock_conflicts_with_a_second_open_until_released(tmp_path):
    path = str(tmp_path / "x.lock")
    with file_lock.hold(path):
        fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
        try:
            assert file_lock._try_lock(fd) is False
        finally:
            os.close(fd)
    fd = os.open(path, os.O_RDWR | getattr(os, "O_BINARY", 0))
    try:
        assert file_lock._try_lock(fd) is True
        file_lock._unlock(fd)
    finally:
        os.close(fd)


def test_process_identity_stops_only_the_matching_process(sleeper):
    identity = proc_kill.process_identity(sleeper.pid)
    assert identity and identity["start"] and identity["cmd"]
    assert proc_kill.stop_if_same(sleeper.pid, {**identity, "start": "1"}) in ("gone", "kept")
    assert sleeper.poll() is None
    assert proc_kill.stop_if_same(sleeper.pid, identity) == "stopped"
    sleeper.wait(timeout=10)


def test_process_identity_of_a_gone_process_is_none():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=30)
    assert proc_kill.process_identity(proc.pid) is None


def test_kill_tree_ends_the_process(sleeper):
    proc_kill.kill_tree(sleeper)
    assert sleeper.wait(timeout=10) is not None


@windows_only
def test_job_object_group_ends_a_process_added_by_pid(sleeper):
    import process_guard
    name = f"Local\\BaiheTest-{os.getpid()}-{time.monotonic_ns()}"
    k = process_guard._kernel32()
    job = k.CreateJobObjectW(None, name)
    assert job
    try:
        assert process_guard.add_process_to_group(name, sleeper.pid) is True
        assert process_guard.terminate_group(name) is True
        assert sleeper.wait(timeout=10) == 1
    finally:
        k.CloseHandle(job)
    assert process_guard.terminate_group(name) is False


def _run_staging_script(tmp_path, source, digest):
    """installer/stage_service.ps1 the way Setup runs it: values in front of the script text,
    passed to powershell.exe as an encoded command."""
    import base64
    from pathlib import Path
    body = (Path(__file__).resolve().parent.parent / "installer" / "stage_service.ps1").read_text(encoding="ascii")
    header = "".join(f"${name} = '{value}'\r\n" for name, value in (
        ("Source", source), ("Staging", tmp_path / "staging"), ("Digest", digest),
        ("Root", tmp_path / "app"), ("Data", tmp_path / "data"), ("Extra", "")))
    encoded = base64.b64encode((header + body).encode("utf-16-le")).decode("ascii")
    run = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand", encoded],
                         capture_output=True, text=True, timeout=120)
    return run.returncode, run.stdout + run.stderr


def _service_tree(tmp_path):
    source = tmp_path / "service"
    (source / "helper" / "lib").mkdir(parents=True)
    (source / "helper" / "lib" / "a.txt").write_bytes(b"one")
    (source / "B.txt").write_bytes(b"two")
    return source


@windows_only
def test_staging_script_agrees_with_the_build_digest_and_reaches_the_run_step(tmp_path):
    from installer import build_installer
    source = _service_tree(tmp_path)
    code, out = _run_staging_script(tmp_path, source, build_installer.service_tree_digest(source))
    # No python.exe in this stand-in tree, so the verified copy fails when it is run (exit 4),
    # which is how this tells "digest accepted" (4) from "digest refused" (3).
    assert code == 4 and "Unexpected error" in out, out
    assert not (tmp_path / "staging").exists()


@windows_only
def test_staging_script_refuses_files_that_changed_after_the_build(tmp_path):
    from installer import build_installer
    source = _service_tree(tmp_path)
    digest = build_installer.service_tree_digest(source)
    (source / "helper" / "lib" / "a.txt").write_bytes(b"swapped")
    code, out = _run_staging_script(tmp_path, source, digest)
    assert code == 3 and "do not match" in out, out
    assert not (tmp_path / "staging").exists()


@windows_only
def test_staging_script_refuses_a_link_in_the_source(tmp_path):
    from installer import build_installer
    source = _service_tree(tmp_path)
    digest = build_installer.service_tree_digest(source)
    target = tmp_path / "elsewhere"
    target.mkdir()
    subprocess.run(["cmd", "/c", "mklink", "/J", str(source / "linked"), str(target)],
                   check=True, capture_output=True)
    code, out = _run_staging_script(tmp_path, source, digest)
    assert code == 3 and "contain a link" in out, out
