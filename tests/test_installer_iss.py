"""Step 80b: static checks of the Inno Setup script (installer/baihe.iss)
and the on-demand Windows installer workflow. Inno Setup can't run here, so
these pin the properties that matter: per-user install, user data in a
separate per-user folder that's never in the payload and never deleted
unless asked, no secrets, and a workflow that doesn't run on every PR."""
import os
import re

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ISS_PATH = os.path.join(ROOT, "installer", "baihe.iss")
WORKFLOW_PATH = os.path.join(ROOT, ".github", "workflows", "windows-installer.yml")


def _read(path):
    with open(path, encoding="utf-8") as f:
        return f.read()


@pytest.fixture(scope="module")
def iss():
    return _read(ISS_PATH)


def _section(text, name):
    m = re.search(rf"^\[{re.escape(name)}\]\s*$(.*?)(?=^\[[A-Za-z]+\]\s*$|\Z)", text, re.M | re.S)
    assert m, f"[{name}] section missing"
    return m.group(1)


def _entries(text, name):
    return [ln.strip() for ln in _section(text, name).splitlines()
            if ln.strip() and not ln.strip().startswith(";")]


def _setup_value(text, key):
    m = re.search(rf"^{key}=(.*)$", _section(text, "Setup"), re.M)
    return m.group(1).strip() if m else None


class TestSetup:
    def test_per_user_install_without_admin(self, iss):
        assert _setup_value(iss, "PrivilegesRequired") == "lowest"
        assert _setup_value(iss, "DefaultDirName") == r"{autopf}\{#AppName}"

    def test_fixed_app_id_for_upgrades(self, iss):
        assert re.fullmatch(r"\{\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}",
                            _setup_value(iss, "AppId"))

    def test_64_bit_and_disk_space(self, iss):
        assert _setup_value(iss, "ArchitecturesInstallIn64BitMode") == "x64compatible"
        assert _setup_value(iss, "ExtraDiskSpaceRequired") == "{#ExtraDiskSpace}"


class TestNoSecrets:
    SECRET_PATTERNS = [
        r"sk-ant-[A-Za-z0-9]", r"sk-[A-Za-z0-9]{20,}", r"AIza[0-9A-Za-z_-]{20,}",
        r"\bhf_[A-Za-z0-9]{20,}", r"\bgh[pousr]_[A-Za-z0-9]{20,}", r"\bgsk_[A-Za-z0-9]{20,}",
        r"(?i)(api[_-]?key|token|secret|password)\s*=\s*['\"]?[A-Za-z0-9]{12,}",
        r"BAIHE_[A-Z_]*KEY\s*=",
    ]

    @pytest.mark.parametrize("pattern", SECRET_PATTERNS)
    def test_no_secret_values(self, iss, pattern):
        assert not re.search(pattern, iss)

    def test_files_come_only_from_the_payload(self, iss):
        entries = _entries(iss, "Files")
        assert entries
        for entry in entries:
            source = re.search(r'Source:\s*"([^"]+)"', entry).group(1)
            assert source.startswith("{#PayloadDir}\\"), source
            lower = source.lower()
            for bad in (".env", "library", "model_cache", "tests", "venv"):
                assert bad not in lower, source

    def test_wheels_are_temporary(self, iss):
        wheels = [e for e in _entries(iss, "Files") if "wheels" in e]
        assert wheels and all("{tmp}" in e and "deleteafterinstall" in e for e in wheels)


class TestDataFolder:
    def test_default_is_per_user_localappdata(self, iss):
        assert re.search(r"ExpandConstant\('\{localappdata\}\\\{#AppName\}'\)", iss)

    def test_silent_install_can_choose_it(self, iss):
        assert "{param:DATADIR}" in iss
        assert "GetPreviousData('DataDir'" in iss and "SetPreviousData(" in iss

    def test_validated_for_silent_installs_too(self, iss):
        prepare = re.search(r"function PrepareToInstall.*?^end;", iss, re.M | re.S).group(0)
        assert "DataDirProblem(" in prepare

    def test_rejects_a_data_folder_inside_the_install_folder(self, iss):
        problem = re.search(r"function DataDirProblem.*?^end;", iss, re.M | re.S).group(0)
        assert "inside the install folder" in problem
        assert "not a whole drive" in problem

    def test_install_step_gets_the_data_dir(self, iss):
        assert "postinstall.py" in iss and "--data-dir" in iss and "--wheels" in iss


