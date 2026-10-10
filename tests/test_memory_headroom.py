"""Keep-free VRAM/RAM headroom: off by default, refuses a load that would eat
the reserve before anything is allocated, skips (never blocks) when memory
can't be read, and never applies to cloud or remote engines."""

import pytest

import memory_headroom as mh
from services import loaded_models_service, settings_service, vram_service
from services.service_errors import InvalidInputError

GB = 1024


@pytest.fixture
def reserve(monkeypatch):
    """Sets the reserves (GB) and the memory readings (free MB) the check sees."""
    def _set(vram=0.0, ram=0.0, vram_free=None, ram_free=None):
        monkeypatch.setattr(mh, "reserved_mb", lambda memory: (vram if memory == "vram" else ram) * GB)
        readings = {"vram": None if vram_free is None else (32 * GB, vram_free),
                    "ram": None if ram_free is None else (32 * GB, ram_free)}
        monkeypatch.setattr(mh, "read_memory_mb", lambda memory, at_load=False: readings[memory])
    return _set


def test_off_reads_nothing(monkeypatch):
    monkeypatch.setattr(mh, "reserved_mb", lambda memory: 0.0)

    def boom(memory, at_load=False):
        raise AssertionError("memory must not be read while the reserve is 0")
    monkeypatch.setattr(mh, "read_memory_mb", boom)
    mh.check("whisper", "large-v3", True)
    mh.check_need("anything", 10 ** 6, "ram")


def test_refuses_when_load_would_break_the_reserve(reserve):
    reserve(vram=4, vram_free=8 * GB)
    with pytest.raises(mh.HeadroomError) as ei:
        mh.check("whisper", "large-v3", True)  # needs ~4.7 GB: 8 - 4.7 < 4
    text = str(ei.value)
    assert "Whisper large-v3" in text and "8.0 GB is free" in text and "4.0 GB is kept free" in text
    assert "estimate" in text and "lower the setting" in text


def test_allows_when_the_reserve_still_holds(reserve):
    reserve(vram=2, vram_free=12 * GB)
    mh.check("whisper", "large-v3", True)


def test_cpu_load_checks_ram_not_vram(reserve):
    reserve(vram=30, ram=4, vram_free=1 * GB, ram_free=6 * GB)
    with pytest.raises(mh.HeadroomError) as ei:
        mh.check("whisper", "large-v3", False)  # ~3.7 GB of 6 free leaves < 4
    assert "RAM" in str(ei.value)
    mh.check("whisper", "tiny", False)


def test_unreadable_memory_skips_and_logs_once(reserve, monkeypatch):
    reserve(vram=4)  # no readings
    monkeypatch.setattr(mh, "_warned_unreadable", set())
    logged = []

    class _Log:
        def warning(self, msg):
            logged.append(msg)
    import applog
    monkeypatch.setattr(applog, "get_logger", lambda: _Log())
    mh.check("whisper", "large-v3", True)
    mh.check("whisper", "large-v3", True)
    assert len(logged) == 1 and "not being enforced" in logged[0]


def test_unknown_model_is_never_blocked(reserve):
    reserve(vram=31, vram_free=1 * GB)
    mh.check("whisper", "/models/my-finetune", True)
    mh.check("qwen_asr", "9B", True)


@pytest.mark.parametrize("kind,name", [("whisper", "base.en"), ("qwen_asr", "1.7B"),
                                       ("aligner", "qwen3"), ("separation", "separator")])
def test_estimates_exist_for_the_loaders(kind, name):
    vram, ram = mh.estimate_mb(kind, name)
    assert vram > 0 and ram > 0


def test_before_load_frees_ollama_first_and_skips_cached(monkeypatch, reserve):
    reserve(vram=31, vram_free=1 * GB)
    import ollama_unload
    calls = []
    monkeypatch.setattr(ollama_unload, "prepare_gpu_for_transcription", lambda g: calls.append(g))
    mh.before_load("whisper", "large-v3", True, cached=True)  # cached: no check, no error
    assert calls == [True]
    with pytest.raises(mh.HeadroomError):
        mh.before_load("whisper", "large-v3", True, cached=False)
    assert calls == [True, True]


def test_whisper_loader_refuses_before_importing_the_model(monkeypatch, reserve):
    import core
    reserve(vram=31, vram_free=2 * GB)
    monkeypatch.setattr(core, "_whisper_model_cache", {})
    import ollama_unload
    monkeypatch.setattr(ollama_unload, "prepare_gpu_for_transcription", lambda g: None)
    with pytest.raises(mh.HeadroomError):
        core.load_whisper_model("large-v3", use_gpu=True)


