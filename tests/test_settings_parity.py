"""Settings parity (inventory G05, G06 URLs, G08, G09, G13, G14, G15):
persisted PC-side preferences and endpoint URLs, their permissions, and
that the services and the CLI read the saved values. Mocked; no network."""

import whisper_models
import contextlib
import inspect
import io
import os
import types

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import translate_engines
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service, settings_service
from services.service_errors import InvalidInputError

LOCAL = "http://127.0.0.1:8600"
REMOTE = "https://baihe.example.com"
SECRET = "sk-secret-value-123"


@pytest.fixture
def env_file(tmp_path, monkeypatch):
    path = tmp_path / ".env"
    monkeypatch.setattr(settings_service, "default_env_path", lambda: str(path))
    for names in settings_service.ENV_NAMES.values():
        for n in names:
            monkeypatch.delenv(n, raising=False)
    return path


@pytest.fixture
def client(isolated_db, env_file):
    return TestClient(create_app(ApiSettings(allow_key_writes=True)), base_url=LOCAL,
                      client=("127.0.0.1", 50000), raise_server_exceptions=False)


@pytest.fixture(autouse=True)
def _clean_jobs():
    background_jobs.clear_all_jobs()
    yield
    background_jobs.clear_all_jobs()


# --- service: preferences --------------------------------------------------------

def test_preference_defaults(isolated_db, env_file):
    prefs = settings_service.get_preferences()
    assert prefs == {
        "default_engine": "claude", "default_locale": "en-US", "default_style_note": "",
        "scene_aware_batches": True, "episode_summary_engine": "ollama", "monthly_cap_usd": None,
        "max_upload_mb": 20480, "ollama_num_ctx_override": 0, "keep_free_vram_gb": 0.0,
        "keep_free_ram_gb": 0.0, "whisper_model_path": "", "ocr_backend": "auto",
        "ocr_prefer_paddle_vl_manga": False, "tesseract_cmd": "", "lncrawl_cmd": "",
        "cookies_browser": None, "cookies_file": ""}
    assert settings_service.get_monthly_cap_usd() == 0.0
    assert settings_service.get_whisper_model_path() is None
    assert settings_service.get_tesseract_cmd() is None
    assert settings_service.get_cookie_settings() == {"cookies_browser": None,
                                                      "cookies_file": None}


def test_preferences_round_trip_and_persist(isolated_db, env_file):
    updates = {
        "default_engine": "deepseek", "default_locale": "en-GB",
        "default_style_note": "  Keep it casual.\nNo slang.  ",
        "episode_summary_engine": "gemini", "monthly_cap_usd": 12.5,
        "ollama_num_ctx_override": 16384, "whisper_model_path": r"D:\models\whisper-small",
        "ocr_backend": "manga_ocr", "ocr_prefer_paddle_vl_manga": True,
        "tesseract_cmd": r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        "cookies_browser": "firefox", "cookies_file": "/home/me/cookies.txt"}
    out = settings_service.set_settings(updates)["preferences"]
    assert out["default_style_note"] == "Keep it casual.\nNo slang."
    assert out["monthly_cap_usd"] == 12.5 and out["ocr_prefer_paddle_vl_manga"] is True
    # Stored in db.app_settings: a fresh read sees them.
    import db
    assert db.get_app_setting("pref.default_engine") == "deepseek"
    assert settings_service.get_default_engine() == "deepseek"
    assert settings_service.get_ollama_num_ctx_override() == 16384
    assert settings_service.get_cookie_settings() == {"cookies_browser": "firefox",
                                                      "cookies_file": "/home/me/cookies.txt"}
    # Clearing: "" for paths/browser, None for the cap.
    settings_service.set_settings({"cookies_browser": "", "cookies_file": "",
                                   "monthly_cap_usd": None})
    assert settings_service.get_cookie_settings() == {"cookies_browser": None,
                                                      "cookies_file": None}
    assert settings_service.get_preference("monthly_cap_usd") is None


