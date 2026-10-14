"""Installs queued for the next start (pending_install.py): the file, its
tamper checks, the apply step with a fake pip, the lock and the timeouts.
No real pip and no network; children are faked unless a test says otherwise."""
import json
import os
import subprocess
import sys
import time

import pytest

import pending_install as pi
import pending_install_child as child

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


@pytest.fixture(autouse=True)
def _no_lock_outlives_its_test():
    # A held fd left in _LOCK points the next test's lock refresh at a dead file.
    yield
    pi._release_lock()
    pi._LOCK.clear()
    pi._LOCK["fd"] = None


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
    return tmp_path


class Fake:
    """Stands in for the children and pip; records every pip run."""

    def __init__(self, monkeypatch, versions, rcs=(0,), derive=None):
        self.runs, self.versions, self.rcs = [], list(versions), list(rcs)
        self.derive = derive or {"ok": True, "temp_files": [],
                                 "argv": [sys.executable, "-m", "pip", "install", "paddleocr"]}
        monkeypatch.setattr(pi, "child_json", self.child_json)
        monkeypatch.setattr(pi, "run_capture", self.run_capture)
        monkeypatch.setattr(pi, "snapshot", self.snapshot)
        monkeypatch.setattr(pi, "_python", lambda: ["python"])

    def child_json(self, *args, stdin_text=None):
        if args[0] == "derive":
            return self.derive
        return {"lines": [f"clean:{ln}" for ln in stdin_text.splitlines()]}

    def run_capture(self, argv, timeout, echo=False):
        self.runs.append(list(argv))
        rc = self.rcs.pop(0) if self.rcs else 0
        if rc == "timeout":
            return None, ["still working"], True
        return rc, ["line one", "line two"], False

    def snapshot(self):
        return dict(self.versions.pop(0))


def test_pending_file_holds_keys_and_checks_out(data):
    pi.write_pending(["paddleocr"], {"numpy": "2.5.3"})
    payload, problem = pi.read_pending()
    assert problem is None and payload["packages"] == ["paddleocr"]
    assert payload["before"] == {"numpy": "2.5.3"}
    doc = json.loads((data / "pending_install" / "pending.json").read_text())
    assert doc["sha256"] == pi.digest(doc["payload"])


def test_nothing_queued_is_not_a_problem(data):
    assert pi.read_pending() == (None, None)


@pytest.mark.parametrize("edit", [
    lambda d: d["payload"].update(packages=["evil"]),
    lambda d: d["payload"].update(packages=["paddleocr", "--index-url=http://x"]),
    lambda d: d.update(sha256="0" * 64),
])
def test_tampered_file_is_refused(data, edit):
    pi.write_pending(["paddleocr"])
    path = data / "pending_install" / "pending.json"
    doc = json.loads(path.read_text())
    edit(doc)
    path.write_text(json.dumps(doc))
    payload, problem = pi.read_pending()
    assert payload is None and problem in ("modified", "invalid")


@pytest.mark.parametrize("keys", [[], ["a b"], ["x;y"], ["--pre"], ["../etc"], [1], "paddleocr"])
def test_unsafe_key_lists_are_invalid_even_with_a_matching_hash(data, keys):
    payload = {"schema": 1, "packages": keys, "created": 1, "before": {}}
    pi._write_json("pending", {"payload": payload, "sha256": pi.digest(payload)})
    assert pi.read_pending() == (None, "invalid")


def test_apply_without_a_pending_file_does_nothing(data, monkeypatch):
    fake = Fake(monkeypatch, [])
    assert pi.apply() == {"ran": False}
    assert fake.runs == [] and not (data / "pending_install").exists()


def test_apply_success_writes_result_and_clears_pending_and_lock(data, monkeypatch):
    pi.write_pending(["paddleocr"])
    fake = Fake(monkeypatch, [{"numpy": "2.5.3"}])
    out = pi.apply()
    assert out["ran"] and out["status"] == "ok"
    assert fake.runs == [[sys.executable, "-m", "pip", "install", "paddleocr"]]
    result = pi.read_result()
    assert result["status"] == "ok" and "paddleocr" in result["message"]
    assert result["tail"] == ["clean:line one", "clean:line two"]       # redacted by the child
    assert pi.read_pending() == (None, None)
    assert not (data / "pending_install" / "apply.lock").exists()


def test_failure_puts_back_what_pip_removed(data, monkeypatch):
    pi.write_pending(["paddleocr"])
    before = {"numpy": "2.5.3", "pyyaml": "6.0.2", "requests": "2.33.0"}
    after_fail = {"numpy": "2.3.5", "requests": "2.33.0"}        # pyyaml gone, numpy moved
    fake = Fake(monkeypatch, [before, after_fail, before], rcs=(1, 0))
    out = pi.apply()
    assert out["status"] == "failed"
    restore = fake.runs[1]
    assert "--no-deps" in restore
    assert "numpy==2.5.3" in restore and "pyyaml==6.0.2" in restore
    assert not any(a.startswith("requests==") for a in restore)    # untouched, left alone
    assert out["restored"] == ["numpy", "pyyaml"] and out["restore_failed"] == []
    assert "put back" in out["message"]


def test_restore_that_cannot_finish_says_which_packages(data, monkeypatch):
    pi.write_pending(["paddleocr"])
    before = {"numpy": "2.5.3", "torch": "2.11.0+cu128"}
    Fake(monkeypatch, [before, {}, {"numpy": "2.5.3"}], rcs=(1, 1))
    out = pi.apply()
    assert out["restored"] == ["numpy"]
    # A local build tag isn't on PyPI, so it is reported rather than guessed at.
    assert out["restore_failed"] == ["torch"]
    assert "torch" in out["message"] and "Install them again" in out["message"]


def test_timeout_stops_pip_and_restores(data, monkeypatch):
    pi.write_pending(["paddleocr"])
    fake = Fake(monkeypatch, [{"numpy": "2.5.3"}, {}, {"numpy": "2.5.3"}], rcs=("timeout", 0))
    out = pi.apply()
    assert out["status"] == "timed_out" and "did not finish in time" in out["message"]
    assert len(fake.runs) == 2


def test_modified_file_never_reaches_pip(data, monkeypatch):
    pi.write_pending(["paddleocr"])
    path = data / "pending_install" / "pending.json"
    doc = json.loads(path.read_text())
    doc["payload"]["packages"] = ["paddleocr", "evil"]
    path.write_text(json.dumps(doc))
    fake = Fake(monkeypatch, [])
    out = pi.apply()
    assert out["status"] == "refused" and fake.runs == []
    assert pi.read_pending() == (None, None)


