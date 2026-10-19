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
        "check_library_round_trip": lambda: (st.PASS, "round trip"),
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
    assert "5 passed, 0 failed, 5 skipped" in text


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


def _gpu_env(monkeypatch, folder="/models/tiny", cuda=1):
    import diagnostics
    import whisper_models
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
    monkeypatch.setattr(whisper_models, "gpu_status", lambda: {"ctranslate2_cuda_devices": cuda})
    monkeypatch.setattr(st, "_local_model_folder", lambda model: folder)
    released = []
    monkeypatch.setattr(whisper_models, "release_gpu_models", lambda: released.append(True))
    return released


def test_gpu_whisper_skipped_without_gpu(monkeypatch):
    _gpu_env(monkeypatch, cuda=0)
    assert st.check_gpu_whisper()[0] == st.SKIPPED


def test_gpu_whisper_never_loads_a_model_that_is_not_fully_local(monkeypatch):
    import whisper_models
    _gpu_env(monkeypatch, folder=None)

    def load(*a, **k):
        raise AssertionError("must not load, and so must not download")
    monkeypatch.setattr(whisper_models, "load_whisper_model", load)
    status, reason = st.check_gpu_whisper("large-v3")
    assert status == st.SKIPPED and "not fully downloaded" in reason and "/" not in reason


def test_local_model_folder_resolves_offline_and_exactly(monkeypatch, tmp_path):
    import model_pins
    from lib.ct2_repo import CT2_REQUIRED
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))

    def snapshot(repo, revision):
        folder = tmp_path / ("models--" + repo.replace("/", "--")) / "snapshots" / revision
        folder.mkdir(parents=True)
        for name in CT2_REQUIRED:
            (folder / name).write_bytes(b"x")
        return folder
    turbo = "mobiuslabsgmbh/faster-whisper-large-v3-turbo"
    on_disk = snapshot(turbo, model_pins.REVISIONS[turbo])
    # Another revision of large-v3 is not the tested one, and turbo is not large-v3.
    snapshot("Systran/faster-whisper-large-v3", "0" * 40)
    assert st._local_model_folder("large-v3") is None
    assert st._local_model_folder("large-v3-turbo") == str(on_disk)


def _fake_gpu_whisper(monkeypatch, device, gpu_error=None, transcribe=None):
    import whisper_models
    released = _gpu_env(monkeypatch)
    seen = {}
    model = types.SimpleNamespace(transcribe=transcribe or (lambda path: (iter([object()]), None)))

    def load(name, use_gpu=False, local_model_path=None, hf_token=None):
        seen["path"] = local_model_path
        return model
    monkeypatch.setattr(whisper_models, "load_whisper_model", load)
    monkeypatch.setattr(whisper_models, "get_whisper_device_info",
                        lambda *a, **k: {"device": device, "compute_type": "float16", "gpu_error": gpu_error})
    return released, seen


def test_gpu_whisper_pass_on_cuda_loads_by_local_folder_and_releases(monkeypatch):
    released, seen = _fake_gpu_whisper(monkeypatch, "cuda")
    status, reason = st.check_gpu_whisper()
    assert status == st.PASS and "1 segment" in reason
    assert seen["path"] == "/models/tiny" and released == [True]


def test_gpu_whisper_fails_when_it_fell_back_to_cpu(monkeypatch):
    released, _ = _fake_gpu_whisper(monkeypatch, "cpu", "CUDA out of memory")
    status, reason = st.check_gpu_whisper()
    assert status == st.FAIL and "CPU" in reason and released == [True]


def test_gpu_whisper_releases_the_model_even_if_transcription_crashes(monkeypatch):
    def boom(path):
        raise RuntimeError("cuda error")
    released, _ = _fake_gpu_whisper(monkeypatch, "cuda", transcribe=boom)
    with pytest.raises(RuntimeError):
        st.check_gpu_whisper()
    assert released == [True]


def test_gpu_whisper_leaves_ollama_and_the_data_folder_alone(monkeypatch, tmp_path):
    import ollama_unload
    import whisper_models
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path / "data"))
    unloads = []
    monkeypatch.setattr(ollama_unload, "_free_ollama_gpu_memory", lambda: unloads.append(True))
    _fake_gpu_whisper(monkeypatch, "cuda")

    def load(name, use_gpu=False, local_model_path=None, hf_token=None):
        ollama_unload.prepare_gpu_for_transcription(use_gpu)  # what the real loader does first
        return types.SimpleNamespace(transcribe=lambda path: (iter([]), None))
    monkeypatch.setattr(whisper_models, "load_whisper_model", load)
    assert st.check_gpu_whisper()[0] == st.PASS
    assert unloads == [] and not (tmp_path / "data").exists()
    assert getattr(ollama_unload._state, "scope", None) is None  # scope restored