@pytest.mark.parametrize("key,bad", [
    ("default_engine", "not-an-engine"),
    ("default_locale", "fr-FR"), ("episode_summary_engine", "fake_mt"),
    ("monthly_cap_usd", -1), ("monthly_cap_usd", True), ("monthly_cap_usd", "5"),
    ("ollama_num_ctx_override", 1.5), ("ollama_num_ctx_override", -1),
    ("max_upload_mb", 99), ("max_upload_mb", 1_048_577), ("max_upload_mb", 0), ("max_upload_mb", -5),
    ("max_upload_mb", 500.5), ("max_upload_mb", True), ("max_upload_mb", "2048"),
    ("ollama_num_ctx_override", True), ("ocr_backend", "easyocr"),
    ("ocr_prefer_paddle_vl_manga", "yes"), ("cookies_browser", "netscape"),
    ("tesseract_cmd", "a\x00b"), ("cookies_file", "x" * 1025),
    ("default_style_note", SECRET * 200),
])
def test_bad_preference_rejected_atomically_without_echo(isolated_db, env_file, key, bad):
    with pytest.raises(InvalidInputError) as ei:
        settings_service.set_settings({"use_gpu": True, key: bad})
    assert SECRET not in str(ei.value) and "not-an-engine" not in str(ei.value)
    assert settings_service.get_use_gpu() is False  # nothing written
    assert settings_service.get_preferences()[key] == settings_service._PREFERENCES[key][0]


def test_stale_stored_value_reads_as_default(isolated_db, env_file):
    import db
    db.set_app_setting("pref.default_engine", "removed_engine")
    db.set_app_setting("pref.ollama_num_ctx_override", "lots")
    assert settings_service.get_default_engine() == "claude"
    assert settings_service.get_ollama_num_ctx_override() == 0


def test_monthly_cap_saved_value_wins_over_env(isolated_db, env_file):
    env_file.write_text("BAIHE_MONTHLY_CAP_USD=40\n")
    assert settings_service.get_monthly_cap_usd() == 40.0
    settings_service.set_settings({"monthly_cap_usd": 7})
    assert settings_service.get_monthly_cap_usd() == 7.0
    settings_service.set_settings({"monthly_cap_usd": 0})
    assert settings_service.get_monthly_cap_usd() == 0.0  # 0 = no cap, even with .env set
    settings_service.set_settings({"monthly_cap_usd": None})
    ov = settings_service.get_settings_overview()
    assert ov["effective_monthly_cap_usd"] == 40.0 and ov["monthly_cap_env_usd"] == 40.0


@pytest.mark.parametrize("lang,prefer,expected", [
    ("ja", False, "manga_ocr"), ("ja", True, "paddle_vl_manga"), ("zh", False, "paddle"),
    ("ko", False, "paddle"), ("en", False, "tesseract")])
def test_resolve_ocr_backend_auto(isolated_db, lang, prefer, expected):
    import scanlate
    settings_service.set_settings({"ocr_prefer_paddle_vl_manga": prefer})
    assert settings_service.resolve_ocr_backend(lang) == expected
    assert expected == scanlate.auto_ocr_backend(lang, prefer)


def test_resolve_ocr_backend_explicit_and_narrowed(isolated_db):
    settings_service.set_settings({"ocr_backend": "tesseract"})
    assert settings_service.resolve_ocr_backend("ja") == "tesseract"
    settings_service.set_settings({"ocr_backend": "paddle_vl_manga"})
    assert settings_service.resolve_ocr_backend("ja", ("manga_ocr", "tesseract")) == "manga_ocr"
    # An explicit pick is kept even if missing; an auto pick falls back.
    assert settings_service.resolve_ocr_backend(
        "ja", ("paddle_vl_manga", "tesseract"), is_installed=lambda b: False) == "paddle_vl_manga"
    settings_service.set_settings({"ocr_backend": "auto"})
    assert settings_service.resolve_ocr_backend(
        "zh", ("paddle", "tesseract"), is_installed=lambda b: b != "paddle") == "paddle"
    assert settings_service.resolve_ocr_backend(
        "zh", ("tesseract", "paddle"), is_installed=lambda b: b != "paddle") == "tesseract"


# --- service: endpoint URLs --------------------------------------------------------

@pytest.mark.parametrize("url", [
    "ftp://h", "http://", "http://user:pw@h:11434", "http://h/?token=1", "http://h/#x",
    "http://h:99999", "http://h a", 'http://h"', "http://h\nX=1", "javascript:alert(1)",
    "http://" + "a" * 300])
