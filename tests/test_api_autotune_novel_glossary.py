"""
Route batch 2C: /api/transcribe/dramas/{id}/autotune[/apply] and
/api/glossary/dramas/{id}/from-novel[/apply], over the #361 services.
FastAPI TestClient against an isolated library; job starters, keys and the
LLM call are faked -- no audio model, no network.
"""

import os
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import core
import translation_guide as tguide
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, glossary_service, transcribe_service, translate_service
from services.service_errors import ConflictError

SECRET = "sk-ant-api03-SECRETSECRETSECRETSECRET"
REMOTE = "https://baihe.example.com"
_PREFIXES = ("autotune_", "novel_glossary_")


def _clear_jobs():
    with background_jobs._lock:
        for jid in [j for j in background_jobs._jobs if j.startswith(_PREFIXES)]:
            background_jobs._jobs.pop(jid, None)


@pytest.fixture(autouse=True)
def _no_leftover_jobs():
    _clear_jobs()
    yield
    _clear_jobs()


def _put_job(job_id, status="done", result=None, message="", error=None, progress=1.0):
    with background_jobs._lock:
        background_jobs._jobs[job_id] = {
            "status": status, "progress": progress, "message": message, "result": result,
            "error": error, "started_at": time.time(), "finished_at": None}


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})  # as the React client sends


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    return body["error"]


def _audio_drama(db, **fields):
    did = db.create_drama(title_en="D", audio_filename="audio.wav", **fields)
    ddir = db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, "audio.wav"), "wb").close()
    return did


def _novel_drama(db, orig="原文", novel="English", engine="claude", series=True):
    sid = db.get_or_create_series("S") if series else None
    did = db.create_drama(title_en="N", series_id=sid, translation_engine=engine,
                          novel_reference_filename="novel.txt" if novel else None)
    ddir = db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    if orig:
        with open(os.path.join(ddir, "raw_novel_context.txt"), "w", encoding="utf-8") as f:
            f.write(orig)
    if novel:
        with open(os.path.join(ddir, "novel.txt"), "w", encoding="utf-8") as f:
            f.write(novel)
    return did, sid


class _Engine:
    supports_reference = True
    model = "fake-model"


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, *a: SECRET)
    monkeypatch.setattr(glossary_service.translate_engines, "get_engine",
                        lambda name, key, **kw: _Engine())


@pytest.fixture
def fake_process_job(monkeypatch):
    started = {}
    monkeypatch.setattr(background_jobs, "start_process_job",
                        lambda job_id, target, args=(), **kw: started.update(
                            job_id=job_id, args=args) or True)
    return started


def _at(did, tail=""):
    return f"/api/transcribe/dramas/{did}/autotune{tail}"


def _gl(did, tail=""):
    return f"/api/glossary/dramas/{did}/from-novel{tail}"


SCORES = {"results": [{"candidate_ms": 300, "long_lines": 2, "total_lines": 9},
                      {"candidate_ms": 800, "long_lines": 0, "total_lines": 12}],
          "best_candidate_ms": 800}


# ----- auto-tune ---------------------------------------------------------------

class TestAutotuneStart:
    def test_success_default_and_explicit_candidates(self, client, isolated_db,
                                                     fake_process_job):
        did = _audio_drama(isolated_db)
        r = client.post(_at(did), json={})
        assert r.status_code == 200, r.text
        assert r.json() == {"job_id": f"autotune_{did}",
                            "candidates": list(core.DEFAULT_AUTOTUNE_CANDIDATES_MS)}
        r = client.post(_at(did), json={"candidates": [300, 1500], "initial_prompt": "人名"})
        assert r.status_code == 200
        assert r.json()["candidates"] == [300, 1500]
        assert fake_process_job["args"][5] == "人名"
        assert isolated_db.drama_dir(did) not in r.text

    @pytest.mark.parametrize("body", [
        {"candidates": []}, {"candidates": [200]}, {"candidates": [3001]},
        {"candidates": [300, 400, 500, 600, 700, 800, 900]}, {"candidates": [True]},
        {"candidates": ["300"]}, {"candidates": [300, 300]}, {"initial_prompt": "x" * 1001},
        {"bogus": 1}])
    def test_bad_input_422(self, client, isolated_db, fake_process_job, body):
        did = _audio_drama(isolated_db)
        r = client.post(_at(did), json=body)
        assert r.status_code == 422
        assert _error(r)["code"] == "validation_error"
        assert fake_process_job == {}

    def test_no_audio_400(self, client, isolated_db):
        did = isolated_db.create_drama(title_en="silent")
        r = client.post(_at(did), json={})
        assert r.status_code == 400
        assert _error(r)["code"] == "unsupported_operation"

    def test_duplicate_409(self, client, isolated_db, monkeypatch):
        did = _audio_drama(isolated_db)
        monkeypatch.setattr(background_jobs, "start_process_job", lambda *a, **k: False)
        r = client.post(_at(did), json={})
        assert r.status_code == 409

    def test_no_paid_engine(self):
        assert transcribe_service.PAID_ENGINE_FUNCTIONS == ()


