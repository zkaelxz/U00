"""The comic editor's backend: change one text box under compare-and-set on
the page rev, undo the latest change, re-render that one page.

Mocked: no models or network. The render tests run the real Pillow/OpenCV
render on tiny synthetic pages."""

import importlib.util
import io
import json
import os
import threading

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")
pytest.importorskip("PIL")

from fastapi.testclient import TestClient
from PIL import Image

import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import scanlate_pages_service as pages_svc
from tests.job_helpers import wait_for_job

needs_cv2 = pytest.mark.skipif(importlib.util.find_spec("cv2") is None,
                               reason="OpenCV is not installed")


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _png(w, h):
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, "PNG")
    return buf.getvalue()


def _region(x=10, **kw):
    b = {"x": x, "y": 10, "w": 80, "h": 40, "source_text": "你好", "translated_text": "Hello",
         "kind": "bubble", "font_size": 14, "skip": False, "font_category": "regular"}
    b.update(kw)
    return b


def _page(**drama_kw):
    did = db.create_drama(title_en="Comic", source_language="zh", **drama_kw)
    folder = os.path.join(db.drama_dir(did), "pages")
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "page_0000.png"), "wb") as f:
        f.write(_png(200, 120))
    pid = db.create_page(did, 0, "pages/page_0000.png", 200, 120)
    db.save_bubbles(pid, [_region(10), _region(110, translated_text="Bye", w=60)])
    return did, pid, [b["id"] for b in db.load_bubbles(pid)]


def _url(did, pid, rid):
    return f"/api/scanlate/dramas/{did}/pages/{pid}/regions/{rid}"


def _undo(client, did, pid, rev, **kw):
    return client.post(f"/api/scanlate/dramas/{did}/pages/{pid}/undo",
                       json={"expected_rev": rev}, **kw)


def _row(pid, rid):
    return next(b for b in db.load_bubbles(pid) if b["id"] == rid)


# --- edit ---------------------------------------------------------------------

