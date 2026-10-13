"""A reply cut off by the output limit keeps its complete id-keyed pairs, and
only the lines still missing are asked for again. No network."""
import json
from types import SimpleNamespace

from engine_backends import shared
from engine_backends.claude import ClaudeEngine
from engine_backends.gemini import GeminiEngine
from engine_backends.shared import ContentModerationBlocked, TruncatedReply

import pytest

LINES = [f"句{i}" for i in range(1, 9)]


def _ids_asked(numbered):
    return [int(p.split(".", 1)[0]) for p in numbered.splitlines() if p[:1].isdigit()]


class TestParseSalvagesCutOffReply:
    def test_complete_pairs_of_a_cut_off_object_are_kept(self):
        got = shared.parse_id_keyed_json('{"5":"a","6":"b","7":"cut', [5, 6, 7, 8])
        assert got == {"5": "a", "6": "b"}

    def test_a_pair_for_an_unasked_id_is_ignored(self):
        assert shared.parse_id_keyed_json('{"9":"x","5":"a","6":"b', [5, 6]) == {"5": "a"}

    def test_a_value_that_is_not_a_string_is_not_salvaged(self):
        assert shared.parse_id_keyed_json('{"5":["a"],"6":"b","7":"c', [5, 6, 7]) == {"6": "b"}

    def test_prose_with_a_bracket_before_the_object(self):
        text = 'Lines [1] and [2]:\n{"5":"a","6":"b"}'
        assert shared.parse_id_keyed_json(text, [5, 6]) == {"5": "a", "6": "b"}

    def test_a_wrapper_object_is_unwrapped(self):
        text = '{"translations": {"5": "a", "6": "b"}}'
        assert shared.parse_id_keyed_json(text, [5, 6]) == {"5": "a", "6": "b"}

    def test_a_plain_array_still_matches_nothing(self):
        assert shared.parse_id_keyed_json('["a", "b"]', [1, 2]) == {}


class TestRetryAsksOnlyForMissingLines:
    def _run(self, replies):
        asked = []

        def call(numbered):
            asked.append(_ids_asked(numbered))
            return replies.pop(0)(asked[-1])

        out = shared.request_translations_with_retry(LINES, None, call, engine_name="claude")
        return out, asked

    def test_truncated_reply_keeps_its_pairs_and_halves_the_rest(self):
        def cut(ids):
            return TruncatedReply('{"1":"E1","2":"E2","3":"E3","4":"cu')

        def full(ids):
            return json.dumps({str(i): f"E{i}" for i in ids})

        out, asked = self._run([cut, full, full])
        assert asked[0] == list(range(1, 9))
        assert asked[1:] == [[4, 5, 6], [7, 8]]
        assert out == [f"E{i}" for i in range(1, 9)]

    def test_each_line_gets_its_own_translation_by_id(self):
        def cut(ids):
            return TruncatedReply('{"3":"E3","1":"E1')

        def rest(ids):
            return json.dumps({str(i): f"E{i}" for i in ids})

        out, _ = self._run([cut, rest, rest])
        assert out[0] == "E1" and out[2] == "E3" and out[1] == "E2"

    def test_splitting_is_bounded_and_leaves_the_rest_blank(self):
        calls = []

        def call(numbered):
            calls.append(1)
            return TruncatedReply("")

        out = shared.request_translations_with_retry(LINES, None, call, engine_name="claude")
        assert out == [""] * 8
        assert len(calls) <= 8

    def test_an_ordinary_short_reply_still_retries_once_whole(self):
        asked = []

        def call(numbered):
            asked.append(_ids_asked(numbered))
            return json.dumps({"1": "E1", "2": "E2"}) if len(asked) == 1 else "{}"

        out = shared.request_translations_with_retry(LINES, None, call)
        assert asked == [list(range(1, 9)), list(range(3, 9))]
        assert out[:2] == ["E1", "E2"] and out[2:] == [""] * 6


def _claude(replies):
    engine = ClaudeEngine("sk-ant-fake")
    engine.client = SimpleNamespace(messages=SimpleNamespace(create=lambda **kw: replies.pop(0)(kw)))
    return engine