def test_bad_endpoint_url_rejected(env_file, url):
    with pytest.raises(InvalidInputError) as ei:
        settings_service.set_endpoint_url("ollama_url", url)
    assert "pw" not in str(ei.value) and "token" not in str(ei.value)
    assert not env_file.exists()


def test_endpoint_url_set_clear_and_read(env_file):
    env_file.write_text("OTHER=1\n")
    out = settings_service.set_endpoint_url("ollama_url", " http://192.168.1.5:11434/ ")
    assert out == {"name": "ollama_url", "url": "http://192.168.1.5:11434", "configured": True}
    assert env_file.read_text() == "OTHER=1\nBAIHE_OLLAMA_URL=http://192.168.1.5:11434\n"
    assert settings_service.resolve_key("ollama_url") == "http://192.168.1.5:11434"
    out = settings_service.clear_endpoint_url("ollama_url")
    assert out == {"name": "ollama_url", "url": None, "configured": False}
    assert env_file.read_text() == "OTHER=1\n"
    with pytest.raises(InvalidInputError):
        settings_service.set_endpoint_url("claude", "http://h")


def test_hand_edited_url_with_password_is_not_returned(isolated_db, env_file):
    env_file.write_text("BAIHE_OLLAMA_URL=http://me:hunter2@lt.local:5000\n")
    ov = settings_service.get_settings_overview()
    assert ov["endpoints"]["ollama_url"] is None
    assert ov["engine_keys"]["ollama_url"] is True
    assert "hunter2" not in repr(ov)


# --- API -------------------------------------------------------------------------

def test_api_get_and_post_preferences(client):
    body = client.get("/api/settings").json()
    assert body["preferences"]["default_engine"] == "claude"
    assert "manga_ocr" in body["choices"]["ocr_backends"]
    assert "firefox" in body["choices"]["cookie_browsers"]
    assert set(body["endpoints"]) == {"ollama_url"}
    assert "gpt_sovits_url" not in body["engine_keys"]
    r = client.post("/api/settings", json={"default_locale": "en-AU", "monthly_cap_usd": 3,
                                           "tesseract_cmd": "/usr/bin/tesseract"})
    assert r.status_code == 200
    prefs = client.get("/api/settings").json()["preferences"]
    assert prefs["default_locale"] == "en-AU" and prefs["monthly_cap_usd"] == 3.0
    assert prefs["tesseract_cmd"] == "/usr/bin/tesseract"


@pytest.mark.parametrize("body", [
    {"ollama_num_ctx_override": "4096"}, {"monthly_cap_usd": "5"},
    {"default_engine": "nope"}, {"ollama_url": "http://h"}, {"claude": SECRET}])
def test_api_rejects_bad_or_unknown_without_echo(client, body):
    r = client.post("/api/settings", json=body)
    assert r.status_code == 422 and SECRET not in r.text


def test_api_endpoint_routes(client, env_file):
    r = client.post("/api/settings/endpoints/ollama_url",
                    json={"url": "http://127.0.0.1:11434", "confirm": True})
    assert r.status_code == 200 and r.json()["url"] == "http://127.0.0.1:11434"
    assert client.get("/api/settings").json()["endpoints"]["ollama_url"] == \
        "http://127.0.0.1:11434"
    bad = client.post("/api/settings/endpoints/ollama_url",
                      json={"url": "http://u:hunter2@h", "confirm": True})
    assert bad.status_code == 422 and "hunter2" not in bad.text
    assert client.post("/api/settings/endpoints/ollama_url",
                       json={"url": "http://h"}).status_code == 422  # no confirm
    r = client.post("/api/settings/endpoints/ollama_url/clear", json={"confirm": True})
    assert r.status_code == 200 and r.json()["configured"] is False
    assert "BAIHE_OLLAMA_URL" not in env_file.read_text()


def test_the_removed_gpt_sovits_address_is_not_settable_and_a_stored_one_is_never_shown(
        client, env_file):
    env_file.write_text("BAIHE_GPT_SOVITS_URL=http://old-sovits.example:9880\n", encoding="utf-8")
    for path in ("/api/settings/endpoints/gpt_sovits_url", "/api/settings/endpoints/gpt_sovits_url/clear"):
        r = client.post(path, json={"url": "http://127.0.0.1:9880", "confirm": True})
        assert r.status_code == 422
    body = client.get("/api/settings")
    assert "old-sovits" not in body.text and "gpt_sovits" not in body.text
    assert "old-sovits" in env_file.read_text()  # left alone, just unused


