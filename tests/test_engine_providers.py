"""Tests for the provider engines (Gemini, DeepSeek, Ollama, chat-API replies) and how the registry builds them."""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from engine_backends import (
    translate_pipeline,
    openai_compat,
    llm_tasks,
    engine_registry,
)
from engine_backends import shared as engine_shared
from engine_backends import gemini as engine_gemini
from engine_backends import pricing as engine_pricing
from engine_backends import local as engine_local
from core import Line
from lib import http
from tests.http_fakes import StreamedBody, patch_post


class TestGeminiEngine:
    def test_translate_batch_parses_response_and_records_usage(self, monkeypatch):
        captured = {}

        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {
                    "candidates": [{"content": {"parts": [
                        {"text": '{"1": "Hello.", "2": "Goodbye."}'}]}}],
                    "usageMetadata": {"promptTokenCount": 42, "candidatesTokenCount": 8},
                }

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured["url"] = url
            captured["headers"] = headers
            captured["json"] = json
            return FakeResponse()

        patch_post(monkeypatch, fake_post)
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest")
        result = engine.translate_batch(["你好", "再见"], {})

        assert result == ["Hello.", "Goodbye."]
        assert engine.last_usage == {"input_tokens": 42, "output_tokens": 8,
                                     "cache_read_tokens": 0, "cache_write_tokens": 0}
        assert "gemini-flash-lite-latest" in captured["url"]
        # Key goes in a header, never the URL/query string -- a
        # raise_for_status() failure's message includes the URL, and that
        # message can end up stored/shown; a key in params would leak.
        assert captured["headers"] == {"x-goog-api-key": "fake-key"}
        assert "key=" not in captured["url"]
        assert "systemInstruction" in captured["json"]

    def test_recent_context_reaches_the_user_message_not_the_system_instruction(self, monkeypatch):
        captured = {}

        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]}

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured["json"] = json
            return FakeResponse()

        patch_post(monkeypatch, fake_post)
        engine = engine_gemini.GeminiEngine("fake-key")
        engine.translate_batch(["x"], {"recent_context": [("她来了", "She came.")]})

        sys_text = captured["json"]["systemInstruction"]["parts"][0]["text"]
        user_text = captured["json"]["contents"][0]["parts"][0]["text"]
        assert "她来了 -> She came." in user_text
        assert "她来了" not in sys_text

    def test_speaker_labels_reach_the_actual_numbered_lines_sent(self, monkeypatch):
        captured = {}

        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": '{"1": "Hi."}'}]}}]}

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured["json"] = json
            return FakeResponse()

        patch_post(monkeypatch, fake_post)
        engine = engine_gemini.GeminiEngine("fake-key")
        result = engine.translate_batch(["你好"], {"speaker_labels": ["Xiaoling"]})

        user_text = captured["json"]["contents"][0]["parts"][0]["text"]
        assert "[Xiaoling] 你好" in user_text
        assert result == ["Hi."]

    def test_pronouns_in_the_speaker_label_reach_the_exact_line(self, monkeypatch):
        """Step 1e: translate_lines_with_engine's character_names values
        now carry pronouns (see translation_guide.build_speaker_labels),
        so the translator sees them on the line itself."""
        captured = {}

        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": '{"1": "Hi."}'}]}}]}

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured["json"] = json
            return FakeResponse()

        patch_post(monkeypatch, fake_post)
        lines = [Line(idx=0, start=0, end=1, zh="你好", speaker="SPEAKER_00")]
        translate_pipeline.translate_lines_with_engine(
            lines, engine_gemini.GeminiEngine("fake-key"), drama_meta={},
            character_names={"SPEAKER_00": "Xiaoling (they/them)"})

        user_text = captured["json"]["contents"][0]["parts"][0]["text"]
        assert "[Xiaoling (they/them)] 你好" in user_text

    def test_a_response_missing_one_id_is_retried_before_giving_up(self, monkeypatch):
        call_count = {"n": 0}

        class FakeResponse(StreamedBody):
            def __init__(self, text):
                self._text = text
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": self._text}]}}]}

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            call_count["n"] += 1
            if call_count["n"] == 1:
                return FakeResponse('{"1": "First."}')  # line 2 missing
            return FakeResponse('{"2": "Second."}')  # retry recovers it

        patch_post(monkeypatch, fake_post)
        engine = engine_gemini.GeminiEngine("fake-key")
        result = engine.translate_batch(["a", "b"], {})

        assert result == ["First.", "Second."]
        assert call_count["n"] == 2

    def test_missing_usage_metadata_does_not_crash(self, monkeypatch):
        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "[]"}]}}]}
                # No usageMetadata key at all.

        patch_post(monkeypatch, lambda *a, **k: FakeResponse())
        engine = engine_gemini.GeminiEngine("fake-key")
        engine.translate_batch(["x"], {})
        assert engine.last_usage == {"input_tokens": 0, "output_tokens": 0,
                                     "cache_read_tokens": 0, "cache_write_tokens": 0}

    def test_empty_candidates_with_prompt_feedback_raises_content_moderation_blocked(
            self, monkeypatch):
        """Step 31 item 1, shape one: blocked before generation even
        started -- no `candidates` key at all, the real reason sitting in
        `promptFeedback.blockReason` instead. Used to raise a bare
        IndexError from the old `data["candidates"][0]...` indexing."""
        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"promptFeedback": {"blockReason": "SAFETY"}}

        monkeypatch.setattr("time.sleep", lambda *_: None)
        patch_post(monkeypatch, lambda *a, **k: FakeResponse())
        engine = engine_gemini.GeminiEngine("fake-key")
        with pytest.raises(engine_shared.ContentModerationBlocked) as exc_info:
            engine.translate_batch(["a graphic passage"], {})
        assert exc_info.value.engine == "gemini"
        assert exc_info.value.reason == "SAFETY"

    def test_safety_finish_reason_with_no_content_raises_content_moderation_blocked(
            self, monkeypatch):
        """Step 31 item 1, shape two: a candidate came back, but with
        finishReason SAFETY/PROHIBITED_CONTENT and no `content` key --
        used to raise a bare KeyError from `candidates[0]["content"]...`."""
        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"finishReason": "PROHIBITED_CONTENT"}]}

        monkeypatch.setattr("time.sleep", lambda *_: None)
        patch_post(monkeypatch, lambda *a, **k: FakeResponse())
        engine = engine_gemini.GeminiEngine("fake-key")
        with pytest.raises(engine_shared.ContentModerationBlocked) as exc_info:
            engine.translate_batch(["a graphic passage"], {})
        assert exc_info.value.engine == "gemini"
        assert exc_info.value.reason == "PROHIBITED_CONTENT"


