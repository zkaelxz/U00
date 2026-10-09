"""The she/her default has to survive an ASR that writes 他 for every spoken
"tā": a per-batch reminder, hidden written 他/她 in the source (only while no
he/him character exists), and a one-shot recheck of any "he" it still lets
through. Mocked engine, no network."""
import pytest

import translate_engines as te
import translation_guide as tguide
from core import Line
from engine_backends.claude import ClaudeEngine

ON = tguide.build_style_guidelines(default_female_pronouns=True)
OFF = tguide.build_style_guidelines(default_female_pronouns=False)
WITH_MALE = ON + "\n\n" + tguide.build_character_gender_hints(
    [], [{"character_name": "Lin", "pronouns": "he/him"}], True)


class ScriptedEngine:
    """Answers each requested id from `answers` (a list of {id: text}, one per
    call) and records what it was asked."""
    name = "scripted"
    supports_reference = True

    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def translate_batch(self, zh_lines, context):
        self.calls.append({"zh": list(zh_lines), "ids": list(context["line_ids"]), "context": context})
        reply = self.answers.pop(0)
        return [reply[i] for i in context["line_ids"]]


def _lines(*texts, speaker=None):
    return [Line(idx=i, start=i, end=i + 1, zh=t, speaker=speaker, id=100 + i)
            for i, t in enumerate(texts)]


def _run(engine, lines, guidelines, names=None, **kw):
    return te.translate_lines_with_engine(
        lines, engine, drama_meta={"source_language": "zh"}, style_guidelines=guidelines,
        character_names=names or {}, context_window=0, context_window_ahead=0, **kw)


def _user_message(guidelines, numbered="1. 他来了", **ctx):
    return te.build_batch_user_message(
        {"style_guidelines": guidelines, "source_language": "zh", **ctx}, numbered)


def test_batch_prompt_opens_with_the_pronoun_note_only_when_on():
    on, off = _user_message(ON), _user_message(OFF)
    assert on.startswith("PRONOUN NOTE:") and "tā" in on and "他" in on
    assert "PRONOUN NOTE" not in off and off.startswith("Translate these lines")


def test_note_is_skipped_for_a_non_chinese_source():
    assert "PRONOUN NOTE" not in _user_message(ON, source_language="ja")


def test_claude_sends_the_note_in_the_user_message_not_the_cached_system_prompt():
    engine = ClaudeEngine.__new__(ClaudeEngine)
    engine.model = "m"
    params = engine.build_request_params(
        {"style_guidelines": ON, "drama_meta": {}, "source_language": "zh"}, "1. 他来了")
    assert params["messages"][0]["content"].startswith("PRONOUN NOTE:")
    assert "PRONOUN NOTE" not in str(params["system"])


def test_neutralising_hides_he_but_not_words_that_merely_contain_it():
    text = "他说其他人在弹吉他，她们看他人，他们走了"
    assert te.pronoun_neutral_texts({"style_guidelines": ON}, [text]) == [
        "TA说其他人在弹吉他，TA-PL看他人，TA-PL走了"]


def test_neutralising_is_off_when_the_default_is_off_or_a_male_is_known_or_not_chinese():
    for ctx in ({"style_guidelines": OFF}, {"style_guidelines": WITH_MALE},
                {"style_guidelines": ON, "source_language": "ja"}):
        assert te.pronoun_neutral_texts(ctx, ["他来了"]) == ["他来了"]


def test_the_model_is_sent_the_neutral_source_but_the_stored_line_keeps_its_text():
    engine = ScriptedEngine({100: "She came."})
    lines = _lines("他来了")
    _run(engine, lines, ON)
    assert engine.calls[0]["zh"] == ["TA来了"] and lines[0].zh == "他来了"


def test_recent_and_upcoming_context_are_neutralised_too():
    msg = _user_message(ON, recent_context=[("他走了", "She left.")], upcoming_lines=["他回来"])
    assert "TA走了 -> She left." in msg and "- TA回来" in msg


