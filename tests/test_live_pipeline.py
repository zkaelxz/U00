"""
tests/test_live_pipeline.py -- Live translation runs on its own thread: Whisper for
the next chunk never waits for the translator, cues still land in source order, a
backlog of translations is shown untranslated (counted) instead of growing, and
Stop, cancel, stream end and an error each leave no thread behind.

Whisper, the stream, ffmpeg and the engine are fakes; chunk files are real WAVs.
Every wait is on an event or a condition, never a fixed sleep.
"""
import os
import sys
import threading
import time
import wave

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import background_jobs
import core
import live_pipeline
import live_translate as lt
import live_whisper
import translate_engines
from engine_backends import local
from engine_backends.shared import request_translations_with_retry

RATE = 8000
PROMPT = 3.0


def _wait(cond, timeout=8.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        if cond():
            return True
        time.sleep(0.01)
    return cond()


def _wav(path, seconds=10):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"\x00\x00" * int(RATE * seconds))


class FakeProc:
    def __init__(self):
        self.error = None
        self.code = None

    def poll(self):
        return self.code


class Stream:
    def __init__(self, out_dir):
        self.out_dir = out_dir
        os.makedirs(out_dir, exist_ok=True)
        self.count = 0

    def add(self, n=1, seconds=10):
        for _ in range(n):
            _wav(os.path.join(self.out_dir, f"chunk_{self.count:05d}.wav"), seconds)
            self.count += 1


def _translator_threads():
    return [t for t in threading.enumerate() if t.name == "live-translate" and t.is_alive()]


@pytest.fixture
def job(isolated_db, monkeypatch, tmp_path):
    background_jobs.clear_all_jobs()
    proc = FakeProc()
    stops = []
    monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://stream")
    monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: proc)
    monkeypatch.setattr(lt, "stop_capture", lambda p, **kw: stops.append(kw))
    monkeypatch.setattr(live_whisper, "warm_up", lambda size, gpu, cb=None: None)
    monkeypatch.setattr(live_whisper, "POLL_SECONDS", 0.005)
    monkeypatch.setattr(live_whisper, "ABANDON_GRACE_SECONDS", 0.05)
    published, stats, notes = [], [], []
    real_set_result = background_jobs.set_result

    def record(job_id, result, **kw):
        published.append([dict(c) for c in result] if isinstance(result, list) else result)
        return real_set_result(job_id, result, **kw)
    monkeypatch.setattr(background_jobs, "set_result", record)
    out_dir = str(tmp_path / "chunks")

    def start(job_id, engine, **kw):
        kw.setdefault("overlap_seconds", 0)
        kw.setdefault("poll_interval", 0.02)
        return background_jobs.start_job(
            job_id, lt.run_live_job, job_id, "http://example.com/live", out_dir, 10, "zh",
            "small", engine, report_note=lambda text, key=None: notes.append(text),
            report_stats=lambda numbers: stats.append(dict(numbers)), **kw)

    start.proc, start.stops, start.published, start.stats, start.notes = proc, stops, published, stats, notes
    yield Stream(out_dir), start
    for jid in list(background_jobs._jobs):
        lt.bump_generation(jid)
        background_jobs.request_cancel(jid)
    _wait(lambda: not background_jobs.list_running_jobs(), 5)
    background_jobs.clear_all_jobs()


def _result(job_id):
    return background_jobs.get_status(job_id).get("result") or []


def _ended(job_id, timeout=8.0):
    return _wait(lambda: background_jobs.get_status(job_id)["status"] != "running", timeout)


