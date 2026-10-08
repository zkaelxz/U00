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
    if module == "db":
        # The fixture yields the db module itself, so attribute reads and
        # patch targets through it must resolve like `db.<name>`.
        aliases.add("isolated_db")
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
    # `d = isolated_db` style aliases; repeat until no new name is found.
    grew = True
    while grew:
        grew = False
        for node in ast.walk(tree):
            if (isinstance(node, ast.Assign) and isinstance(node.value, ast.Name)
                    and node.value.id in aliases):
                for t in node.targets:
                    if isinstance(t, ast.Name) and t.id not in aliases:
                        aliases.add(t.id)
                        grew = True
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

    def test_scanner_treats_the_isolated_db_fixture_as_db(self):
        src = ("def test_x(isolated_db, monkeypatch):\n"
               "    isolated_db.one()\n"
               "    d = isolated_db\n"
               "    d.two()\n"
               "    monkeypatch.setattr(isolated_db, 'three', f)\n"
               "    mock.patch.object(d, 'four')\n")
        assert _referenced_names(ast.parse(src), "db") == {"one", "two", "three", "four"}

    def test_fixture_alias_is_specific_to_db(self):
        src = "def test_x(isolated_db):\n    isolated_db.one()\n"
        assert _referenced_names(ast.parse(src), "translate_engines") == set()

    def test_unknown_name_through_the_fixture_is_reported(self, tmp_path, monkeypatch):
        f = tmp_path / "t.py"
        real = [n for n in dir(importlib.import_module("db")) if not n.startswith("_")][:25]
        body = "".join(f"    isolated_db.{n}\n" for n in real) + "    isolated_db.no_such_db_name_xyz()\n"
        f.write_text("def test_x(isolated_db):\n" + body)
        here = sys.modules[__name__]
        monkeypatch.setattr(here, "_all_py_files", lambda: [str(f)])
        assert _missing_front_door_names("db") == {"no_such_db_name_xyz": [_rel(str(f))]}


# --- 4. db package rule ----------------------------------------------------

_DB_DIR = os.path.join(PROJECT_ROOT, "db")
_DB_PATH_GLOBALS = ("LIBRARY_DIR", "DRAMAS_DIR", "DB_PATH", "BENCHMARK_DIR", "VOICE_BANK_DIR")
# Mutable state configure_library_dir rebinds; a submodule copy would go stale.
_DB_SHARED_STATE = _DB_PATH_GLOBALS + ("_open_connections", "_media_lock", "_path_override")


def _reexported_names(init_path):
    """Public names `db/__init__` pulls in from its submodules."""
    if not os.path.exists(init_path):
        return set()
    tree = ast.parse(open(init_path, encoding="utf-8").read())
    names = set()
    for node in tree.body:
        if isinstance(node, ast.ImportFrom) and (
                node.level > 0 or (node.module or "").split(".")[0] == "db"):
            names |= {a.asname or a.name for a in node.names
                      if a.name != "*" and not (a.asname or a.name).startswith("_")}
    return names


def _db_package_problems(db_dir):
    """Rule violations in the submodules of a db package (everything but
    `__init__`), as "path:line message" strings."""
    public = _reexported_names(os.path.join(db_dir, "__init__.py"))
    problems = []
    for f in _walk_files(db_dir, ".py"):
        if os.path.basename(f) == "__init__.py":
            continue
        rel = os.path.relpath(f, os.path.dirname(db_dir)).replace(os.sep, "/")
        tree = ast.parse(open(f, encoding="utf-8").read(), f)
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                if node.level > 0:
                    problems.append(f"{rel}:{node.lineno} relative import; use `import db` and db.<name>")
                elif (node.module or "").split(".")[0] == "db":
                    problems.append(f"{rel}:{node.lineno} from {node.module} import ...")
                    for a in node.names:
                        if a.name in _DB_PATH_GLOBALS:
                            problems.append(f"{rel}:{node.lineno} imports path global {a.name}; read db.{a.name}")
            elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Load):
                # Same-module bare use bypasses a test patch on db.<name> too.
                if node.id in public:
                    problems.append(f"{rel}:{node.lineno} bare use of {node.id}; use db.{node.id}")
                elif node.id in _DB_PATH_GLOBALS:
                    problems.append(f"{rel}:{node.lineno} bare read of {node.id}; use db.{node.id}")
    return problems