class TestGeminiFreeTierThrottle:
    """Step 1d item 4 added RPM-only throttling; Step 1f extends it to all
    three real limits Google actually enforces -- RPM and RPD are
    per-model (Flash: 10/250, Flash-Lite: 15/1000), TPM (250k) is shared
    across every model. GeminiEngine paces itself client-side against
    whichever ceiling is closest. Uses a fake monotonic clock so a test
    doesn't actually take a minute (or a day) to run -- only the
    *decision* to wait, and for how long, is under test, not real
    wall-clock sleeping."""

    def _fake_clock(self, monkeypatch):
        state = {"now": 0.0, "slept": []}

        def fake_monotonic():
            return state["now"]

        def fake_sleep(seconds):
            state["slept"].append(seconds)
            state["now"] += seconds

        monkeypatch.setattr("time.monotonic", fake_monotonic)
        monkeypatch.setattr("time.sleep", fake_sleep)
        return state

    def test_a_paid_key_is_never_throttled(self, monkeypatch):
        state = self._fake_clock(monkeypatch)
        engine = engine_gemini.GeminiEngine("fake-key", free_tier=False)
        for _ in range(30):
            engine._throttle_for_free_tier()
        assert state["slept"] == []

    def test_flash_and_flash_lite_have_different_real_limits(self):
        assert engine_gemini.gemini_free_tier_limits_for("gemini-flash-latest") == {"rpm": 10, "rpd": 250}
        assert engine_gemini.gemini_free_tier_limits_for("gemini-flash-lite-latest") == {"rpm": 15, "rpd": 1000}
        assert engine_gemini.gemini_free_tier_limits_for("gemini-3.1-flash-lite") == {"rpm": 15, "rpd": 1000}

    def test_free_tier_stays_at_or_under_the_per_minute_limit(self, monkeypatch):
        state = self._fake_clock(monkeypatch)
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        limit = engine_gemini.gemini_free_tier_limits_for(engine.model)["rpm"]
        for _ in range(limit * 3):
            engine._throttle_for_free_tier()
            state["now"] += 0.01  # each call takes negligible real time

        # However long that took, no trailing 60s window (measured from any
        # actual request) contains more than the limit.
        times = sorted(engine._free_tier_request_times)
        for t in times:
            in_window = sum(1 for other in times if t - 60 < other <= t)
            assert in_window <= limit, (
                f"{in_window} requests fell within 60s of t={t:.2f} -- over the {limit} rpm limit")

    def test_the_request_past_the_per_minute_limit_waits(self, monkeypatch):
        state = self._fake_clock(monkeypatch)
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        limit = engine_gemini.gemini_free_tier_limits_for(engine.model)["rpm"]
        for _ in range(limit):
            engine._throttle_for_free_tier()
        assert state["slept"] == []  # all within the same instant: no wait needed

        engine._throttle_for_free_tier()
        assert state["slept"] == [60.0]  # the next one has to wait out the window

    def test_rpd_paces_a_job_even_well_under_the_per_minute_limit(self, monkeypatch):
        """RPD is a real, separate ceiling from RPM -- a job well under its
        per-minute limit can still have used up its whole day's budget."""
        state = self._fake_clock(monkeypatch)
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        limit = engine_gemini.gemini_free_tier_limits_for(engine.model)["rpd"]
        engine._free_tier_daily_request_times = [0.0] * limit

        # Waiting out the daily window would take hours: stop with a clear
        # message instead of sleeping.
        with pytest.raises(engine_shared.FreeTierDailyLimitReached, match="daily request limit"):
            engine._throttle_for_free_tier()
        assert state["slept"] == []

    def test_tpm_paces_a_job_when_past_requests_used_the_shared_budget(self, monkeypatch):
        state = self._fake_clock(monkeypatch)
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine._free_tier_token_counts = [(0.0, engine_gemini.GEMINI_FREE_TIER_TPM)]

        engine._throttle_for_free_tier()
        assert state["slept"] == [60.0]

    def test_tpm_does_not_pace_a_job_under_the_shared_budget(self, monkeypatch):
        state = self._fake_clock(monkeypatch)
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine._free_tier_token_counts = [(0.0, 10)]

        engine._throttle_for_free_tier()
        assert state["slept"] == []


