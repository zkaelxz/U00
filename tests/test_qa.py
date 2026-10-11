"""
tests/test_qa.py -- qa.py's in-app Q&A. Previously untested. qa.py keeps
its own engine dispatch separate from translate_engines.call_llm_json
(same Claude/OpenAI/Gemini shapes, duplicated rather than shared), so
it needed its own Ollama branch added for Step 1d -- confirmed it had
none: Ollama has no .client and isn't GeminiEngine, so it fell straight
through to the generic "doesn't support chat-style Q&A" decline.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import qa
import translate_engines as te
from tests.http_fakes import StreamedBody, patch_post


class TestAskAboutDrama:
    def test_declines_cleanly_for_a_translation_only_engine(self):
        class PureMT:
            supports_reference = False
        result = qa.ask_about_drama("who is this?", [], {}, PureMT())
        assert "doesn't support" in result.lower()

    def test_claude_shaped_engine(self):
        class FakeResponse(StreamedBody):
            content = [type("Block", (), {"type": "text", "text": "An answer."})()]

        class FakeClaudeLike:
            supports_reference = True
            model = "fake-claude"
            def __init__(self):
                self.client = self
                self.messages = self
            def create(self, model, max_tokens, system, messages):
                return FakeResponse()

        result = qa.ask_about_drama("who is this?", [], {}, FakeClaudeLike())
        assert result == "An answer."

    def test_openai_shaped_engine(self):
        class FakeChoice:
            class message:
                content = "An OpenAI-shaped answer."

        class FakeResponse(StreamedBody):
            choices = [FakeChoice()]

        class FakeOpenAiLike:
            supports_reference = True
            model = "fake-deepseek"
            def __init__(self):
                self.client = self
                self.chat = self
                self.completions = self
            def create(self, model, messages):
                return FakeResponse()

        result = qa.ask_about_drama("who is this?", [], {}, FakeOpenAiLike())
        assert result == "An OpenAI-shaped answer."

    def test_gemini_engine(self, monkeypatch):
        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "Gemini's answer."}]}}]}

        patch_post(monkeypatch, lambda *a, **k: FakeResponse())
        engine = te.GeminiEngine("fake-key")
        result = qa.ask_about_drama("who is this?", [], {}, engine)
        assert result == "Gemini's answer."

    def test_free_tier_gemini_is_throttled(self, monkeypatch):
        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"candidates": [{"content": {"parts": [{"text": "answer"}]}}]}

        patch_post(monkeypatch, lambda *a, **k: FakeResponse())
        engine = te.GeminiEngine("fake-key", free_tier=True)
        calls = []
        monkeypatch.setattr(engine, "_throttle_for_free_tier", lambda: calls.append(1))

        qa.ask_about_drama("who is this?", [], {}, engine)
        assert calls == [1]

    def test_ollama_engine_is_not_silently_skipped(self, monkeypatch):
        captured = {}

        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": "Ollama's answer."}}

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured["url"] = url
            captured["json"] = json
            return FakeResponse()
        patch_post(monkeypatch, fake_post)

        engine = te.OllamaEngine()
        result = qa.ask_about_drama("who is this?", [], {}, engine)

        assert result == "Ollama's answer."
        assert captured["url"] == f"{engine.base_url}/api/chat"
        assert captured["json"]["options"]["num_ctx"] >= te.OLLAMA_MIN_NUM_CTX

    def test_ollama_chat_history_is_folded_into_one_prompt(self, monkeypatch):
        captured = {}

        class FakeResponse(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": "answer"}}

        def fake_post(url, headers=None, json=None, timeout=None, stream=None):
            captured["json"] = json
            return FakeResponse()
        patch_post(monkeypatch, fake_post)

        engine = te.OllamaEngine()
        qa.ask_about_drama("second question", [], {}, engine,
                            chat_history=[{"role": "user", "content": "first question"},
                                          {"role": "assistant", "content": "first answer"}])

        user_msg = captured["json"]["messages"][1]["content"]
        assert "first question" in user_msg
        assert "first answer" in user_msg
        assert "second question" in user_msg