def test_registry_refusal_never_reaches_pip(data, monkeypatch):
    pi.write_pending(["paddleocr"])
    fake = Fake(monkeypatch, [], derive={"ok": False, "message": "No."})
    out = pi.apply()
    assert out["status"] == "refused" and out["message"] == "No." and fake.runs == []


def test_a_crash_cannot_rerun_the_install_on_every_start(data, monkeypatch):
    pi.write_pending(["paddleocr"])
    Fake(monkeypatch, [{}])
    monkeypatch.setattr(pi, "run_capture", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        pi.apply()
    assert pi.read_pending() == (None, None)                    # consumed before pip ran
    assert pi.read_result()["status"] == "running"
    assert pi.status()["result"]["status"] == "interrupted"     # no live lock: said plainly
    assert not (data / "pending_install" / "apply.lock").exists()


def test_a_live_lock_means_a_second_start_does_not_run_it_twice(data, monkeypatch):
    pi.write_pending(["paddleocr"])
    fake = Fake(monkeypatch, [])
    (data / "pending_install").mkdir(exist_ok=True)
    (data / "pending_install" / "apply.lock").write_text("{}")
    assert pi.apply(wait_seconds=0) == {"ran": False, "locked": True}
    assert fake.runs == [] and pi.read_pending()[0] is not None


def test_second_start_waits_for_the_first_then_finds_nothing_to_do(data, monkeypatch):
    lock = data / "pending_install" / "apply.lock"
    os.makedirs(lock.parent)
    lock.write_text("{}")

    def first_start_finishes(_seconds):
        lock.unlink()
    assert pi._acquire_lock(time.time() + 30, sleep=first_start_finishes) is True


def test_stale_lock_from_a_crash_is_taken_over(data):
    lock = data / "pending_install" / "apply.lock"
    os.makedirs(lock.parent)
    lock.write_text("{}")
    old = time.time() - pi.OVERALL_SECONDS - 600
    os.utime(lock, (old, old))
    assert pi.apply_running() is False
    assert pi._acquire_lock(time.time()) is True


def _write_lock(data, pid):
    lock = data / "pending_install" / "apply.lock"
    os.makedirs(lock.parent, exist_ok=True)
    lock.write_text(json.dumps({"pid": pid, "time": int(time.time())}))
    return lock


def _dead_pid():
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait()
    return proc.pid


def test_fresh_lock_of_a_dead_pid_is_stale_and_taken_over(data):
    _write_lock(data, _dead_pid())
    assert pi.apply_running() is False
    assert pi._acquire_lock(time.time()) is True


def test_lock_with_our_own_pid_is_running(data):
    _write_lock(data, os.getpid())
    assert pi.apply_running() is True


def test_old_lock_of_a_live_pid_is_still_stale(data):
    lock = _write_lock(data, os.getpid())     # pid reuse: the age rule still applies
    old = time.time() - pi.OVERALL_SECONDS - 600
    os.utime(lock, (old, old))
    assert pi.apply_running() is False


def test_two_takeovers_of_one_dead_lock_let_exactly_one_start_run(data, monkeypatch):
    import threading
    _write_lock(data, _dead_pid())
    together = threading.Barrier(2, timeout=10)
    readers = []
    real = pi._left_by_live_start

    def slow_read(fd):
        readers.append(fd)
        time.sleep(0.5)            # both read the dead pid here if the file alone decided
        return real(fd)
    monkeypatch.setattr(pi, "_left_by_live_start", slow_read)

    def start():
        together.wait()
        results.append(pi._acquire_lock(time.time()))
    results = []
    starts = [threading.Thread(target=start) for _ in range(2)]
    for t in starts:
        t.start()
    for t in starts:
        t.join(15)
    try:
        assert sorted(results) == [False, True]
        assert len(readers) == 1
        assert json.loads((data / "pending_install" / "apply.lock").read_text())["pid"] == os.getpid()
    finally:
        pi._release_lock()
    assert not (data / "pending_install" / "apply.lock").exists()


def test_a_lock_another_process_holds_is_never_taken_even_if_its_record_says_dead(data):
    os.makedirs(data / "pending_install")
    holder = subprocess.Popen(
        [sys.executable, "-c",
         "import sys, time, pending_install as pi\n"
         "print(pi._acquire_lock(time.time()), flush=True)\n"
         "sys.stdin.readline()\n"
         "pi._release_lock()\n"],
        cwd=ROOT, env={**os.environ, "BAIHE_DATA_DIR": str(data)},
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert holder.stdout.readline().strip() == "True"
        lock = data / "pending_install" / "apply.lock"
        lock.write_text(json.dumps({"pid": _dead_pid(), "time": int(time.time())}))
        assert pi.apply_running() is True
        assert pi._acquire_lock(time.time()) is False
        assert lock.exists()
    finally:
        holder.communicate("\n", timeout=15)
    assert pi._acquire_lock(time.time()) is True
    pi._release_lock()


def test_the_lock_records_our_start_time_beside_the_pid(data):
    assert pi._acquire_lock(time.time()) is True
    try:
        doc = json.loads((data / "pending_install" / "apply.lock").read_text())
        assert doc["pid"] == os.getpid()
        assert doc.get("started") == pi._process_start(os.getpid())
    finally:
        pi._release_lock()


def test_a_reused_pid_with_another_start_time_is_dead(data):
    started = pi._process_start(os.getpid())
    if started is None:
        pytest.skip("no process start time on this platform")
    lock = _write_lock(data, os.getpid())
    lock.write_text(json.dumps({"pid": os.getpid(), "started": started + 1}))
    assert pi.apply_running() is False
    lock.write_text(json.dumps({"pid": os.getpid(), "started": started}))
    assert pi.apply_running() is True


@pytest.mark.parametrize("pid", [2 ** 70, 2 ** 32, -5])
def test_an_impossible_pid_is_dead_not_an_error(data, pid):
    _write_lock(data, pid)
    assert pi.apply_running() is False
    assert pi._acquire_lock(time.time()) is True
    pi._release_lock()


def test_watchdog_result_keeps_the_packages_the_running_result_named(data, monkeypatch):
    pi.write_pending(["paddleocr"])
    Fake(monkeypatch, [{"numpy": "1"}])

    def seen_mid_pip(argv, timeout, echo=False):
        seen.update(pi.read_result())
        return None, [], True
    seen = {}
    monkeypatch.setattr(pi, "run_capture", seen_mid_pip)
    pi._CURRENT["stop"] = True      # skips the restore, as the watchdog does
    try:
        pi.apply()
    finally:
        pi._CURRENT["stop"] = False
    assert seen["status"] == "running" and seen["before"] == {"numpy": "1"}
    pi._write_json("result", seen)
    out = pi._watchdog_result()
    assert out["status"] == "timed_out"
    assert out["packages"] == ["paddleocr"] and out["before"] == {"numpy": "1"}
    assert "versions may have been changed" in out["message"] and "Packages in Diagnostics" in out["message"]


def test_watchdog_result_without_a_running_result_has_no_packages(data):
    out = pi._watchdog_result()
    assert out["packages"] == [] and out["before"] == {}


def test_main_always_exits_zero_so_the_server_still_starts(data, monkeypatch):
    monkeypatch.setattr(pi, "apply", lambda **k: (_ for _ in ()).throw(OSError("disk")))
    assert pi.main([]) == 0


def test_run_capture_kills_a_command_that_outlives_its_budget():
    start = time.time()
    rc, _lines, timed_out = pi.run_capture([sys.executable, "-c", "import time; time.sleep(60)"], 1)
    assert timed_out is True and time.time() - start < 20


def test_run_capture_returns_the_output_tail():
    rc, lines, timed_out = pi.run_capture([sys.executable, "-c", "print('a'); print('b')"], 30)
    assert (rc, lines, timed_out) == (0, ["a", "b"], False)


def test_cancel_removes_the_pending_file(data):
    pi.write_pending(["paddleocr"])
    assert pi.cancel() is True and pi.cancel() is False
    assert pi.status()["pending"] is None


def test_the_apply_step_does_not_import_what_pip_is_about_to_replace():
    """If this process loaded numpy or cv2 it would hold the files pip replaces."""
    code = ("import sys, pending_install\n"
            "bad = {'numpy', 'cv2', 'PIL', 'torch', 'diagnostics', 'requests'} & set(sys.modules)\n"
            "assert not bad, bad")
    subprocess.run([sys.executable, "-c", code], cwd=ROOT, check=True, timeout=60)


# --- the child: the pip command is re-derived from the registry ---------------

def test_derive_builds_a_fixed_literal_argv_from_keys(monkeypatch):
    import diagnostics
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda d: None)
    out = child.derive("paddleocr,paddlepaddle,cv2")
    argv = out["argv"]
    assert argv[1:4] == ["-m", "pip", "install"]
    assert list(diagnostics.PIP_INSTALL_FLAGS) == argv[4:4 + len(diagnostics.PIP_INSTALL_FLAGS)]
    assert argv[4 + len(diagnostics.PIP_INSTALL_FLAGS):][:3] == ["paddleocr", "paddlepaddle", "opencv-python"]
    assert "-c" in argv and argv[argv.index("-c") + 1].endswith("constraints.txt")
    assert not any(a.startswith("--index-url") or a.startswith("http") for a in argv)


@pytest.mark.parametrize("arg", ["", "evil", "paddleocr,--index-url=http://x", "paddleocr,../x",
                                 "lightnovel-crawler"])
def test_derive_refuses_names_the_registry_does_not_offer(arg):
    out = child.derive(arg)
    assert out["ok"] is False and "Nothing was changed" in out["message"]


def test_derive_refuses_torch_on_an_nvidia_pc(monkeypatch):
    import install_registry
    monkeypatch.setattr(install_registry.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    assert child.derive("torch")["ok"] is False


def test_torch_family_is_pinned_so_a_dependency_cannot_swap_it(monkeypatch):
    import diagnostics
    monkeypatch.setattr(diagnostics, "get_installed_version",
                        lambda d: "2.11.0+cu128" if d == "torch" else None)
    out = child.derive("paddleocr")
    pins = out["temp_files"][0]
    try:
        assert open(pins).read().strip() == "torch==2.11.0+cu128"
        assert out["argv"][-2:] == ["-c", pins]
    finally:
        os.remove(pins)


def test_redact_hides_paths_and_secrets():
    out = child.redact("ERROR C:\\Users\\Someone\\AppData\\Local\\x\\cv2.pyd sk-ant-api03-SECRETSECRETSECRET1234")
    text = "\n".join(out["lines"])
    assert "Someone" not in text and "SECRETSECRET" not in text


def test_the_real_child_process_round_trips():
    doc = pi.child_json("derive", "paddleocr")
    assert doc["ok"] is True and doc["argv"][1:4] == ["-m", "pip", "install"]
    assert pi._argv_accepted(doc["argv"])
    for path in doc["temp_files"]:
        os.remove(path)
    assert pi.child_json("derive", "evil")["ok"] is False


# --- start.bat and the launcher run it first ----------------------------------

def test_start_scripts_apply_before_the_server_starts():
    bat = open(os.path.join(ROOT, "start.bat"), encoding="utf-8").read()
    # After "already running?" so a live server's files are never replaced under it.
    assert (bat.index("REM --- Already running?") < bat.index("%PY% -m pending_install")
            < bat.index("%PY% -m api"))
    ps1 = open(os.path.join(ROOT, "start.ps1"), encoding="utf-8").read()
    assert ps1.index("-m pending_install") < ps1.index('"-m", "api"')


def test_launcher_applies_only_when_something_is_queued(data, monkeypatch):
    sys.path.insert(0, os.path.join(ROOT, "installer"))
    import launcher
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: calls.append((a, k)))
    launcher.apply_pending_install("python.exe", {}, headless=True)
    assert calls == []
    assert str(launcher.pending_install_file()) == pi._path("pending")
    pi.write_pending(["paddleocr"])
    launcher.apply_pending_install("python.exe", {"A": "b"}, headless=True)
    (argv,), kw = calls[0]
    assert argv == ["python.exe", "-s", "-m", "pending_install"]
    assert kw["timeout"] > pi.OVERALL_SECONDS and kw["cwd"] == str(launcher.APP_DIR)

    def boom(*a, **k):
        raise OSError("gone")
    monkeypatch.setattr(launcher.subprocess, "run", boom)
    launcher.apply_pending_install("python.exe", {}, headless=True)      # never raises


def test_second_start_waits_while_an_apply_runs_with_nothing_queued(data, monkeypatch):
    lock = data / "pending_install" / "apply.lock"
    os.makedirs(lock.parent)
    lock.write_text("{}")
    assert pi.apply(wait_seconds=0) == {"ran": False, "locked": True}
    ticks = []

    def sleeper(_s):
        ticks.append(1)
        lock.unlink()
    assert pi.wait_while_running(time.time() + 30, sleep=sleeper) is True and ticks == [1]
    lock.write_text("{}")
    assert pi.wait_while_running(time.time() - 1) is False


def test_a_waiting_apply_keeps_the_real_outcome(data, monkeypatch):
    pi.write_pending(["paddleocr"])
    pi._write_json("result", {"status": "ok", "packages": ["paddleocr"]})
    Fake(monkeypatch, [])

    def first_start_took_it(_wait_until, sleep=None):
        pi._remove("pending")
        return True
    monkeypatch.setattr(pi, "_acquire_lock", first_start_took_it)
    assert pi.apply() == {"ran": False}
    assert pi.read_result()["status"] == "ok"


def test_the_watchdog_stop_is_never_cleared_and_skips_the_restore(data, monkeypatch):
    pi.write_pending(["paddleocr"])
    fake = Fake(monkeypatch, [{"numpy": "1"}, {"numpy": "2"}], rcs=(1,))
    pi._CURRENT["stop"] = True
    try:
        out = pi.apply()
    finally:
        pi._CURRENT["stop"] = False
    assert out["ran"] and len(fake.runs) == 1          # no restore pip
    assert pi.read_result()["status"] == "failed"


def test_a_forged_argv_is_refused_and_pip_never_runs(data, monkeypatch):
    import tempfile
    good = [sys.executable, "-m", "pip", "install", *pi._PIP_FLAGS, "paddleocr"]
    forged = [
        ["/bin/sh", "-c", "id"],
        [sys.executable, "-c", "print(1)"],
        good + ["--index-url", "http://evil.example/simple"],
        good + ["http://evil.example/x.whl"],
        good + ["-c", "/etc/passwd"],
        good + ["-c"],
        good + [7],
        "pip install paddleocr",
    ]
    for argv in forged:
        pi.write_pending(["paddleocr"])
        fake = Fake(monkeypatch, [], derive={"ok": True, "temp_files": [], "argv": argv})
        out = pi.apply()
        assert out["ran"] and fake.runs == [], argv
        assert pi.read_result()["status"] == "refused"
        assert "not accepted" in pi.read_result()["message"]
    pins = os.path.join(tempfile.gettempdir(), "baihe-torch-pins-x.txt")
    pi.write_pending(["paddleocr"])
    fake = Fake(monkeypatch, [{"numpy": "1"}], derive={"ok": True, "temp_files": [],
                                                       "argv": good + ["-c", pins]})
    pi.apply()
    assert len(fake.runs) == 1


def test_pip_flags_match_the_ones_the_registry_emits():
    import diagnostics
    assert pi._PIP_FLAGS == tuple(diagnostics.PIP_INSTALL_FLAGS)


def test_a_silent_pip_still_keeps_the_lock_fresh(data, monkeypatch):
    lock = data / "pending_install" / "apply.lock"
    os.makedirs(lock.parent)
    lock.write_text("{}")
    old = time.time() - pi.OVERALL_SECONDS - 600
    os.utime(lock, (old, old))
    pi._write_json("result", {"status": "running", "packages": ["x"]})
    assert pi.status()["result"]["status"] == "interrupted"
    clock = iter([0.0, 31.0, 32.0])
    monkeypatch.setattr(pi.time, "monotonic", lambda: next(clock))
    def silent_pip(argv, timeout, cancel):
        cancel()
        yield {"returncode": 0, "timed_out": False}
    monkeypatch.setattr(pi, "stream_tree", silent_pip)
    pi.run_capture(["x"], 5)
    assert pi.apply_running() is True
    assert pi.status()["result"]["status"] == "running"


def test_run_capture_does_not_reset_stop(monkeypatch):
    seen = []
    monkeypatch.setattr(pi, "stream_tree", lambda argv, timeout, cancel: iter(
        [seen.append(cancel()) or {"returncode": 0, "timed_out": False}]))
    pi._CURRENT["stop"] = True
    try:
        pi.run_capture(["x"], 5)
    finally:
        pi._CURRENT["stop"] = False
    assert seen == [True]


@pytest.mark.parametrize("edit", [
    lambda p: p.update(created="soon"),
    lambda p: p.update(created=True),
    lambda p: p.update(before=["numpy"]),
    lambda p: p.update(before={"numpy": 2}),
])
def test_a_rehashed_file_with_bad_types_reads_as_invalid(data, edit):
    pi.write_pending(["paddleocr"])
    path = data / "pending_install" / "pending.json"
    doc = json.loads(path.read_text())
    edit(doc["payload"])
    doc["sha256"] = pi.digest(doc["payload"])
    path.write_text(json.dumps(doc))
    assert pi.read_pending() == (None, "invalid")
    assert pi.status()["problem"] == "invalid"


def test_the_stored_tail_is_exactly_the_tail_constant_and_the_capture_keeps_more():
    many = [f"line {i}" for i in range(pi.CAPTURE_LINES * 2)]
    rc, lines, _ = pi.run_capture(
        [sys.executable, "-c", "print('\\n'.join(f'line {i}' for i in range(%d)))" % len(many)], 30)
    assert lines == many[-pi.CAPTURE_LINES:] and pi.CAPTURE_LINES > pi.TAIL_LINES
    assert len(pi._redacted_tail(lines)) == pi.TAIL_LINES


def _good_argv():
    return [sys.executable, "-m", "pip", "install", *pi._PIP_FLAGS, "paddleocr"]


def test_only_the_app_constraints_and_a_torch_pins_file_may_follow_c(data):
    import tempfile
    tmp = tempfile.gettempdir()
    app = os.path.join(ROOT, "constraints.txt")
    pins = os.path.join(tmp, "baihe-torch-pins-abc123.txt")
    assert pi._argv_accepted(_good_argv() + ["-c", app, "-c", pins])
    # pip's constraints parser takes options such as --extra-index-url, and the temp dir is shared.
    for path in (os.path.join(tmp, "x.txt"), os.path.join(tmp, "baihe-torch-pins-x.txt.evil"),
                 os.path.join(tmp, "baihe-torch-pins-.txt"), os.path.join(pi.state_dir(), "constraints.txt"),
                 os.path.join(ROOT, "requirements.txt"), os.path.join(tmp, "constraints.txt")):
        assert not pi._argv_accepted(_good_argv() + ["-c", path]), path


def test_the_refusal_still_removes_the_pins_file_and_nothing_else(data, monkeypatch, tmp_path):
    import tempfile
    fd, pins = tempfile.mkstemp(prefix="baihe-torch-pins-", suffix=".txt")
    os.close(fd)
    other = tmp_path / "keep.txt"
    other.write_text("x")
    try:
        pi.write_pending(["paddleocr"])
        fake = Fake(monkeypatch, [], derive={"ok": True, "temp_files": [pins, str(other), 7],
                                             "argv": _good_argv() + ["-c", "/etc/passwd"]})
        assert pi.apply()["status"] == "refused" and fake.runs == []
        assert not os.path.exists(pins) and other.exists()
    finally:
        if os.path.exists(pins):
            os.remove(pins)


def test_a_run_removes_only_temp_files_shaped_like_the_pins_file(data, monkeypatch, tmp_path):
    import tempfile
    fd, pins = tempfile.mkstemp(prefix="baihe-torch-pins-", suffix=".txt")
    os.close(fd)
    other = tmp_path / "keep.txt"
    other.write_text("x")
    try:
        pi.write_pending(["paddleocr"])
        fake = Fake(monkeypatch, [{}], derive={"ok": True, "temp_files": [pins, str(other)],
                                               "argv": _good_argv() + ["-c", pins]})
        assert pi.apply()["status"] == "ok" and len(fake.runs) == 1
        assert not os.path.exists(pins) and other.exists()
    finally:
        if os.path.exists(pins):
            os.remove(pins)


def test_a_held_lock_reads_as_running_even_after_the_clock_jumps(data, monkeypatch):
    assert pi._acquire_lock(time.time()) is True
    later = time.time() + pi.OVERALL_SECONDS + 3600
    monkeypatch.setattr(pi.time, "time", lambda: later)
    assert pi.apply_running() is True
    assert pi.wait_while_running(later - 1) is False


def test_a_silent_pip_keeps_a_held_lock_fresh_through_its_fd(data, monkeypatch):
    assert pi._acquire_lock(time.time()) is True
    lock = data / "pending_install" / "apply.lock"
    old = time.time() - pi.OVERALL_SECONDS - 600
    lock.write_text(json.dumps({"pid": os.getpid(), "time": int(old)}))
    os.utime(lock, (old, old))
    clock = iter([0.0, 31.0, 32.0])
    monkeypatch.setattr(pi.time, "monotonic", lambda: next(clock))
    utimes = []
    monkeypatch.setattr(pi.os, "utime", lambda *a, **k: utimes.append(a))

    def silent_pip(argv, timeout, cancel):
        cancel()
        yield {"returncode": 0, "timed_out": False}
    monkeypatch.setattr(pi, "stream_tree", silent_pip)
    pi.run_capture(["x"], 5)
    doc = json.loads(lock.read_text())
    assert utimes == [] and doc["pid"] == os.getpid() and doc["time"] > old + 600
    assert time.time() - os.path.getmtime(lock) < 60


def test_the_lock_refresh_survives_the_watchdog_emptying_the_holder(data, monkeypatch):
    os.makedirs(data / "pending_install")
    pi._LOCK.clear()        # the watchdog's release pops "fd" before it resets it
    clock = iter([0.0, 31.0, 32.0])
    monkeypatch.setattr(pi.time, "monotonic", lambda: next(clock))

    def silent_pip(argv, timeout, cancel):
        cancel()
        yield {"returncode": 0, "timed_out": False}
    monkeypatch.setattr(pi, "stream_tree", silent_pip)
    assert pi.run_capture(["x"], 5)[0] == 0


def test_a_failed_lock_write_does_not_keep_the_os_lock(data, monkeypatch):
    def no_space(fd):
        raise OSError("disk full")
    monkeypatch.setattr(pi, "_write_held", no_space)
    with pytest.raises(OSError):
        pi._acquire_lock(time.time())
    assert pi._LOCK["fd"] is None
    fd, busy = pi._try_hold(create=False)
    assert busy is False and fd is not None
    pi._close_held(fd)


# --- the plain-OpenCV swap: one fixed `pip uninstall -y NAME`, then the install ---

def _uninstall(name="opencv-python"):
    return [sys.executable, "-m", "pip", "uninstall", "-y", name]


def test_derive_adds_the_uninstall_step_for_a_plain_opencv(monkeypatch):
    monkeypatch.setattr(pi, "snapshot", lambda: {"opencv-python-headless": "4.10.0.84"})
    out = child.derive("paddleocr,paddlepaddle")
    assert out["uninstall"] == [_uninstall("opencv-python-headless")]
    assert pi._uninstall_steps_accepted(out["uninstall"]) and pi._argv_accepted(out["argv"])


@pytest.mark.parametrize("installed", [{"opencv-python": "4.10", "opencv-contrib-python": "4.10"}, {}])
def test_derive_adds_no_uninstall_step_otherwise(monkeypatch, installed):
    monkeypatch.setattr(pi, "snapshot", lambda: installed)
    assert child.derive("paddleocr")["uninstall"] == []


def test_derive_adds_no_uninstall_step_for_a_key_that_needs_no_contrib(monkeypatch):
    monkeypatch.setattr(pi, "snapshot", lambda: {"opencv-python": "4.10"})
    assert child.derive("paddlepaddle")["uninstall"] == []


def test_the_uninstall_allowlist_matches_the_registry():
    import install_registry
    assert pi._UNINSTALLABLE == install_registry.CV2_SWAP_UNINSTALLABLE


def test_a_queued_plan_with_the_uninstall_step_runs_it_before_the_install(data, monkeypatch):
    pi.write_pending(["paddleocr"], {})
    derive = {"ok": True, "temp_files": [], "uninstall": [_uninstall()],
              "argv": [sys.executable, "-m", "pip", "install", "paddleocr"]}
    fake = Fake(monkeypatch, [{}], derive=derive)
    assert pi.apply()["ran"] is True
    assert [r[3] for r in fake.runs] == ["install", "uninstall", "install"]
    assert "--dry-run" in fake.runs[0] and "--dry-run" not in fake.runs[2]
    assert fake.runs[1][4:] == ["-y", "opencv-python"]
    assert pi.read_result()["status"] == "ok"


def test_a_failed_uninstall_never_reaches_the_install_and_restores(data, monkeypatch):
    pi.write_pending(["paddleocr"], {})
    derive = {"ok": True, "temp_files": [], "uninstall": [_uninstall()],
              "argv": [sys.executable, "-m", "pip", "install", "paddleocr"]}
    fake = Fake(monkeypatch, [{"opencv-python": "4.10"}, {}, {"opencv-python": "4.10"}],
                rcs=(0, 1, 0), derive=derive)
    pi.apply()
    assert [r[3] for r in fake.runs] == ["install", "uninstall", "install"]
    assert fake.runs[2][-1] == "opencv-python==4.10"          # the restore
    assert pi.read_result()["status"] == "failed"


@pytest.mark.parametrize("steps", [
    [_uninstall("numpy")],
    [_uninstall("opencv-contrib-python")],
    [_uninstall("opencv-python") + ["--break-system-packages"]],
    [_uninstall("opencv-python") + ["numpy"]],
    [_uninstall("opencv-python"), _uninstall("opencv-python")],
    [[sys.executable, "-m", "pip", "uninstall", "opencv-python"]],
    [[sys.executable, "-m", "pip", "uninstall", "-y", "-r", "x.txt"]],
    [["/bin/evil", "-m", "pip", "uninstall", "-y", "opencv-python"]],
    [_uninstall("opencv-python"), "pip install x"],
    "pip uninstall -y opencv-python",
])
def test_an_uninstall_step_outside_the_fixed_form_is_refused(data, monkeypatch, steps):
    assert not pi._uninstall_steps_accepted(steps)
    pi.write_pending(["paddleocr"], {})
    derive = {"ok": True, "temp_files": [], "uninstall": steps,
              "argv": [sys.executable, "-m", "pip", "install", "paddleocr"]}
    fake = Fake(monkeypatch, [{}], derive=derive)
    pi.apply()
    assert fake.runs == [] and pi.read_result()["status"] == "refused"


def test_uninstall_argv_refuses_any_other_name():
    import install_registry
    for bad in ("numpy", "opencv-contrib-python", "-r", "opencv-python numpy", ""):
        with pytest.raises(ValueError):
            install_registry.uninstall_argv(bad)


def _swap_derive():
    return {"ok": True, "temp_files": [], "uninstall": [_uninstall()],
            "argv": [sys.executable, "-m", "pip", "install", "paddleocr"]}


@pytest.mark.parametrize("rc", [1, "timeout"])
def test_nothing_is_uninstalled_when_the_install_cannot_be_previewed(data, monkeypatch, rc):
    pi.write_pending(["paddleocr"], {})
    fake = Fake(monkeypatch, [{"opencv-python": "4.10"}], rcs=(rc,), derive=_swap_derive())
    pi.apply()
    assert len(fake.runs) == 1 and "--dry-run" in fake.runs[0]      # the uninstall never ran
    result = pi.read_result()
    assert result["status"] in ("failed", "timed_out") and "nothing was removed" in result["message"]


def test_a_stop_after_contrib_landed_does_not_put_the_plain_opencv_back(data, monkeypatch):
    pi.write_pending(["paddleocr"], {})
    before = {"opencv-python": "4.10.0.84", "numpy": "2.5.3"}
    now = {"opencv-contrib-python": "4.10.0.84", "numpy": "2.5.3"}     # uninstalled, contrib landed
    fake = Fake(monkeypatch, [before, now], rcs=(0, 0, "timeout"), derive=_swap_derive())
    pi.apply()
    assert [r[3] for r in fake.runs] == ["install", "uninstall", "install"]   # no restore run
    result = pi.read_result()
    assert result["status"] == "timed_out" and result["restored"] == []
    assert "opencv-python was not put back" in result["message"]


def test_a_stop_before_contrib_landed_still_puts_the_plain_opencv_back(data, monkeypatch):
    pi.write_pending(["paddleocr"], {})
    before = {"opencv-python": "4.10.0.84"}
    fake = Fake(monkeypatch, [before, {}, before], rcs=(0, 0, "timeout", 0), derive=_swap_derive())
    pi.apply()
    assert fake.runs[-1][-1] == "opencv-python==4.10.0.84"


def test_without_clashing_cv2_keeps_other_packages_and_a_contrib_the_owner_had():
    wanted = {"opencv-python": "4.10", "numpy": "2.5.3"}
    assert pi.without_clashing_cv2(wanted, {"opencv-contrib-python": "4"}, {}) == (
        {"numpy": "2.5.3"}, ["opencv-python"])
    assert pi.without_clashing_cv2(wanted, {"opencv-contrib-python": "4"},
                                   {"opencv-contrib-python": "3"}) == (wanted, [])
    assert pi.without_clashing_cv2(wanted, {}, {}) == (wanted, [])


# --- an apply that was killed mid-way is put right on the next start --------

def _killed_mid_pip(before, packages=("paddleocr",)):
    """The result a start leaves when it dies while pip runs (power loss, a
    closed console): status "running" with the snapshot taken before pip."""
    pi._write_json("result", {"status": "running", "packages": list(packages), "before": before,
                              "tail": [], "message": "Installing the packages you queued...",
                              "restored": [], "restore_failed": []})


def _derive(*dists):
    """The registry's argv for the recorded keys, naming the dists it installs."""
    return {"ok": True, "temp_files": [],
            "argv": [sys.executable, "-m", "pip", "install", *pi._PIP_FLAGS, *dists]}


def _recovering(monkeypatch, versions, dists=("numpy", "pyyaml", "torch"), rcs=(0,), local=()):
    fake = Fake(monkeypatch, versions, rcs=rcs, derive=_derive(*dists))
    monkeypatch.setattr(pi, "non_index_dists", lambda: set(local))
    return fake


def test_next_start_puts_back_what_a_killed_install_changed(data, monkeypatch):
    before = {"numpy": "2.5.3", "pyyaml": "6.0.2", "requests": "2.33.0"}
    _killed_mid_pip(before)
    half_done = {"pyyaml": "7.0", "requests": "2.33.0"}         # numpy removed, pyyaml replaced
    fake = _recovering(monkeypatch, [half_done, before])
    out = pi.apply()
    assert out["ran"] and len(fake.runs) == 1
    restore = fake.runs[0]
    assert restore[:4] == ["python", "-m", "pip", "install"] and "--no-deps" in restore
    assert "numpy==2.5.3" in restore and "pyyaml==6.0.2" in restore
    assert not any(a.startswith("requests==") for a in restore)
    result = pi.status()["result"]
    assert result["status"] == "interrupted"
    assert result["restored"] == ["numpy", "pyyaml"] and result["restore_failed"] == []
    assert "put back" in result["message"] and result["packages"] == ["paddleocr"]
    assert result["tail"] == ["clean:line one", "clean:line two"]      # the restore's own pip output
    assert not (data / "pending_install" / "apply.lock").exists()


def test_a_killed_install_that_changed_nothing_runs_no_pip(data, monkeypatch):
    before = {"numpy": "2.5.3"}
    _killed_mid_pip(before)
    fake = _recovering(monkeypatch, [before])
    pi.apply()
    assert fake.runs == []
    result = pi.read_result()
    assert result["status"] == "interrupted" and result["restored"] == []
    assert "Nothing else was changed" in result["message"]


@pytest.mark.parametrize("status", ["ok", "failed", "timed_out", "refused", "interrupted"])
def test_a_finished_result_is_never_restored_from(data, monkeypatch, status):
    doc = {"status": status, "packages": ["paddleocr"], "before": {"numpy": "1"},
           "message": "kept", "tail": [], "restored": [], "restore_failed": []}
    pi._write_json("result", doc)
    fake = Fake(monkeypatch, [])
    assert pi.apply() == {"ran": False}
    assert fake.runs == [] and pi.read_result() == doc


def test_a_restore_that_fails_names_only_the_packages(data, monkeypatch):
    before = {"numpy": "2.5.3", "torch": "2.11.0+cu128", "../evil": "1", "pyyaml": "C:\\Users\\kae"}
    _killed_mid_pip(before)
    fake = _recovering(monkeypatch, [{}, {}], rcs=(1,))
    pi.apply()
    assert len(fake.runs) == 1 and fake.runs[0][-1] == "numpy==2.5.3"
    result = pi.read_result()
    assert result["status"] == "interrupted" and result["restored"] == []
    # torch's local build and the path-shaped version can't be fetched by name;
    # the name the registry doesn't offer is dropped and never shown.
    assert result["restore_failed"] == ["numpy", "pyyaml", "torch"]
    assert "could not be put back: numpy, pyyaml, torch" in result["message"]
    assert "Packages in Diagnostics" in result["message"]
    assert "evil" not in json.dumps(result) and "Users" not in json.dumps(result)


@pytest.mark.parametrize("name", ["evilpkg", "requests"])
def test_a_tampered_result_names_nothing_the_registry_does_not_derive(data, monkeypatch, name):
    # An unknown dist, or a real one the queued keys never touch, is not
    # installed or pinned down from a data-folder file.
    _killed_mid_pip({name: "1.0"})
    fake = _recovering(monkeypatch, [{}, {}], dists=("paddleocr",))
    pi.apply()
    assert fake.runs == [] and pi.read_result()["restored"] == []


def test_a_restore_owed_with_keys_the_registry_refuses_installs_nothing(data, monkeypatch):
    pi._write_json("result", {"status": "timed_out", "restore_owed": True, "packages": ["nope"],
                              "before": {"numpy": "1"}, "left": {"numpy": None}})
    fake = Fake(monkeypatch, [{}], derive={"ok": False, "message": "No."})
    pi.apply()
    assert fake.runs == []
    assert "nothing was put back" in pi.read_result()["message"]


def test_a_package_installed_from_a_local_file_is_not_fetched_from_the_index(data, monkeypatch):
    _killed_mid_pip({"numpy": "2.5.3", "pyyaml": "6.0.2"})
    fake = _recovering(monkeypatch, [{"numpy": "9", "pyyaml": "7"}, {"numpy": "9", "pyyaml": "6.0.2"}],
                       local=("numpy",))
    pi.apply()
    assert fake.runs[0][-1] == "pyyaml==6.0.2" and "numpy==2.5.3" not in fake.runs[0]
    assert "numpy came from outside the package index" in pi.read_result()["message"]


def test_two_starts_do_not_restore_twice(data, monkeypatch):
    before = {"numpy": "2.5.3"}
    _killed_mid_pip(before)
    fake = _recovering(monkeypatch, [{}, before])
    pi.apply()
    assert len(fake.runs) == 1
    first = pi.read_result()
    pi.apply()
    assert len(fake.runs) == 1 and pi.read_result() == first


def test_a_restore_that_is_itself_killed_is_reported_not_retried(data, monkeypatch):
    # A restore that keeps dying (pip crashes the PC, the console is closed
    # each time) must not run again on every start.
    _killed_mid_pip({"numpy": "2.5.3"})
    _recovering(monkeypatch, [{}])
    monkeypatch.setattr(pi, "run_capture", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError):
        pi.apply()
    result = pi.status()["result"]
    assert result["status"] == "interrupted" and "Packages in Diagnostics" in result["message"]
    fake = _recovering(monkeypatch, [{}])
    assert pi.apply() == {"ran": False} and fake.runs == []
    assert pi.read_result() == result


def test_the_watchdog_leaves_a_restore_for_the_next_start(data, monkeypatch):
    _killed_mid_pip({"numpy": "2.5.3", "pyyaml": "6.0.2"})
    fake = _recovering(monkeypatch, [{"numpy": "2.6.0"},                      # the watchdog's look
                                     {"numpy": "2.6.0"}, {"numpy": "2.5.3"}])
    pi._write_json("result", pi._watchdog_result())
    assert pi.read_result()["left"] == {"numpy": "2.6.0", "pyyaml": None}
    status = pi.status()["result"]
    assert "put your earlier packages back when it next starts" in status["message"]
    pi.apply()
    assert fake.runs[0][-2:] == ["numpy==2.5.3", "pyyaml==6.0.2"]
    assert pi.read_result()["restored"] == ["numpy"]


def test_a_watchdog_restore_leaves_what_the_owner_changed_since(data, monkeypatch):
    # The server ran after the watchdog's exit; numpy was reinstalled from
    # Diagnostics meanwhile and must not be downgraded on the next start.
    _killed_mid_pip({"numpy": "2.5.3", "pyyaml": "6.0.2"})
    fake = _recovering(monkeypatch, [{"numpy": "2.6.0", "pyyaml": "7.0"},
                                     {"numpy": "2.7.0", "pyyaml": "7.0"},
                                     {"numpy": "2.7.0", "pyyaml": "6.0.2"}])
    pi._write_json("result", pi._watchdog_result())
    pi.apply()
    assert fake.runs[0][-1] == "pyyaml==6.0.2" and "numpy==2.5.3" not in fake.runs[0]


def test_a_watchdog_record_without_versions_reverts_nothing(data, monkeypatch):
    pi._write_json("result", {"status": "timed_out", "restore_owed": True, "packages": ["paddleocr"],
                              "before": {"numpy": "2.5.3"}})
    fake = _recovering(monkeypatch, [{}])
    pi.apply()
    assert fake.runs == []


def test_queueing_an_install_keeps_an_owed_restore(data, monkeypatch):
    _killed_mid_pip({"numpy": "2.5.3"})
    pi.write_pending(["paddleocr"])
    assert pi.restore_owed()
    pi.clear_result()           # Dismiss is the owner's explicit "keep them as they are"
    assert not pi.restore_owed()


def test_a_watchdog_exit_during_the_restore_is_not_restored_again(data, monkeypatch):
    _killed_mid_pip({"numpy": "2.5.3"})
    _recovering(monkeypatch, [{}])

    def watchdog_fires(argv, timeout, echo=False):
        pi._write_json("result", pi._watchdog_result())
        raise SystemExit(0)             # the watchdog's os._exit, as far as this start goes
    monkeypatch.setattr(pi, "run_capture", watchdog_fires)
    with pytest.raises(SystemExit):
        pi.apply()
    fake = _recovering(monkeypatch, [{}])
    assert pi.apply() == {"ran": False} and fake.runs == []


def test_a_live_apply_is_not_mistaken_for_a_killed_one(data, monkeypatch):
    _killed_mid_pip({"numpy": "2.5.3"})
    lock = data / "pending_install" / "apply.lock"
    lock.write_text(json.dumps({"pid": 4242, "time": int(time.time())}))    # another start, alive
    monkeypatch.setattr(pi, "_pid_alive", lambda pid, started=None: True)
    fake = Fake(monkeypatch, [])
    assert pi.apply(wait_seconds=0) == {"ran": False, "locked": True}
    assert fake.runs == [] and pi.read_result()["status"] == "running"


def test_a_queued_install_still_runs_after_the_restore(data, monkeypatch):
    _killed_mid_pip({"numpy": "2.5.3"})
    pi.write_pending(["paddleocr"])
    fake = _recovering(monkeypatch, [{}, {"numpy": "2.5.3"}, {"numpy": "2.5.3"}],
                       dists=("numpy", "paddleocr"))
    out = pi.apply()
    assert [r[-1] for r in fake.runs] == ["numpy==2.5.3", "paddleocr"]
    assert out["status"] == "ok" and pi.read_pending() == (None, None)


def test_launcher_runs_the_apply_step_when_a_restore_is_owed(data, monkeypatch, capsys):
    sys.path.insert(0, os.path.join(ROOT, "installer"))
    import launcher
    calls = []
    monkeypatch.setattr(launcher.subprocess, "run", lambda *a, **k: calls.append(a))
    pi._write_json("result", {"status": "ok", "packages": ["x"], "before": {"numpy": "1"}})
    launcher.apply_pending_install("python.exe", {}, headless=True)
    assert calls == []
    _killed_mid_pip({"numpy": "1"})
    launcher.apply_pending_install("python.exe", {}, headless=True)
    assert calls == [(["python.exe", "-s", "-m", "pending_install"],)]
    assert "Putting back packages" in capsys.readouterr().out


# ---- the core wheels Setup keeps beside the bundled Python ----

NUMPY_WHEEL = "numpy-2.5.3-cp312-cp312-win_amd64.whl"


def _kept_folder(tmp_path, files, pins=None):
    """A kept-wheels folder: `files` {wheel name: bytes} and a lock pinning
    `pins` {name: (version, bytes hashed)}, by default each file's own hash."""
    import hashlib
    folder = tmp_path / "wheels"
    folder.mkdir()
    for name, data in files.items():
        (folder / name).write_bytes(data)
    if pins is None:
        pins = {name.split("-")[0]: (name.split("-")[1], data) for name, data in files.items()}
    (folder / "wheels.lock.txt").write_text("".join(
        f"{n}=={v} \\\n    --hash=sha256:{hashlib.sha256(d).hexdigest()}\n" for n, (v, d) in pins.items()))
    return str(folder)


def test_a_kept_wheel_is_put_back_with_no_index_and_the_rest_from_it(tmp_path, monkeypatch):
    monkeypatch.setattr(pi, "_python", lambda: ["python"])
    folder = _kept_folder(tmp_path, {NUMPY_WHEEL: b"numpy bytes"})
    argvs = pi.restore_argvs({"numpy": "2.5.3", "pyyaml": "6.0.2"}, folder)
    base = ["python", "-m", "pip", "install", "--no-deps", "--no-cache-dir", "--disable-pip-version-check"]
    # By name==version from the folder, so pip records no direct_url.json and a
    # later restore still treats numpy as an index package.
    assert argvs == [base + ["--no-index", "--find-links", folder, "numpy==2.5.3"],
                     base + ["pyyaml==6.0.2"]]


@pytest.mark.parametrize("files, pins", [
    ({NUMPY_WHEEL: b"edited"}, {"numpy": ("2.5.3", b"numpy bytes")}),       # fails its hash
    ({NUMPY_WHEEL: b"numpy bytes"}, {"pillow": ("12.3.0", b"x")}),          # not in the lock
    ({"numpy-2.4.0-cp312-cp312-win_amd64.whl": b"old"}, None),              # another version
    ({NUMPY_WHEEL: b"numpy bytes", "numpy-2.5.3-py3-none-any.whl": b"bad"},
     {"numpy": ("2.5.3", b"numpy bytes")}),                                 # one of two fails
])
def test_a_wheel_that_does_not_check_out_comes_from_the_index(tmp_path, monkeypatch, files, pins):
    monkeypatch.setattr(pi, "_python", lambda: ["python"])
    folder = _kept_folder(tmp_path, files, pins)
    assert pi.kept_wheels({"numpy": "2.5.3"}, folder) == set()
    assert [a[-1] for a in pi.restore_argvs({"numpy": "2.5.3"}, folder)] == ["numpy==2.5.3"]


@pytest.mark.parametrize("lock", [None, "numpy>=2\n"])
def test_no_folder_or_a_bad_lock_means_the_index_only(tmp_path, lock):
    folder = tmp_path / "wheels"
    if lock is not None:
        folder.mkdir()
        (folder / NUMPY_WHEEL).write_bytes(b"numpy bytes")
        (folder / "wheels.lock.txt").write_text(lock)
    assert pi.kept_wheels({"numpy": "2.5.3"}, str(folder)) == set()


def test_the_kept_folder_is_beside_the_bundled_python(monkeypatch):
    monkeypatch.setattr(pi.sys, "executable", os.path.join("C:", "Baihe Studio", "python", "python.exe"))
    assert pi.kept_wheels_dir() == os.path.abspath(os.path.join("C:", "Baihe Studio", "wheels"))


def test_an_offline_failure_puts_numpy_back_from_the_kept_wheel(data, tmp_path, monkeypatch):
    pi.write_pending(["paddleocr"])
    folder = _kept_folder(tmp_path, {NUMPY_WHEEL: b"numpy bytes"})
    monkeypatch.setattr(pi, "kept_wheels_dir", lambda: folder)
    before = {"numpy": "2.5.3", "pyyaml": "6.0.2"}
    fake = Fake(monkeypatch, [before, {"numpy": "2.3.5"}, {"numpy": "2.5.3"}], rcs=(1, 0, 1))
    out = pi.apply()
    assert fake.runs[1][-4:] == ["--no-index", "--find-links", folder, "numpy==2.5.3"]
    assert fake.runs[2][-1] == "pyyaml==6.0.2" and "--no-index" not in fake.runs[2]
    assert out["restored"] == ["numpy"] and out["restore_failed"] == ["pyyaml"]
