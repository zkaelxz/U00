"""Byte caps on the raw HTTP replies of the LLM engines, Q&A, bulk Gemini
batches and Groq transcription. No network: requests is faked."""
import pytest
import requests

import bulk_translate
import core
import qa
from engine_backends import gemini, llm_tasks, local, openai_compat, shared

SECRET = "sk-test-0123456789abcdef0123456789abcdef"


class StreamResp:
    """A `stream=True` requests response that records how much was read."""

    def __init__(self, body=b"{}", status=200, headers=None):
        self.body, self.status_code = body, status
        self.ok = status < 400
        self.headers = headers or {}
        self.closed = False
        self.chunks_read = 0

    def iter_content(self, size):
        for i in range(0, len(self.body), size):
            self.chunks_read += 1
            yield self.body[i:i + size]

    def close(self):
        self.closed = True

    def raise_for_status(self):
        if not self.ok:
            raise requests.HTTPError(f"{self.status_code} error", response=self)


def _oversized():
    """Declares a body one byte over the provider cap; nothing should be read."""
    return StreamResp(headers={"Content-Length": str(shared.PROVIDER_RESPONSE_MAX_BYTES + 1)})


@pytest.fixture
def posts(monkeypatch):
    calls = []

    def install(resp):
        def fake(*args, **kwargs):
            calls.append(kwargs)
            return resp
        monkeypatch.setattr(requests, "post", fake)
        monkeypatch.setattr(requests, "get", fake)
        return resp
    install.calls = calls
    return install


class TestReadJsonCapped:
    def test_reads_json_and_closes(self):
        r = StreamResp(b'{"a": 1}')
        assert shared.read_json_capped(r, 10) == {"a": 1}
        assert r.closed

    def test_over_the_cap_raises_a_message_without_details(self):
        r = StreamResp(b'{"pad": "' + b"x" * 200 + b'"}')
        with pytest.raises(shared.ProviderResponseTooLarge) as exc:
            shared.read_json_capped(r, 10, cap_bytes=100)
        assert r.closed
        assert "http" not in str(exc.value).lower()

    def test_error_status_raises_without_reading_the_body(self):
        r = StreamResp(b"x" * 1000, status=500)
        with pytest.raises(requests.HTTPError):
            shared.read_json_capped(r, 10)
        assert r.chunks_read == 0 and r.closed

    def test_caller_error_type(self):
        with pytest.raises(KeyError):
            shared.read_json_capped(StreamResp(b"x" * 10), 10, cap_bytes=5,
                                    make_error=lambda: KeyError("too big"))


class TestEngines:
    def test_gemini_translate_batch(self, posts):
        r = posts(_oversized())
        engine = gemini.GeminiEngine(SECRET)
        with pytest.raises(shared.ProviderResponseTooLarge) as exc:
            engine.translate_batch(["你好"], {})
        assert SECRET not in str(exc.value)
        assert r.chunks_read == 0 and r.closed
        assert posts.calls[0]["stream"] is True and posts.calls[0]["timeout"]

    def test_openai_chat(self, posts):
        r = posts(_oversized())
        engine = openai_compat.OpenAIEngine(SECRET)
        with pytest.raises(shared.ProviderResponseTooLarge):
            engine.chat([{"role": "user", "content": "hi"}])
        assert r.chunks_read == 0 and r.closed
        assert posts.calls[0]["stream"] is True

    def test_openai_error_detail_is_capped_too(self, posts):
        r = posts(StreamResp(b"x" * (openai_compat.ERROR_BODY_MAX_BYTES + 1), status=400))
        with pytest.raises(requests.HTTPError) as exc:
            openai_compat.OpenAIEngine(SECRET).chat([{"role": "user", "content": "hi"}])
        assert str(exc.value) == "OpenAI returned HTTP 400"
        assert r.closed

    def test_ollama_chat(self, posts):
        r = posts(_oversized())
        with pytest.raises(shared.ProviderResponseTooLarge):
            local._ollama_chat("http://localhost:11434", {"model": "m"})
        assert r.chunks_read == 0 and r.closed
        assert posts.calls[0]["stream"] is True

    def test_ollama_model_missing_still_reported(self, posts):
        r = posts(StreamResp(status=404))
        with pytest.raises(local.OllamaUnavailableError) as exc:
            local._ollama_chat("http://localhost:11434", {"model": "m"})
        assert exc.value.reason == "ollama_model_missing" and r.closed

    def test_ollama_health_check_never_reads_the_model_list(self, posts):
        local._ollama_reachability_cache.clear()
        r = posts(StreamResp(b"x" * 10_000))
        assert local.check_ollama_reachable("http://cap-test:11434") is True
        assert r.chunks_read == 0 and r.closed
        assert posts.calls[0]["stream"] is True

    def test_call_llm_json_gemini(self, posts):
        r = posts(_oversized())
        with pytest.raises(shared.ProviderResponseTooLarge):
            llm_tasks.call_llm_json(gemini.GeminiEngine(SECRET), "prompt")
        assert r.chunks_read == 0 and r.closed


