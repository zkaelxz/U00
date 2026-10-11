"""tools/ci_changes.py: which CI jobs a change needs. A wrong "false" here lets a regression through,
so these pin each rule in the table in docs/testing-and-ci.md."""
import io
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tools"))

import ci_changes  # noqa: E402

ALL = {"notes_only": False, "run_e2e": True, "run_full_pytest": True}


def pr(*files, labels=()):
    return ci_changes.classify(list(files), "pull_request", labels)


@pytest.mark.parametrize("files", [
    ["reports/a.md"],
    ["research_notes/x/y.md", "docs/session-harvest/2026.md"],
])
def test_notes_only_changes_skip_everything_but_guards(files):
    assert pr(*files)["notes_only"] is True


@pytest.mark.parametrize("files", [
    ["reports/a.md", "services/x.py"],
    ["docs/testing-and-ci.md"],
    ["reportsx/a.md"],
])
def test_mixed_or_other_docs_are_not_notes_only(files):
    assert pr(*files)["notes_only"] is False


@pytest.mark.parametrize("files", [
    ["services/drama_service.py"],
    ["db.py", "tests/test_db.py"],
    ["sources/adapters/bilibili.py"],
    ["installer/build_installer.py", "scripts/x.py", "tools/repo_map.py"],
    ["docs/STATUS.md", "services/x.py"],
])
def test_backend_only_change_skips_e2e_but_runs_all_pytest(files):
    assert pr(*files) == {"notes_only": False, "run_e2e": False, "run_full_pytest": True}


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
    assert pr("frontend/src/a.ts") == {"notes_only": False, "run_e2e": True, "run_full_pytest": False}
    assert pr("docs/a.md") == {"notes_only": False, "run_e2e": False, "run_full_pytest": False}


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
    assert pr("reports/a.md", labels=["full-ci"]) == ALL
    assert pr("docs/a.md", labels=["bug", "full-ci"]) == ALL
    assert pr("docs/a.md", labels=["bug"])["run_full_pytest"] is False


@pytest.mark.parametrize("event", ["push", "workflow_dispatch", "schedule", ""])
def test_anything_but_a_pull_request_runs_everything(event):
    assert ci_changes.classify(["reports/a.md"], event) == ALL
    assert ci_changes.classify(["docs/a.md"], event) == ALL


def test_unreadable_diff_runs_everything():
    assert pr() == ALL
    assert ci_changes.classify(["", "  "], "pull_request") == ALL


def test_windows_separators_are_normalised():
    assert pr("frontend\\src\\a.ts")["run_full_pytest"] is False
    assert pr(".github\\workflows\\tests.yml") == ALL


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
    assert got == {"notes_only": "false", "run_e2e": "false", "run_full_pytest": "true"}


def test_cli_reads_labels_json():
    _, got = _run(["--event", "pull_request", "--labels-json", '["full-ci"]'], "docs/a.md\n")
    assert got == {"notes_only": "false", "run_e2e": "true", "run_full_pytest": "true"}


@pytest.mark.parametrize("labels", ["not json", '{"a": 1}', "[1, 2]"])
def test_cli_fails_open_on_a_bad_label_list(labels):
    _, got = _run(["--event", "pull_request", "--labels-json", labels], "docs/a.md\n")
    assert got == {"notes_only": "false", "run_e2e": "true", "run_full_pytest": "true"}


def test_cli_fails_open_when_classification_raises(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("boom")
    monkeypatch.setattr(ci_changes, "classify", boom)
    code, got = _run(["--event", "pull_request"], "docs/a.md\n")
    assert code == 0
    assert got == {"notes_only": "false", "run_e2e": "true", "run_full_pytest": "true"}


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