def test_patch_changes_one_box_and_bumps_rev(client):
    did, pid, (rid, other) = _page()
    r = client.patch(_url(did, pid, rid), json={
        "expected_rev": 0, "expected": {"translated_text": "Hello"},
        "changes": {"translated_text": "Hi there", "x": 20, "y": 30, "w": 90, "h": 50,
                    "font_size": 20, "font_category": "bold", "text_color": "#FF0000"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rev"] == 1 and body["undo_depth"] == 1
    box = next(g for g in body["regions"] if g["id"] == rid)
    assert (box["translated_text"], box["x"], box["y"], box["w"], box["h"]) == \
        ("Hi there", 20, 30, 90, 50)
    assert (box["font_size"], box["font_category"], box["text_color"]) == (20, "bold", "#ff0000")
    # the other box, the ids and the untouched fields of this one stay as they were
    assert [b["id"] for b in db.load_bubbles(pid)] == [rid, other]
    assert _row(pid, other)["translated_text"] == "Bye"
    assert _row(pid, rid)["source_text"] == "你好"
    assert db.LIBRARY_DIR not in r.text and "page_0000" not in r.text


def test_patch_with_the_values_already_there_writes_nothing(client):
    did, pid, (rid, _o) = _page()
    r = client.patch(_url(did, pid, rid), json={"expected_rev": 0,
                                                "changes": {"translated_text": "Hello"}})
    assert r.status_code == 200 and r.json()["rev"] == 0 and r.json()["undo_depth"] == 0


def test_text_color_null_puts_the_automatic_colour_back(client):
    did, pid, (rid, _o) = _page()
    client.patch(_url(did, pid, rid), json={"expected_rev": 0,
                                            "changes": {"text_color": "#123456"}})
    r = client.patch(_url(did, pid, rid), json={"expected_rev": 1,
                                                "changes": {"text_color": None}})
    assert r.status_code == 200
    assert _row(pid, rid)["text_color"] is None


def test_stale_rev_is_409_and_writes_nothing(client):
    did, pid, (rid, other) = _page()
    # a job (or the bridge) rewrote a box after the editor read the page
    assert db.update_bubble_fields(other, {"translated_text": "From the job"})
    r = client.patch(_url(did, pid, rid), json={"expected_rev": 0,
                                                "changes": {"translated_text": "Mine"}})
    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"reason": "stale", "rev": 1}
    assert _row(pid, rid)["translated_text"] == "Hello"
    assert db.get_page(pid)["rev"] == 1


def test_a_job_replacing_the_page_is_409(client):
    did, pid, ids = _page()
    assert db.replace_bubbles_if_unchanged(pid, ids, [_region(5, translated_text="New")],
                                           expected_rev=0) is not None
    r = client.patch(_url(did, pid, ids[0]), json={"expected_rev": 0,
                                                   "changes": {"translated_text": "Mine"}})
    assert r.status_code == 409
    assert [b["translated_text"] for b in db.load_bubbles(pid)] == ["New"]


def test_expected_value_mismatch_is_409(client):
    did, pid, (rid, _o) = _page()
    r = client.patch(_url(did, pid, rid), json={
        "expected_rev": 0, "expected": {"translated_text": "Something else"},
        "changes": {"translated_text": "Mine"}})
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "stale"
    assert _row(pid, rid)["translated_text"] == "Hello" and db.get_page(pid)["rev"] == 0


@pytest.mark.parametrize("changes", [
    {"source_text": "x"}, {"skip": True}, {"kind": "speech"}, {"kind": None}, {"kind": ""},
    {"idx": 3}, {"page_id": 2},
    {"translated_text": None}, {"x": None}, {"x": -1}, {"w": 0}, {"font_size": 2},
    {"font_size": 500}, {"font_category": "comic-sans"}, {"text_color": "red"},
    {"text_color": "#12345"}, {"translated_text": "x" * 2001},
    {"x": 150},                                   # 150 + 80 runs past the 200 px page
    {"y": 100, "h": 30}, {"w": 300}, {}])
def test_unknown_or_invalid_changes_are_422(client, changes):
    did, pid, (rid, _o) = _page()
    r = client.patch(_url(did, pid, rid), json={"expected_rev": 0, "changes": changes})
    assert r.status_code == 422, (changes, r.text)
    assert db.get_page(pid)["rev"] == 0 and _row(pid, rid)["x"] == 10


@pytest.mark.parametrize("kind", ["bubble", "thought", "narration", "caption", "sign", "sfx"])
def test_patch_sets_any_known_kind(client, kind):
    did, pid, (rid, other) = _page()
    r = client.patch(_url(did, pid, rid), json={"expected_rev": 0, "expected": {"kind": "bubble"},
                                                "changes": {"kind": kind}})
    assert r.status_code == 200, r.text
    assert next(g for g in r.json()["regions"] if g["id"] == rid)["kind"] == kind
    assert _row(pid, rid)["kind"] == kind and _row(pid, other)["kind"] == "bubble"
    assert r.json()["rev"] == (0 if kind == "bubble" else 1)


def test_kind_edit_with_a_stale_rev_or_kind_is_409_and_writes_nothing(client):
    did, pid, (rid, other) = _page()
    assert db.update_bubble_fields(other, {"translated_text": "From the job"})
    r = client.patch(_url(did, pid, rid), json={"expected_rev": 0, "changes": {"kind": "sfx"}})
    assert r.status_code == 409 and r.json()["error"]["details"] == {"reason": "stale", "rev": 1}
    # the box's kind moved on since the editor read it
    assert db.update_bubble_fields(rid, {"kind": "sign"})
    r = client.patch(_url(did, pid, rid), json={"expected_rev": 2, "expected": {"kind": "bubble"},
                                                "changes": {"kind": "sfx"}})
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "stale"
    assert _row(pid, rid)["kind"] == "sign" and db.get_page(pid)["rev"] == 2


def _latest_entry(pid):
    return json.loads(db.get_page(pid)["edit_history"])[-1]


def test_undo_of_a_kind_edit_puts_the_kind_back(client):
    did, pid, (rid, _o) = _page()
    client.patch(_url(did, pid, rid), json={"expected_rev": 0, "changes": {
        "kind": "narration", "translated_text": "Meanwhile"}})
    entry = _latest_entry(pid)
    assert (entry["before"]["kind"], entry["after"]["kind"]) == ("bubble", "narration")
    r = _undo(client, did, pid, 1)
    assert r.status_code == 200 and r.json()["undo_depth"] == 0
    assert (_row(pid, rid)["kind"], _row(pid, rid)["translated_text"]) == ("bubble", "Hello")


def test_choosing_sfx_also_includes_the_box_and_undo_takes_both_back(client):
    import scanlate_regions
    did, pid, (rid, _o) = _page()
    r = client.patch(_url(did, pid, rid), json={"expected_rev": 0, "changes": {"kind": "sfx"}})
    assert r.status_code == 200, r.text
    box = next(g for g in r.json()["regions"] if g["id"] == rid)
    assert (box["kind"], box["include_sfx"]) == ("sfx", True)
    entry = _latest_entry(pid)
    assert entry["before"] == {"kind": "bubble", "include_sfx": 0}
    assert entry["after"] == {"kind": "sfx", "include_sfx": 1}
    assert not scanlate_regions.region_excluded_from_auto(_row(pid, rid))
    assert _undo(client, did, pid, 1).status_code == 200
    assert (_row(pid, rid)["kind"], _row(pid, rid)["include_sfx"]) == ("bubble", 0)


def test_a_detected_sfx_box_is_included_once_its_kind_is_applied(client):
    did, pid, (rid, _o) = _page()
    assert db.update_bubble_fields(rid, {"kind": "sfx"})
    # other edits leave the detector's choice alone
    client.patch(_url(did, pid, rid), json={"expected_rev": 1, "changes": {"x": 12}})
    assert _row(pid, rid)["include_sfx"] == 0
    r = client.patch(_url(did, pid, rid), json={"expected_rev": 2, "changes": {"kind": "sfx"}})
    assert r.status_code == 200 and r.json()["rev"] == 3
    assert _row(pid, rid)["include_sfx"] == 1


def test_create_as_sfx_is_included(client):
    did, pid, _ids = _page()
    new = _create(client, did, pid, 0, kind="sfx").json()["regions"][-1]
    assert (new["kind"], new["include_sfx"]) == ("sfx", True)


def test_unknown_top_level_and_expected_fields_are_422(client):
    did, pid, (rid, _o) = _page()
    url = _url(did, pid, rid)
    for body in ({"expected_rev": 0, "changes": {"x": 1}, "force": True},
                 {"changes": {"x": 1}},
                 {"expected_rev": 0, "expected": {"skip": False}, "changes": {"x": 1}}):
        assert client.patch(url, json=body).status_code == 422, body
    assert db.get_page(pid)["rev"] == 0


def test_region_and_page_are_scoped_to_the_title(client):
    did, pid, (rid, _o) = _page()
    did2, pid2, (rid2, _o2) = _page()
    body = {"expected_rev": 0, "changes": {"translated_text": "x"}}
    assert client.patch(_url(did, pid, rid2), json=body).status_code == 404
    assert client.patch(_url(did, pid2, rid2), json=body).status_code == 404
    assert client.patch(_url(did, pid, 99999), json=body).status_code == 404
    assert client.patch(_url(99999, pid, rid), json=body).status_code == 404
    assert _row(pid2, rid2)["translated_text"] == "Hello"
    assert _undo(client, did, pid2, 0).status_code == 404


def test_db_edit_region_refuses_unlisted_columns(isolated_db):
    _did, pid, (rid, _o) = _page()
    with pytest.raises(ValueError):
        db.edit_region(pid, rid, {"page_id = 0, x": 1}, 0)
    with pytest.raises(ValueError):
        db.edit_region(pid, rid, {"x": 1}, 0, expected={"id": rid})


def test_concurrent_edits_on_one_rev_let_exactly_one_through(isolated_db):
    _did, pid, (rid, _o) = _page()
    results = []
    barrier = threading.Barrier(4)

    def go(n):
        barrier.wait()
        results.append(db.edit_region(pid, rid, {"translated_text": f"t{n}"}, 0)[0])
    threads = [threading.Thread(target=go, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["saved", "stale", "stale", "stale"]
    assert db.get_page(pid)["rev"] == 1


# --- undo ---------------------------------------------------------------------

def test_undo_reverses_edits_newest_first(client):
    did, pid, (rid, other) = _page()
    client.patch(_url(did, pid, rid), json={"expected_rev": 0, "changes": {
        "translated_text": "One", "x": 15, "text_color": "#00ff00"}})
    client.patch(_url(did, pid, other), json={"expected_rev": 1, "changes": {
        "translated_text": "Two"}})
    r = _undo(client, did, pid, 2)
    assert r.status_code == 200 and r.json()["rev"] == 3 and r.json()["undo_depth"] == 1
    assert _row(pid, other)["translated_text"] == "Bye"
    assert _row(pid, rid)["translated_text"] == "One"
    r = _undo(client, did, pid, 3)
    assert r.status_code == 200 and r.json()["undo_depth"] == 0
    row = _row(pid, rid)
    assert (row["translated_text"], row["x"], row["text_color"]) == ("Hello", 10, None)
    r = _undo(client, did, pid, 4)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "nothing_to_undo"


def test_an_edit_after_an_undo_goes_on_the_list(client):
    did, pid, (rid, _o) = _page()
    client.patch(_url(did, pid, rid), json={"expected_rev": 0, "changes": {"x": 11}})
    client.patch(_url(did, pid, rid), json={"expected_rev": 1, "changes": {"x": 12}})
    assert _undo(client, did, pid, 2).status_code == 200
    r = client.patch(_url(did, pid, rid), json={"expected_rev": 3, "changes": {"x": 13}})
    assert r.json()["undo_depth"] == 2
    assert _undo(client, did, pid, 4).status_code == 200 and _row(pid, rid)["x"] == 11
    assert _undo(client, did, pid, 5).status_code == 200 and _row(pid, rid)["x"] == 10


def test_undo_with_a_stale_rev_is_409_and_keeps_the_list(client):
    did, pid, (rid, _o) = _page()
    client.patch(_url(did, pid, rid), json={"expected_rev": 0, "changes": {"x": 11}})
    r = _undo(client, did, pid, 0)
    assert r.status_code == 409 and r.json()["error"]["details"] == {"reason": "stale", "rev": 1}
    assert _row(pid, rid)["x"] == 11
    assert _undo(client, did, pid, 1).status_code == 200 and _row(pid, rid)["x"] == 10


def test_undo_after_a_concurrent_change_is_409_and_never_overwrites_it(client):
    did, pid, (rid, other) = _page()
    client.patch(_url(did, pid, rid), json={"expected_rev": 0,
                                            "changes": {"translated_text": "Mine"}})
    # a job writes the page after the edit; the client reloads (rev 2) and undoes
    assert db.update_bubble_fields(rid, {"translated_text": "From the job"})
    assert client.get(f"/api/scanlate/dramas/{did}/pages/{pid}").json()["undo_depth"] == 0
    r = _undo(client, did, pid, 2)
    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"reason": "history_changed", "rev": 2}
    assert _row(pid, rid)["translated_text"] == "From the job"
    assert _row(pid, other)["translated_text"] == "Bye"
    assert db.get_page(pid)["rev"] == 2 and db.get_page(pid)["edit_history"] is None


def test_undo_after_the_page_was_replaced_is_409(client):
    did, pid, ids = _page()
    client.patch(_url(did, pid, ids[0]), json={"expected_rev": 0, "changes": {"x": 11}})
    db.save_bubbles(pid, [_region(5, translated_text="Fresh read")])   # reissues every id
    r = _undo(client, did, pid, 1)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "history_changed"
    assert [b["translated_text"] for b in db.load_bubbles(pid)] == ["Fresh read"]


def test_an_edit_after_a_foreign_write_starts_a_new_list(client):
    did, pid, (rid, other) = _page()
    client.patch(_url(did, pid, rid), json={"expected_rev": 0, "changes": {"x": 11}})
    db.update_bubble_fields(other, {"translated_text": "Job"})
    r = client.patch(_url(did, pid, rid), json={"expected_rev": 2, "changes": {"x": 12}})
    assert r.json()["undo_depth"] == 1
    assert _undo(client, did, pid, 3).status_code == 200
    assert _row(pid, rid)["x"] == 11                 # the edit before the job's write stays
    assert _undo(client, did, pid, 4).status_code == 409


def test_history_is_bounded(client):
    did, pid, (rid, _o) = _page()
    n = db.EDIT_HISTORY_LIMIT + 5
    for i in range(n):
        r = client.patch(_url(did, pid, rid), json={"expected_rev": i,
                                                    "changes": {"translated_text": f"v{i}"}})
        assert r.status_code == 200
    assert r.json()["undo_depth"] == db.EDIT_HISTORY_LIMIT
    rev = n
    for _ in range(db.EDIT_HISTORY_LIMIT):
        assert _undo(client, did, pid, rev).status_code == 200
        rev += 1
    # the oldest five edits fell off the list: the text is the one before the
    # earliest kept edit, and nothing further can be undone
    assert _row(pid, rid)["translated_text"] == "v4"
    assert _undo(client, did, pid, rev).json()["error"]["details"]["reason"] == "nothing_to_undo"


# --- permissions and ownership -------------------------------------------------

def _user(email, *perms):
    u = auth_service.add_user(email)
    for p in auth_service.effective_permissions(u["id"]):
        auth_service.revoke_permission(u["id"], p)
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return u["id"], {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                     api_auth.CSRF_HEADER: s["csrf_token"]}


def test_edit_and_undo_need_lines_edit(isolated_db):
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                   raise_server_exceptions=False)
    did, pid, (rid, _o) = _page()
    edit = {"expected_rev": 0, "changes": {"translated_text": "x"}}
    assert c.patch(_url(did, pid, rid), json=edit).status_code == 401
    _id, no = _user("no@example.com", "library.read", "lines.read", "jobs.start", "media.stream")
    assert c.patch(_url(did, pid, rid), json=edit, headers=no).status_code == 403
    assert _undo(c, did, pid, 0, headers=no).status_code == 403
    assert _row(pid, rid)["translated_text"] == "Hello"
    _id, yes = _user("yes@example.com", "lines.edit")
    assert c.patch(_url(did, pid, rid), json=edit, headers=yes).status_code == 200
    assert _undo(c, did, pid, 1, headers=yes).status_code == 200


def test_another_members_private_comic_is_404(isolated_db):
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                   raise_server_exceptions=False)
    owner_id, owner = _user("owner@example.com", "lines.edit", "lines.read")
    _other_id, other = _user("other@example.com", "lines.edit", "lines.read")
    did, pid, (rid, _o) = _page(owner_user_id=owner_id, is_private=1)
    edit = {"expected_rev": 0, "changes": {"translated_text": "x"}}
    assert c.patch(_url(did, pid, rid), json=edit, headers=other).status_code == 404
    assert _undo(c, did, pid, 0, headers=other).status_code == 404
    assert _row(pid, rid)["translated_text"] == "Hello"
    assert c.patch(_url(did, pid, rid), json=edit, headers=owner).status_code == 200


# --- render -------------------------------------------------------------------

def _render(client, did, pid):
    r = client.post(f"/api/scanlate/dramas/{did}/render", json={"page_id": pid})
    assert r.status_code == 200, r.text
    assert wait_for_job(f"scanlate_{did}", timeout=20)["status"] == "done"
    with open(os.path.join(db.drama_dir(did), db.get_page(pid)["rendered_filename"]), "rb") as f:
        return f.read()


@needs_cv2
def test_one_page_renders_the_same_twice_and_shows_the_edit(client):
    did, pid, (rid, _o) = _page()
    first = _render(client, did, pid)
    assert _render(client, did, pid) == first
    client.patch(_url(did, pid, rid), json={"expected_rev": 0, "changes": {
        "translated_text": "HELLO", "text_color": "#ff0000"}})
    edited = _render(client, did, pid)
    assert edited != first
    import numpy as np
    with Image.open(io.BytesIO(edited)) as im:
        px = np.asarray(im.convert("RGB").crop((10, 10, 90, 50))).reshape(-1, 3)
        assert ((px[:, 0] > 150) & (px[:, 1] < 100) & (px[:, 2] < 100)).any()   # red lettering
    assert _render(client, did, pid) == edited


def test_detail_reports_colour_and_undo_depth(client):
    did, pid, (rid, _o) = _page()
    detail = pages_svc.get_page_detail(did, pid)
    assert detail["undo_depth"] == 0
    assert all(g["text_color"] is None for g in detail["regions"])


# --- add and delete a box ---------------------------------------------------------

def _regions_url(did, pid):
    return f"/api/scanlate/dramas/{did}/pages/{pid}/regions"


def _create(client, did, pid, rev, **region):
    region = {"x": 20, "y": 60, "w": 50, "h": 30, **region}
    return client.post(_regions_url(did, pid), json={"expected_rev": rev, "region": region})


def _delete(client, did, pid, rid, rev, **kw):
    return client.delete(_url(did, pid, rid), params={"expected_rev": rev}, **kw)


def test_create_adds_one_box_at_the_end_and_bumps_rev(client):
    did, pid, (rid, other) = _page()
    r = _create(client, did, pid, 0, translated_text="New", font_size=22,
                font_category="bold", text_color="#00AA00")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rev"] == 1 and body["undo_depth"] == 1
    assert [g["id"] for g in body["regions"][:2]] == [rid, other]
    new = body["regions"][-1]
    assert new["id"] not in (rid, other)
    assert (new["x"], new["y"], new["w"], new["h"], new["translated_text"]) == (20, 60, 50, 30, "New")
    assert (new["font_size"], new["font_category"], new["text_color"]) == (22, "bold", "#00aa00")
    assert (new["source_text"], new["kind"], new["skip"]) == ("", "bubble", False)
    assert db.LIBRARY_DIR not in r.text and "page_0000" not in r.text


def test_create_with_only_the_box_takes_the_defaults(client):
    did, pid, _ids = _page()
    new = _create(client, did, pid, 0).json()["regions"][-1]
    assert (new["translated_text"], new["font_size"], new["font_category"], new["text_color"]) == \
        ("", 18, "regular", None)


def test_create_with_a_kind_keeps_it_and_its_undo_removes_the_box(client):
    did, pid, ids = _page()
    r = _create(client, did, pid, 0, kind="caption")
    assert r.status_code == 200, r.text
    new = r.json()["regions"][-1]
    assert new["kind"] == "caption" and _row(pid, new["id"])["kind"] == "caption"
    entry = _latest_entry(pid)
    assert entry["op"] == "create" and entry["after"]["kind"] == "caption"
    assert _undo(client, did, pid, 1).status_code == 200
    assert [b["id"] for b in db.load_bubbles(pid)] == ids


def test_create_with_a_stale_rev_is_409_and_writes_nothing(client):
    did, pid, (_rid, other) = _page()
    assert db.update_bubble_fields(other, {"translated_text": "From the job"})
    r = _create(client, did, pid, 0)
    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"reason": "stale", "rev": 1}
    assert len(db.load_bubbles(pid)) == 2 and db.get_page(pid)["rev"] == 1