def _whisper_by_chunk(monkeypatch, cues_per_chunk=2, block=None):
    """Whisper that returns `cues_per_chunk` lines named after the chunk; `seen`
    gets the chunk index of every call and `called[idx]` is set as it starts."""
    seen, called = [], {}

    def whisper(path, **kw):
        idx = int(os.path.basename(path)[-9:-4])
        seen.append(idx)
        called.setdefault(idx, threading.Event()).set()
        if block is not None and idx in block:
            block[idx].wait(20)
        return [{"start": i + 0.1, "end": i + 0.9, "text": f"c{idx}l{i}"} for i in range(cues_per_chunk)]
    monkeypatch.setattr(core, "transcribe_for_timing", whisper)

    def started(idx):
        return called.setdefault(idx, threading.Event())
    return seen, started


class Gate:
    """An engine whose request for a line waits until the test opens its gate."""
    name = "fake"

    def __init__(self, held=()):
        self.held = set(held)
        self.entered = {t: threading.Event() for t in held}
        self.open = {t: threading.Event() for t in held}
        self.calls = []

    def translate_batch(self, lines, context):
        self.calls.append((list(lines), list(context.get("line_ids") or [])))
        for text in lines:
            if text in self.held:
                self.entered[text].set()
                self.open[text].wait(20)
        return [f"EN {t}" for t in lines]


class TestTranslationOnItsOwnThread:
    def test_whisper_for_the_next_chunk_starts_while_the_previous_chunk_is_translated(
            self, job, monkeypatch):
        stream, start = job
        seen, whisper_started = _whisper_by_chunk(monkeypatch)
        engine = Gate(held=["c0l0"])
        stream.add(3)
        assert start("live_overlap", engine)
        assert engine.entered["c0l0"].wait(PROMPT)
        # Chunk 0's translation is still held; Whisper must reach chunk 1 anyway.
        assert whisper_started(1).wait(PROMPT)
        assert _wait(lambda: len(_result("live_overlap")) == 4)
        assert not engine.open["c0l0"].is_set()
        assert [c["translation"] for c in _result("live_overlap")] == ["pending"] * 4
        engine.open["c0l0"].set()
        assert _wait(lambda: [c["translation"] for c in _result("live_overlap")] == ["done"] * 4)
        result = _result("live_overlap")
        assert [c["id"] for c in result] == [0, 1, 2, 3]
        assert [c["translated"] for c in result] == ["EN c0l0", "EN c0l1", "EN c1l0", "EN c1l1"]

    def test_every_published_list_is_in_source_order_and_never_loses_a_cue(self, job, monkeypatch):
        stream, start = job
        _whisper_by_chunk(monkeypatch)
        engine = Gate(held=["c0l0", "c1l0"])
        stream.add(2)
        assert start("live_order", engine)
        assert engine.entered["c0l0"].wait(PROMPT)
        stream.add(2)
        assert _wait(lambda: len(_result("live_order")) == 6)
        engine.open["c0l0"].set()
        assert engine.entered["c1l0"].wait(PROMPT)
        engine.open["c1l0"].set()
        assert _wait(lambda: [c["translation"] for c in _result("live_order")] == ["done"] * 6)
        for snapshot in start.published:
            ids = [c["id"] for c in snapshot]
            assert ids == list(range(len(ids)))
        # Translations land chunk by chunk, oldest first.
        done_order = []
        for snapshot in start.published:
            for c in snapshot:
                if c["translation"] == "done" and c["id"] not in done_order:
                    done_order.append(c["id"])
        assert done_order == sorted(done_order)

    def test_a_failed_translation_keeps_the_transcript_with_a_redacted_note(self, job, monkeypatch):
        stream, start = job
        _whisper_by_chunk(monkeypatch, cues_per_chunk=1)

        class Down:
            name = "fake"

            def translate_batch(self, lines, context):
                raise RuntimeError("api down, key sk-ant-abcdefghijklmnopqrstu")
        stream.add(3)
        assert start("live_fail", Down())
        assert _wait(lambda: len(_result("live_fail")) == 2
                     and all(c["translation"] == "failed" for c in _result("live_fail")))
        for c in _result("live_fail"):
            assert c["text"].startswith("c") and c["translated"].startswith("[translation failed")
            assert "sk-ant" not in c["translated"]

    def test_a_full_queue_skips_the_oldest_waiting_chunk_and_whisper_never_waits(
            self, job, monkeypatch):
        stream, start = job
        monkeypatch.setattr(live_pipeline, "MAX_TRANSLATE_BACKLOG", 1)
        seen, whisper_started = _whisper_by_chunk(monkeypatch)
        engine = Gate(held=["c0l0"])
        stream.add(2)
        assert start("live_full", engine)
        assert engine.entered["c0l0"].wait(PROMPT)
        # Chunks 1, 2 and 3 are transcribed while chunk 0 is still being translated.
        for idx in (1, 2, 3):
            stream.add(1)
            assert whisper_started(idx).wait(PROMPT)
        assert _wait(lambda: [c["translation"] for c in _result("live_full")][2:6]
                     == ["skipped", "skipped", "skipped", "skipped"])
        assert _wait(lambda: start.stats and start.stats[-1]["translations_skipped"] == 4)
        engine.open["c0l0"].set()
        assert _wait(lambda: [c["translation"] for c in _result("live_full")]
                     == ["done", "done", "skipped", "skipped", "skipped", "skipped", "done", "done"])
        skipped = [c for c in _result("live_full") if c["translation"] == "skipped"]
        assert all(c["translated"] == "" and c["text"] for c in skipped)
        assert any("Skipped translating 4 lines" in n for n in start.notes), start.notes
        # The engine was never asked for the skipped lines.
        asked = {t for lines, _ in engine.calls for t in lines}
        assert not asked & {"c1l0", "c1l1", "c2l0", "c2l1"}