def test_vram_service_check_fits_honours_the_reserve(reserve):
    # OmniVoice needs about 2 GB: 7 GB free leaves 5 GB.
    reserve(vram=4)
    vram_service.check_fits("OmniVoice", free_mb=7 * GB)
    reserve(vram=6)
    with pytest.raises(mh.HeadroomError):
        vram_service.check_fits("OmniVoice", free_mb=7 * GB)


def test_vram_service_unchanged_when_off(reserve):
    reserve()
    vram_service.check_fits("OmniVoice", free_mb=3 * GB)
    with pytest.raises(vram_service.InsufficientVramError):
        vram_service.check_fits("OmniVoice", free_mb=100)


class TestOllama:
    def _serve(self, monkeypatch, loaded=(), size_gb=8.0):
        calls = []

        def fake(base, model):
            calls.append((base, model))
            return None if model in loaded else size_gb * GB
        monkeypatch.setattr(mh, "_ollama_size_mb_if_not_loaded", fake)
        return calls

    def test_refuses_a_model_that_would_eat_the_reserve(self, monkeypatch, reserve):
        reserve(vram=6, vram_free=12 * GB)
        self._serve(monkeypatch)
        with pytest.raises(mh.HeadroomError) as ei:
            mh.check_ollama("http://localhost:11434", "gemma4:12b")
        assert "Ollama model gemma4:12b" in str(ei.value)

    def test_cloud_tags_and_remote_servers_are_never_checked(self, monkeypatch, reserve):
        reserve(vram=31, vram_free=1 * GB)
        calls = self._serve(monkeypatch)
        mh.check_ollama("http://localhost:11434", "gpt-oss:120b-cloud")
        mh.check_ollama("http://localhost:11434", "qwen3-coder:cloud")
        mh.check_ollama("http://192.168.1.5:11434", "gemma4:12b")
        assert calls == []

    def test_off_makes_no_request(self, monkeypatch, reserve):
        reserve()
        calls = self._serve(monkeypatch)
        mh.check_ollama("http://localhost:11434", "gemma4:12b")
        assert calls == []

    def test_already_loaded_model_needs_nothing(self, monkeypatch, reserve):
        reserve(vram=31, vram_free=1 * GB)
        self._serve(monkeypatch, loaded=("gemma4:12b",))
        mh.check_ollama("http://localhost:11434", "gemma4:12b")

    def test_engine_checks_once_per_instance(self, monkeypatch):
        from engine_backends.local import OllamaEngine
        seen = []
        monkeypatch.setattr(mh, "check_ollama", lambda base, model: seen.append((base, model)))
        engine = OllamaEngine(model="gemma4:12b")
        monkeypatch.setattr("engine_backends.local._ollama_chat",
                            lambda base, payload: {"message": {"content": "{}"}})
        for _ in range(2):
            try:
                engine.translate_batch(["你好"], {"line_ids": [1]})
            except Exception:
                pass  # the reply is not the point; the check is
        assert seen == [("http://localhost:11434", "gemma4:12b")]


class TestSettings:
    def test_default_is_off(self, isolated_db):
        prefs = settings_service.get_settings_overview()["preferences"]
        assert prefs["keep_free_vram_gb"] == 0 and prefs["keep_free_ram_gb"] == 0

    def test_round_trip(self, isolated_db, monkeypatch):
        monkeypatch.setattr(mh, "total_mb", lambda memory: 32 * GB)
        out = settings_service.set_settings({"keep_free_vram_gb": 4, "keep_free_ram_gb": 6.25})
        assert out["preferences"]["keep_free_vram_gb"] == 4.0
        assert out["preferences"]["keep_free_ram_gb"] == 6.2
        assert mh.reserved_mb("vram") == 4 * GB

    @pytest.mark.parametrize("bad", [-1, True, "4", float("nan"), 5000])
    def test_rejects_bad_values(self, isolated_db, monkeypatch, bad):
        monkeypatch.setattr(mh, "total_mb", lambda memory: 32 * GB)
        with pytest.raises(InvalidInputError):
            settings_service.set_settings({"keep_free_vram_gb": bad})

    def test_rejects_more_than_the_detected_total(self, isolated_db, monkeypatch):
        monkeypatch.setattr(mh, "total_mb", lambda memory: 32 * GB)
        with pytest.raises(InvalidInputError) as ei:
            settings_service.set_settings({"keep_free_ram_gb": 33})
        assert "32.0 GB" in str(ei.value)

    def test_accepts_any_value_when_the_total_is_unknown(self, isolated_db, monkeypatch):
        monkeypatch.setattr(mh, "total_mb", lambda memory: None)
        settings_service.set_settings({"keep_free_vram_gb": 12})


def test_loaded_models_reports_memory_and_reserve(monkeypatch, reserve):
    reserve(vram=4, vram_free=10 * GB)
    row = loaded_models_service._memory_row()
    assert row["vram"]["state"] == "ok" and row["vram"]["reserved_bytes"] == 4 * 1024 ** 3
    assert row["ram"]["state"] == "unknown" and row["ram"]["free_bytes"] is None
    assert not any("/" in str(v) for v in row["vram"].values() if isinstance(v, str) and v != "ok")


