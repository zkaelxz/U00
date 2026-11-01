"""Resuming a transcription from the units an earlier run finished: the decode
that reports and continues from units, the key that must change with any
setting, the watcher that stores units sent from the worker process, and the
whole path through a simulated crash. Fakes only: no model, GPU or ffmpeg."""
import queue
import types

import pytest

import audio_extract
import whisper_models
import whisper_transcribe
from jobs import job_runner, process_worker
from services import job_checkpoint_service, transcribe_checkpoint_service as tcs, transcribe_pipeline
from tests.test_transcribe_service import _capture_worker_start, _drama_with_audio, transcribe_service


class Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text, self.words = start, end, text, None


def _model(calls, tail_segments):
    class Model:
        def transcribe(self, audio_path, **kwargs):
            calls.append((audio_path, kwargs))
            return iter([Seg(*s) for s in tail_segments]), types.SimpleNamespace(duration=100.0)

    return Model()


def _segments(*spans):
    return [{"start": a, "end": b, "text": t} for a, b, t in spans]


@pytest.fixture
def decode(monkeypatch):
    """transcribe_for_timing over a fake model; the tail cut is recorded, not run."""
    cuts, calls = [], []

    def fake_slice(audio_path, start, end, out_path, timeout=None):
        cuts.append((audio_path, start))
        with open(out_path, "wb") as f:
            f.write(b"x" * 1000)

    monkeypatch.setattr(audio_extract, "extract_audio_slice", fake_slice)

    def run(tail_segments, **kwargs):
        monkeypatch.setattr(whisper_models, "load_whisper_model",
                            lambda *a, **k: _model(calls, tail_segments))
        return whisper_transcribe.transcribe_for_timing("/media/a.wav", **kwargs)

    return run, cuts, calls


class TestUnitsFromTheDecode:
    def test_units_are_reported_about_every_two_minutes_of_audio(self, decode):
        run, _cuts, _calls = decode
        units = []
        out = run([(0.0, 60.0, "a"), (60.0, 125.0, "b"), (125.0, 130.0, "c"), (130.0, 255.0, "d")],
                  on_unit=lambda batch: units.append(batch), filter_hallucination_repeats=0)
        assert [[s["text"] for s in u] for u in units] == [["a", "b"], ["c", "d"]]
        assert [s["text"] for s in out] == ["a", "b", "c", "d"]

    def test_a_run_without_a_callback_decodes_as_before(self, decode):
        run, cuts, calls = decode
        out = run([(0.0, 2.0, "a")], filter_hallucination_repeats=0)
        assert [s["text"] for s in out] == ["a"] and cuts == []
        assert calls[0][0] == "/media/a.wav"


