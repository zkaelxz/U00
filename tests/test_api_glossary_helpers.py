"""
Glossary helpers (parity X10/X28): glossary from the drama's source lines
(`glossary_service.start_lines_glossary_run` + /api/glossary/dramas/{id}/
from-lines[/apply]) and per-term edits (overrides) on both applies.
Service and routes against an isolated library; keys, the engine and the
LLM call are faked -- no network.
"""

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
from services import auth_service, glossary_service as gs, settings_service, translate_service
from services.service_errors import (ConflictError, DependencyUnavailableError,
                                     InvalidInputError, UnsupportedOperationError)

SECRET = "sk-ant-api03-SECRETSECRETSECRETSECRET"
REMOTE = "https://baihe.example.com"
_PREFIXES = ("lines_glossary_", "novel_glossary_")


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
            "error": error, "started_at": time.time(), "finished_at": None,
            "cancel_requested": False}


def _wait(job_id):
    for _ in range(200):
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error", "cancelled"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


class _Engine:
    supports_reference = True
    model = "fake-model"


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, *a: SECRET)
    built = {}
    monkeypatch.setattr(gs.translate_engines, "get_engine",
                        lambda name, key, **kw: built.update(name=name, key=key) or _Engine())
    return built


def _lines_drama(db, zh=("青云宗的师尊", "  ", "沈清来了"), engine="claude", series=True):
    sid = db.get_or_create_series("S") if series else None
    did = db.create_drama(title_en="L", series_id=sid, translation_engine=engine)
    db.save_lines(did, [core.Line(idx=i, start=float(i), end=i + 1.0, zh=z)
                        for i, z in enumerate(zh)])
    return did, sid


RUN = "run-1"
PROPOSALS = {"run_id": RUN, "proposals": [
    {"term": "师尊", "suggested_translation": "Teacher", "category": "title",
     "policy": "translate", "reason": "r1", "already_in_glossary": True},
    {"term": "青云宗", "suggested_translation": "Qingyun Sect", "category": "sect",
     "policy": "hybrid", "reason": "r2", "already_in_glossary": False},
    {"term": "空", "suggested_translation": "", "category": None, "policy": None,
     "reason": "", "already_in_glossary": False}]}


# ----- service ------------------------------------------------------------------

