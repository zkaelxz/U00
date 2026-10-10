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


@pytest.fixture
def data(tmp_path, monkeypatch):
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
    return tmp_path


class Fake:
    """Stands in for the children and pip; records every pip run."""

    def __init__(self, monkeypatch, versions, rcs=(0,), derive=None):
        self.runs, self.versions, self.rcs = [], list(versions), list(rcs)
        self.derive = derive or {"ok": True, "temp_files": [],
                                 "argv": ["python", "-m", "pip", "install", "paddleocr"]}
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
    assert fake.runs == [["python", "-m", "pip", "install", "paddleocr"]]
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
