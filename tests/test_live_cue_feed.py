"""Transcript first, translation filled in later, in the same cue."""
import copy
import time

import pytest

import background_jobs
import core
import live_cue_feed
import live_translate as lt


class FakeEngine:
    def __init__(self, fail_on=()):
        self.fail_on = fail_on

    def translate_batch(self, lines, context):
        if lines[0] in self.fail_on:
            raise RuntimeError("api down")
        return [f"EN {lines[0]}"]


@pytest.fixture
def two_segments(monkeypatch, tmp_path):
    monkeypatch.setattr(core, "transcribe_for_timing", lambda path, **kw: [
        {"start": 0.0, "end": 1.0, "text": "一"}, {"start": 1.0, "end": 2.0, "text": "  "},
        {"start": 2.0, "end": 3.0, "text": "二"}])
    chunk = tmp_path / "chunk_00000.wav"
    chunk.write_bytes(b"x")
    return str(chunk)


def _states(cues):
    return [(c["id"], c["text"], c["translated"], c["translation"]) for c in cues]


def test_transcript_is_published_before_any_translation(two_segments):
    seen = []
    lt.process_chunk(two_segments, 0, 20, "zh", "small", FakeEngine(), first_id=5,
                     on_cues=lambda cues: seen.append(_states(cues)))
    assert seen[0] == [(5, "一", "", "pending"), (6, "二", "", "pending")]
    assert seen[1] == [(5, "一", "EN 一", "done"), (6, "二", "", "pending")]
    assert seen[-1] == [(5, "一", "EN 一", "done"), (6, "二", "EN 二", "done")]


def test_a_failed_translation_keeps_the_transcript_and_the_next_line_goes_on(two_segments):
    cues = lt.process_chunk(two_segments, 0, 20, "zh", "small", FakeEngine(fail_on=("一",)))
    assert cues[0]["text"] == "一" and cues[0]["translation"] == "failed"
    assert cues[0]["translated"].startswith("[translation failed")
    assert cues[1]["translation"] == "done"


def test_a_cancel_marks_unfinished_cues_and_publishes_them(two_segments):
    seen = []
    calls = [0]

    def cancelled():
        calls[0] += 1
        return calls[0] >= 3   # after Whisper and the first line

    with pytest.raises(background_jobs.JobCancelled):
        lt.process_chunk(two_segments, 0, 20, "zh", "small", FakeEngine(),
                         is_cancelled=cancelled, on_cues=lambda cues: seen.append(_states(cues)))
    assert seen[-1] == [(0, "一", "EN 一", "done"), (1, "二", "", "cancelled")]


def test_snapshot_copies_so_later_changes_do_not_leak():
    chunk = [{"id": 0, "translation": "pending"}]
    snap = live_cue_feed.snapshot([], chunk)
    chunk[0]["translation"] = "done"
    assert snap[0]["translation"] == "pending"


class _Proc:
    error = None

    def poll(self):
        return None


def test_ids_run_on_across_chunks_and_a_mid_chunk_result_holds_the_earlier_chunk(monkeypatch, tmp_path, isolated_db):
    background_jobs.clear_all_jobs()
    out_dir = tmp_path / "chunks"
    out_dir.mkdir()
    # The newest chunk is still being written, so three files make two complete chunks.
    for i in range(3):
        (out_dir / f"chunk_{i:05d}.wav").write_bytes(b"")
    monkeypatch.setattr(lt, "resolve_stream_url", lambda url, **kw: "http://stream")
    monkeypatch.setattr(lt, "start_segment_capture", lambda *a, **k: _Proc())
    monkeypatch.setattr(lt, "stop_capture", lambda proc, **kw: None)
    monkeypatch.setattr(core, "transcribe_for_timing", lambda path, **kw: [
        {"start": 0.0, "end": 1.0, "text": f"{path[-9:-4]}a"},
        {"start": 1.0, "end": 2.0, "text": f"{path[-9:-4]}b"}])
    published = []
    real_set_result = background_jobs.set_result

    def record(job_id, result, **kw):
        published.append(copy.deepcopy(result))
        return real_set_result(job_id, result, **kw)
    monkeypatch.setattr(background_jobs, "set_result", record)

    assert background_jobs.start_job("live_ids", lt.run_live_job, "live_ids", "http://example.com/live",
                                     str(out_dir), 10, "zh", "small", FakeEngine(),
                                     poll_interval=0.05, overlap_seconds=0)
    end = time.monotonic() + 5
    while time.monotonic() < end and not (published and len(published[-1]) == 4
                                          and all(c["translation"] == "done" for c in published[-1])):
        time.sleep(0.02)
    background_jobs.request_cancel("live_ids")
    background_jobs.wait_for_job_threads(5, ["live_ids"])

    final = published[-1]
    assert [c["id"] for c in final] == [0, 1, 2, 3]
    # While the second chunk was still being translated, its pending cues sat after the settled first chunk.
    mid = [r for r in published if len(r) == 4 and r[2]["translation"] == "pending"]
    assert mid
    assert [c["id"] for c in mid[0]] == [0, 1, 2, 3]
    assert [c["translation"] for c in mid[0][:2]] == ["done", "done"]
    assert all(len(r) <= 4 for r in published)
