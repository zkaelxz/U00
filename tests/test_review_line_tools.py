"""Review per-line tools and navigation (review parity R17 alternatives,
R18 grammar, R19 pronounce, R28 auto-shorten, R08 flagged navigation across
pages). Fully mocked: fake engine, stubbed LLM helpers
and edge-tts, no network."""
import asyncio
import json

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
import dub
import line_tools
import translate_engines
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from services import (auth_service, line_tools_service, review_lines_service,
                      translate_service)
from services.service_errors import InvalidInputError

SECRET = "sk-ant-SECRET1234567890abcdef"
REMOTE = "https://baihe.example.com"


class FakeEngine:
    model = "fake-model"
    supports_reference = True


@pytest.fixture(autouse=True)
def _env(isolated_db, monkeypatch):
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())


@pytest.fixture
def client():
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _forbid_writes(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("must not write")
    for name in ("save_lines", "update_line_fields_if", "save_line_history_snapshot",
                 "record_edit_sample"):
        monkeypatch.setattr(db, name, boom)


def _seed(lines=None):
    did = db.create_drama(title_zh="D")
    db.save_lines(did, lines or [Line(idx=0, start=0, end=1, zh="你好", en="hello"),
                                 Line(idx=1, start=1, end=2, zh="再见", en="bye")])
    return did, [r["id"] for r in db.load_lines(did)]


def _ai(did, lid, tool):
    return f"/api/line-ai/dramas/{did}/lines/{lid}/{tool}"


# a bodyless POST must carry the header the React client sends (CSRF guard)
LOCAL = {"X-Baihe-Local": "1"}


# ---------------------------------------------------------------------------
# R17 alternatives, R18 grammar
# ---------------------------------------------------------------------------

class TestAlternatives:
    def test_by_id_bounded_and_writes_nothing(self, client, monkeypatch):
        did, ids = _seed()
        _forbid_writes(monkeypatch)
        seen = {}

        def fake(zh, en, engine, count=3, source_language="zh", style_hint=""):
            seen.update(zh=zh, en=en)
            return [{"translation": "so long", "approach": "casual", "tradeoff": "tone"},
                    {"translation": "", "approach": "blank is dropped"},
                    "not a dict",
                    {"translation": "x" * 5000, "approach": {"nested": 1}}] + \
                [{"translation": f"alt {i}"} for i in range(20)]
        monkeypatch.setattr(line_tools, "alternative_translations", fake)
        r = client.post(_ai(did, ids[1], "alternatives"), json={})
        assert r.status_code == 200
        body = r.json()
        assert (seen["zh"], seen["en"]) == ("再见", "bye")
        assert body["line_id"] == ids[1] and body["current_en"] == "bye"
        alts = body["alternatives"]
        assert len(alts) == 6
        assert alts[0] == {"translation": "so long", "approach": "casual", "tradeoff": "tone"}
        assert len(alts[1]["translation"]) == 1000 and alts[1]["approach"] == ""

    def test_empty_result_is_an_error_without_a_key(self, client, monkeypatch):
        did, ids = _seed()
        monkeypatch.setattr(line_tools, "alternative_translations", lambda *a, **k: [])
        r = client.post(_ai(did, ids[0], "alternatives"), json={})
        assert r.status_code == 500

    def test_engine_failure_is_redacted(self, client, monkeypatch):
        did, ids = _seed()

        def boom(*a, **k):
            raise RuntimeError(f"bad key {SECRET}")
        monkeypatch.setattr(line_tools, "alternative_translations", boom)
        r = client.post(_ai(did, ids[0], "alternatives"), json={})
        assert r.status_code == 500 and SECRET not in r.text

    def test_other_dramas_line_is_404(self, client, monkeypatch):
        did, ids = _seed()
        other, _ = _seed()
        monkeypatch.setattr(line_tools, "alternative_translations",
                            lambda *a, **k: [{"translation": "x"}])
        assert client.post(_ai(other, ids[0], "alternatives"), json={}).status_code == 404


class TestGrammar:
    def test_needs_no_translation(self, client, monkeypatch):
        did, ids = _seed([Line(idx=0, start=0, end=1, zh="你好", en="")])
        _forbid_writes(monkeypatch)
        monkeypatch.setattr(line_tools, "grammar_breakdown", lambda zh, eng, **k: [
            {"word": "你", "reading": "nǐ", "meaning": "you", "function": "subject"},
            {"word": "好", "reading": "hǎo", "meaning": "good", "function": "predicate"}])
        r = client.post(_ai(did, ids[0], "grammar"), json={})
        assert r.status_code == 200
        assert r.json()["zh"] == "你好"
        assert [p["word"] for p in r.json()["parts"]] == ["你", "好"]

    def test_blank_source_is_unsupported(self, client, monkeypatch):
        did, ids = _seed([Line(idx=0, start=0, end=1, zh="", en="x")])
        r = client.post(_ai(did, ids[0], "grammar"), json={})
        assert r.status_code == 400

    def test_unknown_field_rejected(self, client):
        did, ids = _seed()
        r = client.post(_ai(did, ids[0], "grammar"), json={"api_key": "x"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# R19 pronounce
# ---------------------------------------------------------------------------

class TestPronounce:
    def _fake_tts(self, monkeypatch, record=None):
        async def fake(text, voice, out_path):
            if record is not None:
                record.update(text=text, voice=voice)
            with open(out_path, "wb") as f:
                f.write(b"ID3fake-mp3")
        monkeypatch.setattr(dub, "_edge_tts_synthesize", fake)

    @pytest.fixture(autouse=True)
    def _edge_tts_importable(self, monkeypatch):
        import sys
        import types
        monkeypatch.setitem(sys.modules, "edge_tts", types.ModuleType("edge_tts"))

    def test_returns_audio_of_the_stored_source_text(self, client, monkeypatch):
        did, ids = _seed()
        seen = {}
        self._fake_tts(monkeypatch, seen)
        r = client.post(_ai(did, ids[1], "pronounce"), headers=LOCAL)
        assert r.status_code == 200
        assert r.headers["content-type"] == "audio/mpeg"
        assert r.headers["cache-control"] == "no-store"
        assert r.content == b"ID3fake-mp3"
        assert seen["text"] == "再见"
        assert seen["voice"] in line_tools.SOURCE_LANG_VOICES.values()

    def test_too_long_is_refused_before_any_call(self, client, monkeypatch):
        did, ids = _seed([Line(idx=0, start=0, end=1, zh="字" * 201, en="x")])

        async def never(*a):
            raise AssertionError("must not call edge-tts")
        monkeypatch.setattr(dub, "_edge_tts_synthesize", never)
        r = client.post(_ai(did, ids[0], "pronounce"), headers=LOCAL)
        assert r.status_code == 400

    def test_timeout_is_bounded(self, client, monkeypatch):
        did, ids = _seed()
        monkeypatch.setattr(line_tools_service, "PRONOUNCE_TIMEOUT_S", 0.05)

        async def slow(text, voice, out_path):
            await asyncio.sleep(5)
        monkeypatch.setattr(dub, "_edge_tts_synthesize", slow)
        r = client.post(_ai(did, ids[0], "pronounce"), headers=LOCAL)
        assert r.status_code == 500 and "too long" in r.json()["error"]["message"]

    def test_missing_edge_tts_is_503(self, client, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "edge_tts", None)
        did, ids = _seed()
        assert client.post(_ai(did, ids[0], "pronounce"), headers=LOCAL).status_code == 503

    def test_blocked_is_503_and_errors_are_redacted(self, client, monkeypatch):
        did, ids = _seed()

        async def blocked(*a):
            raise dub.EdgeTTSBlockedError("403")
        monkeypatch.setattr(dub, "_edge_tts_synthesize", blocked)
        assert client.post(_ai(did, ids[0], "pronounce"), headers=LOCAL).status_code == 503

        async def leak(*a):
            raise RuntimeError(f"boom {SECRET}")
        monkeypatch.setattr(dub, "_edge_tts_synthesize", leak)
        r = client.post(_ai(did, ids[0], "pronounce"), headers=LOCAL)
        assert r.status_code == 500 and SECRET not in r.text

    def test_oversized_audio_refused(self, client, monkeypatch):
        did, ids = _seed()
        monkeypatch.setattr(line_tools_service, "MAX_AUDIO_BYTES", 4)
        self._fake_tts(monkeypatch)
        assert client.post(_ai(did, ids[0], "pronounce"), headers=LOCAL).status_code == 500


# ---------------------------------------------------------------------------
# R28 auto-shorten overlong lines
# ---------------------------------------------------------------------------

LONG = "this line has far too many words to be spoken in a single second of time"


def _overlong_drama():
    return _seed([Line(idx=0, start=0, end=1, zh="一", en=LONG),
                  Line(idx=1, start=1, end=5, zh="二", en="fine as it is"),
                  Line(idx=2, start=5, end=6, zh="三", en=LONG + " again", flag="uncertain",
                       flag_note="n")])


def _shorten(did):
    return f"/api/lines/dramas/{did}/shorten-overlong"


class TestShorten:
    def test_id_keyed_rewrite_snapshot_and_field_scoped(self, client, monkeypatch):
        did, ids = _overlong_drama()
        prompts = []

        def fake_llm(engine, prompt, max_tokens=2000, fallback="{}", usage_cb=None):
            prompts.append(prompt)
            if usage_cb:
                usage_cb(10, 5)
            # reordered on purpose: matched by id, never by position
            return json.dumps({"2": "Short two.", "1": "Short one."})
        monkeypatch.setattr(translate_engines, "call_llm_json", fake_llm)
        r = client.post(_shorten(did), json={"confirm": True, "engine": "ollama"})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["shortened"] == 2 and body["stale"] == 0 and body["snapshot_saved"]
        rows = {x["id"]: x for x in db.load_lines(did)}
        assert rows[ids[0]]["en"] == "Short one."
        assert rows[ids[1]]["en"] == "fine as it is"
        assert rows[ids[2]]["en"] == "Short two."
        # only en is written: the flag stays
        assert rows[ids[2]]["flag"] == "uncertain" and rows[ids[2]]["flag_note"] == "n"
        assert [(x["id"], x["before"]) for x in body["lines"]] == [
            (ids[0], LONG), (ids[2], LONG + " again")]
        hist = db.list_line_history(did)
        assert [h["label"] for h in hist] == ["before auto-shorten"]
        snap = db.get_line_history_snapshot(hist[0]["id"])
        assert [x["en"] for x in snap][0] == LONG
        assert "fine as it is" not in prompts[0]

    def test_line_ids_limit_and_stale_skip(self, client, monkeypatch):
        did, ids = _overlong_drama()

        def fake_rewrite(work, engine, usage_cb=None, batch_size=15):
            # the user edits line 0 while the model runs
            db.save_lines(did, [ln for ln in db.load_line_objects(did) if ln.id == ids[0]
                                and setattr(ln, "en", "user edit") is None], fields=("en",))
            for w in work:
                w.en = "short"
            return work
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm", fake_rewrite)
        r = client.post(_shorten(did), json={"confirm": True, "engine": "ollama", "line_ids": [ids[0], ids[1]]})
        body = r.json()
        assert body["shortened"] == 0 and body["stale"] == 1
        # nothing could be written, so no snapshot either
        assert not body["snapshot_saved"] and db.list_line_history(did) == []
        rows = {x["id"]: x for x in db.load_lines(did)}
        assert rows[ids[0]]["en"] == "user edit"
        assert rows[ids[2]]["en"] == LONG + " again"   # not in line_ids

    def test_nothing_overlong_calls_nothing(self, client, monkeypatch):
        did, _ = _seed()
        _forbid_writes(monkeypatch)
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
        r = client.post(_shorten(did), json={"confirm": True})
        assert r.status_code == 200 and r.json()["shortened"] == 0

    def test_unchanged_writes_nothing(self, client, monkeypatch):
        did, _ = _overlong_drama()
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm",
                            lambda work, engine, usage_cb=None: work)
        r = client.post(_shorten(did), json={"confirm": True})
        assert r.json()["unchanged"] == 2 and not r.json()["snapshot_saved"]
        assert db.list_line_history(did) == []

    def test_cap_reports_remaining(self, client, monkeypatch):
        did, _ = _seed([Line(idx=i, start=i, end=i + 1, zh="字", en=LONG) for i in range(5)])
        monkeypatch.setattr(line_tools_service, "MAX_SHORTEN_LINES", 2)
        seen = []

        def fake(work, engine, usage_cb=None):
            seen.append(len(work))
            for w in work:
                w.en = "s"
            return work
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm", fake)
        body = client.post(_shorten(did), json={"confirm": True}).json()
        assert seen == [2] and body["shortened"] == 2 and body["remaining"] == 3

    def test_repeated_runs_share_one_snapshot_per_pass(self, client, monkeypatch):
        did, ids = _seed([Line(idx=i, start=i, end=i + 1, zh="字", en=LONG) for i in range(5)])
        monkeypatch.setattr(line_tools_service, "MAX_SHORTEN_LINES", 2)

        def fake(work, engine, usage_cb=None):
            for w in work:
                w.en = "s"
            return work
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm", fake)
        first = client.post(_shorten(did), json={"confirm": True}).json()
        second = client.post(_shorten(did), json={"confirm": True}).json()
        assert first["snapshot_saved"] and not second["snapshot_saved"]
        assert second["shortened"] == 2 and second["remaining"] == 1
        hist = db.list_line_history(did)
        assert [h["label"] for h in hist] == ["before auto-shorten"]
        assert [r["en"] for r in db.get_line_history_snapshot(hist[0]["id"])] == [LONG] * 5
        # any other edit since starts a new pass with its own snapshot
        db.update_line_fields_if(did, ids[4], {"zh": "改"}, {"zh": "字"})
        third = client.post(_shorten(did), json={"confirm": True}).json()
        assert third["snapshot_saved"] and len(db.list_line_history(did)) == 2

    @pytest.mark.parametrize("edited", [0, 2])
    def test_manual_edit_between_runs_gets_its_own_snapshot(self, client, monkeypatch, edited):
        # 0: a line the first run shortened; 2: one it didn't reach yet. Either
        # way the manual (still overlong) text must stay undoable from History.
        did, ids = _seed([Line(idx=i, start=i, end=i + 1, zh="字", en=LONG) for i in range(5)])
        monkeypatch.setattr(line_tools_service, "MAX_SHORTEN_LINES", 2)

        def fake(work, engine, usage_cb=None):
            for w in work:
                w.en = "s"
            return work
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm", fake)
        assert client.post(_shorten(did), json={"confirm": True}).json()["snapshot_saved"]
        manual = "my own words " + LONG
        before = db.load_line_objects(did)[edited].en
        assert db.update_line_fields_if(did, ids[edited], {"en": manual}, {"en": before})
        second = client.post(_shorten(did), json={"confirm": True}).json()
        assert second["snapshot_saved"] and second["shortened"] == 2
        hist = db.list_line_history(did)
        assert len(hist) == 2
        assert db.get_line_history_snapshot(hist[0]["id"])[edited]["en"] == manual

    @pytest.mark.parametrize("change", [{"flag": "idiom", "flag_note": "mine"},
                                        {"flag_note": "changed"}, {"sfx": True}])
    def test_flag_or_sfx_change_between_runs_gets_its_own_snapshot(self, client, monkeypatch,
                                                                    change):
        # Restore brings back a snapshot's flags and SFX marks, so one set
        # between two runs must stay undoable from History.
        did, ids = _seed([Line(idx=i, start=i, end=i + 1, zh="字", en=LONG, flag="name",
                               flag_note="n") for i in range(5)])
        monkeypatch.setattr(line_tools_service, "MAX_SHORTEN_LINES", 2)

        def fake(work, engine, usage_cb=None):
            for w in work:
                w.en = "s"
            return work
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm", fake)
        assert client.post(_shorten(did), json={"confirm": True}).json()["snapshot_saved"]
        lines = db.load_line_objects(did)
        for f, v in change.items():
            setattr(lines[4], f, v)
        db.save_lines(did, lines, fields=tuple(change))
        second = client.post(_shorten(did), json={"confirm": True}).json()
        assert second["snapshot_saved"] and len(db.list_line_history(did)) == 2
        snap = db.get_line_history_snapshot(db.list_line_history(did)[0]["id"])
        assert {f: snap[4][f] for f in change} == change

    def test_confirm_must_be_a_real_boolean(self, client, monkeypatch):
        did, _ = _overlong_drama()
        _forbid_writes(monkeypatch)
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
        for bad in ("yes", "true", 1, "1"):
            assert client.post(_shorten(did), json={"confirm": bad}).status_code == 422

    def test_needs_confirm(self, client, monkeypatch):
        did, _ = _overlong_drama()
        _forbid_writes(monkeypatch)
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
        assert client.post(_shorten(did), json={}).status_code == 422
        assert client.post(_shorten(did), json={"confirm": False}).status_code == 422
        with pytest.raises(InvalidInputError):
            line_tools_service.shorten_overlong(did)

    def test_refused_while_a_job_runs(self, client, monkeypatch):
        from services import drama_service
        did, _ = _overlong_drama()
        _forbid_writes(monkeypatch)
        monkeypatch.setattr(drama_service, "job_running_for_drama", lambda d: d == did)
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("no call")))
        r = client.post(_shorten(did), json={"confirm": True})
        assert r.status_code == 409 and "job" in r.json()["error"]["message"]

    def test_translation_only_engine_refused(self, client):
        did, _ = _overlong_drama()
        assert client.post(_shorten(did), json={"confirm": True, "engine": "nllb"}).status_code in (400, 422)