def test_api_endpoint_routes_need_key_write_gate(isolated_db, env_file):
    off = TestClient(create_app(ApiSettings(allow_key_writes=False)), base_url=LOCAL,
                     client=("127.0.0.1", 50000), raise_server_exceptions=False)
    assert off.post("/api/settings/endpoints/ollama_url",
                    json={"url": "http://h", "confirm": True}).status_code == 403
    on = TestClient(create_app(ApiSettings(allow_key_writes=True)), base_url=LOCAL,
                    client=("127.0.0.1", 50000), raise_server_exceptions=False)
    assert on.post("/api/settings/endpoints/ollama_url", json={"url": "http://h", "confirm": True},
                   headers={"X-Forwarded-For": "1.2.3.4"}).status_code == 403
    assert not env_file.exists()


def _session(admin=False, *perms):
    if admin:
        u = auth_service.grant_admin_local("admin@example.com")
    else:
        u = auth_service.add_user("kid@example.com")
        for p in perms:
            auth_service.grant_permission(u["id"], p)
    return auth_service.create_session(u["id"], "pytest", "203.0.113.9")


def _h(s):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_remote_read_needs_admin_and_writes_are_pc_only(isolated_db, env_file):
    c = TestClient(create_app(ApiSettings(auth_mode="on", allow_key_writes=True)),
                   base_url=REMOTE, raise_server_exceptions=False)
    kid = _session(False, "library.read", "jobs.start")
    assert c.get("/api/settings", headers=_h(kid)).status_code == 403
    admin = _session(True)
    assert c.get("/api/settings", headers=_h(admin)).status_code == 200
    for path, body in (("/api/settings", {"tesseract_cmd": "/tmp/evil"}),
                       ("/api/settings", {"cookies_file": "/tmp/c.txt"}),
                       ("/api/settings/endpoints/ollama_url",
                        {"url": "http://evil.example", "confirm": True})):
        assert c.post(path, json=body, headers=_h(admin)).status_code == 403
    assert settings_service.get_tesseract_cmd() is None
    assert not env_file.exists()


def test_remote_read_gets_path_flags_not_paths(isolated_db, env_file):
    """Security review (PR #439): absolute paths on the PC are returned only
    to the PC itself; a remote admin learns only whether each one is set."""
    settings_service.set_settings({"whisper_model_path": r"D:\models\whisper",
                                   "tesseract_cmd": r"C:\Tesseract\tesseract.exe"})
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                   raise_server_exceptions=False)
    r = c.get("/api/settings", headers=_h(_session(True)))
    prefs = r.json()["preferences"]
    body = r.text.replace("offer_provider_models", "")
    assert r.status_code == 200 and "models" not in body and "Tesseract" not in body
    assert prefs["whisper_model_path"] == prefs["tesseract_cmd"] == prefs["cookies_file"] == ""
    assert prefs["whisper_model_path_configured"] is True
    assert prefs["tesseract_cmd_configured"] is True
    assert prefs["cookies_file_configured"] is False
    local = TestClient(create_app(ApiSettings()), base_url=LOCAL, client=("127.0.0.1", 50000),
                       raise_server_exceptions=False)
    prefs = local.get("/api/settings").json()["preferences"]
    assert prefs["tesseract_cmd"] == r"C:\Tesseract\tesseract.exe"
    assert prefs["tesseract_cmd_configured"] is True and prefs["cookies_file_configured"] is False


# --- consumers ------------------------------------------------------------------

def _seed(isolated_db, texts=(("你好", ""),), **fields):
    fields.setdefault("translation_engine", None)  # no engine saved on the drama
    did = isolated_db.create_drama(title_zh="D", **fields)
    isolated_db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z, en=en)
                                 for i, (z, en) in enumerate(texts)])
    return did


