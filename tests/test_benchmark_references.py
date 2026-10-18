"""
A title's corrected lines as Benchmark Lab transcription references
(services/benchmark_reference_service.py) and auto-tune scoring by CER.
"""
import os
import subprocess

import pytest

import db
import raw_transcript
from core import Line
from services import autotune_service as at
from services import benchmark_lab_service as lab
from services import benchmark_reference_service as refs
from services.service_errors import DependencyUnavailableError, InvalidInputError

# (raw text the recogniser heard, text now in the line)
LINES = [
    ("你好吗", "你好吗"),          # untouched
    ("我们走吧。", "我们走吧"),    # punctuation only
    ("今天天汽很好", "今天天气很好"),  # corrected
    ("再見", "再见"),              # Traditional vs Simplified only
    ("他说什么", "他到底说了什么"),   # corrected
]


def _title(isolated_db, lines=LINES, with_audio=True, **fields):
    did = db.create_drama(title_en="Moon Drama", source_language="zh",
                          audio_filename="audio.wav" if with_audio else None, **fields)
    ddir = db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    if with_audio:
        open(os.path.join(ddir, "audio.wav"), "wb").write(b"RIFF")
    raw = [Line(i, float(i * 3), i * 3 + 2.0, heard) for i, (heard, _now) in enumerate(lines)]
    db.save_lines(did, raw)
    saved = db.load_lines(did)
    segments = [{"start": r.start, "end": r.end, "text": r.zh} for r in raw]
    raw_transcript.write_raw_transcript(
        ddir, segments, [Line(r["idx"], r["start"], r["end"], heard, id=r["id"])
                         for r, (heard, _now) in zip(saved, lines)], "whisper")
    db.save_lines(did, [Line(r["idx"], r["start"], r["end"], now, id=r["id"])
                        for r, (_heard, now) in zip(saved, lines)])
    return did


@pytest.fixture
def fake_ffmpeg(monkeypatch):
    calls = []

    def run(cmd, **kw):
        calls.append(cmd)
        seconds = float(cmd[cmd.index("-t") + 1])
        with open(cmd[-1], "wb") as f:
            f.write(b"\0" * (44 + int(seconds * 32000)))

    monkeypatch.setattr(refs.subprocess, "run", run)
    monkeypatch.setattr(refs.shutil, "which", lambda name: "/usr/bin/ffmpeg")
    return calls


class TestWhichLinesAreReferences:
    def test_only_lines_whose_compared_text_changed(self, isolated_db):
        did = _title(isolated_db)
        found = refs.reference_lines(did)
        assert [(r["raw"], r["edited"]) for r in found["lines"]] == [
            ("今天天汽很好", "今天天气很好"), ("他说什么", "他到底说了什么")]
        assert found["usable_lines"] == 5 and found["raw_available"] is True

    def test_flagged_and_sound_effect_lines_are_left_out(self, isolated_db):
        did = _title(isolated_db)
        changed = db.load_lines(did)
        edited = [Line(r["idx"], r["start"], r["end"], r["zh"], flag="unsure" if r["idx"] == 2 else None,
                       sfx=r["idx"] == 4, id=r["id"]) for r in changed]
        db.save_lines(did, edited)
        assert refs.reference_lines(did)["lines"] == []

    def test_no_raw_transcript_means_no_references(self, isolated_db):
        did = isolated_db.create_drama(title_en="Bare", source_language="zh")
        db.save_lines(did, [Line(0, 0.0, 1.0, "你好")])
        assert refs.reference_lines(did) == {"lines": [], "usable_lines": 0, "raw_available": False}


