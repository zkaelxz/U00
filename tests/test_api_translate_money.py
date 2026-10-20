"""POST /api/translate keeps no length cap but ends before the monthly cap, and
POST /api/translate/estimate prices a run without sending it."""
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")


import db
import translate_engines
from services import translate_run_service, translate_service
from tests.test_standalone_money import PARAGRAPH, TEXT, _Billed, _request_cost


def test_the_stop_message_reaches_the_response(client, monkeypatch):
    engine = _Billed(tokens=translate_engines.estimate_translation_tokens([PARAGRAPH] * 8))
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda name, env_path=None: "k")
    monkeypatch.setattr(translate_engines, "get_engine", lambda *a, **kw: engine)
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: _request_cost() * 1.2)
    resp = client.post("/api/translate", json={"text": TEXT, "engine": "claude",
                                               "source_language": "zh", "target_language": "en"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["translated_text"].count("EN:") == 8
    assert body["message"].startswith("Stopped at chunk 9 of 10")


def test_a_very_long_text_is_still_accepted(client, monkeypatch):
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 0.0)
    text = "\n\n".join("你好" * 700 for _ in range(1400))  # about 1.96 million characters
    assert len(text) > 1_900_000
    resp = client.post("/api/translate/estimate", json={"text": text, "engine": "claude",
                                                        "source_language": "zh",
                                                        "target_language": "en"})
    assert resp.status_code == 200 and resp.json()["chunks"] > 1000


def test_estimate_route(client, monkeypatch):
    monkeypatch.setattr(translate_run_service, "month_cap_usd", lambda: 5.0)
    db.log_usage(None, "claude", "claude-sonnet-5-5", "x", 1, 1, 1.0)
    resp = client.post("/api/translate/estimate", json={"text": TEXT, "engine": "claude",
                                                        "source_language": "zh",
                                                        "target_language": "en"})
    body = resp.json()
    assert resp.status_code == 200
    assert body["cap_left_usd"] == pytest.approx(4.0) and body["fits_cap"] is True


def test_estimate_refuses_an_unlisted_model(client):
    resp = client.post("/api/translate/estimate", json={"text": "你好", "engine": "claude",
                                                        "source_language": "zh",
                                                        "target_language": "en",
                                                        "model": "claude-made-up-9"})
    assert resp.status_code == 422
