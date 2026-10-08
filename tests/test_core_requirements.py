"""Everything `python -m api` imports at startup must be installable from
requirements-core.txt, directly or as a dependency of a package listed there."""
import json
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError, packages_distributions, requires

from pathlib import Path

PROJECT_ROOT = str(Path(__file__).resolve().parent.parent)


def _norm(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _core_requirement_names():
    names = set()
    with open(f"{PROJECT_ROOT}/requirements-core.txt", encoding="utf-8") as f:
        for line in f:
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", line.split("#")[0])
            if m:
                names.add(_norm(m.group(1)))
    return names


def _closure(roots):
    seen, todo = set(), list(roots)
    while todo:
        name = todo.pop()
        if name in seen:
            continue
        seen.add(name)
        try:
            reqs = requires(name) or []
        except PackageNotFoundError:
            continue
        for req in reqs:
            if "extra ==" in req:
                continue
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
            if m:
                todo.append(_norm(m.group(1)))
    return seen


def _optional_extras_of(declared):
    """Distributions that a declared package lists only as an extra, such as
    defusedxml for Pillow's XMP support. Those packages import them when they
    happen to be installed, so loading one on a machine that has it is not a
    missing core requirement."""
    names = set()
    for name in declared:
        try:
            reqs = requires(name) or []
        except PackageNotFoundError:
            continue
        for req in reqs:
            if "extra ==" not in req:
                continue
            m = re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", req)
            if m:
                names.add(_norm(m.group(1)))
    return names


def test_api_server_imports_are_declared_in_core_requirements():
    out = subprocess.run(
        [sys.executable, "-c",
         "import sys, json, api.server; print(json.dumps(sorted({m.split('.')[0] for m in sys.modules})))"],
        cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=120, check=True,
    ).stdout
    loaded = json.loads(out.strip().splitlines()[-1])
    dists = packages_distributions()
    declared = _closure(_core_requirement_names())
    optional = _optional_extras_of(declared)
    missing = {}
    for mod in loaded:
        if mod in sys.stdlib_module_names or mod.startswith("_") or mod not in dists:
            continue
        owners = {_norm(d) for d in dists[mod]}
        if not owners & (declared | optional):
            missing[mod] = sorted(owners)
    assert not missing, f"imported by api.server but not in requirements-core.txt: {missing}"