def test_memory_is_read_first_before_anything_touches_the_gpu():
    names = [name for name, _ in st._checks("tiny")]
    assert names[0] == "VRAM / RAM headroom"
    assert names.index("VRAM / RAM headroom") < names.index("torch / CUDA")


def test_torch_cuda_statuses(monkeypatch):
    import dependency_updates
    import diagnostics
    import diagnostics_torch
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
    monkeypatch.setattr(dependency_updates, "installed_dist_version", lambda name: "2.9.0")
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


def _run_out(tmp_path, target, monkeypatch):
    _stub_all(monkeypatch)
    return st.main(["--out", str(target)])


def test_out_refuses_a_foreign_file_and_leaves_it_untouched(monkeypatch, tmp_path, capsys):
    env = tmp_path / ".env"
    env.write_bytes(b"BAIHE_CLAUDE_KEY=abc\n")
    assert _run_out(tmp_path, env, monkeypatch) == 2
    assert env.read_bytes() == b"BAIHE_CLAUDE_KEY=abc\n"
    assert str(tmp_path) not in capsys.readouterr().out


def test_out_overwrites_a_previous_report(monkeypatch, tmp_path):
    out = tmp_path / "r.txt"
    out.write_text(st.REPORT_TITLE + "\nold\n", encoding="utf-8")
    assert _run_out(tmp_path, out, monkeypatch) == 0
    assert "old" not in out.read_text(encoding="utf-8")


def test_out_refuses_a_directory(monkeypatch, tmp_path):
    assert _run_out(tmp_path, tmp_path, monkeypatch) == 2


def test_out_refuses_a_symlink_even_to_a_previous_report(monkeypatch, tmp_path):
    target = tmp_path / "real.txt"
    target.write_text(st.REPORT_TITLE + "\nold\n", encoding="utf-8")
    link = tmp_path / "link.txt"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not available")
    assert _run_out(tmp_path, link, monkeypatch) == 2
    assert target.read_text(encoding="utf-8") == st.REPORT_TITLE + "\nold\n"


def test_report_is_written_before_it_is_printed_and_console_errors_are_replaced(monkeypatch, tmp_path):
    import io
    _stub_all(monkeypatch, check_keys=lambda: (st.PASS, "caf\u00e9 \u2713"))
    buf = io.BytesIO()
    console = io.TextIOWrapper(buf, encoding="cp1252", errors="strict")
    monkeypatch.setattr(st.sys, "stdout", console)
    out = tmp_path / "r.txt"
    assert st.main(["--out", str(out)]) == 0
    console.flush()
    assert "\u2713" in out.read_text(encoding="utf-8")
    assert b"Report written" in buf.getvalue()


def test_ollama_request_bypasses_environment_proxies(monkeypatch):
    import memory_headroom
    seen = {}

    def fake_get(url, **kw):
        seen.update(kw)
        return types.SimpleNamespace(status=200, body=b"{}")
    monkeypatch.setattr(memory_headroom.http, "get", fake_get)
    memory_headroom._ollama_json("http://localhost:11434/api/tags")
    assert seen["trust_env"] is False and seen["timeout"] == 3


def test_library_round_trip_uses_a_throwaway_library_and_restores_the_real_one(isolated_db):
    real = isolated_db.LIBRARY_DIR
    real_id = isolated_db.create_drama(title_en="Real title", source_language="zh")
    status, reason = st.check_library_round_trip()
    assert status == st.PASS, reason
    assert isolated_db.LIBRARY_DIR == real
    assert [d["id"] for d in isolated_db.list_dramas()] == [real_id]


def test_library_round_trip_fails_when_the_export_loses_the_line(monkeypatch, isolated_db):
    monkeypatch.setattr("services.export_service.generate_subtitle_export",
                        lambda *a, **k: {"text": ""})
    status, _ = st.check_library_round_trip()
    assert status == st.FAIL
    assert isolated_db.LIBRARY_DIR
