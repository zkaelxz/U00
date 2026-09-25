"""
tests/test_local_model_defaults.py -- Step 5 (R3-lite): the local
translation default is qwen3:8b (14B opt-in, labelled), Ollama requests
have a timeout, and GPU model caches are emptied once a stage finishes.
"""
import sys
import types

import pytest

import core
import translate_engines as te


class TestOllamaDefaults:
    def test_default_model_is_qwen3_8b(self):
        assert te.OllamaEngine().model == "qwen3:8b"
        assert te.get_engine("ollama").model == "qwen3:8b"
        assert list(te.OLLAMA_MODELS)[0] == "qwen3:8b"

    def test_14b_is_opt_in_and_says_it_may_not_fit(self):
        assert "may not fit in 8 GB" in te.OLLAMA_MODELS["qwen2.5:14b"]
        assert te.get_engine("ollama", None, "qwen2.5:14b").model == "qwen2.5:14b"

    def test_translate_requests_have_a_timeout_and_use_the_default_model(self, monkeypatch):
        seen = {}

        class Resp:
            def raise_for_status(self):
                pass
            def json(self):
                return {"message": {"content": '{"1": "Hello."}'}}

        def fake_post(url, json=None, timeout=None):
            seen.update(model=json["model"], timeout=timeout)
            return Resp()
        monkeypatch.setattr("requests.post", fake_post)
        assert te.OllamaEngine().translate_batch(["你好"], {}) == ["Hello."]
        assert seen["model"] == "qwen3:8b"
        assert seen["timeout"] and seen["timeout"] > 0


@pytest.fixture
def loaded_models(monkeypatch):
    """Every model cache holding something, and a fake CUDA torch."""
    import asr_backend
    import forced_align
    monkeypatch.setitem(core._whisper_model_cache, ("medium", "cuda"), object())
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
        core.release_gpu_models()
        assert core._whisper_model_cache == {}
        assert loaded_models["asr"] == {} and loaded_models["aligner"] == {}
        assert loaded_models["emptied"] == [True]

    def test_does_not_import_torch_just_to_clear_it(self, monkeypatch):
        monkeypatch.delitem(sys.modules, "torch", raising=False)
        core.release_gpu_models()
        assert "torch" not in sys.modules

    def test_a_broken_cuda_doesnt_fail_the_stage(self, monkeypatch):
        torch = types.ModuleType("torch")
        def boom():
            raise RuntimeError("CUDA driver mismatch")
        torch.cuda = types.SimpleNamespace(is_available=boom, empty_cache=boom)
        monkeypatch.setitem(sys.modules, "torch", torch)
        core.release_gpu_models()  # must not raise


def test_caches_are_empty_after_the_transcription_stage_completes(loaded_models, monkeypatch):
    """The roadmap's exit condition: a stage loads a model, finishes, and
    nothing is left cached."""
    import background_jobs
    import tabs.workspace_tab as wt

    def fake_transcribe(audio_path, whisper_size, **kw):
        core._whisper_model_cache[(whisper_size, "cuda")] = object()  # what loading does
        return [{"start": 0.0, "end": 1.0, "text": "你好"}]
    monkeypatch.setattr(wt, "transcribe_for_timing", fake_transcribe)
    job_id = "test_release_after_transcribe"
    background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                      "error": None, "cancel_requested": False, "result": None}
    wt.run_transcribe_job(job_id, "a.wav", "medium", "zh", True, None, None, None, 5, 2000)
    assert background_jobs.get_status(job_id)["result"]["segments"]
    assert core._whisper_model_cache == {}
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
        core._whisper_model_cache["pyannote"] = object()
        raise RuntimeError("out of memory")
    monkeypatch.setattr(diarize, "diarize", failing)
    args = argparse.Namespace(id=did, hf_token="hf", num_speakers=0, overwrite_manual=False)
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        cli.cmd_diarize(args)
    assert core._whisper_model_cache == {}
