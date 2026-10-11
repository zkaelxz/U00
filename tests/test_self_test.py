"""tools/self_test.py: statuses, exit code, and that nothing secret reaches the report."""
import types

import pytest

from tools import self_test as st

FAKE_KEY = "sk-ant-api03-FAKEKEYVALUE1234567890abcdef"
PLAIN_SECRET = "hunter2-not-a-token-pattern"


def _stub_all(monkeypatch, **overrides):
    fakes = {
        "check_gpu_whisper": lambda model=st.DEFAULT_MODEL: (st.SKIPPED, "no CUDA GPU visible"),
        "check_torch_cuda": lambda: (st.SKIPPED, "torch is not installed"),
        "check_ffmpeg": lambda: (st.PASS, "ffmpeg ok"),
        "check_libass": lambda: (st.PASS, "libass ok"),
        "check_tts": lambda: (st.SKIPPED, "none"),
        "check_ocr": lambda: (st.SKIPPED, "none"),
        "check_ollama": lambda: (st.SKIPPED, "not running"),
        "check_memory": lambda: (st.PASS, "RAM"),
        "check_keys": lambda: (st.PASS, "keys"),
    }
    fakes.update(overrides)
    for name, fn in fakes.items():
        monkeypatch.setattr(st, name, fn)


def test_skipped_does_not_fail_the_run(monkeypatch, tmp_path):
    _stub_all(monkeypatch)
    out = tmp_path / "r.txt"
    assert st.main(["--out", str(out)]) == 0
    text = out.read_text(encoding="utf-8")
    assert "SKIPPED GPU faster-whisper" in text and "PASS    ffmpeg" in text
    assert "4 passed, 0 failed, 5 skipped" in text


def test_any_fail_gives_exit_code_1(monkeypatch, tmp_path):
    _stub_all(monkeypatch, check_libass=lambda: (st.FAIL, "no libass"))
    assert st.main(["--out", str(tmp_path / "r.txt")]) == 1


def test_a_crashing_check_is_a_fail_not_a_crash(monkeypatch, tmp_path):
    def boom():
        raise RuntimeError("driver exploded")
    _stub_all(monkeypatch, check_tts=boom)
    out = tmp_path / "r.txt"
    assert st.main(["--out", str(out)]) == 1
    assert "FAIL    TTS engines: RuntimeError: driver exploded" in out.read_text(encoding="utf-8")


def test_report_never_contains_key_values_paths_or_urls(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("BAIHE_CLAUDE_KEY", FAKE_KEY)
    monkeypatch.setenv("BAIHE_DEEPSEEK_KEY", PLAIN_SECRET)
    monkeypatch.setenv("BAIHE_OLLAMA_URL", "http://localhost:11434/secret-path")
    monkeypatch.setattr("services.settings_service.read_env_file", lambda env_path=None: {})
    leak = f"failed at C:\\Users\\owner\\models\\x.bin via https://host.example/a?token={FAKE_KEY} key {FAKE_KEY}"

    def leaky():
        raise RuntimeError(leak)
    _stub_all(monkeypatch, check_keys=st.check_keys, check_ocr=leaky)
    out = tmp_path / "r.txt"
    st.main(["--out", str(out)])
    printed = capsys.readouterr().out
    for text in (out.read_text(encoding="utf-8"), printed):
        assert FAKE_KEY not in text and PLAIN_SECRET not in text
        assert "FAKEKEYVALUE" not in text
        assert "C:\\Users" not in text and "owner\\models" not in text
        assert "host.example" not in text and "secret-path" not in text and "http" not in text
    assert "Claude: set" in printed and "DeepSeek: set" in printed


def test_key_check_reports_presence_only(monkeypatch):
    monkeypatch.setattr("services.settings_service.key_status",
                        lambda: {"claude": True, "gemini": False})
    status, reason = st.check_keys()
    assert status == st.PASS and reason == "Claude: set; Gemini: not set"


def test_gpu_whisper_skipped_without_gpu_or_model(monkeypatch):
    import diagnostics
    import whisper_models
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
    monkeypatch.setattr(whisper_models, "gpu_status", lambda: {"ctranslate2_cuda_devices": 0})
    assert st.check_gpu_whisper()[0] == st.SKIPPED
    monkeypatch.setattr(whisper_models, "gpu_status", lambda: {"ctranslate2_cuda_devices": 1})
    monkeypatch.setattr(whisper_models, "is_whisper_model_cached", lambda m: False)
    status, reason = st.check_gpu_whisper()
    assert status == st.SKIPPED and "not downloaded" in reason


def _fake_gpu_whisper(monkeypatch, device, gpu_error=None):
    import diagnostics
    import whisper_models
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
    monkeypatch.setattr(whisper_models, "gpu_status", lambda: {"ctranslate2_cuda_devices": 1})
    monkeypatch.setattr(whisper_models, "is_whisper_model_cached", lambda m: True)
    model = types.SimpleNamespace(transcribe=lambda path: (iter([object()]), None))
    monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: model)
    monkeypatch.setattr(whisper_models, "get_whisper_device_info",
                        lambda *a, **k: {"device": device, "compute_type": "float16", "gpu_error": gpu_error})