class TestStopsLeaveNothingBehind:
    @staticmethod
    def _abortable(entered):
        """Like Ollama: a request that polls the abort check the job sets."""
        class Abortable:
            name = "fake"

            def translate_batch(self, lines, context):
                entered.set()
                check = local.abort_check_var.get()
                assert check is not None, "the worker thread must carry the abort check"
                while not check():
                    time.sleep(0.005)
                raise translate_engines.TranslationCancelled("cancelled")
        return Abortable()

    def test_stop_now_ends_the_worker_and_publishes_nothing_more(self, job, monkeypatch):
        stream, start = job
        _whisper_by_chunk(monkeypatch)
        entered = threading.Event()
        stream.add(2)
        assert start("live_stopnow", self._abortable(entered))
        assert entered.wait(PROMPT)
        before = len(start.published)
        lt.bump_generation("live_stopnow")
        background_jobs.request_cancel("live_stopnow")
        assert _ended("live_stopnow", PROMPT)
        assert _translator_threads() == []
        assert len(start.published) == before
        assert start.stops == [{"kill": True}]

    def test_a_plain_cancel_marks_the_untranslated_lines_cancelled(self, job, monkeypatch):
        stream, start = job
        _whisper_by_chunk(monkeypatch)
        entered = threading.Event()
        stream.add(2)
        assert start("live_cancel", self._abortable(entered))
        assert entered.wait(PROMPT)
        background_jobs.request_cancel("live_cancel")
        assert _ended("live_cancel", PROMPT)
        assert _translator_threads() == []
        assert [c["translation"] for c in _result("live_cancel")] == ["cancelled", "cancelled"]

    def test_stop_once_transcribes_the_waiting_chunks_and_the_final_partial_one(
            self, job, monkeypatch):
        stream, start = job
        hold = {0: threading.Event()}
        seen, whisper_started = _whisper_by_chunk(monkeypatch, cues_per_chunk=1, block=hold)
        engine = Gate()
        stream.add(3)   # 0 and 1 complete, 2 is the one ffmpeg was still writing
        assert start("live_finish", engine)
        assert whisper_started(0).wait(PROMPT)
        assert live_pipeline.request_finish("live_finish")
        assert not live_pipeline.request_finish("live_finish")   # once only
        hold[0].set()
        assert _ended("live_finish")
        assert seen == [0, 1, 2]
        assert [(c["text"], c["translation"]) for c in _result("live_finish")] == [
            ("c0l0", "done"), ("c1l0", "done"), ("c2l0", "done")]
        assert background_jobs.get_status("live_finish")["cancel_requested"] is False
        assert start.stops == [{}]   # a graceful stop: ffmpeg flushes its last segment
        assert _translator_threads() == []

    def test_the_stream_ending_captions_the_final_chunk(self, job, monkeypatch):
        stream, start = job
        seen, _ = _whisper_by_chunk(monkeypatch, cues_per_chunk=1)
        stream.add(2)
        assert start("live_end", Gate())
        assert _wait(lambda: [c["translation"] for c in _result("live_end")] == ["done"])
        start.proc.code = 0
        assert _ended("live_end")
        assert seen == [0, 1]
        assert [c["translation"] for c in _result("live_end")] == ["done", "done"]
        assert _translator_threads() == []

    def test_an_error_drops_the_waiting_chunks(self, job, monkeypatch):
        stream, start = job
        hold = {0: threading.Event()}
        seen, whisper_started = _whisper_by_chunk(monkeypatch, cues_per_chunk=1, block=hold)
        stream.add(3)
        assert start("live_err", Gate())
        assert whisper_started(0).wait(PROMPT)
        start.proc.error = "The stream stopped sending data."
        hold[0].set()
        assert _ended("live_err")
        assert background_jobs.get_status("live_err")["status"] == "error"
        assert seen == [0]
        assert _translator_threads() == []

    def test_a_finish_request_wakes_an_idle_loop_at_once(self, job, monkeypatch):
        stream, start = job
        _whisper_by_chunk(monkeypatch, cues_per_chunk=1)
        stream.add(2)
        # A poll this long would hold the job for a minute if Stop did not wake it.
        assert start("live_wake", Gate(), poll_interval=60)
        assert _wait(lambda: [c["translation"] for c in _result("live_wake")] == ["done"])
        assert _wait(lambda: "Capturing audio" in background_jobs.get_status("live_wake")["message"])
        began = time.monotonic()
        assert live_pipeline.request_finish("live_wake")
        assert _ended("live_wake", PROMPT)
        assert time.monotonic() - began < PROMPT

    def test_the_idle_wait_defaults_to_a_quarter_second(self):
        import inspect
        assert inspect.signature(lt.run_live_job).parameters["poll_interval"].default == 0.25


