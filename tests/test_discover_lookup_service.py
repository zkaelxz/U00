"""Discover D-2: services/discover_lookup_service.py. Mocked only; no network, no LLM."""
import json
import time

import pytest

import background_jobs
import bulk_import
import db
import metadata_lookup
import navigator
import title_library
import translate_engines
from services import discover_lookup_service as svc
from services import metadata_service, safe_fetch, settings_service
from services.service_errors import (DependencyUnavailableError, InvalidInputError,
                                     RateLimitedError)

KEY = "sk-ant-api03-SECRETKEYSECRETKEYSECRETKEY1234567890"
PAGE = safe_fetch.FetchResult("Some page text " * 30, False)


class FakeEngine:
    supports_reference = True

    def __init__(self, key=None):
        self.key = key

    def translate_batch(self, texts, ctx):
        return [f"译{t}" for t in texts]


@pytest.fixture
def paid(monkeypatch):
    """claude with a server-side key; get_engine returns a fake holding it."""
    monkeypatch.setattr(settings_service, "resolve_key", lambda name: KEY)
    made = []

    def fake_get_engine(name, key=None, model=None, **kw):
        made.append((name, key))
        return FakeEngine(key)
    monkeypatch.setattr(translate_engines, "get_engine", fake_get_engine)
    return made


def _wait(job_id):
    for _ in range(200):
        s = background_jobs.get_status(job_id)
        if s and s["status"] not in ("running", "queued"):
            return s
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture(autouse=True)
def _clear_jobs():
    for j in (svc.BULK_JOB_ID, svc.NAV_JOB_ID):
        background_jobs.clear_job(j)
    yield
    for j in (svc.BULK_JOB_ID, svc.NAV_JOB_ID):
        _wait(j) if background_jobs.get_status(j) else None
        background_jobs.clear_job(j)


def test_translate_query_resolves_key_server_side_and_never_returns_it(paid):
    out = svc.translate_query("wandering princess", "claude")
    assert out["translated"] == "译wandering princess"
    assert paid == [("claude", KEY)]
    assert KEY not in json.dumps(out)


def test_chinese_query_skips_engine(paid):
    assert svc.translate_query("流浪的公主", "claude")["translated"] == "流浪的公主"
    assert paid == []


@pytest.mark.parametrize("engine", ["nllb", "nllb", "nope", 5])
def test_unknown_or_non_reference_engine_is_422(paid, engine):
    with pytest.raises(InvalidInputError):
        svc.translate_query("q", engine)
    with pytest.raises(InvalidInputError):
        svc.import_suggestion("https://example.com/x", engine)