class _TrippedTorch:
    """A torch stand-in whose every CUDA call fails the test."""
    class cuda:
        @staticmethod
        def is_available():
            raise AssertionError("torch.cuda touched")
        is_initialized = mem_get_info = is_available


def test_status_and_settings_save_never_touch_torch(monkeypatch, isolated_db):
    import sys
    import diagnostics_torch
    monkeypatch.setitem(sys.modules, "torch", _TrippedTorch)
    monkeypatch.setattr(diagnostics_torch, "external_gpu_load",
                        lambda: {"memory_total_mb": 8 * GB, "memory_free_mb": 6 * GB})
    assert loaded_models_service._memory_row()["vram"]["free_bytes"] == 6 * GB * mh.MB
    settings_service.set_settings({"keep_free_vram_gb": 1})


def test_load_time_check_uses_torch_only_once_cuda_is_initialized(monkeypatch):
    import sys

    class Torch:
        class cuda:
            initialized = False
            is_available = staticmethod(lambda: True)
            is_initialized = staticmethod(lambda: Torch.cuda.initialized)
            mem_get_info = staticmethod(lambda: (3 * GB * mh.MB, 8 * GB * mh.MB))
    monkeypatch.setitem(sys.modules, "torch", Torch)
    monkeypatch.setattr(mh, "reserved_mb", lambda memory: 4 * GB)
    import diagnostics_torch
    monkeypatch.setattr(diagnostics_torch, "external_gpu_load", lambda: None)
    assert mh.read_memory_mb("vram", at_load=True) is None
    Torch.cuda.initialized = True
    assert mh.read_memory_mb("vram", at_load=True) == (8 * GB, 3 * GB)


def test_whisper_offline_folder_uses_its_model_name_never_the_path(reserve):
    reserve(vram=4, vram_free=8 * GB)
    with pytest.raises(mh.HeadroomError) as ei:
        mh.check("whisper", "D:\\models\\faster-whisper-large-v3", True)
    assert "Whisper large-v3" in str(ei.value) and "models" not in str(ei.value)
    mh.check("whisper", "/srv/my-finetune", True)  # unknown folder: skipped, not guessed


@pytest.mark.parametrize("torch_cuda, refused", [(True, True), (False, False)])
def test_separation_none_follows_what_the_library_would_pick(monkeypatch, reserve, torch_cuda, refused):
    import sys, types
    reserve(vram=4, vram_free=1 * GB, ram=0)
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: torch_cuda)))
    if refused:
        with pytest.raises(mh.HeadroomError):
            mh.check_separation(None)
    else:
        mh.check_separation(None)
    monkeypatch.delitem(sys.modules, "torch")
    mh.check_separation(None)  # device unknown: skipped
    with pytest.raises(mh.HeadroomError):
        mh.check_separation(True)


def test_separation_on_cpu_checks_ram_not_vram(monkeypatch, reserve):
    import sys, types
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: False)))
    reserve(vram=4, vram_free=1 * GB, ram=4, ram_free=64 * GB)
    mh.check_separation(None)  # plenty of RAM; the tight VRAM is irrelevant on CPU
    reserve(vram=0, vram_free=64 * GB, ram=4, ram_free=1 * GB)
    with pytest.raises(mh.HeadroomError):
        mh.check_separation(None)


class TestRefusalStopsTheRun:
    def test_headroom_error_is_not_a_fallback_error(self):
        from engine_backends.fallback import is_fallback_error
        assert not is_fallback_error(mh.HeadroomError("no room"))

    def test_multi_batch_run_stops_after_one_attempt(self, monkeypatch):
        import translate_engines as te
        from core import Line
        monkeypatch.setattr("engine_backends.shared._cancellable_sleep",
                            lambda *a, **k: (_ for _ in ()).throw(AssertionError("must not retry")))
        lines = [Line(idx=i, start=0, end=1, zh=f"l{i}") for i in range(8)]

        class Engine:
            supports_reference = False
            calls = 0

            def translate_batch(self, zh_lines, context):
                Engine.calls += 1
                raise mh.HeadroomError("Not loading the Ollama model x: no room.")

        _, errors = te.translate_lines_with_engine(lines, Engine(), {}, batch_size=2)
        assert Engine.calls == 1
        assert len(errors) == 1 and "no room" in errors[0]["error"]
        assert not any(l.en for l in lines)


def test_reading_the_reserve_never_creates_a_library(tmp_path, monkeypatch):
    import db
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "library" / "library.db"))
    assert mh.reserved_mb("vram") == 0.0
    assert not (tmp_path / "library").exists()
