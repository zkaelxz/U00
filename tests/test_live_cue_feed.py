"""Transcript first, translation filled in later, in the same cue."""
import copy
import threading
import time

import pytest

import background_jobs
from jobs import job_launch, job_runner
import whisper_transcribe
from live import live_cue_feed
from live import live_translate as lt


class FakeEngine:
    def __init__(self, fail_on=(), hold_prefix=None, gate=None):
        self.fail_on = fail_on
        self.hold_prefix = hold_prefix
        self.gate = gate

    def translate_batch(self, lines, context):
        if self.hold_prefix and lines[0].startswith(self.hold_prefix):
            self.gate.wait(5)
        if lines[0] in self.fail_on:
            raise RuntimeError("api down")
        return [f"EN {lines[0]}"]


@pytest.fixture
def two_segments(monkeypatch, tmp_path):
    monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", lambda path, **kw: [
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
    monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", lambda path, **kw: [
        {"start": 0.0, "end": 1.0, "text": f"{path[-9:-4]}a"},
        {"start": 1.0, "end": 2.0, "text": f"{path[-9:-4]}b"}])
    published = []
    real_set_result = background_jobs.set_result

    def record(job_id, result, **kw):
        published.append(copy.deepcopy(result))
        return real_set_result(job_id, result, **kw)
    monkeypatch.setattr(background_jobs, "set_result", record)

    # Chunk 2's translation is held until its pending cues have been published, so a snapshot
    # with chunk 1 settled and chunk 2 pending must exist whatever the thread scheduling.
    gate = threading.Event()
    engine = FakeEngine(hold_prefix="00001", gate=gate)
    assert job_launch.start_job("live_ids", lt.run_live_job, "live_ids", "http://example.com/live",
                                     str(out_dir), 10, "zh", "small", engine,
                                     poll_interval=0.05, overlap_seconds=0)

    def settled_then_pending():
        return [r for r in list(published) if len(r) == 4
                and [c["translation"] for c in r] == ["done", "done", "pending", "pending"]]
    end = time.monotonic() + 5
    while time.monotonic() < end and not settled_then_pending():
        time.sleep(0.02)
    gate.set()
    while time.monotonic() < end and not (published and len(published[-1]) == 4
                                          and all(c["translation"] == "done" for c in published[-1])):
        time.sleep(0.02)
    background_jobs.request_cancel("live_ids")
    job_runner.wait_for_job_threads(5, ["live_ids"])

    final = published[-1]
    assert [c["id"] for c in final] == [0, 1, 2, 3]
    # While the second chunk was still being translated, its pending cues sat after the settled first chunk.
    mid = settled_then_pending()
    assert mid
    assert [c["id"] for c in mid[0]] == [0, 1, 2, 3]
    assert all(len(r) <= 4 for r in published)


def test_a_blank_reply_is_shown_as_failed_not_as_done(monkeypatch, tmp_path):
    class Blank(FakeEngine):
        def translate_batch(self, lines, context):
            return ["" if lines[0] == "一" else f"EN {lines[0]}"]
    monkeypatch.setattr(whisper_transcribe, "transcribe_for_timing", lambda path, **kw: [
        {"start": 0.0, "end": 1.0, "text": "一"}])
    chunk = tmp_path / "chunk_00000.wav"
    chunk.write_bytes(b"x")
    cues = lt.process_chunk(str(chunk), 0, 20, "zh", "small", Blank())
    assert cues[0]["translation"] == "failed"
    assert cues[0]["translated"] == live_cue_feed.EMPTY_REPLY_NOTE


def test_a_reply_copied_from_the_next_line_is_asked_again_on_its_own():
    """TranslateGemma 27B on Korean news put the next line's English under a short
    fragment's id (Yonhap, 32 s and 34 s): that cue is retried by itself."""
    from live.live_cue_translation import CueTranslator
    calls = []

    class Engine:
        def translate_batch(self, lines, context):
            calls.append(list(lines))
            if len(lines) == 1:
                return ["I'm Lee Jae-kyung."]
            return ["Reporting from Seoul.", "AI cheating fears grow.", "AI cheating fears grow."]

    cues = live_cue_feed.pending_cues(
        [{"start": 0, "end": 1, "text": "서울에서 전했습니다."}, {"start": 1, "end": 2, "text": "이재경입니다."},
         {"start": 3, "end": 5, "text": "A.I. 부정행위 논란이 커집니다."}], 0, 0)
    live_cue_feed.translate_cues(cues, CueTranslator(Engine(), "ko"), lambda: None)
    assert [c["translated"] for c in cues] == [
        "Reporting from Seoul.", "I'm Lee Jae-kyung.", "AI cheating fears grow."]
    assert calls[1:] == [["이재경입니다."]]


