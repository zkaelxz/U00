"""
tests/test_local_model_defaults.py -- Step 5 (R3-lite): the local
translation default is gemma4:12b (Qwen is no longer offered but a saved tag keeps working), Ollama requests
have a timeout, and GPU model caches are emptied once a stage finishes.
"""
import sys
import types

import pytest

import core
import whisper_models
import translate_engines as te
from tests.http_fakes import StreamedBody


class TestOllamaDefaults:
    def test_default_model_is_gemma4_12b(self):
        assert te.OLLAMA_DEFAULT_MODEL == "gemma4:12b"
        assert te.OllamaEngine().model == "gemma4:12b"
        assert te.get_engine("ollama").model == "gemma4:12b"
        assert te.builtin_default_model("ollama") == "gemma4:12b"
        assert list(te.OLLAMA_MODELS)[0] == "gemma4:12b"

    def test_qwen_is_not_offered_but_a_saved_tag_still_builds_an_engine(self):
        assert not any(t.startswith("qwen") for t in te.OLLAMA_MODELS)
        for tag in ("qwen3:8b", "qwen2.5:14b"):
            assert te.get_engine("ollama", None, tag).model == tag

    def test_a_saved_qwen_tag_still_passes_the_run_option_check(self):
        from services import translate_run_service as svc
        svc._require_offered_model("ollama", "qwen3:8b")
        svc._require_offered_model("ollama", "qwen2.5:14b")

    def test_a_saved_qwen_tag_is_kept_by_a_preset(self, isolated_db):
        from services import translate_run_service as svc
        saved = svc.save_translate_preset("Qwen preset", "ollama", engine_model="qwen3:8b")
        did = isolated_db.create_drama(title_zh="D")
        applied = svc.apply_translate_preset(did, saved["preset"]["id"])
        assert applied["translation_engine"] == "ollama"
        assert applied["engine_model"] == "qwen3:8b"
        row = next(p for p in isolated_db.list_presets() if p["id"] == saved["preset"]["id"])
        assert row["engine_model"] == "qwen3:8b"

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
        assert te.OllamaEngine().translate_batch(["你好"], {}) == ["Hello."]
        assert seen["model"] == "gemma4:12b"
        assert seen["timeout"] and seen["timeout"] > 0


@pytest.fixture
def loaded_models(monkeypatch):
    """Every model cache holding something, and a fake CUDA torch."""
    import asr_backend
    import forced_align
    import translate_engines
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


def test_caches_are_empty_after_the_transcription_stage_completes(loaded_models, monkeypatch):
    """The roadmap's exit condition: a stage loads a model, finishes, and
    nothing is left cached."""
    import background_jobs

    def fake_transcribe(audio_path, whisper_size, **kw):
        whisper_models._whisper_model_cache[(whisper_size, "cuda")] = object()  # what loading does
        return [{"start": 0.0, "end": 1.0, "text": "你好"}]
    # Migration Slice 2: run_transcribe_job now lives in and resolves
    # transcribe_for_timing from services.workspace_job_service's own
    # globals, not tabs.workspace_tab's.
    from services import workspace_job_service
    monkeypatch.setattr(workspace_job_service, "transcribe_for_timing", fake_transcribe)
    job_id = "test_release_after_transcribe"
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    # Repointed from the Workspace tab's run_transcribe_job (a re-export of this).
    workspace_job_service.run_transcribe_job(job_id, "a.wav", "medium", "zh", True, None, None, None, 5, 2000)
    assert background_jobs.get_status(job_id)["result"]["segments"]
    assert whisper_models._whisper_model_cache == {}
    assert loaded_models["asr"] == {} and loaded_models["aligner"] == {}
    assert loaded_models["emptied"]
    background_jobs._jobs.pop(job_id, None)


def test_cli_diarize_releases_models_even_when_detection_fails(isolated_db, monkeypatch, loaded_models):
    import argparse
    import contextlib
    import io
    import os
    import cli
    import diarize
    from core import Line
    did = isolated_db.create_drama(title_en="D", audio_filename="a.wav")
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, "a.wav"), "wb").close()
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a")])

    def failing(*a, **k):
        whisper_models._whisper_model_cache["pyannote"] = object()
        raise RuntimeError("out of memory")
    monkeypatch.setattr(diarize, "diarize", failing)
    args = argparse.Namespace(id=did, hf_token="hf", num_speakers=0, overwrite_manual=False)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        cli.cmd_diarize(args)
    assert whisper_models._whisper_model_cache == {}


