"""A local model through Ollama must not silently write bad data or hang:
the shared non-translation call (call_llm_json) and the translation batch.
Every Ollama reply here is faked; nothing reaches a server."""

import pytest
import requests

from engine_backends import llm_tasks, local, thinking


def _reply(content, done_reason="stop", prompt_eval_count=40, eval_count=8):
    return {"message": {"content": content}, "done_reason": done_reason,
            "prompt_eval_count": prompt_eval_count, "eval_count": eval_count}


class _Chat:
    """Stands in for _ollama_chat; records every request body and answers
    with the next queued reply (the last one repeats)."""

    def __init__(self, *replies):
        self.replies = list(replies) or [_reply('{"1": "Hello."}')]
        self.bodies = []

    def __call__(self, base_url, payload):
        self.bodies.append(payload)
        reply = self.replies[min(len(self.bodies), len(self.replies)) - 1]
        if isinstance(reply, Exception):
            raise reply
        return reply(payload) if callable(reply) else reply


@pytest.fixture(autouse=True)
def _clean_ollama_state():
    thinking._think_refused.clear()
    getattr(local, "_warm_until", {}).clear()
    getattr(local, "_schema_refused", set()).clear()
    yield
    thinking._think_refused.clear()
    getattr(local, "_warm_until", {}).clear()
    getattr(local, "_schema_refused", set()).clear()


@pytest.fixture
def task_chat(monkeypatch):
    """The fake call_llm_json's Ollama branch talks to."""
    def install(*replies):
        fake = _Chat(*replies)
        monkeypatch.setattr(llm_tasks, "_ollama_chat", fake)
        return fake
    return install


def _engine(model="gemma4:12b"):
    return local.OllamaEngine(model=model)


# --- Part A: call_llm_json on Ollama ---------------------------------------

class TestOutputLimit:
    def test_max_tokens_is_sent_as_num_predict(self, task_chat):
        fake = task_chat(_reply("{}"))
        llm_tasks.call_llm_json(_engine(), "p", max_tokens=700, fallback="{}")
        assert fake.bodies[0]["options"]["num_predict"] == 700

    def test_num_predict_never_passes_the_ceiling(self, task_chat):
        fake = task_chat(_reply("{}"))
        llm_tasks.call_llm_json(_engine(), "p", max_tokens=50_000, fallback="{}")
        assert fake.bodies[0]["options"]["num_predict"] == local.OLLAMA_MAX_NUM_PREDICT


class TestReplyShape:
    def test_default_still_asks_for_a_json_object(self, task_chat):
        fake = task_chat(_reply("{}"))
        llm_tasks.call_llm_json(_engine(), "p", fallback="{}")
        assert fake.bodies[0]["format"] == "json"

    @pytest.mark.parametrize("expect", ["text", "json_array"])
    def test_text_and_array_prompts_send_no_format(self, task_chat, expect):
        fake = task_chat(_reply("[]" if expect == "json_array" else "Some prose."))
        llm_tasks.call_llm_json(_engine(), "p", expect=expect)
        assert "format" not in fake.bodies[0]

    def test_an_array_prompt_gets_its_array_back(self, task_chat):
        import line_tools
        task_chat(_reply('[{"word": "你", "reading": "nǐ", "meaning": "you", "function": "subject"}]'))
        parts = line_tools.grammar_breakdown("你好", _engine())
        assert parts and parts[0]["word"] == "你"

    def test_flagging_sends_its_array_prompt_without_format(self, task_chat):
        from types import SimpleNamespace
        fake = task_chat(_reply('[{"line_idx": 0, "reason": "uncertain_translation", "note": "x"}]'))
        line = SimpleNamespace(idx=0, id=1, zh="你好", en="Hello", flag="", flag_note="",
                               speaker=None, start=0.0, end=1.0)
        llm_tasks.flag_uncertain_lines([line], _engine())
        assert "format" not in fake.bodies[0]

    def test_improve_line_returns_plain_text(self, task_chat):
        import line_tools
        fake = task_chat(_reply("Hello there."))
        assert line_tools.improve_line("你好", "Hi", _engine()) == "Hello there."
        assert "format" not in fake.bodies[0]

    def test_improve_line_never_returns_a_json_object_as_the_line(self, task_chat):
        import line_tools
        task_chat(_reply('{"translation": "Hello there."}'))
        with pytest.raises(local.OllamaReplyError):
            line_tools.improve_line("你好", "Hi", _engine())

    @pytest.mark.parametrize("bad", ["", "你好，朋友。", "I'm sorry, but I can't help with that.",
                                     " ".join(["word"] * 80)])
    def test_improve_line_rejects_an_unusable_line(self, task_chat, bad):
        import line_tools
        task_chat(_reply(bad))
        with pytest.raises(local.OllamaReplyError):
            line_tools.improve_line("你好", "Hi", _engine())

    @pytest.mark.parametrize("line", ["I won't leave you.", "I can't do this anymore.",
                                      "I cannot lose you.", "[Sighs] Fine."])
    def test_improve_line_accepts_dialogue_that_only_sounds_like_a_refusal(self, task_chat, line):
        import line_tools
        task_chat(_reply(line))
        assert line_tools.improve_line("我不会离开你", "Hi", _engine()) == line

    def test_explain_is_plain_text(self, task_chat):
        import line_tools
        fake = task_chat(_reply("It says hello."))
        assert line_tools.explain_translation("你好", "Hi", _engine()) == "It says hello."
        assert "format" not in fake.bodies[0]


