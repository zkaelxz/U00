"""
tests/test_raw_transcript.py -- Step 3 (R1-lite): the untouched output of
each transcription run is kept on disk, never overwritten, and a single
line's text can be compared against / restored from it.
"""
import contextlib
import json
import os

import raw_transcript as rt
from core import Line, merge_adjacent_short_lines


SEGMENTS = [{"start": 0.0, "end": 0.5, "text": "你好"},
            {"start": 0.6, "end": 1.1, "text": "世界"},
            {"start": 5.0, "end": 9.0, "text": "再见"}]


def _transcribed_drama(isolated_db):
    did = isolated_db.create_drama(title_en="D")
    lines = [Line(idx=i, start=s["start"], end=s["end"], zh=s["text"]) for i, s in enumerate(SEGMENTS)]
    isolated_db.save_lines(did, lines)
    ddir = isolated_db.drama_dir(did)
    path = rt.write_raw_transcript(ddir, SEGMENTS, lines, backend="whisper", model="small",
                                   language="zh", mode="whisper")
    return did, ddir, path


def _bytes(path):
    with open(path, "rb") as f:
        return f.read()


class TestNeverOverwritten:
    def test_first_run_writes_raw_transcript_json_with_its_details(self, isolated_db):
        did, ddir, path = _transcribed_drama(isolated_db)
        assert os.path.basename(path) == "raw_transcript.json"
        raw = rt.load_latest(ddir)
        assert raw["backend"] == "whisper" and raw["model"] == "small"
        assert [s["text"] for s in raw["segments"]] == ["你好", "世界", "再见"]
        assert [o["id"] for o in raw["lines"]] == [r["id"] for r in isolated_db.load_lines(did)]

    def test_a_second_run_gets_its_own_file(self, isolated_db):
        did, ddir, first = _transcribed_drama(isolated_db)
        before = _bytes(first)
        second = rt.write_raw_transcript(ddir, [{"start": 0, "end": 1, "text": "新"}],
                                         [Line(idx=0, start=0, end=1, zh="新")], backend="whisper")
        assert second != first and os.path.basename(second).startswith("raw_transcript.")
        assert _bytes(first) == before
        assert rt.list_raw_transcripts(ddir) == [first, second]
        assert rt.load_latest(ddir)["lines"][0]["text"] == "新"

    def test_edits_merges_and_retranscribes_leave_it_byte_identical(self, isolated_db):
        """The roadmap's exit condition, first half."""
        did, ddir, path = _transcribed_drama(isolated_db)
        before = _bytes(path)
        lines = isolated_db.load_line_objects(did)
        lines[2].zh = "再见了"                           # edit
        isolated_db.save_lines(did, lines)
        isolated_db.save_lines(did, merge_adjacent_short_lines(isolated_db.load_line_objects(did)))
        lines = isolated_db.load_line_objects(did)
        lines[0].zh = "re-transcribed text"            # per-line re-transcribe
        isolated_db.save_lines(did, lines)
        assert len(isolated_db.load_lines(did)) == 2
        assert _bytes(path) == before


class TestOriginalTextForLine:
    def test_matches_by_time(self, isolated_db):
        did, ddir, _ = _transcribed_drama(isolated_db)
        raw = rt.load_latest(ddir)
        edited = isolated_db.load_line_objects(did)[2]
        edited.zh = "changed"
        assert rt.original_text_for_line(raw, edited) == "再见"

    def test_a_merged_line_shows_both_original_parts(self, isolated_db):
        did, ddir, _ = _transcribed_drama(isolated_db)
        merged = merge_adjacent_short_lines(isolated_db.load_line_objects(did))
        assert merged[0].zh == "你好世界"
        assert rt.original_text_for_line(rt.load_latest(ddir), merged[0]) == "你好世界"

    def test_falls_back_to_the_line_id_when_timing_moved(self, isolated_db):
        did, ddir, _ = _transcribed_drama(isolated_db)
        moved = isolated_db.load_line_objects(did)[2]
        moved.start, moved.end = 30.0, 31.0
        assert rt.original_text_for_line(rt.load_latest(ddir), moved) == "再见"

    def test_no_transcript_means_no_original(self, isolated_db, tmp_path):
        assert rt.load_latest(str(tmp_path)) is None
        assert rt.original_text_for_line(None, Line(idx=0, start=0, end=1, zh="x")) is None


