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
    assert second["research_id"] == first["research_id"]
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
    out = mrs.apply_research(drama, r["research_id"], {"director": "replace"})
    assert out["replaced"] == ["director"]
    d = db.get_drama(drama)
    assert d["title_en"] == "Old Title" and d["director"] == "Jane Doe"
    # keep writes nothing
    mrs.apply_research(drama, r["research_id"], {"title_en": "keep"})
    assert db.get_drama(drama)["title_en"] == "Old Title"
    assert [p["field"] for p in db.list_field_provenance(drama)] == ["director"]
    # save both keeps the existing value and records the researched one beside it
    out = mrs.apply_research(drama, r["research_id"], {"title_en": "save_both"})
    assert out["saved_alternates"] == ["title_en"]
    assert db.get_drama(drama)["title_en"] == "Old Title"
    rows = {p["field"]: p for p in db.list_field_provenance(drama)}
    assert rows["title_en"]["status"] == "alternate" and rows["title_en"]["value"] == "New Title"


def test_provenance_is_stored_per_field(setup):
    drama, _ = setup
    r = mrs.research(drama)
    mrs.apply_research(drama, r["research_id"], {"title_en": "replace", "director": "replace"})
    rows = {p["field"]: p for p in mrs.list_provenance(drama)["fields"]}
    assert rows["title_en"]["source_url"] == "https://example.org/a"
    assert rows["director"]["source_url"] == "https://wiki.example/b"
    for p in rows.values():
        assert p["retrieved_at"] and p["last_verified"] and p["status"] == "applied"
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


def test_api_failure_is_fixed_text_and_counts_nothing(setup, monkeypatch):
    drama, _ = setup

    def boom(*a):
        raise RuntimeError(f"401 for key={KEY}")
    monkeypatch.setattr(mrs, "_call_gemini", boom)
    from services.service_errors import DependencyUnavailableError
    with pytest.raises(DependencyUnavailableError) as e:
        mrs.research(drama)
    assert KEY not in str(e.value) and mrs.budget_status()["used_today"] == 0


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

        def json(self):
            return {}

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
                    json={"research_id": body["research_id"], "choices": {"director": "replace"}})
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
                       json={"research_id": "0" * 64,
                             "choices": {"title_en": "replace"}}).status_code == 404
    assert client.get("/api/metadata/dramas/99999/provenance").status_code == 404