def test_translate_config_uses_default_engine_locale_style_and_cap(isolated_db, env_file):
    from services import translate_run_service
    did = _seed(isolated_db)
    cfg = translate_run_service.get_translate_config(did)
    assert cfg["translation_engine"] == "claude" and cfg["default_locale"] == "en-US"
    settings_service.set_settings({"default_engine": "fake_mt", "default_locale": "en-GB",
                                   "default_style_note": "Short lines.",
                                   "monthly_cap_usd": 9})
    cfg = translate_run_service.get_translate_config(did)
    assert cfg["translation_engine"] == "fake_mt"
    assert cfg["default_locale"] == "en-GB" and cfg["default_style_note"] == "Short lines."
    assert cfg["monthly_cap_usd"] == 9.0
    # A drama with its own engine keeps it.
    isolated_db.update_drama(did, translation_engine="gemini")
    assert translate_run_service.get_translate_config(did)["translation_engine"] == "gemini"


def test_translate_run_passes_num_ctx_override_and_summary_engine(isolated_db, env_file,
                                                                   monkeypatch):
    from services import translate_run_service
    did = _seed(isolated_db)
    captured = {}

    def fake_start_job(job_id, target, *a, **k):
        captured.update(dict(zip(inspect.signature(target).parameters, a)))
        captured.update(k)
        return True
    monkeypatch.setattr(background_jobs, "start_job", fake_start_job)
    built = []
    real_get_engine = translate_engines.get_engine

    def fake_get_engine(name, key, *a, **k):
        built.append((name, key))
        return real_get_engine("fake", "offline")
    monkeypatch.setattr(translate_engines, "get_engine", fake_get_engine)
    env_file.write_text("BAIHE_DEEPSEEK_KEY=ds-key\n")

    translate_run_service.start_translate_run(did, engine_name="fake")
    assert captured["ollama_num_ctx_override"] is None
    assert captured["summary_engine_choice"] == "ollama"

    settings_service.set_settings({"ollama_num_ctx_override": 32768,
                                   "episode_summary_engine": "deepseek"})
    background_jobs.clear_all_jobs()
    translate_run_service.start_translate_run(did, engine_name="fake")
    assert captured["ollama_num_ctx_override"] == 32768
    assert captured["summary_engine_choice"] == "deepseek"
    assert ("deepseek", "ds-key") in built


def test_summary_engine_cloud_without_key_is_skipped(isolated_db, env_file):
    from services import translate_run_service
    settings_service.set_settings({"episode_summary_engine": "claude"})
    assert translate_run_service.pick_summary_engine() == (None, None)


def test_summary_engine_paid_pick_skipped_when_not_allowed(isolated_db, env_file, monkeypatch):
    """Security review (PR #439): a caller without engines.paid never gets a
    cloud summary on the owner's key; a free (Ollama) pick still runs."""
    from services import translate_run_service
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: object())
    env_file.write_text("BAIHE_DEEPSEEK_KEY=ds-key\n")
    settings_service.set_settings({"episode_summary_engine": "deepseek"})
    assert translate_run_service.pick_summary_engine(allow_paid=False) == (None, None)
    assert translate_run_service.pick_summary_engine()[1] == "deepseek"
    settings_service.set_settings({"episode_summary_engine": "ollama"})
    assert translate_run_service.pick_summary_engine(allow_paid=False)[1] == "ollama"


def test_translate_run_passes_allow_paid_summary_and_monthly_cap(isolated_db, env_file,
                                                                monkeypatch):
    from services import translate_run_service
    did = _seed(isolated_db)
    captured = {}

    def fake_start_job(job_id, target, *a, **k):
        captured.update(dict(zip(inspect.signature(target).parameters, a)))
        captured.update(k)
        return True
    monkeypatch.setattr(background_jobs, "start_job", fake_start_job)
    real_get_engine = translate_engines.get_engine
    monkeypatch.setattr(translate_engines, "get_engine",
                        lambda *a, **k: real_get_engine("fake", "offline"))
    env_file.write_text("BAIHE_DEEPSEEK_KEY=ds-key\n")
    settings_service.set_settings({"episode_summary_engine": "deepseek", "monthly_cap_usd": 7})

    translate_run_service.start_translate_run(did, engine_name="fake",
                                              allow_paid_summary=False)
    assert captured["summary_engine"] is None and captured["summary_engine_choice"] is None
    assert captured["summary_monthly_cap_usd"] == 7.0
    background_jobs.clear_all_jobs()
    translate_run_service.start_translate_run(did, engine_name="fake")
    assert captured["summary_engine_choice"] == "deepseek"


