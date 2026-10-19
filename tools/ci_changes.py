"""Decides which CI jobs a change needs; used by the `changes` job in .github/workflows/tests.yml.

    python tools/ci_changes.py --event pull_request [--labels-json '["full-ci"]'] < changed-files
    python tools/ci_changes.py --frontend-tests

Prints `name=true|false` lines for $GITHUB_OUTPUT. The rules are tabled in docs/testing-and-ci.md.
Any error prints everything as true: a broken classifier may cost minutes, never skip a job.
"""
import argparse
import fnmatch
import json
import os
import sys

NOTES_PREFIXES = ("docs/archive/research/", "docs/archive/session-harvest/")
FULL_CI_LABEL = "full-ci"
# Changes to CI itself, test plumbing or dependencies can break any job, so they run all of them.
EVERYTHING_PREFIXES = (".github/",)
EVERYTHING_FILES = frozenset({
    "tools/ci_changes.py", "tests/conftest.py", "run_tests.py", "constraints.txt", "pytest.ini",
    "frontend/package.json", "frontend/package-lock.json",
    "frontend/playwright.config.ts", "frontend/vite.config.ts",
})
EVERYTHING_PATTERNS = ("requirements*.txt",)
# The Windows script checks run uninstall.bat, setup.bat and start.bat, and a library restore round
# trip (its file locking, fsync and renames differ on Windows), so they need these (and the `.github/`
# and requirements files above, which already run everything).
WINDOWS_SCRIPT_PREFIXES = ("installer/",)
WINDOWS_SCRIPT_FILES = frozenset({"check_setup.py", "tools/windows_script_checks.py",
                                  "services/library_restore_service.py", "services/library_restore_sql.py",
                                  "db/library_folder.py"})
WINDOWS_SCRIPT_PATTERNS = ("*.bat", "*.ps1")
OUTPUTS = ("notes_only", "run_e2e", "run_full_pytest", "run_windows_scripts")


def _starts(path, prefixes):
    return path.startswith(prefixes)


def _forces_everything(path):
    return (_starts(path, EVERYTHING_PREFIXES) or path in EVERYTHING_FILES
            or any(fnmatch.fnmatchcase(path, p) for p in EVERYTHING_PATTERNS))


def _touches_windows_scripts(path):
    return (_starts(path, WINDOWS_SCRIPT_PREFIXES) or path in WINDOWS_SCRIPT_FILES
            or any(fnmatch.fnmatchcase(path, p) for p in WINDOWS_SCRIPT_PATTERNS))


def classify(files, event, labels=()):
    files = [f.strip().replace("\\", "/") for f in files if f.strip()]
    everything = _all_true()
    # No file list on a pull request means the diff could not be read, not that nothing changed.
    if event != "pull_request" or not files:
        return everything
    if FULL_CI_LABEL in labels or any(_forces_everything(f) for f in files):
        return everything
    return {
        "notes_only": all(_starts(f, NOTES_PREFIXES) for f in files),
        "run_e2e": any(_starts(f, ("frontend/", "api/")) for f in files),
        "run_full_pytest": not all(_starts(f, ("frontend/", "docs/")) for f in files),
        "run_windows_scripts": any(_touches_windows_scripts(f) for f in files),
    }


def frontend_reading_tests(root):
    """Tests that name `frontend` anywhere: they check backend code against frontend files, so
    a frontend-only change must still run them to catch drift between the two."""
    tests_dir = os.path.join(root, "tests")
    found = []
    for name in sorted(os.listdir(tests_dir)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        with open(os.path.join(tests_dir, name), encoding="utf-8") as f:
            if "frontend" in f.read():
                found.append(f"tests/{name}")
    return found


def _all_true():
    return {"notes_only": False, "run_e2e": True, "run_full_pytest": True, "run_windows_scripts": True}


def main(argv=None, stdin=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", default="")
    parser.add_argument("--labels-json", default="[]")
    parser.add_argument("--frontend-tests", action="store_true")
    args = parser.parse_args(argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args.frontend_tests:
        print(" ".join(frontend_reading_tests(root)))
        return 0
    try:
        labels = json.loads(args.labels_json or "[]")
        if not (isinstance(labels, list) and all(isinstance(x, str) for x in labels)):
            raise ValueError("labels must be a JSON list of strings")
        result = classify((stdin or sys.stdin).read().splitlines(), args.event, labels)
    except Exception as exc:
        print(f"ci_changes: {exc!r}; running everything", file=sys.stderr)
        result = _all_true()
    for name in OUTPUTS:
        print(f"{name}={str(result[name]).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
