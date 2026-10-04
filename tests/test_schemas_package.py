"""api/schemas is a package of domain modules; `api.schemas.X` must keep working."""

import ast
import importlib
import os

import api.schemas as schemas

PKG_DIR = os.path.dirname(schemas.__file__)
MODULES = sorted(f[:-3] for f in os.listdir(PKG_DIR) if f.endswith(".py") and f != "__init__.py")


def _defined_names(mod_name):
    with open(os.path.join(PKG_DIR, mod_name + ".py"), encoding="utf-8") as fh:
        tree = ast.parse(fh.read())
    names = []
    for node in tree.body:
        if isinstance(node, ast.ClassDef):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return [n for n in names if not n.startswith("_") and n != "__all__"]


def test_no_name_is_defined_in_two_modules():
    seen = {}
    for m in MODULES:
        for n in _defined_names(m):
            assert n not in seen, f"{n} defined in both {seen[n]} and {m}"
            seen[n] = m


def test_every_module_name_is_reexported_and_identical():
    for m in MODULES:
        mod = importlib.import_module(f"api.schemas.{m}")
        assert sorted(mod.__all__) == sorted(_defined_names(m)), m
        for n in mod.__all__:
            assert getattr(schemas, n) is getattr(mod, n), f"{m}.{n}"


def test_domain_modules_import_only_common():
    for m in MODULES:
        if m == "common":
            continue
        with open(os.path.join(PKG_DIR, m + ".py"), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("api.schemas"):
                assert node.module == "api.schemas.common", f"{m} imports {node.module}"