class TestGeminiRateStatus:
    """Step 1f: a visible rate-status indicator -- real header-reported
    quota when Google's response includes it, self-tracked RPM/RPD/TPM
    against the static table otherwise."""

    def _post_with_headers(self, monkeypatch, headers):
        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "{\"1\": \"Hi.\"}"}]}}],
                        "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}

        resp = FakeResponse()
        resp.headers = headers

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            return resp

        patch_post(monkeypatch, fake_post)

    def test_reads_real_header_values_when_present(self, monkeypatch):
        self._post_with_headers(monkeypatch, {
            "x-ratelimit-limit-requests": "15",
            "x-ratelimit-remaining-requests": "14",
            "x-ratelimit-remaining-tokens": "249990",
            "x-ratelimit-reset-requests": "4s",
        })
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine.translate_batch(["你好"], {})

        assert engine.rate_status["source"] == "header"
        assert engine.rate_status["limit_requests"] == "15"
        assert engine.rate_status["remaining_requests"] == "14"
        assert engine.rate_status["remaining_tokens"] == "249990"

    def test_falls_back_to_the_self_tracked_counter_when_headers_are_absent(self, monkeypatch):
        self._post_with_headers(monkeypatch, {})
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine.translate_batch(["你好"], {})

        assert engine.rate_status["source"] == "estimated"
        assert engine.rate_status["rpm_used"] == 1
        assert engine.rate_status["rpm_limit"] == 15
        assert engine.rate_status["rpd_limit"] == 1000
        assert engine.rate_status["tpm_used"] == 15  # 10 input + 5 output
        assert engine.rate_status["tpm_limit"] == engine_gemini.GEMINI_FREE_TIER_TPM

    def test_no_status_yet_for_a_paid_engine_that_never_throttles(self, monkeypatch):
        self._post_with_headers(monkeypatch, {})
        engine = engine_gemini.GeminiEngine("fake-key", free_tier=False)
        engine.translate_batch(["你好"], {})
        assert engine.rate_status is None

    def test_rate_status_text_is_none_before_any_request(self):
        engine = engine_gemini.GeminiEngine("fake-key", free_tier=True)
        assert engine_gemini.gemini_rate_status_text(engine) is None

    def test_rate_status_text_shows_all_three_dimensions(self, monkeypatch):
        self._post_with_headers(monkeypatch, {})
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine.translate_batch(["你好"], {})

        text = engine_gemini.gemini_rate_status_text(engine)
        assert "1/15" in text
        assert "1/1000" in text
        assert "15/250,000" in text

    def test_progress_message_appends_rate_status_for_free_tier_gemini(self, monkeypatch):
        self._post_with_headers(monkeypatch, {})
        engine = engine_gemini.GeminiEngine("fake-key", model="gemini-flash-lite-latest", free_tier=True)
        engine.translate_batch(["你好"], {})

        message = engine_gemini.progress_message_with_rate_status(engine, 0.5)
        assert message.startswith("Translating... 50%")
        assert "req/min" in message

    def test_progress_message_is_plain_for_a_non_free_tier_engine(self):
        engine = engine_gemini.GeminiEngine("fake-key", free_tier=False)
        assert engine_gemini.progress_message_with_rate_status(engine, 0.5) == "Translating... 50%"


class TestGeminiProGoneFromFreeTier:
    """Step 1f item 4: Gemini Pro was removed from the free tier entirely
    in April 2026 -- a free-tier key can no longer reach it at all."""

    def test_pro_is_the_flagged_unavailable_model(self):
        assert "gemini-pro-latest" in engine_gemini.GEMINI_FREE_TIER_UNAVAILABLE_MODELS

    def test_flash_and_flash_lite_are_not_flagged(self):
        assert "gemini-flash-latest" not in engine_gemini.GEMINI_FREE_TIER_UNAVAILABLE_MODELS
        assert "gemini-flash-lite-latest" not in engine_gemini.GEMINI_FREE_TIER_UNAVAILABLE_MODELS


def _deepseek_engine_with_fake_client(message_content, refusal=None):
    """Builds a real DeepSeekEngine without going through __init__ (which
    imports the `openai` package -- not installed in this core-only test
    environment, same reasoning as _claude_engine_with_fake_client above
    not needing one for `anthropic`, which IS installed here)."""
    engine = openai_compat.DeepSeekEngine.__new__(openai_compat.DeepSeekEngine)
    engine.model = "deepseek-fake"
    engine.last_usage = engine_shared._empty_usage()

    message = type("Message", (), {"content": message_content, "refusal": refusal})()
    choice = type("Choice", (), {"message": message})()
    resp = type("Resp", (), {"choices": [choice], "usage": None})()

    class _Completions:
        def create(self, **kwargs):
            return resp
    engine.client = type("Client", (), {"chat": type("Chat", (), {"completions": _Completions()})()})()
    return engine


class TestDeepSeekEngine:
    def test_null_content_with_refusal_raises_content_moderation_blocked(self, monkeypatch):
        """Step 31 item 1: the OpenAI-compatible refusal shape -- content
        is None/empty and a separate `refusal` field explains why. Used
        to raise a bare AttributeError from the old `.content.strip()`
        (None has no .strip())."""
        monkeypatch.setattr("time.sleep", lambda *_: None)
        engine = _deepseek_engine_with_fake_client(
            message_content=None, refusal="This request violates our usage policies.")
        with pytest.raises(engine_shared.ContentModerationBlocked) as exc_info:
            engine.translate_batch(["a graphic passage"], {})
        assert exc_info.value.engine == "deepseek"
        assert exc_info.value.reason == "This request violates our usage policies."

    def test_ordinary_response_still_translates_normally(self):
        engine = _deepseek_engine_with_fake_client(message_content='{"1": "Hello."}')
        result = engine.translate_batch(["你好"], {})
        assert result == ["Hello."]


