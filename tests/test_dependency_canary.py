"""tests/test_dependency_canary.py -- scripts/dependency_canary.py with the
venv/pip/pytest subprocesses mocked (no network, no real installs)."""

import importlib.util
import os
import subprocess
from types import SimpleNamespace

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_spec = importlib.util.spec_from_file_location(
    "dependency_canary", os.path.join(ROOT, "scripts", "dependency_canary.py"))
dc = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dc)


@pytest.fixture(autouse=True)
def _no_uv_by_default(monkeypatch):
    # Whether uv happens to be installed must not change what the pip-path
    # tests exercise; the uv tests below opt in explicitly.
    monkeypatch.setattr(dc, "uv_path", lambda: None)


def test_pin_is_strictly_below_failing_version():
    assert dc.compute_pin("pandas", "3.0.0") == "pandas<3.0.0"
    assert dc.is_newer("3.0.0", "2.2.3")
    assert not dc.is_newer("2.2.3", "2.2.3")
    assert not dc.is_newer("2.0", "2.0.0")
    assert not dc.is_newer("weird", "1.0")


def test_write_pin_appends_keeps_comments_and_is_idempotent(tmp_path):
    p = tmp_path / "constraints.txt"
    original = "# header comment\ntorch<3     # gpu set\n"
    p.write_text(original, encoding="utf-8")
    assert dc.write_pin(p, "pandas", "3.0.0", "tests/test_x.py::test_a", today="2026-09-30") == "written"
    text = p.read_text(encoding="utf-8")
    assert text.startswith(original)
    assert "pandas<3.0.0" in text and "# canary 2026-09-30: 3.0.0 broke tests/test_x.py::test_a" in text
    assert dc.write_pin(p, "Pandas", "3.0.0", "x", today="2026-10-01") == "exists"
    assert p.read_text(encoding="utf-8") == text


def test_write_pin_leaves_existing_cap_alone(tmp_path):
    p = tmp_path / "constraints.txt"
    p.write_text("transformers<6  # backs Scanlate\n", encoding="utf-8")
    assert dc.write_pin(p, "transformers", "5.9", "t") == "bounded"
    assert p.read_text(encoding="utf-8") == "transformers<6  # backs Scanlate\n"


def test_write_pin_adds_missing_trailing_newline(tmp_path):
    p = tmp_path / "constraints.txt"
    p.write_text("torch<3", encoding="utf-8")
    dc.write_pin(p, "pandas", "3.0", "")
    assert p.read_text(encoding="utf-8").splitlines()[0] == "torch<3"


def test_verdict_parsing():
    out = "....F\nFAILED tests/test_a.py::test_x - assert 1\nERROR tests/test_b.py\n1 failed"
    assert dc.parse_verdict(1, out) == (dc.FAIL, ["tests/test_a.py::test_x", "tests/test_b.py"])
    assert dc.parse_verdict(0, "5 passed") == (dc.PASS, [])
    assert dc.parse_verdict(5, "no tests ran")[0] == dc.ERROR
    assert dc.parse_verdict(124, "[timed out]")[0] == dc.ERROR
    assert dc.parse_verdict(1, "crashed, no summary")[0] == dc.ERROR


def test_quick_subset_selection(tmp_path):
    t = tmp_path / "tests"
    t.mkdir()
    (t / "test_static_analysis.py").write_text("")
    (t / "test_requests_bits.py").write_text("")
    (t / "test_uses.py").write_text("import requests\n")
    (t / "test_from.py").write_text("from requests.adapters import X\n")
    (t / "test_other.py").write_text("import requests_mock_thing\nimport os\n")
    (t / "test_none.py").write_text("import os\n")
    assert dc.quick_test_files(t, "requests") == [
        "tests/test_from.py", "tests/test_requests_bits.py", "tests/test_static_analysis.py",
        "tests/test_uses.py"]


@pytest.mark.parametrize("bad", ["", "a b", "x;rm", "../x", "a/b", "pkg==1"])
def test_invalid_name_refused(bad, capsys, monkeypatch):
    monkeypatch.setattr(dc, "run_canary", lambda *a, **k: pytest.fail("must not run"))
    assert dc.main([bad]) == 2
    assert "letters, digits" in capsys.readouterr().err


def test_bad_target_refused(monkeypatch):
    monkeypatch.setattr(dc, "run_canary", lambda *a, **k: pytest.fail("must not run"))
    assert dc.main(["requests", "1.0; rm -rf"]) == 2