@pytest.mark.parametrize("region", [
    {"x": 160},                                   # 160 + 50 runs past the 200 px page
    {"y": 100},                                   # 100 + 30 runs past the 120 px page
    {"w": 0}, {"h": -1}, {"x": -1}, {"w": None}, {"font_size": 3},
    {"font_category": "comic-sans"}, {"text_color": "blue"}, {"translated_text": "x" * 2001},
    {"translated_text": None}, {"source_text": "x"}, {"kind": "speech"}, {"kind": None},
    {"skip": True},
    {"idx": 0}, {"id": 1}, {"page_id": 2}])
def test_create_with_invalid_values_is_422(client, region):
    did, pid, _ids = _page()
    r = _create(client, did, pid, 0, **region)
    assert r.status_code == 422, (region, r.text)
    assert len(db.load_bubbles(pid)) == 2 and db.get_page(pid)["rev"] == 0


def test_create_needs_the_whole_box_and_no_extra_fields(client):
    did, pid, _ids = _page()
    for body in ({"expected_rev": 0, "region": {"x": 1, "y": 1, "w": 5}},
                 {"region": {"x": 1, "y": 1, "w": 5, "h": 5}},
                 {"expected_rev": 0, "region": {"x": 1, "y": 1, "w": 5, "h": 5}, "force": True}):
        assert client.post(_regions_url(did, pid), json=body).status_code == 422, body
    assert len(db.load_bubbles(pid)) == 2 and db.get_page(pid)["rev"] == 0


