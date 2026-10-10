"""Byte caps on the raw HTTP replies of the LLM engines, Q&A, bulk Gemini
batches and Groq transcription. No network: requests is faked."""
from lib import http
from tests.http_fakes import patch_post
import pytest
import requests
import urllib3

import bulk_providers
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
        patch_post(monkeypatch, fake)
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

    @pytest.mark.parametrize("make_exc, reason", [
        (lambda: requests.ConnectionError(urllib3.exceptions.ReadTimeoutError(
            None, "http://192.168.7.9:11434/api/chat", "Read timed out.")), "ollama_timeout"),
        (lambda: requests.exceptions.ChunkedEncodingError(
            "Connection broken: 192.168.7.9 reset"), "ollama_unreachable"),
    ])
    def test_ollama_body_read_failures_are_mapped(self, posts, make_exc, reason):
        class Failing(StreamResp):
            def iter_content(self, size):
                raise make_exc()
                yield b""
        r = posts(Failing())
        with pytest.raises(local.OllamaUnavailableError) as exc:
            local._ollama_chat("http://192.168.7.9:11434", {"model": "m"})
        assert exc.value.reason == reason and "192.168" not in str(exc.value)
        assert r.closed

    def test_ollama_model_missing_still_reported(self, posts):
        r = posts(StreamResp(status=404))
        with pytest.raises(local.OllamaUnavailableError) as exc:
            local._ollama_chat("http://localhost:11434", {"model": "m"})
        assert exc.value.reason == "ollama_model_missing" and r.closed

    def test_ollama_health_check_never_reads_the_model_list(self, monkeypatch):
        local._ollama_reachability_cache.clear()
        r = StreamResp(b"x" * 10_000)
        monkeypatch.setattr(http, "pinned_get", lambda *a, **kw: r)
        assert local.check_ollama_reachable("http://cap-test:11434") is True
        assert r.chunks_read <= 1 and r.closed  # lib.http reads one byte of a truncated body

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
    @pytest.fixture
    def posts(self, monkeypatch):
        calls = []

        def install(resp):
            def fake(url, ip, headers, timeout=None, method="GET", **kwargs):
                calls.append({"url": url, "ip": ip, "headers": headers, "timeout": timeout})
                return resp
            monkeypatch.setattr(http, "pinned_get", fake)
            return resp
        install.calls = calls
        return install

    def _provider(self):
        return bulk_providers.GeminiBatchProvider(gemini.GeminiEngine(SECRET))

    def test_poll_reply_over_cap(self, posts):
        r = posts(StreamResp(headers={
            "Content-Length": str(bulk_providers.BATCH_RESPONSE_MAX_BYTES + 1)}))
        with pytest.raises(RuntimeError, match="Gemini batch request failed: The response is larger"):
            self._provider().poll("batches/1")
        assert r.chunks_read == 0 and r.closed
        assert posts.calls[0]["timeout"] and posts.calls[0]["ip"] is None
        assert posts.calls[0]["headers"] == {"x-goog-api-key": SECRET}

    def test_auth_error_reads_only_a_small_error_body(self, posts):
        r = posts(StreamResp(b"x" * 100_000, status=401))
        with pytest.raises(bulk_providers.BulkAuthError) as exc:
            self._provider().poll("batches/1")
        assert r.chunks_read <= 1 and r.closed
        assert SECRET not in str(exc.value)

    def test_a_redirect_is_not_followed_so_the_key_stays_home(self, posts):
        r = posts(StreamResp(b"", status=302, headers={"Location": "https://evil.example/"}))
        with pytest.raises(RuntimeError) as exc:
            self._provider().poll("batches/1")
        assert "302" in str(exc.value) and len(posts.calls) == 1

    def test_a_batch_bigger_than_one_llm_reply_is_allowed(self, posts):
        body = b'{"done": true, "pad": "' + b"x" * (shared.PROVIDER_RESPONSE_MAX_BYTES + 1) + b'"}'
        posts(StreamResp(body))
        assert self._provider().poll("batches/1") == "ended"


class TestGroq:
    @pytest.fixture
    def posts(self, monkeypatch):
        calls = []

        def install(resp):
            def fake(url, ip, headers, timeout=None, method="GET", **kwargs):
                calls.append({"timeout": timeout, **kwargs})
                return resp
            monkeypatch.setattr(http, "pinned_get", fake)
            return resp
        install.calls = calls
        return install

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
        assert posts.calls[0]["timeout"] == 600

    def test_error_detail_is_capped_and_redacted(self, posts, tmp_path):
        posts(StreamResp(f"bad key {SECRET}".encode() + b"y" * (core.GROQ_ERROR_MAX_BYTES + 1),
                         status=401))
        with pytest.raises(core.GroqTranscriptionError) as exc:
            self._transcribe(tmp_path)
        # Over the error cap the body is cut, not dropped: the status and a redacted head stay.
        assert str(exc.value).startswith("Groq API returned 401: ") and SECRET not in str(exc.value)
        assert len(str(exc.value)) < 400

    def test_error_detail_kept_and_redacted(self, posts, tmp_path):
        posts(StreamResp(f"bad key {SECRET}".encode(), status=401))
        with pytest.raises(core.GroqTranscriptionError) as exc:
            self._transcribe(tmp_path)
        assert "bad key" in str(exc.value) and SECRET not in str(exc.value)

    def test_segments_still_parsed(self, posts, tmp_path):
        posts(StreamResp(b'{"segments": [{"start": 0, "end": 1, "text": " hi "}]}'))
        assert self._transcribe(tmp_path) == [{"start": 0, "end": 1, "text": "hi"}]