class TestThinking:
    def test_thinking_is_switched_off(self, task_chat):
        fake = task_chat(_reply("{}"))
        llm_tasks.call_llm_json(_engine(), "p", fallback="{}")
        assert fake.bodies[0]["think"] is False

    def test_a_model_that_cannot_think_is_asked_again_without_the_field(self, task_chat):
        resp = requests.Response()
        resp.status_code = 400
        resp._content = b'"llama3.1" does not support thinking'
        fake = task_chat(requests.HTTPError("400", response=resp), _reply("{}"))
        assert llm_tasks.call_llm_json(_engine("llama3.1"), "p", fallback="{}") == "{}"
        assert ["think" in b for b in fake.bodies] == [True, False]

    @pytest.mark.parametrize("content", [
        "<think>I should say hi</think>Hello there.",
        "<thinking>draft</thinking>Hello there.",
        "<|channel|>analysis<|message|>draft {\"x\": 1}<|end|><|start|>assistant"
        "<|channel|>final<|message|>Hello there.",
        "<|channel>thought\ndraft {\"x\": 1}<channel|>Hello there.",
    ])
    def test_leaked_reasoning_is_stripped(self, task_chat, content):
        task_chat(_reply(content))
        assert llm_tasks.call_llm_json(_engine(), "p", expect="text") == "Hello there."


class TestTruncation:
    def test_a_cut_off_reply_is_a_failure_not_a_result(self, task_chat):
        task_chat(_reply('[{"term": "a"}, {"term": "b"', done_reason="length"))
        with pytest.raises(local.OllamaReplyError, match="cut off"):
            llm_tasks.call_llm_json(_engine(), "p", expect="json_array")

    def test_a_full_context_window_means_the_prompt_was_truncated(self, task_chat):
        fake = task_chat(lambda payload: _reply(
            "{}", prompt_eval_count=payload["options"]["num_ctx"]))
        with pytest.raises(local.OllamaReplyError, match="context window was too small"):
            llm_tasks.call_llm_json(_engine(), "p", fallback="{}")
        assert fake.bodies

    def test_far_fewer_prompt_tokens_than_a_prompt_too_big_for_the_window(self, task_chat):
        task_chat(_reply("{}", prompt_eval_count=500))
        huge = "x" * (local.OLLAMA_MAX_NUM_CTX * 6)
        with pytest.raises(local.OllamaReplyError, match="context window was too small"):
            llm_tasks.call_llm_json(_engine(), huge, fallback="{}")

    def test_token_counts_reach_usage_cb(self, task_chat):
        task_chat(_reply("{}", prompt_eval_count=123, eval_count=45))
        seen = []
        llm_tasks.call_llm_json(_engine(), "p", fallback="{}",
                                usage_cb=lambda i, o: seen.append((i, o)))
        assert seen == [(123, 45)]

    def test_translate_batch_records_last_usage(self, monkeypatch):
        monkeypatch.setattr(local, "_ollama_chat", _Chat(_reply('{"1": "Hello."}', prompt_eval_count=90,
                                                               eval_count=7)))
        engine = _engine()
        assert engine.translate_batch(["你好"], {}) == ["Hello."]
        assert engine.last_usage == {"input_tokens": 90, "output_tokens": 7}


