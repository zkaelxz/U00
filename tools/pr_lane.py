"""Prints which merge lane a branch takes, `clear` or `overlap` with the reason (docs/testing-and-ci.md, "Merge flow").

    python tools/pr_lane.py                    # HEAD against origin/baihe-subtitler, fetched first
    python tools/pr_lane.py --head origin/some-branch
    python tools/pr_lane.py --no-fetch         # the base as this clone already has it

Exit status 0 is clear, 1 is overlap and 2 is "could not decide"; the first line then still says `overlap`, because a
failure must never clear a PR. The lane is one half of the clear-lane test: the full green run at the exact head is
still read from GitHub. Stdlib only.
"""
import argparse
import ast
import fnmatch
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath

import ci_changes

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_BASE = "origin/baihe-subtitler"
GIT_TIMEOUT = 120
LISTED = 12  # paths named on one line of the report; the rest are counted

# Nearly every change a person can see edits one of these, and no test reads their text except the one that checks
# the port number in two of them (it runs on the base after every merge), so sharing one does not send a PR to the
# overlap lane. Two PRs that change the same lines still conflict, and GitHub refuses that merge.
USER_GUIDE_PATTERN = "user-guide*.md"

# A database schema change is any diff that adds or edits a table statement or a migration list. These files hold
# nothing else, so any change to them counts; in any other Python file only the changed lines are read.
SCHEMA_FILES = frozenset({"db/migrations.py", "db/schema.py", "db/schema_system.py", "sources/store.py"})
INIT_DB_FILE = "db/__init__.py"  # also holds code that is no schema, so only edits to init_db count
TABLE_STATEMENT = re.compile(
    r"\b(?:CREATE|ALTER|DROP)\s+(?:\w+\s+)?(?:TABLE|INDEX|VIEW|TRIGGER)\b|\bADD\s+COLUMN\b|\b_safe_alter\b",
    re.IGNORECASE)

# Read by name wherever they sit: a manifest or lock file missed here would let a dependency change take the
# clear lane, while a file caught by mistake only costs a new run.
DEPENDENCY_NAMES = frozenset({"package.json", "package-lock.json", "go.mod", "go.sum", "pyproject.toml", "Pipfile",
                              "Pipfile.lock", "yarn.lock", "pnpm-lock.yaml", "poetry.lock", "libs.versions.toml"})
DEPENDENCY_PATTERNS = ("*.lock", "*.lock.txt", "requirements*.txt", "constraints*.txt")


class LaneError(Exception):
    """The lane could not be read from this clone; the report says overlap and why."""


@dataclass
class Verdict:
    merge_base: str
    base_tip: str
    pr_files: int
    base_files: int
    always: dict = field(default_factory=dict)  # path -> why it always takes the overlap lane
    shared: list = field(default_factory=list)  # changed on both sides since the last base merge
    exempt: list = field(default_factory=list)  # shared, but not counted (user guide files)

    @property
    def clear(self):
        return not self.always and not self.shared

    def lines(self):
        out = ["clear" if self.clear else "overlap",
               f"  The branch last merged the base at {self.merge_base[:7]}. The base is now at {self.base_tip[:7]}: "
               f"{self.base_files} files changed there since, {self.pr_files} on the branch."]
        if self.always:
            out.append("  Always the overlap lane: " + _listing(f"{p} ({why})" for p, why in sorted(self.always.items())))
        if self.shared:
            out.append("  Shared with what the base merged since: " + _listing(self.shared))
        if self.exempt:
            out.append("  Not counted as shared (user guide files): " + _listing(self.exempt))
        return out


def _listing(items):
    items = list(items)
    shown = ", ".join(items[:LISTED])
    return shown + (f", and {len(items) - LISTED} more" if len(items) > LISTED else "")


def _git(root, *args):
    try:
        return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, encoding="utf-8",
                              errors="replace", timeout=GIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        raise LaneError(f"git {' '.join(args)} timed out") from None


def _out(root, *args):
    done = _git(root, *args)
    if done.returncode:
        raise LaneError(f"git {' '.join(args)} failed: {done.stderr.strip()[:200]}")
    return done.stdout


def _names(root, old, new):
    # --no-renames: a rename counts as the old path and the new one, as for a human reading the lists.
    return {n for n in _out(root, "diff", "--name-only", "--no-renames", "-z", old, new).split("\0") if n}


def _fetch(root, base):
    remote, _, branch = base.partition("/")
    if branch:  # a local branch name has nothing to fetch
        _out(root, "fetch", remote, branch)