class TestOllamaEngine:
    """Regression coverage for a real gap: Ollama's own default context
    window can be as small as 2-4k tokens, and a prompt longer than it
    gets silently TRUNCATED FROM THE START (system instructions/glossary/
    reference novel) with no error at all. translate_batch now always
    sends an explicit options.num_ctx sized from the actual prompt, with
    a floor, plus a `format` JSON schema pairing with the id-keyed
    parsing from Step 1."""

    def _fake_response(self, captured, text='{"1": "Hello."}'):
        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": text}}

        def fake_post(url, json=None, timeout=None, stream=None):
            captured["json"] = json
            captured["timeout"] = timeout
            return FakeResponse()
        return fake_post

    def test_sends_a_format_json_schema_for_structured_output(self, monkeypatch):
        captured = {}
        patch_post(monkeypatch, self._fake_response(captured))
        engine = engine_local.OllamaEngine()
        engine.translate_batch(["你好"], {})
        from engine_backends.local import id_keyed_schema
        assert captured["json"]["format"] == id_keyed_schema([1])

    def test_num_ctx_is_never_below_the_floor_for_a_short_prompt(self, monkeypatch):
        captured = {}
        patch_post(monkeypatch, self._fake_response(captured))
        engine = engine_local.OllamaEngine()
        engine.translate_batch(["你好"], {})
        assert captured["json"]["options"]["num_ctx"] >= engine_local.OLLAMA_MIN_NUM_CTX

    def test_num_ctx_grows_with_a_much_longer_prompt(self, monkeypatch):
        captured = {}
        patch_post(monkeypatch, self._fake_response(captured))
        engine = engine_local.OllamaEngine()
        long_glossary = [{"term_original": "x" * 200, "term_translation": "y"} for _ in range(200)]
        engine.translate_batch(["你好"], {"glossary_terms": long_glossary})
        assert captured["json"]["options"]["num_ctx"] > engine_local.OLLAMA_MIN_NUM_CTX

    def test_override_can_raise_num_ctx_above_the_estimate(self, monkeypatch):
        captured = {}
        patch_post(monkeypatch, self._fake_response(captured))
        engine = engine_local.OllamaEngine()
        engine.translate_batch(["你好"], {"ollama_num_ctx_override": 100_000})
        assert captured["json"]["options"]["num_ctx"] == 100_000

    def test_override_below_the_estimate_is_not_used(self, monkeypatch):
        """A manual override smaller than what the prompt actually needs
        would silently reintroduce the exact truncation bug this exists
        to prevent -- the larger of the two must always win."""
        captured = {}
        patch_post(monkeypatch, self._fake_response(captured))
        engine = engine_local.OllamaEngine()
        engine.translate_batch(["你好"], {"ollama_num_ctx_override": 1})
        assert captured["json"]["options"]["num_ctx"] >= engine_local.OLLAMA_MIN_NUM_CTX

    def test_request_has_a_timeout(self, monkeypatch):
        captured = {}
        patch_post(monkeypatch, self._fake_response(captured))
        engine = engine_local.OllamaEngine()
        engine.translate_batch(["你好"], {})
        assert captured["timeout"] is not None


class TestOllamaUnavailableErrors:
    def test_translate_batch_and_call_llm_json_share_the_mapping(self, monkeypatch):
        import requests

        def refuse(url, json=None, timeout=None, stream=None):
            raise requests.ConnectionError(f"refused {url}")
        patch_post(monkeypatch, refuse)
        engine = engine_local.OllamaEngine(base_url="http://10.1.2.3:11434")
        with pytest.raises(engine_local.OllamaUnavailableError) as batch:
            engine.translate_batch(["你好"], {})
        with pytest.raises(engine_local.OllamaUnavailableError) as free_form:
            llm_tasks.call_llm_json(engine, "hi")
        for info in (batch, free_form):
            assert info.value.reason == "ollama_unreachable"
            assert "10.1.2.3" not in str(info.value)


    def test_a_stalled_or_reset_body_read_is_mapped_too(self, monkeypatch):
        # The body is read after post() returns, so a drop there raises from
        # the read; requests' text for it names the host.
        import requests

        class Dropped:
            status_code = 200
            ok = True
            headers = {}
            closed = False

            def raise_for_status(self):
                pass

            def iter_content(self, size):
                raise requests.ConnectionError(
                    "HTTPConnectionPool(host='192.168.7.9', port=11434): Read timed out.")
                yield b""

            def close(self):
                self.closed = True

        resp = Dropped()
        patch_post(monkeypatch, lambda *a, **k: resp)
        engine = engine_local.OllamaEngine(base_url="http://192.168.7.9:11434")
        with pytest.raises(engine_local.OllamaUnavailableError) as info:
            engine.translate_batch(["你好"], {})
        assert info.value.reason == "ollama_unreachable"
        assert "192.168" not in str(info.value)
        assert resp.closed


