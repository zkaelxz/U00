"""Tests for /api/sources read endpoints (Migration Slice 56, S-1): mocked, no
network. `isolated_db` also redirects sources.db (its path comes from
db.LIBRARY_DIR); note store.connect() creates that file on the first call, so
even these GETs write it (into the temp library)."""

import json
import os
from types import SimpleNamespace

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from api.api_config import ApiSettings
from api.server import create_app
from services import sources_registry_service as svc
from sources import adapters, health, profiles, registry, store

PROXY = "http://user:hunter2pass@10.9.8.7:8899"
SECRET = "sk-abcdefghijklmnop1234"


@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


@pytest.fixture
def seeded(isolated_db):
    """A library whose sources store holds a proxy, a secret, a path and a
    query string in every place the API reads from. Returns the library dir."""
    import db
    lib = db.LIBRARY_DIR
    store.set_setting("http_proxy_url", PROXY)
    store.log_attempt("manhuagui", "https://www.manhuagui.com/comic/1/2.html?token=SIGNED&x=1#frag", {
        "tier": "STATIC_HTTP", "technical_status": "OK", "capability_status": "VERIFIED",
        "reasons": ["HTTP_ERROR"],
        "lines": [f"Static HTTP: FAILED at {lib}/profiles/x -- {SECRET} C:\\Users\\me\\a.txt "
                  "see https://h.example/p?key=abc123"],
        "handoff": {"tier": "STATIC_HTTP", "reason": "BOT_CHALLENGE",
                    "url": "https://h.example/chal?sig=zzz"}})
    health.record_failure("manhuagui", "ConnectError", f"boom {lib}/x/y {SECRET}")
    store.track_series("manhuagui", "1", "T", url="https://www.manhuagui.com/comic/1/?a=b")
    return lib


def test_every_adapter_serializes(client):
    r = client.get("/api/sources")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == len(adapters.BUILTIN)
    by = {x["name"]: x for x in rows}
    assert by["missevan"]["import_supported"] is False
    assert by["bilibili"]["import_supported"] is False
    assert by["manhuagui"]["import_supported"] is True
    assert by["manhuagui"]["supports_adult_toggle"] is True
    assert by["bilibili"]["auth_supported"] is True
    for x in rows:
        assert isinstance(x["has_saved_signin"], bool)
        assert x["health"] in ("green", "yellow", "red")
        assert client.get(f"/api/sources/{x['name']}").status_code == 200
        assert client.get(f"/api/sources/{x['name']}/attempts").status_code == 200


def test_demo_source_hidden_until_enabled(client):
    demo = [n for n, c in registry.adapter_classes().items() if getattr(c, "is_demo", False)]
    assert demo
    for n in demo:
        assert client.get(f"/api/sources/{n}").status_code == 404
        store.set_setting("demo_source_enabled", True)
        assert client.get(f"/api/sources/{n}").status_code == 200


def test_unknown_source_404(client):
    for path in ("/api/sources/nope", "/api/sources/nope/attempts"):
        r = client.get(path)
        assert r.status_code == 404
        assert r.json()["error"]["code"] == "not_found"


def test_detail_has_separate_fields_and_terms_info_only(client):
    d = client.get("/api/sources/manhuagui").json()
    for k in ("authentication_required", "purchase_required", "technical_protection",
              "automation_permission", "ai_ml_use", "tiers", "terms", "health_detail"):
        assert k in d
    assert d["terms_enforced"] is False
    assert "permitted" not in json.dumps(d).lower().replace("unknown", "")


def test_proxy_is_bool_only_and_nothing_leaks(client, seeded):
    s = client.get("/api/sources/settings").json()
    assert s["proxy_configured"] is True
    assert "http_proxy_url" not in s and "page_server_enabled" not in s
    blob = ""
    for path in ("/api/sources", "/api/sources/settings", "/api/sources/profiles",
                 "/api/sources/tracked", "/api/sources/notifications"):
        blob += client.get(path).text
    for name in [x["name"] for x in client.get("/api/sources").json()]:
        blob += client.get(f"/api/sources/{name}").text
        blob += client.get(f"/api/sources/{name}/attempts").text
    for needle in (PROXY, "hunter2pass", "10.9.8.7", SECRET, seeded, "C:\\Users", "abc123",
                   "SIGNED", "sig=zzz", "token="):
        assert needle not in blob, needle


