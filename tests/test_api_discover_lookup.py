"""Discover network helpers (API batch 1, spec D-2). Mocked: no network, no LLM."""
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import bulk_import
import db
import metadata_lookup
import navigator
import title_library
import translate_engines
from api import auth as api_auth
from api.api_config import ApiSettings
from api.server import create_app
from services import auth_service, safe_fetch, settings_service
from services import discover_lookup_service as svc

KEY = "sk-ant-api03-SECRETKEYSECRETKEYSECRETKEY1234567890"
PAGE = safe_fetch.FetchResult("Menu\nLogin\nSome page text " * 10, False)


class FakeEngine:
    supports_reference = True

    def translate_batch(self, texts, ctx):
        return [f"译{t}" for t in texts]


@pytest.fixture(autouse=True)
def fakes(monkeypatch, isolated_db):
    for j in (svc.BULK_JOB_ID, svc.NAV_JOB_ID):
        background_jobs.clear_job(j)
    monkeypatch.setattr(settings_service, "resolve_key", lambda name: KEY)
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **k: FakeEngine())
    monkeypatch.setattr(safe_fetch, "fetch_public_text", lambda url: PAGE)
    monkeypatch.setattr(metadata_lookup, "extract_metadata_llm",
                        lambda text, eng: {"title_zh": "书", "summary": f"k={KEY}", "evil": "x"})
    monkeypatch.setattr(bulk_import, "extract_listing_entries_llm",
                        lambda text, eng, source_name="": [{"title": "A", "author": KEY}])
    monkeypatch.setattr(navigator, "translate_labels",
                        lambda labels, lang, eng: {l: f"EN {l}" for l in labels})
    monkeypatch.setattr(navigator, "generate_navigation_steps",
                        lambda *a, **k: f"1. Click Login (key {KEY})")
    monkeypatch.setattr(title_library, "search_baihehub",
                        lambda q: [{"title": "T", "url": "https://baihehub.com/books/1",
                                    "snippet": "s"}])
    yield
    for j in (svc.BULK_JOB_ID, svc.NAV_JOB_ID):
        _wait(j)
        background_jobs.clear_job(j)


@pytest.fixture
def client():
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _wait(job_id):
    for _ in range(250):
        s = background_jobs.get_status(job_id)
        if not s or s["status"] not in ("running", "queued"):
            return s
        time.sleep(0.02)
    raise AssertionError("job did not finish")


def _clean(r):
    assert KEY not in r.text and db.LIBRARY_DIR not in r.text
    return r.json()


def test_translate_query_and_baihehub(client):
    r = client.post("/api/discover/translate-query", json={"q": "princess", "engine": "claude"})
    assert r.status_code == 200 and _clean(r)["translated"] == "译princess"
    r = client.post("/api/discover/baihehub-search", json={"q": "百合"})
    assert r.status_code == 200 and _clean(r)["results"][0]["title"] == "T"


def test_import_suggestion_writes_nothing(client):
    r = client.post("/api/discover/import-suggestion",
                    json={"url": "https://example.com/book/1", "engine": "claude"})
    b = _clean(r)
    assert r.status_code == 200 and set(b["suggestion"]) == {"title_zh", "summary", "source_url"}
    assert db.list_known_titles() == []


def test_bulk_extract_then_commit(client):
    r = client.post("/api/discover/bulk-extract",
                    json={"urls": ["https://a.example/1"], "source_label": "jjwxc",
                          "engine": "claude"})
    assert r.status_code == 200 and r.json() == {"job_id": svc.BULK_JOB_ID, "started": True}
    _wait(svc.BULK_JOB_ID)
    res = client.get("/api/discover/bulk-extract/result")
    entries = _clean(res)["result"]["entries"]
    assert [e["title"] for e in entries] == ["A"]
    c = client.post("/api/discover/bulk-commit", json={"entries": entries, "source_label": "jjwxc"})
    assert c.status_code == 200 and c.json()["added"] == 1
    again = client.post("/api/discover/bulk-commit", json={"entries": entries})
    assert again.json() == {"added": 0, "skipped": 1, "ids": []}


