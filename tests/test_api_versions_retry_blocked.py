"""
Review parity R39 (activate a saved translation version) and R10 (retry a
content-blocked line): services/translation_version_service.py,
services/blocked_retry_service.py and their routers. isolated_db + fakes;
no network, no models, no real engines.
"""
import os
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
import translate_engines
from api import auth as api_auth
from api import llm_slots
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import auth_service, blocked_retry_service, translate_service
from services import translation_version_service as tvs
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

FAKE_KEY = "sk-ant-THISISAFAKEKEY1234567890"
REMOTE = "https://baihe.example.com"


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _seed(flag=None, flag_note=""):
    did = db.create_drama(title_en="D", source_language="zh")
    db.save_lines(did, [
        Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello", speaker="A"),
        Line(idx=1, start=1.0, end=2.0, zh="再见", en="", flag=flag, flag_note=flag_note),
    ])
    return did, [r["id"] for r in db.load_lines(did)]


def _no_leak(resp, *needles):
    text = resp.text
    assert db.LIBRARY_DIR not in text and FAKE_KEY not in text
    assert os.sep + "dramas" + os.sep not in text
    for n in needles:
        assert n not in text


def _hold_job(job_id):
    """A running background job that waits until released."""
    gate = threading.Event()
    assert background_jobs.start_job(job_id, lambda: gate.wait(5))
    return gate


# ---------------------------------------------------------------------------
# R39: activate a translation version
# ---------------------------------------------------------------------------

def _version_over_current(did, ens, label="v-old", active=False):
    lines = db.load_line_objects(did)
    for ln, en in zip(lines, ens):
        ln.en = en
    return db.save_translation_version(did, lines, label, "claude", "m", make_active=active)


def _act(did, vid):
    return f"/api/review/dramas/{did}/versions/{vid}/activate"


class TestActivateService:
    def test_writes_only_en_and_marks_active(self, isolated_db):
        did, ids = _seed(flag="uncertain", flag_note="hm")
        vid = _version_over_current(did, ["Hi there", "Goodbye"])
        newer = _version_over_current(did, ["Hello", ""], label="v-new", active=True)
        db.update_line_fields_if(did, ids[0], {"speaker": "B"}, {})
        out = tvs.activate_version(did, vid, confirm=True)
        assert out == {"drama_id": did, "version_id": vid, "label": "v-old", "activated": True,
                       "lines_changed": 2}
        rows = {r["id"]: r for r in db.load_lines(did)}
        assert [rows[i]["en"] for i in ids] == ["Hi there", "Goodbye"]
        # untouched: speaker set after the version, flag, timing, ids
        assert rows[ids[0]]["speaker"] == "B"
        assert rows[ids[1]]["flag"] == "uncertain" and rows[ids[1]]["flag_note"] == "hm"
        assert sorted(rows) == sorted(ids)
        active = {v["id"]: v["is_active"] for v in db.list_translation_versions(did)}
        assert active[vid] == 1 and active[newer] == 0
        assert any(h["label"] == tvs.SNAPSHOT_LABEL for h in db.list_line_history(did))

    def test_uses_field_scoped_save_only(self, isolated_db, monkeypatch):
        did, _ids = _seed()
        vid = _version_over_current(did, ["X", "Y"])
        calls = []
        real = db.save_lines
        monkeypatch.setattr(db, "save_lines",
                            lambda d, lines, fields=None: (calls.append(fields),
                                                           real(d, lines, fields=fields)))
        tvs.activate_version(did, vid, confirm=True)
        assert calls == [("en",)]

    def test_needs_confirm(self, isolated_db):
        did, _ = _seed()
        vid = _version_over_current(did, ["X", "Y"])
        with pytest.raises(InvalidInputError):
            tvs.activate_version(did, vid, confirm=False)
        assert db.load_lines(did)[0]["en"] == "Hello"

    def test_other_dramas_version_is_404(self, isolated_db):
        did, _ = _seed()
        other, _ = _seed()
        vid = _version_over_current(other, ["X", "Y"])
        with pytest.raises(NotFoundError):
            tvs.activate_version(did, vid, confirm=True)
        with pytest.raises(NotFoundError):
            tvs.activate_version(999999, vid, confirm=True)

    def test_restructured_since_is_refused(self, isolated_db):
        did, ids = _seed()
        vid = _version_over_current(did, ["X", "Y"])
        # a line added since: the full-sync path the tab would take is refused
        db.save_lines(did, db.load_line_objects(did)
                      + [Line(idx=2, start=2.0, end=3.0, zh="新", en="New")])
        with pytest.raises(ConflictError):
            tvs.activate_version(did, vid, confirm=True)
        assert [r["en"] for r in db.load_lines(did)] == ["Hello", "", "New"]
        assert not any(v["is_active"] for v in db.list_translation_versions(did))


