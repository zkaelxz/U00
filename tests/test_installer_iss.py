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


class TestExistingDataWarning:
    def test_setup_warns_about_an_existing_library_or_keys(self, iss):
        # The data-folder page asks before reusing a folder that already
        # holds library\\, .env or model_cache, defaulting to No.
        m = re.search(r"function ExistingDataItems.*?\nend;", iss, re.S)
        assert m
        for name in ("library", ".env", "model_cache"):
            assert f"'{name}'" in m.group(0) or f"'{name}" in m.group(0)
        nb = re.search(r"function NextButtonClick.*?\nend;", iss, re.S).group(0)
        assert "ExistingDataItems(DataDir())" in nb
        assert "keeps them unless you tick" in nb
        warn = nb[nb.index("ExistingDataItems(DataDir())"):]
        assert "MB_DEFBUTTON2" in warn

    def test_deletion_stays_opt_in_and_by_name(self, iss):
        # Uninstall boxes start unticked and a folder Setup didn't create is
        # never removed wholesale.
        assert iss.count("Result.Checked := False;") == 1
        assert "if CleanAll and UninstDataDirCreated then" in iss
        assert "DataDirCreatedBySetup := DataDirIsNew or" in iss


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

    def test_writability_checked_before_anything_is_replaced(self, iss):
        prepare = re.search(r"function PrepareToInstall.*?^end;", iss, re.M | re.S).group(0)
        assert "DataDirWriteProblem(" in prepare
        assert prepare.index("DataDirWriteProblem(") < prepare.index("StopRunningServer(")

    def test_failed_package_step_has_its_own_exit_code(self, iss):
        # Not one of Inno Setup's own codes (1-8).
        code = re.search(r"function GetCustomSetupExitCode.*?^end;", iss, re.M | re.S).group(0)
        assert "Result := 100" in code

    def test_install_step_gets_the_data_dir(self, iss):
        assert "postinstall.py" in iss and "--data-dir" in iss and "--wheels" in iss


def _func(iss, name):
    return re.search(rf"(?:function|procedure) {name}\b.*?^end;", iss, re.M | re.S).group(0)


