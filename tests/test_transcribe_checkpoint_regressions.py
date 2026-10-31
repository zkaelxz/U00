"""Regressions found while reviewing transcription chunk checkpoints.
Fakes only: no model, GPU or ffmpeg."""
import queue
import types

import pytest

import audio_extract
import whisper_models
import whisper_transcribe
from jobs import job_runner, process_worker
from services import job_checkpoint_service, transcribe_checkpoint_service as tcs


def _segments(*spans):
    return [{"start": a, "end": b, "text": t} for a, b, t in spans]


class Seg:
    def __init__(self, start, end, text, words=None):
        self.start, self.end, self.text = start, end, text
        self.words = [types.SimpleNamespace(start=a, end=b, word=w) for a, b, w in (words or [])]


@pytest.fixture
def no_rehear(monkeypatch):
    # The sparse re-hear is not under test; long fake segments would trigger it.
    monkeypatch.setattr(whisper_transcribe, "_rehear_sparse", lambda m, p, k, segs, keys: segs)


def test_units_of_an_earlier_cut_are_not_mixed_in_after_a_gap(isolated_db, monkeypatch):
    import db
    scope = "transcribe:1:key"
    real_record = db.record_checkpoint_unit

    # Run A: unit 1's write fails (a locked database); units 0 and 2 are stored.
    def flaky(s, unit_id, payload):
        if str(unit_id) == "1":
            raise OSError("database is locked")
        real_record(s, unit_id, payload)

    monkeypatch.setattr(db, "record_checkpoint_unit", flaky)
    for uid, batch in ((0, _segments((0.0, 120.0, "a0"))),
                       (1, _segments((121.0, 248.0, "a1"))),
                       (2, _segments((250.0, 375.0, "a2")))):
        q = queue.Queue()
        process_worker.report_checkpoint_unit(q, scope, uid, batch)
        job_runner._apply_progress_item("job", q.get_nowait())
    monkeypatch.setattr(db, "record_checkpoint_unit", real_record)

    # Run B resumes after unit 0, records its own unit 1 (a different cut that ends at 241 s), then dies.
    b = tcs.resume_from(scope)
    assert b["resumed_units"] == 1
    job_checkpoint_service.record_unit(scope, b["resumed_units"], _segments((121.0, 241.0, "b1")))

    # Run C must not take run A's unit 2: 241..250 s (the end of "a1") was decoded by neither.
    c = tcs.resume_from(scope)
    assert c["resumed_units"] == 2, [s["text"] for s in c["segments"]]


def test_a_tail_that_cannot_be_cut_falls_back_to_the_whole_file(monkeypatch, no_rehear):
    def no_ffmpeg(*a, **k):
        raise FileNotFoundError(2, "No such file or directory: 'ffmpeg'")

    monkeypatch.setattr(audio_extract, "extract_audio_slice", no_ffmpeg)
    paths = []

    class Model:
        def transcribe(self, audio_path, **kwargs):
            paths.append(audio_path)
            return iter([Seg(0.0, 2.0, "whole")]), types.SimpleNamespace(duration=50.0)

    monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: Model())
    out = whisper_transcribe.transcribe_for_timing(
        "/media/a.wav", resume_segments=_segments((0.0, 10.0, "one two three four five")),
        filter_hallucination_repeats=0)
    assert paths == ["/media/a.wav"] and out


def test_the_tail_is_not_cut_inside_the_next_lines_first_word(monkeypatch, no_rehear):
    cuts, units = [], []

    def fake_slice(audio_path, start, end, out_path, timeout=None):
        cuts.append(start)
        with open(out_path, "wb") as f:
            f.write(b"x" * 1000)

    monkeypatch.setattr(audio_extract, "extract_audio_slice", fake_slice)
    # The uninterrupted decode: line "b" starts speaking 0.05 s after line "a" stops.
    full = [Seg(0.0, 125.0, "a", words=[(0.5, 121.0, "a")]),
            Seg(121.0, 130.0, "b", words=[(121.05, 129.0, "b")])]

    class Model:
        def transcribe(self, audio_path, **kwargs):
            return iter(full), types.SimpleNamespace(duration=300.0)

    monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: Model())
    whisper_transcribe.transcribe_for_timing("/media/a.wav", on_unit=units.append,
                                             filter_hallucination_repeats=0)
    assert [s["text"] for s in units[0]] == ["a"]
    # A later run resumes from that unit.
    whisper_transcribe.transcribe_for_timing("/media/a.wav", resume_segments=units[0],
                                             filter_hallucination_repeats=0)
    next_word_start = full[1].words[0].start
    assert cuts[0] <= next_word_start, (cuts[0], next_word_start)


def test_the_cpu_fallback_does_not_record_audio_twice(monkeypatch, no_rehear):
    units = []

    def gpu_segments():
        yield Seg(0.0, 130.0, "a")
        yield Seg(130.0, 200.0, "b")
        raise RuntimeError("CUDA failed with error out of memory")

    class Gpu:
        def transcribe(self, audio_path, **kwargs):
            return gpu_segments(), types.SimpleNamespace(duration=400.0)

    class Cpu:
        def transcribe(self, audio_path, **kwargs):
            return (iter([Seg(0.0, 130.0, "a"), Seg(130.0, 200.0, "b"), Seg(200.0, 330.0, "c")]),
                    types.SimpleNamespace(duration=400.0))

    monkeypatch.setattr(whisper_models, "load_whisper_model",
                        lambda size, use_gpu=False, **k: Gpu() if use_gpu else Cpu())
    monkeypatch.setattr(whisper_models, "forget_gpu_model", lambda *a, **k: None)
    out = whisper_transcribe.transcribe_for_timing("/media/a.wav", use_gpu=True, on_unit=units.append,
                                                   filter_hallucination_repeats=0)
    assert [s["text"] for s in out] == ["a", "b", "c"]
    assert [s["text"] for u in units for s in u] == ["a", "b", "c"][:sum(len(u) for u in units)], units


def test_a_reported_unit_is_not_changed_after_it_was_reported(monkeypatch, no_rehear):
    units = []
    full = [Seg(0.0, 125.0, "a", words=[(0.5, 121.0, "a")]),
            Seg(121.0, 130.0, "b", words=[(121.05, 129.0, "b")])]

    class Model:
        def transcribe(self, audio_path, **kwargs):
            return iter(full), types.SimpleNamespace(duration=300.0)

    monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: Model())
    whisper_transcribe.transcribe_for_timing("/media/a.wav", on_unit=units.append,
                                             filter_hallucination_repeats=0)
    assert units[0][0]["end"] == pytest.approx(121.2)


def test_a_replaced_local_model_file_is_a_different_scope(tmp_path):
    model = tmp_path / "m"
    model.mkdir()
    (model / "model.bin").write_bytes(b"one")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    names = list(__import__("inspect").signature(tcs._transcribe_worker).parameters)
    args = tuple(str(model) if n == "local_model_path" else None for n in names)
    before = tcs.scope_for(1, args, audio)
    (model / "model.bin").write_bytes(b"different size")
    assert tcs.scope_for(1, args, audio) != before