def test_a_page_holds_at_most_the_region_cap(client, monkeypatch):
    did, pid, _ids = _page()
    assert db.MAX_REGIONS_PER_PAGE == 200
    monkeypatch.setattr(db, "MAX_REGIONS_PER_PAGE", 3)
    assert _create(client, did, pid, 0).status_code == 200
    r = _create(client, did, pid, 1)
    assert r.status_code == 422 and "at most 3" in r.text
    assert len(db.load_bubbles(pid)) == 3 and db.get_page(pid)["rev"] == 1
    # the transaction re-checks the count, not only the service's early answer
    assert db.create_region(pid, {"x": 1, "y": 1, "w": 5, "h": 5}, 1) == ("full", 1, None)
    # deleting one makes room again
    rid = db.load_bubbles(pid)[0]["id"]
    assert _delete(client, did, pid, rid, 1).status_code == 200
    assert _create(client, did, pid, 2).status_code == 200


def test_delete_removes_one_box_and_keeps_the_others(client):
    did, pid, (rid, other) = _page()
    r = _delete(client, did, pid, rid, 0)
    assert r.status_code == 200, r.text
    assert r.json()["rev"] == 1 and r.json()["undo_depth"] == 1
    assert [g["id"] for g in r.json()["regions"]] == [other]
    assert _row(pid, other)["translated_text"] == "Bye"


