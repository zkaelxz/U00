"""
tests/test_local_model_defaults.py -- Step 5 (R3-lite): the local
translation default is hosted gemma4:31b-cloud (a saved qwen or gemma4 tag keeps working but is no longer offered; TranslateGemma 27B and 12B fall back to the default), Ollama requests
have a timeout, and GPU model caches are emptied once a stage finishes.
"""
import sys
import types

import pytest

import whisper_models
from engine_backends import engine_registry
from engine_backends import local as engine_local
from tests.http_fakes import StreamedBody


class TestOllamaDefaults:
    def test_default_model_is_hosted_gemma_31b(self):
        assert engine_local.OLLAMA_DEFAULT_MODEL == "gemma4:31b-cloud"
        assert engine_local.OllamaEngine().model == "gemma4:31b-cloud"
        assert engine_registry.get_engine("ollama").model == "gemma4:31b-cloud"
        assert engine_registry.builtin_default_model("ollama") == "gemma4:31b-cloud"
        assert not engine_local.OLLAMA_MODELS




    def test_translate_requests_have_a_timeout_and_use_the_default_model(self, monkeypatch):
        seen = {}

        class Resp(StreamedBody):
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": '{"1": "Hello."}'}}

        def fake_post(url, json=None, timeout=None, stream=None):
            seen.update(model=json["model"], timeout=timeout)
            return Resp()
        monkeypatch.setattr("requests.post", fake_post)
        assert engine_local.OllamaEngine().translate_batch(["你好"], {}) == ["Hello."]
        assert seen["model"] == "gemma4:31b-cloud"
        assert seen["timeout"] and seen["timeout"] > 0


@pytest.fixture
def loaded_models(monkeypatch):
    """Every model cache holding something, and a fake CUDA torch."""
    import asr_backend
    import forced_align
    monkeypatch.setitem(whisper_models._whisper_model_cache, ("medium", "cuda"), object())
    monkeypatch.setitem(asr_backend._asr_model_cache, "qwen3-asr", object())
    monkeypatch.setitem(forced_align._aligner_model_cache, "aligner", object())
    emptied = []
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: True,
                                       empty_cache=lambda: emptied.append(True))
    monkeypatch.setitem(sys.modules, "torch", torch)
    return {"asr": asr_backend._asr_model_cache, "aligner": forced_align._aligner_model_cache,
            "emptied": emptied}


class TestReleaseGpuModels:
    def test_clears_every_cache_and_empties_cuda(self, loaded_models):
        whisper_models.release_gpu_models()
        assert whisper_models._whisper_model_cache == {}
        assert loaded_models["asr"] == {} and loaded_models["aligner"] == {}
        assert loaded_models["emptied"] == [True]

    def test_does_not_import_torch_just_to_clear_it(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "torch", raising=False)
        whisper_models.release_gpu_models()
        assert "torch" not in sys.modules

    def test_a_broken_cuda_doesnt_fail_the_stage(self, monkeypatch):
        torch = types.ModuleType("torch")
        def boom():
            raise RuntimeError("CUDA driver mismatch")
        torch.cuda = types.SimpleNamespace(is_available=boom, empty_cache=boom)
        monkeypatch.setitem(sys.modules, "torch", torch)
        whisper_models.release_gpu_models()  # must not raise


def test_caches_are_empty_after_the_transcription_stage_completes(loaded_models, monkeypatch, tmp_path):
    """The roadmap's exit condition: a stage loads a model, finishes, and
    nothing is left cached."""
    from services import transcribe_pipeline

    def fake_transcribe(audio_path, whisper_size, **kw):
        whisper_models._whisper_model_cache[(whisper_size, "cuda")] = object()  # what loading does
        return [{"start": 0.0, "end": 1.0, "text": "你好"}]
    monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", fake_transcribe)
    monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: None)
    monkeypatch.setattr(whisper_models, "get_whisper_device_info", lambda *a, **k: {})
    out = transcribe_pipeline._transcribe_pipeline(
        transcribe_pipeline._ThreadReporter(None), str(tmp_path / "a.wav"), "whisper", None,
        "zh", "simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False, None,
        "", True, "whisper", "whisper_diff")
    assert [ln.zh for ln in out["lines"]] == ["你好"]
    assert whisper_models._whisper_model_cache == {}
    assert loaded_models["asr"] == {} and loaded_models["aligner"] == {}
    assert loaded_models["emptied"]


GEMMA_TAGS = ["gemma4:12b", "gemma4:26b"]


class _OllamaReply(StreamedBody):
    def __init__(self, message, status=200):
        self._message, self.status_code = message, status

    def raise_for_status(self):
        if self.status_code >= 400:
            import requests
            err = requests.HTTPError("boom")
            err.response = self
            raise err

    def json(self):
        return {"message": self._message}


class TestGemma4Models:

    def test_picker_choices(self):
        assert list(engine_local.OLLAMA_MODELS) == []
        assert list(engine_local.OLLAMA_CLOUD_MODELS) == ["gemma4:31b-cloud"]



    def test_a_reply_cut_off_mid_thinking_has_no_answer(self):
        from engine_backends.local import strip_ollama_thinking
        assert strip_ollama_thinking('<think>still going {"1": "x"}') == ""


    @pytest.mark.parametrize("tag", GEMMA_TAGS)
    def test_missing_model_message_gives_the_pull_command(self, monkeypatch, tag):
        from engine_backends.local import OllamaUnavailableError, _ollama_chat
        monkeypatch.setattr("requests.post", lambda *a, **k: _OllamaReply({}, status=404))
        with pytest.raises(OllamaUnavailableError) as info:
            _ollama_chat("http://192.168.7.9:11434", {"model": tag})
        assert info.value.reason == "ollama_model_missing"
        assert f'ollama pull {tag}' in info.value.message
        assert "192.168" not in info.value.message
