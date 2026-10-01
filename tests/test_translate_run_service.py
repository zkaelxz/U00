"""Tests for services/translate_run_service.py (Migration Slice 39)."""
import json

import pytest

import db
import translate_engines
from core import Line
from services import settings_service, translate_run_service as svc
from services.service_errors import (InvalidInputError, NotFoundError,
                                      UnsupportedOperationError)


def _drama(**fields):
    return db.create_drama(title_zh="D", **fields)


def _seed(did, texts):
    db.save_lines(did, [Line(idx=i, start=i, end=i + 1, zh=z, en=en)
                        for i, (z, en) in enumerate(texts)])


@pytest.fixture(autouse=True)
def _no_ollama_probe(monkeypatch):
    """No test here touches the network: the Ollama health check is
    stubbed (unreachable) unless a test swaps in its own."""
    monkeypatch.setattr(translate_engines, "check_ollama_reachable", lambda url: False)


@pytest.fixture
def cap(monkeypatch):
    """Sets the resolved monthly cap and month spend."""
    def _set(cap_usd, spend=0.0):
        real = settings_service.resolve_key
        monkeypatch.setattr(settings_service, "resolve_key",
                            lambda k, *a, **kw: str(cap_usd) if k == "monthly_cap_usd" else real(k, *a, **kw))
        monkeypatch.setattr(db, "get_month_spend", lambda *a, **kw: spend)
    return _set


def test_unknown_drama(isolated_db):
    with pytest.raises(NotFoundError):
        svc.get_translate_config(999)
    with pytest.raises(NotFoundError):
        svc.estimate_translate_cost(999)


def test_config_defaults_audio_vs_novel(isolated_db):
    a = svc.get_translate_config(_drama())
    assert a["defaults"] == {"context_window": 6, "context_window_ahead": 3, "batch_size": 20}
    assert a["default_style_preset"] == "audio_drama"
    assert a["translation_engine"] == "claude"
    n = svc.get_translate_config(_drama(content_mode="novel_narration"))
    assert n["defaults"] == {"context_window": 10, "context_window_ahead": 6, "batch_size": 30}
    assert n["default_style_preset"] == "novel"
    assert {"key": "novel", "label": "Novel / prose"} in [{"key": p["key"], "label": p["label"]} for p in n["style_presets"]]
    assert "en-US" in n["locales"]
    assert {t["key"] for t in n["workflow_tiers"]} == set(translate_engines.WORKFLOW_TIERS)


def test_counts(isolated_db):
    did = _drama()
    _seed(did, [("你好", "hi"), ("再见", ""), ("谢谢", "")])
    cfg = svc.get_translate_config(did)
    assert cfg["line_count"] == 3 and cfg["untranslated_count"] == 2


def test_novel_reference_flag_never_text(isolated_db):
    import os
    did = _drama(novel_reference_filename="ref.txt")
    assert svc.get_translate_config(did)["has_novel_reference"] is False
    with open(os.path.join(db.drama_dir(did), "ref.txt"), "w", encoding="utf-8") as f:
        f.write("SECRET-NOVEL-TEXT")
    cfg = svc.get_translate_config(did)
    assert cfg["has_novel_reference"] is True
    assert "SECRET-NOVEL-TEXT" not in json.dumps(cfg)


def test_last_translate_errors_parsing(isolated_db):
    assert svc.get_translate_config(_drama())["last_translate_errors"] is None
    ok = _drama(last_translate_errors=json.dumps([1, 2]))
    assert svc.get_translate_config(ok)["last_translate_errors"] == [1, 2]
    bad = _drama(last_translate_errors="{not json")
    assert svc.get_translate_config(bad)["last_translate_errors"] is None


@pytest.mark.parametrize("cap_usd", [0, 12.5])
def test_month_spend_reported_with_or_without_a_cap(isolated_db, cap, cap_usd):
    cap(cap_usd, spend=4.25)
    cfg = svc.get_translate_config(_drama())
    assert cfg["monthly_cap_usd"] == float(cap_usd)
    assert cfg["month_spend"] == 4.25