class TestLinesGlossaryService:
    def test_paid_exposure_and_engine(self, isolated_db):
        assert "start_lines_glossary_run" in gs.PAID_ENGINE_FUNCTIONS
        did, _ = _lines_drama(isolated_db, engine="deepseek")
        assert gs.lines_glossary_engine(did) == "deepseek"

    def test_job_blocks_drama_delete(self):
        assert "lines_glossary_" in background_jobs.DRAMA_JOB_PREFIXES
        _put_job(gs.lines_glossary_job_id(987654), status="running", progress=0.1)
        assert background_jobs.any_job_running_for_drama(987654)

    def test_run_uses_source_lines_and_hides_key(self, isolated_db, monkeypatch, fake_engine):
        did, sid = _lines_drama(isolated_db)
        isolated_db.upsert_glossary_term(sid, "师尊", "Master")
        seen, usage = {}, []
        monkeypatch.setattr(isolated_db, "log_usage", lambda *a: usage.append(a))

        def fake_extract(lines, engine, **kw):
            seen.update(lines=list(lines), lang=kw["source_language"],
                        known=[t["term_original"] for t in kw["known_terms"]])
            kw["usage_cb"](10, 5)
            return [{"term": "青云宗", "suggested_translation": "Qingyun Sect",
                     "category": "sect", "policy": "hybrid", "reason": "sect"},
                    {"term": 5}, "junk",
                    {"term": "师尊", "suggested_translation": "Teacher", "category": "title",
                     "policy": "translate", "reason": ""},
                    {"term": "沈清", "suggested_translation": "Shen Qing", "category": "bogus",
                     "policy": "bogus", "reason": ""}]
        monkeypatch.setattr(tguide, "extract_terms_llm", fake_extract)
        out = gs.start_lines_glossary_run(did)
        assert out == {"job_id": f"lines_glossary_{did}", "engine": "claude", "line_count": 2}
        assert fake_engine["key"] == SECRET and SECRET not in repr(out)
        st = _wait(out["job_id"])
        assert st["status"] == "done", st.get("error")
        assert seen == {"lines": ["青云宗的师尊", "沈清来了"], "lang": "zh", "known": ["师尊"]}
        assert usage and usage[0][0] == did and usage[0][3] == "extract_terms"
        props = {p["term"]: p for p in st["result"]["proposals"]}
        assert set(props) == {"青云宗", "师尊", "沈清"}
        assert props["师尊"]["already_in_glossary"] and not props["青云宗"]["already_in_glossary"]
        assert props["沈清"]["category"] is None and props["沈清"]["policy"] is None
        status = gs.get_lines_glossary_status(did)
        assert status["status"] == "done" and len(status["result"]["proposals"]) == 3
        assert SECRET not in repr(status)

    def test_engine_error_redacted(self, isolated_db, monkeypatch, fake_engine):
        did, _ = _lines_drama(isolated_db)

        def boom(*a, **kw):
            raise RuntimeError(f"401 for key {SECRET}")
        monkeypatch.setattr(tguide, "extract_terms_llm", boom)
        st = _wait(gs.start_lines_glossary_run(did)["job_id"])
        assert st["status"] == "error" and SECRET not in repr(st)
        assert SECRET not in repr(gs.get_lines_glossary_status(did))

    def test_cancel_during_call_drops_result(self, isolated_db, monkeypatch, fake_engine):
        did, _ = _lines_drama(isolated_db)
        job_id = gs.lines_glossary_job_id(did)

        def extract(*a, **kw):
            background_jobs.request_cancel(job_id)
            return [{"term": "青云宗", "suggested_translation": "Qingyun Sect"}]
        monkeypatch.setattr(tguide, "extract_terms_llm", extract)
        st = _wait(gs.start_lines_glossary_run(did)["job_id"])
        assert st["status"] == "cancelled" and not st.get("result")

    def test_start_refusals(self, isolated_db, monkeypatch, fake_engine):
        nos, _ = _lines_drama(isolated_db, series=False)
        with pytest.raises(UnsupportedOperationError):
            gs.start_lines_glossary_run(nos)
        empty, _ = _lines_drama(isolated_db, zh=(" ",))
        with pytest.raises(UnsupportedOperationError):
            gs.start_lines_glossary_run(empty)
        did, _ = _lines_drama(isolated_db)
        with pytest.raises(ConflictError):
            gs.start_lines_glossary_run(did, engine_name="ollama")
        nllb, _ = _lines_drama(isolated_db, engine="nllb")
        with pytest.raises(UnsupportedOperationError):
            gs.start_lines_glossary_run(nllb)
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda *a: None)
        with pytest.raises(DependencyUnavailableError):
            gs.start_lines_glossary_run(did)

    def test_monthly_cap_used_up_refuses_paid_not_free(self, isolated_db, monkeypatch,
                                                       fake_engine):
        started = []
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda *a, **k: started.append(a) or True)
        monkeypatch.setattr(settings_service, "resolve_key",
                            lambda k, *a: "5" if k == "monthly_cap_usd" else None)
        monkeypatch.setattr(isolated_db, "get_month_spend", lambda *a: 9.0)
        did, _ = _lines_drama(isolated_db)
        with pytest.raises(UnsupportedOperationError, match="spending cap"):
            gs.start_lines_glossary_run(did)
        assert started == []
        free, _ = _lines_drama(isolated_db, engine="ollama")
        assert gs.start_lines_glossary_run(free)["engine"] == "ollama"

    def test_novel_run_monthly_cap_used_up_refuses_paid_not_free(self, isolated_db, monkeypatch,
                                                                 fake_engine):
        import os
        started = []
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda *a, **k: started.append(a) or True)
        monkeypatch.setattr(settings_service, "resolve_key",
                            lambda k, *a: "5" if k == "monthly_cap_usd" else None)
        monkeypatch.setattr(isolated_db, "get_month_spend", lambda *a: 9.0)

        def novel_drama(engine):
            did, _ = _lines_drama(isolated_db, engine=engine)
            with open(os.path.join(isolated_db.drama_dir(did), gs.RAW_NOVEL_FILENAME), "w",
                      encoding="utf-8") as f:
                f.write("沈清来了。青云宗的师尊。")
            return did
        with pytest.raises(UnsupportedOperationError, match="spending cap"):
            gs.start_novel_glossary_run(novel_drama("claude"))
        assert started == []
        assert gs.start_novel_glossary_run(novel_drama("ollama"))["engine"] == "ollama"
        assert len(started) == 1

    def test_duplicate_run_conflict(self, isolated_db, monkeypatch, fake_engine):
        did, _ = _lines_drama(isolated_db)
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: False)
        with pytest.raises(ConflictError):
            gs.start_lines_glossary_run(did)

    def test_status_not_resident_is_idle(self, isolated_db):
        did, _ = _lines_drama(isolated_db)
        assert gs.get_lines_glossary_status(did) == {
            "job_id": "", "status": "idle", "progress": 0.0, "message": "", "result": None,
            "run_id": None}
        with pytest.raises(gs.NotFoundError):
            gs.get_lines_glossary_status(999)

    def test_apply_by_term_with_overrides(self, isolated_db):
        did, sid = _lines_drama(isolated_db)
        isolated_db.upsert_glossary_term(sid, "师尊", "Shizun (user)", enforce_exact=True)
        _put_job(gs.lines_glossary_job_id(did), result=PROPOSALS)
        rep = gs.apply_lines_glossary(
            did, ["空", "青云宗", "不存在", "师尊"], run_id=RUN,
            overrides={"青云宗": {"translation": "Azure Cloud Sect", "policy": "translate_meaning"},
                       "空": {"translation": "Void"},
                       "未选": {"translation": "ignored"}})
        assert rep == {"added": ["空", "青云宗"], "overwritten": [],
                       "skipped_existing": ["师尊"], "unknown": ["不存在"]}
        terms = {t["term_original"]: t for t in isolated_db.list_glossary_terms(sid)}
        assert terms["青云宗"]["term_translation"] == "Azure Cloud Sect"
        assert terms["青云宗"]["policy"] == "translate_meaning" and terms["青云宗"]["category"] == "sect"
        assert terms["空"]["term_translation"] == "Void"
        assert "未选" not in terms
        assert terms["师尊"]["term_translation"] == "Shizun (user)"

        rep = gs.apply_lines_glossary(did, ["师尊"], overwrite_existing=True, run_id=RUN,
                                      overrides={"师尊": {"translation": "Master"}})
        assert rep["overwritten"] == ["师尊"]
        t = {t["term_original"]: t for t in isolated_db.list_glossary_terms(sid)}["师尊"]
        assert t["term_translation"] == "Master" and t["enforce_exact"]

    def test_novel_apply_takes_overrides_too(self, isolated_db):
        did, sid = _lines_drama(isolated_db)
        _put_job(gs.novel_glossary_job_id(did), result=PROPOSALS)
        rep = gs.apply_novel_glossary(did, ["青云宗"],
                                      overrides={"青云宗": {"category": "place"}})
        assert rep["added"] == ["青云宗"]
        t = isolated_db.list_glossary_terms(sid)[0]
        assert t["category"] == "place" and t["term_translation"] == "Qingyun Sect"

    @pytest.mark.parametrize("overrides", [
        {"青云宗": {"translation": ""}}, {"青云宗": {"translation": 5}},
        {"青云宗": {"category": "bogus"}}, {"青云宗": {"policy": "bogus"}},
        {"青云宗": {"notes": "x"}}, {"青云宗": "Qingyun"}, ["青云宗"],
        {"青云宗": {"translation": "x" * 201}}])
    def test_bad_overrides_refused_before_write(self, isolated_db, overrides):
        did, sid = _lines_drama(isolated_db)
        _put_job(gs.lines_glossary_job_id(did), result=PROPOSALS)
        with pytest.raises(InvalidInputError):
            gs.apply_lines_glossary(did, ["青云宗"], overrides=overrides, run_id=RUN)
        assert isolated_db.list_glossary_terms(sid) == []

    def test_apply_refusals(self, isolated_db):
        did, _ = _lines_drama(isolated_db)
        # the lines apply always names the run it reviewed
        with pytest.raises(InvalidInputError, match="run_id"):
            gs.apply_lines_glossary(did, ["x"])
        # no run held (app restarted): the reviewed run is gone
        with pytest.raises(ConflictError):
            gs.apply_lines_glossary(did, ["x"], run_id=RUN)
        # the novel run's result is not the lines run's
        _put_job(gs.novel_glossary_job_id(did), result=PROPOSALS)
        with pytest.raises(ConflictError):
            gs.apply_lines_glossary(did, ["青云宗"], run_id=RUN)
        for bad in ([], "x", [1]):
            with pytest.raises(InvalidInputError):
                gs.apply_lines_glossary(did, bad, run_id=RUN)

    def test_stale_run_refused_and_nothing_written(self, isolated_db):
        """The run was replaced (another tab or device extracted again) after
        the user reviewed it: 409-class refusal, the glossary is untouched --
        for both extractions, with or without overrides or overwrite."""
        did, sid = _lines_drama(isolated_db)
        isolated_db.upsert_glossary_term(sid, "师尊", "Shizun (user)")
        newer = {**PROPOSALS, "run_id": "run-2"}
        for job_id, apply in ((gs.lines_glossary_job_id(did), gs.apply_lines_glossary),
                              (gs.novel_glossary_job_id(did), gs.apply_novel_glossary)):
            _put_job(job_id, result=newer)
            for kw in ({}, {"overrides": {"青云宗": {"translation": "Azure"}}},
                       {"overwrite_existing": True}):
                with pytest.raises(ConflictError, match="review again"):
                    apply(did, ["青云宗", "师尊"], run_id=RUN, **kw)
            # a run that's running again has no proposals to apply either
            _put_job(job_id, status="running", result=None, progress=0.2)
            with pytest.raises(ConflictError):
                apply(did, ["青云宗"], run_id=RUN)
        terms = isolated_db.list_glossary_terms(sid)
        assert [(t["term_original"], t["term_translation"]) for t in terms] == [
            ("师尊", "Shizun (user)")]

    def test_status_names_the_run(self, isolated_db, monkeypatch, fake_engine):
        did, _ = _lines_drama(isolated_db)
        monkeypatch.setattr(tguide, "extract_terms_llm", lambda *a, **kw: [])
        _wait(gs.start_lines_glossary_run(did)["job_id"])
        first = gs.get_lines_glossary_status(did)["run_id"]
        _wait(gs.start_lines_glossary_run(did)["job_id"])
        second = gs.get_lines_glossary_status(did)
        assert first and second["run_id"] and second["run_id"] != first
        assert background_jobs.get_status(gs.lines_glossary_job_id(did))["result"]["run_id"] \
            == second["run_id"]
        # a run held without a run_id (older result) reads as unknown
        _put_job(gs.lines_glossary_job_id(did), result={"proposals": []})
        assert gs.get_lines_glossary_status(did)["run_id"] is None