class TestOllamaModelInstalledCheck:
    class Resp:
        def __init__(self, status, body=b'{"models": [{"name": "m:1b"}]}', headers=None):
            self.status_code, self.body, self.headers = status, body, headers or {}
        def iter_content(self, size):
            return iter([self.body])
        def close(self):
            pass

    def _serve(self, monkeypatch, *replies):
        calls, queue = [], list(replies)
        monkeypatch.setattr(http, "pinned_get",
                            lambda url, *a, **kw: calls.append(url) or queue.pop(0))
        return calls

    def test_follows_a_308_to_the_model_list(self, monkeypatch):
        calls = self._serve(monkeypatch,
                            self.Resp(308, headers={"Location": "http://proxy.local/api/tags"}),
                            self.Resp(200))
        engine_local.check_ollama_model_installed("http://localhost:11434", "m:1b")
        assert calls[-1] == "http://proxy.local/api/tags"

    # 300 and 304 are redirect-class statuses lib/http does not follow, so they
    # reach the status check (a 301 with no Location never does).
    @pytest.mark.parametrize("status", [300, 304, 404, 500])
    def test_a_non_2xx_final_answer_means_unreachable(self, monkeypatch, status):
        self._serve(monkeypatch, self.Resp(status))
        with pytest.raises(engine_local.OllamaUnavailableError) as exc:
            engine_local.check_ollama_model_installed("http://localhost:11434", "m:1b")
        assert exc.value.reason == "ollama_unreachable"

    def test_an_unsettled_redirect_means_unreachable(self, monkeypatch):
        loop = self.Resp(308, headers={"Location": "http://localhost:11434/api/tags"})
        monkeypatch.setattr(http, "pinned_get", lambda *a, **kw: loop)
        with pytest.raises(engine_local.OllamaUnavailableError):
            engine_local.check_ollama_model_installed("http://localhost:11434", "m:1b")


class TestOllamaReachability:
    """Regression coverage for a real gap: Ollama is exempted from the
    API-key check entirely (workspace_tab.py's _needs_key), with nothing
    in its place -- clicking Translate against a stopped local server
    used to start a background job that only failed once
    translate_batch's own 300s timeout expired. check_ollama_reachable()
    is a cheap up-front health check the UI uses to disable that button
    instead."""

    def setup_method(self):
        engine_local._ollama_reachability_cache.clear()

    def _fake_get(self, ok=True, raises=None):
        captured = {}

        def fake_get(url, ip=None, headers=None, timeout=None, method="GET", **kw):
            captured["url"] = url
            captured["timeout"] = timeout
            captured["ip"] = ip
            if raises:
                raise raises
            return type("Resp", (), {"status_code": 200 if ok else 503, "headers": {},
                                     "iter_content": lambda self, size: iter([b"x"]),
                                     "close": lambda self: None})()
        return fake_get, captured

    def test_true_when_the_server_responds_ok(self, monkeypatch):
        fake_get, captured = self._fake_get(ok=True)
        monkeypatch.setattr(http, "pinned_get", fake_get)
        assert engine_local.check_ollama_reachable("http://localhost:11434") is True
        assert captured["url"] == "http://localhost:11434/api/tags"
        assert captured["timeout"] is not None
        assert captured["ip"] is None  # the user's own Ollama address: never the public guard

    def test_false_when_the_server_responds_with_an_error_status(self, monkeypatch):
        fake_get, _ = self._fake_get(ok=False)
        monkeypatch.setattr(http, "pinned_get", fake_get)
        assert engine_local.check_ollama_reachable("http://localhost:11434") is False

    def test_follows_a_redirect_like_the_chat_call_does(self, monkeypatch):
        calls = []

        class Resp:
            def __init__(self, status, headers=None):
                self.status_code, self.headers = status, headers or {}
            def iter_content(self, size):
                return iter([b"x"])
            def close(self):
                pass
        replies = [Resp(308, {"Location": "http://proxy.local/api/tags"}), Resp(200)]
        monkeypatch.setattr(http, "pinned_get", lambda url, *a, **kw: calls.append(url) or replies.pop(0))
        assert engine_local.check_ollama_reachable("http://localhost:11434") is True
        assert calls == ["http://localhost:11434/api/tags", "http://proxy.local/api/tags"]

    def test_false_when_a_redirect_never_settles(self, monkeypatch):
        class Loop:
            status_code = 308
            headers = {"Location": "http://localhost:11434/api/tags"}
            def iter_content(self, size):
                return iter([])
            def close(self):
                pass
        monkeypatch.setattr(http, "pinned_get", lambda *a, **kw: Loop())
        assert engine_local.check_ollama_reachable("http://localhost:11434") is False

    def test_false_when_the_connection_fails(self, monkeypatch):
        fake_get, _ = self._fake_get(raises=ConnectionError("refused"))
        monkeypatch.setattr(http, "pinned_get", fake_get)
        assert engine_local.check_ollama_reachable("http://localhost:11434") is False

    def test_result_is_cached_briefly_not_rechecked_every_call(self, monkeypatch):
        fake_get, _ = self._fake_get(ok=True)
        calls = {"n": 0}
        def counting_get(url, ip, headers, timeout=None, method="GET", **kw):
            calls["n"] += 1
            return fake_get(url, ip, headers, timeout=timeout)
        monkeypatch.setattr(http, "pinned_get", counting_get)

        engine_local.check_ollama_reachable("http://localhost:11434")
        engine_local.check_ollama_reachable("http://localhost:11434")
        engine_local.check_ollama_reachable("http://localhost:11434")

        assert calls["n"] == 1

    def test_a_different_base_url_is_cached_separately(self, monkeypatch):
        monkeypatch.setattr(http, "pinned_get", self._fake_get(ok=True)[0])
        engine_local.check_ollama_reachable("http://localhost:11434")
        # A second, different URL must still be checked fresh, not
        # short-circuited by the first URL's cache entry.
        fake_get_down, _ = self._fake_get(ok=False)
        monkeypatch.setattr(http, "pinned_get", fake_get_down)
        assert engine_local.check_ollama_reachable("http://otherhost:9999") is False

    def test_trailing_slash_in_base_url_is_normalized(self, monkeypatch):
        fake_get, captured = self._fake_get(ok=True)
        monkeypatch.setattr(http, "pinned_get", fake_get)
        engine_local.check_ollama_reachable("http://localhost:11434/")
        assert captured["url"] == "http://localhost:11434/api/tags"