def test_delete_with_a_stale_rev_is_409_and_writes_nothing(client):
    did, pid, (rid, other) = _page()
    assert db.update_bubble_fields(other, {"translated_text": "From the job"})
    r = _delete(client, did, pid, rid, 0)
    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"reason": "stale", "rev": 1}
    assert [b["id"] for b in db.load_bubbles(pid)] == [rid, other]


def test_delete_needs_the_rev(client):
    did, pid, (rid, _o) = _page()
    assert client.delete(_url(did, pid, rid)).status_code == 422
    assert client.delete(_url(did, pid, rid), params={"expected_rev": -1}).status_code == 422
    assert len(db.load_bubbles(pid)) == 2


def test_add_and_delete_are_scoped_to_the_title_and_page(client):
    did, pid, (rid, _o) = _page()
    did2, pid2, (rid2, _o2) = _page()
    assert _create(client, did, pid2, 0).status_code == 404
    assert _create(client, 99999, pid, 0).status_code == 404
    assert _delete(client, did, pid, rid2, 0).status_code == 404       # a box of another page
    assert _delete(client, did, pid2, rid2, 0).status_code == 404      # a page of another title
    assert _delete(client, did, pid, 99999, 0).status_code == 404
    assert len(db.load_bubbles(pid2)) == 2 and db.get_page(pid2)["rev"] == 0
    assert db.get_page(pid)["rev"] == 0