def test_library_bulk_translate_skips_paid_summary_when_not_allowed(isolated_db, env_file,
                                                                   monkeypatch):
    from services import library_admin_service, workspace_job_service
    did = _seed(isolated_db, status="aligned", translation_engine="ollama")
    env_file.write_text("BAIHE_DEEPSEEK_KEY=ds-key\n")
    settings_service.set_settings({"episode_summary_engine": "deepseek", "monthly_cap_usd": 3})
    starts = []

    def fake_start_job(job_id, target, *a, **k):
        starts.append((target, a, k))
        return True
    monkeypatch.setattr(background_jobs, "start_job", fake_start_job)
    library_admin_service.start_bulk_translate([did], allow_paid_summary=False)
    target, a, k = starts[-1]
    assert k["allow_paid_summary"] is False
    # The coordinator job then builds no summary engine and passes the cap on.
    starts.clear()
    target(*a, **k)
    per = [kw for t, _a, kw in starts if t is workspace_job_service.run_translate_job]
    assert per and per[0]["summary_engine"] is None
    assert per[0]["summary_monthly_cap_usd"] == 3.0


def test_new_drama_is_stamped_with_default_engine(isolated_db, env_file):
    from services import drama_service
    assert drama_service.create_drama(source_language="zh", title_en="A")["translation_engine"] \
        == "claude"
    settings_service.set_settings({"default_engine": "deepseek"})
    did = drama_service.create_drama(source_language="ja", title_en="B")["id"]
    assert isolated_db.get_drama(did)["translation_engine"] == "deepseek"
    # A preset's engine still wins.
    pid = isolated_db.insert_preset("P", translation_engine="gemini")
    did = drama_service.create_drama(source_language="zh", title_en="C", preset_id=pid)["id"]
    assert isolated_db.get_drama(did)["translation_engine"] == "gemini"


def test_default_engine_used_for_drama_without_one(isolated_db, env_file):
    from services import glossary_service, translate_run_service
    did = _seed(isolated_db)
    settings_service.set_settings({"default_engine": "ollama"})
    assert glossary_service.novel_glossary_engine(did) == "ollama"
    est = translate_run_service.estimate_translate_cost(did)
    assert est["engine"] == "ollama"


def test_bulk_library_translate_uses_default_locale_and_saved_cap(isolated_db, env_file,
                                                                  monkeypatch):
    from services import library_admin_service
    did = _seed(isolated_db, status="aligned")
    captured = {}

    def fake_start_job(job_id, target, *a, **k):
        captured.update(k)
        return True
    monkeypatch.setattr(background_jobs, "start_job", fake_start_job)
    settings_service.set_settings({"default_locale": "en-AU", "monthly_cap_usd": 5})
    library_admin_service.start_bulk_translate([did])
    assert captured["default_locale"] == "en-AU" and captured["monthly_cap"] == 5.0
    library_admin_service.start_bulk_translate([did], "en-GB")
    assert captured["default_locale"] == "en-GB"


def test_transcribe_uses_saved_tesseract_path(isolated_db, env_file, monkeypatch):
    from services import transcribe_service
    from tests.test_transcribe_service import _drama_with_video
    did, _ = _drama_with_video(isolated_db)
    captured = {}

    def fake_start_job(job_id, target, *a, **k):
        captured.update(dict(zip(inspect.signature(target).parameters, a)))
        return True
    monkeypatch.setattr(background_jobs, "start_job", fake_start_job)
    transcribe_service.start_transcribe_run(did)
    assert captured["tesseract_cmd"] is None
    settings_service.set_settings({"tesseract_cmd": "/opt/tess/bin/tesseract"})
    transcribe_service.start_transcribe_run(did)
    assert captured["tesseract_cmd"] == "/opt/tess/bin/tesseract"
    transcribe_service.start_transcribe_run(did, tesseract_cmd="/other/tesseract")
    assert captured["tesseract_cmd"] == "/other/tesseract"


