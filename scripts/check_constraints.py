"""
scripts/check_constraints.py -- do requirements, constraints.txt and the installer lock agree?

Dependabot bumps the `>=` floors in requirements-*.txt but never edits
constraints.txt or installer/wheels.lock.txt, so those drift behind. This
check reads all three and reports, for each package:

  - a pin (`==`) in constraints.txt or the installer lock that is older than,
    or otherwise outside, what a requirements file now allows;
  - a constraint (cap or pin) that rules out every version a requirements
    file's floor allows, i.e. requirements and constraints disagree.

    python scripts/check_constraints.py

Exit code 0 when everything agrees, 1 with a list of what to bump by hand.
Reads local files only (no network). Needs `packaging`, which pytest brings.
"""

import re
import sys
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

REPO_ROOT = Path(__file__).resolve().parent.parent
REQUIREMENT_FILES = ("requirements-core.txt", "requirements-media.txt", "requirements-optional.txt")
LOCK_FILE = "installer/wheels.lock.txt"


def parse_requirements(path):
    """{canonical name: [(SpecifierSet, source line)]} from a pip-style file.

    Comment lines, blank lines, option lines (-r, -c) and hash continuation
    lines are skipped; an unparseable line is skipped too, since pip is the
    authority on syntax and would reject it on install.
    """
    out = {}
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip().rstrip("\\").strip()
        if not line or line.startswith("-"):
            continue
        try:
            req = Requirement(line)
        except InvalidRequirement:
            continue
        out.setdefault(canonicalize_name(req.name), []).append((req.specifier, line))
    return out


def _exact_version(spec):
    pins = [s.version for s in spec if s.operator == "=="]
    return Version(pins[0]) if len(pins) == 1 and len(spec) == 1 else None


def _lower_bounds(spec):
    return [Version(s.version) for s in spec if s.operator in (">=", "==", "~=") and "*" not in s.version]


def find_problems(requirements, constraints, lock):
    """Human-readable problems. `requirements` and `constraints` map name -> [(SpecifierSet, line)];
    `lock` maps name -> pinned Version."""
    problems = []
    for name, req_specs in sorted(requirements.items()):
        for r_spec, r_line in req_specs:
            lock_pin = lock.get(name)
            if lock_pin is not None and not r_spec.contains(lock_pin, prereleases=True):
                problems.append(f"{name}: {LOCK_FILE} has {lock_pin}, but requirements say `{r_line}`; "
                                f"regenerate the lock")
            for c_spec, c_line in constraints.get(name, []):
                pin = _exact_version(c_spec)
                if pin is not None and not r_spec.contains(pin, prereleases=True):
                    problems.append(f"{name}: constraints.txt pins {pin}, but requirements say "
                                    f"`{r_line}`; bump the pin (and the lock) to a version that satisfies it")
                    continue
                # An upper bound or pin is fine as long as some version both allow; the
                # candidates are the lower bounds on either side, which is where one would start.
                candidates = _lower_bounds(r_spec) + _lower_bounds(c_spec)
                if candidates and not any((r_spec & c_spec).contains(v, prereleases=True) for v in candidates):
                    problems.append(f"{name}: constraints.txt `{c_line}` and requirements `{r_line}` "
                                    f"allow no common version; loosen the constraint or the floor")
    return sorted(set(problems))


def load_lock(path):
    pins = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^([A-Za-z0-9_.\-]+)==([^\s\\]+)", line)
        if m:
            pins[canonicalize_name(m.group(1))] = Version(m.group(2))
    return pins


def check(root=REPO_ROOT):
    root = Path(root)
    requirements = {}
    for name in REQUIREMENT_FILES:
        for pkg, specs in parse_requirements(root / name).items():
            requirements.setdefault(pkg, []).extend(specs)
    constraints = parse_requirements(root / "constraints.txt")
    lock_path = root / LOCK_FILE
    lock = load_lock(lock_path) if lock_path.exists() else {}
    return find_problems(requirements, constraints, lock)


def main(root=REPO_ROOT):
    problems = check(root)
    if not problems:
        print("constraints.txt, the installer lock and the requirements files agree.")
        return 0
    print("These need a manual bump (Dependabot does not edit constraints.txt or the lock):")
    for p in problems:
        print(f"  - {p}")
    print("Procedure: docs/testing-and-ci.md, 'Bumping constraints and the installer lock'.")
    return 1


if __name__ == "__main__":
    sys.exit(main())