class TestUninstall:
    def test_program_folders_only_when_baihe_put_them_there(self, iss):
        # No [UninstallDelete]: the program folders are removed in code, and
        # only when this install owns them (L5).
        assert "[UninstallDelete]" not in iss
        init = _func(iss, "InitializeUninstall")
        assert "ProgramOwned := IsBaiheInstallDir(ExpandConstant('{app}'))" in init
        step = _func(iss, "CurUninstallStepChanged")
        owned = step[step.index("if ProgramOwned then"):step.index("RemoveDir(ExpandConstant('{app}'))")]
        assert "{app}\\python" in owned and "{app}\\app" in owned

    def test_setup_refuses_a_foreign_python_or_app_folder(self, iss):
        problem = _func(iss, "InstallDirProblem")
        assert "IsBaiheInstallDir(AppDir)" in problem and "'python'" in problem and "'app'" in problem
        # Any other program's manifest.json doesn't count: it must name this
        # product and AppId (build_installer.write_manifest), or this app's
        # own uninstall entry must point at the folder.
        owned = _func(iss, "IsBaiheInstallDir")
        assert '"product": "Baihe Studio"' in owned
        app_id = re.search(r"AppId=\{\{([0-9A-F-]+)\}", iss).group(1)
        assert app_id in owned and app_id in iss.split("UninstallKey =", 1)[1].split("\n", 1)[0]
        assert "InstallLocation" in owned
        import sys
        sys.path.insert(0, os.path.join(ROOT, "installer"))
        import build_installer
        assert build_installer.INSTALLER_APP_ID == app_id
        assert "InstallDirProblem(" in _func(iss, "PrepareToInstall")
        assert "InstallDirProblem(" in _func(iss, "NextButtonClick")

    def test_data_deletion_is_opt_in_and_defaults_off(self, iss):
        assert "Result.Checked := False;" in _func(iss, "AddCheckBox")
        init = _func(iss, "InitializeUninstall")
        for flag in ("DeleteLibrary", "DeleteSettings", "DeleteModels", "CleanAll"):
            assert f"{flag} := False;" in init
        # Silent: nothing is deleted unless /CLEAN is passed.
        silent = init[init.index("if UninstallSilent() then"):init.index("else if (UninstDataDir")]
        assert "if HasCleanSwitch() then" in silent
        assert "CompareText(ParamStr(I), '/CLEAN') = 0" in _func(iss, "HasCleanSwitch")

    def test_clean_uninstall_needs_a_second_confirmation(self, iss):
        ask = _func(iss, "AskWhatToDelete")
        assert "Remove everything (clean uninstall)" in ask
        assert "MB_YESNO or MB_DEFBUTTON2" in ask and "UninstDataDir" in ask
        assert "CleanBox.OnClick := @CleanBoxClick;" in ask
        click = _func(iss, "CleanBoxClick")
        for box in ("LibraryBox", "SettingsBox", "ModelsBox"):
            assert f"{box}.Checked := True;" in click

    def test_each_deletion_is_guarded(self, iss):
        step = _func(iss, "CurUninstallStepChanged")
        assert re.search(r"if DeleteLibrary then\s+DeleteTree\(UninstDataDir \+ '\\library'", step)
        assert re.search(r"if DeleteSettings and FileExists\(UninstDataDir \+ '\\\.env'\) then\s+"
                         r"if DeleteFile\(UninstDataDir \+ '\\\.env'\)", step)
        assert re.search(r"if DeleteModels then\s+DeleteTree\(UninstDataDir \+ '\\model_cache'", step)
        # The whole data folder only on a clean uninstall of a folder Setup created.
        assert re.search(r"if CleanAll and UninstDataDirCreated then\s+begin\s+(//[^\n]*\s+)*"
                         r"DeleteTree\(UninstDataDir,", step)
        whole = [m.start() for m in re.finditer(r"DeleteTree\(UninstDataDir,", step)]
        assert len(whole) == 1
        # Otherwise the folder and launcher\ go only when empty; the launcher's
        # own files by name.
        assert "RemoveDir(UninstDataDir);" in step
        assert "RemoveDir(UninstDataDir + '\\launcher');" in step
        for name in ("server.pid", "shutdown.token", "starting.lock", "server.log", "install.log"):
            assert f"DeleteFile(UninstDataDir + '\\launcher\\{name}');" in step
        assert "DelTree(UninstDataDir" not in step

    def test_clean_uninstall_keeps_shared_caches_and_lists_them(self, iss):
        step = _func(iss, "CurUninstallStepChanged")
        for shared in ("ms-playwright", "pip\\cache", ".cache\\huggingface"):
            assert shared in step
        assert "NoteShared(" in step and "Left in place:" in step
        temp = _func(iss, "DeleteOwnTempItems")
        for pattern in ("'baihe_*'", "'baihe_page_*'", "'baihe-torch-pins-*'"):
            assert pattern in temp
        assert "FILE_ATTRIBUTE_DIRECTORY" in _func(iss, "DeleteTempMatches")
        assert ".deno" in step

    def test_declining_the_clean_confirmation_restores_the_boxes(self, iss):
        ask = _func(iss, "AskWhatToDelete")
        declined = ask[ask.index("if not Confirmed then"):]
        for box, prev in (("LibraryBox", "PrevLibrary"), ("SettingsBox", "PrevSettings"),
                          ("ModelsBox", "PrevModels")):
            assert f"{box}.Checked := {prev};" in declined
            assert f"{prev} := {box}.Checked;" in _func(iss, "CleanBoxClick")

    def test_clean_warning_says_the_whole_folder_when_setup_made_it(self, iss):
        ask = _func(iss, "AskWhatToDelete")
        created = ask[ask.index("if UninstDataDirCreated then"):ask.index("  else\n")]
        assert "EVERYTHING in it, including any files you put there yourself" in created

    def test_failed_deletions_are_reported(self, iss):
        tree = _func(iss, "DeleteTree")
        assert "if DelTree(Path, True, True, True) and not DirExists(Path) then" in tree
        assert "couldn''t be removed" in tree

    def test_lockdown_only_for_a_folder_made_now(self, iss):
        assert "DataDirIsNew := not DirExists(DataDir());" in _func(iss, "PrepareToInstall")
        post = _func(iss, "RunPostInstall")
        assert "if DataDirIsNew then" in post and "--data-dir-new" in post

    def test_created_flag_round_trip(self, iss):
        assert "CreatedFlagLine = '# created-by-setup';" in iss
        import sys
        sys.path.insert(0, os.path.join(ROOT, "installer"))
        import postinstall
        assert postinstall.CREATED_FLAG_LINE == "# created-by-setup"
        prepare = _func(iss, "PrepareToInstall")
        # Decided before Setup creates the folder (DataDirWriteProblem makes it).
        assert prepare.index("DataDirCreatedBySetup :=") < prepare.index("DataDirWriteProblem(")
        assert "--data-dir-created" in _func(iss, "RunPostInstall")

    def test_existing_folder_outside_the_profile_is_warned_about(self, iss):
        nxt = _func(iss, "NextButtonClick")
        assert "{%USERPROFILE}" in nxt and "API keys" in nxt and "MB_DEFBUTTON2" in nxt
        # ...but not again on an update that keeps the same folder.
        assert "NormDir(OldDir) <> NormDir(DataDir())" in nxt

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


