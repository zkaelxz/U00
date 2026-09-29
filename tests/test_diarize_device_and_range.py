"""
tests/test_diarize_device_and_range.py -- Step 101: pyannote placed on the
GPU when use_gpu is on and CUDA is available, and the device actually used
is reported.

torch, pyannote.audio and soundfile are all faked in sys.modules, so these
run on the core-only CI install with no GPU.
"""
import argparse
import contextlib
import io
import os
import queue
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import diarize


class _Annotation:
    def itertracks(self, yield_label=True):
        yield types.SimpleNamespace(start=0.0, end=1.0), None, "SPEAKER_00"


class _FakePipeline:
    def __init__(self):
        self.moved_to = []
        self.call_kwargs = None

    def to(self, device):
        self.moved_to.append(device)
        return self

    def __call__(self, audio, **kwargs):
        self.call_kwargs = kwargs
        return _Annotation()


def _install_fakes(monkeypatch, cuda_available, move_raises=False):
    pipeline = _FakePipeline()
    if move_raises:
        def _boom(device):
            raise RuntimeError("CUDA out of memory")
        pipeline.to = _boom

    fake_audio = types.ModuleType("pyannote.audio")
    fake_audio.Pipeline = types.SimpleNamespace(
        from_pretrained=lambda model, **kw: pipeline)
    parent = types.ModuleType("pyannote")
    parent.audio = fake_audio
    monkeypatch.setitem(sys.modules, "pyannote", parent)
    monkeypatch.setitem(sys.modules, "pyannote.audio", fake_audio)

    fake_torch = types.ModuleType("torch")
    fake_torch.cuda = types.SimpleNamespace(is_available=lambda: cuda_available)
    fake_torch.device = lambda name: f"device({name})"
    fake_torch.from_numpy = lambda arr: arr
    monkeypatch.setitem(sys.modules, "torch", fake_torch)

    fake_sf = types.ModuleType("soundfile")
    fake_sf.read = lambda path, dtype="float32", always_2d=True: (
        types.SimpleNamespace(T="waveform"), 16000)
    monkeypatch.setitem(sys.modules, "soundfile", fake_sf)
    return pipeline


class TestStep101DevicePlacement:
    def test_moves_pipeline_to_cuda_when_available_and_use_gpu_on(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=True)
        info = {}
        diarize.diarize("/fake.wav", "hf", use_gpu=True, run_info=info)
        assert pipeline.moved_to == ["device(cuda)"]
        assert info == {"device": "cuda"}

    def test_stays_on_cpu_when_use_gpu_is_off(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=True)
        info = {}
        diarize.diarize("/fake.wav", "hf", use_gpu=False, run_info=info)
        assert pipeline.moved_to == []
        assert info == {"device": "cpu"}

    def test_stays_on_cpu_when_cuda_is_unavailable(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=False)
        info = {}
        diarize.diarize("/fake.wav", "hf", use_gpu=True, run_info=info)
        assert pipeline.moved_to == []
        assert info == {"device": "cpu"}

    def test_a_failed_move_falls_back_to_cpu_and_still_runs(self, monkeypatch):
        _install_fakes(monkeypatch, cuda_available=True, move_raises=True)
        info = {}
        segments = diarize.diarize("/fake.wav", "hf", use_gpu=True, run_info=info)
        assert segments == [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}]
        assert info == {"device": "cpu"}

    def test_worker_reports_the_device_in_its_result(self, monkeypatch):
        _install_fakes(monkeypatch, cuda_available=True)
        q = queue.Queue()
        diarize.diarize_subprocess_worker("/fake.wav", "hf", None, {"use_gpu": True}, q)
        status, payload = q.get_nowait()
        assert status == "ok"
        assert payload["device"] == "cuda"

    def test_worker_keeps_the_old_four_argument_shape(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=True)
        q = queue.Queue()
        diarize.diarize_subprocess_worker("/fake.wav", "hf", 2, q)
        status, payload = q.get_nowait()
        assert status == "ok" and payload["device"] == "cpu"
        assert pipeline.call_kwargs == {"num_speakers": 2}


def test_turns_file_round_trips_the_device(tmp_path):
    diarize.save_turns(str(tmp_path), [], device="cuda")
    assert diarize.load_last_run_info(str(tmp_path)) == {"device": "cuda"}


@pytest.fixture
def client(isolated_db):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False,
                      headers={"X-Baihe-Local": "1"})


def _drama_with_audio(isolated_db):
    did = isolated_db.create_drama(title_en="D", audio_filename="audio.wav")
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, "audio.wav"), "wb").close()
    return did, ddir


class TestServiceAndApi:
    def _capture_start(self, monkeypatch):
        import background_jobs
        calls = []

        def fake_start(job_id, target, args=(), gpu_touching=False, description=None,
                       on_done=None):
            calls.append({"args": args, "on_done": on_done})
            return True
        monkeypatch.setattr(background_jobs, "start_process_job", fake_start)
        return calls

    def test_service_passes_use_gpu_to_the_worker(self, isolated_db, monkeypatch):
        from services import diarization_service, settings_service
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf")
        monkeypatch.setattr(settings_service, "get_use_gpu", lambda: True)
        did, ddir = _drama_with_audio(isolated_db)
        calls = self._capture_start(monkeypatch)
        diarization_service.start_diarization_run(did, expected_speakers=2)
        assert calls[0]["args"] == (os.path.join(ddir, "audio.wav"), "hf", 2, {"use_gpu": True})

    def test_on_done_saves_the_device_and_config_reports_it(self, isolated_db, monkeypatch):
        from services import diarization_service, settings_service
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf")
        did, _ = _drama_with_audio(isolated_db)
        calls = self._capture_start(monkeypatch)
        diarization_service.start_diarization_run(did)
        calls[0]["on_done"]("job", {"segments": [], "model": "m", "embeddings": {},
                                    "device": "cuda"})
        assert diarization_service.get_diarization_config(did)["last_device"] == "cuda"


class TestCliParity:
    def _run(self, did):
        import cli
        args = argparse.Namespace(id=did, hf_token="hf", num_speakers=0, overwrite_manual=False)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli.cmd_diarize(args)
        return out.getvalue()

    def test_cli_passes_use_gpu_and_saves_the_device(self, isolated_db, monkeypatch):
        import cli
        from services import settings_service
        from core import Line
        monkeypatch.setattr(settings_service, "get_use_gpu", lambda: True)
        monkeypatch.setattr(cli, "release_gpu_models", lambda: None)
        did, ddir = _drama_with_audio(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="a")])
        seen = {}

        def fake(audio_path, hf_token, **kw):
            seen.update(kw)
            kw["run_info"]["device"] = "cuda"
            return [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}], "m", {}
        monkeypatch.setattr(diarize, "diarize", fake)
        out = self._run(did)
        assert seen["use_gpu"] is True
        assert "on cuda" in out
        assert diarize.load_last_run_info(ddir) == {"device": "cuda"}
