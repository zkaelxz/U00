"""Moving a tracked series to another source: preview matching, history kept, a failed
move changes nothing, permissions. Fake adapters; no network."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service
from services import sources_search_service as search
from sources import chapter_order, registry, store
from sources.base import SourceAdapter
from sources.http import PacingPolicy
from sources.models import ChapterInfo, SeriesInfo
from tests.job_helpers import wait_for_job
from tests.sources_helpers import ScriptedTransport

HOST = "https://fake.invalid"
LISTINGS = {}      # (source, series_id) -> [(chapter_id, title)]


def _make(name, comic=True):
    class Fake(SourceAdapter):
        pass

    Fake.name = name
    Fake.display_name = name.title()

    def __init__(self, client=None, **kw):
        kw.setdefault("transport", ScriptedTransport({}))
        kw["policy"] = PacingPolicy(min_delay=0.0, max_delay=0.0, session_break_min_requests=0)
        SourceAdapter.__init__(self, client, **kw)

    def get_series(self, series_id):
        return SeriesInfo(name, series_id, "Series T", f"{HOST}/series/{series_id}")

    def get_chapters(self, series_id):
        return [ChapterInfo(name, series_id, cid, title, f"{HOST}/c/{cid}")
                for cid, title in LISTINGS[(name, series_id)]]

    def get_pages(self, ch):
        return []

    Fake.__init__, Fake.get_series, Fake.get_chapters = __init__, get_series, get_chapters
    if comic:
        Fake.get_pages = get_pages
    return Fake


@pytest.fixture
def world(isolated_db, monkeypatch):
    classes = {"old": _make("old"), "new": _make("new"), "text": _make("text", comic=False)}
    monkeypatch.setattr(registry, "adapter_classes", lambda: dict(classes))
    LISTINGS.clear()
    LISTINGS[("old", "o1")] = [("a1", "第1话"), ("a2", "第2话"), ("a3", "第3话"), ("a4", "番外")]
    LISTINGS[("new", "n1")] = [("b1", "Chapter 1"), ("b2", "Chapter 2"), ("b4", "Chapter 4"),
                               ("b5", "Chapter 5")]
    yield
    for jid in list(background_jobs.list_all_jobs()):
        if jid.startswith("sources_"):
            wait_for_job(jid, missing_ok=True)
            background_jobs.clear_job(jid)


@pytest.fixture
def client(world):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _tracked_old(drama_id=None, imported=("a1", "a2")):
    store.track_series("old", "o1", "Old title", "", drama_id, [
        ChapterInfo("old", "o1", cid, title, "") for cid, title in LISTINGS[("old", "o1")]])
    for cid in imported:
        store.record_imported("old", "o1", cid, drama_id)


def _open_new(client, name="new", series_id="n1"):
    assert client.post(f"/api/sources/{name}/series", json={"series_id": series_id}).status_code == 200
    wait_for_job(search.SERIES_JOB_PREFIX + name, missing_ok=True)


BODY = {"source": "old", "series_id": "o1", "new_source": "new", "new_series_id": "n1"}


def test_matching_pairs_by_number_not_by_title_wording():
    old = ["第1话", "第2话", "第3话", "番外"]
    new = ["Chapter 1", "Chapter 2", "Chapter 4", "番外"]
    pairs, old_only, new_only = chapter_order.match_chapters(old, new)
    assert pairs == [(0, 0), (1, 1), (3, 3)] and old_only == [2] and new_only == [2]


def test_matching_never_pairs_a_chapter_twice_or_guesses_without_a_key():
    pairs, old_only, new_only = chapter_order.match_chapters(["Ch 1", "Ch 1"], ["Chapter 1", "", "x"])
    assert pairs == [(0, 0)] and old_only == [1] and new_only == [1, 2]


def test_preview_lists_matched_old_only_and_new(client):
    did = db.create_drama(title_en="T", media_type="manhua")
    _tracked_old(did)
    _open_new(client)
    r = client.post("/api/sources/tracked/move/preview", json=BODY)
    assert r.status_code == 200, r.text
    body = r.json()
    assert [(c["title"], c["saved"]) for c in body["matched"]] == [
        ("Chapter 1", True), ("Chapter 2", True)]
    assert [(c["title"], c["saved"]) for c in body["only_old"]] == [("第3话", False), ("番外", False)]
    assert [c["title"] for c in body["new"]] == ["Chapter 4", "Chapter 5"]
    # A preview changes nothing.
    assert [r["series_id"] for r in store.list_tracked_series()] == ["o1"]


def test_move_keeps_history_and_announces_only_new_chapters(client):
    did = db.create_drama(title_en="T", media_type="manhua")
    _tracked_old(did)
    store.set_tracked_save("old", "o1", True, by_user_id=None)
    _open_new(client)
    r = client.post("/api/sources/tracked/move", json=BODY)
    assert r.status_code == 200, r.text
    (row,) = store.list_tracked_series()
    assert (row["source"], row["series_id"], row["drama_id"], row["save_cbz"]) == ("new", "n1", did, 1)
    assert row["title"] == "Old title"
    # The old import records are history and stay; the new ones stop a re-import.
    assert store.imported_chapter_ids("old", "o1", did) == {"a1", "a2"}
    assert store.imported_chapter_ids("new", "n1", did) == {"b1", "b2"}
    assert store.known_chapter_ids("new", "n1") == {"b1", "b2"}
    assert store.known_chapter_ids("old", "o1") == set()
    assert db.get_drama(did) is not None


def test_new_chapters_are_found_by_the_next_check(client):
    did = db.create_drama(title_en="T", media_type="manhua")
    _tracked_old(did)
    _open_new(client)
    assert client.post("/api/sources/tracked/move", json=BODY).status_code == 200
    from sources import chapter_check
    new = chapter_check.check_series(registry.get_adapter("new"), store.list_tracked_series()[0])
    assert [c.chapter_id for c in new] == ["b4", "b5"]


def test_failed_move_changes_nothing(client):
    did = db.create_drama(title_en="T", media_type="manhua")
    _tracked_old(did)
    _open_new(client)
    # The old rows are removed last; failing there must undo the new rows too.
    with store.connect() as conn:
        conn.execute("CREATE TRIGGER stop BEFORE DELETE ON known_chapters "
                     "BEGIN SELECT RAISE(ABORT, 'disk'); END")
    r = client.post("/api/sources/tracked/move", json=BODY)
    assert r.status_code >= 500
    with store.connect() as conn:
        conn.execute("DROP TRIGGER stop")
    assert [(r["source"], r["series_id"]) for r in store.list_tracked_series()] == [("old", "o1")]
    assert store.known_chapter_ids("new", "n1") == set()
    assert store.imported_chapter_ids("new", "n1", did) == set()
    assert store.imported_chapter_ids("old", "o1", did) == {"a1", "a2"}


def test_series_must_be_open_tracked_and_free(client):
    did = db.create_drama(title_en="T", media_type="manhua")
    _tracked_old(did)
    r = client.post("/api/sources/tracked/move", json=BODY)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "SERIES_NOT_LOADED"
    _open_new(client)
    assert client.post("/api/sources/tracked/move", json={**BODY, "series_id": "zzz"}).status_code == 404
    assert client.post("/api/sources/tracked/move", json={**BODY, "new_source": "nope"}).status_code == 404
    store.track_series("new", "n1", "Already", "", None)
    r = client.post("/api/sources/tracked/move", json=BODY)
    assert r.status_code == 409 and r.json()["error"]["details"]["reason"] == "ALREADY_TRACKED"
    assert [s["series_id"] for s in store.list_tracked_series() if s["source"] == "old"] == ["o1"]


def test_media_type_must_fit_the_new_source(client):
    did = db.create_drama(title_en="N", media_type="novel")
    _tracked_old(did, imported=())
    LISTINGS[("text", "t1")] = [("t1", "Chapter 1")]
    _open_new(client, "text", "t1")
    body = {**BODY, "new_source": "text", "new_series_id": "t1"}
    assert client.post("/api/sources/tracked/move", json=body).status_code == 200
    comic = db.create_drama(title_en="C", media_type="manhua")
    store.track_series("old", "o2", "Other", "", comic)
    LISTINGS[("text", "t2")] = [("t2", "Chapter 1")]
    _open_new(client, "text", "t2")
    r = client.post("/api/sources/tracked/move",
                    json={**body, "series_id": "o2", "new_series_id": "t2"})
    assert r.status_code == 422


def test_refused_while_a_check_or_the_titles_import_runs(client, monkeypatch):
    did = db.create_drama(title_en="T", media_type="manhua")
    _tracked_old(did)
    _open_new(client)
    monkeypatch.setattr(background_jobs, "get_status",
                        lambda jid: {"status": "running"} if jid == f"sourceimport_{did}"
                        else {"status": "done", "result": None}
                        if not jid.startswith(search.SERIES_JOB_PREFIX) else _series_status())
    assert client.post("/api/sources/tracked/move", json=BODY).status_code == 409
    assert [s["source"] for s in store.list_tracked_series()] == ["old"]


def _series_status():
    return {"status": "done", "result": {
        "kind": "series", "source": "new", "series_id": "n1", "info": {"title": "T", "url": ""},
        "chapters": [{"chapter_id": "b1", "title": "Chapter 1"}]}}


def test_permissions(world):
    c = TestClient(create_app(ApiSettings(auth_mode="on")), base_url="https://baihe.example.com",
                   raise_server_exceptions=False)
    owner = auth_service.add_user("a@example.com")
    other = auth_service.add_user("b@example.com")
    private = db.create_drama(title_en="Private", media_type="manhua", owner_user_id=owner["id"],
                              is_private=1)
    _tracked_old(private, imported=())

    def hdrs(user):
        s = auth_service.create_session(user["id"])
        return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
                api_auth.CSRF_HEADER: s["csrf_token"]}
    for path in ("/api/sources/tracked/move", "/api/sources/tracked/move/preview"):
        assert c.post(path, json=BODY).status_code == 401
        assert c.post(path, json=BODY, headers=hdrs(other)).status_code == 403
    auth_service.grant_permission(other["id"], "sources.import")
    # A title the person can't edit reads like an untracked series.
    for path in ("/api/sources/tracked/move", "/api/sources/tracked/move/preview"):
        assert c.post(path, json=BODY, headers=hdrs(other)).status_code == 404
    assert [s["series_id"] for s in store.list_tracked_series()] == ["o1"]