class TestActivateApi:
    def test_success(self, client):
        did, ids = _seed()
        vid = _version_over_current(did, ["Hi", "Bye"])
        r = client.post(_act(did, vid), json={"confirm": True})
        assert r.status_code == 200, r.text
        assert r.json()["lines_changed"] == 2 and r.json()["activated"] is True
        _no_leak(r)

    def test_confirm_missing_or_loose_is_422(self, client):
        did, _ = _seed()
        vid = _version_over_current(did, ["Hi", "Bye"])
        assert client.post(_act(did, vid), json={}).status_code == 422
        assert client.post(_act(did, vid), json={"confirm": "true"}).status_code == 422
        assert client.post(_act(did, vid), json={"confirm": True, "x": 1}).status_code == 422
        assert db.load_lines(did)[0]["en"] == "Hello"

    def test_404(self, client):
        did, _ = _seed()
        assert client.post(_act(did, 424242), json={"confirm": True}).status_code == 404
        assert client.post(_act(999999, 1), json={"confirm": True}).status_code == 404

    def test_409_while_a_line_writing_job_runs(self, client):
        did, _ = _seed()
        vid = _version_over_current(did, ["Hi", "Bye"])
        gate = _hold_job(f"translate_{did}")
        try:
            r = client.post(_act(did, vid), json={"confirm": True})
            assert r.status_code == 409 and r.json()["error"]["code"] == "conflict"
            assert db.load_lines(did)[0]["en"] == "Hello"
        finally:
            gate.set()

    def test_409_when_lines_restructured(self, client):
        did, _ = _seed()
        vid = _version_over_current(did, ["Hi", "Bye"])
        db.save_lines(did, db.load_line_objects(did)[:1])
        assert client.post(_act(did, vid), json={"confirm": True}).status_code == 409


# ---------------------------------------------------------------------------
# R10: retry a content-blocked line
# ---------------------------------------------------------------------------

class FakeEngine:
    def __init__(self, behaviour, model="fake-1"):
        self.behaviour = behaviour
        self.model = model
        self.calls = []

    def translate_batch(self, zh_lines, context):
        self.calls.append((list(zh_lines), dict(context)))
        return self.behaviour(zh_lines, context)


def _use_engine(monkeypatch, engine, key=FAKE_KEY):
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: key)
    seen = {}

    def fake_get_engine(name, api_key, model=None, **kw):
        seen.update(name=name, api_key=api_key, model=model, **kw)
        return engine
    monkeypatch.setattr(translate_engines, "get_engine", fake_get_engine)
    return seen


def _retry(did, lid):
    return f"/api/lines/dramas/{did}/lines/{lid}/retry-blocked"


def _blocked():
    return _seed(flag="content_blocked", flag_note="claude: refusal")


