"""Two decode lanes on one Whisper model (BAIHE_WHISPER_LANES=2, an A/B knob, off by default)."""
import sys
import threading
import types

import numpy as np
import pytest

import vad_segments
import whisper_models
import whisper_transcribe

SR = 16000


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class _Model:
    """Hears one line one second into whatever audio it is given, named by its length."""

    def __init__(self, lanes=2, fail_on=None):
        self.heard, self.kwargs, self.threads = [], [], []
        self.barrier = threading.Barrier(lanes, timeout=5)
        self.fail_on = fail_on

    def transcribe(self, audio, **kwargs):
        self.heard.append(len(audio) / SR)
        self.kwargs.append(kwargs)
        self.threads.append(threading.current_thread().name)
        seconds = len(audio) / SR

        def gen():
            # Both lanes must be decoding at the same time to pass this point.
            self.barrier.wait()
            if self.fail_on == seconds:
                raise ValueError("lane broke")
            yield _Seg(1.0, 2.0, f"heard {seconds:g} s")
        return gen(), types.SimpleNamespace(duration=seconds)


def _spans(*pairs):
    return [vad_segments.Span(a, b) for a, b in pairs]


@pytest.fixture
def run(monkeypatch):
    """transcribe_for_timing on 12 s of audio whose longest pause in the middle third is 5-7 s."""
    monkeypatch.setattr(whisper_transcribe, "LANES_MIN_SECONDS", 10.0)
    monkeypatch.setattr(whisper_transcribe, "_fit_to_speech", lambda result, *a, **k: result)
    monkeypatch.setattr(whisper_transcribe, "_rehear_sparse", lambda m, a, kw, segs, keys: segs)
    state = {"seconds": 12.0, "spans": _spans((0, 3), (3.5, 5), (7, 9), (9.2, 12))}
    monkeypatch.setattr(whisper_transcribe, "_decode_whole",
                        lambda path: np.zeros(int(state["seconds"] * SR), dtype=np.float32))
    monkeypatch.setattr(vad_segments, "speech_spans", lambda audio, sr, **kw: state["spans"])

    def go(model, use_gpu=True, **kw):
        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: model)
        return whisper_transcribe.transcribe_for_timing("a.wav", model_size="large-v3",
                                                        use_gpu=use_gpu, **kw)
    go.state = state
    return go


def test_off_by_default_one_decode_of_the_whole_file(run, monkeypatch):
    monkeypatch.delenv("BAIHE_WHISPER_LANES", raising=False)
    model = _Model(lanes=1)
    out = run(model)
    assert model.heard == [12.0]
    assert [s["text"] for s in out] == ["heard 12 s"]


def test_two_lanes_hear_the_halves_at_once_and_merge_in_time_order(run, monkeypatch):
    monkeypatch.setenv("BAIHE_WHISPER_LANES", "2")
    model = _Model(lanes=2)
    progress = []
    out = run(model, progress_cb=progress.append)
    assert sorted(model.heard) == [6.0, 6.0]   # cut in the middle of the 5-7 s pause
    assert len(set(model.threads)) == 2
    assert model.kwargs[0] == model.kwargs[1]
    assert [(s["start"], s["end"], s["text"]) for s in out] == [
        (1.0, 2.0, "heard 6 s"), (7.0, 8.0, "heard 6 s")]
    assert progress and max(progress) <= 1.0


def test_a_short_file_stays_in_one_lane(run, monkeypatch):
    monkeypatch.setenv("BAIHE_WHISPER_LANES", "2")
    run.state["seconds"] = 8.0
    model = _Model(lanes=1)
    run(model)
    assert model.heard == [8.0]


def test_no_pause_in_the_middle_third_stays_in_one_lane(run, monkeypatch):
    monkeypatch.setenv("BAIHE_WHISPER_LANES", "2")
    run.state["spans"] = _spans((0, 2), (2.2, 12))
    model = _Model(lanes=1)
    run(model)
    assert model.heard == [12.0]


def test_a_cpu_run_stays_in_one_lane(run, monkeypatch):
    monkeypatch.setenv("BAIHE_WHISPER_LANES", "2")
    model = _Model(lanes=1)
    run(model, use_gpu=False)
    assert model.heard == [12.0]


def test_an_error_in_the_second_lane_fails_the_run(run, monkeypatch):
    monkeypatch.setenv("BAIHE_WHISPER_LANES", "2")
    with pytest.raises(ValueError, match="lane broke"):
        run(_Model(lanes=2, fail_on=6.0))


def test_only_the_first_lane_reports_checkpoint_units(run, monkeypatch):
    # A unit from the second half before the first half is done would let a resume skip audio.
    monkeypatch.setenv("BAIHE_WHISPER_LANES", "2")
    monkeypatch.setattr(whisper_transcribe, "CHECKPOINT_UNIT_SECONDS", 0.5)
    units = []
    run(_Model(lanes=2), on_unit=units.append)
    assert [[s["start"] for s in u] for u in units] == [[1.0]]


@pytest.mark.parametrize("env, device, workers", [
    ("2", "cuda", 2), (None, "cuda", None), ("2", "cpu", None), ("3", "cuda", None)])
def test_the_model_is_built_with_two_workers_only_for_gpu_lanes(monkeypatch, env, device, workers):
    built = []
    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = lambda path, **kw: built.append(kw) or object()
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    monkeypatch.setattr(whisper_models, "_whisper_model_cache", {})
    monkeypatch.setattr(whisper_models, "gpu_compute_type", lambda: "float16")
    monkeypatch.setattr(whisper_models, "_generic_repo", lambda size: False)
    if env is None:
        monkeypatch.delenv("BAIHE_WHISPER_LANES", raising=False)
    else:
        monkeypatch.setenv("BAIHE_WHISPER_LANES", env)
    whisper_models.load_whisper_model("small", use_gpu=device == "cuda", local_model_path="/models/small")
    assert built[0]["device"] == device
    assert built[0].get("num_workers") == workers