def test_no_secret_leak_and_cap_from_resolved(isolated_db, monkeypatch):
    secrets = {"claude": "sk-ant-FAKE1", "deepseek": "sk-ds-FAKE2", "monthly_cap_usd": "12.5"}
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, *a, **kw: secrets.get(k))
    monkeypatch.setattr(db, "get_month_spend", lambda *a, **kw: 3.0)
    cfg = svc.get_translate_config(_drama())
    dumped = json.dumps(cfg)
    assert "FAKE1" not in dumped and "FAKE2" not in dumped
    assert cfg["monthly_cap_usd"] == 12.5 and cfg["month_spend"] == 3.0
    assert cfg["cap_applies_by_engine"]["claude"] is True
    assert cfg["cap_applies_by_engine"]["ollama"] is False
    assert "claude" in cfg["bulk_supported_engines"]


def test_estimate_relationships(isolated_db, cap):
    cap(0)
    did = _drama()
    _seed(did, [("你好世界" * 50, ""), ("再见" * 50, "bye")])
    plain = svc.estimate_translate_cost(did, "claude")
    assert plain["target_line_count"] == 1 and plain["estimated_usd"] > 0
    assert plain["cap_applies"] is True and plain["free"] is False
    refl = svc.estimate_translate_cost(did, "claude", reflect=True)
    assert refl["estimated_usd"] == pytest.approx(plain["estimated_usd"] * 3)
    bulk = svc.estimate_translate_cost(did, "claude", bulk=True)
    assert bulk["estimated_usd"] == pytest.approx(plain["estimated_usd"] * 0.5)
    forced = svc.estimate_translate_cost(did, "claude", force_retranslate=True)
    assert forced["target_line_count"] == 2
    assert forced["estimated_usd"] > plain["estimated_usd"]


def test_free_engines(isolated_db, cap):
    cap(0)
    did = _drama()
    _seed(did, [("你好" * 20, "")])
    for name in ("fake", "ollama", "nllb", "libretranslate"):
        r = svc.estimate_translate_cost(did, name)
        assert r["free"] is True and not r["estimated_usd"] and r["cap_applies"] is False
    g = svc.estimate_translate_cost(did, "gemini", gemini_free_tier=True)
    assert g["free"] is True and g["estimated_usd"] == 0.0 and g["cap_applies"] is False


def test_monthly_refusal_and_above_cap(isolated_db, cap):
    did = _drama()
    _seed(did, [("你好世界" * 500, "")])
    cap(5.0, spend=5.0)
    r = svc.estimate_translate_cost(did, "claude")
    assert r["monthly_refusal"] is True
    cap(1000.0, spend=0.0)
    r = svc.estimate_translate_cost(did, "claude", job_cost_cap_usd=0.0001)
    assert r["effective_cap_usd"] == 0.0001
    assert r["monthly_refusal"] is False and r["estimate_above_cap"] is True
    r = svc.estimate_translate_cost(did, "claude")
    assert r["effective_cap_usd"] == 1000.0 and r["estimate_above_cap"] is False


@pytest.mark.parametrize("engine", ["deepl", "google"])
def test_a_removed_engine_is_refused_with_a_clear_message(isolated_db, engine):
    did = _drama()
    _seed(did, [("你好", "")])
    with pytest.raises(InvalidInputError, match="was removed"):
        svc.estimate_translate_cost(did, engine)
    with pytest.raises(InvalidInputError, match="was removed"):
        svc.start_translate_run(did, engine_name=engine)


def test_validation(isolated_db):
    did = _drama()
    with pytest.raises(InvalidInputError):
        svc.estimate_translate_cost(did, "nope")
    with pytest.raises(UnsupportedOperationError):
        svc.estimate_translate_cost(did, "nllb", reflect=True)
    bad = next(iter(translate_engines.GEMINI_FREE_TIER_UNAVAILABLE_MODELS))
    with pytest.raises(UnsupportedOperationError):
        svc.estimate_translate_cost(did, "gemini", model=bad, gemini_free_tier=True)
    with pytest.raises(InvalidInputError):
        svc.estimate_translate_cost(did, "claude", job_cost_cap_usd=-1)


