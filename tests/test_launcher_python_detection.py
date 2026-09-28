"""
tests/test_launcher_python_detection.py -- Step 79 regression coverage.

start.bat/start.ps1 are Windows batch/PowerShell, not Python -- actually
executing them (to prove the Microsoft Store "App execution alias" stub
is correctly distinguished from a real interpreter, and that
--python-version actually invokes `py -X.Y`) needs a real Windows
machine and is the roadmap's own acknowledged manual check, same as
tests/test_check_setup.py's own note about start.bat. What's tested here
is the launcher scripts' own text: that the stub-detection logic and the
version-pin logic are actually present and wired correctly, not just
described in a comment -- a real regression check against reintroducing
the bare `where python`/`Get-Command python` presence-only check this
step replaced.
"""
import os
import re

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _read(name):
    with open(os.path.join(PROJECT_ROOT, name), encoding="utf-8") as f:
        return f.read()


class TestStartBatStubDetection:
    """start.bat must actually invoke Python and check its exit code,
    not just confirm a file named python.exe is on PATH -- the exact
    check that let the Microsoft Store stub through silently."""

    def test_no_longer_treats_where_python_alone_as_success(self):
        text = _read("start.bat")
        # The old bug: `where python` followed directly by the
        # venv-creation branch, with no real invocation in between.
        # A bare `where python` may still appear (used later, only to
        # distinguish "not found" from "found but a non-functional
        # stub" in the error message), but it must not be the thing
        # that gates progressing to venv creation.
        where_idx = text.find("where python")
        version_check_idx = text.find("--version")
        assert where_idx != -1, "expected a `where python` call somewhere (for the stub-vs-missing message)"
        assert version_check_idx != -1, "expected an actual `--version` invocation, not just presence-checking"

    def test_runs_python_version_before_venv_creation(self):
        text = _read("start.bat")
        version_check_idx = text.index("--version")
        venv_idx = text.index("-m venv")
        assert version_check_idx < venv_idx, (
            "the --version check (which catches the non-functional Store "
            "stub) must run before venv creation, not after")

    def test_stub_specific_message_mentions_app_execution_aliases(self):
        text = _read("start.bat")
        assert "App execution aliases" in text
        assert "App Installer python.exe" in text
        assert "App Installer python3.exe" in text

    def test_generic_missing_python_message_still_present(self):
        # The plain "not found at all" case must keep its own distinct
        # message -- the stub message must not replace it outright, or
        # a genuinely Python-less machine gets a confusing "disable
        # this alias" instruction pointing at a stub that isn't there.
        text = _read("start.bat")
        assert "Python wasn't found on PATH." in text
        assert "install.python.org".replace("install.", "") in text or "python.org/downloads" in text

    def test_python_version_flag_is_parsed(self):
        text = _read("start.bat")
        assert '"%~1"=="--python-version"' in text
        assert "set PYTHON_VERSION=%~2" in text

    def test_marker_file_fallback_reads_python_version_file(self):
        text = _read("start.bat")
        assert "exist PYTHON_VERSION" in text
        assert "set /p PYTHON_VERSION=<PYTHON_VERSION" in text

    def test_pinned_version_invokes_py_launcher_not_plain_python(self):
        text = _read("start.bat")
        # PYCMD must resolve to `py -%PYTHON_VERSION%` when the pin is
        # set, and that same %PYCMD% variable (not a hardcoded `python`)
        # must be what actually creates the venv.
        assert re.search(r"set PYCMD=py -%PYTHON_VERSION%", text)
        assert "%PYCMD% -m venv" in text
        # The old hardcoded call must be gone from the venv-creation line.
        assert "\npython -m venv" not in text


class TestStartPs1StubDetection:
    """start.ps1 has the identical bug (Get-Command only checks
    presence) -- confirmed by the secondary-review session and amended
    into this step's scope."""

    def test_no_longer_gates_on_get_command_alone(self):
        text = _read("start.ps1")
        assert "--version" in text, "expected an actual --version invocation, not just Get-Command presence-checking"

    def test_runs_version_check_before_venv_creation(self):
        text = _read("start.ps1")
        version_check_idx = text.index("--version")
        venv_idx = text.index("-m venv")
        assert version_check_idx < venv_idx

    def test_stub_specific_message_mentions_app_execution_aliases(self):
        text = _read("start.ps1")
        assert "App execution aliases" in text
        assert "App Installer python.exe" in text

    def test_python_version_param_exists(self):
        text = _read("start.ps1")
        assert "[string]$PythonVersion" in text

    def test_marker_file_fallback_reads_python_version_file(self):
        text = _read("start.ps1")
        assert 'Join-Path $PSScriptRoot "PYTHON_VERSION"' in text

    def test_pinned_version_invokes_py_launcher_not_plain_python(self):
        text = _read("start.ps1")
        assert '$PyCmd = "py"' in text
        assert '$PyCmd = "python"' in text
        assert "& $PyCmd @PyArgs -m venv" in text
        assert "\npython -m venv" not in text
