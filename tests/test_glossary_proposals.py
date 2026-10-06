"""Glossary proposals: occurrence count, alternatives, confidence band and the
per-series ignore list. Fully mocked: no LLM, no network."""
import json

import pytest

import background_jobs
import db
import translation_guide as tguide
from services import glossary_service as gs
from services.service_errors import InvalidInputError, NotFoundError, UnsupportedOperationError


def _drama(isolated_db, name="S", series=True):
    sid = isolated_db.get_or_create_series(name) if series else None
    return isolated_db.create_drama(title_en=f"D-{name}", series_id=sid), sid


def _prop(term, tr="X", **extra):
    return {"term": term, "suggested_translation": tr, "category": "sect", "policy": "hybrid",
            "reason": "", **extra}


class TestNormalize:
    def test_occurrences_alternatives_and_confidence(self):
        text = "青云宗" * 3 + "沈清" + "玄天宗" * 5
        out = {p["term"]: p for p in gs._normalize_proposals([
            _prop("青云宗", "Qingyun Sect", renderings=["Qingyun Sect"]),
            _prop("沈清", "Shen Qing", renderings=["Shen Qing"]),
            _prop("玄天宗", "Xuantian Sect", renderings=["Mystic Sect", "Xuantian Sect", "Mystic Sect"]),
        ], [], text)}
        assert (out["青云宗"]["occurrences"], out["青云宗"]["confidence"]) == (3, "high")
        assert out["青云宗"]["alternatives"] == []
        assert (out["沈清"]["occurrences"], out["沈清"]["confidence"]) == (1, "low")
        # Seen often, but the windows disagreed: Low, with the other rendering shown.
        assert out["玄天宗"]["occurrences"] == 5 and out["玄天宗"]["confidence"] == "low"
        assert out["玄天宗"]["alternatives"] == ["Mystic Sect"]

    def test_no_translation_is_never_high(self):
        (p,) = gs._normalize_proposals([_prop("师尊", "")], [], "师尊" * 9)
        assert p["confidence"] == "low"

    def test_bad_renderings_are_ignored(self):
        (p,) = gs._normalize_proposals([_prop("师尊", "Teacher", renderings="oops")], [], "师尊" * 3)
        assert p["alternatives"] == [] and p["confidence"] == "high"


class TestExtractionStats:
    def test_windows_and_renderings_across_samples(self, monkeypatch):
        class Engine:
            supports_reference = True
        answers = iter(["Sect A", "Sect B", "Sect B"])
        monkeypatch.setattr(tguide, "_sample_across_text", lambda *a, **k: ["a", "b", "c"])
        monkeypatch.setattr(tguide, "call_llm_json", lambda *a, **k: json.dumps([
            {"term": "宗", "suggested_translation": next(answers), "category": "sect",
             "policy": "hybrid", "reason": ""}]))
        (e,) = tguide.extract_glossary_from_novel("x", Engine())
        assert e["windows"] == 3 and e["suggested_translation"] == "Sect B"
        assert e["renderings"] == ["Sect A", "Sect B"]


