"""The browser extension's translation engine under the API alone (inventory
G16): saved as an app setting, hooked into page_server at startup and on
change, key resolved from .env on the PC and never returned. page_server and
the scheduler are faked: no thread, no port."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
import page_server
import translate_engines
from api import auth as api_auth
from api import background
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, extension_service, settings_service
from sources import store as src_store

SECRET = "sk-ant-TESTSECRET1234567890abcdef"
REMOTE = "https://baihe.example.com"


@pytest.fixture
def env(isolated_db, tmp_path, monkeypatch):
    """An empty .env for this test, no key env vars, faked bridge/scheduler."""
    from sources import chapter_check
    path = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "default_env_path", lambda: str(path))
    for names in settings_service.ENV_NAMES.values():
        for n in names:
            monkeypatch.delenv(n, raising=False)
    state = {"on": False, "starts": 0}

    def serve(port=page_server.DEFAULT_PORT):
        state["starts"] += 1
        state["on"] = True
        return True

    monkeypatch.setattr(page_server, "ensure_server_started", serve)
    monkeypatch.setattr(page_server, "server_running", lambda: state["on"])
    monkeypatch.setattr(chapter_check, "ensure_scheduler_started", lambda: None)
    monkeypatch.setattr(background, "_started", None)
    return path


def _write_key(path, value=SECRET):
    path.write_text(f"BAIHE_CLAUDE_KEY={value}\n", encoding="utf-8")


def _client(**kw):
    return TestClient(create_app(ApiSettings(**kw)), raise_server_exceptions=False)


def test_default_is_no_engine_and_no_keys(env):
    r = _client().get("/api/extension/engine")
    assert r.status_code == 200
    body = r.json()
    assert body["engine"] is None and body["model"] is None and body["ready"] is False
    assert {e["name"] for e in body["engines"]} == set(translate_engines.ENGINES)
    assert all(set(e) == {"name", "label", "free", "models", "model_labels", "key_configured"}
               for e in body["engines"])


def test_save_persists_and_pushes_key_server_side(env):
    _write_key(env)
    c = _client()
    model = list(translate_engines.CLAUDE_MODELS)[0]
    r = c.post("/api/extension/engine", json={"engine": "claude", "model": model})
    assert r.status_code == 200
    assert SECRET not in r.text
    assert r.json()["engine"] == "claude" and r.json()["model"] == model
    assert r.json()["ready"] is True
    assert db.get_app_setting(extension_service.ENGINE_SETTING) == {"engine": "claude",
                                                                     "model": model}
    config = page_server.get_translation_config()
    assert config["engine"] == "claude" and config["model"] == model
    assert config["api_key"] == SECRET
    # A fresh read (e.g. after a restart) comes back from the database.
    r = c.get("/api/extension/engine")
    assert r.json()["engine"] == "claude" and SECRET not in r.text


def test_key_saved_later_is_used_without_another_push(env):
    c = _client()
    r = c.post("/api/extension/engine", json={"engine": "claude"})
    assert r.json()["ready"] is False
    assert page_server._build_engine(page_server.get_translation_config()) is None
    _write_key(env)
    assert page_server.get_translation_config()["api_key"] == SECRET
    assert c.get("/api/extension/engine").json()["ready"] is True


def test_clearing_the_engine(env):
    _write_key(env)
    c = _client()
    c.post("/api/extension/engine", json={"engine": "claude"})
    r = c.post("/api/extension/engine", json={"engine": None})
    assert r.status_code == 200 and r.json()["engine"] is None
    config = page_server.get_translation_config()
    assert config["engine"] is None and config["api_key"] == ""


def test_invalid_input_changes_nothing_and_echoes_nothing(env):
    c = _client()
    c.post("/api/extension/engine", json={"engine": "fake"})
    bad = ({"engine": "nope-" + SECRET}, {"engine": "claude", "model": "gpt-" + SECRET},
           {"engine": None, "model": "claude-sonnet-5"}, {"engine": "fake_mt", "model": "x"},
           {"engine": 3}, {"engine": "claude", "api_key": SECRET})
    for body in bad:
        r = c.post("/api/extension/engine", json=body)
        assert r.status_code == 422, body
        assert SECRET not in r.text
    assert c.get("/api/extension/engine").json()["engine"] == "fake"


def test_startup_pushes_saved_engine(env):
    _write_key(env)
    extension_service.set_translation_settings("claude")
    page_server.set_config_provider(None)
    page_server.set_translation_config(engine=None, api_key="")
    src_store.set_setting("page_server_enabled", True)
    with TestClient(create_app(ApiSettings(background_services=True))):
        pass
    config = page_server.get_translation_config()
    assert config["engine"] == "claude" and config["api_key"] == SECRET
    assert page_server._build_engine(config) is not None


def test_startup_with_bridge_off_pushes_nothing(env):
    extension_service.set_translation_settings("fake")
    page_server.set_config_provider(None)
    page_server.set_translation_config(engine=None, api_key="")
    with TestClient(create_app(ApiSettings(background_services=True))):
        pass
    assert page_server.get_translation_config()["engine"] is None


def test_turning_the_bridge_on_pushes_the_engine(env):
    extension_service.set_translation_settings("fake")
    page_server.set_config_provider(None)
    page_server.set_translation_config(engine=None, api_key="")
    c = _client(background_services=True)
    assert c.post("/api/extension/enabled", json={"enabled": True}).status_code == 200
    config = page_server.get_translation_config()
    assert config["engine"] == "fake" and config["api_key"] == "local"


def test_stale_saved_engine_reads_as_none(env):
    db.set_app_setting(extension_service.ENGINE_SETTING, {"engine": "retired-engine"})
    assert extension_service.get_translation_settings()["engine"] is None
    assert extension_service.resolved_translation_config()["engine"] is None


def test_text_block_translates_or_says_why_not(env):
    extension_service.set_translation_settings("claude")
    out = page_server.translate_text_block("你好", "zh", "en", store=False)
    assert out["translated_text"] == ""
    assert out["notes"][0][0] == "warning" and "(claude) has no key" in out["notes"][0][1]
    extension_service.set_translation_settings("fake")
    out = page_server.translate_text_block("你好", "zh", "en", store=False)
    assert out["engine"] == "fake" and out["translated_text"]
    assert not out["notes"]


def test_broken_provider_keeps_pushed_config(env):
    page_server.set_translation_config(engine="fake", api_key="offline")

    def boom():
        raise RuntimeError("db gone")
    page_server.set_config_provider(boom)
    assert page_server.get_translation_config()["engine"] == "fake"


def _admin_headers():
    u = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(u["id"])
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_remote_admin_reads_but_cannot_write(env):
    _write_key(env)
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    h = _admin_headers()
    r = remote.get("/api/extension/engine", headers=h)
    assert r.status_code == 200 and SECRET not in r.text
    assert remote.get("/api/extension/engine").status_code == 401
    r = remote.post("/api/extension/engine", json={"engine": "claude"}, headers=h)
    assert r.status_code == 403
    assert db.get_app_setting(extension_service.ENGINE_SETTING) is None


def test_remote_household_user_cannot_read(env):
    app = create_app(ApiSettings(auth_mode="on"))
    remote = TestClient(app, base_url=REMOTE, raise_server_exceptions=False)
    u = auth_service.add_user("member@example.com")
    s = auth_service.create_session(u["id"])
    r = remote.get("/api/extension/engine",
                   headers={"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}"})
    assert r.status_code == 403


HF_TOKEN = "hf_" + "A1b2C3d4E5" * 4


def _set_prefs(**prefs):
    for name, value in prefs.items():
        db.set_app_setting(settings_service._PREF_PREFIX + name, value)


def test_resolved_config_carries_no_ocr_overrides_by_default(env):
    config = extension_service.resolved_translation_config()
    assert config["tesseract_cmd"] is None and config["hf_token"] is None
    assert config["ocr_backend"] is None and config["prefer_paddle_vl_manga"] is False


def test_resolved_config_carries_the_saved_ocr_settings(env):
    env.write_text(f"BAIHE_HF_TOKEN={HF_TOKEN}\n", encoding="utf-8")
    _set_prefs(tesseract_cmd="/opt/tess/tesseract", ocr_backend="tesseract",
               ocr_prefer_paddle_vl_manga=True)
    config = extension_service.resolved_translation_config()
    assert config["tesseract_cmd"] == "/opt/tess/tesseract"
    assert config["ocr_backend"] == "tesseract"
    assert config["prefer_paddle_vl_manga"] is True
    assert config["hf_token"] == HF_TOKEN
    # Also with no engine chosen: OCR-only pages still use the saved OCR setup.
    assert extension_service.get_translation_settings()["engine"] is None
    extension_service.push_translation_config()
    assert page_server.get_translation_config()["tesseract_cmd"] == "/opt/tess/tesseract"


def test_a_saved_auto_backend_stays_language_driven(env):
    _set_prefs(ocr_backend="auto")
    assert extension_service.resolved_translation_config()["ocr_backend"] is None


def test_the_page_pipeline_receives_the_saved_ocr_settings(env, monkeypatch):
    import scanlate
    env.write_text(f"BAIHE_HF_TOKEN={HF_TOKEN}\n", encoding="utf-8")
    _set_prefs(tesseract_cmd="/opt/tess/tesseract")
    seen = {}

    def fake_detect(image_path, source_language, **kwargs):
        seen.update(kwargs)
        return [], [("warning", f"download failed with token {HF_TOKEN}")]

    monkeypatch.setattr(scanlate, "detect_and_ocr_page", fake_detect)
    page_server.set_config_provider(extension_service.resolved_translation_config)
    out = page_server.translate_image(b"not-really-a-png", "image/png", store=False,
                                      source_language="zh")
    assert seen["tesseract_cmd"] == "/opt/tess/tesseract"
    assert seen["hf_token"] == HF_TOKEN
    assert HF_TOKEN not in str(out) and "[REDACTED]" in str(out["notes"])


def test_no_response_exposes_the_ocr_settings_or_hf_token(env):
    env.write_text(f"BAIHE_HF_TOKEN={HF_TOKEN}\n", encoding="utf-8")
    _set_prefs(tesseract_cmd="/opt/tess/tesseract")
    c = _client()
    for r in (c.get("/api/extension/status"), c.get("/api/extension/engine"),
              c.post("/api/extension/engine", json={"engine": "fake"})):
        assert HF_TOKEN not in r.text and "/opt/tess" not in r.text