class TestLagFigures:
    def test_stats_are_numbers_only_and_follow_the_chunks(self, job, monkeypatch):
        stream, start = job
        _whisper_by_chunk(monkeypatch, cues_per_chunk=1)
        stream.add(2)
        assert start("live_stats", Gate())
        for done in (1, 2, 3):
            assert _wait(lambda: len([c for c in _result("live_stats")
                                      if c["translation"] == "done"]) == done)
            stream.add(1)
        assert _wait(lambda: start.stats and start.stats[-1]["chunks_measured"] == 3)
        last = start.stats[-1]
        assert set(last) == {"lag_seconds", "processing_seconds", "translations_skipped",
                             "translate_queue", "chunks_measured"}
        assert all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in last.values())
        # The lag of the newest chunk is at least its own audio (10 s).
        assert last["lag_seconds"] >= 10.0
        assert 0.0 <= last["processing_seconds"] < last["lag_seconds"]

    def test_the_median_is_rolling_over_the_last_chunks(self):
        stats = live_pipeline.LagStats(lambda numbers: None)
        for total in (100.0, 1.0, 2.0, 3.0, 4.0, 5.0):
            stats.chunk_done(10.0, total)
        assert stats.figures()["processing_seconds"] == 3.0   # 100 s fell out of the window
        assert stats.figures()["lag_seconds"] == 15.0


