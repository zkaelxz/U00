"""POST /api/queue/after-import/{id}: who may queue the translating after a chapter import, and with which engine."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, model_readiness_service, page_run_service, scanlate_pages_service


@pytest.fixture
def away(isolated_db, monkeypatch):
    monkeypatch.setattr(model_readiness_service, "require_ready", lambda *a, **kw: None)
    monkeypatch.setattr(scanlate_pages_service, "default_engine", lambda *a, **kw: "claude")
    monkeypatch.setattr(page_run_service, "require_cap_for_after_import", lambda engine, model: None)
    calls = []
    monkeypatch.setattr(page_run_service, "start_after_import",
                        lambda principal, did, engine, lines, **kw: calls.append((did, engine, lines, kw))
                        or {"state": "waiting", "step": "follow", "reason": None, "job_id": None})
    client = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                        raise_server_exceptions=False)
    client.calls = calls
    return client


def _member(email="kid@example.com", *perms):
    u = auth_service.add_user(email)
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return u["id"], {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                     api_auth.CSRF_HEADER: s["csrf_token"]}


def test_a_member_on_a_phone_gets_the_engine_every_account_may_use_for_a_comic(away):
    uid, h = _member()
    did = db.create_drama(title_en="Comic", media_type="manhua", owner_user_id=uid)
    assert away.post(f"/api/queue/after-import/{did}", headers=h).status_code == 200
    assert [(c[0], c[1]) for c in away.calls] == [(did, "ollama")]    # the dearer default was swapped out


def test_an_engine_cannot_be_chosen_by_the_client(away):
    uid, h = _member()
    did = db.create_drama(title_en="Comic", media_type="manhua", owner_user_id=uid)
    r = away.post(f"/api/queue/after-import/{did}", headers=h, json={"engine": "claude"})
    assert r.status_code == 200 and away.calls[0][1] == "ollama"


def test_a_member_on_a_phone_queues_a_novel_with_the_engines_every_account_may_use(away, monkeypatch):
    from services import glossary_extract_service, narration_service
    monkeypatch.setattr(glossary_extract_service, "novel_glossary_engine", lambda did: "claude")
    uid, h = _member()
    did = db.create_drama(title_en="Novel", media_type="novel", owner_user_id=uid)
    r = away.post(f"/api/queue/after-import/{did}", headers=h)
    assert r.status_code == 200, r.text
    _, engine, lines, kw = away.calls[0]
    assert engine == "ollama" and lines["engine"] == "ollama" and kw["names"]["engine"] == "ollama"
    assert narration_service.DEFAULT_ENGINE == "claude"      # the dearer default that was swapped out


def test_another_members_title_is_not_found_and_the_right_is_needed(away):
    owner_id, _ = _member("owner@example.com")
    did = db.create_drama(title_en="Theirs", media_type="manhua", owner_user_id=owner_id)
    _, other = _member("other@example.com")
    assert away.post(f"/api/queue/after-import/{did}", headers=other).status_code == 404
    assert away.get(f"/api/queue/after-import/{did}", headers=other).status_code == 404
    _, no_jobs = _member("nojobs@example.com")
    auth_service.revoke_permission(auth_service.find_user_by_email("nojobs@example.com")["id"], "jobs.start")
    assert away.post(f"/api/queue/after-import/{did}", headers=no_jobs).status_code == 403
    assert away.calls == []