class TestResumedDecode:
    PREFIX = _segments((0.0, 2.0, "one"), (2.0, 10.0, "two two two two two two"))

    def test_only_the_audio_after_the_last_unit_is_decoded_with_the_detector_on(self, decode):
        run, cuts, calls = decode
        out = run([(1.0, 3.0, "three")], resume_segments=self.PREFIX, filter_hallucination_repeats=0)
        assert cuts == [("/media/a.wav", 10.0)]
        path, kwargs = calls[0]
        assert path != "/media/a.wav"
        # clip_timestamps would switch the detector off for the whole call.
        assert kwargs["vad_filter"] is True and "clip_timestamps" not in kwargs
        assert [(s["start"], s["end"], s["text"]) for s in out] == [
            (0.0, 2.0, "one"), (2.0, 10.0, "two two two two two two"), (11.0, 13.0, "three")]

    def test_word_times_of_the_new_segments_move_with_them(self, decode, monkeypatch):
        run, _cuts, _calls = decode
        seg = Seg(1.0, 3.0, "three")
        seg.words = [types.SimpleNamespace(start=1.0, end=2.0, word="th"),
                     types.SimpleNamespace(start=2.0, end=3.0, word="ree")]

        class Model:
            def transcribe(self, audio_path, **kwargs):
                return iter([seg]), types.SimpleNamespace(duration=50.0)

        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: Model())
        out = whisper_transcribe.transcribe_for_timing(
            "/media/a.wav", resume_segments=self.PREFIX, filter_hallucination_repeats=0)
        words = out[-1]["words"]
        assert [(w["start"], w["end"]) for w in words] == [(11.0, 12.0), (12.0, 13.0)]

    def test_progress_counts_from_the_resume_point(self, decode):
        run, _cuts, _calls = decode
        seen = []
        run([(0.0, 45.0, "x")], resume_segments=self.PREFIX, progress_cb=seen.append,
            filter_hallucination_repeats=0)
        # The tail is 100 s long and starts at 10 s: 45 s into it is 55 of 110.
        assert seen == [pytest.approx(55 / 110)]

    def test_unit_ids_continue_and_new_units_hold_original_times(self, decode):
        run, _cuts, _calls = decode
        units = []
        run([(0.0, 130.0, "x")], resume_segments=self.PREFIX, on_unit=units.append,
            filter_hallucination_repeats=0)
        assert units == [[{"start": 10.0, "end": 140.0, "text": "x"}]]

    def test_nothing_after_the_last_unit_means_nothing_to_decode(self, decode, monkeypatch):
        run, _cuts, calls = decode

        def empty_slice(audio_path, start, end, out_path, timeout=None):
            open(out_path, "wb").close()

        monkeypatch.setattr(audio_extract, "extract_audio_slice", empty_slice)
        out = run([(0.0, 1.0, "never decoded")], resume_segments=self.PREFIX,
                  filter_hallucination_repeats=0)
        assert calls == [] and [s["text"] for s in out] == ["one", "two two two two two two"]

    def test_the_cut_file_is_removed_afterwards_and_after_a_failure(self, decode, monkeypatch):
        import os
        run, _cuts, calls = decode
        run([(0.0, 1.0, "a")], resume_segments=self.PREFIX, filter_hallucination_repeats=0)
        assert not os.path.exists(calls[0][0])

        class Broken:
            def transcribe(self, audio_path, **kwargs):
                calls.append((audio_path, kwargs))
                raise RuntimeError("model fell over")

        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: Broken())
        with pytest.raises(RuntimeError):
            whisper_transcribe.transcribe_for_timing("/media/a.wav", resume_segments=self.PREFIX)
        assert not os.path.exists(calls[-1][0])

    def test_fast_mode_ignores_the_units(self, decode, monkeypatch):
        import sys
        run, cuts, _calls = decode
        seen = []

        class Pipeline:
            def __init__(self, model):
                pass

            def transcribe(self, audio_path, **kwargs):
                seen.append(audio_path)
                return iter([Seg(0.0, 1.0, "full")]), types.SimpleNamespace(duration=5.0)

        stub = types.ModuleType("faster_whisper")
        stub.BatchedInferencePipeline = Pipeline
        monkeypatch.setitem(sys.modules, "faster_whisper", stub)
        out = run([], resume_segments=self.PREFIX, fast_mode=True, filter_hallucination_repeats=0)
        assert cuts == [] and seen == ["/media/a.wav"] and [s["text"] for s in out] == ["full"]


def _worker_names():
    import inspect
    return list(inspect.signature(transcribe_pipeline._transcribe_worker).parameters)


