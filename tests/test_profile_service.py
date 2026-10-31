"""Translation setting profiles: the saved presets widened with Translate run
options and transcribe options (services/profile_service.py), applied at New
title and edited from Library Tools."""
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import (auth_service, drama_service, profile_service, series_defaults_service,
                      translate_preset_service, translate_run_service)
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

PRESETS = "/api/translate-run/presets"
OPTIONS = {"beam_size": 7, "whisper_fast_mode": True, "translate_by_sentence": True,
           "reflect": True, "batch_size": 12, "context_window": 4, "cost_cap": 1.5,
           "style_note": "Keep honorifics.", "fallback_engines": ["deepseek"]}


def _save(name="Mine", **kw):
    args = dict(translation_engine="claude", style_preset="audio_drama", options=OPTIONS)
    args.update(kw)
    return translate_preset_service.save_translate_preset(name, **args)["preset"]


# --- what a profile may hold --------------------------------------------------

@pytest.mark.parametrize("bad", [
    {"source_language": "ja"}, {"locale": "en-GB"}, {"glossary": []}, {"language_packs": ["x"]},
    {"beam_size": 99}, {"batch_size": 0}, {"batch_size": "12"}, {"reflect": "yes"},
    {"cost_cap": -1}, {"cost_cap": True}, {"fallback_engines": ["nope"]},
    {"fallback_engines": ["claude", "deepseek", "gemini"]}, {"style_note": "x" * 4001},
    {"whisper_size": "not-a-size"}, "beam_size",
])
def test_options_refuse_language_glossary_and_bad_values(isolated_db, bad):
    with pytest.raises(InvalidInputError):
        profile_service.validate_options(bad)


def test_old_presets_without_options_still_list_and_apply(isolated_db):
    pid = db.save_preset("Old", translation_engine="gemini", style_preset="casual")
    listed = translate_preset_service.library_service.list_presets()[0]
    assert listed["options"] == {} and listed["is_default"] is False
    did = db.create_drama(title_zh="D", source_language="ko")
    applied = translate_preset_service.apply_translate_preset(did, pid)
    assert applied["options"] == {} and applied["skipped"] == []
    assert db.get_drama(did)["translation_engine"] == "gemini"


def test_a_stored_option_that_no_longer_validates_is_left_out(isolated_db):
    pid = db.save_preset("P", translation_engine="claude",
                         options_json=json.dumps({"beam_size": 7, "fallback_engines": ["gone"],
                                                  "source_language": "ja"}))
    assert profile_service.stored_options(db.list_presets()[0]) == {"beam_size": 7}
    assert pid


# --- applying ------------------------------------------------------------------

def test_create_with_a_profile_fills_the_title_but_never_the_language(isolated_db):
    p = _save(include_genre_notes=False, default_female_pronouns=True, engine_model=None)
    d = drama_service.create_drama(source_language="ko", title_en="T", preset_id=p["id"])
    row = db.get_drama(d["id"])
    assert row["source_language"] == "ko"
    assert row["translation_engine"] == "claude"
    assert (row["beam_size"], row["whisper_fast_mode"], row["translate_by_sentence"]) == (7, 1, 1)
    assert (row["include_genre_notes"], row["default_female_pronouns"]) == (0, 1)
    assert profile_service.translate_start(row) == {
        "reflect": True, "batch_size": 12, "context_window": 4, "cost_cap": 1.5,
        "style_note": "Keep honorifics.", "fallback_engines": ["deepseek"],
        "style_preset": "audio_drama"}
    assert d["profile_skipped"] == []


