"""Suggest spellings (one bounded model call, verified against the transcript) and the
English name-consistency check: service, job and routes. The model call is faked; no network."""
import itertools
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import core
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, glossary_extract_service, glossary_spelling_service as sp
from services import glossary_service, translate_run_service, translate_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError, UnsupportedOperationError

SECRET = "sk-ant-api03-SECRETSECRETSECRETSECRET"
LINES = ["ヤローさんこんにちは", "ヤロチが来た", "ヤロチも来た", "ヤローが笑った", "関係ない話"]


class _Engine:
    supports_reference = True
    model = "fake-model"


@pytest.fixture(autouse=True)
def _clean_jobs():
    def clear():
        with background_jobs._lock:
            for j in [j for j in background_jobs._jobs if j.startswith("spelling_suggest_")]:
                background_jobs._jobs.pop(j)
    clear()
    yield
    clear()


@pytest.fixture
def engine(monkeypatch):
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, *a: SECRET)
    monkeypatch.setattr(glossary_extract_service.translate_engines, "get_engine",
                        lambda name, key, **kw: _Engine())


@pytest.fixture
def reply(monkeypatch):
    """Set reply["text"]; every call is recorded in reply["calls"]."""
    state = {"text": "[]", "calls": [], "usage": (10, 5), "raises": None, "hook": None}

    def fake(eng, prompt, max_tokens=2000, fallback="[]", usage_cb=None, system=None):
        state["calls"].append({"prompt": prompt, "max_tokens": max_tokens})
        if state["hook"]:
            state["hook"]()
        if state["raises"]:
            raise state["raises"]
        if usage_cb:
            usage_cb(*state["usage"])
        return state["text"]
    monkeypatch.setattr(sp.llm_tasks, "call_llm_json", fake)
    return state


def _title(db, lines=LINES, ens=None, aliases="", translation="Yaro", language="ja"):
    sid = db.get_or_create_series("S")
    did = db.create_drama(title_en="T", series_id=sid, source_language=language,
                          translation_engine="claude")
    db.save_lines(did, [core.Line(idx=i, start=float(i), end=i + 1.0, zh=z,
                                  en=(ens[i] if ens else ""))
                        for i, z in enumerate(lines)])
    db.upsert_glossary_term(sid, "ヤロー", translation)
    term = db.list_glossary_terms(sid)[0]
    if aliases:
        glossary_service.upsert_glossary_term(
            did, {"id": term["id"], "aliases": aliases.split("|")})
    return did, sid, term["id"]


def _wait(job_id):
    for _ in range(300):
        st = background_jobs.get_status(job_id)
        if st and st["status"] in ("done", "error", "cancelled"):
            return st
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _run(did, tid, english="Yaro"):
    out = sp.start_spelling_suggestions(did, tid, english)
    _wait(out["job_id"])
    return sp.get_spelling_status(did)


class TestParse:
    @pytest.mark.parametrize("text,expected", [
        ('["a1", "b2"]', ["a1", "b2"]),
        ('```json\n["a1"]\n```', ["a1"]),
        ('{"spellings": ["a1", {"spelling": "b2"}, 5, "a1"]}', ["a1", "b2"]),
        ("[]", []),
        ("Sure! Here are some: ヤロチ, ヤロウ", None),
        ("", None), ('{"x": 1}', None), ("42", None)])
    def test_strict(self, text, expected):
        assert sp.parse_spellings(text) == expected


