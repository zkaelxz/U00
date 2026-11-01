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
    # A top-level `if TYPE_CHECKING: ... else: ...` defines its names at module level too.
    nodes = [n for node in tree.body
             for n in ((node.body + node.orelse) if isinstance(node, ast.If) else [node])]
    for node in nodes:
        if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
            names.append(node.name)
        elif isinstance(node, ast.Assign):
            names += [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.append(node.target.id)
    return [n for n in dict.fromkeys(names) if not n.startswith("_") and n != "__all__"]


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


def test_error_responses_documents_each_code_with_the_error_envelope():
    assert schemas.error_responses() == {}
    assert schemas.error_responses(404, 409) == {
        404: {"model": schemas.ErrorResponse},
        409: {"model": schemas.ErrorResponse},
    }
    assert list(schemas.error_responses(422, 400)) == [422, 400]


def test_shared_request_bases_keep_each_models_name_and_strictness():
    import pytest
    from pydantic import ValidationError
    from api.diagnostics_install_schemas import DiagnosticsUpgradeCheckRequest

    for model in (schemas.BrowserInstallRequest, schemas.DeleteConfirm, schemas.EngineKeyClearRequest):
        assert issubclass(model, schemas.ConfirmRequest)
        assert model().confirm is False
        assert model.model_json_schema()["title"] == model.__name__
        with pytest.raises(ValidationError):
            model(confirm="yes")
        with pytest.raises(ValidationError):
            model(confirm=True, extra=1)
    assert list(DiagnosticsUpgradeCheckRequest.model_fields) == ["confirm", "target"]
    with pytest.raises(ValidationError):
        schemas.DiagnosticsUpgradePlanRequest(target="not a version")


def test_error_responses_only_feeds_openapi_responses_in_routers():
    # A rewrite once turned runtime state (`_open_streams = {}`) into error_responses().
    routers = os.path.join(os.path.dirname(PKG_DIR), "routers")
    for name in sorted(os.listdir(routers)):
        if not name.endswith(".py"):
            continue
        with open(os.path.join(routers, name), encoding="utf-8") as fh:
            tree = ast.parse(fh.read())
        parents = {child: node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}
        consts = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and getattr(node.func, "id", "") == "error_responses":
                assert node.args, f"{name}:{node.lineno} error_responses() with no codes"
                parent = parents[node]
                if isinstance(parent, ast.Assign):
                    consts.update(t.id for t in parent.targets)
                else:
                    assert isinstance(parent, (ast.keyword, ast.Dict)), f"{name}:{node.lineno}"
        for node in ast.walk(tree):
            if isinstance(node, ast.Name) and node.id in consts and isinstance(node.ctx, ast.Load):
                parent = parents[node]
                assert (isinstance(parent, ast.keyword) and parent.arg == "responses") or isinstance(
                    parent, ast.Dict), f"{name}:{node.lineno} {node.id} is not used as responses="