def test_an_option_that_does_not_suit_the_title_language_is_skipped_and_reported(isolated_db):
    p = _save(options={"asr_backend_choice": "anime_whisper", "beam_size": 6})
    ko = drama_service.create_drama(source_language="ko", title_en="K", preset_id=p["id"])
    ja = drama_service.create_drama(source_language="ja", title_en="J", preset_id=p["id"])
    assert ko["profile_skipped"] == ["asr_backend_choice"]
    assert db.get_drama(ko["id"])["asr_backend_choice"] != "anime_whisper"
    assert db.get_drama(ko["id"])["beam_size"] == 6
    assert ja["profile_skipped"] == [] and db.get_drama(ja["id"])["asr_backend_choice"] == "anime_whisper"


def test_the_profile_wins_over_series_defaults_but_unset_options_still_inherit(isolated_db):
    sid = db.create_series("Saga")
    series_defaults_service.set_defaults(None, sid, {"beam_size": 2, "min_silence_ms": 900})
    p = _save()
    d = drama_service.create_drama(source_language="ko", title_en="T", series_id=sid,
                                   preset_id=p["id"])
    row = db.get_drama(d["id"])
    assert row["beam_size"] == 7           # the profile
    assert row["min_silence_ms"] == 900    # only the series set it


def test_apply_to_an_existing_title_writes_the_same_and_starts_nothing(isolated_db, monkeypatch):
    from jobs import job_launch
    monkeypatch.setattr(job_launch, "start_job", lambda *a, **k: pytest.fail("no job"))
    p = _save()
    did = db.create_drama(title_zh="D", source_language="ja", translation_engine="fake_mt")
    out = translate_preset_service.apply_translate_preset(did, p["id"])
    row = db.get_drama(did)
    assert row["source_language"] == "ja" and row["beam_size"] == 7
    assert out["options"]["batch_size"] == 12
    assert translate_run_service.get_translate_config(did)["translate_start"]["batch_size"] == 12


def test_a_title_without_a_profile_has_an_empty_translate_start(isolated_db):
    did = db.create_drama(title_zh="D")
    assert translate_run_service.get_translate_config(did)["translate_start"] == {}


# --- edit, rename, default ----------------------------------------------------

def test_edit_replaces_fields_and_name_and_keeps_id_and_default(isolated_db):
    p = _save()
    translate_preset_service.set_default_preset(p["id"], True)
    out = translate_preset_service.update_translate_preset(
        p["id"], "Renamed", "gemini", style_preset="subtitle", options={"beam_size": 3})["preset"]
    assert out["id"] == p["id"] and out["name"] == "Renamed"
    assert out["translation_engine"] == "gemini" and out["options"] == {"beam_size": 3}
    assert out["is_default"] is True


def test_edit_errors(isolated_db):
    a, b = _save("A"), _save("B")
    with pytest.raises(ConflictError):
        translate_preset_service.update_translate_preset(b["id"], "A", "claude")
    with pytest.raises(NotFoundError):
        translate_preset_service.update_translate_preset(9999, "Z", "claude")
    with pytest.raises(InvalidInputError):
        translate_preset_service.update_translate_preset(a["id"], "A", "claude",
                                                         options={"locale": "en-GB"})
    assert a["id"] != b["id"]


def test_only_one_default_at_a_time(isolated_db):
    a, b = _save("A"), _save("B")
    translate_preset_service.set_default_preset(a["id"], True)
    translate_preset_service.set_default_preset(b["id"], True)
    assert [p["name"] for p in translate_preset_service.library_service.list_presets()
            if p["is_default"]] == ["B"]
    translate_preset_service.set_default_preset(b["id"], False)
    assert not any(p["is_default"] for p in translate_preset_service.library_service.list_presets())
    with pytest.raises(NotFoundError):
        translate_preset_service.set_default_preset(9999, True)


def test_overwrite_save_keeps_the_default_flag(isolated_db):
    p = _save()
    translate_preset_service.set_default_preset(p["id"], True)
    _save(overwrite=True, options={"beam_size": 2})
    assert db.list_presets()[0]["is_default"] == 1


