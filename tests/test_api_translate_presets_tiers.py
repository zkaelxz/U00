"""Parity X02/X22: apply a workflow tier and save the Translate form's
settings as a preset (services/translate_run_service.py, routes in
api/routers/translate_run_routes.py)."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import translate_engines
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, settings_service, translate_run_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

TIER = "/api/translate-run/dramas/{}/workflow-tier"
PRESETS = "/api/translate-run/presets"
FAKE_KEY = "sk-FAKE-SECRET-1234567890"


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _preset_body(**kw):
    body = {"name": "Mine", "translation_engine": "claude", "engine_model": "claude-sonnet-5",
            "style_preset": "audio_drama", "locale": "en-GB",
            "default_female_pronouns": True, "include_genre_notes": False}
    body.update(kw)
    return body


# --- service ------------------------------------------------------------------

def test_service_apply_tier_matches_streamlit(isolated_db):
    """apply_workflow_tier in tabs/workspace_tab.py: engine onto the drama row;
    model/reflect/auto_qc into the form (returned here)."""
    did = db.create_drama(title_zh="D", translation_engine="deepl")
    for key, t in translate_engines.WORKFLOW_TIERS.items():
        r = translate_run_service.apply_workflow_tier(did, key)
        assert r == {"drama_id": did, "tier": key, "label": t["label"],
                     "translation_engine": t["translation_engine"],
                     "engine_model": t["engine_model"], "reflect": bool(t["reflect"]),
                     "auto_qc": bool(t["auto_qc"])}
        assert db.get_drama(did)["translation_engine"] == t["translation_engine"]


def test_service_apply_tier_starts_nothing(isolated_db, monkeypatch):
    monkeypatch.setattr(background_jobs, "start_job",
                        lambda *a, **k: pytest.fail("applying a tier must not start a job"))
    did = db.create_drama(title_zh="D")
    translate_run_service.apply_workflow_tier(did, "release")


def test_service_apply_tier_errors(isolated_db):
    with pytest.raises(NotFoundError):
        translate_run_service.apply_workflow_tier(9999, "draft")
    did = db.create_drama(title_zh="D")
    with pytest.raises(InvalidInputError):
        translate_run_service.apply_workflow_tier(did, "nope")


def test_service_save_preset_and_overwrite(isolated_db):
    r = translate_run_service.save_translate_preset(
        "Mine", "claude", engine_model="claude-sonnet-5", style_preset="audio_drama",
        locale="en-GB", default_female_pronouns=True, include_genre_notes=False)
    assert r["replaced"] is False
    p = r["preset"]
    assert p["name"] == "Mine" and p["translation_engine"] == "claude"
    assert p["engine_model"] == "claude-sonnet-5" and p["locale"] == "en-GB"
    assert p["default_female_pronouns"] == 1 and p["include_genre_notes"] == 0
    with pytest.raises(ConflictError):
        translate_run_service.save_translate_preset("Mine", "deepseek")
    assert db.list_presets()[0]["translation_engine"] == "claude"   # untouched
    r2 = translate_run_service.save_translate_preset("  Mine ", "deepseek", overwrite=True)
    assert r2["replaced"] is True and r2["preset"]["id"] == p["id"]
    assert r2["preset"]["translation_engine"] == "deepseek"
    assert r2["preset"]["engine_model"] is None
    assert len(db.list_presets()) == 1


@pytest.mark.parametrize("kw", [
    {"name": "   "}, {"name": "x" * 101}, {"translation_engine": "nope"},
    {"engine_model": "not-a-model"},
    {"translation_engine": "deepseek", "engine_model": "claude-sonnet-5"},
    {"style_preset": "nope"}, {"locale": "fr-FR"},
])
def test_service_save_preset_invalid(isolated_db, kw):
    args = {"name": "Mine", "translation_engine": "claude", **kw}
    with pytest.raises(InvalidInputError):
        translate_run_service.save_translate_preset(**args)
    assert db.list_presets() == []


def test_service_save_preset_accepts_a_pulled_ollama_model(isolated_db):
    p = translate_run_service.save_translate_preset(
        "Local", "ollama", engine_model="my-own/qwen3:14b-q4")["preset"]
    assert p["engine_model"] == "my-own/qwen3:14b-q4"


@pytest.mark.parametrize("bad", ["../x", "/abs", "has space", "a/../b", "", "x" * 101])
def test_service_save_preset_refuses_unsafe_ollama_names(isolated_db, bad):
    with pytest.raises(InvalidInputError):
        translate_run_service.save_translate_preset("Local", "ollama", engine_model=bad)
    assert db.list_presets() == []


# --- API ----------------------------------------------------------------------

def test_api_apply_tier(client):
    did = db.create_drama(title_zh="D", translation_engine="deepseek")
    r = client.post(TIER.format(did), json={"tier": "release"})
    assert r.status_code == 200
    body = r.json()
    assert body["translation_engine"] == "claude" and body["engine_model"] == "claude-opus-4-8"
    assert body["reflect"] is True and body["auto_qc"] is True
    assert db.get_drama(did)["translation_engine"] == "claude"


def test_api_apply_tier_errors(client):
    assert client.post(TIER.format(9999), json={"tier": "draft"}).status_code == 404
    did = db.create_drama(title_zh="D")
    for body in ({"tier": "nope"}, {}, {"tier": "draft", "extra": 1}):
        r = client.post(TIER.format(did), json=body)
        assert r.status_code == 422, body


def test_api_save_preset(client):
    r = client.post(PRESETS, json=_preset_body())
    assert r.status_code == 200
    body = r.json()
    assert body["replaced"] is False and body["preset"]["name"] == "Mine"
    listed = client.get("/api/library/presets").json()["items"]
    assert [p["name"] for p in listed] == ["Mine"]
    r = client.post(PRESETS, json=_preset_body(locale="en-US"))
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "conflict"
    r = client.post(PRESETS, json=_preset_body(locale="en-US", overwrite=True))
    assert r.status_code == 200 and r.json()["replaced"] is True
    assert r.json()["preset"]["locale"] == "en-US"


@pytest.mark.parametrize("patch", [
    {"name": ""}, {"translation_engine": "nope"}, {"locale": "xx"},
    {"default_female_pronouns": "yes"}, {"unknown": 1},
])
def test_api_save_preset_422(client, patch):
    assert client.post(PRESETS, json=_preset_body(**patch)).status_code == 422


def test_no_secrets_or_paths(client, monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **kw: FAKE_KEY)
    did = db.create_drama(title_zh="D")
    texts = [client.post(TIER.format(did), json={"tier": "standard"}).text,
             client.post(PRESETS, json=_preset_body()).text,
             client.post(PRESETS, json=_preset_body()).text]
    for t in texts:
        assert FAKE_KEY not in t
        assert db.LIBRARY_DIR not in t and "library.db" not in t


# --- auth on ------------------------------------------------------------------

def _remote_client():
    return TestClient(create_app(ApiSettings(auth_mode="on")),
                      base_url="https://baihe.example.com", raise_server_exceptions=False)


def _session(*perms):
    u = auth_service.add_user("kid@example.com")
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}, u


def test_auth_on_permissions(isolated_db):
    c = _remote_client()
    did = db.create_drama(title_zh="D")
    assert c.post(TIER.format(did), json={"tier": "draft"}).status_code == 401
    assert c.post(PRESETS, json=_preset_body()).status_code == 401

    h, u = _session()
    auth_service.revoke_permission(u["id"], "lines.edit")   # a household default
    assert "admin.library" not in auth_service.effective_permissions(u["id"])
    assert c.post(TIER.format(did), json={"tier": "draft"}, headers=h).status_code == 403
    assert c.post(PRESETS, json=_preset_body(), headers=h).status_code == 403

    auth_service.grant_permission(u["id"], "lines.edit")
    assert c.post(TIER.format(did), json={"tier": "draft"}, headers=h).status_code == 200
    assert c.post(PRESETS, json=_preset_body(), headers=h).status_code == 403   # needs admin.library

    # admin.library comes only from the admin flag (auth_service.ADMIN_PERMISSIONS).
    admin = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(admin["id"])
    ha = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
          api_auth.CSRF_HEADER: s["csrf_token"]}
    assert c.post(PRESETS, json=_preset_body(), headers=ha).status_code == 200


def test_insert_preset_refuses_a_taken_name(isolated_db):
    import sqlite3
    db.insert_preset("Mine", translation_engine="claude")
    with pytest.raises(sqlite3.IntegrityError):
        db.insert_preset("Mine", translation_engine="deepseek")
    assert [p["translation_engine"] for p in db.list_presets()] == ["claude"]


def test_overwrite_is_pc_only_when_auth_on(isolated_db):
    admin = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(admin["id"])
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    remote = _remote_client()
    assert remote.post(PRESETS, json=_preset_body(), headers=h).status_code == 200
    assert remote.post(PRESETS, json=_preset_body(locale="en-US"), headers=h).status_code == 409
    r = remote.post(PRESETS, json=_preset_body(locale="en-US", overwrite=True), headers=h)
    assert r.status_code == 403
    assert db.list_presets()[0]["locale"] == "en-GB"          # untouched
    local = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="http://127.0.0.1:8600",
                       client=("127.0.0.1", 5000), raise_server_exceptions=False)
    r = local.post(PRESETS, json=_preset_body(locale="en-US", overwrite=True), headers=h)
    assert r.status_code == 200 and r.json()["replaced"] is True
    assert db.list_presets()[0]["locale"] == "en-US"


# --- Parity X03: apply a saved preset to an existing drama --------------------

APPLY = "/api/translate-run/dramas/{}/apply-preset"


def test_apply_preset_saves_engine_and_returns_form_values(client, isolated_db, monkeypatch):
    monkeypatch.setattr(background_jobs, "start_job",
                        lambda *a, **k: pytest.fail("applying a preset must not start a job"))
    did = db.create_drama(title_zh="D", translation_engine="deepl")
    pid = db.save_preset("Mine", translation_engine="gemini", engine_model="gemini-2.5-pro",
                         style_preset="subtitle", locale="en-GB",
                         default_female_pronouns=True, include_genre_notes=False)
    r = client.post(APPLY.format(did), json={"preset_id": pid})
    assert r.status_code == 200, r.text
    assert r.json() == {"drama_id": did, "preset_id": pid, "name": "Mine",
                        "translation_engine": "gemini", "engine_model": "gemini-2.5-pro",
                        "style_preset": "subtitle", "locale": "en-GB",
                        "default_female_pronouns": True, "include_genre_notes": False}
    d = db.get_drama(did)
    assert d["translation_engine"] == "gemini" and d["title_zh"] == "D"


def test_apply_preset_without_engine_keeps_the_drama_engine(client, isolated_db):
    did = db.create_drama(title_zh="D", translation_engine="deepl")
    pid = db.save_preset("Bare", style_preset="bogus", locale="xx")
    body = client.post(APPLY.format(did), json={"preset_id": pid}).json()
    assert body["translation_engine"] is None and body["engine_model"] is None
    assert body["style_preset"] is None and body["locale"] is None
    assert body["include_genre_notes"] is True and body["default_female_pronouns"] is False
    assert db.get_drama(did)["translation_engine"] == "deepl"


@pytest.mark.parametrize("body", [{}, {"preset_id": "1"}, {"preset_id": 0}, {"preset_id": 1, "x": 1}])
def test_apply_preset_bad_body_422(client, isolated_db, body):
    did = db.create_drama(title_zh="D")
    assert client.post(APPLY.format(did), json=body).status_code == 422


def test_apply_preset_unknown_404(client, isolated_db):
    did = db.create_drama(title_zh="D")
    pid = db.save_preset("P", translation_engine="claude")
    assert client.post(APPLY.format(did), json={"preset_id": pid + 50}).status_code == 404
    assert client.post(APPLY.format(9999), json={"preset_id": pid}).status_code == 404


# --- Parity X04: style guidance text in the config -----------------------------

def test_config_style_presets_carry_guidance(client, isolated_db):
    import translation_guide
    did = db.create_drama(title_zh="D")
    presets = client.get(f"/api/translate-run/dramas/{did}/config").json()["style_presets"]
    assert {p["key"]: p["guidance"] for p in presets} == {
        k: v["guidance"] for k, v in translation_guide.STYLE_PRESETS.items()}