def _merge_base(root, base, head):
    done = _git(root, "merge-base", "--all", base, head)
    if done.returncode > 1:  # 1 only means no common commit
        raise LaneError(f"git merge-base failed: {done.stderr.strip()[:200]}")
    found = done.stdout.split()
    if not found:
        raise LaneError(f"{head} and {base} share no commit in this clone; deepen it "
                        "(`git fetch --deepen=100 origin baihe-subtitler`) until `git merge-base` prints one")
    if len(found) > 1:
        raise LaneError("the branch and the base have more than one merge base, so the last base commit "
                        "the branch merged is not defined")
    return found[0]


def _is_user_guide(path):
    p = PurePosixPath(path)
    return p.parent == PurePosixPath("docs") and fnmatch.fnmatchcase(p.name, USER_GUIDE_PATTERN)


def path_kind(path):
    """Why this path alone sends a PR to the overlap lane (Kae's guard, 2026-11-14), or None."""
    name = path.rsplit("/", 1)[-1]
    if (path.startswith(ci_changes.EVERYTHING_PREFIXES) or path in ci_changes.EVERYTHING_FILES
            or any(fnmatch.fnmatchcase(path, p) for p in ci_changes.EVERYTHING_PATTERNS)):
        return "CI file"
    if (path.startswith(("api/schemas/", "frontend/src/api/generated/"))
            or (path.startswith("api/") and path.count("/") == 1 and name.endswith("_schemas.py"))):
        return "API model"
    if (name in DEPENDENCY_NAMES or any(fnmatch.fnmatchcase(name, p) for p in DEPENDENCY_PATTERNS)
            or (path.startswith("android/") and (name.endswith((".gradle", ".gradle.kts")) or name.startswith("gradle")))):
        return "dependency file"
    if path in SCHEMA_FILES:
        return "database schema"
    return None


def _touches_init_db(root, rev, patch):
    try:
        tree = ast.parse(_out(root, "show", f"{rev}:{INIT_DB_FILE}"))
    except (LaneError, SyntaxError):
        return True  # an edit that cannot be placed is assumed to count
    span = next(((n.lineno, n.end_lineno) for n in ast.walk(tree)
                 if isinstance(n, ast.FunctionDef) and n.name == "init_db"), None)
    if span is None:
        return True
    for hunk in re.finditer(r"^@@ -\S+ \+(\d+)(?:,(\d+))? @@", patch, re.MULTILINE):
        start, count = int(hunk.group(1)), int(hunk.group(2) or 1)
        low, high = (start, start + count - 1) if count else (start, start + 1)  # a deletion sits between two lines
        if low <= span[1] and high >= span[0]:
            return True
    return False


def edits_schema(root, old, new, path):
    """True when the diff of a Python file outside tests/ changes a table statement or an init_db step."""
    if not path.endswith(".py") or path.startswith("tests/"):
        return False
    patch = _out(root, "diff", "-U0", "--no-color", "--no-renames", old, new, "--", path)
    changed = [line[1:] for line in patch.splitlines()
               if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))]
    if any(TABLE_STATEMENT.search(line) for line in changed):
        return True
    return path == INIT_DB_FILE and _touches_init_db(root, new, patch)


def decide(root, base, head):
    merge_base = _merge_base(root, base, head)
    pr_files = _names(root, merge_base, head)
    base_files = _names(root, merge_base, base)
    always = {}
    for path in sorted(pr_files):
        why = path_kind(path) or ("database schema" if edits_schema(root, merge_base, head, path) else None)
        if why:
            always[path] = why
    both = sorted(pr_files & base_files)
    return Verdict(merge_base=merge_base, base_tip=_out(root, "rev-parse", base).strip(), pr_files=len(pr_files),
                   base_files=len(base_files), always=always,
                   shared=[p for p in both if not _is_user_guide(p)], exempt=[p for p in both if _is_user_guide(p)])


def main(argv=None, root=ROOT):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--base", default=DEFAULT_BASE, help="the base branch as this clone names it")
    parser.add_argument("--head", default="HEAD", help="the PR's head")
    parser.add_argument("--no-fetch", action="store_true", help="use the base as this clone already has it")
    args = parser.parse_args(argv)
    try:
        if not args.no_fetch:
            _fetch(root, args.base)
        verdict = decide(root, args.base, args.head)
    except (LaneError, OSError) as e:
        print("overlap")
        print(f"  Could not decide, so treat it as the overlap lane: {e}")
        return 2
    print("\n".join(verdict.lines()))
    return 0 if verdict.clear else 1


if __name__ == "__main__":
    sys.exit(main())
