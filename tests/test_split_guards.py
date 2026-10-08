"""
tests/test_split_guards.py -- guards that must exist before the large modules
are split. Several older guards name files (or read one file) and would keep
passing while checking nothing once a module becomes a package.
"""
import ast
import importlib
import os
import re
import sys
import warnings

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from tests.test_static_analysis import (  # noqa: E402
    PROJECT_ROOT,
    _find_requests_calls_missing_timeout,
)

_SKIP_DIRS = {"__pycache__", "node_modules", ".git", ".claude", "venv", ".venv", "tmp"}


def _rel(path):
    return os.path.relpath(path, PROJECT_ROOT).replace(os.sep, "/")


def _walk_files(top, suffixes, skip=_SKIP_DIRS):
    out = []
    for root, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if d not in skip and not d.startswith(".")]
        out += [os.path.join(root, f) for f in files if f.endswith(suffixes)]
    return sorted(out)


# --- 1. HTTP timeouts by folder -------------------------------------------

_TIMEOUT_FOLDERS = ("sources", "installer", "engine_backends", "scripts", "tools", "extension")


def _timeout_scan_files():
    # Root files only (not recursive): services/ and api/ have their own scan.
    files = [os.path.join(PROJECT_ROOT, f) for f in sorted(os.listdir(PROJECT_ROOT))
             if f.endswith(".py") and not f.startswith("test_")]
    for folder in _TIMEOUT_FOLDERS:
        files += [f for f in _walk_files(os.path.join(PROJECT_ROOT, folder), ".py")
                  if not os.path.basename(f).startswith("test_")]
    return files


class TestHttpTimeoutsByFolder:
    """The named-file timeout tests go blind when a module is split into a
    package; scanning whole folders keeps new files covered."""

    def test_scan_sees_the_expected_folders(self):
        seen = {_rel(f).split("/")[0] for f in _timeout_scan_files()}
        assert set(_TIMEOUT_FOLDERS) <= seen, seen
        assert len(_timeout_scan_files()) > 50, "the folder scan found too few files"

    def test_no_untimed_http_call_in_root_or_support_folders(self):
        problems = {}
        for f in _timeout_scan_files():
            bad = _find_requests_calls_missing_timeout(f, session_verbs=True)
            if bad:
                problems[_rel(f)] = bad
        assert problems == {}, f"call(s) missing timeout= at line(s): {problems}"


# --- 2. Frontend size guard -----------------------------------------------

MAX_FRONTEND_BYTES = 40 * 1024
# Exact byte sizes today. A listed file may shrink but never grow.
OVERSIZED_FRONTEND_BYTES = {
    "frontend/src/pages/workspace/stages/review/LinesPanel.tsx": 55746,
    "frontend/e2e/review-stage.spec.ts": 48405,
}


def _frontend_sizes():
    sizes = {}
    for top in ("frontend/src", "frontend/e2e"):
        for f in _walk_files(os.path.join(PROJECT_ROOT, top), (".ts", ".tsx")):
            if ".test." not in os.path.basename(f):
                sizes[_rel(f)] = os.path.getsize(f)
    return sizes


class TestFrontendSize:
    def test_scan_sees_frontend_sources(self):
        sizes = _frontend_sizes()
        assert any(p.startswith("frontend/src/") for p in sizes)
        assert any(p.startswith("frontend/e2e/") for p in sizes)

    def test_no_new_oversized_frontend_file(self):
        too_big = {p: s for p, s in _frontend_sizes().items()
                   if s > MAX_FRONTEND_BYTES and p not in OVERSIZED_FRONTEND_BYTES}
        assert too_big == {}, (
            f"Frontend files over {MAX_FRONTEND_BYTES} bytes: {too_big}. "
            "Split the file; do not add to the allowlist.")

    def test_allowlisted_frontend_files_never_grow(self):
        sizes = _frontend_sizes()
        grown = {p: (limit, sizes[p]) for p, limit in OVERSIZED_FRONTEND_BYTES.items()
                 if sizes.get(p, 0) > limit}
        assert grown == {}, f"allowlisted files grew past their recorded size (limit, now): {grown}"

    def test_stale_allowlist_entries_are_reported_not_failed(self):
        # A split PR must not need to edit the allowlist, so stale entries only
        # warn; the final ratchet PR removes them.
        sizes = _frontend_sizes()
        stale = sorted(p for p in OVERSIZED_FRONTEND_BYTES
                       if p not in sizes or sizes[p] <= MAX_FRONTEND_BYTES)
        if stale:
            warnings.warn(
                f"Remove these from OVERSIZED_FRONTEND_BYTES (split below the limit or deleted): {stale}")


# --- 3. Front-door names resolve ------------------------------------------