def test_user_urls_come_back_without_query_or_fragment(client):
    """Spec section 5: a pasted URL can carry a signed token."""
    signed = "https://a.example/list/1?sig=TOKENVALUE123&page=2#frag"
    r = client.post("/api/discover/import-suggestion", json={"url": signed, "engine": "claude"})
    assert r.json()["suggestion"]["source_url"] == "https://a.example/list/1"
    assert "TOKENVALUE123" not in r.text and "frag" not in r.text
    client.post("/api/discover/bulk-extract", json={"urls": [signed], "engine": "claude"})
    _wait(svc.BULK_JOB_ID)
    res = client.get("/api/discover/bulk-extract/result")
    assert "TOKENVALUE123" not in res.text and "?" not in res.text
    b = res.json()["result"]
    assert b["pages"][0]["url"] == "https://a.example/list/1"
    assert b["entries"][0]["source_url"] == "https://a.example/list/1"
    assert "_source_urls" not in b


def test_ids_from_an_earlier_extraction_never_match_a_later_one(client, monkeypatch):
    """Extraction A, then B with the same shape, then committing A's ids:
    422 and nothing stored (not silently B's URLs)."""
    titles = {"https://a.example/1": "A book", "https://b.example/1": "B book"}
    monkeypatch.setattr(safe_fetch, "fetch_public_text",
                        lambda url: safe_fetch.FetchResult(titles[url], False))
    monkeypatch.setattr(bulk_import, "extract_listing_entries_llm",
                        lambda text, eng, source_name="": [{"title": text}])
    client.post("/api/discover/bulk-extract", json={"urls": ["https://a.example/1"],
                                                    "engine": "claude"})
    _wait(svc.BULK_JOB_ID)
    a_entries = client.get("/api/discover/bulk-extract/result").json()["result"]["entries"]
    client.post("/api/discover/bulk-extract", json={"urls": ["https://b.example/1"],
                                                    "engine": "claude"})
    _wait(svc.BULK_JOB_ID)
    b_entries = client.get("/api/discover/bulk-extract/result").json()["result"]["entries"]
    assert a_entries[0]["entry_id"] != b_entries[0]["entry_id"]
    r = client.post("/api/discover/bulk-commit", json={"entries": a_entries})
    assert r.status_code == 422
    assert db.list_known_titles() == []
    assert client.post("/api/discover/bulk-commit",
                       json={"entries": b_entries}).json()["added"] == 1
    assert [t["source_url"] for t in db.list_known_titles()] == ["https://b.example/1"]


def test_urls_differing_only_by_query_stay_distinct(client, monkeypatch):
    """Display strips the query; commit stores (and dedupes on) the full URL
    the server kept, looked up by entry_id -- never the client's copy."""
    pages = {"https://s.example/book.php?id=1": "Book One",
             "https://s.example/book.php?id=2": "Book Two"}
    monkeypatch.setattr(safe_fetch, "fetch_public_text",
                        lambda url: safe_fetch.FetchResult(pages[url], False))
    monkeypatch.setattr(bulk_import, "extract_listing_entries_llm",
                        lambda text, eng, source_name="": [{"title": text}])
    client.post("/api/discover/bulk-extract", json={"urls": list(pages), "engine": "claude"})
    _wait(svc.BULK_JOB_ID)
    res = client.get("/api/discover/bulk-extract/result")
    assert "id=" not in res.text
    entries = res.json()["result"]["entries"]
    assert [e["source_url"] for e in entries] == ["https://s.example/book.php"] * 2
    # a client that tampers with (or keeps) the display URL can't change what is stored
    entries[0]["source_url"] = "https://evil.example/x"
    c = client.post("/api/discover/bulk-commit", json={"entries": entries})
    assert c.status_code == 200 and c.json()["added"] == 2
    stored = sorted(r["source_url"] for r in db.list_known_titles())
    assert stored == sorted(pages)
    # a later book whose URL differs only by query is not deduped away
    pages["https://s.example/book.php?id=3"] = "Book Three"
    client.post("/api/discover/bulk-extract",
                json={"urls": ["https://s.example/book.php?id=3"], "engine": "claude"})
    _wait(svc.BULK_JOB_ID)
    third = client.get("/api/discover/bulk-extract/result").json()["result"]["entries"]
    assert client.post("/api/discover/bulk-commit", json={"entries": third}).json()["added"] == 1
    assert "https://s.example/book.php?id=3" in {r["source_url"] for r in db.list_known_titles()}
    # ids from the replaced result are gone
    r = client.post("/api/discover/bulk-commit", json={"entries": entries})
    assert r.status_code == 422
    # a manual entry (no entry_id) is stored as sent
    manual = {"title": "Manual", "source_url": "https://m.example/b?id=9"}
    assert client.post("/api/discover/bulk-commit", json={"entries": [manual]}).json()["added"] == 1
    assert "https://m.example/b?id=9" in {r["source_url"] for r in db.list_known_titles()}


