"""services/fixflag_translate.py: reason-aware, id-keyed re-translation of flagged
lines. Mocked: no network, no real model."""
import json
from engine_backends import llm_tasks
import re

import pytest

from core import Line
from services import fixflag_translate


def _line(idx, zh, en, flag="uncertain_translation", note=""):
    return Line(idx=idx, start=idx, end=idx + 1, zh=zh, en=en, flag=flag, flag_note=note)


def _run(lines, engine, **kw):
    errors = kw.pop("errors", [])
    heard = kw.pop("heard_by_idx", {})
    out = fixflag_translate.fix_lines(
        lines, engine=engine, engine_choice=kw.pop("engine_choice", "claude"), drama_id=1,
        base_context=kw.pop("base_context", {}),
        character_names={}, source_language="zh", heard_by_idx=heard, translate=True,
        cost_cap_usd=kw.pop("cost_cap_usd", None), check_cancel=lambda: None,
        on_progress=lambda done, total: None, errors=errors,
        all_lines=kw.pop("all_lines", None))
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
    monkeypatch.setattr(llm_tasks, "call_llm_json", fake)
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


def test_an_unusable_reply_is_not_saved_and_the_line_stays_flagged(llm_replies):
    llm_replies["replies"] = {"一": "她 left.", "二": '{"1": "two"}', "三": "three"}
    lines = [_line(0, "一", "x"), _line(1, "二", "y"), _line(2, "三", "z")]
    out, errors = _run(lines, _LLM(), engine_choice="ollama")
    assert [(ln.en, ln.flag) for ln in lines] == [
        ("x", "uncertain_translation"), ("y", "uncertain_translation"), ("three", None)]
    assert out["fixed"] == 1
    assert errors == ["line 1: the reply was not used (source text left in it)",
                      "line 2: the reply was not used (JSON instead of a line)"]


def test_a_glossary_term_kept_in_the_english_is_not_leftover_source(llm_replies):
    terms = [{"term_original": "林晚", "term_translation": "林晚"}]
    llm_replies["replies"] = {"林晚来了": "林晚 came."}
    lines = [_line(0, "林晚来了", "x")]
    _run(lines, _LLM(), base_context={"glossary_terms": terms}, engine_choice="ollama")
    assert (lines[0].en, lines[0].flag) == ("林晚 came.", None)


def test_the_reply_check_follows_the_translate_runs_switch(llm_replies, monkeypatch):
    llm_replies["replies"] = {"一": "她 left."}
    # Claude's replies are not checked by default, as in a translate run.
    lines = [_line(0, "一", "x")]
    _run(lines, _LLM())
    assert (lines[0].en, lines[0].flag) == ("她 left.", None)
    monkeypatch.setattr(fixflag_translate.settings_service, "reply_guard_override", lambda: True)
    lines = [_line(0, "一", "x")]
    _run(lines, _LLM())
    assert (lines[0].en, lines[0].flag) == ("x", "uncertain_translation")


def test_new_english_still_missing_a_glossary_name_stays_flagged_for_it(llm_replies):
    terms = [{"term_original": "林晚", "term_translation": "Lin Wan"}]
    llm_replies["replies"] = {"林晚来了": "Lynn came.", "林晚走了": "Lin Wan left."}
    lines = [_line(0, "林晚来了", "x", flag="reading_speed"),
             _line(1, "林晚走了", "y", flag="glossary_missing", note="Glossary: 林晚 -> Lin Wan missing")]
    out, _ = _run(lines, _LLM(), base_context={"glossary_terms": terms})
    assert (lines[0].en, lines[0].flag) == ("Lynn came.", "glossary_missing")
    assert "Lin Wan" in lines[0].flag_note
    assert (lines[1].en, lines[1].flag, lines[1].flag_note) == ("Lin Wan left.", None, "")
    assert out["fixed"] == 1