def test_transcribe_job_uses_offline_whisper_folder(isolated_db, env_file, monkeypatch):
    import core as core_module
    from services import transcribe_pipeline, transcribe_service
    from tests.test_transcribe_service import _drama_with_audio
    did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
    settings_service.set_settings({"whisper_model_path": "/models/faster-whisper-small"})
    seen = {}
    monkeypatch.setattr(whisper_models, "is_whisper_model_cached", lambda *a, **k: False)
    monkeypatch.setattr(whisper_models, "load_whisper_model",
                        lambda *a, **k: seen.setdefault("load", k.get("local_model_path")))
    monkeypatch.setattr(whisper_models, "get_whisper_device_info",
                        lambda *a, **k: seen.setdefault("info", k.get("local_model_path")) and {})
    monkeypatch.setattr(whisper_models, "describe_whisper_device", lambda info: "")

    def fake_transcribe(*a, **k):
        seen["transcribe"] = k.get("local_model_path")
        raise RuntimeError("stop here")
    monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", fake_transcribe)
    captured = {}

    def fake_start_process_job(job_id, target, args=(), **k):
        captured["call"] = (target, args)
        return True
    monkeypatch.setattr(background_jobs, "start_process_job", fake_start_process_job)
    transcribe_service.start_transcribe_run(did)
    target, args = captured["call"]
    # Run the worker in this process: keep this process's group and temp dir.
    import queue
    import tempfile
    monkeypatch.setattr(background_jobs, "start_own_process_group", lambda: None)
    monkeypatch.setattr(tempfile, "tempdir", tempfile.tempdir)
    result_queue = queue.Queue()
    target(*args, result_queue)
    items = []
    while not result_queue.empty():
        items.append(result_queue.get_nowait())
    assert items[-1] == ("error", "RuntimeError", "stop here")
    assert seen == {"load": "/models/faster-whisper-small",
                    "info": "/models/faster-whisper-small",
                    "transcribe": "/models/faster-whisper-small"}


def test_novel_ocr_uses_saved_tesseract_path(isolated_db, env_file, monkeypatch):
    import io
    from services import novel_attach_service
    monkeypatch.setattr(novel_attach_service.importlib.util, "find_spec", lambda name: object())
    captured = {}

    def fake_start_job(job_id, target, *a, **k):
        captured["args"] = a
        return True
    monkeypatch.setattr(background_jobs, "start_job", fake_start_job)
    did = isolated_db.create_drama(title_zh="N", source_language="zh")
    settings_service.set_settings({"tesseract_cmd": "/opt/tesseract"})
    novel_attach_service.start_ocr_chapter(did, [("a.png", io.BytesIO(b"x"))])
    assert captured["args"][-1] == "/opt/tesseract"
    assert captured["args"][4] == "tesseract"  # backend default unchanged (Streamlit parity)


def test_url_download_passes_saved_cookies(isolated_db, env_file, monkeypatch, tmp_path):
    from services import url_media_service, ytdlp_child
    specs = []

    def fake_run_download(tmp_dir, spec, timeout, cancel):
        specs.append(spec)
        path = os.path.join(tmp_dir, "x.wav")
        open(path, "wb").close()
        yield {"event": {"path": path}}
        yield {"returncode": 0, "timed_out": False, "cancelled": False}
    monkeypatch.setattr(ytdlp_child, "run_download", fake_run_download)
    url_media_service._download("urlmedia_1", "https://example.com/v", str(tmp_path), True)
    assert specs[-1]["cookies_browser"] is None and specs[-1]["cookies_file"] is None
    assert not [k for k in ytdlp_child.ydl_options(str(tmp_path), None, None) if "cookie" in k.lower()]
    settings_service.set_settings({"cookies_browser": "chrome",
                                   "cookies_file": "/home/me/cookies.txt"})
    url_media_service._download("urlmedia_1", "https://example.com/v", str(tmp_path), True)
    assert specs[-1]["cookies_browser"] == "chrome"
    assert specs[-1]["cookies_file"] == "/home/me/cookies.txt"