def test_api_edit_and_default_round_trip(client):
    body = {"name": "Mine", "translation_engine": "claude", "options": OPTIONS}
    pid = client.post(PRESETS, json=body).json()["preset"]["id"]
    r = client.post(f"{PRESETS}/{pid}/update", json={**body, "name": "Edited", "options": {"beam_size": 4}})
    assert r.status_code == 200 and r.json()["preset"]["name"] == "Edited"
    assert r.json()["preset"]["options"] == {"beam_size": 4}
    r = client.post(f"{PRESETS}/{pid}/default", json={"is_default": True})
    assert r.status_code == 200 and r.json()["is_default"] is True
    listed = client.get("/api/library/presets").json()["items"]
    assert listed[0]["is_default"] is True and listed[0]["options"] == {"beam_size": 4}


@pytest.mark.parametrize("path,body", [
    ("update", {"name": "X", "translation_engine": "claude", "options": {"source_language": "ja"}}),
    ("update", {"name": "X", "translation_engine": "claude", "bogus": 1}),
    ("default", {"is_default": "yes"}),
])
def test_api_422(client, path, body):
    pid = _save()["id"]
    assert client.post(f"{PRESETS}/{pid}/{path}", json=body).status_code == 422


def test_api_404(client):
    assert client.post(f"{PRESETS}/999/update",
                       json={"name": "X", "translation_engine": "claude"}).status_code == 404
    assert client.post(f"{PRESETS}/999/default", json={"is_default": True}).status_code == 404


def test_edit_is_pc_only_and_admin_only_when_auth_on(isolated_db):
    pid = _save()["id"]
    body = {"name": "X", "translation_engine": "claude"}
    admin = auth_service.grant_admin_local("admin@example.com")
    s = auth_service.create_session(admin["id"])
    h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
         api_auth.CSRF_HEADER: s["csrf_token"]}
    remote = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="http://192.168.1.50:8600",
                        client=("192.168.1.50", 5000), raise_server_exceptions=False)
    assert remote.post(f"{PRESETS}/{pid}/update", json=body).status_code == 401
    assert remote.post(f"{PRESETS}/{pid}/update", json=body, headers=h).status_code == 403
    assert db.list_presets()[0]["name"] == "Mine"
    assert remote.post(f"{PRESETS}/{pid}/default", json={"is_default": True}, headers=h).status_code == 200
    local = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="http://127.0.0.1:8600",
                       client=("127.0.0.1", 5000), raise_server_exceptions=False)
    assert local.post(f"{PRESETS}/{pid}/update", json=body, headers=h).status_code == 200


def test_profile_fields_route_lists_engines_styles_and_only_allowed_options(client):
    r = client.get("/api/translate-run/profile-fields")
    assert r.status_code == 200, r.text
    body = r.json()
    keys = {f["key"] for f in body["fields"]}
    assert "beam_size" in keys and "translate_by_sentence" in keys
    assert not keys & {"translation_engine", "include_genre_notes", "default_female_pronouns"}
    assert any(e["name"] == "claude" for e in body["engines"])
    assert {s["key"] for s in body["style_presets"]} >= {"audio_drama", "subtitle"}


# --- review fixes -------------------------------------------------------------

def test_a_huge_cost_cap_is_a_422_not_a_500(client):
    body = {"name": "Big", "translation_engine": "claude"}
    r = client.post(PRESETS, content=json.dumps({**body, "options": {"cost_cap": 10 ** 400}}),
                    headers={"content-type": "application/json"})
    assert r.status_code == 422, r.text
    with pytest.raises(InvalidInputError):
        profile_service.validate_options({"cost_cap": 10 ** 400})


def test_clearing_one_default_leaves_the_others_alone(isolated_db):
    a, b = _save("A"), _save("B")
    translate_preset_service.set_default_preset(a["id"], True)
    # Only a manual edit can leave two flags; clearing one must still touch only that row.
    import contextlib
    with contextlib.closing(db.get_conn()) as conn:
        conn.execute("UPDATE presets SET is_default = 1")
        conn.commit()
    translate_preset_service.set_default_preset(a["id"], False)
    flags = {p["name"]: p["is_default"] for p in translate_preset_service.library_service.list_presets()}
    assert flags == {"A": False, "B": True}
    assert b["id"] != a["id"]


