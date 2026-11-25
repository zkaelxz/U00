"""Watch bookmarks: the routes as the PC uses them, and the per-account rules in the service and database."""

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

import db
from lib.errors import ConflictError, NotFoundError
from services import watch_bookmark_service as bookmarks


@pytest.fixture
def title(isolated_db):
    return db.create_drama(title_en="Stream")


def test_routes_save_list_rename_and_delete(client, title):
    url = f"/api/reader/dramas/{title}/bookmarks"
    late = client.post(url, json={"at_seconds": 90.5, "note": "  funny  "})
    early = client.post(url, json={"at_seconds": 12})
    assert late.status_code == 200 and early.status_code == 200, late.text
    assert late.json()["note"] == "funny" and early.json()["note"] == ""
    listed = client.get(url).json()
    assert [b["at_seconds"] for b in listed["bookmarks"]] == [12, 90.5]      # by moment, not by when saved
    changed = client.patch(f"{url}/{early.json()['id']}", json={"note": "intro"})
    assert changed.json()["note"] == "intro"
    assert client.delete(f"{url}/{late.json()['id']}").json() == {"id": late.json()["id"]}
    assert [b["note"] for b in client.get(url).json()["bookmarks"]] == ["intro"]


def test_routes_refuse_bad_moments_long_notes_and_unknown_ones(client, title):
    url = f"/api/reader/dramas/{title}/bookmarks"
    assert client.post(url, json={"at_seconds": -1}).status_code == 422
    assert client.post(url, json={"at_seconds": 1, "note": "x" * 301}).status_code == 422
    assert client.post(url, json={"at_seconds": 1, "extra": 1}).status_code == 422
    assert client.patch(f"{url}/999", json={"note": "x"}).status_code == 404
    assert client.delete(f"{url}/999").status_code == 404
    assert client.get("/api/reader/dramas/9999/bookmarks").status_code == 404
    assert client.get(url).json()["bookmarks"] == []


def test_each_account_sees_and_changes_only_its_own(title):
    mine = bookmarks.add_bookmark(title, 5, "mine", user_id=1)
    bookmarks.add_bookmark(title, 6, "pc")
    assert [b["note"] for b in bookmarks.list_bookmarks(title, 1)["bookmarks"]] == ["mine"]
    assert [b["note"] for b in bookmarks.list_bookmarks(title)["bookmarks"]] == ["pc"]
    with pytest.raises(NotFoundError):
        bookmarks.set_note(title, mine["id"], "taken", user_id=2)
    with pytest.raises(NotFoundError):
        bookmarks.delete_bookmark(title, mine["id"])
    assert bookmarks.list_bookmarks(title, 1)["bookmarks"][0]["note"] == "mine"


def test_a_title_holds_a_limited_number_per_account(title, monkeypatch):
    monkeypatch.setattr(bookmarks, "MAX_BOOKMARKS_PER_TITLE", 2)
    bookmarks.add_bookmark(title, 1, "")
    bookmarks.add_bookmark(title, 2, "")
    with pytest.raises(ConflictError):
        bookmarks.add_bookmark(title, 3, "")
    bookmarks.add_bookmark(title, 3, "", user_id=1)        # another account has its own room


def test_bookmarks_go_with_the_title(title):
    bookmarks.add_bookmark(title, 1, "x")
    db.delete_drama(title)
    with db.get_conn() as conn:
        assert conn.execute("SELECT COUNT(*) FROM watch_bookmarks").fetchone()[0] == 0