def test_no_key_is_503(monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_key", lambda name: None)
    with pytest.raises(DependencyUnavailableError):
        svc.translate_query("hello", "claude")


def test_paid_engine_functions():
    assert set(svc.PAID_ENGINE_FUNCTIONS) >= {"translate_query", "bulk_extract"}


def test_private_url_is_422_without_connecting(monkeypatch, paid):
    pytest.importorskip("bs4")
    monkeypatch.setattr(metadata_service.socket, "getaddrinfo",
                        lambda h, p, **k: [(2, 1, 6, "", ("169.254.169.254", p))])

    def boom(*a, **k):
        raise AssertionError("connected")
    monkeypatch.setattr(metadata_service, "_pinned_get", boom)
    with pytest.raises(InvalidInputError):
        svc.import_suggestion("http://metadata.internal/latest", "claude")


def test_bad_scheme_is_422(paid):
    with pytest.raises(InvalidInputError):
        svc.import_suggestion("file:///etc/passwd", "claude")


def test_import_suggestion_needs_manual_passthrough(monkeypatch, paid):
    monkeypatch.setattr(safe_fetch, "fetch_public_text",
                        lambda url: safe_fetch.FetchResult("", True, safe_fetch.NEEDS_MANUAL_MESSAGE))
    monkeypatch.setattr(metadata_lookup, "extract_metadata_llm",
                        lambda *a: pytest.fail("LLM called on an empty page"))
    out = svc.import_suggestion("https://example.com/book/1", "claude")
    assert out["needs_manual"] is True and out["suggestion"] == {}


def test_import_suggestion_whitelists_and_writes_nothing(monkeypatch, paid, isolated_db):
    monkeypatch.setattr(safe_fetch, "fetch_public_text", lambda url: PAGE)
    monkeypatch.setattr(metadata_lookup, "extract_metadata_llm",
                        lambda text, eng: {"title_zh": "书", "evil": "x", "summary": f"k={KEY}"})
    out = svc.import_suggestion("https://example.com/book/1", "claude")
    assert set(out["suggestion"]) == {"title_zh", "summary", "source_url"}
    assert KEY not in json.dumps(out)
    assert db.list_known_titles() == []


def test_baihehub_search_shapes_results(monkeypatch):
    monkeypatch.setattr(title_library, "search_baihehub",
                        lambda q: [{"title": "T", "url": "https://baihehub.com/books/1",
                                    "snippet": "s"}])
    out = svc.baihehub_search("百合")
    assert out["results"][0]["title"] == "T" and out["fallback_url"].startswith("https://baihehub.com/")


def test_bulk_extract_per_url_status_and_partial_failure(monkeypatch, paid):
    def fetch(url):
        if "bad" in url:
            raise DependencyUnavailableError(safe_fetch.FETCH_FAILED)
        if "js" in url:
            return safe_fetch.FetchResult("", True, safe_fetch.NEEDS_MANUAL_MESSAGE)
        return PAGE
    monkeypatch.setattr(safe_fetch, "fetch_public_text", fetch)
    monkeypatch.setattr(bulk_import, "extract_listing_entries_llm",
                        lambda text, eng, source_name="": [
                            {"title": "A", "author": "x", "has_audio_drama": True},
                            {"title": "A"}, {"title": ""}, "junk"])
    urls = ["https://a.example/1", "https://bad.example/2", "https://js.example/3"]
    started = svc.bulk_extract(urls, "jjwxc", "claude")
    assert started["job_id"] == svc.BULK_JOB_ID
    _wait(svc.BULK_JOB_ID)
    res = svc.bulk_extract_result()["result"]
    pages = {p["url"]: p for p in res["pages"]}
    assert pages[urls[0]]["ok"] and pages[urls[0]]["count"] == 1
    assert not pages[urls[1]]["ok"] and pages[urls[1]]["message"] == safe_fetch.FETCH_FAILED
    assert pages[urls[2]]["needs_manual"]
    assert [e["title"] for e in res["entries"]] == ["A"]
    assert KEY not in json.dumps(svc.bulk_extract_result())


def test_results_are_idle_before_any_run():
    idle = {"job_id": "", "status": "idle", "progress": 0.0, "message": "", "result": None}
    background_jobs.clear_job(svc.BULK_JOB_ID)
    background_jobs.clear_job(svc.NAV_JOB_ID)
    assert svc.bulk_extract_result() == idle
    assert svc.navigation_help_result() == idle


def test_bulk_extract_limits(paid):
    with pytest.raises(InvalidInputError):
        svc.bulk_extract([f"https://a.example/{i}" for i in range(11)], "", "claude")
    with pytest.raises(InvalidInputError):
        svc.bulk_extract(["ftp://a.example/"], "", "claude")


def test_second_bulk_job_is_rate_limited(monkeypatch, paid):
    monkeypatch.setattr(background_jobs, "is_running", lambda j: True)
    with pytest.raises(RateLimitedError):
        svc.bulk_extract(["https://a.example/1"], "", "claude")


def test_bulk_commit_dedups_title_and_url(isolated_db):
    db.create_known_title(title_original="Old", title_en="", author="", tags="",
                          summary_en="", summary_original="", source_name="manual",
                          source_url="https://site.example/book/9", language="zh",
                          media_type="novel")
    listing = "https://site.example/list?page=1"
    out = svc.bulk_commit([
        {"title": "Old", "source_url": listing},
        {"title": "New1", "source_url": listing, "has_audio_drama": True},
        {"title": "New2", "source_url": listing},
        {"title": "New1", "source_url": listing},
        {"title": "Other", "source_url": "https://site.example/book/9"},
    ], "jjwxc")
    assert out["added"] == 2 and out["skipped"] == 3
    rows = {r["title_original"]: r for r in db.list_known_titles()}
    assert rows["New1"]["media_type"] == "audio_drama" and rows["New1"]["source_name"] == "jjwxc"


def test_bulk_commit_length_caps(isolated_db):
    with pytest.raises(InvalidInputError):
        svc.bulk_commit([{"title": "x" * 301}])
    with pytest.raises(InvalidInputError):
        svc.bulk_commit([{"title": "ok", "source_url": "javascript:alert(1)"}])
    assert db.list_known_titles() == []


def test_navigation_help_job(monkeypatch, paid):
    monkeypatch.setattr(safe_fetch, "fetch_public_text",
                        lambda url: safe_fetch.FetchResult("首页\n搜索\n" + "x" * 300, False))
    monkeypatch.setattr(navigator, "translate_labels",
                        lambda labels, lang, eng: {l: "T" for l in labels if len(l) < 5})
    monkeypatch.setattr(navigator, "generate_navigation_steps",
                        lambda *a, **k: "1. Click Search")
    svc.navigation_help("https://site.example/", "find audio dramas", "English", "claude")
    _wait(svc.NAV_JOB_ID)
    res = svc.navigation_help_result()["result"]
    assert res["labels"] == {"首页": "T", "搜索": "T"} and res["steps"] == "1. Click Search"


def test_paginate_rejects_format_injection_and_caps_range():
    urls = bulk_import.paginate_urls("https://s.example/{0.__class__}?p={page}&{x}", 1, 3)
    assert urls == [f"https://s.example/{{0.__class__}}?p={i}&{{x}}" for i in (1, 2, 3)]
    assert len(bulk_import.paginate_urls("https://s.example/{page}", 1, 10**6)) == \
        bulk_import.MAX_PAGINATE_PAGES