def test_a_plain_stream_fix_checks_only_the_terms_its_prompt_lists(llm_replies):
    from engine_backends import stream_recipe
    terms = [{"term_original": "牛肉", "term_translation": "beef", "category": "culture_item"},
             {"term_original": "林晚", "term_translation": "Lin Wan"}]
    llm_replies["replies"] = {"林晚吃牛肉": "Lynn ate steak."}
    lines = [_line(0, "林晚吃牛肉", "x", flag="reading_speed")]
    _run(lines, _LLM(), base_context={"glossary_terms": terms,
                                      "style_guidelines": stream_recipe.build_guidelines(terms)})
    assert (lines[0].en, lines[0].flag) == ("Lynn ate steak.", "glossary_missing")
    assert "Lin Wan" in lines[0].flag_note and "beef" not in lines[0].flag_note
    llm_replies["replies"] = {"林晚吃牛肉": "Lin Wan ate steak."}
    lines = [_line(0, "林晚吃牛肉", "x", flag="reading_speed")]
    _run(lines, _LLM(), base_context={"glossary_terms": terms,
                                      "style_guidelines": stream_recipe.build_guidelines(terms)})
    assert (lines[0].en, lines[0].flag) == ("Lin Wan ate steak.", None)


def test_prompt_carries_the_flag_note_and_current_english(llm_replies):
    llm_replies["replies"] = {"一": "one"}
    _run([_line(0, "一", "she left", "ambiguous_reference", "who is she?")], _LLM())
    prompt = llm_replies["prompts"][0]
    assert "Current English: she left" in prompt and "who is she?" in prompt
    assert "pronoun or reference" in prompt


def test_new_heard_text_keeps_the_users_reason_in_the_prompt_and_on_the_line(llm_replies):
    # Hearing the line again changes its source, which re-flags it source_changed;
    # the reason the user flagged it for must still be what the model is asked to fix.
    llm_replies["replies"] = {"一。": None}
    lines = [_line(0, "一", "she left", "ambiguous_reference", "who is she?")]
    _run(lines, _LLM(), heard_by_idx={0: {"text": "一。"}})
    prompt = llm_replies["prompts"][0]
    assert "Flagged because: " in prompt and "who is she?" in prompt
    assert "pronoun or reference" in prompt
    # No new English landed: the line is flagged source_changed and keeps the reason.
    assert lines[0].flag == "source_changed"
    assert "who is she?" in lines[0].flag_note


def test_new_heard_text_with_a_refused_reply_stays_source_changed_with_the_reason(llm_replies):
    llm_replies["replies"] = {"一。": "她 left."}
    lines = [_line(0, "一", "she left", "ambiguous_reference", "who is she?")]
    _, errors = _run(lines, _LLM(), heard_by_idx={0: {"text": "一。"}}, engine_choice="ollama")
    assert (lines[0].zh, lines[0].en, lines[0].flag) == ("一。", "she left", "source_changed")
    assert "who is she?" in lines[0].flag_note
    assert errors == ["line 1: the reply was not used (source text left in it)"]


def test_new_heard_text_whose_english_misses_a_glossary_name_is_flagged_for_it(llm_replies):
    terms = [{"term_original": "林晚", "term_translation": "Lin Wan"}]
    llm_replies["replies"] = {"林晚来了。": "Lynn came."}
    lines = [_line(0, "林晚来了", "x", "ambiguous_reference", "who?")]
    out, _ = _run(lines, _LLM(), heard_by_idx={0: {"text": "林晚来了。"}},
                  base_context={"glossary_terms": terms})
    assert (lines[0].zh, lines[0].en, lines[0].flag) == ("林晚来了。", "Lynn came.", "glossary_missing")
    assert "Lin Wan" in lines[0].flag_note
    assert out["fixed"] == 0


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
    monkeypatch.setattr(llm_tasks, "call_llm_json", boom)
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


class _Mt:
    """Translation-only engine: a list in, a list out."""
    model = "mt"
    supports_reference = False

    def __init__(self, reply):
        self.reply = reply

    def translate_batch(self, texts, context):
        return self.reply(texts)