def test_navigation_help(client):
    r = client.post("/api/discover/navigation-help",
                    json={"url": "https://site.example/", "goal": "find chapter list",
                          "engine": "claude"})
    assert r.status_code == 200
    _wait(svc.NAV_JOB_ID)
    b = _clean(client.get("/api/discover/navigation-help/result"))
    assert b["status"] == "done" and b["result"]["steps"].startswith("1. Click Login")
    assert b["result"]["labels"]["Menu"] == "EN Menu"


def test_errors(client, monkeypatch):
    assert client.get("/api/discover/bulk-extract/result").status_code == 404
    assert client.get("/api/discover/navigation-help/result").status_code == 404
    for path, body in (
            ("/api/discover/translate-query", {"q": "x", "engine": "deepl"}),
            ("/api/discover/translate-query", {"q": ""}),
            ("/api/discover/translate-query", {"q": "x", "api_key": KEY}),
            ("/api/discover/import-suggestion", {"url": "file:///etc/passwd"}),
            ("/api/discover/bulk-extract", {"urls": []}),
            ("/api/discover/bulk-extract", {"urls": [f"https://a.example/{i}" for i in range(11)]}),
            ("/api/discover/bulk-commit", {"entries": []}),
            ("/api/discover/bulk-commit", {"entries": [{"title": "x", "id": 3}]}),
            ("/api/discover/navigation-help", {"url": "https://a.example", "goal": "g",
                                               "target_language": "Klingon"})):
        r = client.post(path, json=body)
        assert r.status_code == 422, (path, body, r.text)
        assert KEY not in r.text
    monkeypatch.setattr(settings_service, "resolve_key", lambda name: None)
    r = client.post("/api/discover/translate-query", json={"q": "hello", "engine": "claude"})
    assert r.status_code == 503 and r.json()["error"]["code"] == "dependency_unavailable"


def test_second_job_is_429(client, monkeypatch):
    monkeypatch.setattr(background_jobs, "is_running", lambda j: True)
    r = client.post("/api/discover/bulk-extract", json={"urls": ["https://a.example/1"]})
    assert r.status_code == 429


def _headers(*perms, email="kid@example.com", admin=False):
    u = (auth_service.grant_admin_local(email) if admin else auth_service.add_user(email))
    for p in perms:
        auth_service.grant_permission(u["id"], p)
    s = auth_service.create_session(u["id"])
    return {"Cookie": f"{api_auth.COOKIE_NAME}={s['session_token']}",
            api_auth.CSRF_HEADER: s["csrf_token"]}


def test_auth_on_permissions():
    c = TestClient(create_app(ApiSettings(auth_mode="on")),
                   base_url="https://baihe.example.com", raise_server_exceptions=False)
    tq = ("/api/discover/translate-query", {"q": "princess", "engine": "claude"})
    tq_free = ("/api/discover/translate-query", {"q": "princess", "engine": "ollama"})
    sug = ("/api/discover/import-suggestion", {"url": "https://e.example/1", "engine": "claude"})
    sug_free = ("/api/discover/import-suggestion", {"url": "https://e.example/1",
                                                     "engine": "ollama"})
    commit = ("/api/discover/bulk-commit", {"entries": [{"title": "Z"}]})
    hub = ("/api/discover/baihehub-search", {"q": "百合"})
    assert c.post(hub[0], json=hub[1]).status_code == 401
    kid = _headers()
    assert c.post(hub[0], json=hub[1], headers=kid).status_code == 200
    assert c.post(tq[0], json=tq[1], headers=kid).status_code == 403        # paid engine
    assert c.post(tq_free[0], json=tq_free[1], headers=kid).status_code == 200
    assert c.post(sug_free[0], json=sug_free[1], headers=kid).status_code == 403  # no import_url
    assert c.post(commit[0], json=commit[1], headers=kid).status_code == 403
    importer = _headers("media.import_url", email="imp@example.com")
    assert c.post(sug[0], json=sug[1], headers=importer).status_code == 403  # paid
    assert c.post(sug_free[0], json=sug_free[1], headers=importer).status_code == 200
    spender = _headers("media.import_url", "engines.paid", email="pay@example.com")
    assert c.post(sug[0], json=sug[1], headers=spender).status_code == 200
    assert c.post(tq[0], json=tq[1], headers=spender).status_code == 200
    admin = _headers(email="admin@example.com", admin=True)
    assert c.post(commit[0], json=commit[1], headers=admin).status_code == 200
    # CSRF still required on these POSTs
    no_csrf = {"Cookie": kid["Cookie"]}
    assert c.post(hub[0], json=hub[1], headers=no_csrf).status_code == 403