class TestRetryService:
    def test_success_writes_en_and_clears_flag(self, isolated_db, monkeypatch):
        did, ids = _blocked()
        eng = FakeEngine(lambda zh, ctx: ["Goodbye"])
        seen = _use_engine(monkeypatch, eng)
        out = blocked_retry_service.retry_blocked_line(did, ids[1], "deepseek")
        assert out["retried"] is True and out["blocked"] is False
        assert out["line"]["en"] == "Goodbye" and out["line"]["flag"] is None
        assert out["line"]["flag_note"] == ""
        # the engine is told the permanent line id, and the source language
        assert eng.calls == [(["再见"], {"source_language": "zh", "line_ids": [ids[1]]})]
        assert seen["name"] == "deepseek"
        other = next(r for r in db.load_lines(did) if r["id"] == ids[0])
        assert other["en"] == "Hello"

    def test_blocked_again_updates_note_only(self, isolated_db, monkeypatch):
        did, ids = _blocked()

        def block(zh, ctx):
            raise translate_engines.ContentModerationBlocked("gemini", f"SAFETY {FAKE_KEY}")
        _use_engine(monkeypatch, FakeEngine(block))
        out = blocked_retry_service.retry_blocked_line(did, ids[1], "gemini")
        assert out["retried"] is False and out["blocked"] is True
        assert FAKE_KEY not in out["reason"] and out["reason"].startswith("SAFETY")
        row = next(r for r in db.load_lines(did) if r["id"] == ids[1])
        assert row["flag"] == "content_blocked" and row["en"] == ""
        assert row["flag_note"] == "gemini: " + out["reason"]

    def test_uses_saved_free_tier_and_default_model(self, isolated_db, monkeypatch):
        did, ids = _blocked()
        monkeypatch.setattr(blocked_retry_service.settings_service, "get_gemini_free_tier",
                            lambda: True)
        seen = _use_engine(monkeypatch, FakeEngine(lambda zh, ctx: ["Goodbye"]))
        blocked_retry_service.retry_blocked_line(did, ids[1], "gemini")
        assert seen["model"] is None and seen["free_tier"] is True

    def test_flag_changed_during_call_is_409(self, isolated_db, monkeypatch):
        did, ids = _blocked()

        def dismiss_meanwhile(zh, ctx):
            db.update_line_fields_if(did, ids[1], {"flag": None, "flag_note": ""}, {})
            raise translate_engines.ContentModerationBlocked("gemini", "SAFETY")
        _use_engine(monkeypatch, FakeEngine(dismiss_meanwhile))
        with pytest.raises(ConflictError):
            blocked_retry_service.retry_blocked_line(did, ids[1], "gemini")
        row = next(r for r in db.load_lines(did) if r["id"] == ids[1])
        assert row["flag"] is None and row["flag_note"] == ""

    def test_409_while_translate_job_runs_before_engine_built(self, isolated_db, monkeypatch):
        did, ids = _blocked()
        built = []
        monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: built.append(a))
        gate = _hold_job(f"translate_{did}")
        try:
            with pytest.raises(ConflictError):
                blocked_retry_service.retry_blocked_line(did, ids[1], "test_offline")
        finally:
            gate.set()
        assert built == []

    @pytest.mark.parametrize("answer", [[], ["a", "b"], [""], None])
    def test_wrong_shaped_answer_writes_nothing(self, isolated_db, monkeypatch, answer):
        did, ids = _blocked()
        _use_engine(monkeypatch, FakeEngine(lambda zh, ctx: answer))
        with pytest.raises(Exception) as exc:
            blocked_retry_service.retry_blocked_line(did, ids[1], "claude")
        assert not isinstance(exc.value, (ConflictError, NotFoundError))
        row = next(r for r in db.load_lines(did) if r["id"] == ids[1])
        assert row["en"] == "" and row["flag"] == "content_blocked"

    def test_edit_during_call_is_409_nothing_written(self, isolated_db, monkeypatch):
        did, ids = _blocked()

        def edit_meanwhile(zh, ctx):
            db.update_line_fields_if(did, ids[1], {"en": "typed by user"}, {})
            return ["Engine text"]
        _use_engine(monkeypatch, FakeEngine(edit_meanwhile))
        with pytest.raises(ConflictError):
            blocked_retry_service.retry_blocked_line(did, ids[1], "claude")
        row = next(r for r in db.load_lines(did) if r["id"] == ids[1])
        assert row["en"] == "typed by user" and row["flag"] == "content_blocked"

    def test_note_edit_during_call_is_409_nothing_written(self, isolated_db, monkeypatch):
        did, ids = _blocked()

        def edit_note_meanwhile(zh, ctx):
            db.update_line_fields_if(did, ids[1], {"flag_note": "my own note"}, {})
            return ["Engine text"]
        _use_engine(monkeypatch, FakeEngine(edit_note_meanwhile))
        with pytest.raises(ConflictError):
            blocked_retry_service.retry_blocked_line(did, ids[1], "claude")
        row = next(r for r in db.load_lines(did) if r["id"] == ids[1])
        assert row["flag_note"] == "my own note" and row["en"] == ""
        assert row["flag"] == "content_blocked"

    def test_offline_engine_end_to_end(self, isolated_db):
        did, ids = _blocked()
        out = blocked_retry_service.retry_blocked_line(did, ids[1], "test_offline")
        assert out["retried"] is True and out["line"]["en"].startswith("[TEST]")