def _shared_state_copies(modules):
    return [f"{m.__name__}.{n}" for m in modules for n in _DB_SHARED_STATE if n in vars(m)]


class TestDbPackageRule:
    """Once db/ is a package, submodules must reach each other's public
    functions through the `db` front door so test patches on `db.<name>`
    still take effect; a direct import or bare call would bypass them."""

    def test_submodules_use_the_front_door(self):
        if not os.path.isdir(_DB_DIR):
            pytest.skip("db is still a single file; the package rule applies after the split")
        problems = _db_package_problems(_DB_DIR)
        assert problems == [], "db/ submodules must use the db front door:\n" + "\n".join(problems)

    @staticmethod
    def _make_pkg(tmp_path, init, mod):
        pkg = tmp_path / "db"
        pkg.mkdir()
        (pkg / "__init__.py").write_text(init)
        (pkg / "mod.py").write_text(mod)
        return str(pkg)

    @pytest.mark.parametrize("line", [
        "from . import x",
        "from .other import x",
        "from .. import x",
        "from db.other import x",
        "from db import LIBRARY_DIR",
        "def g():\n    fsync_dir('p')",
        "def g():\n    return MAX_SNAPSHOT_WORD_BYTES",
        "def g():\n    update_drama(1)",
        "def g():\n    return DB_PATH",
        "def g():\n    return get_app_setting('k')",
    ])
    def test_rule_fires_on_each_violation(self, tmp_path, line):
        init = ("from .mod import fsync_dir, update_drama, upsert_character, get_app_setting\n"
                "from .consts import MAX_SNAPSHOT_WORD_BYTES\n")
        assert _db_package_problems(self._make_pkg(tmp_path, init, line + "\n")) != []

    def test_rule_allows_front_door_use_and_skips_init(self, tmp_path):
        init = "from .mod import update_drama\ndef f():\n    return update_drama(1) or LIBRARY_DIR\n"
        mod = "import db\ndef update_drama(i):\n    return db.get_drama(i), db.LIBRARY_DIR\n"
        assert _db_package_problems(self._make_pkg(tmp_path, init, mod)) == []

    def test_reexports_ignore_non_package_imports(self, tmp_path):
        init = "from contextlib import contextmanager\nfrom .mod import update_drama\n"
        pkg = self._make_pkg(tmp_path, init, "")
        assert _reexported_names(os.path.join(pkg, "__init__.py")) == {"update_drama"}

    def test_submodules_hold_no_copy_of_shared_state(self):
        if not os.path.isdir(_DB_DIR):
            pytest.skip("db is still a single file; submodule state applies after the split")
        import pkgutil
        import tempfile
        db = importlib.import_module("db")
        submodules = [importlib.import_module(m.name)
                      for m in pkgutil.iter_modules(db.__path__, "db.")]
        previous = db.LIBRARY_DIR
        try:
            with tempfile.TemporaryDirectory() as tmp:
                db.configure_library_dir(tmp)
                copies = _shared_state_copies(submodules)
        finally:
            db.configure_library_dir(previous)
        assert copies == [], f"define these once in db/__init__ and read them as db.<name>: {copies}"

    def test_shared_state_guard_flags_a_copy(self):
        import types
        m = types.ModuleType("db.fake")
        m.LIBRARY_DIR = "/stale"
        m._media_lock = object()
        assert _shared_state_copies([m]) == ["db.fake.LIBRARY_DIR", "db.fake._media_lock"]
        assert _shared_state_copies([types.ModuleType("db.clean")]) == []
