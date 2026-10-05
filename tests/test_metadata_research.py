"""
Tests for grounded metadata research (roadmap Step 37,
services/metadata_research_service.py and its routes). Fully mocked: no
network, no real key.
"""
import os

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import db
import translate_engines
from api.api_config import ApiSettings
from api.server import create_app
from services import metadata_research_service as mrs
from services import settings_service
from services.service_errors import ConflictError, InvalidInputError, NotFoundError

KEY = "AIzaSyTESTKEY0000000000000000000000000"


def _response(fields, supports=None, chunks=None, related=None, text=None):
    import json
    body = text if text is not None else json.dumps({"fields": fields, "related": related or []})
    return {
        "candidates": [{
            "content": {"parts": [{"text": body}]},
            "groundingMetadata": {
                "groundingChunks": chunks if chunks is not None else [
                    {"web": {"uri": "https://example.org/a", "title": "example.org"}},
                    {"web": {"uri": "https://wiki.example/b", "title": "wiki.example"}},
                    {"web": {"uri": "javascript:alert(1)", "title": "bad"}},
                ],
                "groundingSupports": supports or [],
            },
        }],
        "usageMetadata": {"promptTokenCount": 1000, "candidatesTokenCount": 500},
    }


class FakeGemini:
    def __init__(self, data):
        self.data = data
        self.calls = []

    def __call__(self, api_key, model, prompt):
        self.calls.append((api_key, model, prompt))
        return self.data


@pytest.fixture
def setup(isolated_db, monkeypatch):
    drama = db.create_drama(title_en="Old Title", title_zh="白河", source_language="zh")
    db.update_drama(drama, author="Someone")
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda name, *a, **kw: KEY if name == "gemini" else None)
    monkeypatch.setattr(settings_service, "get_gemini_free_tier", lambda: False)
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda *a, **kw: 0.0)
    data = _response(
        {"title_en": {"value": "New Title", "confidence": "low"},
         "author": {"value": "Someone", "confidence": "high"},
         "director": "Jane Doe",
         "not_a_field": {"value": "x"}},
        supports=[
            {"segment": {"text": '"title_en": {"value": "New Title"'},
             "groundingChunkIndices": [0, 2], "confidenceScores": [0.82]},
            {"segment": {"text": '"director": "Jane Doe"'},
             "groundingChunkIndices": [1, 9]},
        ],
        related=[{"title": "Baihe (manga)", "relation": "manga adaptation"}])
    fake = FakeGemini(data)
    monkeypatch.setattr(mrs, "_call_gemini", fake)
    return drama, fake


def test_result_fields_carry_their_own_sources(setup):
    drama, fake = setup
    r = mrs.research(drama)
    by = {f["field"]: f for f in r["fields"]}
    assert set(by) == {"title_en", "author", "director"}  # keyed by name, extras dropped
    assert by["title_en"]["sources"] == [{"title": "example.org", "url": "https://example.org/a"}]
    assert by["title_en"]["confidence"] == 0.82  # grounding score wins over self-report
    assert by["director"]["sources"] == [{"title": "wiki.example", "url": "https://wiki.example/b"}]
    assert by["author"]["sources"] == [] and by["author"]["confidence"] == 0.9
    assert by["title_en"]["status"] == "conflict" and by["title_en"]["current"] == "Old Title"
    assert by["author"]["status"] == "same" and by["director"]["status"] == "new"
    assert all(s["url"].startswith("https://") for s in r["sources"])  # javascript: dropped
    assert r["related"] == [{"title": "Baihe (manga)", "relation": "manga adaptation"}]
    assert fake.calls[0][0] == KEY and fake.calls[0][1] == mrs.DEFAULT_MODEL
    assert KEY not in str(r)


def test_repeat_lookup_is_cached_and_does_not_respend(setup):
    drama, fake = setup
    first = mrs.research(drama)
    assert first["cached"] is False and first["budget"]["used_today"] == 1
    second = mrs.research(drama)
    assert len(fake.calls) == 1
    assert second["cached"] is True and second["cost_usd"] == 0.0
    assert second["research_id"] != first["research_id"]  # one id per lookup shown
    assert second["budget"]["used_today"] == 1
    # a different mode or model is a different lookup
    mrs.research(drama, mode="deep")
    assert len(fake.calls) == 2


def test_budget_counter_increments_and_resets_daily(setup, monkeypatch):
    drama, _ = setup
    assert mrs.budget_status()["free_remaining"] == mrs.GROUNDED_FREE_RPD
    mrs.research(drama)
    mrs.research(drama, mode="verify")
    b = mrs.budget_status()
    assert b["used_today"] == 2 and b["free_remaining"] == mrs.GROUNDED_FREE_RPD - 2
    monkeypatch.setattr(mrs, "_today", lambda: "2099-01-01")
    assert mrs.budget_status()["used_today"] == 0


