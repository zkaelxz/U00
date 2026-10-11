"""services/fixflag_translate.py: reason-aware, id-keyed re-translation of flagged
lines. Mocked: no network, no real model."""
import json
import re

import pytest

from core import Line
from services import fixflag_translate


def _line(idx, zh, en, flag="uncertain_translation", note=""):
    return Line(idx=idx, start=idx, end=idx + 1, zh=zh, en=en, flag=flag, flag_note=note)


def _run(lines, engine, **kw):
    errors = kw.pop("errors", [])
    out = fixflag_translate.fix_lines(
        lines, engine=engine, engine_choice="claude", drama_id=1, base_context={},
        character_names={}, source_language="zh", heard_by_idx={}, translate=True,
        cost_cap_usd=kw.pop("cost_cap_usd", None), check_cancel=lambda: None,
        on_progress=lambda done, total: None, errors=errors)
    return out, errors


class _LLM:
    model = "fake-model"
    supports_reference = True


@pytest.fixture
def llm_replies(monkeypatch):
    """Patches the paid call; `replies` maps source text -> English (None = omit)."""
    state = {"replies": {}, "prompts": []}

    def fake(engine, prompt, max_tokens=2000, fallback="[]", usage_cb=None):
        state["prompts"].append(prompt)
        found = re.findall(r"\[(\d+)\] Source: (.*)\n", prompt)
        # Reversed on purpose: matching must follow the id, not the order.
        body = {n: state["replies"][zh] for n, zh in reversed(found)
                if state["replies"].get(zh) is not None}
        return json.dumps(body)
    monkeypatch.setattr(fixflag_translate.translate_engines, "call_llm_json", fake)
    monkeypatch.setattr(fixflag_translate.db, "log_usage", lambda *a, **k: None)
    return state


def test_reply_lands_on_its_own_line_by_id(llm_replies):
    llm_replies["replies"] = {"一": "one", "二": "two"}
    lines = [_line(0, "一", "x"), _line(1, "二", "y")]
    out, _ = _run(lines, _LLM())
    assert [ln.en for ln in lines] == ["one", "two"]
    assert out == {"fixed": 2, "unchanged": 0, "cap_reached": None}
    assert len(llm_replies["prompts"]) == 1


def test_a_line_with_no_reply_stays_flagged_and_untouched(llm_replies):
    llm_replies["replies"] = {"一": "one", "二": None}
    lines = [_line(0, "一", "x"), _line(1, "二", "y")]
    _run(lines, _LLM())
    assert (lines[0].en, lines[0].flag) == ("one", None)
    assert (lines[1].en, lines[1].flag) == ("y", "uncertain_translation")


def test_identical_reply_keeps_the_flag_and_is_counted(llm_replies):
    llm_replies["replies"] = {"一": "x "}
    lines = [_line(0, "一", "x")]
    out, _ = _run(lines, _LLM())
    assert (lines[0].en, lines[0].flag) == ("x", "uncertain_translation")
    assert out["fixed"] == 0 and out["unchanged"] == 1


def test_prompt_carries_the_flag_note_and_current_english(llm_replies):
    llm_replies["replies"] = {"一": "one"}
    _run([_line(0, "一", "she left", "ambiguous_reference", "who is she?")], _LLM())
    prompt = llm_replies["prompts"][0]
    assert "Current English: she left" in prompt and "who is she?" in prompt
    assert "pronoun or reference" in prompt


def test_batches_split_at_batch_size(llm_replies, monkeypatch):
    monkeypatch.setattr(fixflag_translate, "BATCH_SIZE", 2)
    llm_replies["replies"] = {f"句{i}": f"s{i}" for i in range(5)}
    lines = [_line(i, f"句{i}", "x") for i in range(5)]
    _run(lines, _LLM())
    assert len(llm_replies["prompts"]) == 3
    assert [ln.en for ln in lines] == [f"s{i}" for i in range(5)]


def test_a_failed_batch_is_recorded_per_line_with_secrets_redacted(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("401 key sk-ant-SECRET1234567890abcdef")
    monkeypatch.setattr(fixflag_translate.translate_engines, "call_llm_json", boom)
    lines = [_line(0, "一", "x"), _line(1, "二", "y")]
    out, errors = _run(lines, _LLM())
    assert out["fixed"] == 0 and all(ln.flag for ln in lines)
    assert len(errors) == 2 and not any("SECRET1234567890" in e for e in errors)


def test_translation_only_engine_translates_the_source_in_one_batch():
    class MT:
        model = "mt"
        calls = []

        def translate_batch(self, zh, ctx):
            MT.calls.append((list(zh), ctx["line_ids"]))
            return [f"EN:{z}" for z in zh]
    lines = [_line(0, "一", "x"), _line(1, "二", "EN:二")]
    out, _ = _run(lines, MT())
    assert MT.calls == [(["一", "二"], [0, 1])]
    assert lines[0].en == "EN:一" and lines[0].flag is None
    assert lines[1].flag == "uncertain_translation" and out["unchanged"] == 1
