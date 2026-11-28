"""Two decode lanes on one Whisper model (BAIHE_WHISPER_LANES=2, an A/B knob, off by default)."""
import sys
import threading
import time
import types

import numpy as np
import pytest

import vad_segments
import whisper_models
import whisper_transcribe

SR = 16000
LANES_BUILT = {"device": "cuda", "compute_type": "float16", "gpu_error": None, "lanes": 2}


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


class _Model:
    """Hears `lines` lines a second apart in whatever audio it is given, named by its length.
    fail_on maps a length to the error that half raises once, after the first line."""

    def __init__(self, lanes=2, fail_on=None, lines=1, pause=0.0):
        self.heard, self.kwargs, self.threads, self.yielded = [], [], [], []
        self.barrier = threading.Barrier(lanes, timeout=5)
        self.fail_on = dict(fail_on or {})
        self.lines, self.pause = lines, pause

    def transcribe(self, audio, **kwargs):
        seconds = len(audio) / SR
        first_call = seconds not in self.heard
        self.heard.append(seconds)
        self.kwargs.append(kwargs)
        self.threads.append(threading.current_thread().name)

        def gen():
            if first_call:
                # Both lanes must be decoding at the same time to pass this point.
                self.barrier.wait()
            for n in range(self.lines):
                if n == 1 and first_call and seconds in self.fail_on:
                    raise self.fail_on[seconds]
                time.sleep(self.pause)
                self.yielded.append(seconds)
                yield _Seg(1.0 + n, 1.5 + n, f"heard {seconds:g} s")
        return gen(), types.SimpleNamespace(duration=seconds)


def _spans(*pairs):
    return [vad_segments.Span(a, b) for a, b in pairs]


@pytest.fixture
def run(monkeypatch):
    """transcribe_for_timing on 12 s of audio whose longest pause in the middle third is 4.5-5.5 s,
    so the halves are 5 s and 7 s long."""
    monkeypatch.setattr(whisper_transcribe, "LANES_MIN_SECONDS", 10.0)
    monkeypatch.setattr(whisper_transcribe, "_fit_to_speech", lambda result, *a, **k: result)
    monkeypatch.setattr(whisper_transcribe, "_rehear_sparse", lambda m, a, kw, segs, keys: segs)
    state = {"seconds": 12.0, "spans": _spans((0, 3), (3.5, 4.5), (5.5, 9), (9.2, 12)),
             "built": dict(LANES_BUILT)}
    monkeypatch.setattr(whisper_transcribe, "_decode_whole",
                        lambda path: np.zeros(int(state["seconds"] * SR), dtype=np.float32))
    monkeypatch.setattr(vad_segments, "speech_spans", lambda audio, sr, **kw: state["spans"])
    monkeypatch.setattr(whisper_models, "get_whisper_device_info",
                        lambda size, use_gpu=False, local_model_path=None:
                        state["built"] if use_gpu else {"device": "cpu"})

    def go(model, use_gpu=True, **kw):
        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: model)
        return whisper_transcribe.transcribe_for_timing("a.wav", model_size="large-v3",
                                                        use_gpu=use_gpu, **kw)
    go.state = state
    return go


def test_a_model_built_without_two_workers_decodes_the_whole_file_once(run):
    run.state["built"] = {"device": "cuda", "compute_type": "float16", "gpu_error": None}
    model = _Model(lanes=1)
    out = run(model)
    assert model.heard == [12.0]
    assert [s["text"] for s in out] == ["heard 12 s"]


def test_two_lanes_hear_the_halves_at_once_and_merge_in_time_order(run):
    model = _Model(lanes=2)
    progress, reporters = [], set()
    out = run(model, progress_cb=lambda f: (progress.append(f),
                                             reporters.add(threading.current_thread().name)))
    assert sorted(model.heard) == [5.0, 7.0]   # cut in the middle of the 4.5-5.5 s pause
    assert len(set(model.threads)) == 2
    assert model.kwargs[0] == model.kwargs[1]
    assert [(s["start"], s["end"], s["text"]) for s in out] == [
        (1.0, 1.5, "heard 5 s"), (6.0, 6.5, "heard 7 s")]
    assert progress and max(progress) <= 1.0
    # Progress goes through the job's own thread, where a force stop is checked.
    assert "whisper-lane-2" not in reporters