class TestOneRequestPerChunk:
    def test_a_chunk_is_one_id_keyed_request(self, job, monkeypatch):
        stream, start = job
        _whisper_by_chunk(monkeypatch, cues_per_chunk=3)
        engine = Gate()
        stream.add(2)
        assert start("live_batch", engine)
        assert _wait(lambda: [c["translation"] for c in _result("live_batch")] == ["done"] * 3)
        assert engine.calls == [(["c0l0", "c0l1", "c0l2"], [1, 2, 3])]


class IdKeyedEngine:
    """The real id-keyed request path with a scripted model reply."""
    name = "fake"

    def __init__(self, reply):
        self.reply = reply
        self.requests = []

    def translate_batch(self, lines, context):
        def call_model(numbered):
            self.requests.append(numbered)
            return self.reply(numbered)
        return request_translations_with_retry(lines, None, call_model, max_retries=0,
                                               line_ids=context.get("line_ids"))


class TestChunkTranslator:
    def _feed(self, engine, texts):
        import live_cue_feed
        import live_cue_translation
        cues = live_cue_feed.pending_cues(
            [{"start": i, "end": i + 1, "text": t} for i, t in enumerate(texts)], 0.0, 0)
        translator = live_cue_translation.CueTranslator(engine, "zh")
        live_cue_feed.translate_cues(cues, translator, lambda: None)
        return cues, translator

    def test_a_reordered_reply_is_matched_by_id(self):
        import json
        engine = IdKeyedEngine(lambda numbered: json.dumps({"3": "C", "1": "A", "2": "B"}))
        cues, _ = self._feed(engine, ["a", "b", "c"])
        assert [c["translated"] for c in cues] == ["A", "B", "C"]
        assert len(engine.requests) == 1

    def test_only_the_missing_ids_are_asked_again_one_by_one(self):
        import json

        def reply(numbered):
            if numbered.count("\n") >= 1:   # the whole chunk: id 2 is left out
                return json.dumps({"1": "A", "3": "C"})
            line_id = numbered.split(".", 1)[0]
            return json.dumps({line_id: "B"})
        engine = IdKeyedEngine(reply)
        cues, _ = self._feed(engine, ["a", "b", "c"])
        assert [(c["translated"], c["translation"]) for c in cues] == [
            ("A", "done"), ("B", "done"), ("C", "done")]
        assert len(engine.requests) == 2 and "b" in engine.requests[1]
        assert "a" not in engine.requests[1] and "c" not in engine.requests[1]

    def test_a_failed_chunk_request_falls_back_to_one_request_per_cue(self):
        calls = []

        class FailsOnBatches:
            name = "fake"

            def translate_batch(self, lines, context):
                calls.append(list(lines))
                if len(lines) > 1:
                    raise RuntimeError("too long")
                return [f"EN {lines[0]}"]
        cues, _ = self._feed(FailsOnBatches(), ["a", "b"])
        assert [c["translated"] for c in cues] == ["EN a", "EN b"]
        assert calls == [["a", "b"], ["a"], ["b"]]

    def test_the_chunk_request_carries_the_last_four_pairs_as_context(self):
        contexts = []

        class Capturing:
            name = "fake"

            def translate_batch(self, lines, context):
                contexts.append(context)
                return [f"EN {t}" for t in lines]
        import live_cue_feed
        import live_cue_translation
        translator = live_cue_translation.CueTranslator(Capturing(), "zh")
        for chunk in (["a", "b", "c"], ["d", "e", "f"]):
            cues = live_cue_feed.pending_cues(
                [{"start": 0, "end": 1, "text": t} for t in chunk], 0.0, 0)
            live_cue_feed.translate_cues(cues, translator, lambda: None)
        assert [s for s, _ in contexts[-1]["recent_context"]] == ["a", "b", "c"]
        assert contexts[-1]["line_ids"] == [4, 5, 6]
        assert contexts[-1]["recent_as_data"] is True
