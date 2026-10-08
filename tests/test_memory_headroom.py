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
        monkeypatch.setattr(mh, "read_memory_mb", lambda memory: readings[memory])
    return _set


def test_off_reads_nothing(monkeypatch):
    monkeypatch.setattr(mh, "reserved_mb", lambda memory: 0.0)

    def boom(memory):
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