def test_two_lines_that_really_say_the_same_keep_the_same_translation():
    from live.live_cue_translation import CueTranslator

    class Engine:
        def translate_batch(self, lines, context):
            return ["Thank you."] * len(lines)

    cues = live_cue_feed.pending_cues(
        [{"start": 0, "end": 1, "text": "고맙습니다."}, {"start": 1, "end": 2, "text": "고맙습니다."}], 0, 0)
    live_cue_feed.translate_cues(cues, CueTranslator(Engine(), "ko"), lambda: None)
    assert [c["translated"] for c in cues] == ["Thank you.", "Thank you."]


def test_a_sentence_split_over_lines_is_translated_whole_on_its_last_line():
    """Yonhap 165 s / 171 s: the first half of a verb-final sentence, sent on its
    own, got the whole sentence's English and its own content was lost."""
    from live.live_cue_translation import CueTranslator
    sent = []

    class Engine:
        def translate_batch(self, lines, context):
            sent.append(list(lines))
            return [f"en:{t}" for t in lines]

    cues = live_cue_feed.pending_cues(
        [{"start": 0, "end": 4, "text": "젤렌스키 대통령은 무기가 러시아에 공급되고"},
         {"start": 4, "end": 8, "text": "있다면서 북한이 성능을 개선한다고 주장했습니다."},
         {"start": 9, "end": 10, "text": "다음 소식입니다."},
         {"start": 11, "end": 12, "text": "그리고"}], 0, 0)
    live_cue_feed.translate_cues(cues, CueTranslator(Engine(), "ko"), lambda: None)
    assert sent == [["젤렌스키 대통령은 무기가 러시아에 공급되고 있다면서 북한이 성능을 개선한다고 주장했습니다.",
                     "다음 소식입니다.", "그리고"]]
    assert [(c["translated"], c["translation"]) for c in cues] == [
        (live_cue_feed.CONTINUED, "done"),
        ("en:젤렌스키 대통령은 무기가 러시아에 공급되고 있다면서 북한이 성능을 개선한다고 주장했습니다.", "done"),
        ("en:다음 소식입니다.", "done"), ("en:그리고", "done")]


def test_unpunctuated_speech_is_not_grouped_and_a_run_is_capped():
    plain = [{"text": t} for t in ("그건", "있습니까", "네")]
    assert live_cue_feed.sentence_groups(plain) == [[0], [1], [2]]
    run = [{"text": t} for t in ("a", "b", "c", "d.")]
    # Past MAX_SENTENCE_LINES the lines go one by one rather than onto one line.
    assert live_cue_feed.sentence_groups(run) == [[0], [1], [2], [3]]


def test_a_failed_sentence_shows_the_failure_on_each_of_its_lines():
    from live.live_cue_translation import CueTranslator

    class Engine:
        def translate_batch(self, lines, context):
            raise RuntimeError("down")

    cues = live_cue_feed.pending_cues(
        [{"start": 0, "end": 1, "text": "특검의 수사가 뻗어나가고"},
         {"start": 1, "end": 2, "text": "있습니다."}], 0, 0)
    live_cue_feed.translate_cues(cues, CueTranslator(Engine(), "ko"), lambda: None)
    assert [c["translation"] for c in cues] == ["failed", "failed"]


class TestSourceScriptKeptInTheReply:
    """TranslateGemma 12B on a Japanese stream: a line naming
    "プリキューキューティコマ" came back with no English, both in its chunk and
    asked alone, because the reply guard drops a reply with Japanese left in it."""

    LINE = "さてお休み前に言ってたFGOのイベントでプリキューキューティコマが実装"

    def _translate(self, monkeypatch, content):
        from engine_backends import local
        from live.live_cue_translation import CueTranslator
        bodies = []

        def chat(base_url, payload):
            bodies.append(payload)
            return {"message": {"content": '{"1": "%s"}' % content}}
        monkeypatch.setattr(local, "_ollama_chat", chat)
        translated = CueTranslator(local.OllamaEngine(model="gemma4:31b-cloud"), "ja").translate(self.LINE)
        return translated, bodies

    def test_a_name_left_in_japanese_still_gives_a_caption(self, monkeypatch):
        reply = "At the FGO event I mentioned before the break, Puri Kyu キューティコマ was added."
        translated, bodies = self._translate(monkeypatch, reply)
        assert translated == reply
        assert len(bodies) >= 2   # the guarded request(s), then one without the guard

    def test_the_line_copied_back_is_still_a_failure_with_its_reason(self, monkeypatch):
        translated, _ = self._translate(monkeypatch, self.LINE)
        assert translated == "[translation failed: the model's reply was unusable (source text left in it)]"

    def test_another_unusable_reply_names_its_reason_without_another_request(self, monkeypatch):
        refusal = "I'm sorry, but I can't translate this."
        translated, bodies = self._translate(monkeypatch, refusal)
        assert translated == "[translation failed: the model's reply was unusable (a refusal)]"
        _, with_name = self._translate(monkeypatch, "Puri Kyu キューティコマ was added.")
        assert len(bodies) == len(with_name) - 1