def test_free_budget_used_up_refuses_unless_paid_allowed(setup):
    drama, fake = setup
    db.set_app_setting(mrs.BUDGET_SETTING, {"date": mrs._today(), "count": mrs.GROUNDED_FREE_RPD})
    with pytest.raises(ConflictError) as e:
        mrs.research(drama)
    assert e.value.details["reason"] == "free_budget_used"
    assert fake.calls == []
    r = mrs.research(drama, allow_paid=True)
    assert r["cost_usd"] >= mrs.GROUNDED_PAID_PRICE_USD


def test_monthly_free_limit_also_applies(setup):
    drama, fake = setup
    db.set_app_setting(mrs.BUDGET_SETTING, {"date": "2000-01-01", "count": 0,
                                            "month": mrs._today()[:7],
                                            "month_count": mrs.GROUNDED_FREE_MONTHLY})
    assert mrs.budget_status()["free_remaining"] == 0
    with pytest.raises(ConflictError):
        mrs.research(drama)
    assert fake.calls == []


def test_every_search_query_counts(setup, monkeypatch):
    drama, fake = setup
    fake.data["candidates"][0]["groundingMetadata"]["webSearchQueries"] = ["a", "b", "c"]
    r = mrs.research(drama)
    assert r["budget"]["used_today"] == 3 and r["budget"]["used_this_month"] == 3
    assert r["search_queries"] == ["a", "b", "c"]


def test_failed_call_still_counts(setup, monkeypatch):
    drama, _ = setup
    monkeypatch.setattr(mrs, "_call_gemini", lambda *a: (_ for _ in ()).throw(TimeoutError()))
    from services.service_errors import DependencyUnavailableError
    with pytest.raises(DependencyUnavailableError):
        mrs.research(drama)
    assert mrs.budget_status()["used_today"] == 1  # Google may have run it


def test_concurrent_lookups_cannot_share_the_last_free_search(setup, monkeypatch):
    drama, _ = setup
    start = mrs.GROUNDED_FREE_RPD - mrs.MAX_QUERIES_PER_LOOKUP  # enough for exactly one
    db.set_app_setting(mrs.BUDGET_SETTING, {"date": mrs._today(), "count": start,
                                            "month": mrs._today()[:7], "month_count": 0})
    import threading
    results, barrier = [], threading.Barrier(2)

    def slow_call(*a):
        return _response({"studio": "S"})
    monkeypatch.setattr(mrs, "_call_gemini", slow_call)
    orig = mrs._usage

    def racing_usage():
        out = orig()
        try:
            barrier.wait(timeout=0.5)
        except threading.BrokenBarrierError:
            pass
        return out
    monkeypatch.setattr(mrs, "_usage", racing_usage)

    def run(mode):
        try:
            mrs.research(drama, mode=mode)
            results.append("ok")
        except ConflictError:
            results.append("refused")
    threads = [threading.Thread(target=run, args=(m,)) for m in ("quick", "deep")]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(results) == ["ok", "refused"]
    assert mrs.budget_status()["used_today"] == start + 1  # the refused one counted nothing


def test_apply_uses_the_lookup_that_was_shown(setup, monkeypatch):
    drama, fake = setup
    first = mrs.research(drama)
    # another tab / user runs "Look up again" and gets different values
    fake.data["candidates"][0]["content"]["parts"][0]["text"] = (
        '{"fields": {"director": {"value": "Someone Else"}}}')
    second = mrs.research(drama, refresh=True)
    assert [f["value"] for f in second["fields"]] == ["Someone Else"]
    mrs.apply_research(drama, first["research_id"], {"director": "replace"}, seen={"director": None})
    assert db.get_drama(drama)["director"] == "Jane Doe"  # what the first user saw


def test_research_id_is_bound_to_its_drama(setup):
    drama, _ = setup
    r = mrs.research(drama)
    other = db.create_drama(title_en="Other", source_language="zh")
    with pytest.raises(NotFoundError):
        mrs.apply_research(other, r["research_id"], {"director": "replace"}, seen={"director": None})
    assert db.get_drama(other)["director"] in (None, "")


