"""A job that would replace existing text or speakers needs `lines.edit` on
top of the route's `jobs.start`; the same job on nothing to overwrite
needs `jobs.start` only. The four jobs: a transcribe run on a title with
lines, diarization with overwrite_manual, a translate run with
force_retranslate, and fix-flagged. Fully mocked: no job thread starts."""
import contextlib
import os

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
from core import Line
from services import (auth_service, diarization_service, ownership_service,
                      review_jobs_service, settings_service, transcribe_service,
                      translate_run_service, translate_service)
from services.service_errors import ForbiddenError

NO_EDIT = ("library.read", "lines.read", "jobs.start")
WITH_EDIT = NO_EDIT + ("lines.edit",)


class FakeEngine:
    model = "fake-model"
    supports_reference = True
    last_usage = {"input_tokens": 0, "output_tokens": 0}

    def translate_batch(self, texts, ctx=None):
        return [f"EN:{t}" for t in texts]


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    background_jobs.clear_all_jobs()
    monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: True)
    monkeypatch.setattr(background_jobs, "start_process_job", lambda *a, **k: True)
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **k: "hf-token")
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda *a, **k: "k")
    yield
    background_jobs.clear_all_jobs()


@contextlib.contextmanager
def _acting(perms):
    token = ownership_service.bind_request()
    ownership_service.note_acting_principal(
        {"user_id": 7, "is_admin": False, "is_local_owner": False, "permissions": list(perms)})
    try:
        yield
    finally:
        ownership_service.unbind_request(token)


def _drama(rows=(), **fields):
    did = db.create_drama(title_zh="D", audio_filename="audio.wav", **fields)
    os.makedirs(db.drama_dir(did), exist_ok=True)
    open(os.path.join(db.drama_dir(did), "audio.wav"), "wb").close()
    if rows:
        db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=zh, en=en, flag=flag)
                            for i, (zh, en, flag) in enumerate(rows)])
    return did


def _transcribe(rows):
    did = _drama(rows)
    return lambda: transcribe_service.start_transcribe_run(did, transcript_text="你好")


def _diarize(overwrite):
    did = _drama([("你好", "hi", None)])
    return lambda: diarization_service.start_diarization_run(
        did, overwrite_manual=overwrite, confirm=overwrite)


def _translate(force):
    did = _drama([("你好", "hi", None), ("再见", "", None)])
    return lambda: translate_run_service.start_translate_run(
        did, engine_name="fake", force_retranslate=force)


def _review(fix_flagged, monkeypatch):
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())
    did = _drama([("你好", "hi", "check"), ("再见", "bye", None)])
    start = review_jobs_service.start_fix_flagged if fix_flagged \
        else review_jobs_service.start_flag_review
    return lambda: start(did, engine_name="claude")


def _cases(monkeypatch):
    """(name, overwriting start, non-destructive start) per job."""
    return [
        ("transcribe", _transcribe([("旧", "old", None)]), _transcribe(())),
        ("diarize", _diarize(True), _diarize(False)),
        ("translate", _translate(True), _translate(False)),
        ("review", _review(True, monkeypatch), _review(False, monkeypatch)),
    ]


def test_without_lines_edit_only_the_non_destructive_variant_starts(monkeypatch):
    for name, overwrite, safe in _cases(monkeypatch):
        with _acting(NO_EDIT):
            with pytest.raises(ForbiddenError):
                overwrite()
            assert safe(), name


def test_lines_edit_holder_starts_both_variants(monkeypatch):
    for name, overwrite, safe in _cases(monkeypatch):
        with _acting(WITH_EDIT):
            assert overwrite(), name
            assert safe(), name


def test_no_principal_is_the_local_owner_and_starts_both(monkeypatch):
    for name, overwrite, safe in _cases(monkeypatch):
        assert overwrite(), name
        assert safe(), name


def test_translate_force_over_untranslated_lines_only_needs_jobs_start():
    did = _drama([("你好", "", None)])
    with _acting(NO_EDIT):
        assert translate_run_service.start_translate_run(
            did, engine_name="fake", force_retranslate=True)


# --- through the API with sign-in on: the route fills the acting principal ---

def _session(email, perms):
    u = auth_service.add_user(email)
    for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
        if p not in perms:
            auth_service.revoke_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_api_refuses_each_overwrite_without_lines_edit(monkeypatch):
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())
    client = TestClient(create_app(ApiSettings(auth_mode="on")),
                        base_url="https://baihe.example.com", raise_server_exceptions=False)
    member = _session("kid@example.com", NO_EDIT)
    did = _drama([("你好", "hi", "check")])
    requests = [
        (f"/api/transcribe/dramas/{did}/run", {"transcript_text": "你好"}),
        (f"/api/diarization/dramas/{did}/run?overwrite_manual=true&confirm=true", None),
        (f"/api/translate-run/dramas/{did}/run", {"engine": "ollama", "force_retranslate": True}),
        (f"/api/review-jobs/dramas/{did}/fix-flagged", {"engine": "ollama"}),
    ]
    for url, body in requests:
        r = client.post(url, json=body, headers=member)
        assert r.status_code == 403, (url, r.text)
        assert r.json()["error"]["code"] == "forbidden"
    r = client.post(f"/api/diarization/dramas/{did}/run",
                    headers=_session("editor@example.com", WITH_EDIT))
    assert r.status_code == 200, r.text