# ---------------------------------------------------------------------------
# Monthly spending cap on the paid per-line tools
# ---------------------------------------------------------------------------

class TestMonthlyCap:
    @pytest.fixture(autouse=True)
    def _cap_used_up(self, monkeypatch):
        from services import translate_run_service
        monkeypatch.setattr(translate_run_service, "_monthly_cap", lambda: 5.0)
        monkeypatch.setattr(db, "get_month_spend", lambda *a, **k: 5.5)
        monkeypatch.setattr(line_tools, "alternative_translations",
                            lambda *a, **k: [{"translation": "x"}])
        monkeypatch.setattr(line_tools, "grammar_breakdown",
                            lambda *a, **k: [{"word": "w", "meaning": "m"}])

    @pytest.mark.parametrize("tool", ["alternatives", "grammar"])
    def test_paid_engine_refused_when_cap_used_up(self, client, monkeypatch, tool):
        did, ids = _seed()
        called = []
        monkeypatch.setattr(translate_engines, "get_engine",
                            lambda *a, **k: called.append(1) or FakeEngine())
        r = client.post(_ai(did, ids[0], tool), json={"engine": "claude"})
        assert r.status_code == 400 and "spending cap" in r.json()["error"]["message"]
        assert called == []

    @pytest.mark.parametrize("tool", ["alternatives", "grammar"])
    def test_free_engine_allowed_when_cap_used_up(self, client, tool):
        did, ids = _seed()
        assert client.post(_ai(did, ids[0], tool), json={"engine": "ollama"}).status_code == 200

    def test_shorten_paid_refused_free_allowed(self, client, monkeypatch):
        did, _ = _overlong_drama()
        _forbid_writes(monkeypatch)
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm",
                            lambda work, engine, usage_cb=None: work)
        r = client.post(_shorten(did), json={"confirm": True, "engine": "deepseek"})
        assert r.status_code == 400 and "spending cap" in r.json()["error"]["message"]
        assert client.post(_shorten(did), json={"confirm": True, "engine": "ollama"}).status_code == 200