def test_male_output_is_asked_again_once_for_just_those_ids():
    engine = ScriptedEngine({100: "He came.", 101: "Hello.", 102: "She said he left."},
                            {100: "She came.", 102: "She said they left."})
    lines = _lines("他来了", "你好", "她说他走了")
    _run(engine, lines, ON)
    assert len(engine.calls) == 2
    retry = engine.calls[1]
    assert retry["ids"] == [100, 102] and retry["context"]["pronoun_strict"]
    assert [ln.en for ln in lines] == ["She came.", "Hello.", "She said they left."]
    assert not any(ln.flag for ln in lines)


def test_a_line_still_male_after_the_retry_is_flagged_not_asked_a_third_time():
    engine = ScriptedEngine({100: "He came."}, {100: "He did come."})
    lines = _lines("他来了")
    _run(engine, lines, ON)
    assert len(engine.calls) == 2
    assert lines[0].flag == "pronoun_check" and lines[0].en == "He did come."


def test_an_existing_flag_is_kept():
    engine = ScriptedEngine({100: "He came."}, {100: "He came."})
    lines = _lines("他来了")
    lines[0].flag = "uncertain_translation"
    _run(engine, lines, ON, force_retranslate=True)
    assert lines[0].flag == "uncertain_translation"


def test_nothing_is_rechecked_when_the_default_is_off():
    engine = ScriptedEngine({100: "He came."})
    lines = _lines("他来了")
    _run(engine, lines, OFF)
    assert len(engine.calls) == 1 and lines[0].flag is None


def test_a_male_speaker_is_left_alone():
    engine = ScriptedEngine({100: "He came."})
    lines = _lines("他来了", speaker="SPEAKER_01")
    _run(engine, lines, WITH_MALE, names={"SPEAKER_01": "Lin (he/him)"})
    assert len(engine.calls) == 1 and lines[0].flag is None


def test_a_line_that_names_a_known_male_character_is_left_alone():
    engine = ScriptedEngine({100: "She asked Lin if he was fine."})
    lines = _lines("她问Lin他好不好")
    _run(engine, lines, WITH_MALE)
    assert len(engine.calls) == 1 and lines[0].flag is None


def test_the_recheck_respects_the_cost_cap(monkeypatch):
    engine = ScriptedEngine({100: "He came."}, {100: "She came."})
    engine.last_usage = {"input_tokens": 10, "output_tokens": 10}
    monkeypatch.setattr("engine_backends.translate_pipeline.estimate_cost_for_engine",
                        lambda *a, **k: 1.0)
    lines = _lines("他来了")
    _run(engine, lines, ON, cost_cap_usd=0.5)
    assert len(engine.calls) == 1 and lines[0].flag == "pronoun_check"


def test_a_failing_recheck_keeps_the_translation_and_flags_it(monkeypatch):
    class Boom(ScriptedEngine):
        def translate_batch(self, zh_lines, context):
            if context.get("pronoun_strict"):
                raise RuntimeError("sk-secret-123456789 down")
            return super().translate_batch(zh_lines, context)
    engine = Boom({100: "He came."})
    lines = _lines("他来了")
    _, errors = _run(engine, lines, ON)
    assert lines[0].en == "He came." and lines[0].flag == "pronoun_check" and errors == []


def test_the_flag_has_a_review_label():
    assert "he/him" in te.flag_reason_label("pronoun_check")


def test_the_style_text_carries_the_marker_the_switch_looks_for():
    assert te.pronoun_default_active(tguide.FEMALE_PRONOUN_DEFAULT_GUIDANCE)
    assert not te.pronoun_default_active(OFF)


def test_a_bulk_request_hides_he_and_opens_with_the_note():
    import bulk_translate

    class Provider:
        def build_request(self, key, ctx, numbered):
            return te.build_batch_user_message(ctx, numbered)
    lines = _lines("他来了")
    reqs, _rows = bulk_translate.build_bulk_requests(
        1, lines, Provider(), {"style_guidelines": ON, "source_language": "zh"}, batch_size=20,
        context_window=0, context_window_ahead=0)
    assert reqs[0].startswith("PRONOUN NOTE:") and "100. TA来了" in reqs[0]