def test_short_reply_from_a_translation_only_engine_fails_the_whole_batch():
    lines = [_line(0, "一", "x"), _line(1, "二", "y"), _line(2, "三", "z")]
    out, errors = _run(lines, _Mt(lambda texts: ["one", "two"]))
    assert [ln.en for ln in lines] == ["x", "y", "z"]
    assert all(ln.flag for ln in lines) and out["fixed"] == 0
    assert len(errors) == 3 and "returned 2 lines for 3" in errors[0]


def test_matching_reply_from_a_translation_only_engine_is_applied():
    lines = [_line(0, "一", "x"), _line(1, "二", "y")]
    out, errors = _run(lines, _Mt(lambda texts: ["one", "two"]))
    assert [ln.en for ln in lines] == ["one", "two"] and not errors and out["fixed"] == 2


def test_timing_flags_are_split_off_and_reading_speed_stays():
    flags = ["timing_drift", "timing_overlap", "timing_uncertain", "reading_speed",
             "uncertain_translation"]
    lines = [_line(n, f"句{n}", "x", flag=f) for n, f in enumerate(flags)]
    fixable, skipped = fixflag_translate.split_timing_flags(lines)
    assert [ln.flag for ln in fixable] == ["reading_speed", "uncertain_translation"]
    assert skipped == 3


def _script(n):
    return [Line(idx=i, start=i, end=i + 1, zh=f"源{i}", en=f"eng{i}") for i in range(n)]


def _flag(script, *idxs):
    for i in idxs:
        script[i].flag, script[i].flag_note = "ambiguous_reference", ""
    return [script[i] for i in idxs]


def _run_with_context(batch, script, llm_replies):
    llm_replies["replies"] = {ln.zh: "fixed" for ln in batch}
    return _run(batch, _LLM(), all_lines=script)


def test_prompt_shows_three_lines_either_side_as_context_only(llm_replies):
    script = _script(20)
    batch = _flag(script, 10)
    _run_with_context(batch, script, llm_replies)
    prompt = llm_replies["prompts"][0]
    marked = re.findall(r"\(context only, do not return\) Source: (源\d+)", prompt)
    assert marked == ["源7", "源8", "源9", "源11", "源12", "源13"]
    assert re.findall(r"^\[\d+\] Source: (.*)$", prompt, re.M) == ["源10"]
    assert [ln.en for ln in script if ln.idx != 10] == [f"eng{i}" for i in range(20) if i != 10]
    assert script[10].en == "fixed"


def test_overlapping_windows_merge_and_show_each_line_once(llm_replies):
    script = _script(30)
    batch = _flag(script, 10, 12, 25)
    _run_with_context(batch, script, llm_replies)
    prompt = llm_replies["prompts"][0]
    shown = re.findall(r"Source: (源\d+)", prompt)
    assert shown == [f"源{i}" for i in [*range(7, 16), *range(22, 29)]]
    assert len(shown) == len(set(shown)) and "...\n" in prompt


def test_context_never_changes_or_gets_saved_even_if_the_model_returns_it(monkeypatch, llm_replies):
    script = _script(10)
    batch = _flag(script, 5)

    def reply(engine, prompt, max_tokens=2000, fallback="[]", usage_cb=None):
        return json.dumps({"1": "fixed", "2": "context hijack", "3": "context hijack"})
    monkeypatch.setattr(llm_tasks, "call_llm_json", reply)
    _run(batch, _LLM(), all_lines=script)
    assert script[5].en == "fixed"
    assert [ln.en for ln in script if ln.idx != 5] == [f"eng{i}" for i in range(10) if i != 5]


def test_prompt_growth_is_bounded_by_the_window(llm_replies):
    # Worst case: 15 flagged lines too far apart to share a window.
    script = _script(200)
    batch = _flag(script, *range(3, 200, 13)[:fixflag_translate.BATCH_SIZE])
    _run_with_context(batch, script, llm_replies)
    prompt = llm_replies["prompts"][0]
    context = prompt.count("(context only, do not return)")
    assert context == 2 * fixflag_translate.CONTEXT_RADIUS * len(batch)
    assert len(llm_replies["prompts"]) == 1