def test_db_create_region_refuses_unlisted_columns(isolated_db):
    _did, pid, _ids = _page()
    with pytest.raises(ValueError):
        db.create_region(pid, {"x": 1, "y": 1, "w": 5, "h": 5, "page_id": 9}, 0)
    with pytest.raises(ValueError):
        db.create_region(pid, {"x": 1, "y": 1, "w": 5}, 0)


# --- undoing adds and deletes ----------------------------------------------------

def test_undo_of_a_create_removes_the_box(client):
    did, pid, ids = _page()
    _create(client, did, pid, 0, translated_text="New")
    r = _undo(client, did, pid, 1)
    assert r.status_code == 200 and r.json()["rev"] == 2 and r.json()["undo_depth"] == 0
    assert [b["id"] for b in db.load_bubbles(pid)] == ids


def test_undo_of_a_delete_puts_the_box_back_with_every_value(client):
    did, pid, (rid, other) = _page()
    db.edit_region(pid, rid, {"skip": True, "kind": "narration", "language": "zh",
                              "text_color": "#112233", "include_sfx": True}, 0)
    before = db.load_bubbles(pid)
    assert _delete(client, did, pid, rid, 1).status_code == 200
    r = _undo(client, did, pid, 2)
    assert r.status_code == 200 and r.json()["rev"] == 3
    assert db.load_bubbles(pid) == before             # same id, idx and reading order
    assert [g["id"] for g in r.json()["regions"]] == [rid, other]
    assert r.json()["undo_depth"] == 1                # the edit before it is still undoable
    assert _undo(client, did, pid, 3).status_code == 200
    assert _row(pid, rid)["skip"] == 0 and _row(pid, rid)["text_color"] is None


def test_edit_then_delete_then_two_undos(client):
    did, pid, (rid, other) = _page()
    client.patch(_url(did, pid, rid), json={"expected_rev": 0, "changes": {
        "translated_text": "Changed", "x": 30}})
    _delete(client, did, pid, rid, 1)
    assert _undo(client, did, pid, 2).status_code == 200
    assert (_row(pid, rid)["translated_text"], _row(pid, rid)["x"]) == ("Changed", 30)
    r = _undo(client, did, pid, 3)
    assert r.status_code == 200 and r.json()["undo_depth"] == 0
    assert (_row(pid, rid)["translated_text"], _row(pid, rid)["x"]) == ("Hello", 10)
    assert [b["id"] for b in db.load_bubbles(pid)] == [rid, other]