class TestSuggest:
    def test_only_spellings_in_the_transcript_are_shown(self, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db)
        reply["text"] = '["ヤロチ", "ヤロウ", "ヤロー", "ヤ"]'
        st = _run(did, tid)
        assert st["status"] == "done" and st["result"]["unusable"] is False
        # ヤロウ never occurs, the term itself and a one-character guess are dropped
        assert [(p["text"], p["count"]) for p in st["result"]["proposals"]] == [("ヤロチ", 2)]
        p = st["result"]["proposals"][0]
        assert p["is_alias"] is False and len(p["examples"]) == 2
        assert p["examples"][0]["line_idx"] == 1 and "ヤロチ" in p["examples"][0]["snippet"]
        assert "Yaro" in reply["calls"][0]["prompt"] and len(reply["calls"]) == 1

    def test_nothing_is_saved(self, isolated_db, engine, reply):
        did, sid, tid = _title(isolated_db)
        reply["text"] = '["ヤロチ"]'
        before = isolated_db.list_glossary_terms(sid)
        _run(did, tid)
        assert isolated_db.list_glossary_terms(sid) == before
        assert [ln.zh for ln in isolated_db.load_line_objects(did)] == LINES

    def test_existing_alias_is_marked(self, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db, aliases="ヤロチ")
        reply["text"] = '["ヤロチ"]'
        assert _run(did, tid)["result"]["proposals"][0]["is_alias"] is True

    @pytest.mark.parametrize("text", ["I think ヤロチ is likely.", "", "{not json", "null"])
    def test_unusable_reply_writes_nothing_and_says_so(self, isolated_db, engine, reply, text):
        did, sid, tid = _title(isolated_db)
        reply["text"] = text
        before = isolated_db.list_glossary_terms(sid)
        st = _run(did, tid)
        assert st["status"] == "done" and st["result"]["unusable"] is True
        assert st["result"]["proposals"] == []
        assert st["message"] == sp.UNUSABLE_MESSAGE
        assert isolated_db.list_glossary_terms(sid) == before

    def test_no_match_has_a_plain_message(self, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db)
        reply["text"] = '["ヤロウ"]'
        st = _run(did, tid)
        assert st["result"]["proposals"] == [] and st["message"] == sp.NO_MATCH_MESSAGE

    def test_proposals_are_capped(self, isolated_db, engine, reply):
        spellings = [f"名前{i:02d}" for i in range(40)]
        did, _, tid = _title(isolated_db, lines=[" ".join(spellings)])
        reply["text"] = str(spellings).replace("'", '"')
        st = _run(did, tid)
        assert len(st["result"]["proposals"]) == sp.MAX_SHOWN
        # the model's own list was cut at MAX_MODEL_SPELLINGS before verification
        assert all(int(p["text"][2:]) < sp.MAX_MODEL_SPELLINGS for p in st["result"]["proposals"][:1])

    def test_latin_spelling_is_not_counted_inside_a_longer_word(self, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db, lines=["Yaloch said hi", "then Yalo left", "Yalo again"],
                             language="zh")
        reply["text"] = '["Yalo", "Yaloch"]'
        got = {p["text"]: p["count"] for p in _run(did, tid)["result"]["proposals"]}
        assert got == {"Yalo": 2, "Yaloch": 1}

    def test_half_width_guess_cannot_collapse_to_one_character(self, isolated_db, engine, reply):
        # NFKC turns half-width ｶﾞ (two code points) into the single ガ, which would match nearly every line
        did, _, tid = _title(isolated_db, lines=["ガガ", "ガ来た", "ヤロチが来た", "ヤロチも来た"])
        reply["text"] = '["ｶﾞ", "ヤロチ"]'
        assert [p["text"] for p in _run(did, tid)["result"]["proposals"]] == ["ヤロチ"]

    def test_full_width_latin_keeps_the_word_boundary(self, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db, lines=["Yaloch said hi", "then Yalo left"], language="zh")
        reply["text"] = '["Ｙａｌｏ"]'
        got = {p["text"]: p["count"] for p in _run(did, tid)["result"]["proposals"]}
        assert got == {"Yalo": 1}

    def test_full_width_spelling_equal_to_the_term_is_not_offered(self, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db, lines=["Ｙａｌｏ", "Yalo"], language="zh")
        glossary_service.upsert_glossary_term(did, {"id": tid, "term_original": "Yalo"})
        reply["text"] = '["Ｙａｌｏ"]'
        assert _run(did, tid)["result"]["proposals"] == []

    def test_offered_text_is_the_form_the_transcript_uses(self, isolated_db, engine, reply):
        # Alias matching in translation is a literal substring, so the model's "yalo" must not be returned
        did, _, tid = _title(isolated_db, lines=["YALO said", "YALO again", "Yalo left"], language="zh")
        reply["text"] = '["yalo", "Yalo"]'
        got = _run(did, tid)["result"]["proposals"]
        assert [(p["text"], p["count"]) for p in got] == [("YALO", 3)]

    def test_usage_is_logged(self, isolated_db, engine, reply, monkeypatch):
        did, _, tid = _title(isolated_db)
        logged = []
        monkeypatch.setattr(isolated_db, "log_usage", lambda *a: logged.append(a))
        reply["text"] = "[]"
        _run(did, tid)
        assert len(logged) == 1 and logged[0][0] == did and logged[0][3] == "glossary_spellings"
        assert logged[0][4:6] == (10, 5)

    def test_cancel_is_honoured_and_nothing_is_published(self, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db)
        reply["text"] = '["ヤロチ"]'
        reply["hook"] = lambda: background_jobs.request_cancel(sp.spelling_job_id(did))
        out = sp.start_spelling_suggestions(did, tid, "Yaro")
        st = _wait(out["job_id"])
        assert st["status"] == "cancelled" and not st.get("result")
        assert sp.get_spelling_status(did)["result"] is None

    def test_stale_run_id_cannot_cancel(self, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db)
        _run(did, tid)
        with pytest.raises(ConflictError):
            sp.cancel_spelling_run(did, "not-the-run", None)

    def test_engine_error_is_redacted(self, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db)
        reply["raises"] = RuntimeError(f"401 bad key {SECRET}")
        st = _run(did, tid)
        assert st["status"] == "error" and SECRET not in repr(st)

    def test_spent_cap_refuses_before_any_call(self, isolated_db, engine, reply, monkeypatch):
        did, _, tid = _title(isolated_db)

        def spent(*a, **kw):
            raise UnsupportedOperationError("This month's spending cap is used up.")
        monkeypatch.setattr(translate_run_service, "refuse_when_cap_spent", spent)
        with pytest.raises(UnsupportedOperationError):
            sp.start_spelling_suggestions(did, tid, "Yaro")
        assert reply["calls"] == []
        assert not background_jobs.get_status(sp.spelling_job_id(did))

    def test_bad_input(self, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db)
        with pytest.raises(NotFoundError):
            sp.start_spelling_suggestions(did, tid + 99, "Yaro")
        with pytest.raises(InvalidInputError):
            sp.start_spelling_suggestions(did, tid, "  ")
        with pytest.raises(NotFoundError):
            sp.start_spelling_suggestions(99999, tid, "Yaro")
        assert reply["calls"] == []

    def test_job_blocks_title_delete(self):
        assert "spelling_suggest_" in background_jobs.DRAMA_JOB_PREFIXES