class TestKey:
    def test_the_scope_changes_with_any_setting_or_the_media(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        scope = captured["checkpoint"]["scope"]
        assert scope.startswith(f"transcribe:{did}:")
        names = _worker_names()
        base = tuple(captured[n] for n in names[:-1])
        audio = captured["audio_path"]
        assert tcs.scope_for(did, base, audio) == scope
        for name in ("beam_size", "min_silence_ms", "vad_threshold", "use_gpu", "whisper_size",
                     "initial_prompt", "repeat_guard", "sensitivity_preset", "source_language"):
            changed = list(base)
            old = changed[names.index(name)]
            changed[names.index(name)] = (not old) if isinstance(old, bool) else f"{old}-changed"
            assert tcs.scope_for(did, tuple(changed), audio) != scope, name
        with open(audio, "ab") as f:
            f.write(b"more")
        assert tcs.scope_for(did, base, audio) != scope

    def test_the_scope_ignores_the_scratch_folder_and_the_checkpoint_itself(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        captured = _capture_worker_start(monkeypatch)
        transcribe_service.start_transcribe_run(did)
        names = _worker_names()
        base = [captured[n] for n in names[:-1]]
        a = tcs.scope_for(did, tuple(base), captured["audio_path"])
        base[names.index("scratch_dir")] = "/somewhere/else"
        base[names.index("checkpoint")] = {"scope": "x"}
        assert tcs.scope_for(did, tuple(base), captured["audio_path"]) == a

    @pytest.mark.parametrize("override", [{"whisper_fast_mode": 1}, {"use_groq": 1}])
    def test_runs_that_decode_another_way_are_not_resumable(self, isolated_db, monkeypatch, override):
        did, _ = _drama_with_audio(isolated_db, **{"transcript_mode": "whisper", **override})
        captured = _capture_worker_start(monkeypatch)
        monkeypatch.setattr(transcribe_service.settings_service, "resolve_key",
                            lambda key, env_path=None: "gsk_key" if key == "groq" else None)
        transcribe_service.start_transcribe_run(did)
        assert captured["checkpoint"] is None

    @pytest.mark.parametrize("args", [
        ("whisper", "qwen3_asr_vad", False, False, False),
        ("whisper", "anime_whisper", False, False, False),
        ("whisper", "whisper", False, False, True),
        ("have_transcript", "whisper", False, False, False)])
    def test_the_other_decodes_are_not_resumable(self, args):
        assert tcs.applies(*args) is False

    @pytest.mark.parametrize("backend", ["whisper", "qwen3_asr"])
    def test_the_plain_whisper_decode_is_resumable(self, backend):
        assert tcs.applies("whisper", backend, False, False, False) is True


class TestReadingUnitsBack:
    SCOPE = "transcribe:1:abc"

    def _store(self, isolated_db, units):
        for unit_id, batch in units.items():
            job_checkpoint_service.record_unit(self.SCOPE, unit_id, batch)

    def test_units_are_taken_in_order(self, isolated_db):
        self._store(isolated_db, {0: _segments((0.0, 5.0, "a")), 1: _segments((5.0, 9.0, "b"))})
        got = tcs.resume_from(self.SCOPE)
        assert got["resumed_units"] == 2 and [s["text"] for s in got["segments"]] == ["a", "b"]
        assert got["record"] is True

    def test_a_missing_unit_stops_the_resume_there(self, isolated_db):
        self._store(isolated_db, {0: _segments((0.0, 5.0, "a")), 2: _segments((9.0, 12.0, "c"))})
        got = tcs.resume_from(self.SCOPE)
        assert got["resumed_units"] == 1 and [s["text"] for s in got["segments"]] == ["a"]

    def test_a_unit_that_doubles_back_stops_the_resume_there(self, isolated_db):
        self._store(isolated_db, {0: _segments((0.0, 50.0, "a")), 1: _segments((10.0, 20.0, "b"))})
        assert tcs.resume_from(self.SCOPE)["resumed_units"] == 1

    @pytest.mark.parametrize("bad", [[], "text", [{"start": 1}], [{"start": 5, "end": 1, "text": "x"}]])
    def test_a_damaged_unit_stops_the_resume_there(self, isolated_db, bad):
        self._store(isolated_db, {0: _segments((0.0, 5.0, "a")), 1: bad})
        assert tcs.resume_from(self.SCOPE)["resumed_units"] == 1

    def test_nothing_stored_is_a_fresh_start(self, isolated_db):
        assert tcs.resume_from(self.SCOPE) == {
            "scope": self.SCOPE, "segments": [], "resumed_units": 0, "record": True}


class TestWorkerToDatabase:
    def test_a_unit_sent_from_the_worker_is_stored_by_the_watcher(self, isolated_db):
        q = queue.Queue()
        process_worker.report_checkpoint_unit(q, "transcribe:1:abc", 3, _segments((0.0, 1.0, "hi")))
        item = q.get_nowait()
        assert job_runner._apply_progress_item("job", item) is True
        assert job_checkpoint_service.done_units("transcribe:1:abc") == {
            "3": _segments((0.0, 1.0, "hi"))}

    def test_a_unit_that_cannot_be_stored_does_not_kill_the_watcher(self, isolated_db, monkeypatch):
        import db

        def boom(*a):
            raise OSError("disk full")

        monkeypatch.setattr(db, "record_checkpoint_unit", boom)
        assert job_runner._apply_progress_item("job", ("unit", "s", "0", [])) is True


class TestThroughTheWorker:
    """A run that dies after two units, then the same run again."""

    def _run_worker(self, monkeypatch, tmp_path, checkpoint, decode):
        monkeypatch.setattr(process_worker, "start_own_process_group", lambda: None)
        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: object())
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", decode)
        q = queue.Queue()
        transcribe_pipeline._transcribe_worker(
            "a.wav", "whisper", None, "zh", "simplified", "small", 5, 300, 0.5, False, "auto",
            False, False, False, "", False, "whisper", "whisper_diff", None, 1, False, False,
            2.0, 0.35, False, False, "normal", "standard", None, None, checkpoint,
            str(tmp_path / "scratch"), q)
        items = []
        while not q.empty():
            items.append(q.get_nowait())
        return items

    def test_units_leave_the_worker_and_a_second_run_continues_after_them(self, isolated_db, monkeypatch,
                                                                         tmp_path):
        class Crash(Exception):
            pass

        def dies_after_two_units(path, *a, resume_segments=None, on_unit=None, **k):
            assert not resume_segments
            on_unit(_segments((0.0, 130.0, "a")))
            on_unit(_segments((130.0, 260.0, "b")))
            raise Crash()

        scope = "transcribe:7:key"
        fresh = {"scope": scope, "segments": [], "resumed_units": 0, "record": True}
        items = self._run_worker(monkeypatch, tmp_path, fresh, dies_after_two_units)
        assert items[-1][0] == "error"
        for item in items:
            job_runner._apply_progress_item("job", item)

        resume = tcs.resume_from(scope)
        assert resume["resumed_units"] == 2 and [s["text"] for s in resume["segments"]] == ["a", "b"]

        seen = {}

        def finishes(path, *a, resume_segments=None, on_unit=None, **k):
            seen["resume"] = resume_segments
            on_unit(_segments((260.0, 400.0, "c")))
            return list(resume_segments) + _segments((260.0, 400.0, "c"))

        items = self._run_worker(monkeypatch, tmp_path, resume, finishes)
        assert [s["text"] for s in seen["resume"]] == ["a", "b"]
        assert items[-1][0] == "ok"
        for item in items:
            job_runner._apply_progress_item("job", item)
        assert sorted(job_checkpoint_service.done_units(scope)) == ["0", "1", "2"]

    def test_a_retry_that_does_not_record_leaves_the_stored_units_alone(self, isolated_db, monkeypatch,
                                                                       tmp_path):
        def finishes(path, *a, resume_segments=None, on_unit=None, **k):
            assert on_unit is None
            return _segments((0.0, 5.0, "a"))

        retry = {"scope": "transcribe:7:key", "segments": [], "resumed_units": 0, "record": False}
        items = self._run_worker(monkeypatch, tmp_path, retry, finishes)
        assert items[-1][0] == "ok"
        assert not [i for i in items if i[0] == "unit"]

    def test_a_run_with_no_checkpoint_asks_for_neither(self, isolated_db, monkeypatch, tmp_path):
        def finishes(path, *a, resume_segments=None, on_unit=None, **k):
            assert resume_segments is None and on_unit is None
            return _segments((0.0, 5.0, "a"))

        self._run_worker(monkeypatch, tmp_path, None, finishes)


class TestSavedRunClearsItsUnits:
    def test_applying_the_outcome_clears_every_attempt_of_the_title(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_checkpoint_service.record_unit(f"transcribe:{did}:old", 0, _segments((0.0, 1.0, "x")))
        job_checkpoint_service.record_unit(f"transcribe:{did + 1}:other", 0, _segments((0.0, 1.0, "y")))
        tcs.clear(did)
        assert job_checkpoint_service.done_units(f"transcribe:{did}:old") == {}
        assert job_checkpoint_service.done_units(f"transcribe:{did + 1}:other") != {}


class TestUnitValidation:
    @pytest.mark.parametrize("words", [[{"start": 0.0}], "x", [1], [{"start": 2.0, "end": 1.0}],
                                       [{"start": 0.0, "end": 99.0}]])
    def test_a_unit_with_unusable_word_times_is_rejected(self, words):
        seg = {"start": 0.0, "end": 5.0, "text": "a", "words": words}
        assert not tcs._usable([seg], 0.0)

    def test_a_unit_with_sound_words_is_kept(self):
        seg = {"start": 0.0, "end": 5.0, "text": "a", "words": [{"start": 0.1, "end": 4.9, "word": "a"}]}
        assert tcs._usable([seg], 0.0)

    def test_a_database_error_means_no_resume_not_a_failed_start(self, monkeypatch, tmp_path):
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"x")
        names = list(__import__("inspect").signature(tcs._transcribe_worker).parameters)
        args = tuple("whisper" if n in ("transcript_mode", "asr_backend_choice") else False
                     if n in ("whisper_fast_mode", "use_groq", "mixed_languages") else None for n in names)
        import sqlite3
        monkeypatch.setattr(job_checkpoint_service, "done_units",
                            lambda scope: (_ for _ in ()).throw(sqlite3.OperationalError("locked")))
        assert tcs.for_run(1, args, audio) is None