def test_a_short_file_stays_in_one_lane(run):
    run.state["seconds"] = 8.0
    model = _Model(lanes=1)
    run(model)
    assert model.heard == [8.0]


def test_no_pause_in_the_middle_third_stays_in_one_lane(run):
    run.state["spans"] = _spans((0, 2), (2.2, 12))
    model = _Model(lanes=1)
    run(model)
    assert model.heard == [12.0]


def test_a_cpu_run_stays_in_one_lane(run):
    model = _Model(lanes=1)
    run(model, use_gpu=False)
    assert model.heard == [12.0]


def test_a_gpu_request_that_loaded_on_the_cpu_stays_in_one_lane(run):
    run.state["built"] = {"device": "cpu", "compute_type": "int8", "gpu_error": "no CUDA"}
    model = _Model(lanes=1)
    run(model)
    assert model.heard == [12.0]


@pytest.mark.parametrize("failing", [5.0, 7.0])
def test_an_error_in_either_lane_fails_the_run_and_stops_the_other_lane(run, failing):
    other = 12.0 - failing
    model = _Model(lanes=2, fail_on={failing: ValueError("lane broke")}, lines=40, pause=0.05)
    started = time.monotonic()
    with pytest.raises(ValueError, match="lane broke"):
        run(model)
    # The other lane's 40 lines would take 2 s; it stops at its next line instead.
    assert time.monotonic() - started < 1.5
    assert model.yielded.count(other) < 20


def test_a_half_that_ran_out_of_memory_is_heard_again_alone(run):
    model = _Model(lanes=2, fail_on={7.0: RuntimeError("CUDA failed with error out of memory")},
                   lines=3)
    out = run(model)
    assert sorted(model.heard) == [5.0, 7.0, 7.0]
    assert [s["start"] for s in out] == [1.0, 2.0, 3.0, 6.0, 7.0, 8.0]


def test_both_halves_out_of_memory_fails_the_run(run):
    oom = RuntimeError("CUDA failed with error out of memory")
    model = _Model(lanes=2, fail_on={5.0: oom, 7.0: oom}, lines=3)
    with pytest.raises(RuntimeError, match="out of memory"):
        run(model, on_gpu_fallback=lambda exc: (_ for _ in ()).throw(exc))


def test_a_cancel_from_the_progress_callback_stops_both_lanes(run):
    class Cancelled(Exception):
        pass

    def progress(frac):
        if frac > 0:
            raise Cancelled()
    model = _Model(lanes=2, lines=40, pause=0.05)
    started = time.monotonic()
    with pytest.raises(Cancelled):
        run(model, progress_cb=progress)
    assert time.monotonic() - started < 1.5


def test_only_the_first_lane_reports_checkpoint_units(run, monkeypatch):
    # A unit from the second half before the first half is done would let a resume skip audio.
    monkeypatch.setattr(whisper_transcribe, "CHECKPOINT_UNIT_SECONDS", 0.2)
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
    monkeypatch.setattr(whisper_models, "_whisper_device_info", {})
    monkeypatch.setattr(whisper_models, "gpu_compute_type", lambda: "float16")
    monkeypatch.setattr(whisper_models, "_generic_repo", lambda size: False)
    if env is None:
        monkeypatch.delenv("BAIHE_WHISPER_LANES", raising=False)
    else:
        monkeypatch.setenv("BAIHE_WHISPER_LANES", env)
    gpu = device == "cuda"
    whisper_models.load_whisper_model("small", use_gpu=gpu, local_model_path="/models/small")
    assert built[0]["device"] == device
    assert built[0].get("num_workers") == workers
    info = whisper_models.get_whisper_device_info("small", use_gpu=gpu, local_model_path="/models/small")
    assert info.get("lanes") == (2 if workers else None)