class TestAutotuneStatus:
    def test_not_resident_404(self, client, isolated_db):
        did = _audio_drama(isolated_db)
        assert client.get(_at(did)).status_code == 404

    def test_running_then_done(self, client, isolated_db):
        did = _audio_drama(isolated_db)
        _put_job(f"autotune_{did}", status="running", progress=0.5,
                 message="Testing candidate 2 of 4 (800ms)...")
        body = client.get(_at(did)).json()
        assert body == {"job_id": f"autotune_{did}", "status": "running", "progress": 0.5,
                        "message": "Testing candidate 2 of 4 (800ms)...", "results": None,
                        "best_candidate_ms": None}
        _put_job(f"autotune_{did}", result=SCORES)
        body = client.get(_at(did)).json()
        assert body["status"] == "done"
        assert body["results"] == SCORES["results"]
        assert body["best_candidate_ms"] == 800

    def test_error_message_redacted(self, client, isolated_db):
        did = _audio_drama(isolated_db)
        _put_job(f"autotune_{did}", status="error", error=f"RuntimeError: bad token {SECRET}")
        r = client.get(_at(did))
        assert r.status_code == 200
        assert r.json()["status"] == "error"
        assert SECRET not in r.text


class TestAutotuneApply:
    def test_refused_before_finished_run(self, client, isolated_db):
        did = _audio_drama(isolated_db)
        assert client.post(_at(did, "/apply"), json={"candidate_ms": 800}).status_code == 400
        _put_job(f"autotune_{did}", status="running", progress=0.2)
        assert client.post(_at(did, "/apply"), json={"candidate_ms": 800}).status_code == 400

    def test_refused_for_unmeasured_value(self, client, isolated_db):
        did = _audio_drama(isolated_db)
        _put_job(f"autotune_{did}", result=SCORES)
        r = client.post(_at(did, "/apply"), json={"candidate_ms": 1500})
        assert r.status_code == 422   # the service's InvalidInputError
        for bad in (200, True, "800"):
            assert client.post(_at(did, "/apply"),
                               json={"candidate_ms": bad}).status_code == 422

    def test_changes_only_min_silence_ms(self, client, isolated_db):
        did = _audio_drama(isolated_db, beam_size=8, vad_threshold=0.6)
        other = _audio_drama(isolated_db)
        isolated_db.update_drama(other, min_silence_ms=500)
        _put_job(f"autotune_{did}", result=SCORES)
        before = isolated_db.get_drama(did)
        r = client.post(_at(did, "/apply"), json={"candidate_ms": 800})
        assert r.status_code == 200, r.text
        assert r.json()["min_silence_ms"] == 800 and r.json()["drama_id"] == did
        after = isolated_db.get_drama(did)
        assert {k for k in after if after[k] != before[k]} <= {"min_silence_ms", "updated_at"}
        assert isolated_db.get_drama(other)["min_silence_ms"] == 500


# ----- glossary from novel -------------------------------------------------------

def _wait(job_id):
    for _ in range(200):
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