class TestUnreadableRawTranscript:
    def test_a_corrupt_raw_transcript_means_no_references_not_an_error(self, isolated_db):
        did = _title(isolated_db)
        ddir = db.drama_dir(did)
        path = raw_transcript.list_raw_transcripts(ddir)[-1]
        open(path, "wb").write(b'{"lines": [{"id": 1, "idx": 0, "text": "x"')
        assert refs.reference_lines(did) == {"lines": [], "usable_lines": 0, "raw_available": False}
        assert refs.title_stats(None, did)["raw_available"] is False

    def test_a_raw_line_missing_its_times_is_unavailable_too(self, isolated_db):
        import json
        did = _title(isolated_db)
        path = raw_transcript.list_raw_transcripts(db.drama_dir(did))[-1]
        data = json.load(open(path, encoding="utf-8"))
        del data["lines"][0]["start"]
        json.dump(data, open(path, "w", encoding="utf-8"))
        assert refs.reference_lines(did)["raw_available"] is False

    def test_autotune_starts_as_before_with_a_corrupt_raw_transcript(self, isolated_db, monkeypatch):
        from jobs import job_launch
        did = _title(isolated_db)
        open(raw_transcript.list_raw_transcripts(db.drama_dir(did))[-1], "wb").write(b"{")
        captured = {}
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda job_id, target, args=(), **kw: captured.update(target=target) or True)
        at.start_autotune_run(did, candidates=[300])
        assert captured["target"].keywords == {"references": [], "corrected_lines": 0}


class TestRawToEditedRate:
    def test_counts_changed_characters_over_corrected_characters(self, isolated_db):
        did = _title(isolated_db)
        rate = refs.raw_to_edited_cer(refs.reference_lines(did)["lines"])
        # 汽->气 is 1 edit over 6 characters; 他说什么 -> 他到底说了什么 is 3 edits over 7.
        assert rate == pytest.approx(4 / 13, abs=1e-3)

    def test_none_without_references(self):
        assert refs.raw_to_edited_cer([]) is None

    def test_stats_for_the_lab(self, isolated_db):
        did = _title(isolated_db)
        stats = refs.title_stats(None, did)
        assert (stats["lines_compared"], stats["lines_corrected"]) == (5, 2)
        assert stats["raw_to_edited_cer"] == pytest.approx(4 / 13, abs=1e-3)