def test_word_timings_are_not_saved_with_the_raw_transcript(tmp_path):
    segs = [{"start": 0.0, "end": 1.0, "text": "hi", "words": [{"start": 0, "end": 1, "word": "hi"}]}]
    path = rt.write_raw_transcript(str(tmp_path), segs, [], backend="whisper")
    with open(path, encoding="utf-8") as f:
        saved = json.load(f)
    assert saved["segments"] == [{"start": 0.0, "end": 1.0, "text": "hi"}]
    assert "words" in segs[0]


class TestRunSettings:
    SECRETS = ["hf_SECRETTOKEN123", "gsk_GROQSECRET456", "sk-ant-ENGINEKEY789"]

    def _settings(self, **over):
        kw = dict(asr_backend="whisper", whisper_size="small",
                  local_model_path="/models/whisper-small", language="zh",
                  transcript_mode="whisper", alignment_method="whisper_diff",
                  min_silence_ms=300, vad_threshold=0.5, beam_size=5,
                  hallucination_silence_sec=2.0, min_pause_sec=0.35, use_gpu=True, gpu_max_parallel=2,
                  gpu_limit_enabled=True, initial_prompt="secret names " + self.SECRETS[0],
                  stage_seconds={"load": 1.234}, expected_speakers=3)
        kw.update(over)
        return rt.build_run_settings(**kw)

    def test_key_set_is_exactly_the_allowlist(self):
        assert set(self._settings()) == set(rt.SETTINGS_KEYS)
        assert len(rt.SETTINGS_KEYS) == len(set(rt.SETTINGS_KEYS))

    def test_values_describe_the_run(self):
        s = self._settings(gpu_fallback_msgs=["cublas missing at C:\\x"])
        assert s["whisper_size"] == "small" and s["beam_size"] == 5 and s["expected_speakers"] == 3
        assert s["local_model_path_set"] is True and s["min_pause_sec"] == 0.35
        assert s["gpu_requested"] is True and s["gpu_used"] is False and s["gpu_fell_back_to_cpu"] is True
        assert s["initial_prompt_used"] is True and s["initial_prompt_chars"] == len("secret names " + self.SECRETS[0])
        assert s["stage_seconds"] == {"load": 1.23}

    def test_no_secrets_prompt_text_or_paths_reach_the_file(self, tmp_path):
        s = self._settings(gpu_fallback_msgs=["failed loading /home/u/cuda.so"],
                           initial_prompt="glossary " + self.SECRETS[1])
        path = rt.write_raw_transcript(str(tmp_path), SEGMENTS, [], backend="whisper", settings=s)
        text = _bytes(path).decode("utf-8")
        for secret in self.SECRETS + ["secret names", "glossary", "/models", "/home", "C:\\"]:
            assert secret not in text
        assert "settings" in json.loads(text)

    def test_files_without_settings_still_load_and_are_not_given_one(self, isolated_db):
        did, ddir, path = _transcribed_drama(isolated_db)
        raw = rt.load_latest(ddir)
        assert "settings" not in raw
        line = isolated_db.load_line_objects(did)[0]
        assert rt.original_text_for_line(raw, line) == "你好"

def _runs(ddir, n):
    """n runs, each with its own text; returns their paths oldest first."""
    for i in range(n):
        rt.write_raw_transcript(ddir, [{"start": 0, "end": 1, "text": f"run{i}"}],
                                [Line(idx=0, start=0, end=1, zh=f"run{i}")], backend="whisper")
    return rt.list_raw_transcripts(ddir)


def _texts(ddir):
    return [rt.read_raw_transcript(p)["text"] for p in rt.list_raw_transcripts(ddir)]