class TestNovelGlossaryStart:
    def test_success_and_status_hide_key(self, client, isolated_db, monkeypatch, fake_engine):
        did, _ = _novel_drama(isolated_db)
        monkeypatch.setattr(tguide, "extract_glossary_from_novel", lambda *a, **kw: [
            {"term": "青云宗", "suggested_translation": "Qingyun Sect", "category": "sect",
             "policy": "hybrid", "reason": "a sect"}])
        r = client.post(_gl(did))
        assert r.status_code == 200, r.text
        assert r.json() == {"job_id": f"novel_glossary_{did}", "engine": "claude",
                            "paired": True}
        _wait(f"novel_glossary_{did}")
        r = client.get(_gl(did))
        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "done"
        assert [p["term"] for p in body["proposals"]] == ["青云宗"]
        assert set(body["proposals"][0]) == {"term", "suggested_translation", "category",
                                             "policy", "reason", "already_in_glossary"}
        assert isolated_db.drama_dir(did) not in r.text

    def test_status_never_contains_key(self, client, isolated_db, monkeypatch, fake_engine):
        did, _ = _novel_drama(isolated_db)
        monkeypatch.setattr(tguide, "extract_glossary_from_novel", lambda *a, **kw: [
            {"term": "师尊", "suggested_translation": "Master", "reason": "r"}])
        client.post(_gl(did))
        _wait(f"novel_glossary_{did}")
        r = client.get(_gl(did))
        assert r.status_code == 200 and SECRET not in r.text

    def test_error_status_never_contains_key(self, client, isolated_db, monkeypatch,
                                             fake_engine):
        did, _ = _novel_drama(isolated_db)

        def boom(*a, **kw):
            raise RuntimeError(f"401 for key {SECRET}")
        monkeypatch.setattr(tguide, "extract_glossary_from_novel", boom)
        client.post(_gl(did))
        _wait(f"novel_glossary_{did}")
        r = client.get(_gl(did))
        assert r.json()["status"] == "error"
        assert SECRET not in r.text

    def test_no_series_or_no_novel_400(self, client, isolated_db, fake_engine):
        nos, _ = _novel_drama(isolated_db, series=False)
        assert client.post(_gl(nos)).status_code == 400
        none, _ = _novel_drama(isolated_db, orig=None, novel=None)
        r = client.post(_gl(none))
        assert r.status_code == 400
        assert _error(r)["code"] == "unsupported_operation"

    def test_no_key_503(self, client, isolated_db, monkeypatch):
        did, _ = _novel_drama(isolated_db)
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda *a: None)
        r = client.post(_gl(did))
        assert r.status_code == 503
        assert _error(r)["code"] == "dependency_unavailable"

    def test_duplicate_409(self, client, isolated_db, monkeypatch, fake_engine):
        did, _ = _novel_drama(isolated_db)
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: False)
        assert client.post(_gl(did)).status_code == 409

    def test_status_not_resident_404(self, client, isolated_db):
        did, _ = _novel_drama(isolated_db)
        assert client.get(_gl(did)).status_code == 404


PROPOSALS = {"proposals": [
    {"term": "师尊", "suggested_translation": "Teacher", "category": "title",
     "policy": "translate", "reason": "r1", "already_in_glossary": True},
    {"term": "青云宗", "suggested_translation": "Qingyun Sect", "category": "sect",
     "policy": "hybrid", "reason": "r2", "already_in_glossary": False}]}


class TestNovelGlossaryApply:
    def test_by_text_and_overwrite_needs_confirm(self, client, isolated_db):
        did, sid = _novel_drama(isolated_db)
        isolated_db.upsert_glossary_term(sid, "师尊", "Shizun (user)")
        _put_job(f"novel_glossary_{did}", result=PROPOSALS)
        r = client.post(_gl(did, "/apply"), json={"terms": ["不存在", "青云宗", "师尊"]})
        assert r.status_code == 200, r.text
        assert r.json() == {"added": ["青云宗"], "overwritten": [],
                            "skipped_existing": ["师尊"], "unknown": ["不存在"]}

        r = client.post(_gl(did, "/apply"), json={"terms": ["师尊"], "overwrite_existing": True})
        assert r.status_code == 422
        assert _error(r)["code"] == "validation_error"
        terms = {t["term_original"]: t for t in isolated_db.list_glossary_terms(sid)}
        assert terms["师尊"]["term_translation"] == "Shizun (user)"

        r = client.post(_gl(did, "/apply"), json={"terms": ["师尊"], "overwrite_existing": True,
                                                  "confirm": True})
        assert r.status_code == 200
        assert r.json()["overwritten"] == ["师尊"]

    @pytest.mark.parametrize("body", [{"terms": []}, {"terms": ["x"] * 1001}, {"terms": [1]},
                                      {"terms": [""]}, {"terms": ["x"], "confirm": "yes"}, {}])
    def test_bad_body_422(self, client, isolated_db, body):
        did, _ = _novel_drama(isolated_db)
        _put_job(f"novel_glossary_{did}", result=PROPOSALS)
        assert client.post(_gl(did, "/apply"), json=body).status_code == 422

    def test_refused_before_finished_run(self, client, isolated_db):
        did, _ = _novel_drama(isolated_db)
        assert client.post(_gl(did, "/apply"), json={"terms": ["x"]}).status_code == 400


# ----- unknown drama ---------------------------------------------------------------