class TestConsistency:
    ENS = ["Yaro, hello", "Yaroch came", "Yaro came too", "Yarou laughed", ""]

    def test_lists_lines_where_the_english_differs(self, isolated_db):
        did, _, tid = _title(isolated_db, ens=self.ENS, aliases="ヤロチ")
        out = sp.check_name_consistency(did, tid)
        assert out["available"] and out["english"] == "Yaro"
        assert out["lines_with_name"] == 4 and out["consistent"] == 2 and out["untranslated"] == 0
        assert [r["line_idx"] for r in out["differing"]] == [1, 3]
        assert out["differing"][0]["english"] == "Yaroch came"
        assert out["other_words"] == []

    def test_other_words_hint_at_the_other_rendering(self, isolated_db):
        did, _, tid = _title(isolated_db, aliases="ヤロチ",
                             ens=["Hello Yaro", "Then Yalou left", "See Yalou", "Hi", ""])
        assert sp.check_name_consistency(did, tid)["other_words"] == ["Yalou"]

    def test_typed_english_overrides_and_nothing_changes(self, isolated_db):
        did, sid, tid = _title(isolated_db, ens=self.ENS, aliases="ヤロチ")
        before = isolated_db.list_glossary_terms(sid)
        out = sp.check_name_consistency(did, tid, "Yaroch")
        assert out["english"] == "Yaroch" and out["consistent"] == 1
        assert isolated_db.list_glossary_terms(sid) == before
        assert [ln.en for ln in isolated_db.load_line_objects(did)] == self.ENS

    def test_no_english_yet_says_so(self, isolated_db):
        did, _, tid = _title(isolated_db, ens=self.ENS, translation="")
        out = sp.check_name_consistency(did, tid)
        assert out["available"] is False and "English" in out["reason"] and out["differing"] == []

    def test_scan_stops_at_the_line_budget_and_says_so(self, isolated_db, monkeypatch):
        did, _, tid = _title(isolated_db, ens=self.ENS)
        monkeypatch.setattr(sp, "SCAN_MAX_LINES", 2)
        out = sp.check_name_consistency(did, tid)
        assert out["truncated"] is True and out["lines_scanned"] == 2
        assert out["lines_with_name"] == 1   # only line 0 mentions ヤロー within the first two lines

    def test_scan_stops_at_the_character_budget(self, isolated_db, monkeypatch):
        did, _, tid = _title(isolated_db, ens=self.ENS)
        monkeypatch.setattr(sp, "SCAN_MAX_CHARS", 12)
        assert sp.check_name_consistency(did, tid)["truncated"] is True

    def test_small_title_is_not_truncated(self, isolated_db):
        did, _, tid = _title(isolated_db, ens=self.ENS)
        out = sp.check_name_consistency(did, tid)
        assert out["truncated"] is False and out["lines_scanned"] == 5

    def test_untranslated_lines_are_counted_not_listed(self, isolated_db):
        did, _, tid = _title(isolated_db, ens=["", "", "", "", ""], aliases="ヤロチ")
        out = sp.check_name_consistency(did, tid)
        assert out["untranslated"] == 4 and out["differing"] == []


# ----- routes -------------------------------------------------------------------

_emails = itertools.count()


def _session(*permissions, keep=("library.read", "lines.read")):
    user = auth_service.add_user(f"sp{next(_emails)}@example.com")
    for gone in ("library.read", "lines.read"):
        if gone not in keep:
            auth_service.revoke_permission(user["id"], gone)
    for p in permissions:
        auth_service.grant_permission(user["id"], p)
    s = auth_service.create_session(user["id"], "pytest", "203.0.113.9")
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"], "Origin": "https://baihe.example.com"}