class TestNLLBRemoved:
    """NLLB-200 was removed: the name stays only so saved data that names it
    is refused with the removed-engine message instead of "Unknown engine"."""

    def test_is_no_longer_registered_or_exported(self):
        assert "nllb" not in engine_registry.ENGINES
        assert "nllb" not in engine_registry.ENGINE_NOTES
        assert "nllb" not in engine_registry.ENGINE_CAPABILITIES
        assert "nllb" not in engine_registry.FREE_ENGINES | engine_registry.KEYLESS_ENGINES
        for gone in ("NLLBEngine", "NLLB_MODELS", "_NLLB_LANG_CODES", "_nllb_pipeline_cache"):
            assert not any(hasattr(mod, gone) for mod in (engine_registry, engine_local, engine_pricing, engine_shared))

    def test_is_refused_with_the_removed_engine_message(self):
        assert "nllb" in engine_registry.REMOVED_ENGINES
        assert engine_registry.unknown_engine_message("nllb") == "The nllb engine was removed. Pick another engine."
        assert engine_registry.unknown_engine_message("nope") == "Unknown engine."


class TestFreeEngineLabelling:
    """Step 1d item 4: every free-to-use option is clearly labelled as
    such (test_offline, ollama always; gemini only
    when the per-session "free-tier key" setting is on), and paid
    engines keep their normal descriptions."""

    def test_free_engines_set_matches_the_roadmap_table(self):
        assert engine_registry.FREE_ENGINES == {"fake", "fake_mt", "ollama"}

    def test_gemini_is_not_unconditionally_free(self):
        # Gemini reuses one engine for free and paid keys -- whether a
        # given key is free depends on a setting, not the engine itself.
        assert "gemini" not in engine_registry.FREE_ENGINES

    def test_every_free_engine_note_says_free(self):
        for name in engine_registry.FREE_ENGINES:
            assert engine_registry.ENGINE_NOTES[name].startswith("Free")
            assert engine_registry.engine_picker_label(name) == engine_registry.ENGINE_NOTES[name]

    def test_paid_engine_notes_say_paid(self):
        for name in ("claude", "deepseek", "openai"):
            assert engine_registry.ENGINE_NOTES[name].startswith("Paid")

    def test_ollama_note_says_local_is_free_and_hosted_gemma_4_is_paid(self):
        note = engine_registry.ENGINE_NOTES["ollama"]
        assert "local" in note and "Gemma 4" in note and "paid" in note and "Free" in note
        assert "testing" not in note and "lower quality" not in note

    def test_every_note_fits_the_narrow_dropdown(self):
        for name in engine_registry.ENGINES:
            assert 0 < len(engine_registry.ENGINE_NOTES[name]) <= 80, name
        assert len(engine_registry.GEMINI_FREE_TIER_NOTE) <= 80

    def test_note_models_match_the_model_lists(self):
        assert "claude-sonnet-5-5" in engine_pricing.CLAUDE_MODELS and "claude-opus-5-5" in engine_pricing.CLAUDE_MODELS
        assert all(m.startswith(("gemma4", "translategemma", "qwen3")) for m in engine_local.OLLAMA_MODELS)

    def test_gemini_label_is_plain_by_default(self):
        assert engine_registry.engine_picker_label("gemini") == engine_registry.ENGINE_NOTES["gemini"]
        assert "Free" not in engine_registry.engine_picker_label("gemini", gemini_free_tier=False)

    def test_gemini_label_switches_when_free_tier_is_on(self):
        label = engine_registry.engine_picker_label("gemini", gemini_free_tier=True)
        assert label.startswith("Free")
        assert "Google may use your text" in label

    def test_free_tier_flag_never_changes_other_engines_labels(self):
        for name in engine_registry.ENGINES:
            if name == "gemini":
                continue
            assert engine_registry.engine_picker_label(name, gemini_free_tier=True) == engine_registry.ENGINE_NOTES[name]


class TestGetEngineFreeTierPassthrough:
    def test_free_tier_reaches_the_constructed_gemini_engine(self):
        engine = engine_registry.get_engine("gemini", "fake-key", free_tier=True)
        assert isinstance(engine, engine_gemini.GeminiEngine)
        assert engine.free_tier is True

    def test_defaults_to_not_free_tier(self):
        engine = engine_registry.get_engine("gemini", "fake-key")
        assert engine.free_tier is False

    def test_free_tier_kwarg_is_ignored_for_non_gemini_engines(self):
        # Every other engine class's __init__ has no free_tier parameter --
        # this must not raise a TypeError just because the caller always
        # passes the kwarg.
        engine = engine_registry.get_engine("fake", free_tier=True)
        assert not hasattr(engine, "free_tier")

    def test_free_tier_reaches_gemini_even_with_an_explicit_model(self):
        engine = engine_registry.get_engine("gemini", "fake-key", "gemini-pro-latest", free_tier=True)
        assert engine.model == "gemini-pro-latest"
        assert engine.free_tier is True