class TestRetryApi:
    def test_success(self, client, monkeypatch):
        did, ids = _blocked()
        _use_engine(monkeypatch, FakeEngine(lambda zh, ctx: ["Goodbye"]))
        r = client.post(_retry(did, ids[1]), json={"engine": "claude"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["retried"] is True and body["line"]["en"] == "Goodbye"
        _no_leak(r)

    def test_default_engine_is_ollama(self, client, monkeypatch):
        did, ids = _blocked()
        seen = _use_engine(monkeypatch, FakeEngine(lambda zh, ctx: ["Goodbye"]), key="local")
        assert client.post(_retry(did, ids[1]), json={}).status_code == 200
        assert seen["name"] == "ollama"

    def test_404s(self, client):
        did, ids = _blocked()
        assert client.post(_retry(did, 999999), json={"engine": "test_offline"}).status_code == 404
        assert client.post(_retry(999999, ids[1]),
                           json={"engine": "test_offline"}).status_code == 404
        other, oids = _blocked()
        assert client.post(_retry(did, oids[1]),
                           json={"engine": "test_offline"}).status_code == 404

    def test_409_when_not_blocked(self, client):
        did, ids = _seed(flag="uncertain")
        r = client.post(_retry(did, ids[1]), json={"engine": "test_offline"})
        assert r.status_code == 409
        assert client.post(_retry(did, ids[0]), json={"engine": "test_offline"}).status_code == 409

    def test_422s(self, client):
        did, ids = _blocked()
        assert client.post(_retry(did, ids[1]), json={"engine": "nope"}).status_code == 422
        assert client.post(_retry(did, ids[1]),
                           json={"engine": "claude", "api_key": FAKE_KEY}).status_code == 422
        assert client.post(_retry(did, ids[1]), json={"engine": ""}).status_code == 422

    def test_model_and_free_tier_fields_refused(self, client):
        did, ids = _blocked()
        r = client.post(_retry(did, ids[1]), json={"engine": "nllb", "model": "x/y"})
        assert r.status_code == 422
        r = client.post(_retry(did, ids[1]), json={"engine": "ollama", "gemini_free_tier": True})
        assert r.status_code == 422

    def test_busy_is_429(self, client, monkeypatch):
        did, ids = _blocked()
        _use_engine(monkeypatch, FakeEngine(lambda zh, ctx: ["Goodbye"]))
        llm_slots._ACTIVE_CALLERS.add("local")
        try:
            r = client.post(_retry(did, ids[1]), json={"engine": "claude"})
            assert r.status_code == 429 and r.json()["error"]["code"] == "rate_limited"
        finally:
            llm_slots._ACTIVE_CALLERS.discard("local")
        got = [llm_slots._SLOTS.acquire(blocking=False) for _ in range(llm_slots.LLM_MAX_IN_FLIGHT)]
        try:
            assert all(got)
            assert client.post(_retry(did, ids[1]), json={"engine": "claude"}).status_code == 429
        finally:
            for ok in got:
                if ok:
                    llm_slots._SLOTS.release()
        row = next(x for x in db.load_lines(did) if x["id"] == ids[1])
        assert row["flag"] == "content_blocked"
        assert client.post(_retry(did, ids[1]), json={"engine": "claude"}).status_code == 200

    def test_concurrent_retry_is_429(self, client, monkeypatch):
        did, ids = _blocked()
        started, release = threading.Event(), threading.Event()

        def slow(zh, ctx):
            started.set()
            release.wait(5)
            return ["Goodbye"]
        _use_engine(monkeypatch, FakeEngine(slow))
        out = {}
        t = threading.Thread(target=lambda: out.update(
            r=client.post(_retry(did, ids[1]), json={"engine": "claude"})))
        t.start()
        try:
            assert started.wait(5)
            assert client.post(_retry(did, ids[1]), json={"engine": "claude"}).status_code == 429
        finally:
            release.set()
            t.join(10)
        assert out["r"].status_code == 200

    def test_409_while_translate_job_runs(self, client):
        did, ids = _blocked()
        gate = _hold_job(f"translate_{did}")
        try:
            r = client.post(_retry(did, ids[1]), json={"engine": "test_offline"})
            assert r.status_code == 409
        finally:
            gate.set()

    def test_missing_key_503(self, client, monkeypatch):
        did, ids = _blocked()
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda n, env_path=None: None)
        assert client.post(_retry(did, ids[1]), json={"engine": "claude"}).status_code == 503

    def test_engine_error_is_redacted(self, client, monkeypatch):
        did, ids = _blocked()

        def boom(zh, ctx):
            raise RuntimeError(f"401 bad key {FAKE_KEY} at {db.LIBRARY_DIR}")
        _use_engine(monkeypatch, FakeEngine(boom))
        r = client.post(_retry(did, ids[1]), json={"engine": "claude"})
        assert r.status_code == 500
        assert r.json()["error"]["message"] == "The engine call failed."
        _no_leak(r, "401 bad key")
        row = next(x for x in db.load_lines(did) if x["id"] == ids[1])
        assert row["flag"] == "content_blocked"


# ---------------------------------------------------------------------------
# Permissions (BAIHE_API_AUTH=on)
# ---------------------------------------------------------------------------

def _user(email, *perms):
    """A user holding exactly `perms` (household defaults revoked first)."""
    u = auth_service.add_user(email)
    for p in auth_service.HOUSEHOLD_DEFAULT_PERMISSIONS:
        if p not in perms:
            auth_service.revoke_permission(u["id"], p)
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    return auth_service.create_session(u["id"], "pytest", "203.0.113.9")


def _h(s):
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


@pytest.fixture
def remote(isolated_db):
    return TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                      raise_server_exceptions=False)