def test_near_the_free_limit_a_lookup_is_paid(setup):
    drama, fake = setup
    db.set_app_setting(mrs.BUDGET_SETTING, {"date": mrs._today(),
                                            "count": mrs.GROUNDED_FREE_RPD - 2,
                                            "month": mrs._today()[:7], "month_count": 0})
    with pytest.raises(ConflictError) as e:  # 2 free left < worst-case queries
        mrs.research(drama)
    assert e.value.details["reason"] == "free_budget_used" and fake.calls == []
    assert mrs.budget_status()["used_today"] == mrs.GROUNDED_FREE_RPD - 2  # nothing counted


def test_cap_precheck_covers_worst_case_queries(setup, monkeypatch):
    drama, fake = setup
    db.set_app_setting(mrs.BUDGET_SETTING, {"date": mrs._today(), "count": mrs.GROUNDED_FREE_RPD,
                                            "month": mrs._today()[:7], "month_count": 0})
    # room for one paid query's fee, but not for the worst case
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd",
                        lambda *a, **kw: mrs.GROUNDED_PAID_PRICE_USD * 2)
    with pytest.raises(ConflictError) as e:
        mrs.research(drama, allow_paid=True)
    assert e.value.details["reason"] == "monthly_cap" and fake.calls == []


def test_extra_queries_within_the_free_allowance_cost_no_fee(setup):
    drama, fake = setup
    fake.data["candidates"][0]["groundingMetadata"]["webSearchQueries"] = ["a", "b", "c"]
    r = mrs.research(drama)
    tokens_only = translate_engines.estimate_cost(mrs.DEFAULT_MODEL, 1000, 500)
    assert r["cost_usd"] == pytest.approx(tokens_only, abs=1e-6)


def test_refresh_spends_a_new_search(setup):
    drama, fake = setup
    mrs.research(drama)
    r = mrs.research(drama, refresh=True)
    assert len(fake.calls) == 2 and r["cached"] is False


def test_paid_search_refused_on_free_tier_key(setup, monkeypatch):
    drama, fake = setup
    monkeypatch.setattr(settings_service, "get_gemini_free_tier", lambda: True)
    db.set_app_setting(mrs.BUDGET_SETTING, {"date": mrs._today(), "count": mrs.GROUNDED_FREE_RPD})
    with pytest.raises(ConflictError):
        mrs.research(drama, allow_paid=True)
    assert fake.calls == []


def test_monthly_cap_respected(setup, monkeypatch):
    drama, fake = setup
    monkeypatch.setattr(settings_service, "get_monthly_cap_usd", lambda *a, **kw: 1.0)
    db.log_usage(None, "claude", "x", "translate", 0, 0, 0.9999)
    with pytest.raises(ConflictError) as e:
        mrs.research(drama)
    assert e.value.details["reason"] == "monthly_cap"
    assert fake.calls == []


def test_spend_is_logged(setup):
    drama, _ = setup
    before = db.get_month_spend()
    r = mrs.research(drama)
    assert r["cost_usd"] > 0
    assert db.get_month_spend() == pytest.approx(before + r["cost_usd"])


def test_conflicting_field_is_never_overwritten_without_a_choice(setup):
    drama, _ = setup
    r = mrs.research(drama)
    # applying only "director" leaves the conflicting title untouched
    out = mrs.apply_research(drama, r["research_id"], {"director": "replace"},
                             seen={"director": None})
    assert out["replaced"] == ["director"]
    d = db.get_drama(drama)
    assert d["title_en"] == "Old Title" and d["director"] == "Jane Doe"
    # keep writes nothing
    mrs.apply_research(drama, r["research_id"], {"title_en": "keep"})  # needs no `seen`
    assert db.get_drama(drama)["title_en"] == "Old Title"
    assert [p["field"] for p in db.list_field_provenance(drama)] == ["director"]
    # save both keeps the existing value and records the researched one beside it
    out = mrs.apply_research(drama, r["research_id"], {"title_en": "save_both"},
                             seen={"title_en": "Old Title"})
    assert out["saved_alternates"] == ["title_en"]
    assert db.get_drama(drama)["title_en"] == "Old Title"
    rows = {p["field"]: p for p in db.list_field_provenance(drama)}
    assert rows["title_en"]["status"] == "alternate" and rows["title_en"]["value"] == "New Title"


def test_apply_refuses_when_drama_changed_since_lookup(setup):
    drama, _ = setup
    r = mrs.research(drama)
    db.update_drama(drama, director="Edited Meanwhile")  # e.g. saved in Details
    with pytest.raises(ConflictError) as e:
        mrs.apply_research(drama, r["research_id"], {"director": "replace"},
                           seen={"director": None})
    assert e.value.details["reason"] == "changed"
    assert db.get_drama(drama)["director"] == "Edited Meanwhile"
    with pytest.raises(InvalidInputError):  # a write needs the value that was shown
        mrs.apply_research(drama, r["research_id"], {"director": "replace"})