class TestGetEngineOllamaBaseUrlPassthrough:
    """Step 5b item 1: the Ollama base URL configured in Settings wasn't
    actually reaching OllamaEngine -- get_engine() silently dropped it, so
    translation always talked to the hardcoded http://localhost:11434
    default no matter what was configured."""

    def test_base_url_reaches_the_constructed_ollama_engine(self):
        engine = engine_registry.get_engine("ollama", None, base_url="http://gpu-box:11434")
        assert isinstance(engine, engine_local.OllamaEngine)
        assert engine.base_url == "http://gpu-box:11434"

    def test_defaults_to_ollamas_own_default_when_not_configured(self):
        engine = engine_registry.get_engine("ollama")
        assert engine.base_url == "http://localhost:11434"

    def test_base_url_reaches_ollama_even_with_an_explicit_model(self):
        engine = engine_registry.get_engine("ollama", None, "qwen2.5:14b", base_url="http://gpu-box:11434")
        assert engine.model == "qwen2.5:14b"
        assert engine.base_url == "http://gpu-box:11434"

    def test_base_url_kwarg_is_ignored_for_non_ollama_engines(self):
        # Every other engine class's __init__ has no base_url parameter --
        # this must not raise a TypeError just because the caller always
        # passes the kwarg.
        engine = engine_registry.get_engine("fake", base_url="http://gpu-box:11434")
        assert not hasattr(engine, "base_url")


class TestGemini31FlashLite:
    """Step 9: Gemini 3.1 Flash-Lite is selectable, never the default."""

    def test_selectable_but_not_the_default(self):
        keys = list(engine_pricing.GEMINI_MODELS)
        assert "gemini-3.1-flash-lite" in keys
        assert keys[0] == "gemini-flash-lite-latest"
        assert engine_gemini.GeminiEngine("k").model == "gemini-flash-lite-latest"

    def test_priced_at_the_cheaper_rate(self):
        assert engine_pricing.PRICING_PER_MILLION_TOKENS["gemini-3.1-flash-lite"] == {"input": 0.25, "output": 1.50}
        assert engine_pricing.estimate_cost("gemini-3.1-flash-lite", 1_000_000, 1_000_000) == pytest.approx(1.75)

    def test_reaches_the_api_with_the_right_model_id(self, monkeypatch):
        captured = {}

        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass

            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": '{"1": "Hi."}'}]}}]}

        patch_post(monkeypatch, lambda url, headers=None, json=None, timeout=None, stream=None:
                   captured.update(url=url) or FakeResponse())
        engine = engine_registry.get_engine("gemini", "k", "gemini-3.1-flash-lite")
        assert engine.translate_batch(["你好"], {}) == ["Hi."]
        assert captured["url"].endswith("/models/gemini-3.1-flash-lite:generateContent")


def test_gemini_generate_request_keeps_key_in_header_not_url():
    from engine_backends.shared import gemini_generate_request
    url, headers = gemini_generate_request("gemini-x", "SECRET-KEY")
    assert url == ("https://generativelanguage.googleapis.com/v1beta/models/"
                   "gemini-x:generateContent")
    assert "SECRET-KEY" not in url and "key=" not in url
    assert headers == {"x-goog-api-key": "SECRET-KEY"}


