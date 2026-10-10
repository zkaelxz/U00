"""
tests/test_start_bat_update.py -- start.bat's "Auto-update" section.

start.bat can't be run on the Linux CI, so (as in
tests/test_launcher_python_detection.py) this checks the script's own
text: the skip rules are present and in front of the fetch, only a
fast-forward is ever attempted, nothing that rewrites or deletes the
owner's files is used, and the section runs before the dependency check.
"""
import os

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _lines(section):
    with open(os.path.join(PROJECT_ROOT, "start.bat"), encoding="utf-8", newline="") as f:
        text = f.read().replace("\r\n", "\n")
    start = text.index("REM --- Auto-update")
    end = text.index("REM --- Dependencies")
    body = text[start:end] if section else text
    return body.splitlines()


def _code():
    """The update section without REM comment lines."""
    return [ln for ln in _lines(True) if not ln.lstrip().upper().startswith("REM")]


def _index(prefix):
    for i, ln in enumerate(_code()):
        if ln.startswith(prefix):
            return i
    raise AssertionError(f"no line starting with {prefix!r} in the update section")


class TestUpdateSectionPlacement:
    def test_runs_after_venv_and_before_dependencies(self):
        text = "\n".join(_lines(False))
        assert text.index("REM --- Virtual environment") < text.index("REM --- Auto-update")
        assert text.index("REM --- Auto-update") < text.index("REM --- Dependencies")

    def test_forced_dependency_install_is_checked_before_the_import_test(self):
        text = "\n".join(_lines(False))
        forced = text.index("if defined BAIHE_FORCE_DEPS goto :install_deps")
        assert forced < text.index("%PY% -c \"import requests")

    def test_file_keeps_crlf_line_endings(self):
        with open(os.path.join(PROJECT_ROOT, "start.bat"), "rb") as f:
            raw = f.read()
        assert raw.count(b"\n") == raw.count(b"\r\n")


class TestUpdateIsSkipped:
    def test_each_skip_rule_precedes_the_fetch(self):
        fetch = _index("git -c http.lowSpeedLimit")
        for rule in (
            "if defined BAIHE_CI goto :update_done",
            "if defined BAIHE_SERVER_ONLY goto :update_done",
            "if defined BAIHE_NO_UPDATE goto :update_done",
            "if exist NOUPDATE goto :update_done",
            "if exist INSTALLED goto :update_done",
            'if not exist ".git" goto :update_done',
            "if errorlevel 1 goto :update_done",
        ):
            assert _index(rule) < fetch, rule

    def test_skips_when_git_missing_or_app_running(self):
        code = _code()
        assert "where git >nul 2>nul" in code
        assert "call :health_ok" in code
        assert _index("where git") < _index("call :health_ok") < _index("git -c http.lowSpeedLimit")

    def test_no_update_flag_is_parsed(self):
        text = "\n".join(_lines(False))
        assert 'if /i "%~1"=="--no-update" set BAIHE_NO_UPDATE=1' in text

    def test_branch_and_clean_tree_are_checked_before_fetch(self):
        fetch = _index("git -c http.lowSpeedLimit")
        assert _index("for /f \"delims=\" %%B in ('git rev-parse --abbrev-ref HEAD") < fetch
        assert 'if not "%UPD_BRANCH%"=="baihe-subtitler" goto :update_other_branch' in _code()
        assert _index("for /f \"delims=\" %%L in ('git status --porcelain --untracked-files=no") < fetch

    def test_skip_paths_do_not_pause(self):
        assert not any("pause" in ln for ln in _code())
        assert not any("exit /b 1" in ln for ln in _code())


class TestUpdateIsFastForwardOnly:
    def test_fetch_has_a_low_speed_timeout_and_prompt_off(self):
        line = next(ln for ln in _code() if ln.startswith("git -c http.lowSpeedLimit"))
        assert "http.lowSpeedLimit=1000" in line
        assert "http.lowSpeedTime=15" in line
        assert "fetch --quiet origin baihe-subtitler" in line
        assert "set GIT_TERMINAL_PROMPT=0" in _code()

    def test_merge_is_ff_only_from_fetch_head(self):
        merges = [ln for ln in _code() if "git merge " in ln]
        assert len(merges) == 1
        assert "--ff-only" in merges[0]
        assert "FETCH_HEAD" in merges[0]

    def test_never_rewrites_or_deletes_files(self):
        text = "\n".join(_lines(True)).lower()
        for word in ("reset", "stash", "clean", "checkout", "rebase", "--force", "pull",
                     "git rm", "del ", "rmdir", "rd ", "git restore", "git switch", "branch -d"):
            assert word not in text, word

    def test_only_expected_git_subcommands_are_used(self):
        import re
        used = set()
        for ln in _code():
            if ln.lstrip().lower().startswith("echo"):
                continue
            for m in re.finditer(r"\bgit (?:-c \S+ )*([a-z-]+)", ln):
                used.add(m.group(1))
        assert used <= {"rev-parse", "status", "fetch", "merge-base", "merge", "rev-list",
                        "log", "diff"}, used

    def test_relaunch_is_one_line_and_exits(self):
        line = next(ln for ln in _code() if "git merge " in ln)
        assert 'call "%~f0" %*' in line
        assert "exit /b" in line


class TestAfterUpdate:
    def test_dependency_files_force_the_install(self):
        line = next(ln for ln in _code() if "git diff --name-only" in ln and "requirements-core.txt" in ln)
        for name in ("requirements-core.txt", "constraints.txt", "constraints.lock.txt"):
            assert name in line
        assert "if defined UPD_DEPS set BAIHE_FORCE_DEPS=1" in _code()

    def test_frontend_changes_rebuild_only_when_npm_exists(self):
        code = _code()
        line = next(ln for ln in code if "git diff --name-only" in ln and "frontend/src" in ln)
        for name in ("frontend/src", "frontend/public", "frontend/index.html",
                     "frontend/package.json", "frontend/package-lock.json", "frontend/vite.config.ts"):
            assert name in line
        assert _index("where npm") < _index("set BAIHE_BUILD_FRONTEND=1")
        assert "if errorlevel 1 goto :update_no_npm" in code
        assert any("start.bat --build-frontend" in ln for ln in code)

    def test_auto_rebuild_failure_does_not_stop_the_launch(self):
        text = "\n".join(_lines(False))
        failed = text[text.index("\n:build_failed\n"):]
        assert "if defined BAIHE_AUTO_BUILD" in failed.split(":frontend_ready")[0]

    def test_commit_subjects_are_printed_by_git_capped_at_five(self):
        line = next(ln for ln in _code() if ln.startswith("git log"))
        assert "-n 5" in line
        assert "%%s" in line