class TestAddingLinesToASet:
    def test_each_corrected_line_becomes_a_clipped_transcription_case(self, isolated_db, fake_ffmpeg):
        did = _title(isolated_db)
        res = refs.add_title_lines(None, did, "moon-fixes")
        assert (res["added"], res["unedited_skipped"], res["skipped"]) == (2, 3, 0)
        cases = db.list_benchmark_cases("transcription")
        assert len(cases) == 2
        first = cases[0]
        assert first["reference_text"] == "今天天气很好"
        assert (first["set_name"], first["origin_drama_id"]) == ("moon-fixes", did)
        assert first["input_filename"].startswith("clip_") and first["input_filename"].endswith(".wav")
        assert os.path.isfile(os.path.join(db.BENCHMARK_DIR, first["input_filename"]))
        assert first["source_language"] == "zh"
        # The line starts at 6.0 s; the clip opens 0.25 s earlier, so the line sits at 0.25 s in.
        assert (first["ref_start"], first["ref_end"]) == (0.25, 2.25)
        assert "-ss" in fake_ffmpeg[0] and fake_ffmpeg[0][fake_ffmpeg[0].index("-ss") + 1] == "5.750"

    def test_clips_stay_inside_the_clip_folder_under_generated_names(self, isolated_db, fake_ffmpeg):
        did = _title(isolated_db)
        refs.add_title_lines(None, did, "moon-fixes")
        assert sorted(os.listdir(db.BENCHMARK_DIR)) == sorted(
            c["input_filename"] for c in db.list_benchmark_cases("transcription"))

    def test_adding_twice_adds_nothing(self, isolated_db, fake_ffmpeg):
        did = _title(isolated_db)
        refs.add_title_lines(None, did, "moon-fixes")
        again = refs.add_title_lines(None, did, "moon-fixes")
        assert (again["added"], again["skipped"]) == (0, 2)
        assert len(db.list_benchmark_cases("transcription")) == 2
        assert len(os.listdir(db.BENCHMARK_DIR)) == 2

    def test_a_preview_counts_and_writes_nothing(self, isolated_db, fake_ffmpeg):
        did = _title(isolated_db)
        res = refs.add_title_lines(None, did, "moon-fixes", dry_run=True)
        assert (res["case_count"], res["added"], res["dry_run"]) == (2, 0, True)
        assert db.list_benchmark_cases("transcription") == [] and fake_ffmpeg == []

    def test_deleting_a_case_removes_only_its_own_clip(self, isolated_db, fake_ffmpeg):
        did = _title(isolated_db)
        refs.add_title_lines(None, did, "moon-fixes")
        keep, drop = db.list_benchmark_cases("transcription")
        stray = os.path.join(db.BENCHMARK_DIR, "notes.txt")
        open(stray, "w").close()
        lab.delete_case(drop["id"])
        left = sorted(os.listdir(db.BENCHMARK_DIR))
        assert left == sorted([keep["input_filename"], "notes.txt"])

    def test_a_failed_cut_leaves_no_files_or_cases(self, isolated_db, monkeypatch):
        did = _title(isolated_db)
        seen = []

        def run(cmd, **kw):
            seen.append(cmd)
            with open(cmd[-1], "wb") as f:
                f.write(b"\0" * 100000)
            if len(seen) == 2:
                raise subprocess.CalledProcessError(1, cmd)

        monkeypatch.setattr(refs.subprocess, "run", run)
        monkeypatch.setattr(refs.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        with pytest.raises(DependencyUnavailableError) as err:
            refs.add_title_lines(None, did, "moon-fixes")
        assert "audio.wav" not in str(err.value) and "line 5" in str(err.value)
        assert os.listdir(db.BENCHMARK_DIR) == []
        assert db.list_benchmark_cases("transcription") == []

    def test_a_header_only_clip_is_refused_and_names_the_line(self, isolated_db, monkeypatch):
        did = _title(isolated_db)

        def run(cmd, **kw):
            with open(cmd[-1], "wb") as f:
                f.write(b"\0" * 200)  # a WAV header plus a metadata chunk, no audio

        monkeypatch.setattr(refs.subprocess, "run", run)
        monkeypatch.setattr(refs.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        with pytest.raises(DependencyUnavailableError, match="line 3"):
            refs.add_title_lines(None, did, "moon-fixes")
        assert os.listdir(db.BENCHMARK_DIR) == []

    def test_a_second_add_to_the_same_set_is_refused_while_one_runs(self, isolated_db, monkeypatch):
        did = _title(isolated_db)
        inner = {}

        def run(cmd, **kw):
            if "second" not in inner:
                inner["second"] = True
                with pytest.raises(refs.ConflictError, match="already being added"):
                    refs.add_title_lines(None, did, "moon-fixes")
            with open(cmd[-1], "wb") as f:
                f.write(b"\0" * 200000)

        monkeypatch.setattr(refs.subprocess, "run", run)
        monkeypatch.setattr(refs.shutil, "which", lambda name: "/usr/bin/ffmpeg")
        assert refs.add_title_lines(None, did, "moon-fixes")["added"] == 2
        assert len(db.list_benchmark_cases("transcription")) == 2
        # The set is free again afterwards.
        assert refs.add_title_lines(None, did, "moon-fixes")["added"] == 0

    def test_one_add_stops_at_the_cap_and_reports_what_is_left(self, isolated_db, fake_ffmpeg, monkeypatch):
        monkeypatch.setattr(refs, "MAX_LINES_PER_ADD", 1)
        did = _title(isolated_db)
        first = refs.add_title_lines(None, did, "moon-fixes")
        assert (first["added"], first["remaining"], first["skipped"]) == (1, 1, 0)
        second = refs.add_title_lines(None, did, "moon-fixes")
        assert (second["added"], second["remaining"], second["skipped"]) == (1, 0, 1)

    def test_refusals(self, isolated_db, fake_ffmpeg, monkeypatch):
        untouched = _title(isolated_db, lines=[("你好", "你好")])
        with pytest.raises(InvalidInputError, match="No corrected lines"):
            refs.add_title_lines(None, untouched, "x")
        silent = _title(isolated_db, with_audio=False)
        with pytest.raises(InvalidInputError, match="no audio"):
            refs.add_title_lines(None, silent, "x")
        bare = isolated_db.create_drama(title_en="Bare", source_language="zh")
        with pytest.raises(InvalidInputError, match="original transcription"):
            refs.add_title_lines(None, bare, "x")
        monkeypatch.setattr(refs.shutil, "which", lambda name: None)
        with pytest.raises(DependencyUnavailableError, match="ffmpeg"):
            refs.add_title_lines(None, _title(isolated_db), "x")


class TestAutotuneScoredByCer:
    REFS = [{"id": 1, "idx": 0, "start": 0.0, "end": 2.0, "language": "zh", "raw": "今天天汽", "edited": "今天天气"},
            {"id": 2, "idx": 1, "start": 3.0, "end": 5.0, "language": "zh", "raw": "他说", "edited": "他到底说"}]

    def test_segments_are_placed_by_midpoint_and_scored(self):
        exact = [{"start": 0.0, "end": 2.0, "text": "今天天气"}, {"start": 3.0, "end": 5.0, "text": "他到底说"}]
        assert refs.score_against_references(exact, self.REFS)["cer"] == 0.0
        split = [{"start": 0.0, "end": 1.0, "text": "今天"}, {"start": 1.0, "end": 2.0, "text": "天气"},
                 {"start": 3.0, "end": 5.0, "text": "他说"}]
        # Two characters missing out of eight.
        assert refs.score_against_references(split, self.REFS)["cer"] == pytest.approx(0.25)

    def test_a_line_nothing_was_heard_in_counts_as_missed(self):
        only_first = [{"start": 0.0, "end": 2.0, "text": "今天天气"}]
        assert refs.score_against_references(only_first, self.REFS)["cer"] == pytest.approx(0.5)

    def test_candidates_are_ranked_by_cer_when_there_are_references(self):
        results = [
            at.score_autotune_segments(300, [{"start": 0.0, "end": 2.0, "text": "今天天汽"}], self.REFS),
            at.score_autotune_segments(800, [{"start": 0.0, "end": 2.0, "text": "今天天气"},
                                             {"start": 3.0, "end": 5.0, "text": "他到底说"}], self.REFS),
        ]
        assert [r["cer"] for r in results] == [pytest.approx(0.625), 0.0]
        assert at.best_candidate(results) == 800

    def test_without_references_it_still_ranks_by_long_lines(self):
        results = [at.score_autotune_segments(300, [{"start": 0.0, "end": 30.0, "text": "你好"}]),
                   at.score_autotune_segments(800, [{"start": 0.0, "end": 2.0, "text": "你好"}])]
        assert all("cer" not in r for r in results)
        assert at.best_candidate(results) == 800

    @staticmethod
    def _started(isolated_db, monkeypatch, corrected):
        from jobs import job_launch
        lines = [(f"原文{i}字", f"改文{i}字") for i in range(corrected)] + [("没改", "没改")]
        did = _title(isolated_db, lines=lines)
        captured = {}
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda job_id, target, args=(), **kw: captured.update(target=target) or True)
        at.start_autotune_run(did, candidates=[300])
        return captured["target"].keywords

    def test_four_corrected_lines_are_too_few_to_score_by(self, isolated_db, monkeypatch):
        kw = self._started(isolated_db, monkeypatch, at.MIN_REFERENCE_LINES - 1)
        assert kw["references"] == [] and kw["corrected_lines"] == 4

    def test_five_corrected_lines_are_scored_by_cer(self, isolated_db, monkeypatch):
        kw = self._started(isolated_db, monkeypatch, at.MIN_REFERENCE_LINES)
        assert [r["edited"] for r in kw["references"]][0] == "改文0字"
        assert len(kw["references"]) == 5 and kw["corrected_lines"] == 5

    def test_scored_by_says_long_lines_when_no_candidate_has_a_cer(self, monkeypatch):
        class Q:
            items = []
            def put(self, item): self.items.append(item)
        monkeypatch.setattr(at.whisper_transcribe, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "你好"}])
        empty = [{"id": 1, "idx": 0, "start": 0.0, "end": 1.0, "language": "zh", "raw": "。", "edited": "！"}]
        q = Q()
        at._autotune_all_worker("a", "small", "zh", False, None, "", 5, [300], 0.5, False, "normal", False,
                                q, references=empty, corrected_lines=1)
        assert q.items[-1][1]["scored_by"] == "long_lines"

    def test_status_reports_how_it_was_scored(self, isolated_db, monkeypatch):
        import background_jobs
        did = _title(isolated_db)
        monkeypatch.setattr(background_jobs, "get_status", lambda jid: {
            "status": "done", "result": {
                "results": [{"candidate_ms": 300, "long_lines": 1, "total_lines": 5, "cer": 0.1}],
                "best_candidate_ms": 300, "scored_by": "cer", "reference_lines": 2}})
        res = at.get_autotune_status(did)["result"]
        assert (res["scored_by"], res["reference_lines"], res["results"][0]["cer"]) == ("cer", 2, 0.1)