class TestGeminiReplies:
    """Every Gemini call site reads a reply the same way (engine_backends.gemini.gemini_reply)."""

    @staticmethod
    def _reply(parts, finish="STOP"):
        return {"candidates": [{"content": {"parts": [{"text": p} for p in parts]},
                                "finishReason": finish}],
                "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}

    def test_every_text_part_is_read(self):
        assert engine_gemini.gemini_reply(self._reply(["{\"a\": ", "1}"])) == ("{\"a\": 1}", "STOP")

    def test_a_withheld_reply_is_a_block_with_googles_reason(self):
        with pytest.raises(engine_shared.ContentModerationBlocked, match="OTHER_BLOCK"):
            engine_gemini.gemini_reply({"promptFeedback": {"blockReason": "OTHER_BLOCK"}})
        with pytest.raises(engine_shared.ContentModerationBlocked, match="RECITATION"):
            engine_gemini.gemini_reply(self._reply([], "RECITATION"))
        assert engine_gemini.gemini_reply(self._reply([], "STOP")) == ("", "STOP")

    def test_call_llm_json_reads_every_part(self, monkeypatch):
        monkeypatch.setattr("engine_backends.gemini.post_json",
                            lambda *a, **k: (self._reply(["[1, ", "2]"]), {}))
        assert llm_tasks.call_llm_json(engine_gemini.GeminiEngine("fake-key"), "prompt") == "[1, 2]"

    def test_call_llm_json_paces_and_counts_every_free_tier_attempt(self, monkeypatch):
        import requests
        calls = []

        def post(*a, **k):
            calls.append(1)
            if len(calls) == 1:
                raise requests.HTTPError("Gemini returned HTTP 503",
                                         response=type("R", (), {"status_code": 503, "headers": {}})())
            return self._reply(["ok"]), {}

        monkeypatch.setattr("engine_backends.gemini.post_json", post)
        monkeypatch.setattr("engine_backends.shared._cancellable_sleep", lambda s: None)
        engine = engine_gemini.GeminiEngine("fake-key", free_tier=True)
        paced = []
        real_throttle = engine._throttle_for_free_tier
        monkeypatch.setattr(engine, "_throttle_for_free_tier",
                            lambda: (paced.append(1), real_throttle()))
        assert llm_tasks.call_llm_json(engine, "prompt") == "ok"
        assert len(calls) == 2 and len(paced) == 2
        assert engine.rate_status["tpm_used"] == 15  # this call's tokens reach the TPM window

    def test_a_block_fails_a_bounded_call_and_gives_the_default_otherwise(self, monkeypatch):
        from engine_backends.llm_tasks import bounded_llm_calls
        monkeypatch.setattr("engine_backends.gemini.post_json",
                            lambda *a, **k: ({"promptFeedback": {"blockReason": "SAFETY"}}, {}))
        engine = engine_gemini.GeminiEngine("fake-key")
        with bounded_llm_calls("job-1", lambda: False):
            with pytest.raises(engine_shared.ContentModerationBlocked, match="SAFETY"):
                llm_tasks.call_llm_json(engine, "prompt")
        assert llm_tasks.call_llm_json(engine, "prompt", fallback="{}") == "{}"

    def test_an_http_error_carries_googles_reason_without_the_key(self, monkeypatch):
        import requests

        class Refused(StreamedBody):
            status_code = 400
            text = '{"error": {"code": 400, "message": "API key not valid. Please pass a valid API key."}}'

        patch_post(monkeypatch, lambda *a, **k: Refused())
        engine = engine_gemini.GeminiEngine("AIzaSyFAKEKEY0123456789")
        with pytest.raises(requests.HTTPError) as err:
            engine.translate_batch(["你好"], {})
        assert "API key not valid" in str(err.value)
        assert "AIzaSyFAKEKEY0123456789" not in str(err.value)


class TestChatTaskReplies:
    @staticmethod
    def _engine(choices, finish=None):
        class Resp:
            usage = None

        resp = Resp()
        resp.choices = choices

        class Engine:
            name = "deepseek"
            model = "deepseek-flash"
            supports_reference = True

            def __init__(self):
                self.client = self
                self.chat = self
                self.completions = self

            def create(self, **kw):
                return resp
        return Engine()

    def test_no_choices_is_an_empty_reply_not_an_index_error(self):
        from engine_backends.llm_tasks import bounded_llm_calls
        engine = self._engine([])
        assert llm_tasks.call_llm_json(engine, "prompt") == ""
        with bounded_llm_calls("job-1", lambda: False):
            with pytest.raises(RuntimeError, match="empty reply"):
                llm_tasks.call_llm_json(engine, "prompt")

    def test_a_claude_refusal_fails_a_bounded_call(self):
        from engine_backends.llm_tasks import bounded_llm_calls

        class Resp:
            content = []
            stop_reason = "refusal"
            usage = None

        class Claude:
            name = "claude"
            model = "claude-sonnet-5-5"
            supports_reference = True

            def __init__(self):
                self.client = self
                self.messages = self

            def create(self, **kw):
                return Resp()

        with bounded_llm_calls("job-1", lambda: False):
            with pytest.raises(engine_shared.ContentModerationBlocked, match="refusal"):
                llm_tasks.call_llm_json(Claude(), "prompt")
        assert llm_tasks.call_llm_json(Claude(), "prompt") == ""


class TestGeminiQuotaReplies:
    """A Gemini 429 says in its body which quota it hit and how long to wait."""

    @staticmethod
    def _quota_429(monkeypatch, details, then_ok=False):
        import json
        calls = []

        class Limited(StreamedBody):
            status_code = 429
            text = json.dumps({"error": {"code": 429, "status": "RESOURCE_EXHAUSTED",
                                         "message": "You exceeded your current quota.",
                                         "details": details}})

        class Ok(StreamedBody):
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}

        def post(*a, **k):
            calls.append(1)
            return Ok() if then_ok and len(calls) > 1 else Limited()

        patch_post(monkeypatch, post)
        return calls

    def test_a_daily_quota_stops_at_once_instead_of_backing_off(self, monkeypatch):
        calls = self._quota_429(monkeypatch, [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{"quotaId": "GenerateRequestsPerDayPerProjectPerModel-FreeTier"}]}])
        slept = []
        monkeypatch.setattr("engine_backends.shared._cancellable_sleep", slept.append)
        with pytest.raises(engine_shared.FreeTierDailyLimitReached, match="daily request limit"):
            llm_tasks.call_llm_json(engine_gemini.GeminiEngine("fake-key", free_tier=True), "prompt")
        assert len(calls) == 1 and slept == []

    def test_the_bodys_retry_delay_is_waited(self, monkeypatch):
        calls = self._quota_429(monkeypatch, [
            {"@type": "type.googleapis.com/google.rpc.QuotaFailure",
             "violations": [{"quotaId": "GenerateRequestsPerMinutePerProjectPerModel-FreeTier"}]},
            {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "37s"}],
            then_ok=True)
        slept = []
        monkeypatch.setattr("engine_backends.shared._cancellable_sleep", slept.append)
        monkeypatch.setattr("engine_backends.shared._jitter", lambda: 1.0)
        assert llm_tasks.call_llm_json(engine_gemini.GeminiEngine("fake-key"), "prompt") == "ok"
        assert len(calls) == 2 and slept == [37.0]  # the plain backoff would wait 2 s

    def test_a_fallback_chain_moves_on_from_a_spent_daily_quota(self):
        from engine_backends.fallback import is_fallback_error, is_transient_fallback_error
        spent = engine_shared.FreeTierDailyLimitReached("daily request limit")
        assert is_fallback_error(spent) and not is_transient_fallback_error(spent)