class TestEngineTransport:
    """The plain-POST engines go through lib.http inside call_with_backoff; the
    exception types must keep driving its retry decisions."""

    GEMINI_OK = b'{"candidates": [{"content": {"parts": [{"text": "{\\"1\\": \\"Hi\\"}"}]}}]}'
    OPENAI_OK = b'{"choices": [{"message": {"content": "hi"}}]}'
    OLLAMA_OK = b'{"message": {"content": "hi"}}'

    CALLERS = {
        "openai": (lambda: shared.call_with_backoff(lambda: openai_compat.OpenAIEngine(SECRET).chat(
            [{"role": "user", "content": "hi"}])), OPENAI_OK),
        "gemini_batch": (lambda: shared.call_with_backoff(lambda: gemini.GeminiEngine(SECRET).translate_batch(
            ["你好"], {"line_ids": [1]})), GEMINI_OK),
        "gemini_task": (lambda: llm_tasks.call_llm_json(gemini.GeminiEngine(SECRET), "p"), GEMINI_OK),
        "qa_gemini": (lambda: qa.dispatch_chat("s", [{"role": "user", "content": "q"}],
                                               gemini.GeminiEngine(SECRET)), GEMINI_OK),
        "qa_ollama": (lambda: qa.dispatch_chat("s", [{"role": "user", "content": "q"}],
                                               local.OllamaEngine()), OLLAMA_OK),
    }

    @pytest.fixture
    def wire(self, monkeypatch):
        """Each call to lib.http's connection function pops the next step: an
        exception to raise or a response to return."""
        steps, calls = [], []
        monkeypatch.setattr(shared, "_cancellable_sleep", lambda s: None)

        def fake(url, ip, headers, timeout=None, method="GET", **kw):
            calls.append((url, headers))
            step = steps.pop(0)
            if isinstance(step, Exception):
                raise step
            return step
        monkeypatch.setattr(http, "pinned_get", fake)
        return steps, calls

    @pytest.mark.parametrize("name", CALLERS)
    def test_a_transport_error_is_retried_once(self, wire, name):
        steps, calls = wire
        call, ok = self.CALLERS[name]
        steps[:] = [requests.ConnectionError("boom"), StreamResp(ok)]
        call()
        assert len(calls) == 2

    @pytest.mark.parametrize("name", CALLERS)
    def test_a_persistent_transport_error_surfaces_without_the_url_or_key(self, wire, name):
        steps, calls = wire
        call, _ = self.CALLERS[name]
        steps[:] = [requests.ConnectionError(f"https://x/?key={SECRET}")] * 5
        with pytest.raises(http.FetchError) as exc:
            call()
        assert len(calls) == 2 and SECRET not in str(exc.value)

    @pytest.mark.parametrize("name", CALLERS)
    def test_a_401_is_not_backed_off(self, wire, name):
        steps, calls = wire
        call, _ = self.CALLERS[name]
        steps[:] = [StreamResp(b'{"error": {"message": "bad key"}}', status=401)] * 5
        with pytest.raises(requests.HTTPError) as exc:
            call()
        assert exc.value.response.status_code == 401
        assert len(calls) == 2  # the one quick retry, not the rate-limit ladder
        assert SECRET not in str(exc.value)

    @pytest.mark.parametrize("name", CALLERS)
    def test_a_429_backs_off_until_it_succeeds(self, wire, name):
        steps, calls = wire
        call, ok = self.CALLERS[name]
        steps[:] = [StreamResp(b"{}", status=429)] * 3 + [StreamResp(ok)]
        call()
        assert len(calls) == 4

    @pytest.mark.parametrize("name", CALLERS)
    def test_a_redirect_is_not_followed_and_the_key_goes_nowhere_else(self, wire, name):
        steps, calls = wire
        call, _ = self.CALLERS[name]
        steps[:] = [StreamResp(b"", status=302, headers={"Location": "http://elsewhere.example/x"})] * 5
        with pytest.raises(Exception):
            call()
        assert all(not u.startswith("http://elsewhere") for u, _ in calls)
        assert len(calls) == 2  # each attempt is one request, never a second hop