def test_h3_config_does_not_create_folder(isolated_db):
    import os
    did = _drama(novel_reference_filename="novel.txt")
    folder = os.path.join(db.DRAMAS_DIR, str(did))
    assert not os.path.exists(folder)
    assert svc.get_translate_config(did)["has_novel_reference"] is False
    assert not os.path.exists(folder)


def test_config_reports_ollama_reachable_boolean_only(isolated_db, monkeypatch):
    """Parity X24: a boolean from check_ollama_reachable against the
    configured URL; the URL itself is never in the config."""
    seen = []
    real = settings_service.resolve_key
    monkeypatch.setattr(settings_service, "resolve_key",
                        lambda k, *a, **kw: "http://10.9.8.7:11434" if k == "ollama_url"
                        else real(k, *a, **kw))
    monkeypatch.setattr(translate_engines, "check_ollama_reachable",
                        lambda url: seen.append(url) or True)
    cfg = svc.get_translate_config(_drama(translation_engine="ollama"))
    assert cfg["ollama_reachable"] is True and seen == ["http://10.9.8.7:11434"]
    assert "10.9.8.7" not in json.dumps(cfg)
    monkeypatch.setattr(translate_engines, "check_ollama_reachable", lambda url: False)
    assert svc.get_translate_config(_drama(translation_engine="ollama"))["ollama_reachable"] is False


def test_config_probes_ollama_only_for_an_ollama_drama(isolated_db, monkeypatch):
    """Review fix: the health check is a network call, so a drama on
    another engine (or the default engine, when it isn't Ollama) gets
    None -- not probed -- and no request is made."""
    seen = []
    monkeypatch.setattr(translate_engines, "check_ollama_reachable",
                        lambda url: seen.append(url) or True)
    assert svc.get_translate_config(_drama(translation_engine="claude"))["ollama_reachable"] is None
    monkeypatch.setattr(settings_service, "get_default_engine", lambda: "gemini")
    assert svc.get_translate_config(_drama(translation_engine=None))["ollama_reachable"] is None
    assert seen == []
    monkeypatch.setattr(settings_service, "get_default_engine", lambda: "ollama")
    assert svc.get_translate_config(_drama(translation_engine=None))["ollama_reachable"] is True
    assert len(seen) == 1


def test_api_config_carries_ollama_reachable(isolated_db, monkeypatch):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    monkeypatch.setattr(translate_engines, "check_ollama_reachable", lambda url: True)
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = c.get(f"/api/translate-run/dramas/{_drama(translation_engine='ollama')}/config")
    assert r.status_code == 200 and r.json()["ollama_reachable"] is True
    r = c.get(f"/api/translate-run/dramas/{_drama(translation_engine='claude')}/config")
    assert r.status_code == 200 and r.json()["ollama_reachable"] is None


def test_summary_engine_build_failure_is_logged_without_key(isolated_db, monkeypatch):
    import applog
    import translate_engines
    from services import settings_service, translate_service, translate_run_service as trs
    seen = []

    class Log:
        def warning(self, msg, *args):
            seen.append(msg % args)
    monkeypatch.setattr(applog, "get_logger", lambda: Log())
    monkeypatch.setattr(settings_service, "get_preference", lambda k: "claude")
    monkeypatch.setattr(translate_service, "resolve_api_key", lambda c: "k")

    def boom(*a, **k):
        raise ValueError("bad config sk-ant-abcdefghijklmnopqrstuvwxyz0123")
    monkeypatch.setattr(translate_engines, "get_engine", boom)
    assert trs._summary_engine() == (None, None)
    assert len(seen) == 1 and "bad config" in seen[0] and "sk-ant-abcdef" not in seen[0]
