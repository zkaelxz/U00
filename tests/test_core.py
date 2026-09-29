"""
tests/test_core.py -- tests for core.py, the pipeline logic shared by
the GUI and CLI (no Streamlit/database dependency).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import core

from core import (
    Line, fmt_ts, lines_to_srt, lines_to_bilingual_srt,
    split_user_transcript, align_transcript_to_timing,
    merge_adjacent_short_lines, chunk_novel_text,
    filter_hallucinated_segments,
)


class TestFmtTs:
    def test_zero(self):
        assert fmt_ts(0) == "00:00:00,000"

    def test_basic(self):
        assert fmt_ts(65.5) == "00:01:05,500"

    def test_hours(self):
        assert fmt_ts(3661.25) == "01:01:01,250"

    def test_negative_clamped_to_zero(self):
        assert fmt_ts(-5) == "00:00:00,000"


class TestLinesToSrt:
    def test_basic_format(self):
        lines = [Line(idx=0, start=0.0, end=1.5, zh="你好", en="Hello")]
        srt = lines_to_srt(lines, "en")
        assert "1\n" in srt
        assert "00:00:00,000 --> 00:00:01,500" in srt
        assert "Hello" in srt

    def test_bilingual_includes_both_languages(self):
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")]
        srt = lines_to_bilingual_srt(lines)
        assert "Hello" in srt
        assert "你好" in srt

    def test_bilingual_falls_back_to_zh_when_untranslated(self):
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="")]
        srt = lines_to_bilingual_srt(lines)
        assert "你好" in srt

    def test_notes_by_idx_appends_a_bracketed_aside(self):
        lines = [Line(idx=5, start=0.0, end=1.0, zh="齊居堂", en="Qijutang")]
        notes = {5: [{"term": "Qijutang", "note": "lit. 'Hall of Sitting Together', used as a joke"}]}
        srt = lines_to_srt(lines, "en", notes_by_idx=notes)
        assert "Qijutang: lit. 'Hall of Sitting Together', used as a joke" in srt

    def test_notes_by_idx_only_touches_the_matching_line(self):
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello"),
                 Line(idx=1, start=1.0, end=2.0, zh="再见", en="Bye")]
        notes = {1: [{"term": "再见", "note": "a note"}]}
        srt = lines_to_srt(lines, "en", notes_by_idx=notes)
        entries = srt.split("\n\n")
        assert "[再见: a note]" not in entries[0]
        assert "[再见: a note]" in entries[1]

    def test_no_notes_by_idx_leaves_srt_unchanged(self):
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")]
        assert lines_to_srt(lines, "en") == lines_to_srt(lines, "en", notes_by_idx=None)

    def test_bilingual_also_supports_notes_by_idx(self):
        lines = [Line(idx=0, start=0.0, end=1.0, zh="你好", en="Hello")]
        notes = {0: [{"term": "你好", "note": "a greeting"}]}
        srt = lines_to_bilingual_srt(lines, notes_by_idx=notes)
        assert "[你好: a greeting]" in srt


class TestSplitUserTranscript:
    def test_multi_line_input_splits_on_newlines(self):
        result = split_user_transcript("line one\nline two\nline three")
        assert result == ["line one", "line two", "line three"]

    def test_single_blob_splits_on_chinese_punctuation(self):
        result = split_user_transcript("你好。今天天气不错！你觉得呢？")
        assert len(result) == 3
        assert result[0] == "你好。"

    def test_empty_input(self):
        assert split_user_transcript("") == []

    def test_strips_whitespace(self):
        result = split_user_transcript("  line one  \n  line two  ")
        assert result == ["line one", "line two"]


class TestAlignTranscriptToTiming:
    def test_basic_alignment_assigns_increasing_timestamps(self):
        segments = [{"start": 0.0, "end": 2.0, "text": "你好世界"},
                    {"start": 2.0, "end": 4.0, "text": "再见了朋友"}]
        user_lines = ["你好世界。", "再见了朋友。"]
        lines = align_transcript_to_timing(user_lines, segments)
        assert len(lines) == 2
        assert lines[0].start <= lines[0].end
        assert lines[1].start <= lines[1].end
        # monotonic: second line shouldn't start before the first ends
        assert lines[1].start >= lines[0].end - 0.01

    def test_missing_whisper_match_interpolates_rather_than_crashing(self):
        segments = [{"start": 0.0, "end": 2.0, "text": "你好"}]
        user_lines = ["你好。", "一段完全不匹配的文字用来测试插值。"]
        lines = align_transcript_to_timing(user_lines, segments)
        assert len(lines) == 2
        # both lines should get *some* valid timing, not crash or produce None
        assert all(ln.end > ln.start for ln in lines)

    def test_empty_segments_does_not_crash(self):
        lines = align_transcript_to_timing(["一些文字。"], [])
        assert len(lines) == 1
        assert lines[0].end > lines[0].start


class TestMergeAdjacentShortLines:
    def test_merges_two_short_same_speaker_lines(self, sample_lines):
        merged = merge_adjacent_short_lines(sample_lines)
        assert len(merged) < len(sample_lines)
        assert merged[0].zh == "你好"
        assert merged[0].en == "You good"

    def test_does_not_absorb_a_legitimately_long_following_line(self, sample_lines):
        merged = merge_adjacent_short_lines(sample_lines)
        # the long weather line should survive as its own entry, not get
        # swallowed into the merged short-line chain (regression test for
        # a real bug caught during development)
        long_line = next(l for l in merged if "天气" in l.zh)
        assert long_line.zh == "今天天气不错"

    def test_different_speakers_never_merge(self, sample_lines):
        merged = merge_adjacent_short_lines(sample_lines)
        speaker_b_line = next(l for l in merged if l.speaker == "B")
        assert speaker_b_line.zh == "嗯"

    def test_reindexes_after_merge(self, sample_lines):
        merged = merge_adjacent_short_lines(sample_lines)
        indices = [l.idx for l in merged]
        assert indices == list(range(len(merged)))

    def test_empty_list(self):
        assert merge_adjacent_short_lines([]) == []

    def test_single_line(self):
        single = [Line(idx=0, start=0, end=1, zh="a", en="b")]
        result = merge_adjacent_short_lines(single)
        assert len(result) == 1

    def test_large_gap_prevents_merge(self):
        lines = [
            Line(idx=0, start=0.0, end=0.3, zh="a", en="a", speaker="A"),
            Line(idx=1, start=10.0, end=10.3, zh="b", en="b", speaker="A"),
        ]
        merged = merge_adjacent_short_lines(lines, max_gap=0.5)
        assert len(merged) == 2  # too far apart in time to be a split artifact


class TestChunkNovelText:
    def test_splits_paragraphs(self):
        text = "第一段内容。\n\n第二段内容。"
        chunks = chunk_novel_text(text, max_chars=100)
        assert len(chunks) == 2

    def test_long_paragraph_splits_on_sentences(self):
        long_para = "这是一句话。" * 50  # forces a split under a small max_chars
        chunks = chunk_novel_text(long_para, max_chars=30)
        assert len(chunks) > 1
        assert all(len(c) <= 40 for c in chunks)  # allow small overshoot at boundaries

    def test_empty_text(self):
        assert chunk_novel_text("", max_chars=100) == []


class TestModelDownloadErrorHandling:
    """A failed model download is a network problem, not an audio problem.
    Misclassifying it sends people debugging the wrong thing -- this is
    regression cover for the real error seen in the field."""

    def test_classifies_the_real_windows_dns_failure(self):
        from core import _is_network_error
        exc = Exception("Got: ConnectError: [Errno 11004] getaddrinfo failed")
        assert _is_network_error(exc) is True

    def test_classifies_common_network_failures(self):
        from core import _is_network_error
        for msg in ("httpx.ConnectError", "Max retries exceeded",
                    "LocalEntryNotFoundError", "Connection timed out",
                    "Temporary failure in name resolution", "proxy error"):
            assert _is_network_error(Exception(msg)) is True, msg

    def test_does_not_misclassify_real_audio_or_gpu_errors(self):
        from core import _is_network_error
        for msg in ("Invalid audio file format", "CUDA out of memory",
                    "unsupported sample rate"):
            assert _is_network_error(Exception(msg)) is False, msg

    def test_model_download_error_is_a_runtime_error(self):
        from core import ModelDownloadError
        assert issubclass(ModelDownloadError, RuntimeError)

    def test_cache_check_returns_bool_and_never_raises(self):
        from core import is_whisper_model_cached
        assert isinstance(is_whisper_model_cached("medium"), bool)
        assert isinstance(is_whisper_model_cached("nonexistent-size"), bool)


class TestDefaultWhisperSize:
    """Step 5b item 9: a consumer GPU in the 8-12GB class this app targets
    (confirmed against real hardware -- an RTX 3080 Ti, 12GB) comfortably
    fits large-v3 without needing large-v3-turbo's memory savings, and
    large-v3-turbo is reported weaker on Japanese/Korean -- so large-v3 is
    the default for all three source languages, not just an available
    option alongside 'medium'."""

    def test_default_whisper_size_is_large_v3(self):
        from core import DEFAULT_WHISPER_SIZE, WHISPER_MODELS
        assert DEFAULT_WHISPER_SIZE == "large-v3"
        assert DEFAULT_WHISPER_SIZE in WHISPER_MODELS


class TestDnsDiagnosis:
    """A DNS blocker (Pi-hole, AdGuard) returns 0.0.0.0 for blocked domains
    rather than failing, which looks identical to a broken connection from
    inside a stack trace. Naming the difference matters: one is fixed by
    whitelisting a domain, the other by fixing your network."""

    def test_loopback_hostname_not_flagged_as_blocked(self):
        from core import diagnose_hostname
        assert diagnose_hostname("localhost")["status"] == "ok"

    def test_unresolvable_hostname_reports_no_dns(self):
        from core import diagnose_hostname
        assert diagnose_hostname("definitely-not-real-xyz123.invalid")["status"] == "no_dns"

    def test_result_always_has_expected_keys(self):
        from core import diagnose_hostname
        for host in ("localhost", "not-real-xyz123.invalid"):
            r = diagnose_hostname(host)
            for key in ("status", "hostname", "addresses", "detail"):
                assert key in r

    def test_status_is_one_of_three_known_values(self):
        from core import diagnose_hostname
        assert diagnose_hostname("localhost")["status"] in ("ok", "blocked", "no_dns")


class TestInitialPromptBuilding:
    """Priming Whisper with expected proper nouns is the biggest free
    accuracy win for Chinese, where a misheard name is usually still a
    valid word and so passes silently into the translation."""

    def test_builds_from_glossary_rows(self):
        from core import build_initial_prompt
        p = build_initial_prompt([{"term_original": "沈清疑"}, {"term_original": "云隐宗"}])
        assert "沈清疑" in p and "云隐宗" in p

    def test_accepts_plain_strings(self):
        from core import build_initial_prompt
        assert "岳家" in build_initial_prompt(["岳家", "神机营"])

    def test_ends_with_a_full_stop(self):
        from core import build_initial_prompt
        assert build_initial_prompt(["x"]).endswith("。")

    def test_empty_inputs_give_empty_prompt(self):
        from core import build_initial_prompt
        assert build_initial_prompt([]) == ""
        assert build_initial_prompt(None) == ""
        assert build_initial_prompt([{"term_original": "   "}]) == ""

    def test_caps_term_count(self):
        from core import build_initial_prompt
        p = build_initial_prompt([{"term_original": f"t{i}"} for i in range(100)], max_terms=40)
        assert p.count("、") == 39

    def test_skips_blank_terms(self):
        from core import build_initial_prompt
        assert build_initial_prompt(["", "  ", "实名"]) == "实名。"

    def test_primes_recorded_aliases_alongside_the_canonical_original(self):
        """Step 30: a glossary row's aliases (alt spellings/transliterations
        of term_original) are included in Whisper's priming context too,
        not just the canonical original."""
        from core import build_initial_prompt
        p = build_initial_prompt([{"term_original": "沈清疑", "aliases": "沈清儀|Shen Qing Yi"}])
        assert "沈清疑" in p and "沈清儀" in p and "Shen Qing Yi" in p

    def test_a_glossary_row_with_no_aliases_field_still_works(self):
        from core import build_initial_prompt
        assert build_initial_prompt([{"term_original": "岳家"}]) == "岳家。"


class TestBlankSubtitleExportDetection:
    """A timed-but-textless line exports cleanly with no error -- it just
    looks broken when opened. Confirms lines_to_srt itself has no bug (it
    faithfully writes whatever field it's given) and gives the counting
    logic the UI/CLI guards rely on to warn before that happens."""

    def test_untranslated_lines_produce_real_timing_blank_text(self):
        from core import Line, lines_to_srt
        lines = [Line(idx=i, start=float(i * 2), end=float(i * 2 + 2), zh="z", en="")
                 for i in range(5)]
        srt = lines_to_srt(lines, "en")
        block = srt.split("\n\n")[0].split("\n")
        assert block == ["1", "00:00:00,000 --> 00:00:02,000"]  # no third (text) line

    def test_translated_lines_include_text(self):
        from core import Line, lines_to_srt
        lines = [Line(idx=0, start=0, end=2, zh="z", en="Hello")]
        assert "Hello" in lines_to_srt(lines, "en")

    def test_fill_count_detects_zero_translated(self):
        from core import Line
        lines = [Line(idx=i, start=0, end=1, zh="z", en="") for i in range(5)]
        assert sum(1 for ln in lines if ln.en.strip()) == 0

    def test_fill_count_detects_partial_translation(self):
        from core import Line
        lines = [Line(idx=i, start=0, end=1, zh="z", en=("x" if i < 2 else ""))
                 for i in range(5)]
        assert sum(1 for ln in lines if ln.en.strip()) == 2

    def test_fill_count_detects_fully_translated(self):
        from core import Line
        lines = [Line(idx=i, start=0, end=1, zh="z", en="x") for i in range(5)]
        assert sum(1 for ln in lines if ln.en.strip()) == len(lines)

    def test_whitespace_only_translation_counts_as_untranslated(self):
        from core import Line
        lines = [Line(idx=0, start=0, end=1, zh="z", en="   ")]
        assert sum(1 for ln in lines if ln.en.strip()) == 0


class TestModelSelection:
    """The Claude model was hardcoded to an older generation with no UI
    to change it -- these lock in the fix."""

    def test_default_is_current_not_legacy(self):
        import inspect
        import translate_engines as te
        default = inspect.signature(te.ClaudeEngine.__init__).parameters["model"].default
        assert default == "claude-sonnet-5"

    def test_every_selectable_model_has_pricing(self):
        import translate_engines as te
        for model in te.CLAUDE_MODELS:
            assert model in te.PRICING_PER_MILLION_TOKENS

    def test_get_engine_threads_a_chosen_model_through(self, monkeypatch):
        import types
        import translate_engines as te
        fake = types.ModuleType("anthropic")
        fake.Anthropic = lambda api_key: types.SimpleNamespace(api_key=api_key)
        monkeypatch.setitem(sys.modules, "anthropic", fake)
        for model in te.CLAUDE_MODELS:
            eng = te.get_engine("claude", "fake-key", model)
            assert eng.model == model


class TestVadSensitivity:
    def test_transcribe_signature_accepts_min_silence_duration(self):
        import inspect
        from core import transcribe_for_timing
        params = inspect.signature(transcribe_for_timing).parameters
        assert "min_silence_duration_ms" in params
        assert params["min_silence_duration_ms"].default == 2000

    def test_transcribe_signature_accepts_vad_threshold(self):
        import inspect
        from core import transcribe_for_timing
        params = inspect.signature(transcribe_for_timing).parameters
        assert "vad_threshold" in params
        assert params["vad_threshold"].default == 0.5

    def test_vad_threshold_is_passed_through_to_silero(self, monkeypatch):
        """faster-whisper's own vad.py is adapted directly from
        snakers4/silero-vad -- its built-in VAD IS Silero, not a separate
        technology to swap in. This locks in that vad_threshold actually
        reaches Silero's own "threshold" parameter, not just that the
        function accepts the argument."""
        import sys, types
        import core
        core._whisper_model_cache.clear()

        class FakeSegment:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        captured = {}

        class FakeModel:
            def __init__(self, *a, **k):
                pass

            def transcribe(self, audio_path, **kwargs):
                captured.update(kwargs)
                def gen():
                    yield FakeSegment(0.0, 1.0, "hi")
                return gen(), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeModel()
        sys.modules["faster_whisper"] = fake_fw

        core.transcribe_for_timing("/fake/audio.mp3", vad_threshold=0.7)

        assert captured["vad_parameters"]["threshold"] == 0.7

    def test_vad_threshold_defaults_to_point_five(self, monkeypatch):
        import sys, types
        import core
        core._whisper_model_cache.clear()

        class FakeSegment:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        captured = {}

        class FakeModel:
            def __init__(self, *a, **k):
                pass

            def transcribe(self, audio_path, **kwargs):
                captured.update(kwargs)
                def gen():
                    yield FakeSegment(0.0, 1.0, "hi")
                return gen(), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeModel()
        sys.modules["faster_whisper"] = fake_fw

        core.transcribe_for_timing("/fake/audio.mp3")

        assert captured["vad_parameters"]["threshold"] == 0.5


class TestFilterHallucinatedSegments:
    """Regression coverage for a real, well-documented Whisper failure
    mode: looping a short phrase across many separate segments on silence
    or background music. Each individual repeated segment can look
    perfectly confident on its own (faster-whisper's own per-segment
    compression_ratio/log_prob/no_speech thresholds don't catch it) --
    it's the run of identical segments that's the actual tell."""

    def _seg(self, start, end, text):
        return {"start": start, "end": end, "text": text}

    def test_a_short_run_is_left_untouched(self):
        segments = [self._seg(0, 1, "hello"), self._seg(1, 2, "hello"),
                    self._seg(2, 3, "world")]
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == segments

    def test_a_long_run_is_collapsed_to_the_first_occurrence(self):
        segments = ([self._seg(i, i + 1, "thanks for watching") for i in range(5)]
                    + [self._seg(5, 6, "real dialogue")])
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == [self._seg(0, 1, "thanks for watching"),
                           self._seg(5, 6, "real dialogue")]

    def test_exactly_at_the_threshold_is_collapsed(self):
        segments = [self._seg(i, i + 1, "x") for i in range(4)]
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == [self._seg(0, 1, "x")]

    def test_one_below_the_threshold_is_untouched(self):
        segments = [self._seg(i, i + 1, "x") for i in range(3)]
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == segments

    def test_whitespace_differences_still_count_as_the_same_run(self):
        segments = [self._seg(0, 1, "hi there"), self._seg(1, 2, "hi  there"),
                    self._seg(2, 3, " hithere "), self._seg(3, 4, "hi there")]
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == [self._seg(0, 1, "hi there")]

    def test_blank_segments_are_never_collapsed(self):
        """A stretch of real silence correctly produces no text at all --
        collapsing blanks would be pointless (there's nothing to dedupe)
        and could accidentally hide a legitimate run of blank segments."""
        segments = [self._seg(i, i + 1, "") for i in range(6)]
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == segments

    def test_multiple_separate_runs_are_each_collapsed(self):
        segments = ([self._seg(i, i + 1, "a") for i in range(4)]
                    + [self._seg(4, 5, "real line")]
                    + [self._seg(5 + i, 6 + i, "b") for i in range(5)])
        result = filter_hallucinated_segments(segments, min_repeat_count=4)
        assert result == [self._seg(0, 1, "a"), self._seg(4, 5, "real line"),
                           self._seg(5, 6, "b")]

    def test_empty_input_returns_empty(self):
        assert filter_hallucinated_segments([]) == []

    def test_default_threshold_is_four(self):
        import inspect
        params = inspect.signature(filter_hallucinated_segments).parameters
        assert params["min_repeat_count"].default == 4


class TestTranscribeForTimingHallucinationFilter:
    def _stub_faster_whisper(self, texts):
        import sys, types

        class FakeSegment:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        class FakeModel:
            def __init__(self, *a, **k):
                pass

            def transcribe(self, audio_path, **kwargs):
                def gen():
                    for i, t in enumerate(texts):
                        yield FakeSegment(float(i), float(i + 1), t)
                return gen(), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeModel()
        sys.modules["faster_whisper"] = fake_fw

    def test_hallucination_filter_is_applied_by_default(self):
        import core
        core._whisper_model_cache.clear()
        self._stub_faster_whisper(["thanks for watching"] * 5 + ["real line"])

        result = core.transcribe_for_timing("/fake/audio.mp3")

        assert result == [{"start": 0.0, "end": 1.0, "text": "thanks for watching"},
                           {"start": 5.0, "end": 6.0, "text": "real line"}]

    def test_filter_can_be_disabled(self):
        import core
        core._whisper_model_cache.clear()
        self._stub_faster_whisper(["thanks for watching"] * 5 + ["real line"])

        result = core.transcribe_for_timing("/fake/audio.mp3", filter_hallucination_repeats=0)

        assert len(result) == 6  # nothing collapsed


class TestAutotuneSubprocessWorker:
    """Step 6h: autotune_subprocess_worker() is the entry point
    background_jobs.start_process_job() runs in its own OS process for
    each auto-tune candidate, so a real mid-run Cancel can terminate it
    (transcribe_for_timing() has no cancel checkpoint of its own).
    Tested here as a plain function call against the same fakes used
    elsewhere in this file -- background_jobs.py's own tests cover the
    actual multiprocessing.Process/cancel machinery."""

    def _stub_faster_whisper(self, texts):
        import sys, types

        class FakeSegment:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        class FakeModel:
            def __init__(self, *a, **k):
                pass

            def transcribe(self, audio_path, **kwargs):
                def gen():
                    for i, t in enumerate(texts):
                        yield FakeSegment(float(i), float(i + 1), t)
                return gen(), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeModel()
        sys.modules["faster_whisper"] = fake_fw

    def test_matches_a_direct_transcribe_for_timing_call_on_success(self):
        import queue
        import core

        core._whisper_model_cache.clear()
        self._stub_faster_whisper(["你好", "世界"])
        direct = core.transcribe_for_timing("/fake/audio.wav", "medium", language="zh",
                                            min_silence_duration_ms=800)

        core._whisper_model_cache.clear()
        self._stub_faster_whisper(["你好", "世界"])
        result_queue = queue.Queue()
        core.autotune_subprocess_worker(
            "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 800, 0.5, False,
            result_queue)
        outcome = result_queue.get_nowait()

        assert outcome == ("ok", {"candidate_ms": 800, "segments": direct})

    def test_uses_the_given_candidate_ms_as_min_silence_duration(self):
        import queue
        import core

        core._whisper_model_cache.clear()
        self._stub_faster_whisper(["a"])
        seen = {}
        real_transcribe = core.transcribe_for_timing

        def spy(*a, **kw):
            seen.update(kw)
            return real_transcribe(*a, **kw)
        core.transcribe_for_timing = spy
        try:
            result_queue = queue.Queue()
            core.autotune_subprocess_worker(
                "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 1500, 0.5, False,
                result_queue)
        finally:
            core.transcribe_for_timing = real_transcribe

        assert seen["min_silence_duration_ms"] == 1500

    def test_reports_an_exception_instead_of_raising(self):
        import queue
        import core

        core._whisper_model_cache.clear()

        def _boom(*a, **k):
            raise RuntimeError("model download failed")
        core.transcribe_for_timing, real = _boom, core.transcribe_for_timing
        try:
            result_queue = queue.Queue()
            core.autotune_subprocess_worker(
                "/fake/audio.wav", "medium", "zh", False, None, None, "", 5, 300, 0.5, False,
                result_queue)
        finally:
            core.transcribe_for_timing = real
        outcome = result_queue.get_nowait()

        assert outcome == ("error", "RuntimeError", "model download failed")


class TestLineCoverageDiagnosis:
    """Regression cover built directly from a real uploaded file: several
    lines spanning many minutes with only a few characters of text each,
    the signature of the voice-activity detector merging multiple real
    lines of dialogue into one oversized segment."""

    def test_catches_the_real_worst_case_from_the_field(self):
        from core import Line, diagnose_line_coverage
        lines = [
            Line(idx=0, start=390.0, end=413.0, zh="还有其他吗", en="x"),
            Line(idx=1, start=431.0, end=477.0, zh="都好", en="x"),        # 46s, 2 chars
            Line(idx=2, start=2262.0, end=2668.0, zh="清除叛徒", en="x"),  # 6.4 minutes
        ]
        report = diagnose_line_coverage(lines)
        assert len(report["long_lines"]) == 3
        assert report["long_lines"][0]["idx"] == 2  # worst offender sorts first

    def test_normal_short_line_is_not_flagged(self):
        from core import Line, diagnose_line_coverage
        lines = [Line(idx=0, start=0.0, end=2.0, zh="正常对话", en="Normal.")]
        assert diagnose_line_coverage(lines)["long_lines"] == []

    def test_detects_large_silent_gaps(self):
        from core import Line, diagnose_line_coverage
        lines = [Line(idx=0, start=0.0, end=1.0, zh="a", en="a"),
                 Line(idx=1, start=10.0, end=11.0, zh="b", en="b")]
        report = diagnose_line_coverage(lines, gap_seconds=3.0)
        assert len(report["large_gaps"]) == 1
        assert report["large_gaps"][0]["gap_seconds"] == 9.0

    def test_small_gap_not_flagged(self):
        from core import Line, diagnose_line_coverage
        lines = [Line(idx=0, start=0.0, end=1.0, zh="a", en="a"),
                 Line(idx=1, start=1.5, end=2.5, zh="b", en="b")]
        assert diagnose_line_coverage(lines, gap_seconds=3.0)["large_gaps"] == []

    def test_detects_blank_source_text(self):
        from core import Line, diagnose_line_coverage
        lines = [Line(idx=0, start=0.0, end=1.0, zh="", en="")]
        report = diagnose_line_coverage(lines)
        assert len(report["blank_zh"]) == 1

    def test_detects_untranslated_lines_with_source_present(self):
        from core import Line, diagnose_line_coverage
        lines = [Line(idx=0, start=0.0, end=1.0, zh="有文本", en="")]
        report = diagnose_line_coverage(lines)
        assert len(report["blank_en"]) == 1
        assert report["blank_en"][0]["zh"] == "有文本"

    def test_empty_input_does_not_crash(self):
        from core import diagnose_line_coverage
        report = diagnose_line_coverage([])
        assert report == {"long_lines": [], "large_gaps": [], "blank_zh": [], "blank_en": []}


class TestPersistedTranslationFailures:
    """The one-time completion warning was ephemeral (in-memory job status
    only) and got lost if the app restarted or the rerun was missed --
    exactly the field symptom of 'lines transcribed but never translated,
    with no explanation why'. These lock in that the record now survives."""

    def test_failure_record_persists_across_a_simulated_restart(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        errors = [{"batch_index": 0, "lines": [0, 1], "error": "simulated"}]
        isolated_db.update_drama(did, last_translate_errors=__import__("json").dumps(errors))
        # simulate a restart: nothing about db state depends on process memory
        reloaded = isolated_db.get_drama(did)
        assert reloaded["last_translate_errors"] is not None
        parsed = __import__("json").loads(reloaded["last_translate_errors"])
        assert parsed[0]["lines"] == [0, 1]

    def test_clean_run_clears_a_stale_failure_record(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        isolated_db.update_drama(did, last_translate_errors='[{"lines": [0]}]')
        isolated_db.update_drama(did, last_translate_errors=None)
        assert isolated_db.get_drama(did)["last_translate_errors"] is None

    def test_no_errors_field_defaults_to_none(self, isolated_db):
        did = isolated_db.create_drama(title_en="T")
        assert isolated_db.get_drama(did)["last_translate_errors"] is None


class TestGpuInferenceFailureFallback:
    """Regression cover for a real, previously-uncaught crash: ctranslate2
    (which faster-whisper wraps) defers all CUDA initialization until the
    first actual transcription call -- constructing WhisperModel(device=
    "cuda") never touches the GPU, it just stores config. The earlier GPU
    fallback wrapped model CONSTRUCTION, which is why it never caught a
    real cuBLAS/CUDA failure: that can only ever surface at inference
    time. transcribe_for_timing now catches it there instead."""

    def test_classifies_the_exact_reported_error(self):
        from core import _is_gpu_error
        exc = RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        assert _is_gpu_error(exc) is True

    def test_classifies_common_cuda_failures(self):
        from core import _is_gpu_error
        for msg in ("CUDA error: no kernel image is available",
                    "cuDNN error", "no CUDA-capable device is detected",
                    "CUDA out of memory"):
            assert _is_gpu_error(RuntimeError(msg)) is True, msg

    def test_does_not_misclassify_network_or_genuine_errors(self):
        from core import _is_gpu_error
        assert _is_gpu_error(RuntimeError("Connection timed out")) is False
        assert _is_gpu_error(ValueError("Invalid audio file format")) is False

    def _stub_faster_whisper(self):
        import sys, types
        calls = {"cuda_construct": 0, "cuda_transcribe": 0,
                 "cpu_construct": 0, "cpu_transcribe": 0}

        class FakeSegment:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        class FakeCudaModel:
            def __init__(self, *a, **k):
                calls["cuda_construct"] += 1
            def transcribe(self, audio_path, **kwargs):
                calls["cuda_transcribe"] += 1
                def gen():
                    raise RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
                    yield
                return gen(), None

        class FakeCpuModel:
            def __init__(self, *a, **k):
                calls["cpu_construct"] += 1
            def transcribe(self, audio_path, **kwargs):
                calls["cpu_transcribe"] += 1
                def gen():
                    yield FakeSegment(0.0, 1.0, "第一句")
                    yield FakeSegment(1.0, 2.0, "第二句")
                return gen(), None

        fake_fw = types.ModuleType("faster_whisper")
        def FakeWhisperModel(model_size, device="cpu", compute_type="int8"):
            return FakeCudaModel() if device == "cuda" else FakeCpuModel()
        fake_fw.WhisperModel = FakeWhisperModel
        sys.modules["faster_whisper"] = fake_fw
        return calls

    def test_gpu_inference_failure_falls_back_to_cpu_and_returns_real_segments(self):
        import core
        calls = self._stub_faster_whisper()
        core._whisper_model_cache.clear()

        result = core.transcribe_for_timing("/fake/audio.mp3", use_gpu=True)

        assert result == [{"start": 0.0, "end": 1.0, "text": "第一句"},
                           {"start": 1.0, "end": 2.0, "text": "第二句"}]
        assert calls["cuda_construct"] == 1
        assert calls["cuda_transcribe"] == 1  # attempted, and failed
        assert calls["cpu_construct"] == 1    # fell back
        assert calls["cpu_transcribe"] == 1   # and actually transcribed there

    def test_caller_is_notified_when_fallback_occurs(self):
        import core
        self._stub_faster_whisper()
        core._whisper_model_cache.clear()

        notified = []
        core.transcribe_for_timing("/fake/audio.mp3", use_gpu=True,
                                    on_gpu_fallback=lambda exc: notified.append(str(exc)))
        assert len(notified) == 1
        assert "cublas64_12.dll" in notified[0]

    def test_no_fallback_when_gpu_was_never_requested(self):
        import sys, types
        calls = {"cpu_transcribe": 0}

        class FakeSegment:
            def __init__(self, start, end, text):
                self.start, self.end, self.text = start, end, text

        class FakeCpuModel:
            def __init__(self, *a, **k):
                pass
            def transcribe(self, audio_path, **kwargs):
                calls["cpu_transcribe"] += 1
                def gen():
                    yield FakeSegment(0.0, 1.0, "text")
                return gen(), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeCpuModel()
        sys.modules["faster_whisper"] = fake_fw

        import core
        core._whisper_model_cache.clear()
        notified = []
        core.transcribe_for_timing("/fake/audio.mp3", use_gpu=False,
                                    on_gpu_fallback=lambda exc: notified.append(exc))
        assert notified == []
        assert calls["cpu_transcribe"] == 1

    def test_non_gpu_error_at_inference_time_still_propagates(self):
        import sys, types
        class FakeModel:
            def __init__(self, *a, **k):
                pass
            def transcribe(self, audio_path, **kwargs):
                def gen():
                    raise ValueError("corrupt audio stream")
                    yield
                return gen(), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeModel()
        sys.modules["faster_whisper"] = fake_fw

        import core
        core._whisper_model_cache.clear()
        try:
            core.transcribe_for_timing("/fake/audio.mp3", use_gpu=True)
            assert False, "a genuine non-GPU error must still propagate, not be swallowed"
        except ValueError as e:
            assert "corrupt audio stream" in str(e)


class TestTranscribeProgress:
    """A 3+ hour file with only a spinner and no progress indication looks
    stuck. faster-whisper's transcribe() returns segments lazily, so
    progress_cb can report real fraction-complete as they arrive instead."""

    class _FakeSegment:
        def __init__(self, start, end, text):
            self.start, self.end, self.text = start, end, text

    class _FakeInfo:
        def __init__(self, duration):
            self.duration = duration

    def _stub_faster_whisper(self, segments, info):
        import sys, types

        class FakeModel:
            def __init__(self, *a, **k):
                pass
            def transcribe(self, audio_path, **kwargs):
                return iter(segments), info

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": FakeModel()
        sys.modules["faster_whisper"] = fake_fw

    def test_progress_cb_reports_fraction_of_duration(self):
        import core
        segments = [self._FakeSegment(0.0, 30.0, "a"),
                    self._FakeSegment(30.0, 90.0, "b"),
                    self._FakeSegment(90.0, 120.0, "c")]
        self._stub_faster_whisper(segments, self._FakeInfo(duration=120.0))
        core._whisper_model_cache.clear()

        seen = []
        result = core.transcribe_for_timing("/fake/audio.mp3", progress_cb=seen.append)

        assert len(result) == 3
        assert seen == [0.25, 0.75, 1.0]

    def test_progress_cb_is_optional(self):
        import core
        segments = [self._FakeSegment(0.0, 10.0, "a")]
        self._stub_faster_whisper(segments, self._FakeInfo(duration=10.0))
        core._whisper_model_cache.clear()

        result = core.transcribe_for_timing("/fake/audio.mp3")
        assert result == [{"start": 0.0, "end": 10.0, "text": "a"}]

    def test_missing_duration_does_not_crash(self):
        """info can legitimately be None (some callers/tests stub it that
        way) or lack .duration -- progress just can't be estimated then."""
        import core
        segments = [self._FakeSegment(0.0, 10.0, "a")]
        self._stub_faster_whisper(segments, info=None)
        core._whisper_model_cache.clear()

        seen = []
        result = core.transcribe_for_timing("/fake/audio.mp3", progress_cb=seen.append)
        assert result == [{"start": 0.0, "end": 10.0, "text": "a"}]
        assert seen == [0.0]

    def test_progress_cb_called_on_gpu_fallback_path_too(self):
        import core
        segments = [self._FakeSegment(0.0, 50.0, "a"), self._FakeSegment(50.0, 100.0, "b")]
        info = self._FakeInfo(duration=100.0)

        class FakeCudaModel:
            def __init__(self, *a, **k):
                pass
            def transcribe(self, audio_path, **kwargs):
                def gen():
                    raise RuntimeError("CUDA out of memory")
                    yield
                return gen(), None

        class FakeCpuModel:
            def __init__(self, *a, **k):
                pass
            def transcribe(self, audio_path, **kwargs):
                return iter(segments), info

        import sys, types
        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda model_size, device="cpu", compute_type="int8": (
            FakeCudaModel() if device == "cuda" else FakeCpuModel())
        sys.modules["faster_whisper"] = fake_fw
        core._whisper_model_cache.clear()

        seen = []
        result = core.transcribe_for_timing("/fake/audio.mp3", use_gpu=True, progress_cb=seen.append)
        assert len(result) == 2
        assert seen == [0.5, 1.0]


class TestTranscribeWithGroq:
    """Step 6i: an opt-in, paid cloud ASR alternative to the local
    faster-whisper path -- sends the audio to Groq's hosted Whisper
    Large-v3-Turbo API and must return the identical [{"start", "end",
    "text"}, ...] segment shape transcribe_for_timing does, so the
    result reaches the exact same downstream pipeline (alignment,
    diarization hand-off)."""

    class _FakeResponse:
        def __init__(self, status_code=200, segments=None, text=""):
            self.status_code = status_code
            self._segments = segments if segments is not None else []
            self.text = text

        def json(self):
            return {"segments": self._segments}

    def test_returns_the_same_segment_shape_as_local_whisper(self, monkeypatch, tmp_path):
        import core
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        captured = {}

        def fake_post(url, headers=None, files=None, data=None, timeout=None):
            captured["url"], captured["headers"] = url, headers
            captured["data"], captured["timeout"] = data, timeout
            return self._FakeResponse(segments=[
                {"start": 0.0, "end": 1.5, "text": " Hello there. "},
                {"start": 1.5, "end": 3.0, "text": "Goodbye."},
            ])
        monkeypatch.setattr("requests.post", fake_post)

        result = core.transcribe_with_groq(str(audio), "en", "fake-groq-key")

        assert result == [{"start": 0.0, "end": 1.5, "text": "Hello there."},
                          {"start": 1.5, "end": 3.0, "text": "Goodbye."}]
        assert captured["headers"] == {"Authorization": "Bearer fake-groq-key"}
        assert captured["data"]["model"] == core.GROQ_DEFAULT_MODEL
        assert captured["data"]["language"] == "en"
        assert captured["timeout"] is not None  # never an unbounded hang

    def test_blank_segments_are_dropped(self, monkeypatch, tmp_path):
        import core
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        monkeypatch.setattr("requests.post", lambda *a, **k: self._FakeResponse(
            segments=[{"start": 0.0, "end": 1.0, "text": "   "},
                      {"start": 1.0, "end": 2.0, "text": "Real text."}]))

        result = core.transcribe_with_groq(str(audio), "en", "fake-key")

        assert result == [{"start": 1.0, "end": 2.0, "text": "Real text."}]

    def test_progress_cb_is_called_once_at_completion(self, monkeypatch, tmp_path):
        import core
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        monkeypatch.setattr("requests.post", lambda *a, **k: self._FakeResponse(segments=[]))
        seen = []

        core.transcribe_with_groq(str(audio), "en", "fake-key", progress_cb=seen.append)

        assert seen == [1.0]

    def test_a_non_200_response_raises_with_the_body_and_status(self, monkeypatch, tmp_path):
        import core
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        monkeypatch.setattr("requests.post", lambda *a, **k: self._FakeResponse(
            status_code=401, text="invalid api key"))

        try:
            core.transcribe_with_groq(str(audio), "en", "bad-key")
            assert False, "expected GroqTranscriptionError"
        except core.GroqTranscriptionError as exc:
            assert "401" in str(exc) and "invalid api key" in str(exc)

    def test_a_network_error_is_raised_as_groq_transcription_error(self, monkeypatch, tmp_path):
        import core
        import requests
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")

        def fake_post(*a, **k):
            raise requests.ConnectionError("could not connect")
        monkeypatch.setattr("requests.post", fake_post)

        try:
            core.transcribe_with_groq(str(audio), "en", "fake-key")
            assert False, "expected GroqTranscriptionError"
        except core.GroqTranscriptionError as exc:
            assert "could not connect" in str(exc)

    def test_a_key_leaked_into_the_error_message_is_redacted(self, monkeypatch, tmp_path):
        """Never put an API key in a shown/stored/logged error -- see
        translate_engines.redact_secrets and this project's own rule
        against reintroducing that class of bug."""
        import core
        import requests
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")

        def fake_post(*a, **k):
            raise requests.ConnectionError(
                "failed sending header Authorization: Bearer gsk_realsecretkey1234567890")
        monkeypatch.setattr("requests.post", fake_post)

        try:
            core.transcribe_with_groq(str(audio), "en", "gsk_realsecretkey1234567890")
            assert False, "expected GroqTranscriptionError"
        except core.GroqTranscriptionError as exc:
            assert "gsk_realsecretkey1234567890" not in str(exc)
            assert "[REDACTED]" in str(exc)

    def test_needs_a_timeout_so_a_hung_server_cannot_stick_a_job_at_running_forever(self):
        """Statically enforced too by
        tests/test_static_analysis.py::TestHttpCallsHaveTimeouts::test_core
        -- this test documents why, at the unit level."""
        import inspect
        import core
        assert "timeout=" in inspect.getsource(core.transcribe_with_groq)


class TestWhisperDeviceReporting:
    def _stub_faster_whisper(self, monkeypatch, cuda_error):
        import sys, types

        class FakeWhisperModel:
            def __init__(self, target, device, compute_type):
                if device == "cuda" and cuda_error:
                    raise RuntimeError(cuda_error)
                self.device = device

        mod = types.ModuleType("faster_whisper")
        mod.WhisperModel = FakeWhisperModel
        monkeypatch.setitem(sys.modules, "faster_whisper", mod)
        monkeypatch.setattr(core, "_whisper_model_cache", {})
        monkeypatch.setattr(core, "_whisper_device_info", {})

    def test_gpu_load_success_reports_gpu(self, monkeypatch):
        self._stub_faster_whisper(monkeypatch, None)
        core.load_whisper_model("tiny", use_gpu=True)
        info = core.get_whisper_device_info("tiny", use_gpu=True)
        assert info == {"device": "cuda", "compute_type": "float16", "gpu_error": None}
        assert core.describe_whisper_device(info) == "Using GPU (float16)"

    def test_gpu_failure_falls_back_and_reports_redacted_reason(self, monkeypatch):
        self._stub_faster_whisper(
            monkeypatch, "Library cublas64_12.dll is not found key=sk-abcdefghijklmnopqrstuvwx")
        model = core.load_whisper_model("tiny", use_gpu=True)
        assert model.device == "cpu"
        info = core.get_whisper_device_info("tiny", use_gpu=True)
        assert info["device"] == "cpu"
        assert "cublas64_12.dll" in info["gpu_error"]
        assert "sk-abcdefghijklmnopqrstuvwx" not in info["gpu_error"]
        text = core.describe_whisper_device(info)
        assert text.startswith("GPU unavailable (") and text.endswith("); using CPU")

    def test_cpu_request_reports_cpu(self, monkeypatch):
        self._stub_faster_whisper(monkeypatch, None)
        core.load_whisper_model("tiny", use_gpu=False)
        assert core.describe_whisper_device(
            core.get_whisper_device_info("tiny")) == "Using CPU (int8)"

    def test_gpu_status_never_raises_and_reports_both_probes(self, monkeypatch):
        import sys, types
        ct2 = types.ModuleType("ctranslate2")
        ct2.get_cuda_device_count = lambda: 1
        torch = types.ModuleType("torch")
        torch.cuda = types.SimpleNamespace(is_available=lambda: False)
        monkeypatch.setitem(sys.modules, "ctranslate2", ct2)
        monkeypatch.setitem(sys.modules, "torch", torch)
        assert core.gpu_status() == {"ctranslate2_cuda_devices": 1,
                                     "torch_cuda_available": False, "errors": []}
        ct2.get_cuda_device_count = lambda: (_ for _ in ()).throw(RuntimeError("boom"))
        status = core.gpu_status()
        assert status["ctranslate2_cuda_devices"] is None
        assert status["errors"] and "boom" in status["errors"][0]
