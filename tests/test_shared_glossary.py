"""Shared glossary groups: the effective glossary (group terms overlaid by the
series' own), the consumers that read it, and who may change a group."""

import pytest

import db
from core import Line
from lib.errors import ConflictError, NotFoundError
from services import glossary_service, name_fix_service, shared_glossary_service as svc
from services import workspace_job_service

LOCAL = {"user_id": None, "is_admin": True, "is_local_owner": True}


def _p(uid):
    return {"user_id": uid, "is_admin": False, "is_local_owner": False, "admin_override": False}


def _group_with(name="Streamer", terms=()):
    gid = svc.create_group(LOCAL, name)["id"]
    for original, translation, extra in terms:
        svc.save_term(LOCAL, gid, {"term_original": original, "term_translation": translation, **extra})
    return gid


def test_series_term_wins_and_origin_is_labelled(isolated_db):
    sid = db.get_or_create_series("Show")
    gid = _group_with(terms=[("草", "lol", {}), ("ヤロ", "Yaro", {})])
    db.upsert_glossary_term(sid, "草", "grass")
    svc.set_series_group(LOCAL, sid, gid)
    by_original = {t["term_original"]: t for t in db.list_effective_glossary_terms(sid)}
    assert by_original["草"]["term_translation"] == "grass" and by_original["草"]["origin"] == "series"
    assert by_original["ヤロ"]["origin"] == "shared" and by_original["ヤロ"]["origin_name"] == "Streamer"
    assert by_original["ヤロ"]["id"] < 0


def test_leaving_and_deleting_a_group_keep_own_terms(isolated_db):
    sid = db.get_or_create_series("Show")
    other = db.get_or_create_series("Other")
    gid = _group_with(terms=[("ヤロ", "Yaro", {})])
    db.upsert_glossary_term(sid, "own", "mine")
    svc.set_series_group(LOCAL, sid, gid)
    svc.set_series_group(LOCAL, other, gid)
    svc.set_series_group(LOCAL, sid, None)
    assert [t["term_original"] for t in db.list_effective_glossary_terms(sid)] == ["own"]
    out = svc.delete_group(LOCAL, gid)
    assert out["series_unlinked"] == 1
    assert db.get_series_shared_group_id(other) is None
    assert [t["term_original"] for t in db.list_glossary_terms(sid)] == ["own"]
    assert db.list_shared_glossary_terms(gid) == []


def test_series_without_group_reads_only_its_own_terms(isolated_db):
    sid = db.get_or_create_series("Show")
    db.upsert_glossary_term(sid, "a", "A")
    assert [(t["term_original"], t["origin"]) for t in db.list_effective_glossary_terms(sid)] == [("a", "series")]


def test_translation_prompt_inputs_name_fix_and_own_listing(isolated_db):
    sid = db.get_or_create_series("Show")
    did = db.create_drama(title_en="T", series_id=sid, source_language="ja")
    db.save_lines(did, [Line(idx=0, start=0, end=1, zh="ヤローが来た", en="")])
    gid = _group_with(terms=[("ヤロ", "Yaro", {"aliases": ["ヤロー"]})])
    svc.set_series_group(LOCAL, sid, gid)
    glossary_terms, _style, _names = workspace_job_service.build_run_style_context(
        did, db.get_drama(did), db.load_line_objects(did), "audio_drama", False)
    assert "ヤロ" in [t["term_original"] for t in glossary_terms]
    assert name_fix_service.apply_known_aliases(did)["lines_changed"] == 1
    # The title's own glossary tab still lists and edits only the series' terms.
    assert glossary_service.list_glossary_terms(did) == []
    with pytest.raises(NotFoundError):
        glossary_service.upsert_glossary_term(did, {"id": -1, "term_translation": "x"})


def test_group_names_are_unique_ignoring_case(isolated_db):
    _group_with("Streamer")
    with pytest.raises(ConflictError):
        svc.create_group(LOCAL, " streamer ")


def test_only_creator_or_override_changes_a_group(isolated_db):
    a, b = db.auth_create_user("a@example.com"), db.auth_create_user("b@example.com")
    gid = svc.create_group(_p(a), "Mine")["id"]
    svc.save_term(_p(a), gid, {"term_original": "x", "term_translation": "y"})
    for call in (lambda: svc.save_term(_p(b), gid, {"term_original": "z", "term_translation": "y"}),
                 lambda: svc.delete_group(_p(b), gid),
                 lambda: svc.delete_term(_p(b), gid, 1)):
        with pytest.raises(NotFoundError):
            call()
    assert [t["term_original"] for t in svc.list_terms(_p(b), gid)] == ["x"]
    assert svc.list_groups(_p(b))["items"][0]["can_edit"] is False
    svc.delete_term(LOCAL, gid, db.list_shared_glossary_terms(gid)[0]["id"])


def test_joining_needs_an_editable_series(isolated_db):
    a, b = db.auth_create_user("a@example.com"), db.auth_create_user("b@example.com")
    sid = db.create_series("Priv", owner_user_id=a, is_private=True)
    gid = _group_with()
    with pytest.raises(NotFoundError):
        svc.set_series_group(_p(b), sid, gid)
    with pytest.raises(NotFoundError):
        svc.set_series_group(_p(a), sid, 999)
    assert svc.set_series_group(_p(a), sid, gid)["group"]["id"] == gid


def test_term_fields_are_validated_like_series_terms(isolated_db):
    gid = _group_with()
    with pytest.raises(Exception):
        svc.save_term(LOCAL, gid, {"term_original": "x", "term_translation": "y", "category": "nope"})
    first = svc.save_term(LOCAL, gid, {"term_original": "x", "term_translation": "y", "notes": "keep"})
    again = svc.save_term(LOCAL, gid, {"term_original": "x", "term_translation": "z"})
    assert again["id"] == first["id"] and again["notes"] == "keep"
    other = svc.save_term(LOCAL, gid, {"term_original": "w", "term_translation": "v"})
    with pytest.raises(ConflictError):
        svc.save_term(LOCAL, gid, {"id": other["id"], "term_original": "x"})


def test_routes_join_view_and_leave(isolated_db):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from api.api_config import ApiSettings
    from api.server import create_app
    from tests.api_helpers import loopback_client

    c = loopback_client(create_app(ApiSettings(auth_mode="off", serve_frontend=False)))
    sid = db.get_or_create_series("Show")
    db.upsert_glossary_term(sid, "own", "mine")
    group = c.post("/api/shared-glossaries", json={"name": "Streamer"}).json()
    assert c.post(f"/api/shared-glossaries/{group['id']}/terms",
                  json={"term_original": "草", "term_translation": "lol"}).status_code == 200
    view = c.post(f"/api/series/{sid}/shared-glossary", json={"group_id": group["id"]}).json()
    assert {(t["term_original"], t["origin"]) for t in view["terms"]} == {("own", "series"), ("草", "shared")}
    left = c.post(f"/api/series/{sid}/shared-glossary", json={"group_id": None}).json()
    assert left["group"] is None and [t["term_original"] for t in left["terms"]] == ["own"]
    assert c.post(f"/api/series/{sid}/shared-glossary", json={"group_id": 999}).status_code == 404
