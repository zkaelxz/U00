"""
tests/test_upgrade_check.py -- Step 66: "will this upgrade break the app?"
answered by actually trying it in a throwaway environment.

These run the real mechanism end to end -- a real throwaway venv, a real
`pip --python` install, a real nested pytest run -- but fully offline:
the "candidate release" is a tiny wheel built right here and installed
with --no-index, the "currently installed" version is a plain directory
on the throwaway venv's parent path, and the "app's test suite" is a
two-test file written into tmp_path. No network, no real package.
"""
import base64
import hashlib
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diagnostics

PKG = "baihe-probe"
MOD = "baihe_probe"


def _make_wheel(dist_dir, version, value):
    """A minimal, valid pure-Python wheel for baihe-probe==version whose
    module exposes VALUE = value."""
    distinfo = f"{MOD}-{version}.dist-info"
    files = {
        f"{MOD}.py": f"VALUE = {value}\n",
        f"{distinfo}/METADATA": f"Metadata-Version: 2.1\nName: {PKG}\nVersion: {version}\n",
        f"{distinfo}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    path = os.path.join(dist_dir, f"{MOD}-{version}-py3-none-any.whl")
    record = []
    with zipfile.ZipFile(path, "w") as z:
        for name, text in files.items():
            data = text.encode()
            z.writestr(name, data)
            digest = base64.urlsafe_b64encode(hashlib.sha256(data).digest()).rstrip(b"=").decode()
            record.append(f"{name},sha256={digest},{len(data)}")
        record.append(f"{distinfo}/RECORD,,")
        z.writestr(f"{distinfo}/RECORD", "\n".join(record) + "\n")
    return path


def _setup(tmp_path, suite_src, candidates):
    """current install = baihe-probe 1.0 (VALUE = 1) in a plain parent dir;
    `candidates` = {version: VALUE} built as local wheels."""
    parent = tmp_path / "current_env"
    parent.mkdir()
    (parent / f"{MOD}.py").write_text("VALUE = 1\n")
    info = parent / f"{MOD}-1.0.dist-info"
    info.mkdir()
    (info / "METADATA").write_text(f"Metadata-Version: 2.1\nName: {PKG}\nVersion: 1.0\n")
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for version, value in candidates.items():
        _make_wheel(str(wheels), version, value)
    suite = tmp_path / "suite"
    suite.mkdir()
    (suite / "test_probe.py").write_text(suite_src)
    return parent, wheels, suite


SUITE = (f"import {MOD}\n"
         f"def test_value_is_one():\n    assert {MOD}.VALUE == 1\n"
         f"def test_unrelated():\n    assert True\n")


def _run(tmp_path, parent, wheels, suite, version):
    items = list(diagnostics.check_upgrade_candidate(
        PKG, version, project_root=str(tmp_path), test_args=[str(suite)],
        parent_dirs=[str(parent)] + diagnostics._env_package_dirs(),
        pip_extra_args=["--no-index", "--find-links", str(wheels)],
        pip_timeout=120, test_timeout=120))
    return items[-1], [i["line"] for i in items if "line" in i]


class TestCheckUpgradeCandidate:
    def test_catches_a_deliberately_broken_candidate_with_real_detail(self, tmp_path):
        parent, wheels, suite = _setup(tmp_path, SUITE, {"2.0": 2})
        result, lines = _run(tmp_path, parent, wheels, suite, "2.0")
        assert result["verdict"] == "broken" and result["ok"] is False
        assert result["version"] == "2.0"
        assert any(f.endswith("test_value_is_one") for f in result["new_failures"])
        assert not any("test_unrelated" in f for f in result["new_failures"])
        assert "1 test(s)" in result["reason"]
        # the real pytest output is streamed, not summarized away
        assert any("assert 2 == 1" in line for line in lines)

    def test_a_clean_candidate_is_reported_safe(self, tmp_path):
        parent, wheels, suite = _setup(tmp_path, SUITE, {"1.1": 1})
        result, _ = _run(tmp_path, parent, wheels, suite, "1.1")
        assert result["verdict"] == "safe" and result["ok"] is True
        assert result["version"] == "1.1"
        assert result["new_failures"] == [] and result["preexisting_failures"] == []

    def test_a_test_that_already_fails_today_is_not_blamed_on_the_upgrade(self, tmp_path):
        suite_src = SUITE + "def test_already_broken():\n    assert False\n"
        parent, wheels, suite = _setup(tmp_path, suite_src, {"1.1": 1})
        result, _ = _run(tmp_path, parent, wheels, suite, "1.1")
        assert result["verdict"] == "safe"
        assert result["new_failures"] == []
        assert any(f.endswith("test_already_broken") for f in result["preexisting_failures"])

    def test_a_version_that_cant_be_installed_is_incomplete_not_safe(self, tmp_path):
        parent, wheels, suite = _setup(tmp_path, SUITE, {"1.1": 1})
        result, _ = _run(tmp_path, parent, wheels, suite, "9.9")
        assert result["verdict"] == "incomplete" and result["ok"] is False
        assert "couldn't be installed" in result["reason"]

    def test_the_real_environment_is_never_modified(self, tmp_path):
        parent, wheels, suite = _setup(tmp_path, SUITE, {"2.0": 2})
        _run(tmp_path, parent, wheels, suite, "2.0")
        assert (parent / f"{MOD}.py").read_text() == "VALUE = 1\n"
        assert (parent / f"{MOD}-1.0.dist-info").is_dir()
        assert not (parent / f"{MOD}-2.0.dist-info").exists()

    def test_the_throwaway_environment_is_cleaned_up(self, tmp_path, monkeypatch):
        parent, wheels, suite = _setup(tmp_path, SUITE, {"1.1": 1})
        work = tmp_path / "work"
        work.mkdir()
        monkeypatch.setattr(diagnostics.tempfile, "mkdtemp", lambda prefix="": str(work))
        _run(tmp_path, parent, wheels, suite, "1.1")
        assert not work.exists()


class TestEnsurePytest:
    def _run(self, monkeypatch, importable, pip_returncode=0):
        calls = []

        class Proc:
            returncode = 0 if importable else 1

        monkeypatch.setattr(diagnostics.subprocess, "run", lambda *a, **k: Proc())

        def fake_stream(cmd, timeout, **kw):
            calls.append(cmd)
            yield {"line": "Successfully installed pytest"}
            yield {"returncode": pip_returncode, "timed_out": False}

        monkeypatch.setattr(diagnostics, "_stream_process", fake_stream)
        items = list(diagnostics._ensure_pytest("/venv/python", "/real/python", 60))
        return items, calls

    def test_nothing_is_installed_when_pytest_is_already_importable(self, monkeypatch):
        items, calls = self._run(monkeypatch, importable=True)
        assert items == [{"ok": True}] and calls == []

    def test_a_missing_pytest_is_added_to_the_throwaway_environment_only(self, monkeypatch):
        items, calls = self._run(monkeypatch, importable=False)
        assert items[-1] == {"ok": True}
        assert len(calls) == 1
        assert calls[0][:5] == ["/real/python", "-m", "pip", "--python", "/venv/python"]
        assert "pytest>=7.4" in calls[0]

    def test_a_failed_pytest_install_is_reported_not_ok(self, monkeypatch):
        items, _ = self._run(monkeypatch, importable=False, pip_returncode=1)
        assert items[-1] == {"ok": False}


class TestPipConflicts:
    """The real huggingface_hub 2.0.0 manual check passed every (mocked)
    test, yet `import transformers` then fails outright -- only pip's own
    conflict report saw it. A passing run with such a conflict must not
    read "safe"."""

    def test_a_passing_candidate_that_an_installed_package_rejects_is_a_conflict(self, tmp_path):
        suite_src = f"import {MOD}\ndef test_imports():\n    assert {MOD}.VALUE\n"
        parent, wheels, suite = _setup(tmp_path, suite_src, {"2.0": 2})
        dependent = parent / "baihe_dependent-1.0.dist-info"
        dependent.mkdir()
        (dependent / "METADATA").write_text(
            f"Metadata-Version: 2.1\nName: baihe-dependent\nVersion: 1.0\n"
            f"Requires-Dist: {PKG}<2.0\n")
        result, lines = _run(tmp_path, parent, wheels, suite, "2.0")
        assert result["verdict"] == "conflict" and result["ok"] is False
        assert result["new_failures"] == []
        assert len(result["conflicts"]) == 1
        assert result["conflicts"][0].startswith("baihe-dependent 1.0 requires")
        assert "don't support it" in result["reason"]

    def test_parser_keeps_only_conflicts_this_install_caused(self):
        lines = [
            "Successfully installed huggingface-hub-2.0.0",
            "ERROR: pip's dependency resolver does not currently take into account all the "
            "packages that are installed. This behaviour is the source of the following "
            "dependency conflicts.",
            "paddlex 3.7.2 requires numpy<2.4, but you have numpy 2.4.1 which is incompatible.",
            "transformers 5.17.0 requires huggingface-hub<2.0,>=1.5.0, but you have "
            "huggingface-hub 2.0.0 which is incompatible.",
            "tokenizers 0.23.2 requires huggingface-hub<2.0,>=0.16.4, but you have "
            "huggingface-hub 2.0.0 which is incompatible.",
        ]
        conflicts = diagnostics._parse_pip_conflicts(lines)
        assert [c.split()[0] for c in conflicts] == ["transformers", "tokenizers"]

    def test_underscore_and_dash_names_match(self):
        lines = ["Successfully installed Foo_Bar-1.0",
                 "baz 1.0 requires foo-bar<1, but you have foo-bar 1.0 which is incompatible."]
        assert len(diagnostics._parse_pip_conflicts(lines)) == 1

    def test_no_install_line_means_no_attributable_conflicts(self):
        lines = ["baz 1.0 requires foo<1, but you have foo 1.0 which is incompatible."]
        assert diagnostics._parse_pip_conflicts(lines) == []


class TestKnownHuggingFaceHubLimitation:
    """Step 66 item 1: the real, reproduced huggingface_hub 2.x break."""

    def _requires(self, monkeypatch, requirements):
        def fake(dist):
            if requirements is None:
                raise diagnostics.importlib.metadata.PackageNotFoundError(dist)
            assert dist == "transformers"
            return requirements
        monkeypatch.setattr(diagnostics.importlib.metadata, "requires", fake)

    def test_blocks_2x_while_transformers_caps_it(self, monkeypatch, tmp_path):
        self._requires(monkeypatch, ["filelock", "huggingface-hub<2.0,>=1.5.0"])
        reason = diagnostics.upgrade_blocked_reason(
            "huggingface_hub", "2.0.0", project_root=str(tmp_path))
        assert reason and "transformers" in reason and "2.0.0" in reason

    def test_a_1x_release_is_not_blocked(self, monkeypatch, tmp_path):
        self._requires(monkeypatch, ["huggingface-hub<2.0,>=1.5.0"])
        assert diagnostics.upgrade_blocked_reason(
            "huggingface_hub", "1.40.0", project_root=str(tmp_path)) is None

    def test_lifts_itself_once_transformers_accepts_2x(self, monkeypatch, tmp_path):
        self._requires(monkeypatch, ["huggingface_hub>=1.5.0,<3"])
        assert diagnostics.upgrade_blocked_reason(
            "huggingface_hub", "2.0.0", project_root=str(tmp_path)) is None

    def test_not_blocked_without_transformers_installed(self, monkeypatch, tmp_path):
        self._requires(monkeypatch, None)
        assert diagnostics.upgrade_blocked_reason(
            "huggingface_hub", "2.0.0", project_root=str(tmp_path)) is None

    def test_does_not_affect_the_install_time_reason(self):
        assert diagnostics.known_install_limitation_reason("huggingface_hub") is None


class TestParsePytestFailures:
    def test_reads_failed_and_error_lines_from_the_short_summary(self):
        lines = ["..F.E", "=== short test summary info ===",
                 "FAILED tests/test_a.py::TestX::test_y - AssertionError: nope",
                 "ERROR tests/test_b.py - ModuleNotFoundError: No module named 'x'",
                 "FAILED tests/test_a.py::test_p[a b] - ValueError",
                 "1 failed, 1 error"]
        assert diagnostics._parse_pytest_failures(lines) == [
            "tests/test_a.py::TestX::test_y", "tests/test_b.py", "tests/test_a.py::test_p[a b]"]

    def test_no_summary_lines_means_no_failures(self):
        assert diagnostics._parse_pytest_failures(["....", "4 passed in 0.1s"]) == []


class TestStreamProcessTimeout:
    def test_a_hung_process_is_killed_and_reported_as_timed_out(self):
        items = list(diagnostics._stream_process(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=1))
        assert items[-1]["timed_out"] is True
        assert items[-1]["returncode"] != 0