class TestRetention:
    def test_keeps_the_first_and_newest_five_plain_and_compresses_the_rest(self, tmp_path):
        ddir = str(tmp_path)
        paths = _runs(ddir, 9)
        names = [os.path.basename(p) for p in paths]
        assert len(paths) == 9 and names[0] == "raw_transcript.json"
        plain = [n for n in names if n.endswith(".json")]
        packed = [n for n in names if n.endswith(".json.gz")]
        assert plain == [names[0]] + names[-rt.KEEP_NEWEST_PLAIN:]
        assert packed == names[1:4]
        # Nothing is deleted: every run is still there, in order, readable.
        assert _texts(ddir) == [f"run{i}" for i in range(9)]
        for n in packed:
            assert not os.path.exists(os.path.join(ddir, n[:-3]))
        assert not [n for n in os.listdir(ddir) if n.startswith(".")]

    def test_a_compressed_run_reads_back_byte_for_byte(self, tmp_path):
        ddir = str(tmp_path)
        _runs(ddir, 2)
        second = rt.list_raw_transcripts(ddir)[1]
        before = _bytes(second)
        _runs(ddir, 6)
        packed = rt.list_raw_transcripts(ddir)[1]
        assert packed == second + ".gz"
        import gzip
        assert gzip.decompress(_bytes(packed)) == before

    def test_the_newest_reader_still_finds_the_newest_run(self, tmp_path):
        ddir = str(tmp_path)
        _runs(ddir, 12)
        assert rt.load_latest(ddir)["text"] == "run11"

    def test_six_runs_or_fewer_stay_plain(self, tmp_path):
        ddir = str(tmp_path)
        paths = _runs(ddir, 6)
        assert all(p.endswith(".json") for p in paths)

    def test_a_failed_check_keeps_the_plain_file_and_drops_the_copy(self, tmp_path, monkeypatch):
        ddir = str(tmp_path)
        _runs(ddir, 6)
        monkeypatch.setattr(rt, "_same_content", lambda *a: False)
        _runs(ddir, 1)
        names = os.listdir(ddir)
        assert len(names) == 7 and all(n.endswith(".json") for n in names)
        assert _texts(ddir) == [f"run{i}" for i in range(6)] + ["run0"]

    def test_the_rename_is_made_durable_before_the_plain_file_is_removed(
            self, tmp_path, monkeypatch):
        ddir = str(tmp_path)
        _runs(ddir, 6)
        events = []
        real_remove = os.remove
        monkeypatch.setattr(rt.db, "fsync_dir", lambda path: events.append("fsync"))
        monkeypatch.setattr(
            rt.os, "remove",
            lambda path: (events.append("remove") if path.endswith(".json") else None,
                          real_remove(path))[1])
        _runs(ddir, 1)
        assert events == ["fsync", "remove"]

    def test_a_failed_fsync_keeps_the_plain_file(self, tmp_path, monkeypatch):
        ddir = str(tmp_path)
        _runs(ddir, 6)

        def broken(path):
            raise OSError("disk full")
        monkeypatch.setattr(rt.db, "fsync_dir", broken)
        with contextlib.suppress(OSError):
            _runs(ddir, 1)
        assert _texts(ddir)[1] == "run1"
        assert os.path.exists(os.path.join(ddir, "raw_transcript.json"))
        second = rt.list_raw_transcripts(ddir)[1]
        assert second.endswith(".json") and os.path.exists(second)

    def test_a_check_that_fails_after_another_process_removed_the_plain_file_keeps_the_copy(
            self, tmp_path, monkeypatch):
        ddir = str(tmp_path)
        _runs(ddir, 6)
        second_plain = rt.list_raw_transcripts(ddir)[1]
        real_same = rt._same_content
        calls = []

        def interleaved(packed_path, plain):
            calls.append(packed_path)
            if len(calls) == 1:
                return real_same(packed_path, plain)    # nothing packed yet
            # Another process verified the same .gz and removed the plain
            # file between our write and our read-back, which now fails.
            os.remove(second_plain)
            return False
        monkeypatch.setattr(rt, "_same_content", interleaved)
        rt._compress(second_plain)
        assert os.path.exists(second_plain + ".gz")
        assert rt.read_raw_transcript(second_plain + ".gz")["text"] == "run1"

    def test_a_plain_file_that_cant_be_removed_is_listed_once_and_removed_later(
            self, tmp_path, monkeypatch):
        ddir = str(tmp_path)
        _runs(ddir, 6)
        real_remove = os.remove

        def locked(path):
            raise PermissionError("in use")
        monkeypatch.setattr(rt.os, "remove", locked)
        _runs(ddir, 1)
        second = os.path.join(ddir, os.path.basename(rt.list_raw_transcripts(ddir)[1]))
        assert second.endswith(".json")      # the plain file is preferred while both exist
        assert os.path.exists(second + ".gz")
        assert len(rt.list_raw_transcripts(ddir)) == 7
        monkeypatch.setattr(rt.os, "remove", real_remove)
        rt.apply_retention(ddir)
        assert not os.path.exists(second) and os.path.exists(second + ".gz")
        assert len(rt.list_raw_transcripts(ddir)) == 7

    def test_a_retention_error_never_fails_the_run(self, tmp_path, monkeypatch):
        ddir = str(tmp_path)
        _runs(ddir, 6)

        def boom(*a):
            raise OSError("disk hiccup")
        monkeypatch.setattr(rt, "_compress", boom)
        path = rt.write_raw_transcript(ddir, SEGMENTS, [], backend="whisper")
        assert rt.load_latest(ddir)["text"] == rt.read_raw_transcript(path)["text"]
        assert len(rt.list_raw_transcripts(ddir)) == 7

    def test_retention_waits_for_a_reader_of_the_same_title(self, tmp_path):
        import threading
        ddir = str(tmp_path)
        _runs(ddir, 6)
        with rt.title_lock(ddir):
            t = threading.Thread(target=_runs, args=(ddir, 1))
            t.start()
            t.join(timeout=0.3)
            assert t.is_alive()
            assert not [n for n in os.listdir(ddir) if n.endswith(".gz")]
        t.join(timeout=10)
        assert not t.is_alive()
        assert len([n for n in os.listdir(ddir) if n.endswith(".gz")]) == 1

    def test_an_old_leftover_temp_file_is_removed(self, tmp_path):
        ddir = str(tmp_path)
        _runs(ddir, 1)
        stale = os.path.join(ddir, ".raw_transcript.abc.json.gz.tmp")
        fresh = os.path.join(ddir, ".raw_transcript.def.json.gz.tmp")
        for p in (stale, fresh):
            open(p, "wb").close()
        os.utime(stale, (1, 1))
        rt.apply_retention(ddir)
        assert not os.path.exists(stale) and os.path.exists(fresh)