# ----- routes -------------------------------------------------------------------

@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _error(resp):
    body = resp.json()
    assert set(body) == {"error"}, body
    return body["error"]


def _gl(did, tail=""):
    return f"/api/glossary/dramas/{did}/from-lines{tail}"


class TestLinesGlossaryRoutes:
    def test_start_status_and_apply(self, client, isolated_db, monkeypatch, fake_engine):
        did, sid = _lines_drama(isolated_db)
        monkeypatch.setattr(tguide, "extract_terms_llm", lambda *a, **kw: [
            {"term": "青云宗", "suggested_translation": "Qingyun Sect", "category": "sect",
             "policy": "hybrid", "reason": "r"}])
        idle = client.get(_gl(did))
        assert idle.status_code == 200 and idle.json()["status"] == "idle"
        r = client.post(_gl(did))
        assert r.status_code == 200, r.text
        assert r.json() == {"job_id": f"lines_glossary_{did}", "engine": "claude",
                            "line_count": 2}
        _wait(f"lines_glossary_{did}")
        r = client.get(_gl(did))
        assert r.status_code == 200 and SECRET not in r.text
        body = r.json()
        assert body["status"] == "done" and body["run_id"]
        assert set(body["proposals"][0]) == {"term", "suggested_translation", "category",
                                             "policy", "reason", "already_in_glossary"}
        r = client.post(_gl(did, "/apply"), json={
            "terms": ["青云宗"], "overrides": {"青云宗": {"translation": "Azure Cloud Sect"}},
            "run_id": body["run_id"]})
        assert r.status_code == 200, r.text
        assert r.json()["added"] == ["青云宗"]
        assert isolated_db.list_glossary_terms(sid)[0]["term_translation"] == "Azure Cloud Sect"

    def test_error_status_never_contains_key(self, client, isolated_db, monkeypatch,
                                             fake_engine):
        did, _ = _lines_drama(isolated_db)

        def boom(*a, **kw):
            raise RuntimeError(f"401 for key {SECRET}")
        monkeypatch.setattr(tguide, "extract_terms_llm", boom)
        client.post(_gl(did))
        _wait(f"lines_glossary_{did}")
        r = client.get(_gl(did))
        assert r.json()["status"] == "error" and SECRET not in r.text

    def test_refusals(self, client, isolated_db, monkeypatch, fake_engine):
        nos, _ = _lines_drama(isolated_db, series=False)
        assert client.post(_gl(nos)).status_code == 400
        empty, _ = _lines_drama(isolated_db, zh=())
        r = client.post(_gl(empty))
        assert r.status_code == 400 and _error(r)["code"] == "unsupported_operation"
        did, _ = _lines_drama(isolated_db)
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: False)
        assert client.post(_gl(did)).status_code == 409
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda *a: None)
        assert client.post(_gl(did)).status_code == 503

    def test_overwrite_needs_confirm(self, client, isolated_db):
        did, sid = _lines_drama(isolated_db)
        isolated_db.upsert_glossary_term(sid, "师尊", "Shizun (user)")
        _put_job(f"lines_glossary_{did}", result=PROPOSALS)
        r = client.post(_gl(did, "/apply"), json={"terms": ["师尊"], "overwrite_existing": True,
                                                  "run_id": RUN})
        assert r.status_code == 422 and _error(r)["code"] == "validation_error"
        assert isolated_db.list_glossary_terms(sid)[0]["term_translation"] == "Shizun (user)"
        r = client.post(_gl(did, "/apply"), json={"terms": ["师尊"], "overwrite_existing": True,
                                                  "confirm": True, "run_id": RUN})
        assert r.status_code == 200 and r.json()["overwritten"] == ["师尊"]

    @pytest.mark.parametrize("body", [
        {"terms": []}, {"terms": [1]}, {},
        {"terms": ["青云宗"], "overrides": {"青云宗": {"translation": ""}}},
        {"terms": ["青云宗"], "overrides": {"青云宗": {"notes": "x"}}},
        {"terms": ["青云宗"], "overrides": {"青云宗": {"category": "bogus"}}},
        {"terms": ["青云宗"], "overrides": {"青云宗": {"translation": None}}},
        {"terms": ["青云宗"], "overrides": ["青云宗"]},
        {"terms": ["青云宗"], "run_id": ""}, {"terms": ["青云宗"], "run_id": 5},
        {"terms": ["青云宗"], "run_id": "x" * 65}])
    @pytest.mark.parametrize("kind", ["from-lines", "from-novel"])
    def test_bad_body_422(self, client, isolated_db, body, kind):
        did, sid = _lines_drama(isolated_db)
        _put_job(f"{kind.replace('from-', '')}_glossary_{did}", result=PROPOSALS)
        r = client.post(f"/api/glossary/dramas/{did}/{kind}/apply", json=body)
        assert r.status_code == 422, r.text
        assert isolated_db.list_glossary_terms(sid) == []

    def test_lines_apply_requires_run_id(self, client, isolated_db):
        did, sid = _lines_drama(isolated_db)
        _put_job(f"lines_glossary_{did}", result=PROPOSALS)
        r = client.post(_gl(did, "/apply"), json={"terms": ["青云宗"]})
        assert r.status_code == 422 and _error(r)["code"] == "validation_error"
        assert isolated_db.list_glossary_terms(sid) == []

    @pytest.mark.parametrize("kind", ["from-lines", "from-novel"])
    def test_stale_run_409_nothing_written(self, client, isolated_db, kind):
        did, sid = _lines_drama(isolated_db)
        _put_job(f"{kind.replace('from-', '')}_glossary_{did}",
                 result={**PROPOSALS, "run_id": "run-2"})
        path = f"/api/glossary/dramas/{did}/{kind}"
        assert client.get(path).json()["run_id"] == "run-2"
        r = client.post(f"{path}/apply", json={
            "terms": ["青云宗"], "overrides": {"青云宗": {"translation": "Azure"}},
            "run_id": RUN})
        assert r.status_code == 409, r.text
        assert _error(r)["message"] == gs.PROPOSALS_CHANGED
        assert isolated_db.list_glossary_terms(sid) == []
        r = client.post(f"{path}/apply", json={"terms": ["青云宗"], "run_id": "run-2"})
        assert r.status_code == 200 and r.json()["added"] == ["青云宗"]

    def test_novel_apply_accepts_overrides(self, client, isolated_db):
        did, sid = _lines_drama(isolated_db)
        _put_job(f"novel_glossary_{did}", result=PROPOSALS)
        r = client.post(f"/api/glossary/dramas/{did}/from-novel/apply", json={
            "terms": ["青云宗"], "overrides": {"青云宗": {"translation": "Azure"}}})
        assert r.status_code == 200, r.text
        assert isolated_db.list_glossary_terms(sid)[0]["term_translation"] == "Azure"

    @pytest.mark.parametrize("method,path,body", [
        ("post", _gl(999), None), ("get", _gl(999), None),
        ("post", _gl(999, "/apply"), {"terms": ["x"], "run_id": RUN})])
    def test_unknown_drama_404(self, client, isolated_db, method, path, body):
        kw = {"json": body} if body is not None else {}
        r = getattr(client, method)(path, **kw)
        assert r.status_code == 404 and _error(r)["code"] == "not_found"