def test_confirm_records_a_matching_value(setup):
    drama, _ = setup
    r = mrs.research(drama, mode="verify")
    out = mrs.apply_research(drama, r["research_id"], {"author": "confirm"},
                             seen={"author": "Someone"})
    assert out["confirmed"] == ["author"]
    row = db.list_field_provenance(drama)[0]
    assert row["status"] == "verified" and row["value"] == "Someone"
    with pytest.raises(InvalidInputError):  # confirm is only for a matching value
        mrs.apply_research(drama, r["research_id"], {"title_en": "confirm"},
                           seen={"title_en": "Old Title"})


def test_segment_spanning_fields_does_not_leak_sources(setup, monkeypatch):
    drama, _ = setup
    monkeypatch.setattr(mrs, "_call_gemini", FakeGemini(_response(
        {"author": "Someone", "summary": "A story written by Someone about rivers."},
        supports=[{"segment": {"text": '"summary": "A story written by Someone about rivers."'},
                   "groundingChunkIndices": [1]},
                  {"segment": {"text": '"author": "Someone"'}, "groundingChunkIndices": [0]}])))
    by = {f["field"]: f for f in mrs.research(drama)["fields"]}
    assert [s["url"] for s in by["author"]["sources"]] == ["https://example.org/a"]
    assert [s["url"] for s in by["summary"]["sources"]] == ["https://wiki.example/b"]


def test_provenance_is_stored_per_field(setup):
    drama, _ = setup
    r = mrs.research(drama)
    mrs.apply_research(drama, r["research_id"], {"title_en": "replace", "director": "replace"},
                       seen={"title_en": "Old Title", "director": None})
    rows = {p["field"]: p for p in mrs.list_provenance(drama)["fields"]}
    assert rows["title_en"]["source_url"] == "https://example.org/a"
    assert rows["director"]["source_url"] == "https://wiki.example/b"
    for p in rows.values():
        assert p["retrieved_at"] and p["status"] == "applied"
        assert p["last_verified"] == r["retrieved_at"]  # when the sources were checked
    assert rows["title_en"]["confidence"] == 0.82
    assert db.get_drama(drama)["title_en"] == "New Title"


def test_apply_validation(setup):
    drama, _ = setup
    r = mrs.research(drama)
    with pytest.raises(InvalidInputError):
        mrs.apply_research(drama, r["research_id"], {})
    with pytest.raises(InvalidInputError):
        mrs.apply_research(drama, r["research_id"], {"summary": "replace"})  # not in result
    with pytest.raises(InvalidInputError):
        mrs.apply_research(drama, r["research_id"], {"title_en": "overwrite"})
    with pytest.raises(InvalidInputError):
        mrs.apply_research(drama, "../x", {"title_en": "keep"})
    with pytest.raises(NotFoundError):
        mrs.apply_research(drama, "0" * 64, {"title_en": "keep"})


def test_research_input_errors(setup, monkeypatch):
    drama, fake = setup
    with pytest.raises(InvalidInputError):
        mrs.research(drama, mode="crawl")
    with pytest.raises(InvalidInputError):
        mrs.research(drama, model="gemini-pro-latest")
    with pytest.raises(NotFoundError):
        mrs.research(999999)
    empty = db.create_drama(title_en="", source_language="zh")
    with pytest.raises(InvalidInputError):
        mrs.research(empty)
    assert fake.calls == []


def test_api_failure_is_fixed_text(setup, monkeypatch):
    drama, _ = setup

    def boom(*a):
        raise RuntimeError(f"401 for key={KEY}")
    monkeypatch.setattr(mrs, "_call_gemini", boom)
    from services.service_errors import DependencyUnavailableError
    with pytest.raises(DependencyUnavailableError) as e:
        mrs.research(drama)
    assert KEY not in str(e.value)


def test_unreadable_answer_is_not_cached(setup, monkeypatch):
    drama, _ = setup
    fake = FakeGemini(_response({}, text="sorry, no JSON here"))
    monkeypatch.setattr(mrs, "_call_gemini", fake)
    r = mrs.research(drama)
    assert r["fields"] == []
    mrs.research(drama)
    assert len(fake.calls) == 2  # an empty answer can be retried


def test_fenced_json_is_parsed(setup, monkeypatch):
    drama, _ = setup
    monkeypatch.setattr(mrs, "_call_gemini", FakeGemini(_response(
        {}, text='Here:\n```json\n{"fields": {"studio": "Studio X"}}\n```')))
    assert [f["value"] for f in mrs.research(drama)["fields"]] == ["Studio X"]