GEMMA_TAGS = ["gemma4:12b", "gemma4:26b", "gemma4:31b"]


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
    def test_tags_are_offered_and_e4b_e2b_are_not(self):
        for tag in GEMMA_TAGS:
            assert tag in te.OLLAMA_MODELS
            assert te.get_engine("ollama", None, tag).model == tag
        assert not any(t.startswith("gemma4:e") for t in te.OLLAMA_MODELS)

    def test_default_and_existing_choices_are_unchanged(self):
        assert list(te.OLLAMA_MODELS) == ["gemma4:12b", "gemma4:26b", "gemma4:31b"]
        assert te.OllamaEngine().model == "gemma4:12b"

    def test_service_lists_the_tags_for_the_picker(self, isolated_db):
        from services import translate_service
        ollama = next(e for e in translate_service.list_engines() if e["name"] == "ollama")
        assert all(t in ollama["models"] for t in GEMMA_TAGS)

    @pytest.mark.parametrize("tag", GEMMA_TAGS)
    def test_cli_passes_the_tag_to_the_engine(self, isolated_db, monkeypatch, tag):
        import contextlib
        import io
        import cli_translate
        from core import Line
        from tests.test_cli import _translate_args
        seen = {}

        class Engine:
            name, supports_reference, model, last_usage = "ollama", True, tag, {}

            def translate_batch(self, zh_lines, context):
                return [f"EN:{z}" for z in zh_lines]

        def fake_get_engine(name, api_key=None, model=None, **kw):
            seen["model"] = model
            return Engine()
        monkeypatch.setattr(te, "get_engine", fake_get_engine)
        did = isolated_db.create_drama(title_en="T", status="aligned")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你好")])
        with contextlib.redirect_stdout(io.StringIO()):
            cli_translate.cmd_translate(_translate_args(id=did, engine="ollama", model=tag,
                                              cost_cap=None, monthly_cap=None))
        assert seen["model"] == tag

    def test_request_shape_is_the_same_as_for_a_hand_typed_qwen_tag(self, monkeypatch):
        sent = []

        def fake_post(url, json=None, timeout=None, stream=None):
            sent.append((json, timeout))
            return _OllamaReply({"content": '{"1": "Hello."}'})
        monkeypatch.setattr("requests.post", fake_post)
        for model in ("qwen3:8b", "gemma4:12b"):
            te.OllamaEngine(model=model).translate_batch(["你好"], {})
        (qwen, _), (gemma, _) = sent
        assert gemma["format"] == qwen["format"] == te._OLLAMA_ID_KEYED_JSON_SCHEMA
        assert gemma["options"] == qwen["options"] and gemma["stream"] is False
        assert [m["role"] for m in gemma["messages"]] == ["system", "user"]

    @pytest.mark.parametrize("message", [
        {"content": '{"1": "Hello."}', "thinking": 'reasoning {"1": "wrong"}'},
        {"content": '<think>try {"1": "wrong"}</think>\n{"1": "Hello."}'},
    ])
    def test_reasoning_never_reaches_the_subtitle(self, monkeypatch, message):
        monkeypatch.setattr("requests.post", lambda *a, **k: _OllamaReply(message))
        assert te.OllamaEngine(model="gemma4:12b").translate_batch(["你好"], {}) == ["Hello."]

    def test_a_reply_cut_off_mid_thinking_has_no_answer(self):
        from engine_backends.local import strip_ollama_thinking
        assert strip_ollama_thinking('<think>still going {"1": "x"}') == ""

    @pytest.mark.parametrize("model, expected", [
        ("qwen3:8b", 300), ("gemma4:12b", 300),
        ("gemma4:26b", 900), ("gemma4:31b", 900),
    ])
    def test_cpu_split_models_get_a_longer_but_finite_timeout(self, monkeypatch, model, expected):
        seen = {}

        def fake_post(url, json=None, timeout=None, stream=None):
            seen["timeout"] = timeout
            return _OllamaReply({"content": "{}"})
        monkeypatch.setattr("requests.post", fake_post)
        te.OllamaEngine(model=model).translate_batch(["你好"], {})
        assert seen["timeout"] == expected

    @pytest.mark.parametrize("tag", GEMMA_TAGS)
    def test_missing_model_message_gives_the_pull_command(self, monkeypatch, tag):
        from engine_backends.local import OllamaUnavailableError, _ollama_chat
        monkeypatch.setattr("requests.post", lambda *a, **k: _OllamaReply({}, status=404))
        with pytest.raises(OllamaUnavailableError) as info:
            _ollama_chat("http://192.168.7.9:11434", {"model": tag})
        assert info.value.reason == "ollama_model_missing"
        assert f'ollama pull {tag}' in info.value.message
        assert "192.168" not in info.value.message