# ----- auth on ------------------------------------------------------------------

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


class TestRunScopedCancel:
    @pytest.mark.parametrize("kind", ["from-lines", "from-novel"])
    def test_cancel_only_stops_the_named_run(self, client, isolated_db, kind):
        import threading
        did, _ = _lines_drama(isolated_db)
        job_id = f"{kind.replace('from-', '')}_glossary_{did}"
        release = threading.Event()

        def target(jid, run_id):
            for _ in range(200):
                if release.is_set() or background_jobs.is_cancel_requested(jid):
                    return
                time.sleep(0.02)
        path = f"/api/glossary/dramas/{did}/{kind}"
        try:
            assert gs._start_extraction_job(job_id, target)
            run = gs._current_run_id(job_id, background_jobs.get_status(job_id))
            # a Cancel pressed for an older run leaves the current one running
            r = client.post(f"{path}/cancel", json={"run_id": "older-run"})
            assert r.status_code == 409, r.text
            assert not background_jobs.is_cancel_requested(job_id)
            r = client.post(f"{path}/cancel", json={"run_id": run})
            assert r.status_code == 200, r.text
            assert r.json()["cancel_requested"] is True
            assert background_jobs.is_cancel_requested(job_id)
        finally:
            release.set()
            _wait(job_id)
        # finished: nothing left to cancel
        assert client.post(f"{path}/cancel", json={"run_id": run}).status_code == 409

    @pytest.mark.parametrize("body", [{}, {"run_id": ""}, {"run_id": "x" * 65},
                                      {"run_id": "a", "extra": 1}])
    def test_cancel_needs_a_run_id(self, client, isolated_db, body):
        did, _ = _lines_drama(isolated_db)
        assert client.post(_gl(did, "/cancel"), json=body).status_code == 422

    def test_unknown_drama_404(self, client, isolated_db):
        assert client.post(_gl(999, "/cancel"), json={"run_id": "r"}).status_code == 404


