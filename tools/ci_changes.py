"""Decides which CI jobs a change needs; used by the `changes` job in .github/workflows/tests.yml.

    python tools/ci_changes.py --event pull_request [--labels-json '["full-ci"]'] [--merge-approved false] < changed-files
    python tools/ci_changes.py --frontend-tests
    python tools/ci_changes.py --docs-tests
    python tools/ci_changes.py --sources-tests

Prints `name=true|false` lines for $GITHUB_OUTPUT. The rules are tabled in docs/testing-and-ci.md.
Any error prints everything as true: a broken classifier may cost minutes, never skip a job.
"""
import argparse
import fnmatch
import json
import os
import sys

FULL_CI_LABEL = "full-ci"
# Changes to CI itself, test plumbing or dependencies can break any job, so they run all of them.
EVERYTHING_PREFIXES = (".github/",)
EVERYTHING_FILES = frozenset({
    "tools/ci_changes.py", "tools/ci_tested_tree.py", "tests/conftest.py", "run_tests.py", "constraints.txt", "pytest.ini",
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
                                  "db/library_folder.py", "tests/test_windows_real.py", "process_guard.py",
                                  "lib/proc_kill.py", "lib/file_lock.py"})
WINDOWS_SCRIPT_PATTERNS = ("*.bat", "*.ps1")
# The `caddy` job builds the bundled Caddy and serves the rendered household Caddyfile, so it needs
# whatever changes that file, how it is rendered, or the Caddy build.
CADDY_PREFIXES = ("deploy/caddy/", "installer/caddy/")
CADDY_FILES = frozenset({"installer/service_config.py", "tools/caddy_check.py"})
# The real-app smoke test and the frontend build neither import nor serve anything a site adapter
# changes beyond what the pytest shards already cover, so a diff confined to these skips them.
APP_CHECK_FREE_PREFIXES = ("sources/", "tests/")
# A diff confined to site adapters and their own tests runs this subset in one shard instead of the
# whole suite: the adapter and API-sources tests plus the tests of rules every adapter must obey
# (redaction, SSRF, domains, install payload, feature switches, page serving, db and library isolation).
# `guards` still runs on every PR.
SOURCES_TEST_GLOBS = ("test_sources_*.py", "test_api_sources_*.py")
SOURCES_TEST_FILES = (
    "test_secret_redaction.py", "test_b25_redirect_ssrf.py", "test_b28_browser_tier_ssrf.py",
    "test_source_domains.py", "test_feature_switches.py", "test_adaptive_extraction.py",
    "test_installer_service.py", "test_installer_remote.py", "test_installer_payload.py", "test_page_server.py",
    "test_db.py", "test_db_lines.py", "test_db_series.py", "test_db_connections.py", "test_db_library_file.py",
    "test_library_isolation_guard.py", "test_ci_changes.py",
)
# What the browser tests can exercise through the running app; a backend-only change used to skip them
# and show a broken screen only in the push run after the merge.
BROWSER_PREFIXES = ("frontend/", "api/", "services/", "db/")
OUTPUTS = ("run_e2e", "run_full_pytest", "run_windows_scripts", "run_caddy", "run_app_checks",
           "run_sources_subset", "e2e_deferred")


def _is_sources_only_file(path):
    name = path.rsplit("/", 1)[-1]
    if path.startswith("sources/adapters/"):
        return True
    return path.startswith("tests/") and path.count("/") == 1 and any(
        fnmatch.fnmatchcase(name, g) for g in SOURCES_TEST_GLOBS)


def _starts(path, prefixes):
    return path.startswith(prefixes)


def _reaches_browser(path):
    """The Playwright specs drive the real API, which runs services and db. The restore code is
    left out: the Windows script checks run it, and no spec does."""
    return _starts(path, BROWSER_PREFIXES) and path not in WINDOWS_SCRIPT_FILES


def _forces_everything(path):
    return (_starts(path, EVERYTHING_PREFIXES) or path in EVERYTHING_FILES
            or any(fnmatch.fnmatchcase(path, p) for p in EVERYTHING_PATTERNS))