class TestContextWindow:
    def test_window_is_a_coarse_step_and_stays_put_for_the_run(self, task_chat):
        fake = task_chat(_reply("{}"))
        engine = _engine()
        llm_tasks.call_llm_json(engine, "short", fallback="{}")
        llm_tasks.call_llm_json(engine, "y" * 30_000, fallback="{}")
        llm_tasks.call_llm_json(engine, "short again", fallback="{}")
        sizes = [b["options"]["num_ctx"] for b in fake.bodies]
        assert all(s in local.OLLAMA_NUM_CTX_STEPS for s in sizes)
        assert sizes[0] <= sizes[1] == sizes[2]

    def test_window_never_passes_the_ceiling(self, task_chat):
        fake = task_chat(lambda payload: _reply("{}", prompt_eval_count=10))
        with pytest.raises(local.OllamaReplyError):
            llm_tasks.call_llm_json(_engine(), "x" * 1_000_000, fallback="{}")
        assert fake.bodies[0]["options"]["num_ctx"] == local.OLLAMA_MAX_NUM_CTX

    def test_cjk_text_is_counted_closer_to_one_token_per_character(self):
        assert local.estimate_ollama_prompt_tokens("你" * 10_000) >= 10_000


class TestTimeouts:
    @pytest.mark.parametrize("model,slow", [
        ("gemma4:26b", True), ("gemma4:31b", True), ("qwen3:32b", True),
        ("qwen3:30b-a3b", True), ("hf.co/x/Qwen2.5-32B-Instruct-GGUF:Q4_K_M", True),
        ("gemma4:12b", False), ("qwen3:8b", False), ("gemma4:e4b", False), ("llama3.1", False),
        ("gemma4:31b-cloud", False),
    ])
    def test_timeout_follows_model_size(self, model, slow):
        expected = local.OLLAMA_SLOW_MODEL_CHAT_TIMEOUT if slow else local.OLLAMA_CHAT_TIMEOUT
        assert local.ollama_chat_timeout(model) == expected

    def test_a_cold_model_gets_time_to_load_on_its_first_request_only(self, monkeypatch):
        from tests.http_fakes import StreamedBody, patch_post
        timeouts = []

        class Ok(StreamedBody):
            def raise_for_status(self):
                pass

            def json(self):
                return _reply("{}")

        def fake_post(url, json=None, timeout=None, stream=None):
            timeouts.append(timeout)
            return Ok()
        patch_post(monkeypatch, fake_post)
        payload = {"model": "gemma4:12b", "options": {"num_ctx": 16384}}
        local._ollama_chat("http://localhost:11434", payload)
        local._ollama_chat("http://localhost:11434", payload)
        local._ollama_chat("http://localhost:11434", {**payload, "options": {"num_ctx": 32768}})
        base = local.OLLAMA_CHAT_TIMEOUT
        assert timeouts == [base + local.OLLAMA_COLD_LOAD_SECONDS, base,
                            base + local.OLLAMA_COLD_LOAD_SECONDS]

    def test_the_deadline_leaves_room_for_a_cold_load(self):
        scope = llm_tasks._BoundedScope("jo", lambda: False, None, False)
        assert llm_tasks._deadline_for(scope, _engine()) > (
            local.OLLAMA_CHAT_TIMEOUT + local.OLLAMA_COLD_LOAD_SECONDS)


# --- Part B: translation with a weak model ----------------------------------

@pytest.fixture
def batch_chat(monkeypatch):
    """The fake OllamaEngine.translate_batch talks to."""
    def install(*replies):
        fake = _Chat(*replies)
        monkeypatch.setattr(local, "_ollama_chat", fake)
        return fake
    return install


def _user_text(body):
    return body["messages"][-1]["content"]


_IDS = {"line_ids": [11, 12, 13]}
_SOURCES = ["你好", "谢谢你", "再见了"]