def test_a_profile_deleted_mid_request_is_a_404_not_a_crash(isolated_db, monkeypatch):
    p = _save()
    monkeypatch.setattr(translate_preset_service.library_service, "list_presets", lambda: [])
    with pytest.raises(NotFoundError):
        translate_preset_service.set_default_preset(p["id"], True)
    with pytest.raises(NotFoundError):
        translate_preset_service.update_translate_preset(p["id"], "X", "claude")


def test_an_old_preset_at_create_writes_the_engine_only(isolated_db):
    sid = db.create_series("Saga")
    series_defaults_service.set_defaults(None, sid, {"default_female_pronouns": True, "beam_size": 2})
    pid = db.save_preset("Old", translation_engine="gemini", style_preset="subtitle",
                         default_female_pronouns=False, include_genre_notes=False)
    d = drama_service.create_drama(source_language="ko", title_en="T", series_id=sid, preset_id=pid)
    row = db.get_drama(d["id"])
    assert row["translation_engine"] == "gemini"
    assert row["default_female_pronouns"] == 1 and row["beam_size"] == 2   # the series' defaults
    assert profile_service.translate_start(row) == {
        "style_preset": "subtitle", "default_female_pronouns": False, "include_genre_notes": False}


def test_an_old_preset_applied_to_a_title_leaves_its_toggles_and_options(isolated_db):
    pid = db.save_preset("Old", translation_engine="gemini", default_female_pronouns=False)
    did = db.create_drama(title_zh="D", source_language="ko", default_female_pronouns=1, beam_size=3)
    out = translate_preset_service.apply_translate_preset(did, pid)
    row = db.get_drama(did)
    assert row["translation_engine"] == "gemini"
    assert row["default_female_pronouns"] == 1 and row["beam_size"] == 3
    assert out["default_female_pronouns"] is False   # still returned for the form


def test_a_profile_with_no_options_still_writes_its_toggles(isolated_db):
    p = translate_preset_service.save_translate_preset(
        "Bare", "claude", default_female_pronouns=True)["preset"]
    d = drama_service.create_drama(source_language="ko", title_en="T", preset_id=p["id"])
    assert db.get_drama(d["id"])["default_female_pronouns"] == 1


@pytest.mark.parametrize("module", ["services.transcribe_options_service", "services.library_service",
                                    "services.drama_service", "services.profile_service"])
def test_each_module_imports_first_without_a_cycle(module):
    """series_defaults_service builds its settings from transcribe_options_service at import,
    so nothing that module reaches may import profile_service at the top."""
    import subprocess
    import sys
    r = subprocess.run([sys.executable, "-c", f"import {module}"], capture_output=True, text=True, timeout=120)
    assert r.returncode == 0, r.stderr[-400:]


def test_quick_video_options_are_validated_and_never_applied_to_a_title():
    options = {"quick_source_language": "ja", "quick_export_format": "vtt",
               "quick_export_language": "bilingual"}
    assert profile_service.validate_options(options) == options
    for bad in ({"quick_source_language": "en"}, {"quick_export_format": "ass"},
                {"quick_export_language": "fr"}, {"quick_language_packs": ["nope"]},
                {"quick_source_language": "ja", "quick_language_packs": "ja-honorifics"}):
        with pytest.raises(InvalidInputError):
            profile_service.validate_options(bad)
    preset = {"translation_engine": "ollama", "options_json": profile_service.dump_options(options)}
    planned = profile_service.plan(preset, {"source_language": "ko"})
    assert "source_language" not in planned["columns"]
    assert not any(k.startswith("quick_") for k in planned["columns"]["translate_start_json"])
    assert profile_service.stored_options(preset) == options