class TestPermissions:
    def test_activate_401_403_200(self, remote):
        did, _ = _seed()
        vid = _version_over_current(did, ["Hi", "Bye"])
        body = {"confirm": True}
        assert remote.post(_act(did, vid), json=body).status_code == 401
        reader = _user("r@example.com", "lines.read", "review.use")
        assert remote.post(_act(did, vid), json=body, headers=_h(reader)).status_code == 403
        editor = _user("e@example.com", "lines.edit")
        r = remote.post(_act(did, vid), json=body, headers=_h(editor))
        assert r.status_code == 200 and r.json()["lines_changed"] == 2

    def test_retry_401_403_and_paid_gate(self, remote, monkeypatch):
        did, ids = _blocked()
        _use_engine(monkeypatch, FakeEngine(lambda zh, ctx: ["Goodbye"]))
        url = _retry(did, ids[1])
        assert remote.post(url, json={"engine": "ollama"}).status_code == 401
        editor = _user("e@example.com", "lines.edit")
        assert remote.post(url, json={"engine": "ollama"}, headers=_h(editor)).status_code == 403
        household = _user("h@example.com", "jobs.start")
        assert remote.post(url, json={"engine": "claude"},
                           headers=_h(household)).status_code == 403
        assert remote.post(url, json={"engine": "gemini"},
                           headers=_h(household)).status_code == 403
        # the free-tier flag is not a way around the paid-engine check
        assert remote.post(url, json={"engine": "gemini", "gemini_free_tier": True},
                           headers=_h(household)).status_code == 403
        assert remote.post(url, json={"engine": "nllb", "model": "x/y"},
                           headers=_h(household)).status_code == 422
        r = remote.post(url, json={"engine": "ollama"}, headers=_h(household))
        assert r.status_code == 200 and r.json()["retried"] is True

    def test_retry_paid_engine_with_engines_paid(self, remote, monkeypatch):
        did, ids = _blocked()
        _use_engine(monkeypatch, FakeEngine(lambda zh, ctx: ["Goodbye"]))
        paid = _user("p@example.com", "jobs.start", "engines.paid")
        r = remote.post(_retry(did, ids[1]), json={"engine": "claude"}, headers=_h(paid))
        assert r.status_code == 200
        _no_leak(r)