class TestCutOffReply:
    def test_complete_pairs_of_a_cut_off_reply_are_kept_and_only_the_rest_re_asked(self, batch_chat):
        fake = batch_chat(_reply('{"11": "Hello.", "12": "Thank you.", "13": "Good', done_reason="length"),
                          _reply('{"13": "Goodbye."}'))
        out = _engine().translate_batch(_SOURCES, dict(_IDS))
        assert out == ["Hello.", "Thank you.", "Goodbye."]
        retry = _user_text(fake.bodies[1])
        assert "13." in retry and "11." not in retry and "12." not in retry

    def test_other_engines_keep_the_complete_pairs_of_a_cut_off_reply(self):
        from engine_backends.shared import parse_id_keyed_json
        assert parse_id_keyed_json('{"11": "Hello.", "12": "Tha', [11, 12]) == {"11": "Hello."}


class TestLeakedDraft:
    def test_the_last_object_wins_over_a_reasoning_draft(self, batch_chat):
        batch_chat(_reply('Draft: {"11": "Hi.", "12": "Thanks.", "13": "Bye."}\n'
                          'Final: {"11": "Hello.", "12": "Thank you.", "13": "Goodbye."}'))
        assert _engine().translate_batch(_SOURCES, dict(_IDS)) == ["Hello.", "Thank you.", "Goodbye."]

    def test_a_channel_reasoning_block_is_never_the_translation(self, batch_chat):
        batch_chat(_reply('<|channel>thought\n{"11": "wrong", "12": "wrong", "13": "wrong"}<channel|>'
                          '{"11": "Hello.", "12": "Thank you.", "13": "Goodbye."}'))
        assert _engine().translate_batch(_SOURCES, dict(_IDS)) == ["Hello.", "Thank you.", "Goodbye."]

    def test_other_engines_keep_the_first_object(self):
        from engine_backends.shared import parse_id_keyed_json
        assert parse_id_keyed_json('{"1": "a"} {"1": "b"}', [1]) == {"1": "a"}


class TestValuesAreChecked:
    @pytest.mark.parametrize("bad", [
        "12. Thank you.",
        '["Thank you."]',
        "[Xiaoling] Thank you.",
        "谢谢你",
        "Okay, the user wants me to translate this line. " * 6,
    ])
    def test_a_bad_value_is_re_asked_then_left_untranslated(self, batch_chat, bad):
        fake = batch_chat(_reply('{"11": "Hello.", "12": %s, "13": "Goodbye."}' % _json(bad)))
        engine = _engine()
        out = engine.translate_batch(_SOURCES, dict(_IDS, speaker_labels=[None, "Xiaoling", None]))
        assert out == ["Hello.", "", "Goodbye."]
        assert len(fake.bodies) == 2 and "12." in _user_text(fake.bodies[1])
        assert set(engine.last_rejected) == {12}

    @pytest.mark.parametrize("line", ["I won't leave you.", "I can't do this anymore.",
                                      "I cannot lose you.", "[Sighs] Fine."])
    def test_dialogue_that_only_sounds_like_a_refusal_is_written(self, batch_chat, line):
        fake = batch_chat(_reply('{"11": "Hello.", "12": %s, "13": "Goodbye."}' % _json(line)))
        engine = _engine()
        assert engine.translate_batch(["你好", "我不会离开你", "再见了"], dict(_IDS))[1] == line
        assert len(fake.bodies) == 1 and engine.last_rejected == {}

    def test_a_whole_reply_refusal_still_reads_as_one(self, batch_chat):
        from engine_backends.shared import ContentModerationBlocked
        batch_chat(_reply("I'm sorry, but I can't help with that."))
        with pytest.raises(ContentModerationBlocked):
            _engine().translate_batch(_SOURCES, dict(_IDS))

    def test_rejections_from_a_later_call_do_not_erase_earlier_ones(self, batch_chat):
        batch_chat(_reply('{"11": "Hello.", "12": "谢谢你", "13": "Goodbye."}'),
                   _reply('{"12": "谢谢你"}'), _reply('{"13": "Bye."}'))
        engine = _engine()
        engine.translate_batch(_SOURCES, dict(_IDS))
        assert set(engine.last_rejected) == {12}
        engine.translate_batch(["再见了"], {"line_ids": [13]})
        assert set(engine.last_rejected) == {12}

    def test_reply_guard_off_skips_the_value_checks(self, batch_chat):
        fake = batch_chat(_reply('{"11": "Hello.", "12": "谢谢你", "13": "Goodbye."}'))
        out = _engine().translate_batch(_SOURCES, dict(_IDS, check_replies=False))
        assert out == ["Hello.", "谢谢你", "Goodbye."] and len(fake.bodies) == 1

    def test_the_run_passes_reply_guard_off_to_the_engine(self, monkeypatch):
        from core import Line
        from engine_backends import translate_pipeline
        monkeypatch.setattr(local, "_ollama_chat", _Chat(_reply('{"11": "谢谢你"}')))
        line = Line(idx=0, start=0, end=1, zh="谢谢你", en="")
        line.id = 11
        out, _ = translate_pipeline.translate_lines_with_engine(
            [line], _engine(), drama_meta={}, check_replies=False)
        assert out[0].en == "谢谢你"

    def test_a_bad_value_that_comes_back_good_is_written(self, batch_chat):
        batch_chat(_reply('{"11": "Hello.", "12": "谢谢你", "13": "Goodbye."}'),
                   _reply('{"12": "Thank you."}'))
        engine = _engine()
        assert engine.translate_batch(_SOURCES, dict(_IDS)) == ["Hello.", "Thank you.", "Goodbye."]
        assert engine.last_rejected == {}

    def test_a_glossary_term_kept_in_source_script_is_allowed(self, batch_chat):
        batch_chat(_reply('{"11": "Hello, 白鹤.", "12": "Thank you.", "13": "Goodbye."}'))
        ctx = dict(_IDS, glossary_terms=[{"term_original": "白鹤", "term_translation": "白鹤"}])
        assert _engine().translate_batch(_SOURCES, ctx)[0] == "Hello, 白鹤."

    def test_the_run_flags_a_line_whose_reply_was_unusable(self, monkeypatch):
        from core import Line
        from engine_backends import translate_pipeline
        monkeypatch.setattr(local, "_ollama_chat", _Chat(
            _reply('{"11": "Hello.", "12": "谢谢你"}')))
        lines = [Line(idx=0, start=0, end=1, zh="你好", en=""),
                 Line(idx=1, start=1, end=2, zh="谢谢你", en="")]
        lines[0].id, lines[1].id = 11, 12
        out, errors = translate_pipeline.translate_lines_with_engine(
            lines, _engine(), drama_meta={})
        assert [ln.en for ln in out] == ["Hello.", ""]
        assert out[1].flag == "uncertain_translation" and "source text" in out[1].flag_note
        assert errors