@pytest.mark.parametrize("method,path,body", [
    ("post", _at(999), {}), ("get", _at(999), None),
    ("post", _at(999, "/apply"), {"candidate_ms": 800}),
    ("post", _gl(999), None), ("get", _gl(999), None),
    ("post", _gl(999, "/apply"), {"terms": ["x"]})])
def test_unknown_drama_404(client, isolated_db, method, path, body):
    kw = {"json": body} if body is not None else {}
    r = getattr(client, method)(path, **kw)
    assert r.status_code == 404
    assert _error(r)["code"] == "not_found"


# ----- auth on -------------------------------------------------------------------

def _remote():
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                      raise_server_exceptions=False)


def _session(email="kid@example.com", *extra):
    u = auth_service.add_user(email)
    for p in extra:
        auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


class TestAuthOn:
    def test_no_session_401(self, isolated_db):
        c = _remote()
        did, _ = _novel_drama(isolated_db)
        for method, path in (("post", _at(did)), ("get", _at(did)), ("post", _at(did, "/apply")),
                             ("post", _gl(did)), ("get", _gl(did)),
                             ("post", _gl(did, "/apply"))):
            assert getattr(c, method)(path).status_code == 401

    def test_missing_permission_403(self, isolated_db):
        c = _remote()
        did, _ = _novel_drama(isolated_db)
        u = auth_service.add_user("bare@example.com")
        for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
            auth_service.revoke_permission(u["id"], p)
        s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
        h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
             api_auth.CSRF_HEADER: s["csrf_token"]}
        assert c.post(_at(did), json={}, headers=h).status_code == 403
        assert c.get(_at(did), headers=h).status_code == 403
        assert c.post(_at(did, "/apply"), json={"candidate_ms": 800}, headers=h).status_code == 403
        assert c.post(_gl(did), headers=h).status_code == 403
        assert c.get(_gl(did), headers=h).status_code == 403
        assert c.post(_gl(did, "/apply"), json={"terms": ["x"]}, headers=h).status_code == 403

    @pytest.mark.parametrize("engine", ["claude", "deepseek", "gemini", None])
    def test_household_refused_paid_engine(self, isolated_db, fake_engine, monkeypatch, engine):
        started = []
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda *a, **k: started.append(a) or True)
        c = _remote()
        did, _ = _novel_drama(isolated_db, engine=engine)
        assert c.post(_gl(did), headers=_session()).status_code == 403
        assert started == []
        r = c.post(_gl(did), headers=_session("paid@example.com", "engines.paid"))
        assert r.status_code == 200, r.text
        assert SECRET not in r.text

    def test_household_allowed_ollama(self, isolated_db, fake_engine, monkeypatch):
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: True)
        c = _remote()
        did, _ = _novel_drama(isolated_db, engine="ollama")
        r = c.post(_gl(did), headers=_session())
        assert r.status_code not in (401, 403), r.text

    def test_engine_switched_after_gate_is_409(self, isolated_db, monkeypatch):
        """The gate sees ollama; the stored engine becomes claude before the
        run starts (e.g. a bulk resume). The run must not go to claude."""
        did, _ = _novel_drama(isolated_db, engine="ollama")
        resolved, started = [], []
        monkeypatch.setattr(translate_service, "resolve_api_key",
                            lambda name, *a: resolved.append(name) or SECRET)
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda *a, **k: started.append(a) or True)
        real = glossary_service.novel_glossary_engine

        def gate_then_switch(drama_id):
            name = real(drama_id)
            isolated_db.update_drama(drama_id, translation_engine="claude")
            return name
        monkeypatch.setattr(glossary_service, "novel_glossary_engine", gate_then_switch)
        r = _remote().post(_gl(did), headers=_session())
        assert r.status_code == 409, r.text
        assert resolved == [] and started == []

    def test_service_uses_only_the_checked_engine(self, isolated_db, fake_engine, monkeypatch):
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: True)
        did, _ = _novel_drama(isolated_db, engine="claude")
        with pytest.raises(ConflictError):
            glossary_service.start_novel_glossary_run(did, engine_name="ollama")
        assert glossary_service.start_novel_glossary_run(
            did, engine_name="claude")["engine"] == "claude"

    def test_household_can_autotune_and_apply(self, isolated_db, fake_process_job):
        c = _remote()
        did = _audio_drama(isolated_db)
        h = _session()
        assert c.post(_at(did), json={}, headers=h).status_code == 200
        _put_job(f"autotune_{did}", result=SCORES)
        assert c.get(_at(did), headers=h).status_code == 200
        assert c.post(_at(did, "/apply"), json={"candidate_ms": 800},
                      headers=h).status_code == 200