class TestStatusAndIgnoreList:
    def _held(self, monkeypatch, did, proposals):
        real = background_jobs.get_status
        monkeypatch.setattr(background_jobs, "get_status", lambda jid: (
            {"status": "done", "result": {"proposals": proposals, "run_id": "r1"}}
            if jid == gs.novel_glossary_job_id(did) else real(jid)))

    def test_status_carries_the_new_fields_and_hides_ignored(self, isolated_db, monkeypatch):
        did, _ = _drama(isolated_db)
        props = gs._normalize_proposals([_prop("青云宗", "Q"), _prop("路人", "Passer-by")], [],
                                        "青云宗" * 4)
        self._held(monkeypatch, did, props)
        shown = gs.get_novel_glossary_status(did)["result"]["proposals"]
        assert [p["term"] for p in shown] == ["青云宗", "路人"]
        assert shown[0]["confidence"] == "high" and shown[0]["occurrences"] == 4

        assert gs.dismiss_glossary_proposals(did, ["路人", " 路人 "]) == {"changed": 1}
        assert [p["term"] for p in gs.get_novel_glossary_status(did)["result"]["proposals"]] \
            == ["青云宗"]
        assert gs.dismiss_glossary_proposals(did, ["路人"]) == {"changed": 0}
        assert [d["term"] for d in gs.list_glossary_dismissals(did)["dismissals"]] == ["路人"]

        assert gs.restore_glossary_proposals(did, ["路人", "never ignored"]) == {"changed": 1}
        assert len(gs.get_novel_glossary_status(did)["result"]["proposals"]) == 2
        assert gs.list_glossary_dismissals(did) == {"dismissals": []}

    def test_ignore_list_is_shared_by_the_series_and_not_other_series(self, isolated_db):
        a, sid = _drama(isolated_db, "A")
        sibling = isolated_db.create_drama(title_en="A2", series_id=sid)
        other, _ = _drama(isolated_db, "B")
        gs.dismiss_glossary_proposals(a, ["路人"])
        assert len(gs.list_glossary_dismissals(sibling)["dismissals"]) == 1
        assert gs.list_glossary_dismissals(other)["dismissals"] == []

    def test_apply_path_is_unchanged_by_dismissal(self, isolated_db, monkeypatch):
        did, sid = _drama(isolated_db)
        self._held(monkeypatch, did, gs._normalize_proposals([_prop("青云宗", "Q")], [], ""))
        gs.dismiss_glossary_proposals(did, ["青云宗"])
        out = gs.apply_novel_glossary(did, ["青云宗"], run_id="r1")
        assert out["added"] == ["青云宗"]

    def test_validation_and_errors(self, isolated_db):
        did, _ = _drama(isolated_db)
        nos, _ = _drama(isolated_db, "N", series=False)
        for bad in ([], "x", [""], [5], ["x" * 201]):
            with pytest.raises(InvalidInputError):
                gs.dismiss_glossary_proposals(did, bad)
        with pytest.raises(UnsupportedOperationError):
            gs.dismiss_glossary_proposals(nos, ["a"])
        assert gs.list_glossary_dismissals(nos) == {"dismissals": []}
        with pytest.raises(NotFoundError):
            gs.list_glossary_dismissals(999)

    def test_ignore_list_is_capped_per_series(self, isolated_db, monkeypatch):
        monkeypatch.setattr(gs, "MAX_DISMISSALS_PER_SERIES", 3)
        did, sid = _drama(isolated_db)
        gs.dismiss_glossary_proposals(did, ["a", "b"])
        with pytest.raises(InvalidInputError, match="at most 3"):
            gs.dismiss_glossary_proposals(did, ["c", "d"])
        assert len(gs.list_glossary_dismissals(did)["dismissals"]) == 2
        # Re-ignoring known terms doesn't grow the list, so it stays allowed.
        assert gs.dismiss_glossary_proposals(did, ["a", "c"]) == {"changed": 1}
        assert gs.dismiss_glossary_proposals(did, ["a"]) == {"changed": 0}

    def test_status_checks_only_the_held_terms(self, isolated_db, monkeypatch):
        did, sid = _drama(isolated_db)
        isolated_db.add_glossary_dismissals(sid, ["路人", "other"])
        monkeypatch.setattr(db, "list_glossary_dismissals",
                            lambda *a: pytest.fail("status must not load the whole list"))
        self._held(monkeypatch, did, gs._normalize_proposals(
            [_prop("青云宗"), _prop("路人")], [], ""))
        shown = gs.get_novel_glossary_status(did)["result"]["proposals"]
        assert [p["term"] for p in shown] == ["青云宗"]

    def test_dismissed_lookup_chunks_large_batches(self, isolated_db):
        _, sid = _drama(isolated_db)
        isolated_db.add_glossary_dismissals(sid, ["t7", "t1200"])
        found = isolated_db.list_dismissed_glossary_terms(sid, [f"t{i}" for i in range(1500)])
        assert found == {"t7", "t1200"}

    def test_dismissal_timestamp_has_no_timezone_suffix(self, isolated_db):
        _, sid = _drama(isolated_db)
        isolated_db.add_glossary_dismissals(sid, ["a"])
        assert "+" not in isolated_db.list_glossary_dismissals(sid)[0]["created_at"]

    def test_deleting_the_series_removes_its_ignore_list(self, isolated_db):
        _, sid = _drama(isolated_db)
        isolated_db.add_glossary_dismissals(sid, ["a"])
        with isolated_db.get_conn() as conn:
            conn.execute("DELETE FROM series WHERE id = ?", (sid,))
        assert isolated_db.list_glossary_dismissals(sid) == []


