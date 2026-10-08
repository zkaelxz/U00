"""The "works only with the browser extension" marker: storage, the write route, the status
fields, and the early stop on every import entry point. Mocked: no network, no real adapter
fetch (any fetch raises)."""

import sqlite3
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import db
from api.api_config import ApiSettings
from api.server import create_app
from services import sources_extension_service as ext
from services import url_guard
from sources import chapter_check, extension_marker, generic_import, health, ladder, store
from sources.base import SourceAdapter

SOURCE = "miaoqumh"
CHAPTER_URL = "https://www.miaoqumh.org/12/345.html"
OTHER_URL = "https://novel.example/book/1/ch2.html"


@pytest.fixture
def client(isolated_db, monkeypatch):
    monkeypatch.setattr(url_guard.socket, "getaddrinfo",
                        lambda host, port, **kw: [(2, 1, 6, "", ("93.184.216.34", port))])

    def no_network(*a, **kw):
        raise AssertionError("a marked source must not be fetched")

    monkeypatch.setattr(generic_import, "fetch_page", no_network)
    for method in ("search", "get_series", "get_chapters", "get_pages"):
        monkeypatch.setattr(SourceAdapter, method, no_network, raising=False)
    yield TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    for jid in list(background_jobs.list_all_jobs()):
        if jid.startswith(("sources_", "sourceimport_")):
            background_jobs.clear_job(jid)


def _mark(client, on=True, note=None):
    body = {"extension_only": on}
    if note is not None:
        body["note"] = note
    return client.post(f"/api/sources/{SOURCE}/extension-only", json=body)


def _assert_stopped(r):
    assert r.status_code == 400, r.text
    err = r.json()["error"]
    assert err["code"] == "extension_only"
    assert err["message"] == ext.EXTENSION_ONLY_MESSAGE
    assert err["details"] == {"reason": "EXTENSION_ONLY", "help": "settings"}
    assert "http" not in r.text


# --- store -----------------------------------------------------------------

def test_default_is_not_marked(isolated_db):
    assert extension_marker.is_marked(SOURCE) is False
    assert extension_marker.marked_sources() == {}
    assert extension_marker.view(SOURCE)["extension_only"] is False


def test_old_sources_db_gains_the_table(isolated_db):
    # A sources.db from before the marker existed has no such table; opening it adds one.
    path = store.db_path()
    store.connect().close()
    conn = sqlite3.connect(path)
    conn.execute("DROP TABLE source_extension_only")
    conn.commit()
    conn.close()
    store._initialised.discard(__import__("os").path.abspath(path))
    assert extension_marker.is_marked(SOURCE) is False
    extension_marker.mark(SOURCE, 7, "note")
    assert extension_marker.get(SOURCE)["marked_by_user_id"] == 7


def test_mark_keeps_first_date_and_updates_note(isolated_db):
    first = extension_marker.mark(SOURCE, 1, "a")
    time.sleep(0.01)
    again = extension_marker.mark(SOURCE, 2, "b")
    assert again["marked_at"] == first["marked_at"] and again["note"] == "b"
    assert again["marked_by_user_id"] == 1
    extension_marker.clear(SOURCE)
    assert extension_marker.get(SOURCE) is None


def test_note_validation():
    assert extension_marker.clean_note("  hi\x00there ") == "hi there"
    assert extension_marker.clean_note(None) == ""
    with pytest.raises(ValueError):
        extension_marker.clean_note("x" * (extension_marker.MAX_NOTE_LEN + 1))
    with pytest.raises(ValueError):
        extension_marker.clean_note(5)


def test_works_without_needs_a_later_automated_success():
    tiers = {"STATIC_HTTP": {"ok": True, "at": 100.0}, "RENDERED_BROWSER": {"ok": False, "at": 300.0}}
    assert extension_marker.works_without_extension(50.0, tiers) is True
    assert extension_marker.works_without_extension(150.0, tiers) is False
    assert extension_marker.works_without_extension(50.0, {"OFFICIAL_API": {"ok": True, "at": 900.0}}) is False


# --- route and fields -------------------------------------------------------

def test_mark_and_clear_through_the_route(client):
    r = _mark(client, True, "needs Chrome")
    assert r.status_code == 200
    body = r.json()
    assert body["extension_only"] is True and body["extension_note"] == "needs Chrome"
    assert body["extension_works_without"] is False and body["extension_marked_at"] > 0

    detail = client.get(f"/api/sources/{SOURCE}").json()
    assert detail["extension_only"] is True and detail["extension_marked_at"] == body["extension_marked_at"]
    listed = {s["name"]: s for s in client.get("/api/sources").json()}
    assert listed[SOURCE]["extension_only"] is True
    assert listed["manhuagui"]["extension_only"] is False

    r = _mark(client, False)
    assert r.json() == {"extension_only": False, "extension_marked_at": None,
                        "extension_note": "", "extension_works_without": False}
    assert client.get(f"/api/sources/{SOURCE}").json()["extension_only"] is False


def test_route_validation(client):
    assert _mark(client, True, "x" * 201).status_code == 422
    assert client.post(f"/api/sources/{SOURCE}/extension-only", json={"extension_only": "yes"}).status_code == 422
    assert client.post(f"/api/sources/{SOURCE}/extension-only", json={"extension_only": True, "x": 1}).status_code == 422
    assert client.post("/api/sources/nope/extension-only", json={"extension_only": True}).status_code == 404