def test_proxy_false_when_unset(client):
    assert client.get("/api/sources/settings").json()["proxy_configured"] is False


def test_attempt_urls_have_no_query_and_text_is_scrubbed(client, seeded):
    a = client.get("/api/sources/manhuagui/attempts").json()
    assert len(a) == 1
    assert a[0]["url"] == "https://www.manhuagui.com/comic/1/2.html"
    assert a[0]["handoff"]["url"] == "https://h.example/chal"
    assert "?" not in json.dumps(a)
    assert "[REDACTED]" in a[0]["lines"][0] and "[path]" in a[0]["lines"][0]


def test_health_error_scrubbed(client, seeded):
    d = client.get("/api/sources/manhuagui").json()
    err = d["health_detail"]["last_error"]
    assert SECRET not in err and seeded not in err


def test_health_detail_has_a_plain_category(client, seeded):
    h = client.get("/api/sources/manhuagui").json()["health_detail"]
    assert h["last_error_type"] == "ConnectError" and h["last_error_category"] == "other"
    health.record_failure("manhuagui", "LAYOUT_CHANGED", "x")
    h = client.get("/api/sources/manhuagui").json()["health_detail"]
    assert h["last_error_category"] == "layout_changed"


def test_tracked_url_has_no_query(client, seeded):
    t = client.get("/api/sources/tracked").json()
    assert t[0]["url"] == "https://www.manhuagui.com/comic/1/"


def test_notifications_and_profiles(client, isolated_db):
    store.record_new_chapters("manhuagui", "1", [SimpleNamespace(chapter_id="c1", title="Ch 1")])
    n = client.get("/api/sources/notifications").json()
    assert n[0]["chapter_id"] == "c1" and n[0]["dismissed"] is False
    os.makedirs(profiles.profiles_dir(), exist_ok=True)
    rec = {"domain": "a.example", "format": 1, "active": {"novel": 1}, "versions": [
        {"version": 1, "kind": "novel", "status": "active", "origin": "auto",
         "rules": {"content": "SECRET-SELECTOR"}, "created_at": 1.0}]}
    with open(os.path.join(profiles.profiles_dir(), "a.example.json"), "w") as f:
        json.dump(rec, f)
    r = client.get("/api/sources/profiles")
    assert r.json()[0]["domain"] == "a.example" and r.json()[0]["versions"][0]["version"] == 1
    assert "SECRET-SELECTOR" not in r.text


def test_scrub_and_safe_url_units():
    assert svc.safe_url("https://u:p@h.example:8080/a/b?q=1#f") == "https://h.example:8080/a/b"
    assert svc.safe_url("not a url") == ""
    out = svc._scrub("open /home/kae/lib/x.db and C:\\a\\b, \\\\srv\\share\\f, ~/x/y")
    assert "/home/kae" not in out and "C:\\a" not in out and "srv" not in out and "~/x" not in out
    assert svc._scrub("https://example.com/a/b is down") == "https://example.com/a/b is down"


def test_removed_source_stored_data_still_lists_and_is_skipped(client, isolated_db):
    from sources import chapter_check
    assert "mangaz" not in registry.adapter_classes()
    store.track_series("mangaz", "42", "Old Import", url="https://example.invalid/series/detail/42")
    t = client.get("/api/sources/tracked").json()
    assert t[0]["source"] == "mangaz" and t[0]["last_check_error"] == registry.SOURCE_REMOVED
    assert client.get("/api/sources/mangaz").json()["error"]["message"] == registry.SOURCE_REMOVED

    def never(name):
        raise AssertionError("no adapter may be built for a removed source")
    summary = chapter_check.run_check_cycle(adapter_factory=never)
    assert summary["errors"] == {"Old Import": registry.SOURCE_REMOVED}
    assert summary["checked"] == 0
    with pytest.raises(KeyError, match="removed"):
        registry.get_adapter("mangaz")

    r = client.post("/api/sources/tracked", json={"source": "mangaz", "series_id": "42",
                                                  "tracked": False})
    assert r.status_code == 200 and r.json() == []