def _claude_resp(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop,
                           usage=SimpleNamespace(input_tokens=10, output_tokens=5,
                                                 cache_read_input_tokens=0,
                                                 cache_creation_input_tokens=0))


def test_claude_max_tokens_keeps_pairs_and_bills_every_call():
    def cut(kw):
        return _claude_resp('{"1":"E1","2":"E2","3":"cu', stop="max_tokens")

    def rest(kw):
        numbered = kw["messages"][0]["content"].split("Translate these lines:\n\n", 1)[1]
        return _claude_resp(json.dumps({str(i): f"E{i}" for i in _ids_asked(numbered)}))

    engine = _claude([cut, rest, rest])
    out = engine.translate_batch(LINES, {})
    assert out == [f"E{i}" for i in range(1, 9)]
    assert engine.last_usage["input_tokens"] == 30


def _gemini_body(parts, finish="STOP"):
    cand = {"finishReason": finish}
    if parts is not None:
        cand["content"] = {"parts": [{"text": t} for t in parts]}
    return {"candidates": [cand], "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3}}


@pytest.fixture
def gemini_replies(monkeypatch):
    replies = []

    def fake_post_json(url, payload, **kw):
        return replies.pop(0), {}

    monkeypatch.setattr("engine_backends.gemini.post_json", fake_post_json)
    return replies


def _gemini():
    engine = GeminiEngine("key")
    engine._throttle_for_free_tier = lambda: None
    return engine


def test_gemini_joins_every_text_part(gemini_replies):
    gemini_replies.append(_gemini_body(['{"1":"E1",', '"2":"E2"}']))
    out = _gemini().translate_batch(["a", "b"], {})
    assert out == ["E1", "E2"]


def test_gemini_max_tokens_keeps_complete_pairs(gemini_replies):
    gemini_replies.append(_gemini_body(['{"1":"E1","2":"cu'], finish="MAX_TOKENS"))
    gemini_replies.append(_gemini_body(['{"2":"E2"}']))
    out = _gemini().translate_batch(["a", "b"], {})
    assert out == ["E1", "E2"]


@pytest.mark.parametrize("finish", ["RECITATION", "BLOCKLIST", "SPII"])
def test_gemini_reply_without_content_is_a_block_not_a_keyerror(gemini_replies, finish):
    gemini_replies.append(_gemini_body(None, finish=finish))
    with pytest.raises(ContentModerationBlocked) as err:
        _gemini().translate_batch(["a"], {})
    assert err.value.reason == finish


def _deepseek(choices, monkeypatch):
    from engine_backends import openai_compat
    monkeypatch.setattr(openai_compat, "make_openai_client", lambda *a, **k: None)
    engine = openai_compat.DeepSeekEngine("sk-fake")
    resp = SimpleNamespace(choices=choices, usage=None)
    engine.client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(
        create=lambda **kw: resp)))
    return engine


def _choice(content, finish="stop"):
    return SimpleNamespace(message=SimpleNamespace(content=content, refusal=None), finish_reason=finish)


def test_deepseek_empty_choices_is_a_block_not_an_indexerror(monkeypatch):
    with pytest.raises(ContentModerationBlocked):
        _deepseek([], monkeypatch).translate_batch(["a"], {})


def test_deepseek_content_filter_is_a_block(monkeypatch):
    with pytest.raises(ContentModerationBlocked) as err:
        _deepseek([_choice("", "content_filter")], monkeypatch).translate_batch(["a"], {})
    assert err.value.reason == "content_filter"


def test_deepseek_length_stop_keeps_complete_pairs(monkeypatch):
    replies = [_choice('{"1":"E1","2":"cu', "length"), _choice('{"2":"E2"}')]
    engine = _deepseek([], monkeypatch)
    engine.client.chat.completions.create = lambda **kw: SimpleNamespace(
        choices=[replies.pop(0)], usage=None)
    assert engine.translate_batch(["a", "b"], {}) == ["E1", "E2"]
