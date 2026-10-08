"""Transcript first, translation filled in later, in the same cue."""
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