class TestQa:
    def test_gemini(self, posts):
        r = posts(_oversized())
        with pytest.raises(shared.ProviderResponseTooLarge):
            qa.dispatch_chat("sys", [{"role": "user", "content": "q"}], gemini.GeminiEngine(SECRET))
        assert r.chunks_read == 0 and r.closed
        assert posts.calls[0]["stream"] is True

    def test_ollama(self, posts):
        r = posts(_oversized())
        with pytest.raises(shared.ProviderResponseTooLarge):
            qa.dispatch_chat("sys", [{"role": "user", "content": "q"}], local.OllamaEngine())
        assert r.chunks_read == 0 and r.closed

    def test_ollama_reply_still_parsed(self, posts):
        posts(StreamResp(b'{"message": {"content": " hi "}}'))
        assert qa.dispatch_chat("sys", [{"role": "user", "content": "q"}],
                                local.OllamaEngine()) == "hi"


class TestBulkGemini:
    def _provider(self):
        return bulk_translate.GeminiBatchProvider(gemini.GeminiEngine(SECRET))

    def test_poll_reply_over_cap(self, posts):
        r = posts(StreamResp(headers={
            "Content-Length": str(bulk_translate.BATCH_RESPONSE_MAX_BYTES + 1)}))
        with pytest.raises(shared.ProviderResponseTooLarge):
            self._provider().poll("batches/1")
        assert r.chunks_read == 0 and r.closed
        assert posts.calls[0]["stream"] is True

    def test_auth_error_closes_without_reading(self, posts):
        r = posts(StreamResp(b"x" * 100, status=401))
        with pytest.raises(bulk_translate.BulkAuthError):
            self._provider().poll("batches/1")
        assert r.chunks_read == 0 and r.closed

    def test_a_batch_bigger_than_one_llm_reply_is_allowed(self, posts):
        body = b'{"done": true, "pad": "' + b"x" * (shared.PROVIDER_RESPONSE_MAX_BYTES + 1) + b'"}'
        posts(StreamResp(body))
        assert self._provider().poll("batches/1") == "ended"


class TestGroq:
    def _transcribe(self, tmp_path):
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"RIFF")
        return core.transcribe_with_groq(str(audio), "zh", SECRET)

    def test_reply_over_cap(self, posts, tmp_path):
        r = posts(StreamResp(headers={"Content-Length": str(core.GROQ_RESPONSE_MAX_BYTES + 1)}))
        with pytest.raises(core.GroqTranscriptionError) as exc:
            self._transcribe(tmp_path)
        assert SECRET not in str(exc.value) and "http" not in str(exc.value).lower()
        assert r.chunks_read == 0 and r.closed
        assert posts.calls[0]["stream"] is True and posts.calls[0]["timeout"] == 600

    def test_error_detail_is_capped_and_redacted(self, posts, tmp_path):
        posts(StreamResp(f"bad key {SECRET}".encode() + b"y" * (core.GROQ_ERROR_MAX_BYTES + 1),
                         status=401))
        with pytest.raises(core.GroqTranscriptionError) as exc:
            self._transcribe(tmp_path)
        assert str(exc.value) == "Groq API returned 401: "

    def test_error_detail_kept_and_redacted(self, posts, tmp_path):
        posts(StreamResp(f"bad key {SECRET}".encode(), status=401))
        with pytest.raises(core.GroqTranscriptionError) as exc:
            self._transcribe(tmp_path)
        assert "bad key" in str(exc.value) and SECRET not in str(exc.value)

    def test_segments_still_parsed(self, posts, tmp_path):
        posts(StreamResp(b'{"segments": [{"start": 0, "end": 1, "text": " hi "}]}'))
        assert self._transcribe(tmp_path) == [{"start": 0, "end": 1, "text": "hi"}]