def test_create_edit_delete_of_one_box_undo_all_the_way(client):
    did, pid, ids = _page()
    new = _create(client, did, pid, 0).json()["regions"][-1]["id"]
    client.patch(_url(did, pid, new), json={"expected_rev": 1,
                                            "changes": {"translated_text": "Typed"}})
    _delete(client, did, pid, new, 2)
    assert _undo(client, did, pid, 3).status_code == 200
    assert _row(pid, new)["translated_text"] == "Typed"
    assert _undo(client, did, pid, 4).status_code == 200
    assert _row(pid, new)["translated_text"] == ""
    assert _undo(client, did, pid, 5).status_code == 200
    assert [b["id"] for b in db.load_bubbles(pid)] == ids
    assert _undo(client, did, pid, 6).json()["error"]["details"]["reason"] == "nothing_to_undo"


def test_undo_of_a_delete_whose_id_was_taken_moves_the_older_entries_to_the_new_id(client):
    did, pid, ids = _page()
    _did2, pid2, _ids2 = _page()
    new = _create(client, did, pid, 0).json()["regions"][-1]["id"]
    client.patch(_url(did, pid, new), json={"expected_rev": 1, "changes": {"x": 40}})
    _delete(client, did, pid, new, 2)
    # bubbles.id never reissues an id by itself; force the clash by hand
    conn = db.get_conn()
    conn.execute("INSERT INTO bubbles (id, page_id, idx, x, y, w, h) "
                 "VALUES (?, ?, 9, 1, 1, 5, 5)", (new, pid2))
    conn.commit()
    conn.close()
    r = _undo(client, did, pid, 3)
    assert r.status_code == 200 and r.json()["undo_depth"] == 2
    back = r.json()["regions"][-1]
    assert back["id"] != new and back["x"] == 40
    assert _undo(client, did, pid, 4).status_code == 200            # the edit, on the new id
    assert _row(pid, back["id"])["x"] == 20
    assert _undo(client, did, pid, 5).status_code == 200            # the create, on the new id
    assert [b["id"] for b in db.load_bubbles(pid)] == ids
    assert [b["x"] for b in db.load_bubbles(pid2) if b["id"] == new] == [1]


def test_undo_of_a_delete_after_a_concurrent_write_is_409_and_writes_nothing(client):
    did, pid, (rid, other) = _page()
    _delete(client, did, pid, rid, 0)
    assert db.update_bubble_fields(other, {"translated_text": "From the job"})
    r = _undo(client, did, pid, 2)
    assert r.status_code == 409
    assert r.json()["error"]["details"] == {"reason": "history_changed", "rev": 2}
    assert [b["id"] for b in db.load_bubbles(pid)] == [other]
    assert _row(pid, other)["translated_text"] == "From the job"
    assert db.get_page(pid)["rev"] == 2 and db.get_page(pid)["edit_history"] is None


def test_undo_of_a_create_after_a_concurrent_write_is_409(client):
    did, pid, _ids = _page()
    new = _create(client, did, pid, 0).json()["regions"][-1]["id"]
    assert db.update_bubble_fields(new, {"translated_text": "From the job"})
    r = _undo(client, did, pid, 2)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "history_changed"
    assert _row(pid, new)["translated_text"] == "From the job"


def test_undo_after_a_replace_that_kept_the_rev_is_409(client):
    # the legacy save_bubbles reissues every id without bumping rev
    did, pid, (rid, _o) = _page()
    _delete(client, did, pid, rid, 0)
    db.save_bubbles(pid, [_region(5, translated_text="Fresh read")])
    assert client.get(f"/api/scanlate/dramas/{did}/pages/{pid}").json()["undo_depth"] == 0
    r = _undo(client, did, pid, 1)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "history_changed"
    assert [b["translated_text"] for b in db.load_bubbles(pid)] == ["Fresh read"]


def test_history_cap_counts_adds_and_deletes(client):
    did, pid, ids = _page()
    rev = 0
    for _ in range(30):
        new = _create(client, did, pid, rev).json()["regions"][-1]["id"]
        r = _delete(client, did, pid, new, rev + 1)
        rev += 2
    assert r.json()["undo_depth"] == db.EDIT_HISTORY_LIMIT
    assert [b["id"] for b in db.load_bubbles(pid)] == ids


def test_a_job_that_read_the_page_before_an_add_or_delete_is_skipped(client):
    did, pid, ids = _page()
    _create(client, did, pid, 0)
    assert db.replace_bubbles_if_unchanged(pid, ids, [_region(5)], expected_rev=0) is None
    did, pid, ids = _page()
    _delete(client, did, pid, ids[0], 0)
    assert db.replace_bubbles_if_unchanged(pid, ids, [_region(5)], expected_rev=0) is None
    assert db.replace_bubbles_if_unchanged(pid, ids[1:], [_region(5)], expected_rev=0) is None
    assert [b["id"] for b in db.load_bubbles(pid)] == ids[1:]


def test_concurrent_create_and_delete_on_one_rev_let_exactly_one_through(isolated_db):
    _did, pid, (rid, _other) = _page()
    results = {}
    barrier = threading.Barrier(2)

    def create():
        barrier.wait()
        results["create"] = db.create_region(pid, {"x": 1, "y": 1, "w": 5, "h": 5}, 0)[0]

    def delete():
        barrier.wait()
        results["delete"] = db.delete_region(pid, rid, 0)[0]
    threads = [threading.Thread(target=create), threading.Thread(target=delete)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results.values()) in (["deleted", "stale"], ["saved", "stale"])
    assert db.get_page(pid)["rev"] == 1
    assert len(db.load_bubbles(pid)) == (3 if results["create"] == "saved" else 1)
    assert db.edit_history_depth(db.get_page(pid)) == 1