def test_response_carries_no_paths_or_urls(client):
    _mark(client, True, f"see {db.LIBRARY_DIR}/x and https://h.example/p?token=abc123")
    text = client.get(f"/api/sources/{SOURCE}").text
    assert db.LIBRARY_DIR not in text and "token=abc123" not in text


def test_marker_leaves_the_tier_record_alone(client):
    caps = ladder.load_capabilities(SOURCE)
    caps.tiers["STATIC_HTTP"].tested = True
    caps.tiers["STATIC_HTTP"].reason = "EMPTY_SPA_SHELL"
    ladder.save_capabilities(SOURCE, caps)
    _mark(client)
    tiers = client.get(f"/api/sources/{SOURCE}").json()["tiers"]
    assert tiers["STATIC_HTTP"]["tested"] and not tiers["STATIC_HTTP"]["ok"]
    assert tiers["STATIC_HTTP"]["reason"] == "EMPTY_SPA_SHELL"
    assert tiers["USER_ASSISTED_BROWSER"]["tested"] is False


def test_later_static_success_sets_the_hint_but_keeps_the_marker(client):
    _mark(client)
    caps = ladder.load_capabilities(SOURCE)
    caps.tiers["STATIC_HTTP"].tested = caps.tiers["STATIC_HTTP"].ok = True
    caps.tiers["STATIC_HTTP"].at = time.time() + 5
    ladder.save_capabilities(SOURCE, caps)
    detail = client.get(f"/api/sources/{SOURCE}").json()
    assert detail["extension_only"] is True and detail["extension_works_without"] is True


def test_marked_source_is_left_out_of_failure_summaries(client):
    health.record_failure(SOURCE, "ConnectError", "boom")
    health.record_failure("manhuagui", "ConnectError", "boom")
    assert {r["source"] for r in health.recent_failures()} == {SOURCE, "manhuagui"}
    _mark(client)
    assert {r["source"] for r in health.recent_failures()} == {"manhuagui"}


# --- early stop on every entry point -----------------------------------------

def test_paste_a_link_stops_early(client):
    _mark(client)
    drama = db.create_drama(title_en="N", media_type="novel", content_mode="novel_narration")
    comic = db.create_drama(title_en="M", media_type="manhua")
    _assert_stopped(client.post("/api/sources/url/preview", json={"url": CHAPTER_URL}))
    _assert_stopped(client.post("/api/sources/url/import", json={"url": CHAPTER_URL, "drama_id": drama}))
    _assert_stopped(client.post("/api/sources/url/import-comic", json={"url": CHAPTER_URL, "drama_id": comic}))


def test_a_link_to_another_site_still_goes_ahead(client):
    _mark(client)
    r = client.post("/api/sources/url/preview", json={"url": OTHER_URL})
    assert r.status_code == 200


def test_unmarked_link_is_not_stopped(client):
    r = client.post("/api/sources/url/preview", json={"url": CHAPTER_URL})
    assert r.status_code == 200


def test_series_import_save_and_ai_recover_stop_early(client):
    _mark(client)
    comic = db.create_drama(title_en="M", media_type="manhua")
    _assert_stopped(client.post(f"/api/sources/{SOURCE}/series", json={"series_id": "12"}))
    _assert_stopped(client.post(f"/api/sources/{SOURCE}/import",
                                json={"series_id": "12", "chapter_ids": ["1"], "drama_id": comic}))
    _assert_stopped(client.post(f"/api/sources/{SOURCE}/save",
                                json={"series_id": "12", "chapter_ids": ["1"]}))


def test_tracking_stops_early(client):
    _mark(client)
    r = client.post("/api/sources/tracked", json={"source": SOURCE, "series_id": "12", "tracked": True,
                                                  "title": "T"})
    _assert_stopped(r)


def test_explicit_search_stops_and_a_general_search_skips_quietly(client):
    _mark(client)
    _assert_stopped(client.post("/api/sources/search", json={"query": "abc", "sources": [SOURCE]}))
    r = client.post("/api/sources/search", json={"query": "abc"})
    assert r.status_code == 200
    deadline = time.time() + 10
    while time.time() < deadline and (background_jobs.get_status("sources_search") or {}).get("status") in (
            "running", "queued"):
        time.sleep(0.02)
    result = client.get("/api/sources/jobs/sources_search/result").json()["result"]
    assert SOURCE not in result["errors"] and SOURCE not in result["per_source_counts"]


def test_scheduled_check_skips_quietly(isolated_db):
    store.track_series(SOURCE, "12", "Marked", url="https://www.miaoqumh.org/12/")
    store.track_series("manhuagui", "1", "Other", url="https://www.manhuagui.com/comic/1/")
    asked = []

    def factory(name):
        asked.append(name)
        raise RuntimeError("not reached")

    extension_marker.mark(SOURCE)
    summary = chapter_check.run_check_cycle(adapter_factory=factory)
    assert asked == ["manhuagui"]
    assert summary["extension_only"] == ["Marked"]
    assert "Marked" not in summary["errors"]
    row = {r["title"]: r for r in store.list_tracked_series()}["Marked"]
    assert not row.get("last_check_error")
    from services import sources_registry_service as registry_svc
    flags = {t["title"]: t["extension_only"] for t in registry_svc.list_tracked()}
    assert flags == {"Marked": True, "Other": False}