# ---------------------------------------------------------------------------
# R08 flagged navigation across pages
# ---------------------------------------------------------------------------

def _paged():
    lines = [Line(idx=i, start=i, end=i + 1, zh=f"z{i}", en=f"e{i}" if i != 7 else "",
                  flag="uncertain" if i in (2, 9) else None) for i in range(12)]
    return _seed(lines)


class TestNavigation:
    def test_flagged_adjacent_across_pages(self, client):
        did, ids = _paged()
        base = f"/api/review/dramas/{did}/flagged-adjacent"
        r = client.get(base, params={"direction": "next", "page_size": 5})
        assert r.json() == {"line_id": ids[2], "idx": 2, "page": 1, "page_all": 1}
        r = client.get(base, params={"direction": "next", "from_line_id": ids[2], "page_size": 5})
        assert r.json() == {"line_id": ids[9], "idx": 9, "page": 2, "page_all": 2}
        r = client.get(base, params={"direction": "next", "from_line_id": ids[9], "page_size": 5})
        assert r.json() == {"line_id": None, "idx": None, "page": None, "page_all": None}
        r = client.get(base, params={"direction": "prev", "page_size": 5})
        assert r.json()["line_id"] == ids[9]
        r = client.get(base, params={"direction": "prev", "from_line_id": ids[9],
                                     "only": "flagged", "page_size": 1})
        assert r.json() == {"line_id": ids[2], "idx": 2, "page": 1, "page_all": 3}

    def test_bad_inputs(self, client):
        did, ids = _paged()
        other, oids = _seed()
        assert client.get(f"/api/review/dramas/{did}/flagged-adjacent",
                          params={"from_line_id": oids[0]}).status_code == 404
        assert client.get(f"/api/review/dramas/{did}/flagged-adjacent",
                          params={"direction": "up"}).status_code == 422
        assert client.get(f"/api/review/dramas/{did}/flagged-adjacent",
                          params={"only": "bogus"}).status_code == 422
        with pytest.raises(InvalidInputError):
            review_lines_service.adjacent_flagged(did, True, page_size=0)