# --- API: permission and ownership ---------------------------------------------
pytest.importorskip("fastapi")
pytest.importorskip("httpx")
from fastapi.testclient import TestClient  # noqa: E402

from api import auth as api_auth  # noqa: E402
from api.api_config import ApiSettings  # noqa: E402
from api.server import create_app  # noqa: E402
from services import auth_service  # noqa: E402

REMOTE = "https://baihe.example.com"


def _login(email, *perms):
    u = auth_service.add_user(email)
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return u["id"], {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                     api_auth.CSRF_HEADER: s["csrf_token"]}


def _url(did, tail=""):
    return f"/api/glossary/dramas/{did}/dismissals{tail}"


class TestApi:
    @pytest.fixture
    def world(self, isolated_db):
        client = TestClient(create_app(ApiSettings(auth_mode="on")), base_url=REMOTE,
                            raise_server_exceptions=False)
        a_id, a = _login("a@example.com")
        _, b = _login("b@example.com")
        sid = db.create_series("Private", owner_user_id=a_id, is_private=True)
        did = db.create_drama(title_en="D", series_id=sid, owner_user_id=a_id)
        return {"c": client, "a": a, "b": b, "did": did}

    def test_round_trip_for_the_owner(self, world):
        c, a, did = world["c"], world["a"], world["did"]
        assert c.post(_url(did), json={"terms": ["路人"]}, headers=a).json() == {"changed": 1}
        assert c.get(_url(did), headers=a).json()["dismissals"][0]["term"] == "路人"
        assert c.post(_url(did, "/restore"), json={"terms": ["路人"]}, headers=a).json() \
            == {"changed": 1}

    def test_other_users_private_series_is_404_on_every_route(self, world):
        c, b, did = world["c"], world["b"], world["did"]
        assert c.get(_url(did), headers=b).status_code == 404
        assert c.post(_url(did), json={"terms": ["x"]}, headers=b).status_code == 404
        assert c.post(_url(did, "/restore"), json={"terms": ["x"]}, headers=b).status_code == 404

    def test_writes_need_lines_edit_and_csrf(self, world, isolated_db):
        c = world["c"]
        u, h = _login("ro@example.com")
        with db.get_conn() as conn:
            conn.execute("DELETE FROM user_permissions WHERE user_id = ? AND permission = ?",
                         (u, "lines.edit"))
        did = db.create_drama(title_en="Shared", series_id=db.create_series("Shared"))
        assert c.get(_url(did), headers=h).status_code == 200      # library.read
        assert c.post(_url(did), json={"terms": ["x"]}, headers=h).status_code == 403
        assert c.post(_url(did, "/restore"), json={"terms": ["x"]}, headers=h).status_code == 403
        no_csrf = {k: v for k, v in world["a"].items() if k != api_auth.CSRF_HEADER}
        assert c.post(_url(did), json={"terms": ["x"]}, headers=no_csrf).status_code == 403

    def test_body_is_validated(self, world):
        c, a, did = world["c"], world["a"], world["did"]
        for body in ({"terms": []}, {"terms": [""]}, {"terms": ["x"], "extra": 1}, {}):
            assert c.post(_url(did), json=body, headers=a).status_code == 422
