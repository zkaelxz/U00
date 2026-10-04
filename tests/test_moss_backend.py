"""Step 104: asr_backend's backend seam (BACKENDS / get_backend) and the
experimental MossTranscribeDiarizeBackend, against fake torch, transformers
and moss_transcribe_diarize modules -- no model, GPU or network."""
import sys
import types
from dataclasses import dataclass

import pytest

import asr_backend as ab


@dataclass
class Seg:
    start: float
    end: float
    speaker: str
    text: str


class FakeDevice:
    def __init__(self, spec):
        self.type = "cuda" if str(spec).startswith("cuda") else "cpu"


@pytest.fixture
def fakes(monkeypatch):
    """Installs fake modules; returns a dict recording what was called."""
    rec = {"loads": 0, "generate": [], "cuda": False, "tokens": 10, "text": ""}

    torch = types.ModuleType("torch")
    torch.bfloat16, torch.float32 = "bf16", "f32"
    torch.device = FakeDevice
    torch.cuda = types.SimpleNamespace(is_available=lambda: rec["cuda"])

    class FakeModel:
        def to(self, *a, **k):
            if a and isinstance(a[0], FakeDevice):
                rec["model_device"] = a[0].type
            return self

        def eval(self):
            return self

    transformers = types.ModuleType("transformers")

    class AutoModelForCausalLM:
        @staticmethod
        def from_pretrained(model_id, **kw):
            rec["loads"] += 1
            rec["model_id"], rec["trust"] = model_id, kw.get("trust_remote_code")
            return FakeModel()

    class AutoProcessor:
        @staticmethod
        def from_pretrained(model_id, **kw):
            return object()
    transformers.AutoModelForCausalLM = AutoModelForCausalLM
    transformers.AutoProcessor = AutoProcessor

    moss = types.ModuleType("moss_transcribe_diarize")
    moss.parse_transcript = lambda text: rec["segments"]
    utils = types.ModuleType("moss_transcribe_diarize.inference_utils")
    utils.build_transcription_messages = lambda path: [{"audio": path}]

    def generate_transcription(model, processor, messages, **kw):
        rec["generate"].append((messages, kw))
        return {"text": rec["text"], "prompt_len": 1, "generated_tokens": rec["tokens"]}
    utils.generate_transcription = generate_transcription
    moss.inference_utils = utils

    for name, mod in (("torch", torch), ("transformers", transformers),
                      ("moss_transcribe_diarize", moss),
                      ("moss_transcribe_diarize.inference_utils", utils)):
        monkeypatch.setitem(sys.modules, name, mod)
    monkeypatch.setattr(ab, "_asr_model_cache", {})
    rec["segments"] = []
    return rec


def test_registry_lists_every_backend_and_marks_moss_experimental():
    assert set(ab.BACKENDS) == {"whisper", "qwen3_asr", "qwen3_asr_vad", "moss_td"}
    assert ab.EXPERIMENTAL_BACKENDS == {"moss_td"}
    assert isinstance(ab.get_backend("whisper"), ab.WhisperBackend)
    assert isinstance(ab.get_backend("moss_td"), ab.MossTranscribeDiarizeBackend)
    with pytest.raises(ValueError, match="Unknown"):
        ab.get_backend("nope")


def test_transcribe_returns_segments_with_speakers(fakes):
    fakes["segments"] = [Seg(0.0, 1.5, "S01", " 你好 "), Seg(1.5, 3.0, "S02", "再见"),
                         Seg(3.0, 3.0, "S01", "zero length"), Seg(3.0, 4.0, "S01", "   ")]
    info = {}
    out = ab.MossTranscribeDiarizeBackend().transcribe("/a.wav", "zh", run_info=info)
    assert out == [{"start": 0.0, "end": 1.5, "text": "你好", "speaker": "S01"},
                   {"start": 1.5, "end": 3.0, "text": "再见", "speaker": "S02"}]
    assert info == {"device": "cpu", "truncated": False}
    messages, kw = fakes["generate"][0]
    assert messages == [{"audio": "/a.wav"}]
    assert kw["do_sample"] is False and kw["max_new_tokens"] == ab.MOSS_MAX_NEW_TOKENS
    assert fakes["model_id"] == ab.MOSS_MODEL_ID and fakes["trust"] is True


