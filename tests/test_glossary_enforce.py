"""Glossary check after each translate batch: lines whose English lacks a
glossary name go back once to the same engine (matched by line id), and a
line still wrong is flagged for Review. Mocked engine, no network."""
import translate_engines as te
from core import Line
from engine_backends import glossary_check

TERMS = [{"term_original": "白上吹雪", "term_translation": "Shirakami Fubuki", "aliases": "吹雪",
          "enforce_exact": 0, "notes": ""}]


class ScriptedEngine:
    """Answers each requested id from `answers` (one {id: text} per call)."""
    name = "scripted"
    supports_reference = True

    def __init__(self, *answers, name="scripted"):
        self.name = name
        self.answers = list(answers)
        self.calls = []

    def translate_batch(self, zh_lines, context):
        self.calls.append({"ids": list(context["line_ids"]), "context": context})
        reply = self.answers.pop(0)
        return [reply[i] for i in context["line_ids"]]


def _lines():
    return [Line(idx=0, start=0, end=1, zh="白上吹雪来了", id=10),
            Line(idx=1, start=1, end=2, zh="今天天气好", id=11),
            Line(idx=2, start=2, end=3, zh="吹雪你好", id=12)]


def _run(engine, lines, **kw):
    return te.translate_lines_with_engine(
        lines, engine, drama_meta={"source_language": "zh"}, glossary_terms=TERMS,
        context_window=0, context_window_ahead=0, **kw)


def test_wrong_name_then_right_name_is_fixed_by_one_id_keyed_retry():
    engine = ScriptedEngine({10: "Shirokami Buki came.", 11: "Nice weather.", 12: "Hi Fubuki."},
                            {10: "Shirakami Fubuki came.", 12: "Hello, Shirakami Fubuki."})
    lines = _run(engine, _lines())[0]
    assert [ln.en for ln in lines] == ["Shirakami Fubuki came.", "Nice weather.",
                                       "Hello, Shirakami Fubuki."]
    assert len(engine.calls) == 2 and engine.calls[1]["ids"] == [10, 12]
    assert all(not ln.flag for ln in lines)


def test_the_retry_prompt_lists_the_missed_terms_first():
    engine = ScriptedEngine({10: "x", 11: "y", 12: "z"},
                            {10: "Shirakami Fubuki came.", 12: "Hello, Shirakami Fubuki."})
    _run(engine, _lines())
    msg = te.build_batch_user_message(engine.calls[1]["context"], "1. a")
    assert msg.startswith("GLOSSARY CORRECTION") and "白上吹雪 -> Shirakami Fubuki" in msg


def test_a_line_that_stays_wrong_is_flagged_and_keeps_its_english():
    engine = ScriptedEngine({10: "Buki came.", 11: "Nice.", 12: "Hello Shirakami Fubuki."},
                            {10: "Still Buki."})
    lines = _run(engine, _lines())[0]
    assert len(engine.calls) == 2
    assert lines[0].flag == "glossary_missing" and lines[0].en == "Buki came."
    assert lines[0].flag_note.startswith("Glossary: 白上吹雪 -> Shirakami Fubuki missing")
    assert not lines[1].flag and not lines[2].flag


def test_a_retry_reply_without_the_term_never_replaces_the_first_try():
    engine = ScriptedEngine({10: "Buki came.", 11: "Nice.", 12: "Fubuki, hi."}, {10: "", 12: "Hello"})
    lines = _run(engine, _lines())[0]
    assert lines[0].en == "Buki came." and lines[2].en == "Fubuki, hi."
    assert lines[0].flag == lines[2].flag == "glossary_missing"


def test_no_retry_when_every_name_is_present_or_there_is_no_glossary():
    engine = ScriptedEngine({10: "shirakami fubuki came", 11: "Nice.", 12: "Hi Shirakami Fubuki"})
    _run(engine, _lines())
    assert len(engine.calls) == 1
    other = ScriptedEngine({10: "a", 11: "b", 12: "c"})
    te.translate_lines_with_engine(_lines(), other, drama_meta={"source_language": "zh"},
                                   context_window=0, context_window_ahead=0)
    assert len(other.calls) == 1


