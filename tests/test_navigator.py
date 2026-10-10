"""
tests/test_navigator.py -- navigator.py's own logic (label
translation, navigation-step generation), independent of whichever tab
renders it.

Step 25f item 1: navigator.py was deleted by mistake (commit 4d25c9c),
so "🧭 Translate page + get navigation steps" raised ModuleNotFoundError
on every click, and no test imported it to notice.

Step 17 folded the Navigator tab's UI into Discover's "🧭 Site
navigation helper" section -- see test_discover_tab.py's
TestSiteNavigationHelper for the button/UI-level coverage that used to
live here as TestNavigatorButton.
"""
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import navigator
import translate_engines

class _LLMEngine:
    supports_reference = True


class _MTEngine:
    supports_reference = False

    def translate_batch(self, lines, context):
        return [f"MT:{l}" for l in lines]


def _fake_llm(monkeypatch, label_answer):
    """call_llm_json stand-in: the label-translation prompt gets
    label_answer(prompt), the steps prompt gets fixed steps text."""
    prompts = []

    def fake_call(engine, prompt, max_tokens=2000, fallback="[]", usage_cb=None):
        prompts.append(prompt)
        if "UI labels" in prompt:
            return label_answer(prompt)
        return "1. Click 广播剧 (Audio dramas).\n2. Use 搜索 (Search)."
    monkeypatch.setattr(translate_engines, "call_llm_json", fake_call)
    return prompts


class TestTranslateLabels:
    def test_matches_by_id_not_position(self, monkeypatch):
        # Reordered and one short: position-matching would shift labels.
        _fake_llm(monkeypatch, lambda p: json.dumps({"3": "Search", "1": "Home"}))
        result = navigator.translate_labels(["首页", "排行榜", "搜索"], "English", _LLMEngine())
        assert result["首页"] == "Home"
        assert result["搜索"] == "Search"
        assert "排行榜" not in result

    def test_retries_missing_ids_once(self, monkeypatch):
        answers = iter([json.dumps({"1": "Home"}), json.dumps({"2": "Ranking"})])
        prompts = _fake_llm(monkeypatch, lambda p: next(answers))
        result = navigator.translate_labels(["首页", "排行榜"], "English", _LLMEngine())
        assert result == {"首页": "Home", "排行榜": "Ranking"}
        assert "1. 首页" not in prompts[1] and "2. 排行榜" in prompts[1]

    def test_pure_mt_engine_uses_translate_batch(self):
        assert navigator.translate_labels(["首页"], "English", _MTEngine()) == {"首页": "MT:首页"}

    def test_no_labels(self):
        assert navigator.translate_labels([], "English", _LLMEngine()) == {}


class TestGenerateNavigationSteps:
    def test_includes_translated_labels_in_prompt(self, monkeypatch):
        prompts = _fake_llm(monkeypatch, lambda p: "{}")
        steps = navigator.generate_navigation_steps(
            "https://example.com", "find audio dramas", "English", _LLMEngine(),
            translated_labels={"广播剧": "Audio dramas"})
        assert steps.startswith("1. Click")
        assert "广播剧 -> Audio dramas" in prompts[0]

    def test_pure_mt_engine_declines(self):
        steps = navigator.generate_navigation_steps("x", "y", "English", _MTEngine())
        assert "doesn't support" in steps

