"""Print (or run) the checks that matter for the files you changed, in under 2 KB.

    python tools/check_change.py              # changed files vs origin/baihe-subtitler -> lean pytest command
    python tools/check_change.py --run        # run it (pytest, then the frontend commands), stop at the first failure
    python tools/check_change.py --base HEAD  # only uncommitted work
    python tools/check_change.py --max 20     # more than the default 12 mapped tests

A changed file maps to tests by name (tests/test_<stem>*.py) and by imports
(an AST scan, plus dotted module names written as strings, which is how tests
patch). A shared module such as core or db maps to well over a hundred test
files, so the list is capped, name matches first, then the cheapest by the
tracked .test_durations; the output says so. The guards CI runs are always added.
Stdlib only.
"""
import argparse
import ast
import json
import re
import subprocess
import sys
import warnings
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = "origin/baihe-subtitler"
DEFAULT_MAX = 12
# The quick guards plus the ones CI runs that the short list misses; test_diagnostics_and_export
# is the only test of expected_files.py, and test_db pins the migrated-column list.
GUARDS = (
    "tests/test_static_analysis.py", "tests/test_api_permissions.py", "tests/test_split_guards.py",
    "tests/test_file_organization.py", "tests/test_agent_docs.py", "tests/test_settings_schema.py",
    "tests/test_settings_parity.py", "tests/test_frontend_limit_parity.py", "tests/test_db.py",
    "tests/test_diagnostics_and_export.py",
)
# -W ignore::<Category> only: a blanket -W ignore or -p no:warnings would also turn off
# pytest.ini's error::PytestUnhandledThreadExceptionWarning, and a crashing thread would pass.
LEAN_PYTEST = ("python -m pytest -q -o addopts= -W ignore::DeprecationWarning -W ignore::SyntaxWarning "
               "--no-header -x --tb=short")


def _git(*args, base_ok=False):
    done = subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if done.returncode and not base_ok:
        sys.exit(f"git {' '.join(args)} failed: {done.stderr.strip()[:200]}")
    return done.stdout.split("\n") if not done.returncode else None


def changed_files(base):
    """Paths changed since the merge-base with `base`, uncommitted work and untracked files included."""
    anchor = _git("merge-base", base, "HEAD", base_ok=True)
    note = ""
    if anchor and anchor[0]:
        ref = anchor[0]
    else:
        ref, note = "HEAD", f"base {base} not found, using HEAD"
    names = set(_git("diff", "--name-only", ref)) | set(_git("ls-files", "--others", "--exclude-standard"))
    return sorted(n for n in names if n and (ROOT / n).exists()), note


def module_name(rel):
    stem = rel[:-3]
    return stem[:-len("/__init__")].replace("/", ".") if stem.endswith("/__init__") else stem.replace("/", ".")


_DOTTED = re.compile(r"^[A-Za-z_]\w*(\.[A-Za-z_]\w*)+$")


def test_references(path):
    """Dotted module names a test file imports, or patches by string."""
    refs = set()
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")  # old regex literals warn on parse
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except (SyntaxError, ValueError):
        return refs
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            refs.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            refs.add(node.module)
            refs.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and _DOTTED.match(node.value):
            refs.add(node.value)
    return refs


def file_costs():
    try:
        raw = json.loads((ROOT / ".test_durations").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    costs = {}
    for test_id, seconds in raw.items():
        costs[test_id.split("::", 1)[0]] = costs.get(test_id.split("::", 1)[0], 0) + seconds
    return costs


def name_candidates(rel):
    stem = Path(rel).stem
    return {stem, re.sub(r"_(service|routes)$", "", stem)} - {"__init__"}


def map_tests(changed):
    """(name_matches, import_matches) as sets of test paths."""
    tests = sorted(p for p in (ROOT / "tests").glob("test_*.py"))
    py = [c for c in changed if c.endswith(".py") and not c.startswith(("tests/", "frontend/"))]
    by_name, by_import = set(), set()
    modules = {module_name(c) for c in py}
    for test in tests:
        rel = test.relative_to(ROOT).as_posix()
        base = test.stem[len("test_"):]
        if any(base.startswith(c) or base.startswith("api_" + c) for rel_py in py for c in name_candidates(rel_py)):
            by_name.add(rel)
            continue
        if modules and any(m == r or r.startswith(m + ".") for r in test_references(test) for m in modules):
            by_import.add(rel)
    return by_name, by_import


def plan(changed, limit):
    costs = file_costs()
    average = sum(costs.values()) / len(costs) if costs else 1.0
    cost = lambda t: costs.get(t, average)  # noqa: E731
    direct = {c for c in changed if c.startswith("tests/") and c.endswith(".py") and Path(c).name.startswith("test_")}
    by_name, by_import = map_tests(changed)
    ordered = sorted(direct | by_name, key=cost) + sorted(by_import - direct - by_name, key=cost)
    ordered = [t for t in ordered if t not in GUARDS]
    kept = ordered[:max(limit, len(direct))]
    return kept, len(ordered)


def frontend_steps(changed):
    web = [c for c in changed if c.startswith("frontend/") and not c.startswith("frontend/e2e/")]
    if not web:
        return []
    rel = [c[len("frontend/"):] for c in web]
    tests = [r for r in rel if re.search(r"\.test\.tsx?$", r)]
    steps = ["cd frontend && npx tsc -b"]
    steps.append("cd frontend && npx vitest run " + " ".join(tests) if tests
                 else "cd frontend && npx vitest related --run " + " ".join(rel[:6]))
    return steps


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--base", default=DEFAULT_BASE)
    parser.add_argument("--max", type=int, default=DEFAULT_MAX, help="max mapped test files (guards not counted)")
    parser.add_argument("--run", action="store_true", help="run the commands instead of printing them")
    args = parser.parse_args(argv)
    changed, note = changed_files(args.base)
    kept, total = plan(changed, args.max)
    pytest_cmd = f"{LEAN_PYTEST} {' '.join(kept + list(GUARDS))}"
    steps = [pytest_cmd] + frontend_steps(changed)
    if args.run:
        for step in steps:
            print("$", step[:200], flush=True)
            if subprocess.run(step, shell=True, cwd=ROOT).returncode:
                return 1
        return 0
    print(f"changed: {len(changed)} files vs {args.base}" + (f" ({note})" if note else ""))
    if total > len(kept):
        print(f"mapped tests: {total}, showing the {len(kept)} that match by name first, then cheapest "
              f"(shared module; --max N for more)")
    elif not kept:
        print("no test file maps to the change; the guards below still apply")
    print("\n" + "\n\n".join(steps))
    web = [c[len("frontend/"):] for c in changed if re.search(r"^frontend/src/.*\.(ts|tsx)$", c)]
    if web:
        print("\nlint one file at a time (bare `npx oxlint` prints ~32 KB): cd frontend && npx oxlint " + web[0])
    return 0


if __name__ == "__main__":
    sys.exit(main())