class TestAuthOn:
    def test_no_session_401(self, isolated_db):
        c = _remote()
        did, _ = _lines_drama(isolated_db)
        for method, path in (("post", _gl(did)), ("get", _gl(did)), ("post", _gl(did, "/apply"))):
            assert getattr(c, method)(path).status_code == 401

    def test_missing_permission_403(self, isolated_db):
        c = _remote()
        did, _ = _lines_drama(isolated_db)
        u = auth_service.add_user("bare@example.com")
        for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
            auth_service.revoke_permission(u["id"], p)
        s = auth_service.create_session(u["id"], "pytest", "203.0.113.9")
        h = {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
             api_auth.CSRF_HEADER: s["csrf_token"]}
        assert c.post(_gl(did), headers=h).status_code == 403
        assert c.get(_gl(did), headers=h).status_code == 403
        assert c.post(_gl(did, "/apply"), json={"terms": ["x"]}, headers=h).status_code == 403

    @pytest.mark.parametrize("engine", ["claude", "deepseek", "gemini", None])
    def test_household_refused_paid_engine(self, isolated_db, fake_engine, monkeypatch, engine):
        started = []
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda *a, **k: started.append(a) or True)
        c = _remote()
        did, _ = _lines_drama(isolated_db, engine=engine)
        assert c.post(_gl(did), headers=_session()).status_code == 403
        assert started == []
        r = c.post(_gl(did), headers=_session("paid@example.com", "engines.paid"))
        assert r.status_code == 200, r.text
        assert SECRET not in r.text

    def test_household_allowed_ollama(self, isolated_db, fake_engine, monkeypatch):
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: True)
        did, _ = _lines_drama(isolated_db, engine="ollama")
        r = _remote().post(_gl(did), headers=_session())
        assert r.status_code == 200, r.text

    def test_engine_switched_after_gate_is_409(self, isolated_db, monkeypatch):
        did, _ = _lines_drama(isolated_db, engine="ollama")
        resolved, started = [], []
        monkeypatch.setattr(translate_service, "resolve_api_key",
                            lambda name, *a: resolved.append(name) or SECRET)
        monkeypatch.setattr(background_jobs, "start_job",
                            lambda *a, **k: started.append(a) or True)
        real = gs.lines_glossary_engine

        def gate_then_switch(drama_id):
            name = real(drama_id)
            isolated_db.update_drama(drama_id, translation_engine="claude")
            return name
        monkeypatch.setattr(gs, "lines_glossary_engine", gate_then_switch)
        r = _remote().post(_gl(did), headers=_session())
        assert r.status_code == 409, r.text
        assert resolved == [] and started == []
