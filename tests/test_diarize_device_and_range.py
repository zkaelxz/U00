"""
tests/test_diarize_device_and_range.py -- Step 101 (pyannote placed on the
GPU when use_gpu is on and CUDA is available, and the device actually used
is reported) and Step 105 (a min/max speaker-count range passed through to
pyannote's own min_speakers/max_speakers).

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
from tests.saved_settings import patch_setting

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


def _drain(q):
    items = []
    while True:
        try:
            items.append(q.get_nowait())
        except queue.Empty:
            return items


def _no_hook(kwargs):
    return {k: v for k, v in kwargs.items() if k != "hook"}


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
        assert info == {"device": "cpu", "fell_back_to_cpu": True, "fallback_kind": "placement",
                        "fallback_reason": "Couldn't move the speaker model to the GPU"}

    def test_worker_reports_the_device_in_its_result(self, monkeypatch):
        _install_fakes(monkeypatch, cuda_available=True)
        q = queue.Queue()
        diarize.diarize_subprocess_worker("/fake.wav", "hf", None, {"use_gpu": True}, q)
        status, payload = _drain(q)[-1]
        assert status == "ok"
        assert payload["device"] == "cuda"

    def test_worker_keeps_the_old_four_argument_shape(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=True)
        q = queue.Queue()
        diarize.diarize_subprocess_worker("/fake.wav", "hf", 2, q)
        items = _drain(q)
        status, payload = items[-1]
        assert status == "ok" and payload["device"] == "cpu"
        assert _no_hook(pipeline.call_kwargs) == {"num_speakers": 2}


class TestStep105SpeakerRange:
    def test_range_is_passed_through_as_min_and_max_speakers(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=False)
        diarize.diarize("/fake.wav", "hf", min_speakers=2, max_speakers=4)
        assert pipeline.call_kwargs == {"num_speakers": None, "min_speakers": 2, "max_speakers": 4}

    def test_one_sided_range_passes_only_that_bound(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=False)
        diarize.diarize("/fake.wav", "hf", max_speakers=5)
        assert pipeline.call_kwargs == {"num_speakers": None, "max_speakers": 5}

    def test_exact_count_still_works_exactly_as_before(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=False)
        diarize.diarize("/fake.wav", "hf", num_speakers=3)
        assert pipeline.call_kwargs == {"num_speakers": 3}

    def test_auto_detect_still_passes_num_speakers_none(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=False)
        diarize.diarize("/fake.wav", "hf")
        assert pipeline.call_kwargs == {"num_speakers": None}

    def test_worker_passes_the_range_through(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=False)
        q = queue.Queue()
        diarize.diarize_subprocess_worker(
            "/fake.wav", "hf", None, {"min_speakers": 2, "max_speakers": 3}, q)
        assert _drain(q)[-1][0] == "ok"
        assert _no_hook(pipeline.call_kwargs) == {
            "num_speakers": None, "min_speakers": 2, "max_speakers": 3}

    @pytest.mark.parametrize("num,lo,hi,expected", [
        (None, None, None, (None, None, None)),
        (0, 0, 0, (None, None, None)),
        (3, None, None, (3, None, None)),
        (None, 2, 4, (None, 2, 4)),
        (None, 3, 3, (None, 3, 3)),
        (None, 2, None, (None, 2, None)),
    ])
    def test_valid_hints(self, num, lo, hi, expected):
        assert diarize.validate_speaker_hints(num, lo, hi) == expected

    @pytest.mark.parametrize("num,lo,hi,message", [
        (None, 4, 2, "more than maximum"),
        (-1, None, None, "at least 1"),
        (None, -2, None, "at least 1"),
        (3, 2, 4, "not both"),
        (3, None, 4, "not both"),
    ])
    def test_invalid_hints(self, num, lo, hi, message):
        with pytest.raises(ValueError, match=message):
            diarize.validate_speaker_hints(num, lo, hi)

    def test_turns_file_round_trips_range_and_device(self, tmp_path):
        diarize.save_turns(str(tmp_path), [], min_speakers=2, max_speakers=4, device="cuda")
        assert diarize.load_last_run_info(str(tmp_path)) == {
            "min_speakers": 2, "max_speakers": 4, "device": "cuda"}

    def test_no_run_yet_reports_all_none(self, tmp_path):
        assert diarize.load_last_run_info(str(tmp_path)) == {
            "min_speakers": None, "max_speakers": None, "device": None}


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
                       on_done=None, run_settings=None, **launch):
            calls.append({"args": args, "on_done": on_done, "launch": launch})
            return True
        monkeypatch.setattr(background_jobs, "start_process_job", fake_start)
        return calls

    def test_service_passes_use_gpu_and_range_to_the_worker(self, isolated_db, monkeypatch):
        from services import diarization_service, settings_service
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf")
        patch_setting(monkeypatch, "use_gpu", True)
        did, ddir = _drama_with_audio(isolated_db)
        calls = self._capture_start(monkeypatch)
        diarization_service.start_diarization_run(did, min_speakers=2, max_speakers=4)
        assert calls[0]["args"] == (os.path.join(ddir, "audio.wav"), "hf", None,
                                    {"use_gpu": True, "min_speakers": 2, "max_speakers": 4})

    def test_diarization_runs_spawned_and_is_killed_as_a_tree(self, isolated_db, monkeypatch):
        """Spawn keeps a forked child from inheriting CUDA state; the whole
        tree is killed so a cancel leaves no grandchild holding the card."""
        from services import diarization_service, settings_service
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf")
        did, _ = _drama_with_audio(isolated_db)
        calls = self._capture_start(monkeypatch)
        diarization_service.start_diarization_run(did)
        assert calls[0]["launch"] == {"kill_whole_tree": True, "start_method": "spawn"}

    def test_service_rejects_an_inverted_range(self, isolated_db, monkeypatch):
        from services import diarization_service, settings_service
        from services.service_errors import InvalidInputError
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf")
        did, _ = _drama_with_audio(isolated_db)
        self._capture_start(monkeypatch)
        with pytest.raises(InvalidInputError):
            diarization_service.start_diarization_run(did, min_speakers=5, max_speakers=2)

    def test_on_done_saves_range_and_device_and_config_reports_them(self, isolated_db,
                                                                     monkeypatch):
        from services import diarization_service, settings_service
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf")
        did, _ = _drama_with_audio(isolated_db)
        calls = self._capture_start(monkeypatch)
        diarization_service.start_diarization_run(did, min_speakers=2, max_speakers=4)
        calls[0]["on_done"]("job", {"segments": [], "model": "m", "embeddings": {},
                                    "device": "cuda"})
        cfg = diarization_service.get_diarization_config(did)
        assert (cfg["min_speakers"], cfg["max_speakers"], cfg["last_device"]) == (2, 4, "cuda")
        assert cfg["expected_speakers"] is None

    def test_api_run_passes_range_and_rejects_bad_combos(self, client, isolated_db,
                                                          monkeypatch):
        monkeypatch.setenv("BAIHE_HF_TOKEN", "sk-test")
        did, _ = _drama_with_audio(isolated_db)
        calls = self._capture_start(monkeypatch)
        ok = client.post(f"/api/diarization/dramas/{did}/run?min_speakers=2&max_speakers=4")
        assert ok.status_code == 200
        assert calls[-1]["args"][3]["min_speakers"] == 2
        assert calls[-1]["args"][3]["max_speakers"] == 4
        assert client.post(f"/api/diarization/dramas/{did}/run?min_speakers=4&max_speakers=2"
                           ).status_code == 422
        assert client.post(f"/api/diarization/dramas/{did}/run?expected_speakers=3"
                           f"&min_speakers=2").status_code == 422
        assert client.post(f"/api/diarization/dramas/{did}/run?max_speakers=21"
                           ).status_code == 422


class TestCliParity:
    def _run(self, did, **kw):
        import cli
        args = argparse.Namespace(id=did, hf_token="hf", num_speakers=kw.get("num_speakers", 0),
                                  min_speakers=kw.get("min_speakers"),
                                  max_speakers=kw.get("max_speakers"), overwrite_manual=False)
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli.cmd_diarize(args)
        return out.getvalue()

    def test_cli_passes_range_and_use_gpu_and_saves_the_device(self, isolated_db, monkeypatch):
        import cli
        from services import settings_service
        from core import Line
        patch_setting(monkeypatch, "use_gpu", True)
        monkeypatch.setattr(cli, "release_gpu_models", lambda: None)
        did, ddir = _drama_with_audio(isolated_db)
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="a")])
        seen = {}

        def fake(audio_path, hf_token, **kw):
            seen.update(kw)
            kw["run_info"]["device"] = "cuda"
            return [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}], "m", {}
        monkeypatch.setattr(diarize, "diarize", fake)
        out = self._run(did, min_speakers=2, max_speakers=4)
        assert seen["min_speakers"] == 2 and seen["max_speakers"] == 4
        assert seen["num_speakers"] is None and seen["use_gpu"] is True
        assert "on cuda" in out
        assert diarize.load_last_run_info(ddir) == {"min_speakers": 2, "max_speakers": 4,
                                                    "device": "cuda"}

    def test_cli_rejects_a_bad_range_without_running(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db)
        monkeypatch.setattr(diarize, "diarize", lambda *a, **k: pytest.fail("should not run"))
        assert "more than maximum" in self._run(did, min_speakers=5, max_speakers=2)


class TestProgressReporting:
    def test_worker_reports_stages_and_hook_steps_below_100_percent(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=False)

        def call(audio, hook=None, **kwargs):
            hook("segmentation", None, completed=1, total=4)
            hook("embeddings", None, completed=4, total=4)
            hook("segmentation", None, completed=2, total=4)
            return _Annotation()
        pipeline.__class__ = type("P", (_FakePipeline,), {"__call__": lambda self, a, **k: call(a, **k)})
        q = queue.Queue()
        diarize.diarize_subprocess_worker("/fake.wav", "hf", None, q)
        items = _drain(q)
        progress = [i for i in items if i[0] == "progress"]
        assert items[-1][0] == "ok"
        assert progress[0][2] == "Loading speaker model..."
        messages = [p[2] for p in progress]
        assert "Detecting speakers: segmentation (1 of 4)" in messages
        assert "Detecting speakers: embeddings (4 of 4)" in messages
        fracs = [p[1] for p in progress]
        assert fracs == sorted(fracs) and max(fracs) < 1.0

    def test_pipeline_without_hook_support_still_succeeds(self, monkeypatch):
        pipeline = _install_fakes(monkeypatch, cuda_available=False)
        seen = []

        def call(self, audio, **kwargs):
            if "hook" in kwargs:
                raise TypeError("__call__() got an unexpected keyword argument 'hook'")
            seen.append(kwargs)
            return _Annotation()
        pipeline.__class__ = type("P", (_FakePipeline,), {"__call__": call})
        q = queue.Queue()
        diarize.diarize_subprocess_worker("/fake.wav", "hf", None, q)
        items = _drain(q)
        assert items[-1][0] == "ok" and seen
        assert any(i[2] == "Detecting speakers..." for i in items if i[0] == "progress")


def test_diarize_worker_leaves_the_parents_process_group(monkeypatch):
    import queue
    import background_jobs
    import diarize
    left = []
    monkeypatch.setattr(background_jobs, "start_own_process_group", lambda: left.append(True))
    diarize.diarize_subprocess_worker("missing.wav", "hf", None, queue.Queue())
    assert left == [True]