# ---------------------------------------------------------------------------
# Permissions (BAIHE_API_AUTH=on)
# ---------------------------------------------------------------------------

def _user(email, *perms):
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
    def test_llm_read_tools_need_lines_read_and_paid_gate(self, remote, monkeypatch):
        did, ids = _seed()
        monkeypatch.setattr(line_tools, "alternative_translations",
                            lambda *a, **k: [{"translation": "x"}])
        url = _ai(did, ids[0], "alternatives")
        assert remote.post(url, json={"engine": "ollama"}).status_code == 401
        nobody = _user("n@example.com")
        assert remote.post(url, json={"engine": "ollama"}, headers=_h(nobody)).status_code == 403
        reader = _user("r@example.com", "lines.read")
        assert remote.post(url, json={"engine": "claude"}, headers=_h(reader)).status_code == 403
        assert remote.post(url, json={}, headers=_h(reader)).status_code == 403
        assert remote.post(url, json={"engine": "ollama"}, headers=_h(reader)).status_code == 200
        paid = _user("p@example.com", "lines.read", "engines.paid")
        assert remote.post(url, json={"engine": "claude"}, headers=_h(paid)).status_code == 200

    def test_shorten_needs_lines_edit_and_paid_gate(self, remote, monkeypatch):
        did, _ = _overlong_drama()
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm",
                            lambda work, engine, usage_cb=None: work)
        url = _shorten(did)
        reader = _user("r@example.com", "lines.read", "engines.paid")
        assert remote.post(url, json={"confirm": True, "engine": "ollama"}, headers=_h(reader)).status_code == 403
        editor = _user("e@example.com", "lines.edit")
        assert remote.post(url, json={"confirm": True, "engine": "claude"}, headers=_h(editor)).status_code == 403
        assert remote.post(url, json={"confirm": True, "engine": "ollama"}, headers=_h(editor)).status_code == 200

    @pytest.mark.parametrize("tool", ["alternatives", "grammar", "shorten"])
    def test_gate_uses_the_dramas_engine_when_none_is_named(self, remote, monkeypatch, tool):
        monkeypatch.setattr(line_tools, "alternative_translations",
                            lambda *a, **k: [{"translation": "x"}])
        monkeypatch.setattr(line_tools, "grammar_breakdown",
                            lambda *a, **k: [{"word": "w", "meaning": "m"}])
        monkeypatch.setattr(translate_engines, "rewrite_for_pacing_llm",
                            lambda work, engine, usage_cb=None: work)
        built = []
        monkeypatch.setattr(translate_engines, "get_engine",
                            lambda name, *a, **k: built.append(name) or FakeEngine())
        household = _user("h@example.com", "lines.read", "lines.edit")
        for engine, status in (("ollama", 200), ("claude", 403)):
            did, ids = _overlong_drama()
            db.update_drama(did, translation_engine=engine)
            url = _shorten(did) if tool == "shorten" else _ai(did, ids[0], tool)
            body = {"confirm": True} if tool == "shorten" else {}
            assert remote.post(url, json=body, headers=_h(household)).status_code == status
        # the call runs on the engine the gate checked
        assert built == ["ollama"]

    def test_pronounce_and_navigation_need_lines_read(self, remote):
        did, ids = _seed()
        nobody = _user("n@example.com")
        assert remote.post(_ai(did, ids[0], "pronounce"), headers={**_h(nobody), **LOCAL}).status_code == 403
        assert remote.get(f"/api/review/dramas/{did}/flagged-adjacent",
                          headers=_h(nobody)).status_code == 403
        reader = _user("r@example.com", "lines.read")
        assert remote.get(f"/api/review/dramas/{did}/flagged-adjacent",
                          headers=_h(reader)).status_code == 200