class TestUninstall:
    def test_program_folders_only(self, iss):
        entries = _entries(iss, "UninstallDelete")
        assert entries
        for entry in entries:
            name = re.search(r'Name:\s*"([^"]+)"', entry).group(1)
            assert name.startswith("{app}"), name
        assert not re.search(r"(?i)localappdata|DataDir", _section(iss, "UninstallDelete"))

    def test_data_deletion_is_opt_in_and_defaults_off(self, iss):
        add_box = re.search(r"function AddCheckBox.*?^end;", iss, re.M | re.S).group(0)
        assert "Result.Checked := False;" in add_box
        init = re.search(r"function InitializeUninstall.*?^end;", iss, re.M | re.S).group(0)
        for flag in ("DeleteLibrary", "DeleteSettings", "DeleteModels"):
            assert f"{flag} := False;" in init
        # A silent uninstall never asks, so never deletes.
        assert "not UninstallSilent()" in init

    def test_each_deletion_is_guarded_by_its_box(self, iss):
        step = re.search(r"procedure CurUninstallStepChanged.*?^end;", iss, re.M | re.S).group(0)
        assert re.search(r"if DeleteLibrary then\s+DelTree\(UninstDataDir \+ '\\library'", step)
        assert re.search(r"if DeleteSettings then\s+DeleteFile\(UninstDataDir \+ '\\\.env'\)", step)
        assert re.search(r"if DeleteModels then\s+DelTree\(UninstDataDir \+ '\\model_cache'", step)
        # The data folder itself is only ever removed when empty.
        assert "RemoveDir(UninstDataDir)" in step
        assert not re.search(r"DelTree\(UninstDataDir\s*,", step)

    def test_stops_the_server_first(self, iss):
        assert any("--stop" in e for e in _entries(iss, "UninstallRun"))


class TestLauncherWiring:
    def test_shortcut_runs_the_launcher_with_the_bundled_python(self, iss):
        icons = _entries(iss, "Icons")
        main = [e for e in icons if e.startswith('Name: "{group}\\{#AppName}"')]
        assert main
        assert r'Filename: "{app}\python\pythonw.exe"' in main[0]
        assert r"launcher.py" in main[0] and "-s " in main[0]

    def test_no_streamlit(self, iss):
        assert "streamlit" not in iss.lower()


class TestPascalPitfalls:
    """Two ways the [Code] section has broken the compile before."""

    def test_no_line_starts_with_a_hash(self, iss):
        # ISPP reads a line starting with '#' as a preprocessor directive.
        code = iss.split("[Code]", 1)[1]
        assert not re.search(r"^\s*#\d", code, re.M)

    def test_no_brace_comment_mentions_a_constant(self, iss):
        # A { } comment ends at the first '}', so '{app}' inside one breaks it.
        code = iss.split("[Code]", 1)[1]
        for comment in re.findall(r"\{[^}]*\}", code):
            assert not comment.startswith("{ "), comment


@pytest.fixture(scope="module")
def wf():
    return _read(WORKFLOW_PATH)


class TestWorkflow:
    def test_on_demand_only(self, wf):
        on = re.search(r"^on:\n(.*?)^\S", wf, re.M | re.S).group(1)
        assert "workflow_dispatch:" in on
        assert re.search(r"tags:\s*\n\s*-\s*\"installer-v\*\"", on)
        assert "pull_request" not in on
        assert "branches" not in on

    def test_windows_with_a_timeout(self, wf):
        assert "runs-on: windows-latest" in wf
        assert re.search(r"timeout-minutes: \d+", wf)

    def test_inno_setup_is_pinned_by_hash(self, wf):
        assert re.search(r'INNO_SHA256: "[0-9a-f]{64}"', wf)
        assert "Get-FileHash" in wf

    def test_smoke_test_covers_install_health_and_uninstall(self, wf):
        for needle in ("/VERYSILENT", "/DATADIR=", "--no-browser", "/api/health",
                       "unins000.exe", "library.db", "upload-artifact"):
            assert needle in wf, needle

    def test_dispatch_input_goes_through_env(self, wf):
        # Never pasted into a script (injection).
        assert "run: |" in wf
        for block in re.findall(r"run: \|\n(.*?)(?=\n\s*- (?:name|uses):|\Z)", wf, re.S):
            assert "github.event.inputs" not in block
