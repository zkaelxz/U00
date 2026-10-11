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
    yield
    thinking._think_refused.clear()
    getattr(local, "_warm_until", {}).clear()


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