def _touches_windows_scripts(path):
    return (_starts(path, WINDOWS_SCRIPT_PREFIXES) or path in WINDOWS_SCRIPT_FILES
            or any(fnmatch.fnmatchcase(path, p) for p in WINDOWS_SCRIPT_PATTERNS))


def classify(files, event, labels=(), merge_approved=True):
    """`merge_approved` is false for a pull request without auto-merge turned on. Its browser tests
    then wait (`e2e_deferred`), and the required `browser-tests` status stays pending until a run after
    the approval has run them:
    the browser-test jobs are the largest share of a run's runner time, and a PR is often pushed several
    times before anyone asks for it to merge."""
    files = [f.strip().replace("\\", "/") for f in files if f.strip()]
    everything = _all_true()
    # No file list on a pull request means the diff could not be read, not that nothing changed.
    if event != "pull_request" or not files:
        return everything
    if FULL_CI_LABEL in labels:
        return everything
    result = everything if any(_forces_everything(f) for f in files) else _by_rule(files)
    if result["run_e2e"] and not merge_approved:
        result["run_e2e"] = False
        result["e2e_deferred"] = True
    return result


def _by_rule(files):
    return {
        "run_e2e": any(_reaches_browser(f) for f in files),
        "run_full_pytest": not all(_starts(f, ("frontend/", "docs/")) for f in files),
        "run_windows_scripts": any(_touches_windows_scripts(f) for f in files),
        "run_caddy": any(_starts(f, CADDY_PREFIXES) or f in CADDY_FILES for f in files),
        "run_app_checks": not all(_starts(f, APP_CHECK_FREE_PREFIXES) for f in files),
        "run_sources_subset": all(_is_sources_only_file(f) for f in files),
        "e2e_deferred": False,
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


def docs_reading_tests(root):
    """Tests that read files under docs/ (route and settings tables, the agent docs): a docs-only
    change must still run them, since the table they check is what it edited."""
    tests_dir = os.path.join(root, "tests")
    found = []
    for name in sorted(os.listdir(tests_dir)):
        if not (name.startswith("test_") and name.endswith(".py")):
            continue
        with open(os.path.join(tests_dir, name), encoding="utf-8") as f:
            text = f.read()
        if "docs/" in text or '"docs"' in text or "'docs'" in text:
            found.append(f"tests/{name}")
    return found


def sources_tests(root):
    tests_dir = os.path.join(root, "tests")
    found = {n for g in SOURCES_TEST_GLOBS for n in fnmatch.filter(os.listdir(tests_dir), g)}
    found.update(n for n in SOURCES_TEST_FILES if os.path.exists(os.path.join(tests_dir, n)))
    return [f"tests/{n}" for n in sorted(found)]


def _all_true():
    return {"run_e2e": True, "run_full_pytest": True, "run_windows_scripts": True,
            "run_caddy": True, "run_app_checks": True, "run_sources_subset": False, "e2e_deferred": False}


def main(argv=None, stdin=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--event", default="")
    parser.add_argument("--labels-json", default="[]")
    # Anything but "false" counts as approved: a missing or garbled value runs the browser tests.
    parser.add_argument("--merge-approved", default="true")
    parser.add_argument("--frontend-tests", action="store_true")
    parser.add_argument("--docs-tests", action="store_true")
    parser.add_argument("--sources-tests", action="store_true")
    args = parser.parse_args(argv)
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    if args.frontend_tests:
        print(" ".join(frontend_reading_tests(root)))
        return 0
    if args.docs_tests:
        print(" ".join(docs_reading_tests(root)))
        return 0
    if args.sources_tests:
        print(" ".join(sources_tests(root)))
        return 0
    try:
        labels = json.loads(args.labels_json or "[]")
        if not (isinstance(labels, list) and all(isinstance(x, str) for x in labels)):
            raise ValueError("labels must be a JSON list of strings")
        result = classify((stdin or sys.stdin).read().splitlines(), args.event, labels,
                          merge_approved=args.merge_approved != "false")
    except Exception as exc:
        print(f"ci_changes: {exc!r}; running everything", file=sys.stderr)
        result = _all_true()
    for name in OUTPUTS:
        print(f"{name}={str(result[name]).lower()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
