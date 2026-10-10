"""Ollama cloud tags need `engines.paid` on every route that takes a
client-chosen model, not only the translate run. Mocked: nothing is sent."""
import ast
import inspect
import textwrap
import typing

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service

CLOUD = "gemma4:31b-cloud"
MODEL_FIELDS = ("model", "models", "engine_model", "review_model")
GATE_CALLS = {"require_cloud_model_allowed", "require_engines_allowed"}

# Routes that carry a model field but never send text to it.
NO_TEXT_SENT = {
    ("GET", "/api/translate-run/dramas/{drama_id}/estimate"),
    ("POST", "/api/transcribe/dramas/{drama_id}/compare-transcription/estimate"),
    ("GET", "/api/translate-run/dramas/{drama_id}/glossary-affected"),
    ("GET", "/api/live/ollama-check"),
    ("POST", "/api/metadata/dramas/{drama_id}/research"),  # Gemini only
}


def _model_fields(cls) -> set:
    return set(getattr(cls, "model_fields", {}))


def _routes_with_model(app):
    for route, path, methods, decls in api_auth.iter_route_declarations(app):
        dep = getattr(route, "dependant", None)
        if dep is None:
            continue
        names = {qp.name for qp in dep.query_params}
        for bp in dep.body_params:
            for t in (getattr(bp, "type_", None), getattr(bp, "annotation", None),
                      getattr(getattr(bp, "field_info", None), "annotation", None)):
                names |= _model_fields(t)
                for arg in typing.get_args(t) or ():
                    names |= _model_fields(arg)
        if names & set(MODEL_FIELDS):
            yield route, path, sorted(methods), decls


def _applies_cloud_rule(fn, _depth=0) -> bool:
    """True when `fn` calls the cloud check directly, passes `model=` to
    require_engines_allowed, or hands a model to a `_require_*` helper that
    itself applies the rule (a helper that ignores the model doesn't count)."""
    for node in ast.walk(ast.parse(textwrap.dedent(inspect.getsource(fn)))):
        if not isinstance(node, ast.Call):
            continue
        name = getattr(node.func, "id", getattr(node.func, "attr", ""))
        if name == "require_cloud_model_allowed":
            return True
        if name == "require_engines_allowed" and any(k.arg == "model" for k in node.keywords):
            return True
        if name.startswith("_require") and "model" in ast.unparse(node) and _depth < 3:
            helper = getattr(fn, "__globals__", {}).get(name)
            if helper is not None and _applies_cloud_rule(helper, _depth + 1):
                return True
    return False


def test_every_model_taking_route_applies_the_cloud_rule():
    app = create_app(ApiSettings(auth_mode="on"))
    missing = []
    for route, path, methods, decls in _routes_with_model(app):
        if ("local_only", None) in decls or ("permission", "engines.paid") in decls:
            continue
        if any((m, path) in NO_TEXT_SENT for m in methods):
            continue
        if not _applies_cloud_rule(route.endpoint):
            missing.append(f"{methods} {path}")
    assert not missing, (
        "Routes with a client-chosen model must call require_engines_allowed(request, "
        "engine, model=...) or require_cloud_model_allowed so an Ollama cloud tag needs "
        "engines.paid:\n  " + "\n  ".join(missing))


def test_every_exemption_still_names_a_model_taking_route():
    seen = {(m, path) for _r, path, methods, _d in _routes_with_model(create_app(ApiSettings(auth_mode="on")))
            for m in methods}
    assert NO_TEXT_SENT <= seen, sorted(NO_TEXT_SENT - seen)


def test_the_sweep_sees_the_routes_it_should():
    seen = {path for _r, path, _m, _d in _routes_with_model(create_app(ApiSettings(auth_mode="on")))}
    for path in ("/api/line-ai/dramas/{drama_id}/lines/{line_id}/alternatives",
                 "/api/review-jobs/dramas/{drama_id}/consistency",
                 "/api/reader/dramas/{drama_id}/ask"):
        assert path in seen


def _household(email, extra=()):
    u = auth_service.add_user(email)
    for perm in extra:
        auth_service.grant_permission(u["id"], perm)
    sess = auth_service.create_session(u["id"])
    return {"Cookie": f"{api_auth.COOKIE_NAME}={sess['session_token']}",
            api_auth.CSRF_HEADER: sess["csrf_token"]}


@pytest.fixture
def remote(isolated_db):
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                      client=("203.0.113.9", 5000), raise_server_exceptions=False)


@pytest.fixture
def drama(isolated_db):
    return db.create_drama(title_en="T", source_language="zh")


def _calls(drama_id):
    return [
        ("line-ai alternatives", "lines.read",
         f"/api/line-ai/dramas/{drama_id}/lines/1/alternatives", {"engine": "ollama", "model": CLOUD}),
        ("line-ai grammar", "lines.read",
         f"/api/line-ai/dramas/{drama_id}/lines/1/grammar", {"engine": "ollama", "model": CLOUD}),
        ("review job", "jobs.start",
         f"/api/review-jobs/dramas/{drama_id}/consistency", {"engine": "ollama", "model": CLOUD}),
        ("reader ask", "jobs.start",
         f"/api/reader/dramas/{drama_id}/ask", {"engine": "ollama", "model": CLOUD, "question": "who?"}),
        ("narration", "jobs.start",
         f"/api/narration/dramas/{drama_id}/run", {"engine": "ollama", "model": CLOUD}),
        ("shorten", "lines.edit",
         f"/api/lines/dramas/{drama_id}/shorten-overlong", {"engine": "ollama", "model": CLOUD, "line_ids": [1]}),
    ]


def test_cloud_tag_is_refused_on_every_tool_route_without_engines_paid(remote, drama):
    for label, perm, url, body in _calls(drama):
        r = remote.post(url, json=body, headers=_household(f"{perm}-{abs(hash(url))}@example.com", [perm]))
        assert r.status_code == 403, label
        assert "paid-engine permission" in r.json()["error"]["message"], label


def test_cloud_tag_passes_the_gate_with_engines_paid(remote, drama):
    for label, perm, url, body in _calls(drama):
        h = _household(f"paid-{abs(hash(url))}@example.com", [perm, "engines.paid"])
        assert remote.post(url, json=body, headers=h).status_code != 403, label


def test_local_tag_on_a_tool_route_is_not_refused_by_the_cloud_gate(remote, drama):
    h = _household("kid@example.com", ["lines.read"])
    r = remote.post(f"/api/line-ai/dramas/{drama}/lines/1/alternatives",
                    json={"engine": "ollama", "model": "gemma4:12b"}, headers=h)
    assert r.status_code != 403


def test_omitted_model_is_treated_as_the_local_default():
    from types import SimpleNamespace
    from unittest.mock import patch
    with patch.object(api_auth, "holds", return_value=False):
        api_auth.require_engines_allowed(SimpleNamespace(), "ollama", model=None)
        with pytest.raises(Exception):
            api_auth.require_engines_allowed(SimpleNamespace(), "ollama", model=CLOUD)


def test_restored_cloud_default_override_is_ignored_on_read(isolated_db):
    import translate_engines as te
    db.set_app_setting(te.MODEL_OVERRIDE_DEFAULTS_KEY, {"ollama": "gemma4:cloud", "claude": "claude-sonnet-5-5"})
    assert te.model_override_for_default("ollama") is None
    assert te.model_override_for_default("claude") == "claude-sonnet-5-5"
    assert not te.is_ollama_cloud_model(te.effective_default_model("ollama"))