class TestRetentionRespectsKeepOriginal:
    """A title pinned with "Keep original files" keeps every raw transcript
    exactly as written: retention compresses nothing in its folder."""

    def test_a_pinned_title_keeps_every_run_plain(self, isolated_db):
        did = isolated_db.create_drama(title_en="P", keep_original=1)
        ddir = isolated_db.drama_dir(did)
        paths = _runs(ddir, 9)
        assert len(paths) == 9 and all(p.endswith(".json") for p in paths)

    def test_unpinning_lets_the_next_run_compress(self, isolated_db):
        did = isolated_db.create_drama(title_en="P", keep_original=1)
        ddir = isolated_db.drama_dir(did)
        _runs(ddir, 8)
        isolated_db.update_drama(did, keep_original=0)
        _runs(ddir, 1)
        assert len([p for p in rt.list_raw_transcripts(ddir) if p.endswith(".gz")]) == 3

    def test_an_unpinned_title_is_compressed(self, isolated_db):
        did = isolated_db.create_drama(title_en="U")
        ddir = isolated_db.drama_dir(did)
        _runs(ddir, 9)
        assert len([p for p in rt.list_raw_transcripts(ddir) if p.endswith(".gz")]) == 3

    def test_when_the_pin_cant_be_read_nothing_is_compressed(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="U")
        ddir = isolated_db.drama_dir(did)

        def broken(_id):
            raise RuntimeError("database is locked")
        monkeypatch.setattr(isolated_db, "get_drama", broken)
        paths = _runs(ddir, 9)
        assert all(p.endswith(".json") for p in paths)
