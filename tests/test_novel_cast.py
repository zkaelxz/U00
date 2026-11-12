"""The cast proposal (services/novel_cast_service.py and /api/characters/dramas/{id}/cast-proposal):
people from the glossary scan's result with their pronouns; applying never replaces pronouns already set.
No model call is made anywhere here."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
from api.api_config import ApiSettings
from api.server import create_app
from core import Line
from lib.errors import InvalidInputError
from services import glossary_extract_service, novel_cast_service as svc


def _scan(monkeypatch, proposals, status="done"):
    monkeypatch.setattr(glossary_extract_service, "get_novel_glossary_status",
                        lambda drama_id: {"status": status, "result": {"proposals": proposals}})


def _p(term, name, reason, category="person_name", occ=5):
    return {"term": term, "suggested_translation": name, "category": category, "reason": reason, "occurrences": occ}


def _title(series=True, speakers=()):
    sid = db.get_or_create_series("S") if series else None
    did = db.create_drama(title_en="N", series_id=sid)
    db.save_lines(did, [Line(idx=i, start=float(i), end=i + 1.0, zh="话", en="", speaker=s)
                        for i, s in enumerate(speakers)])
    return did, sid


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False, headers={"X-Baihe-Local": "1"})


def test_the_proposal_lists_only_people_with_the_pronouns_the_scan_read(isolated_db, monkeypatch):
    did, _ = _title()
    _scan(monkeypatch, [
        _p("林晚", "Lin Wan", "she/her; the heroine, Shen Yi's wife", occ=9),
        _p("沈一", "Shen Yi", "He/him. Lin Wan's husband", occ=7),
        _p("某人", "Mou", "unclear; a passer-by", occ=1),
        _p("青云宗", "Qingyun Sect", "she/her", category="clan_sect")])
    out = svc.proposal(did)
    assert out["ready"] and [(p["name"], p["pronouns"]) for p in out["people"]] == [
        ("Lin Wan", "she/her"), ("Shen Yi", "he/him"), ("Mou", "")]
    assert out["people"][0]["who"] == "the heroine, Shen Yi's wife"
    assert out["people"][1]["who"] == "Lin Wan's husband"


def test_a_scan_that_is_not_done_gives_an_empty_not_ready_proposal(isolated_db, monkeypatch):
    did, _ = _title()
    _scan(monkeypatch, [_p("林晚", "Lin Wan", "she/her")], status="running")
    assert svc.proposal(did) == {"drama_id": did, "ready": False, "has_series": True, "people": []}


def test_existing_pronouns_are_shown_and_never_replaced(isolated_db, monkeypatch):
    did, sid = _title(speakers=["Lin Wan", "Shen Yi"])
    db.upsert_character(did, "Lin Wan", character_name="Lin Wan", pronouns="they/them")
    db.upsert_series_character(sid, "Old Man", gender="he/him")
    _scan(monkeypatch, [_p("林晚", "Lin Wan", "she/her"), _p("沈一", "Shen Yi", "he/him"),
                        _p("老人", "Old Man", "she/her")])
    people = {p["name"]: p for p in svc.proposal(did)["people"]}
    assert people["Lin Wan"]["current_pronouns"] == "they/them" and people["Lin Wan"]["exists"]
    assert people["Shen Yi"]["current_pronouns"] == "" and people["Shen Yi"]["exists"]
    report = svc.apply(did, [{"name": "Lin Wan", "pronouns": "she/her"}, {"name": "Shen Yi", "pronouns": "he/him"},
                             {"name": "Old Man", "pronouns": "she/her"}])
    assert report == {"set": ["Shen Yi"], "added": [], "kept": ["Lin Wan", "Old Man"], "not_applied": []}
    by_label = {c["speaker_label"]: c["pronouns"] for c in db.list_characters(did)}
    assert by_label["Lin Wan"] == "they/them" and by_label["Shen Yi"] == "he/him"
    assert [s["gender"] for s in db.list_series_characters(sid) if s["character_name"] == "Old Man"] == ["he/him"]


def test_a_new_name_becomes_a_series_character_and_without_a_series_has_nowhere_to_go(isolated_db):
    did, sid = _title(series=True)
    assert svc.apply(did, [{"name": "Lin Wan", "pronouns": "she/her"}])["added"] == ["Lin Wan"]
    assert [(s["character_name"], s["gender"]) for s in db.list_series_characters(sid)] == [("Lin Wan", "she/her")]
    assert svc.apply(did, [{"name": "Lin Wan", "pronouns": "he/him"}])["kept"] == ["Lin Wan"]
    alone, _ = _title(series=False)
    assert svc.apply(alone, [{"name": "Lin Wan", "pronouns": "she/her"}])["not_applied"] == ["Lin Wan"]


def test_an_empty_series_pronoun_is_filled_but_nothing_else_about_the_character_changes(isolated_db):
    did, sid = _title()
    db.upsert_series_character(sid, "Lin Wan", aliases="Wanwan", notes="keeps a diary")
    assert svc.apply(did, [{"name": "Lin Wan", "pronouns": "she/her"}])["set"] == ["Lin Wan"]
    [row] = db.list_series_characters(sid)
    assert (row["gender"], row["aliases"], row["notes"]) == ("she/her", "Wanwan", "keeps a diary")


def test_apply_refuses_unknown_pronouns_and_empty_choices(isolated_db):
    did, _ = _title()
    with pytest.raises(InvalidInputError):
        svc.apply(did, [{"name": "Lin Wan", "pronouns": "xe/xem"}])
    with pytest.raises(InvalidInputError):
        svc.apply(did, [])


def test_routes(client, isolated_db, monkeypatch):
    did, _ = _title()
    _scan(monkeypatch, [_p("林晚", "Lin Wan", "she/her; heroine")])
    r = client.get(f"/api/characters/dramas/{did}/cast-proposal")
    assert r.status_code == 200 and r.json()["people"][0]["pronouns"] == "she/her"
    r = client.post(f"/api/characters/dramas/{did}/cast-proposal/apply",
                    json={"people": [{"name": "Lin Wan", "pronouns": "she/her"}]})
    assert r.status_code == 200 and r.json()["added"] == ["Lin Wan"]
    assert client.post(f"/api/characters/dramas/{did}/cast-proposal/apply",
                       json={"people": [{"name": "Lin Wan", "pronouns": "xe"}]}).status_code == 422