def test_gpu_is_used_only_when_asked_and_available(fakes):
    fakes["cuda"] = True
    info = {}
    ab.MossTranscribeDiarizeBackend().transcribe("/a.wav", use_gpu=False, run_info=info)
    assert info["device"] == "cpu"
    info = {}
    ab.MossTranscribeDiarizeBackend().transcribe("/a.wav", use_gpu=True, run_info=info)
    assert info["device"] == "cuda" and fakes["model_device"] == "cuda"
    fakes["cuda"] = False
    ab._asr_model_cache.clear()
    info = {}
    ab.MossTranscribeDiarizeBackend().transcribe("/a.wav", use_gpu=True, run_info=info)
    assert info["device"] == "cpu"


def test_model_is_loaded_once_and_cached_for_release_gpu_models(fakes):
    backend = ab.MossTranscribeDiarizeBackend()
    backend.transcribe("/a.wav")
    backend.transcribe("/b.wav")
    assert fakes["loads"] == 1
    assert "moss_cpu" in ab._asr_model_cache


def test_hitting_the_token_limit_is_reported_as_truncated(fakes):
    fakes["tokens"] = ab.MOSS_MAX_NEW_TOKENS
    info = {}
    ab.MossTranscribeDiarizeBackend().transcribe("/a.wav", run_info=info)
    assert info["truncated"] is True


def test_missing_package_raises_an_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "moss_transcribe_diarize", None)
    monkeypatch.setattr(ab, "_asr_model_cache", {})
    with pytest.raises(ImportError):
        ab.MossTranscribeDiarizeBackend().transcribe("/a.wav")


def test_toggle_defaults_off_and_round_trips(isolated_db):
    from services import asr_options_service as svc
    assert svc.get_moss_experimental() is False
    assert svc.set_asr_options(moss_experimental=True)["moss_experimental"] is True
    assert svc.get_moss_experimental() is True
    from services.service_errors import InvalidInputError
    with pytest.raises(InvalidInputError):
        svc.set_asr_options(moss_experimental="yes")


def test_registered_as_an_optional_dependency_but_not_offered_for_pip_install():
    import diagnostics
    assert diagnostics.OPTIONAL_DEPENDENCIES["moss-transcribe-diarize"][0] == "moss_transcribe_diarize"
    assert "moss-transcribe-diarize" in diagnostics.NOT_OFFERED_FOR_INSTALL


def test_model_and_processor_load_a_pinned_hub_revision(fakes, monkeypatch):
    """trust_remote_code runs the Hub repo's Python: it must be a pinned revision."""
    seen = []
    import transformers
    real_model = transformers.AutoModelForCausalLM.from_pretrained
    monkeypatch.setattr(transformers.AutoModelForCausalLM, "from_pretrained",
                        staticmethod(lambda mid, **kw: seen.append(kw) or real_model(mid, **kw)))
    monkeypatch.setattr(transformers.AutoProcessor, "from_pretrained",
                        staticmethod(lambda mid, **kw: seen.append(kw) or object()))
    ab.MossTranscribeDiarizeBackend().transcribe("/a.wav")
    assert len(seen) == 2
    assert all(kw["revision"] == ab.MOSS_HF_REVISION for kw in seen)
    assert len(ab.MOSS_HF_REVISION) == 40


def test_diagnostics_links_moss_to_its_repository_never_pypi():
    import diagnostics
    url = diagnostics.package_source_url("moss-transcribe-diarize")
    assert url == "https://github.com/OpenMOSS/MOSS-Transcribe-Diarize"
    assert diagnostics.package_source_url("jieba") == diagnostics.pypi_url("jieba")


def test_version_check_never_asks_pypi_about_experimental_entries(monkeypatch):
    import diagnostics
    asked = []
    monkeypatch.setattr(diagnostics, "get_latest_pypi_version",
                        lambda name, timeout=10.0: asked.append(name) or "9.9")
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda name: "0.1.0")
    out = diagnostics.check_dependency_versions({
        "moss-transcribe-diarize": {"installed": True, "tier": "experimental"},
        "jieba": {"installed": True, "tier": "feature"}})
    assert asked == ["jieba"] and "moss-transcribe-diarize" not in out