class TestPerBatchSchema:
    def test_the_schema_requires_exactly_this_batchs_ids(self, batch_chat):
        fake = batch_chat(_reply('{"11": "Hello.", "12": "Thanks."}'), _reply('{"13": "Bye."}'))
        _engine().translate_batch(_SOURCES, dict(_IDS))
        first, retry = fake.bodies[0]["format"], fake.bodies[1]["format"]
        assert first["required"] == ["11", "12", "13"] and first["additionalProperties"] is False
        assert set(first["properties"]) == {"11", "12", "13"}
        assert retry["required"] == ["13"]

    def test_a_server_that_rejects_the_schema_gets_plain_json(self, batch_chat):
        resp = requests.Response()
        resp.status_code = 400
        resp._content = b'{"error": "invalid JSON schema in format"}'
        err = requests.HTTPError("400", response=resp)
        err.body_text = resp._content.decode()
        fake = batch_chat(err, _reply('{"11": "Hello.", "12": "Thanks.", "13": "Bye."}'))
        engine = _engine()
        assert engine.translate_batch(_SOURCES, dict(_IDS)) == ["Hello.", "Thanks.", "Bye."]
        assert fake.bodies[1]["format"] == "json"
        engine.translate_batch(_SOURCES, dict(_IDS))
        assert fake.bodies[2]["format"] == "json"  # remembered

    def test_an_unrelated_400_mentioning_format_keeps_the_schema(self, batch_chat):
        resp = requests.Response()
        resp.status_code = 400
        resp._content = b'{"error": "invalid date format in keep_alive"}'
        err = requests.HTTPError("400", response=resp)
        err.body_text = resp._content.decode()
        batch_chat(err, _reply('{"11": "Hello.", "12": "Thanks.", "13": "Bye."}'))
        engine = _engine()
        with pytest.raises(requests.HTTPError):
            engine.translate_batch(_SOURCES, dict(_IDS))
        assert (engine.base_url, engine.model) not in local._schema_refused