@pytest.fixture
def remote(isolated_db):
    return TestClient(create_app(ApiSettings(auth_mode="on", serve_frontend=False)),
                      base_url="https://baihe.example.com", raise_server_exceptions=False)


@pytest.fixture
def local(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _url(did, tail=""):
    return f"/api/glossary/dramas/{did}/spelling-suggestions{tail}"


class TestRoutes:
    def test_start_status_and_cancel_shapes(self, local, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db)
        assert local.get(_url(did)).json()["status"] == "idle"
        reply["text"] = '["ヤロチ"]'
        r = local.post(_url(did), json={"term_id": tid, "english": "Yaro"})
        assert r.status_code == 200, r.text
        assert r.json() == {"job_id": f"spelling_suggest_{did}", "engine": "claude"}
        _wait(r.json()["job_id"])
        body = local.get(_url(did))
        assert body.status_code == 200 and SECRET not in body.text
        b = body.json()
        assert b["status"] == "done" and b["result"]["proposals"][0]["text"] == "ヤロチ"
        assert set(b["result"]["proposals"][0]) == {"text", "count", "is_alias", "is_term", "examples"}
        stale = local.post(_url(did, "/cancel"), json={"run_id": "nope"})
        assert stale.status_code == 409

    @pytest.mark.parametrize("body", [{}, {"term_id": 0, "english": "x"}, {"term_id": 1, "english": ""},
                                      {"term_id": 1, "english": "x" * 81}])
    def test_bad_body_422(self, local, isolated_db, body):
        did, _, _ = _title(isolated_db)
        assert local.post(_url(did), json=body).status_code == 422

    def test_spent_cap_is_a_plain_400(self, local, isolated_db, engine, reply, monkeypatch):
        did, _, tid = _title(isolated_db)
        monkeypatch.setattr(translate_run_service, "refuse_when_cap_spent",
                            lambda *a, **k: (_ for _ in ()).throw(
                                UnsupportedOperationError("This month's spending cap is used up.")))
        r = local.post(_url(did), json={"term_id": tid, "english": "Yaro"})
        assert r.status_code == 400 and "cap" in r.json()["error"]["message"]
        assert reply["calls"] == []

    def test_missing_key_is_503(self, local, isolated_db, monkeypatch):
        did, _, tid = _title(isolated_db)
        monkeypatch.setattr(translate_service, "resolve_api_key", lambda *a: None)
        assert local.post(_url(did), json={"term_id": tid, "english": "Yaro"}).status_code == 503

    def test_permissions(self, remote, isolated_db, engine, reply):
        did, _, tid = _title(isolated_db, ens=TestConsistency.ENS)
        body = {"term_id": tid, "english": "Yaro"}
        check = f"/api/glossary/dramas/{did}/name-consistency?term_id={tid}"
        for call in (lambda h: remote.post(_url(did), json=body, headers=h),
                     lambda h: remote.get(_url(did), headers=h),
                     lambda h: remote.post(_url(did, "/cancel"), json={"run_id": "x"}, headers=h),
                     lambda h: remote.get(check, headers=h)):
            assert call({}).status_code == 401
        # starting needs jobs.start AND engines.paid for a paid engine; reading lines is not enough
        assert remote.post(_url(did), json=body, headers=_session()).status_code == 403
        assert remote.post(_url(did), json=body, headers=_session("jobs.start")).status_code == 403
        started = remote.post(_url(did), json=body, headers=_session("jobs.start", "engines.paid"))
        assert started.status_code == 200, started.text
        _wait(started.json()["job_id"])
        # the status and the check return source line text: lines.read, library.read is not enough
        only_library = _session(keep=("library.read",))
        assert remote.get(_url(did), headers=only_library).status_code == 403
        assert remote.get(check, headers=only_library).status_code == 403
        assert remote.get(_url(did), headers=_session()).status_code == 200
        assert remote.get(check, headers=_session()).status_code == 200

    def test_consistency_shape_and_errors(self, local, isolated_db):
        did, _, tid = _title(isolated_db, ens=TestConsistency.ENS)
        r = local.get(f"/api/glossary/dramas/{did}/name-consistency?term_id={tid}&english=Yaro")
        assert r.status_code == 200
        assert set(r.json()) == {"term", "english", "available", "reason", "lines_with_name",
                                 "untranslated", "consistent", "differing", "other_words", "truncated",
                                 "lines_scanned", "more_rows"}
        assert local.get(f"/api/glossary/dramas/{did}/name-consistency?term_id={tid + 9}").status_code == 404
        assert local.get(f"/api/glossary/dramas/{did}/name-consistency").status_code == 422
        assert local.get(f"/api/glossary/dramas/9999/name-consistency?term_id=1").status_code == 404