def test_live_saved_cookies_only_when_asked(isolated_db, env_file, monkeypatch):
    from services import live_service
    monkeypatch.setattr(live_service, "_require_public", lambda *a, **k: None)
    monkeypatch.setattr(live_service, "_build_engine", lambda e, m: ("fake", object()))
    captured = []

    def fake_start_job(job_id, target, *a, **k):
        captured.append(k)
        return True
    monkeypatch.setattr(background_jobs, "start_job", fake_start_job)
    settings_service.set_settings({"cookies_browser": "edge"})
    live_service.start_session("https://example.com/live")
    assert "cookies_browser" not in captured[-1]
    live_service._sessions.clear()
    live_service.start_session("https://example.com/live", use_saved_cookies=True)
    assert captured[-1]["cookies_browser"] == "edge" and captured[-1]["cookies_file"] is None
    live_service._sessions.clear()


def test_live_route_uses_cookies_only_at_the_pc(isolated_db, env_file, monkeypatch):
    from services import live_service
    calls = []
    monkeypatch.setattr(live_service, "start_session",
                        lambda *a, **k: calls.append(k) or {"session_id": "live_" + "0" * 32})
    local = TestClient(create_app(ApiSettings()), base_url=LOCAL,
                       client=("127.0.0.1", 50000), raise_server_exceptions=False)
    assert local.post("/api/live/sessions", json={"url": "https://e.com/x"}).status_code == 200
    assert calls[-1]["use_saved_cookies"] is True
    remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                        raise_server_exceptions=False)
    s = _session(False, "media.import_url", "engines.paid")
    r = remote.post("/api/live/sessions", json={"url": "https://e.com/x"}, headers=_h(s))
    assert r.status_code == 200
    assert calls[-1]["use_saved_cookies"] is False


def test_cli_reads_saved_settings(isolated_db, env_file, monkeypatch):
    import cli_translate
    settings_service.set_settings({"monthly_cap_usd": 4})
    assert cli_translate._monthly_cap_setting() == 4.0
    settings_service.set_settings({"monthly_cap_usd": 0})
    assert cli_translate._monthly_cap_setting() is None

    # cli translate: engine, locale, style note, num_ctx and summary engine
    # fall back to the saved Settings values when no flag is given.
    did = _seed(isolated_db, status="aligned")
    settings_service.set_settings({"default_engine": "deepseek", "default_locale": "en-AU",
                                   "default_style_note": "Terse.",
                                   "ollama_num_ctx_override": 8192})
    engines = []
    monkeypatch.setattr(translate_engines, "get_engine",
                        lambda name, *a, **k: engines.append(name) or object())
    seen = {}

    def fake_translate(lines, engine, **k):
        seen.update(k)
        return lines, []
    monkeypatch.setattr(translate_engines, "translate_lines_with_engine", fake_translate)

    def args(**over):
        base = dict(id=did, status=None, engine=None, api_key=None, model=None,
                    style_note=None, style_preset=None, locale=None, force=False,
                    ollama_num_ctx=None)
        base.update(over)
        return types.SimpleNamespace(**base)

    with contextlib.redirect_stdout(io.StringIO()):
        cli_translate.cmd_translate(args())
    assert "deepseek" in engines  # the drama has no engine saved
    assert seen["locale"] == "en-AU" and seen["style_note"] == "Terse."
    assert seen["ollama_num_ctx_override"] == 8192
    with contextlib.redirect_stdout(io.StringIO()):
        cli_translate.cmd_translate(args(engine="fake", locale="en-GB", style_note="",
                               ollama_num_ctx=0))
    assert seen["locale"] == "en-GB" and seen["style_note"] == ""
    assert seen["ollama_num_ctx_override"] == 0


def test_baihe_own_ports_includes_configured_ports(monkeypatch, tmp_path):
    from services import settings_service as ss
    monkeypatch.setattr(ss, "default_env_path", lambda: str(tmp_path / ".env"))
    for name in (ss.API_PORT_ENV, ss.HOUSEHOLD_PORT_ENV):
        monkeypatch.delenv(name, raising=False)
    assert ss.baihe_own_ports() == {8600, 8756}
    monkeypatch.setenv(ss.API_PORT_ENV, "9123")
    monkeypatch.setenv(ss.HOUSEHOLD_PORT_ENV, " 9124 ")
    (tmp_path / ".env").write_text(f"{ss.API_PORT_ENV}=9125\n")
    assert ss.baihe_own_ports() == {8600, 8756, 9123, 9124, 9125}
    monkeypatch.setenv(ss.API_PORT_ENV, "not-a-port")
    assert 9123 not in ss.baihe_own_ports()