class TestServerErrors:
    def _post_500(self, monkeypatch, body):
        from tests.http_fakes import StreamedBody, patch_post

        class Err(StreamedBody):
            status_code = 500

            def raise_for_status(self):
                raise requests.HTTPError(
                    "500 Server Error: Internal Server Error for url: http://192.168.7.9:11434/api/chat",
                    response=self)

            def iter_content(self, size):
                yield body

        patch_post(monkeypatch, lambda *a, **k: Err())

    def test_ollamas_own_message_is_kept_and_the_url_is_not(self, monkeypatch):
        self._post_500(monkeypatch, b'{"error": "CUDA error: out of memory"}')
        engine = local.OllamaEngine(base_url="http://192.168.7.9:11434")
        with pytest.raises(requests.HTTPError) as info:
            engine.translate_batch(["你好"], {})
        text = str(info.value)
        assert "out of memory" in text and "500" in text
        assert "192.168" not in text and "http" not in text and "11434" not in text

    @pytest.mark.parametrize("path", [
        r"C:\Users\mai.tran\.ollama\models\blobs\sha256-abc",
        r"\\nas\share\mai.tran\models\blob",
        "/home/mai.tran/.ollama/models/blobs/sha256-abc",
        "/Users/mai.tran/.ollama/models/blobs/sha256-abc",
    ])
    def test_filesystem_paths_never_reach_the_message(self, monkeypatch, path):
        import json
        self._post_500(monkeypatch, json.dumps({"error": f"open {path}: no such file"}).encode())
        engine = local.OllamaEngine()
        for _ in range(local.OllamaEngine.consecutive_failure_limit - 1):
            with pytest.raises(requests.HTTPError) as info:
                engine.translate_batch(["你好"], {})
            assert "mai.tran" not in str(info.value) and ": no such file" in str(info.value)
        with pytest.raises(local.OllamaRunStopped) as stopped:
            engine.translate_batch(["你好"], {})
        assert "mai.tran" not in str(stopped.value)

    def test_the_run_stops_after_repeated_engine_failures_and_keeps_its_work(self, monkeypatch):
        from core import Line
        from engine_backends import translate_pipeline
        calls = []

        def chat(base_url, payload):
            calls.append(payload)
            if len(calls) == 1:
                return _reply('{"1": "Hello."}')
            resp = requests.Response()
            resp.status_code = 500
            raise requests.HTTPError("Ollama answered with an error (HTTP 500): model does not fit",
                                     response=resp)
        monkeypatch.setattr(local, "_ollama_chat", chat)
        monkeypatch.setattr(translate_pipeline, "call_with_backoff", lambda fn, **kw: fn())
        lines = [Line(idx=i, start=i, end=i + 1, zh="你好", en="") for i in range(8)]
        saved = []
        out, errors = translate_pipeline.translate_lines_with_engine(
            lines, _engine(), drama_meta={}, batch_size=1, check_replies=False,
            save_cb=lambda ls: saved.append([ln.en for ln in ls]))
        assert out[0].en == "Hello."
        limit = local.OllamaEngine.consecutive_failure_limit
        assert len(calls) == 1 + limit
        assert saved[-1][0] == "Hello."
        assert "in a row" in errors[-1]["error"] and "does not fit" in errors[-1]["error"]

    def test_rate_limits_are_left_to_the_backoff_and_not_counted(self, batch_chat):
        cap = local.OllamaCloudLimitError("weekly limit")
        batch_chat(cap)
        engine = local.OllamaEngine(model="gemma4:31b-cloud")
        for _ in range(local.OllamaEngine.consecutive_failure_limit + 1):
            with pytest.raises(local.OllamaCloudLimitError):
                engine.translate_batch(["你好"], {})

    def test_a_success_resets_the_count(self, batch_chat):
        err = requests.HTTPError("Ollama answered with an error (HTTP 500): boom",
                                 response=requests.Response())
        batch_chat(err, err, _reply('{"1": "Hello."}'), err, err, _reply('{"1": "Hello."}'))
        engine = _engine()
        for _ in range(2):
            for _ in range(2):
                with pytest.raises(requests.HTTPError):
                    engine.translate_batch(["你好"], {})
            assert engine.translate_batch(["你好"], {}) == ["Hello."]


def _json(text):
    import json
    return json.dumps(text, ensure_ascii=False)