def test_redact():
    s = "key sk-abcdefghijklmnopqrstuv and Authorization: Bearer abcdef1234567890xyz and hf_abcdefghijklmnopqrst"
    out = dc.redact(s)
    assert "abcdefghijklmnop" not in out and "abcdef1234567890" not in out


def test_clean_env_drops_keys(monkeypatch, tmp_path):
    for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN", "HTTPS_PROXY"):
        monkeypatch.setenv(k, "secret-value-123456")
    env = dc.clean_env(str(tmp_path))
    assert not any(k in env for k in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN", "HTTPS_PROXY"))
    assert env["BAIHE_DATA_DIR"] == str(tmp_path)


def _fake_runner(calls, versions, pytest_result):
    """Stands in for subprocess.run and records (cmd, env, timeout).
    pytest_result(version) -> (returncode, output)."""
    state = {"ver": versions[0]}

    def fake(cmd, cwd=None, env=None, timeout=None, **kw):
        calls.append((list(cmd), env, timeout))
        out, rc = "", 0
        joined = " ".join(map(str, cmd))
        if "importlib.metadata" in joined:
            out = state["ver"]
        elif "--upgrade" in cmd:
            state["ver"] = next((str(t).split("==")[1] for t in cmd if "==" in str(t)), versions[1])
        elif "pip install" in joined and "==" in joined:
            state["ver"] = next(str(t).split("==")[1] for t in cmd if "==" in str(t))
        elif " -m pytest" in joined:
            rc, out = pytest_result(state["ver"])
        return SimpleNamespace(returncode=rc, stdout=out, stderr="")
    return fake


def _repo(tmp_path):
    root = tmp_path / "repo"
    (root / "tests").mkdir(parents=True)
    return root


def test_run_canary_fail_when_only_new_version_breaks(monkeypatch, tmp_path):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-should-not-leak-1234567890")
    calls = []
    fake = _fake_runner(calls, ["2.2.3", "3.0.0"], lambda v: (
        (1, "FAILED tests/test_a.py::test_x - boom\n") if v == "3.0.0" else (0, "1 passed")))
    monkeypatch.setattr(dc.subprocess, "run", fake)
    res = dc.run_canary("pandas", "latest", False, False, root=_repo(tmp_path), log=lambda *_: None)
    assert (res["verdict"], res["known_good"], res["version"]) == (dc.FAIL, "2.2.3", "3.0.0")
    assert res["failures"] == ["tests/test_a.py::test_x"]
    assert calls and all(t for _, _, t in calls)             # a timeout on every call
    assert all("OPENAI_API_KEY" not in (e or {}) for _, e, _ in calls)
    assert not any("sk-should-not-leak" in str(v) for _, e, _ in calls for v in (e or {}).values())
    assert any("requirements-core.txt" in " ".join(c) and "-c" in c for c, _, _ in calls)
    assert not any("requirements-optional.txt" in " ".join(c) for c, _, _ in calls)


def test_run_canary_preexisting_failure_is_not_blamed_on_package(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(dc.subprocess, "run", _fake_runner(
        calls, ["2.2.3", "3.0.0"], lambda v: (1, "FAILED tests/test_a.py::test_x\n")))
    res = dc.run_canary("pandas", "latest", False, False, root=_repo(tmp_path), log=lambda *_: None)
    assert res["verdict"] == dc.PREEXISTING


def test_run_canary_pass_with_optional(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(dc.subprocess, "run",
                        _fake_runner(calls, ["1.0", "1.1"], lambda v: (0, "5 passed")))
    res = dc.run_canary("requests", "1.1", True, True, root=_repo(tmp_path), log=lambda *_: None)
    assert res["verdict"] == dc.PASS
    assert any("requirements-optional.txt" in " ".join(c) for c, _, _ in calls)


def test_timeout_is_an_error_not_a_pass(monkeypatch):
    def boom(*a, **k):
        raise subprocess.TimeoutExpired(a[0], 1, output="partial")
    monkeypatch.setattr(dc.subprocess, "run", boom)
    rc, out = dc._run(["x"], 1, {})
    assert rc == 124 and "timed out" in out


def test_report_prints_manual_pin_back_and_writes_pin(tmp_path):
    p = tmp_path / "constraints.txt"
    p.write_text("# c\n", encoding="utf-8")
    lines = []
    res = dict(verdict=dc.FAIL, known_good="2.2.3", version="3.0.0",
               failures=["tests/test_a.py::t"], tail="boom", reason="")
    dc.report(res, "pandas", True, p, out=lines.append)
    text = "\n".join(lines)
    assert 'pip install "pandas==2.2.3"' in text and "wheels.lock.txt" in text
    assert "pandas<3.0.0" in p.read_text(encoding="utf-8")


def test_report_does_not_pin_when_not_newer(tmp_path):
    p = tmp_path / "constraints.txt"
    p.write_text("", encoding="utf-8")
    res = dict(verdict=dc.FAIL, known_good="3.0.0", version="2.0.0", failures=[], tail="", reason="")
    dc.report(res, "pandas", True, p, out=lambda *_: None)
    assert p.read_text(encoding="utf-8") == ""


def test_without_uv_uses_venv_and_pip(monkeypatch, tmp_path):
    calls = []
    monkeypatch.setattr(dc.subprocess, "run",
                        _fake_runner(calls, ["1.0", "1.1"], lambda v: (0, "5 passed")))
    dc.run_canary("requests", "1.1", False, False, root=_repo(tmp_path), log=lambda *_: None)
    cmds = [c for c, _, _ in calls]
    assert cmds[0][1:3] == ["-m", "venv"]
    assert any(c[1:4] == ["-m", "pip", "install"] for c in cmds)
    assert not any("uv" in c[0] for c in cmds)


def test_with_uv_builds_venv_and_installs_through_uv(monkeypatch, tmp_path):
    monkeypatch.setattr(dc, "uv_path", lambda: "/bin/uv")
    monkeypatch.delenv("UV_CACHE_DIR", raising=False)
    calls = []
    inner = _fake_runner(calls, ["1.0", "1.1"], lambda v: (0, "5 passed"))

    def fake(cmd, **kw):
        if cmd[:3] == ["/bin/uv", "cache", "dir"]:
            calls.append((list(cmd), kw.get("env"), kw.get("timeout")))
            return SimpleNamespace(returncode=0, stdout="/real/uv-cache\n", stderr="")
        return inner(cmd, **kw)
    monkeypatch.setattr(dc.subprocess, "run", fake)
    res = dc.run_canary("requests", "1.1", False, False, root=_repo(tmp_path), log=lambda *_: None)
    assert res["verdict"] == dc.PASS
    cmds = [c for c, _, _ in calls]
    assert ["/bin/uv", "venv"] == cmds[1][:2]
    installs = [c for c in cmds if c[:3] == ["/bin/uv", "pip", "install"]]
    assert installs and all("--python" in c for c in installs)
    assert any("--upgrade" in c for c in installs)
    # The cache is the real one, not the throwaway HOME.
    venv_env = next(e for c, e, _ in calls if c[:2] == ["/bin/uv", "venv"])
    assert venv_env["UV_CACHE_DIR"] == "/real/uv-cache"


def test_package_map_adds_tests_the_heuristic_misses(tmp_path):
    t = tmp_path / "tests"
    t.mkdir()
    for n in ("test_static_analysis.py", "test_sources_a.py", "test_sources_b.py", "test_other.py"):
        (t / n).write_text("import os\n")
    assert dc.quick_test_files(t, "beautifulsoup4") == [
        "tests/test_sources_a.py", "tests/test_sources_b.py", "tests/test_static_analysis.py"]
    assert dc.quick_test_files(t, "Beautifulsoup4") == dc.quick_test_files(t, "beautifulsoup4")


def test_every_mapped_glob_matches_a_real_test_file():
    from pathlib import Path
    tests = Path(ROOT, "tests")
    for pkg, globs in dc.PACKAGE_TESTS.items():
        for g in globs:
            assert list(tests.glob(g)), f"{pkg}: {g} matches nothing"


def test_then_full_runs_full_suite_only_after_quick_pass(monkeypatch, tmp_path):
    calls, repo = [], _repo(tmp_path)
    monkeypatch.setattr(dc.subprocess, "run",
                        _fake_runner(calls, ["1.0", "1.1"], lambda v: (0, "5 passed")))
    dc.run_canary("requests", "1.1", True, False, root=repo, log=lambda *_: None,
                  then_full=True)
    runs = [c for c, _, _ in calls if c[1:3] == ["-m", "pytest"]]
    assert len(runs) == 2 and "-n" not in runs[0] and "-n" in runs[1]

    calls.clear()
    monkeypatch.setattr(dc.subprocess, "run", _fake_runner(
        calls, ["1.0", "1.1"], lambda v: (1, "FAILED tests/test_a.py::t\n") if v == "1.1" else (0, "")))
    res = dc.run_canary("requests", "1.1", True, False, root=repo, log=lambda *_: None,
                        then_full=True)
    assert res["verdict"] == dc.FAIL
    assert not any("-n" in c for c, _, _ in calls if c[1:3] == ["-m", "pytest"])
