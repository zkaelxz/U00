"""tools/ci_changes.py: which CI jobs a change needs. A wrong "false" here lets a regression through,
so these pin each rule in the table in docs/testing-and-ci.md."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import ci_changes  # noqa: E402

ALL = {"notes_only": False, "run_e2e": True, "run_full_pytest": True, "run_windows_scripts": True, "run_caddy": True,
       "run_app_checks": True}
ALL_OUT = {"notes_only": "false", "run_e2e": "true", "run_full_pytest": "true", "run_windows_scripts": "true",
           "run_caddy": "true", "run_app_checks": "true"}


def pr(*files, labels=()):
    return ci_changes.classify(list(files), "pull_request", labels)


@pytest.mark.parametrize("files", [
    ["docs/archive/research/reports/a.md"],
    ["docs/archive/research/research_notes/x/y.md", "docs/archive/research/reports/b.md"],
])
def test_notes_only_changes_skip_everything_but_guards(files):
    assert pr(*files)["notes_only"] is True


@pytest.mark.parametrize("files", [
    ["docs/archive/research/reports/a.md", "services/x.py"],
    ["docs/testing-and-ci.md"],
    ["docs/archive/researchx/a.md"],
    ["docs/archive/research/reports/a.md", "docs/archive/session-harvest/2026.md"],
])
def test_mixed_or_other_docs_are_not_notes_only(files):
    assert pr(*files)["notes_only"] is False


@pytest.mark.parametrize("files", [
    ["services/drama_service.py"],
    ["db.py", "tests/test_db.py"],
    ["scripts/x.py", "tools/repo_map.py"],
    ["docs/user-guide.md", "services/x.py"],
])
def test_backend_only_change_skips_e2e_but_runs_all_pytest(files):
    assert pr(*files) == {"notes_only": False, "run_e2e": False, "run_full_pytest": True, "run_windows_scripts": False,
                          "run_caddy": False, "run_app_checks": True}


@pytest.mark.parametrize("files", [
    ["api/routers/x_routes.py"],
    ["api/schemas/common.py", "services/x.py"],
    ["frontend/src/App.tsx", "services/x.py"],
])
def test_touching_api_or_frontend_runs_e2e(files):
    assert pr(*files)["run_e2e"] is True


@pytest.mark.parametrize("files", [
    ["frontend/src/App.tsx"],
    ["frontend/src/a.ts", "frontend/e2e/b.spec.ts"],
    ["docs/user-guide.md"],
    ["frontend/src/a.ts", "docs/x.md"],
])
def test_frontend_and_docs_only_change_runs_the_reduced_pytest(files):
    assert pr(*files)["run_full_pytest"] is False


@pytest.mark.parametrize("files", [
    ["frontend/src/a.ts", "api/x.py"],
    ["frontend/src/a.ts", "tests/test_x.py"],
    ["frontendish/a.ts"],
    ["db.py"],
])
def test_any_file_outside_frontend_and_docs_runs_full_pytest(files):
    assert pr(*files)["run_full_pytest"] is True


def test_frontend_only_change_still_runs_e2e_and_docs_only_does_not():
    assert pr("frontend/src/a.ts") == {"notes_only": False, "run_e2e": True, "run_full_pytest": False,
                                       "run_windows_scripts": False, "run_caddy": False,
                                       "run_app_checks": True}
    assert pr("docs/a.md") == {"notes_only": False, "run_e2e": False, "run_full_pytest": False,
                               "run_windows_scripts": False, "run_caddy": False,
                               "run_app_checks": True}


@pytest.mark.parametrize("files", [
    ["sources/adapters/bilibili.py"],
    ["sources/adapters/bilibili.py", "tests/test_sources_bilibili.py"],
    ["tests/test_sources.py"],
])
def test_sources_and_tests_only_change_skips_smoke_and_frontend_but_runs_pytest(files):
    assert pr(*files) == {"notes_only": False, "run_e2e": False, "run_full_pytest": True, "run_windows_scripts": False,
                          "run_caddy": False, "run_app_checks": False}


@pytest.mark.parametrize("extra", ["services/x.py", "api/x.py", "frontend/src/a.ts", "docs/a.md", "tests/conftest.py"])
def test_any_file_outside_sources_and_tests_brings_smoke_and_frontend_back(extra):
    assert pr("sources/adapters/bilibili.py", extra)["run_app_checks"] is True


def test_app_checks_run_for_full_ci_label_and_non_pr_events():
    assert pr("sources/adapters/bilibili.py", labels=["full-ci"])["run_app_checks"] is True
    assert ci_changes.classify(["sources/a.py"], "push")["run_app_checks"] is True
    assert pr()["run_app_checks"] is True


@pytest.mark.parametrize("path", [
    ".github/workflows/tests.yml", ".github/CODEOWNERS",
    "tools/ci_changes.py", "tests/conftest.py", "run_tests.py", "pytest.ini", "constraints.txt",
    "requirements.txt", "requirements-core.txt", "requirements-media.txt",
    "frontend/package.json", "frontend/package-lock.json",
    "frontend/playwright.config.ts", "frontend/vite.config.ts",
])
def test_ci_and_dependency_files_run_everything(path):
    assert pr(path) == ALL
    assert pr("frontend/src/a.ts", path) == ALL
    assert pr("docs/a.md", path) == ALL


@pytest.mark.parametrize("path", [
    "frontend/src/requirements.txt", "docs/requirements-notes.md", "tests/test_ci_changes.py",
    "frontend/tsconfig.json", "tools/repo_map.py",
])
def test_lookalike_paths_do_not_run_everything(path):
    assert pr(path) != ALL


def test_full_ci_label_runs_everything_even_for_notes():
    assert pr("docs/archive/research/reports/a.md", labels=["full-ci"]) == ALL
    assert pr("docs/a.md", labels=["bug", "full-ci"]) == ALL
    assert pr("docs/a.md", labels=["bug"])["run_full_pytest"] is False


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "schedule", "merge_group", ""])
def test_anything_but_a_pull_request_runs_everything(event):
    assert ci_changes.classify(["docs/archive/research/reports/a.md"], event) == ALL
    assert ci_changes.classify(["docs/a.md"], event) == ALL


def test_merge_group_runs_everything_even_for_a_notes_only_diff():
    # The queue's merged result is what lands, so no path filter may skip a job there.
    _, got = _run(["--event", "merge_group", "--labels-json", "[]"], "reports/a.md\n")
    assert got == ALL_OUT


def test_unreadable_diff_runs_everything():
    assert pr() == ALL
    assert ci_changes.classify(["", "  "], "pull_request") == ALL


def test_windows_separators_are_normalised():
    assert pr("frontend\\src\\a.ts")["run_full_pytest"] is False
    assert pr(".github\\workflows\\tests.yml") == ALL


@pytest.mark.parametrize("path", [
    "start.bat", "setup.bat", "uninstall.bat", "make_shortcut.bat", "deploy/run.bat", "start.ps1",
    "uninstall_path_cleanup.ps1", "check_setup.py", "installer/baihe.iss", "installer/service/x.py",
    "tools/windows_script_checks.py", "services/library_restore_service.py",
    "services/library_restore_sql.py", "db/library_folder.py",
])
def test_windows_script_files_run_only_the_windows_script_checks(path):
    assert pr(path)["run_windows_scripts"] is True
    assert pr("services/x.py", path)["run_windows_scripts"] is True
    assert pr(path)["run_e2e"] is False


@pytest.mark.parametrize("path", [
    "services/x.py", "docs/uninstall.md", "frontend/src/start.bat.ts", "installers/a.py", "tools/repo_map.py",
    "tests/test_start_bat.py",
])
def test_other_files_skip_the_windows_script_checks(path):
    assert pr(path)["run_windows_scripts"] is False


def test_windows_script_checks_run_for_ci_and_non_pr_events_and_the_full_ci_label():
    assert pr(".github/workflows/tests.yml")["run_windows_scripts"] is True
    assert pr("docs/a.md", labels=["full-ci"])["run_windows_scripts"] is True
    assert ci_changes.classify(["docs/a.md"], "push")["run_windows_scripts"] is True
    assert ci_changes.classify(["docs/a.md"], "merge_group")["run_windows_scripts"] is True
    assert pr()["run_windows_scripts"] is True


def test_cli_prints_the_windows_script_output():
    _, got = _run(["--event", "pull_request"], "uninstall.bat\n")
    assert got["run_windows_scripts"] == "true"
    _, got = _run(["--event", "pull_request"], "services/x.py\n")
    assert got["run_windows_scripts"] == "false"


@pytest.mark.parametrize("path", [
    "deploy/caddy/Caddyfile.template", "installer/caddy/go.mod", "installer/caddy/main.go",
    "installer/service_config.py", "tools/caddy_check.py",
])
def test_caddy_files_run_the_caddy_check(path):
    assert pr(path)["run_caddy"] is True
    assert pr("docs/a.md", path)["run_caddy"] is True


@pytest.mark.parametrize("path", [
    "deploy/run.bat", "installer/service.py", "docs/household-access.md", "tests/test_caddyfile_template.py",
    "services/x.py",
])
def test_other_files_skip_the_caddy_check(path):
    assert pr(path)["run_caddy"] is False


def test_caddy_check_runs_for_ci_and_non_pr_events_and_the_full_ci_label():
    assert pr(".github/workflows/tests.yml")["run_caddy"] is True
    assert pr("docs/a.md", labels=["full-ci"])["run_caddy"] is True
    assert ci_changes.classify(["docs/a.md"], "push")["run_caddy"] is True
    assert pr()["run_caddy"] is True


def _run(argv, text=""):
    out = io.StringIO()
    old, sys.stdout = sys.stdout, out
    try:
        code = ci_changes.main(argv, io.StringIO(text))
    finally:
        sys.stdout = old
    return code, dict(line.split("=") for line in out.getvalue().split())


def test_cli_prints_outputs_for_github_output():
    code, got = _run(["--event", "pull_request"], "services/x.py\n")
    assert code == 0
    assert got == {"notes_only": "false", "run_e2e": "false", "run_full_pytest": "true", "run_windows_scripts": "false",
                   "run_caddy": "false", "run_app_checks": "true"}


def test_cli_reads_labels_json():
    _, got = _run(["--event", "pull_request", "--labels-json", '["full-ci"]'], "docs/a.md\n")
    assert got == ALL_OUT


@pytest.mark.parametrize("labels", ["not json", '{"a": 1}', "[1, 2]"])
def test_cli_fails_open_on_a_bad_label_list(labels):
    _, got = _run(["--event", "pull_request", "--labels-json", labels], "docs/a.md\n")
    assert got == ALL_OUT


def test_cli_fails_open_when_classification_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(ci_changes, "classify", boom)
    code, got = _run(["--event", "pull_request"], "docs/a.md\n")
    assert code == 0
    assert got == ALL_OUT


def test_frontend_reading_tests_lists_files_that_mention_frontend(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("p = 'frontend/src'\n")
    (tmp_path / "tests" / "test_b.py").write_text("nothing\n")
    (tmp_path / "tests" / "helper.py").write_text("frontend\n")
    assert ci_changes.frontend_reading_tests(str(tmp_path)) == ["tests/test_a.py"]


def test_the_repos_frontend_reading_tests_include_the_drift_checks():
    found = ci_changes.frontend_reading_tests(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    assert "tests/test_frontend_limit_parity.py" in found
    assert "tests/test_api_static_frontend.py" in found
    assert all(os.path.exists(f) for f in found)