def test_no_key_is_503_text(setup, monkeypatch):
    drama, fake = setup
    monkeypatch.setattr(settings_service, "resolve_key", lambda *a, **kw: None)
    from services.service_errors import DependencyUnavailableError
    with pytest.raises(DependencyUnavailableError):
        mrs.research(drama)
    assert fake.calls == []


def test_call_sends_key_as_header_with_timeout(monkeypatch):
    seen = {}

    class Resp:
        def raise_for_status(self):
            pass

        headers = {}

        def iter_content(self, size):
            yield b"{}"

        def close(self):
            pass

    def post(url, **kw):
        seen.update(kw, url=url)
        return Resp()
    import requests
    monkeypatch.setattr(requests, "post", post)
    mrs._call_gemini(KEY, "gemini-flash-lite-latest", "p")
    assert KEY not in seen["url"] and seen["headers"] == {"x-goog-api-key": KEY}
    assert seen["timeout"] and seen["json"]["tools"] == [{"google_search": {}}]


def test_static_timeout():
    from tests.test_static_analysis import PROJECT_ROOT, _find_requests_calls_missing_timeout
    path = os.path.join(PROJECT_ROOT, "services", "metadata_research_service.py")
    assert _find_requests_calls_missing_timeout(path) == []


# --- routes ---------------------------------------------------------------

@pytest.fixture
def client(isolated_db):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def test_routes_round_trip(client, setup):
    drama, _ = setup
    b = client.get("/api/metadata/research/budget")
    assert b.status_code == 200 and b.json()["free_remaining"] == mrs.GROUNDED_FREE_RPD
    r = client.post(f"/api/metadata/dramas/{drama}/research", json={"mode": "quick"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert KEY not in r.text and body["budget"]["used_today"] == 1
    a = client.post(f"/api/metadata/dramas/{drama}/research/apply",
                    json={"research_id": body["research_id"], "choices": {"director": "replace"},
                          "seen": {"director": None}})
    assert a.status_code == 200, a.text
    assert a.json()["drama"]["director"] == "Jane Doe"
    p = client.get(f"/api/metadata/dramas/{drama}/provenance")
    assert p.status_code == 200 and p.json()["fields"][0]["field"] == "director"


def test_routes_errors(client, setup):
    drama, _ = setup
    assert client.post(f"/api/metadata/dramas/{drama}/research",
                       json={"mode": "crawl"}).status_code == 422
    assert client.post(f"/api/metadata/dramas/{drama}/research",
                       json={"api_key": "x"}).status_code == 422  # no keys from clients
    assert client.post("/api/metadata/dramas/99999/research", json={}).status_code == 404
    db.set_app_setting(mrs.BUDGET_SETTING, {"date": mrs._today(), "count": mrs.GROUNDED_FREE_RPD})
    assert client.post(f"/api/metadata/dramas/{drama}/research", json={}).status_code == 409
    assert client.post(f"/api/metadata/dramas/{drama}/research/apply",
                       json={"research_id": "0" * 64, "choices": {"title_en": "replace"},
                             "seen": {"title_en": "Old Title"}}).status_code == 404
    assert client.get("/api/metadata/dramas/99999/provenance").status_code == 404


def test_remote_research_needs_engines_paid(setup):
    from tests.test_api_permissions import _app, _h, _remote, _user, _user_named
    drama, fake = setup
    c = _remote(_app())
    _u, s = _user()
    url = f"/api/metadata/dramas/{drama}/research"
    assert c.post(url, json={}, headers=_h(s)).status_code == 403  # no media.import_url
    _u2, both = _user_named("both@example.com", "media.import_url", "engines.paid")
    _u3, imp = _user_named("imp@example.com", "media.import_url")
    assert c.post(url, json={}, headers=_h(imp)).status_code == 403  # Gemini is paid
    assert c.post(url, json={}, headers=_h(both)).status_code == 200
    assert c.get("/api/metadata/research/budget", headers=_h(s)).status_code == 200


def test_an_oversized_gemini_response_is_refused(monkeypatch):
    import requests

    class Endless:
        headers = {}
        closed = False

        def raise_for_status(self):
            pass

        def iter_content(self, size):
            while True:
                yield b"x" * size

        def close(self):
            Endless.closed = True
    monkeypatch.setattr(mrs, "MAX_RESPONSE_BYTES", 1000)
    monkeypatch.setattr(requests, "post", lambda *a, **k: Endless())
    with pytest.raises(ValueError, match="too large"):
        mrs._call_gemini(KEY, "gemini-flash-lite-latest", "p")
    assert Endless.closed