_FRONT_DOORS = ("db", "translate_engines", "api.schemas")
_PATCH_FUNCS = {"setattr", "patch", "object", "delattr"}


def _bound_aliases(tree, module):
    """Local names bound to `module` by import statements, and the names
    pulled out of it with `from module import ...`."""
    aliases, from_names = set(), set()
    head, _, tail = module.rpartition(".")
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                if a.name == module:
                    aliases.add(a.asname or a.name)
        elif isinstance(node, ast.ImportFrom) and node.level == 0:
            if node.module == module:
                from_names |= {a.name for a in node.names if a.name != "*"}
            elif head and node.module == head:
                aliases |= {a.asname or a.name for a in node.names if a.name == tail}
    return aliases, from_names


def _referenced_names(tree, module):
    aliases, names = _bound_aliases(tree, module)
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
                and node.value.id in aliases):
            names.add(node.attr)
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        fname = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if fname not in _PATCH_FUNCS or not node.args:
            continue
        first = node.args[0]
        # setattr(db, "x", ...) / patch.object(db, "x")
        if (isinstance(first, ast.Name) and first.id in aliases and len(node.args) > 1
                and isinstance(node.args[1], ast.Constant) and isinstance(node.args[1].value, str)):
            names.add(node.args[1].value)
        # monkeypatch.setattr("db.x", ...) / mock.patch("db.x")
        elif (isinstance(first, ast.Constant) and isinstance(first.value, str)
              and first.value.startswith(module + ".")):
            names.add(first.value[len(module) + 1:].split(".")[0])
    return names


def _all_py_files():
    return _walk_files(PROJECT_ROOT, ".py", skip=_SKIP_DIRS | {"frontend"})


def _missing_front_door_names(module):
    mod = importlib.import_module(module)
    missing, checked = {}, 0
    for f in _all_py_files():
        try:
            tree = ast.parse(open(f, encoding="utf-8").read(), f)
        except SyntaxError:
            continue
        for name in _referenced_names(tree, module):
            checked += 1
            if not hasattr(mod, name):
                try:
                    importlib.import_module(f"{module}.{name}")
                    continue  # a submodule of a package
                except ImportError:
                    pass
                missing.setdefault(name, []).append(_rel(f))
    # An import-style change that hides every reference must not pass silently.
    assert checked >= 20, f"the scan found only {checked} references to {module}"
    return missing


class TestFrontDoorNamesResolve:
    """A split moves functions into submodules; the front door must keep
    re-exporting every name that callers and monkeypatch targets use."""

    @pytest.mark.parametrize("module", _FRONT_DOORS)
    def test_every_referenced_name_exists(self, module):
        missing = _missing_front_door_names(module)
        assert missing == {}, f"{module} is missing names used elsewhere: {missing}"

    def test_scanner_finds_attribute_and_string_targets(self):
        src = ("import db\nfrom unittest import mock\n"
               "db.one()\nmonkeypatch.setattr(db, 'two', f)\n"
               "mock.patch('db.three.x')\nmock.patch.object(db, 'four')\n")
        assert _referenced_names(ast.parse(src), "db") == {"one", "two", "three", "four"}


# --- 4. db package rule ----------------------------------------------------

_DB_DIR = os.path.join(PROJECT_ROOT, "db")


class TestDbPackageRule:
    """Once db/ is a package, submodules must reach each other's public
    functions through the `db` front door so test patches on `db.<name>`
    still take effect; a direct `from db.x import f` would bypass them."""

    def test_submodules_use_the_front_door(self):
        if not os.path.isdir(_DB_DIR):
            pytest.skip("db is still a single file; the package rule applies after the split")
        init = os.path.join(_DB_DIR, "__init__.py")
        public = set()
        if os.path.exists(init):
            tree = ast.parse(open(init, encoding="utf-8").read())
            for node in tree.body:
                if isinstance(node, ast.ImportFrom):
                    public |= {a.asname or a.name for a in node.names if not a.name.startswith("_")}
        problems = []
        for f in _walk_files(_DB_DIR, ".py"):
            if os.path.basename(f) == "__init__.py":
                continue
            tree = ast.parse(open(f, encoding="utf-8").read(), f)
            own = {n.name for n in ast.walk(tree)
                   if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.level == 0 and \
                        (node.module or "").split(".")[0] == "db":
                    problems.append(f"{_rel(f)}:{node.lineno} from {node.module} import ...")
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) \
                        and node.func.id in public and node.func.id not in own:
                    problems.append(f"{_rel(f)}:{node.lineno} bare call {node.func.id}(); use db.{node.func.id}()")
        assert problems == [], "db/ submodules must call public functions as db.<name>:\n" + "\n".join(problems)