class TestBootService:
    """installer/service.py wired into Setup and the uninstaller
    (docs/windows-installer-design.md, "Boot service")."""

    def test_task_on_by_default_and_install_stays_per_user(self, iss):
        task = [e for e in _entries(iss, "Tasks") if e.startswith('Name: "service"')]
        assert len(task) == 1 and "unchecked" not in task[0]
        assert "administrator permission" in task[0]
        assert _setup_value(iss, "PrivilegesRequired") == "lowest"

    def test_files_come_from_the_payload_and_update_replaces_them(self, iss):
        sources = [re.search(r'Source:\s*"([^"]+)"', e).group(1) for e in _entries(iss, "Files")]
        assert "{#PayloadDir}\\service\\*" in sources
        assert any("{app}\\service" in e for e in _entries(iss, "InstallDelete"))

    def test_only_the_service_step_is_elevated(self, iss):
        helper = _func(iss, "RunServiceHelper")
        assert "if IsAdmin() then" in helper and "ShellExec('runas'" in helper
        assert "-I -S" in helper and "(ResultCode = 0)" in helper
        assert iss.count("'runas'") == 1

    def test_install_runs_from_setups_files_the_rest_from_the_admin_folder(self, iss):
        conf = _func(iss, "ConfigureService")
        assert "RunServiceHelper(ExpandConstant('{app}\\service')" in conf
        assert "--install-root" in conf and "--data-dir" in conf
        assert "RunServiceHelper(AdminDir(), 'uninstall')" in conf
        assert "RunServiceHelper(AdminDir(), 'stop')" in _func(iss, "StopBackgroundService")
        assert "'\\Baihe Studio Services'" in _func(iss, "AdminDir")

    def test_set_up_after_the_packages_and_removed_when_unticked(self, iss):
        step = _func(iss, "CurStepChanged")
        assert re.search(r"RunPostInstall\(\);\s+if not PostInstallFailed then\s+ConfigureService\(\);", step)
        assert "WizardIsTaskSelected('service')" in _func(iss, "ConfigureService")

    def test_own_exit_code(self, iss):
        code = _func(iss, "GetCustomSetupExitCode")
        assert "Result := 101" in code and code.index("100") < code.index("101")

    def test_update_stops_the_service_before_replacing_files(self, iss):
        prepare = _func(iss, "PrepareToInstall")
        assert (prepare.index("DataDirWriteProblem(") < prepare.index("StopBackgroundService(")
                < prepare.index("StopRunningServer("))

    def test_uninstall_removes_the_service_first_and_stops_if_it_cant(self, iss):
        init = _func(iss, "InitializeUninstall")
        assert init.rstrip().endswith("Result := RemoveService();\nend;")
        remove = _func(iss, "RemoveService")
        assert "RunServiceHelper(AdminDir(), 'uninstall')" in remove
        assert "if not Result then" in remove

    def test_uninstall_can_be_run_again_over_a_half_removed_service(self, iss):
        present = _func(iss, "ServiceOrAdminDirPresent")
        assert "ServiceInstalled()" in present and "\\helper\\python\\python.exe" in present
        assert "if not ServiceOrAdminDirPresent() then" in _func(iss, "RemoveService")
        assert "ServiceOrAdminDirPresent()" in _func(iss, "ConfigureService")

    def test_nothing_but_the_service_is_added(self, iss):
        # No firewall, router or second-listener step belongs to the installer.
        for needle in ("netsh", "firewall", "caddy", "upnp"):
            assert needle not in iss.lower(), needle


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

    def test_actions_pinned_by_commit_and_no_persisted_token(self, wf):
        uses = re.findall(r"uses:\s*(\S+)", wf)
        assert uses
        for ref in uses:
            assert re.fullmatch(r"[\w.-]+/[\w.-]+@[0-9a-f]{40}", ref), ref
        assert "persist-credentials: false" in wf

    def test_inno_setup_is_pinned_by_hash(self, wf):
        assert re.search(r'INNO_SHA256: "[0-9a-f]{64}"', wf)
        assert "Get-FileHash" in wf

    def test_smoke_test_covers_install_health_and_uninstall(self, wf):
        for needle in ("/VERYSILENT", "/DATADIR=", "--no-browser", "/api/health",
                       "unins000.exe", "library.db", "upload-artifact"):
            assert needle in wf, needle

    def test_smoke_test_covers_stop_and_clean_uninstall(self, wf):
        # Stop ends the server's children (smoke_child.py joins its job);
        # a /CLEAN uninstall removes Baihe's folders and nothing else.
        for needle in ("--stop", "smoke_child.py", "outlived Stop", '"/CLEAN"',
                       "notbaihe_smoke_clean", "sentinel.txt",
                       "touched the first install's data folder"):
            assert needle in wf, needle

    def test_launcher_smoke_tests_opt_out_of_the_service(self, wf):
        assert wf.count('"/MERGETASKS=!service"') == 2

    def test_service_steps_cover_boot_start_restart_update_and_removal(self, wf):
        for needle in (
            "Get-Service BaiheStudio", "'Automatic'", "NT SERVICE\\BaiheStudio", "qprivs",
            "SeImpersonatePrivilege", "qfailure", "http://127.0.0.1:8600/api/health",
            "listening beyond 127.0.0.1:8600", "Stop-Process -Id $server.ProcessId",
            "processes left after stopping the service",
            "the update changed .env", "the BaiheStudio service is still installed",
            "the BaiheStudio account still has access to the data folder",
        ):
            assert needle in wf, needle
        for needle in ("caddy", "enable-remote", "New-NetFirewallRule", "netsh"):
            assert needle not in wf.lower(), needle

    def test_dispatch_input_goes_through_env(self, wf):
        # Never pasted into a script (injection).
        assert "run: |" in wf
        for block in re.findall(r"run: \|\n(.*?)(?=\n\s*- (?:name|uses):|\Z)", wf, re.S):
            assert "github.event.inputs" not in block