def test_add_and_delete_need_lines_edit(isolated_db):
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                   raise_server_exceptions=False)
    did, pid, (rid, _o) = _page()
    assert _create(c, did, pid, 0).status_code == 401
    assert _delete(c, did, pid, rid, 0).status_code == 401
    _id, no = _user("no@example.com", "library.read", "lines.read", "jobs.start", "media.stream")
    region = {"x": 1, "y": 1, "w": 5, "h": 5}
    assert c.post(_regions_url(did, pid), json={"expected_rev": 0, "region": region},
                  headers=no).status_code == 403
    assert _delete(c, did, pid, rid, 0, headers=no).status_code == 403
    assert len(db.load_bubbles(pid)) == 2 and db.get_page(pid)["rev"] == 0
    _id, yes = _user("yes@example.com", "lines.edit")
    assert c.post(_regions_url(did, pid), json={"expected_rev": 0, "region": region},
                  headers=yes).status_code == 200
    assert _delete(c, did, pid, rid, 1, headers=yes).status_code == 200


def test_add_and_delete_on_another_members_private_comic_are_404(isolated_db):
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                   raise_server_exceptions=False)
    owner_id, owner = _user("owner@example.com", "lines.edit", "lines.read")
    _other_id, other = _user("other@example.com", "lines.edit", "lines.read")
    did, pid, (rid, _o) = _page(owner_user_id=owner_id, is_private=1)
    region = {"x": 1, "y": 1, "w": 5, "h": 5}
    assert c.post(_regions_url(did, pid), json={"expected_rev": 0, "region": region},
                  headers=other).status_code == 404
    assert _delete(c, did, pid, rid, 0, headers=other).status_code == 404
    assert len(db.load_bubbles(pid)) == 2 and db.get_page(pid)["rev"] == 0
    assert _delete(c, did, pid, rid, 0, headers=owner).status_code == 200


def test_a_read_finishing_after_a_new_page_was_edited_keeps_the_edit(isolated_db):
    # A new page is editable the moment it is stored; the slow read must not wipe a box added meanwhile.
    from page_capture_checks import save_read_bubbles
    did = db.create_drama(title_en="Comic", source_language="zh")
    pid = db.create_page(did, 0, "pages/page_0000.png", 200, 120)
    status, rev, rid = db.create_region(pid, {"x": 5, "y": 5, "w": 40, "h": 20}, 0)
    assert status == "saved"
    notes = save_read_bubbles(pid, [_region(10)], newly_stored=True, reused_rev=0)
    assert notes and notes[0][0] == "warning"
    assert [b["id"] for b in db.load_bubbles(pid)] == [rid]


def test_a_read_on_an_untouched_new_page_is_saved(isolated_db):
    from page_capture_checks import save_read_bubbles
    did = db.create_drama(title_en="Comic", source_language="zh")
    pid = db.create_page(did, 0, "pages/page_0000.png", 200, 120)
    assert save_read_bubbles(pid, [_region(10)], newly_stored=True, reused_rev=0) == []
    assert len(db.load_bubbles(pid)) == 1


def _set_raw_history(pid, text, rev=1):
    conn = db.get_conn()
    try:
        conn.execute("UPDATE pages SET edit_history = ?, rev = ? WHERE id = ?", (text, rev, pid))
        conn.commit()
    finally:
        conn.close()


def test_a_deeply_nested_saved_history_reads_as_empty(isolated_db):
    _did, pid, _ids = _page()
    _set_raw_history(pid, "[" * 200000 + "]" * 200000)
    assert db.edit_history_depth(db.get_page(pid)) == 0


@pytest.mark.parametrize("entry", [
    {"op": 5, "region_id": 1, "rev_before": 0, "rev_after": 1},
    {"op": "edit", "region_id": 1, "before": [1], "after": {"translated_text": "x"},
     "rev_before": 0, "rev_after": 1},
    {"op": "edit", "region_id": 1, "before": {"translated_text": "x"}, "after": "x",
     "rev_before": 0, "rev_after": 1},
    "not an entry",
])
def test_malformed_history_entries_are_dropped_not_a_server_error(client, entry):
    did, pid, _ids = _page()
    _set_raw_history(pid, json.dumps([entry]))
    assert db.edit_history_depth(db.get_page(pid)) == 0
    r = _undo(client, did, pid, 1)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "nothing_to_undo"


def test_an_entry_whose_undo_raises_clears_the_history_as_broken(client):
    did, pid, _ids = _page()
    _set_raw_history(pid, json.dumps([{
        "op": "edit", "region_id": [1], "before": {"translated_text": "x"},
        "after": {"translated_text": "y"}, "rev_before": 0, "rev_after": 1}]))
    r = _undo(client, did, pid, 1)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "history_changed"
    assert db.get_page(pid)["edit_history"] is None
