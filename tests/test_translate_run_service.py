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
    for name in ("test_offline", "ollama", "nllb", "libretranslate"):
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


def test_validation(isolated_db):
    did = _drama()
    with pytest.raises(InvalidInputError):
        svc.estimate_translate_cost(did, "nope")
    with pytest.raises(UnsupportedOperationError):
        svc.estimate_translate_cost(did, "deepl", reflect=True)
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