def test_gpu_whisper_pass_on_cuda(monkeypatch):
    _fake_gpu_whisper(monkeypatch, "cuda")
    status, reason = st.check_gpu_whisper()
    assert status == st.PASS and "1 segment" in reason


def test_gpu_whisper_fails_when_it_fell_back_to_cpu(monkeypatch):
    _fake_gpu_whisper(monkeypatch, "cpu", "CUDA out of memory")
    status, reason = st.check_gpu_whisper()
    assert status == st.FAIL and "CPU" in reason


def test_torch_cuda_statuses(monkeypatch):
    import diagnostics
    import diagnostics_torch
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
    monkeypatch.setattr(diagnostics, "installed_dist_version", lambda name: "2.9.0")
    monkeypatch.setattr(diagnostics_torch, "get_gpu_status",
                        lambda: {"available": True, "torch_cuda_version": "12.8"})
    assert st.check_torch_cuda()[0] == st.PASS
    monkeypatch.setattr(diagnostics_torch, "get_gpu_status",
                        lambda: {"available": False, "torch_cuda_version": None, "message": "no GPU"})
    assert st.check_torch_cuda()[0] == st.SKIPPED
    monkeypatch.setattr(diagnostics_torch, "get_gpu_status",
                        lambda: {"available": False, "message": "GPU info unavailable."})
    assert st.check_torch_cuda()[0] == st.FAIL


@pytest.mark.parametrize("ff,expected", [
    ({"found": False}, st.FAIL),
    ({"found": True, "libass": None, "version": "v"}, st.SKIPPED),
    ({"found": True, "libass": False, "version": "v"}, st.FAIL),
    ({"found": True, "libass": True, "version": "v"}, st.PASS),
])
def test_libass(monkeypatch, ff, expected):
    import diagnostics
    monkeypatch.setattr(diagnostics, "check_ffmpeg", lambda: ff)
    assert st.check_libass()[0] == expected


def test_tts_and_ocr_report_availability(monkeypatch):
    import diagnostics
    from services import dub_service
    monkeypatch.setattr(dub_service, "_engine_install_problem", lambda key: "missing")
    assert st.check_tts()[0] == st.SKIPPED
    monkeypatch.setattr(dub_service, "_engine_install_problem", lambda key: None if key == "omnivoice" else "missing")
    assert st.check_tts()[0] == st.PASS
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: name == "manga_ocr")
    status, reason = st.check_ocr()
    assert status == st.PASS and "manga-ocr" in reason


def test_ollama_states(monkeypatch):
    import memory_headroom
    from lib.http import FetchError
    from services import settings_service
    monkeypatch.setattr(settings_service, "ollama_endpoint_is_loopback", lambda: False)
    assert st.check_ollama()[0] == st.SKIPPED
    monkeypatch.setattr(settings_service, "ollama_endpoint_is_loopback", lambda: True)
    monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: None)
    monkeypatch.setattr(memory_headroom, "_ollama_json", lambda url: {"models": [{}, {}]})
    status, reason = st.check_ollama()
    assert status == st.PASS and "2 model" in reason

    def refused(url):
        raise FetchError()
    monkeypatch.setattr(memory_headroom, "_ollama_json", refused)
    assert st.check_ollama()[0] == st.SKIPPED
    monkeypatch.setattr(memory_headroom, "_ollama_json", lambda url: (_ for _ in ()).throw(ValueError("HTTP 500")))
    assert st.check_ollama()[0] == st.FAIL