def test_no_retry_once_the_run_cost_cap_is_spent():
    class Billed(ScriptedEngine):
        def translate_batch(self, zh_lines, context):
            self.last_usage = {"input_tokens": 10 ** 6, "output_tokens": 10 ** 6}
            return super().translate_batch(zh_lines, context)

    engine = Billed({10: "Buki", 11: "ok", 12: "Buki"}, name="claude")
    engine.model = "claude-sonnet-5-5"
    lines = _run(engine, _lines(), cost_cap_usd=0.0001, usage_cb=lambda *a: None)[0]
    assert len(engine.calls) == 1 and lines[0].flag == "glossary_missing"


def test_a_local_model_gets_the_names_in_the_first_request_too():
    engine = ScriptedEngine({10: "Shirakami Fubuki", 11: "ok", 12: "Shirakami Fubuki"}, name="ollama")
    _run(engine, _lines())
    first = engine.calls[0]["context"]
    assert first["glossary_required"] == [("白上吹雪", "Shirakami Fubuki"), ("白上吹雪", "Shirakami Fubuki")]
    assert te.build_batch_user_message(first, "1. a").startswith("REQUIRED NAMES")


def test_a_cloud_engine_first_request_has_no_extra_block():
    engine = ScriptedEngine({10: "Shirakami Fubuki", 11: "ok", 12: "Shirakami Fubuki"})
    _run(engine, _lines())
    assert "glossary_required" not in engine.calls[0]["context"]


def test_a_flag_is_cleared_when_a_new_translation_has_the_name():
    ln = Line(idx=0, start=0, end=1, zh="吹雪", en="Shirakami Fubuki", id=1,
              flag=glossary_check.GLOSSARY_FLAG, flag_note="Glossary: x missing")
    glossary_check.enforce([ln], TERMS, lambda *a: None, lambda: False)
    assert ln.flag is None and ln.flag_note == ""


def test_another_flag_is_not_overwritten():
    ln = Line(idx=0, start=0, end=1, zh="吹雪", en="Buki", id=1, flag="content_blocked")
    glossary_check.enforce([ln], TERMS, lambda *a: None, lambda: True)
    assert ln.flag == "content_blocked"


def test_a_name_only_reply_is_rejected_and_the_first_try_stays():
    zh = "白上吹雪今天来到了我们这里参加聚会"
    lines = [Line(idx=0, start=0, end=1, zh=zh, id=10)]
    engine = ScriptedEngine({10: "Shirokami Buki arrived today to join our party."},
                            {10: "Shirakami Fubuki"})
    out = _run(engine, lines)[0]
    assert out[0].en == "Shirokami Buki arrived today to join our party."
    assert out[0].flag == "glossary_missing"


def test_a_reply_far_longer_than_its_source_is_rejected():
    lines = [Line(idx=0, start=0, end=1, zh="吹雪好", id=10)]
    merged = "Shirakami Fubuki is good. " + "And then the next speaker said something else entirely. " * 4
    engine = ScriptedEngine({10: "Buki is good."}, {10: merged})
    out = _run(engine, lines)[0]
    assert out[0].en == "Buki is good." and out[0].flag == "glossary_missing"


def test_the_reask_honours_stop_before_batch():
    engine = ScriptedEngine({10: "Buki", 11: "ok", 12: "Buki"}, {10: "Shirakami Fubuki came.", 12: "Hi Shirakami Fubuki."})
    gate = iter([False, True, True])
    lines = _run(engine, _lines(), stop_before_batch=lambda: next(gate))[0]
    assert len(engine.calls) == 1 and lines[0].flag == "glossary_missing"


def test_a_failed_reask_that_ends_the_run_is_logged_without_naming_lines_as_failed():
    from engine_backends.shared import TranslationCancelled  # noqa: F401
    class Dies(ScriptedEngine):
        def translate_batch(self, zh_lines, context):
            if self.calls:
                exc = RuntimeError("quota used up sk-ant-abc123")
                exc.stops_run = True
                raise exc
            return super().translate_batch(zh_lines, context)
    engine = Dies({10: "Buki", 11: "ok", 12: "Buki"})
    errors = _run(engine, _lines())[1]
    assert all(e["lines"] == [] for e in errors) and errors
    assert "sk-ant-abc123" not in str(errors)
