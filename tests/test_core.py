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
from tests.http_fakes import StreamedBody


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
        from core import is_network_error
        exc = Exception("Got: ConnectError: [Errno 11004] getaddrinfo failed")
        assert is_network_error(exc) is True

    def test_classifies_common_network_failures(self):
        from core import is_network_error
        for msg in ("httpx.ConnectError", "Max retries exceeded",
                    "LocalEntryNotFoundError", "Connection timed out",
                    "Temporary failure in name resolution", "proxy error"):
            assert is_network_error(Exception(msg)) is True, msg

    def test_does_not_misclassify_real_audio_or_gpu_errors(self):
        from core import is_network_error
        for msg in ("Invalid audio file format", "CUDA out of memory",
                    "unsupported sample rate"):
            assert is_network_error(Exception(msg)) is False, msg

    def test_model_download_error_is_a_runtime_error(self):
        from core import ModelDownloadError
        assert issubclass(ModelDownloadError, RuntimeError)

    def test_cache_check_returns_bool_and_never_raises(self):
        from core import is_whisper_model_cached
        assert isinstance(is_whisper_model_cached("medium"), bool)
        assert isinstance(is_whisper_model_cached("nonexistent-size"), bool)


class TestDefaultWhisperSize:
    """large-v3-turbo is the default and its label says so, without the old
    claim that it is weaker on Japanese/Korean (the benchmarks don't show it)."""

    def test_default_whisper_size_is_large_v3_turbo(self):
        from core import DEFAULT_WHISPER_SIZE, WHISPER_MODELS
        assert DEFAULT_WHISPER_SIZE == "large-v3-turbo"
        assert DEFAULT_WHISPER_SIZE in WHISPER_MODELS
        assert "default" in WHISPER_MODELS["large-v3-turbo"]
        assert "default" not in WHISPER_MODELS["medium"]
        assert "weaker" not in WHISPER_MODELS["large-v3-turbo"]


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
        fake.Anthropic = lambda api_key, **kw: types.SimpleNamespace(api_key=api_key, **kw)
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


class TestStockPhraseFilter:
    def _seg(self, start, end, text):
        return {"start": start, "end": end, "text": text}

    def test_isolated_stock_phrases_are_dropped(self):
        for text in ("Subtitles by the Amara.org community", "ご視聴ありがとうございました",
                     "请不吝点赞 订阅 转发 打赏", "字幕由Amara.org社群提供",
                     "MBC 뉴스 이덕영입니다", "Thanks for watching!"):
            segments = [self._seg(0, 2, "real line"), self._seg(30, 32, text),
                        self._seg(60, 62, "another line")]
            assert filter_hallucinated_segments(segments) == [segments[0], segments[2]], text

    def test_a_stock_phrase_at_the_end_of_the_audio_is_dropped(self):
        segments = [self._seg(0, 2, "real line"), self._seg(40, 42, "Thanks for watching")]
        assert filter_hallucinated_segments(segments) == [segments[0]]

    def test_a_stock_phrase_inside_dialogue_is_kept(self):
        segments = [self._seg(0, 2, "real line"), self._seg(2.5, 4, "Thanks for watching"),
                    self._seg(4.5, 6, "another line")]
        assert filter_hallucinated_segments(segments) == segments

    def test_silent_on_one_side_only_is_kept(self):
        segments = [self._seg(0, 2, "real line"), self._seg(2.5, 4, "Thanks for watching"),
                    self._seg(60, 62, "another line")]
        assert filter_hallucinated_segments(segments) == segments

    def test_real_dialogue_containing_the_words_is_kept_even_when_isolated(self):
        segments = [self._seg(0, 2, "real line"),
                    self._seg(30, 33, "He said thanks for watching over her all those years"),
                    self._seg(60, 62, "another line")]
        assert filter_hallucinated_segments(segments) == segments


class TestHallucinationSilenceThreshold:
    def _run(self, model_cls, **kwargs):
        import sys, types
        import core
        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda *a, **k: model_cls()
        sys.modules["faster_whisper"] = fake_fw
        core._whisper_model_cache.clear()
        return core.transcribe_for_timing("/fake/audio.mp3", **kwargs)

    def test_passed_by_default_and_overridable(self):
        seen = {}

        class Model:
            def transcribe(self, audio_path, **kwargs):
                seen.clear()
                seen.update(kwargs)
                return iter([]), None

        self._run(Model)
        assert seen["hallucination_silence_threshold"] == 2.0
        self._run(Model, hallucination_silence_sec=3.5)
        assert seen["hallucination_silence_threshold"] == 3.5

    def test_zero_does_not_pass_it(self):
        seen = {}

        class Model:
            def transcribe(self, audio_path, **kwargs):
                seen.update(kwargs)
                return iter([]), None

        self._run(Model, hallucination_silence_sec=0)
        assert "hallucination_silence_threshold" not in seen

    def test_an_older_faster_whisper_without_the_parameter_still_runs(self):
        seen = {}

        class OldModelStrict:
            def transcribe(self, audio_path, language=None, vad_filter=False, beam_size=5,
                           vad_parameters=None, word_timestamps=False,
                           condition_on_previous_text=True, no_repeat_ngram_size=0,
                           repetition_penalty=1.0, initial_prompt=None):
                seen["ok"] = True
                return iter([]), None

        self._run(OldModelStrict)
        assert seen == {"ok": True}


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


class TestTightenToWords:
    class W:
        def __init__(self, start, end):
            self.start, self.end = start, end

    def test_narrows_to_the_first_and_last_spoken_word(self):
        from core import tighten_to_words
        words = [self.W(3.2, 3.6), self.W(3.6, 4.1), self.W(4.1, 4.4)]
        assert tighten_to_words(1.0, 8.0, words) == (3.2, 4.4)

    def test_never_widens_the_segment(self):
        from core import tighten_to_words
        words = [self.W(0.5, 1.0), self.W(1.0, 9.0)]
        assert tighten_to_words(1.0, 8.0, words) == (1.0, 8.0)

    def test_keeps_the_segment_times_without_usable_words(self):
        from core import tighten_to_words
        for words in (None, [], [object()], [self.W(2.0, 2.0)], "not words"):
            assert tighten_to_words(1.0, 8.0, words) == (1.0, 8.0)

    def test_transcribe_uses_word_times_and_asks_whisper_for_them(self):
        import core, sys, types

        seen = {}

        class Seg:
            start, end, text = 0.0, 10.0, " hi "
            words = [self.W(4.0, 4.5), self.W(4.5, 5.0)]

        class Model:
            def transcribe(self, audio_path, **kwargs):
                seen.update(kwargs)
                return iter([Seg()]), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda *a, **k: Model()
        sys.modules["faster_whisper"] = fake_fw
        core._whisper_model_cache.clear()

        assert core.transcribe_for_timing("/fake/audio.mp3") == [
            {"start": 4.0, "end": 5.0, "text": "hi"}]
        assert seen["word_timestamps"] is True


class TestPunctuationOnlySegmentsDropped:
    def test_a_lone_bracket_is_dropped_but_real_and_tag_lines_stay(self):
        import core, sys, types

        class Seg:
            def __init__(self, start, text):
                self.start, self.end, self.text, self.words = start, start + 1.0, text, None

        class Model:
            def transcribe(self, audio_path, **kwargs):
                texts = ["[", "你好", "...", "[Music]", "  —  ", "こんにちは。", "7"]
                return iter([Seg(float(i), t) for i, t in enumerate(texts)]), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda *a, **k: Model()
        sys.modules["faster_whisper"] = fake_fw
        core._whisper_model_cache.clear()

        got = core.transcribe_for_timing("/fake/audio.mp3")

        assert [g["text"] for g in got] == ["你好", "[Music]", "こんにちは。", "7"]


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
        from core import is_gpu_error
        exc = RuntimeError("Library cublas64_12.dll is not found or cannot be loaded")
        assert is_gpu_error(exc) is True

    def test_classifies_common_cuda_failures(self):
        from core import is_gpu_error
        for msg in ("CUDA error: no kernel image is available",
                    "cuDNN error", "no CUDA-capable device is detected",
                    "CUDA out of memory"):
            assert is_gpu_error(RuntimeError(msg)) is True, msg

    def test_does_not_misclassify_network_or_genuine_errors(self):
        from core import is_gpu_error
        assert is_gpu_error(RuntimeError("Connection timed out")) is False
        assert is_gpu_error(ValueError("Invalid audio file format")) is False

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

    class _FakeResponse(StreamedBody):
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

        def fake_post(url, headers=None, files=None, data=None, timeout=None, stream=None):
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


class TestSplitLongSegments:
    @staticmethod
    def _seg(text, start=10.0, end=40.0, **extra):
        return {"start": start, "end": end, "text": text, **extra}

    @staticmethod
    def _check(seg, pieces):
        assert pieces[0]["start"] == seg["start"] and pieces[-1]["end"] == seg["end"]
        for a, b in zip(pieces, pieces[1:]):
            assert a["end"] == b["start"]
        assert all(p["start"] < p["end"] for p in pieces)
        assert "".join("".join(p["text"].split()) for p in pieces) == "".join(seg["text"].split())

    def test_splits_at_sentence_ends_and_keeps_text(self):
        import core
        seg = self._seg("好,你刚讲不要讲。哇,我先离开一下。好,OK。重来。哇,大家好哦!" * 3,
                        speaker="A")
        out = core.split_long_segments([seg], max_seconds=8, max_cjk_chars=1000)
        assert len(out) > 3
        self._check(seg, out)
        assert all(p["speaker"] == "A" for p in out)
        assert all(p["end"] - p["start"] <= 8.0 + 1e-6 for p in out)
        assert all(p["text"][-1] in "。!" for p in out)

    def test_comma_fallback_for_one_long_sentence(self):
        import core
        seg = self._seg(",".join(["一二三四五六七八九十"] * 8) + "。", 0.0, 30.0)
        out = core.split_long_segments([seg], max_seconds=8)
        assert len(out) > 1
        self._check(seg, out)

    def test_no_punctuation_left_alone(self):
        import core
        seg = self._seg("一二三四五六七八九十" * 10)
        assert core.split_long_segments([seg]) == [seg]

    def test_cjk_spaces_are_cut_points_when_no_punctuation(self):
        import core
        seg = self._seg(" ".join(["我們記得昨天早的時候呢"] * 6), 0.0, 26.0)
        out = core.split_long_segments([seg], max_seconds=8)
        assert len(out) > 1
        self._check(seg, out)
        assert all(p["end"] - p["start"] <= 8.0 + 1e-6 for p in out)

    def test_space_fallback_respects_char_limit(self):
        import core
        seg = self._seg(" ".join(["一二三四五六七八九十"] * 7), 0.0, 6.0)
        out = core.split_long_segments([seg], max_seconds=8, max_cjk_chars=40)
        assert len(out) > 1
        self._check(seg, out)
        assert all(len(core._CJK_RE.findall(p["text"])) <= 40 for p in out)

    def test_space_fallback_keeps_latin_tokens_whole(self):
        import core
        seg = self._seg("這是一個很長的句子呢 Dormi Q&A 絕不NG的表單 " * 4, 0.0, 30.0)
        out = core.split_long_segments([seg], max_seconds=8)
        assert len(out) > 1
        self._check(seg, out)
        for p in out:
            assert not p["text"].startswith(("Q&A", "A ")) and not p["text"].endswith(("Dormi", "Q&A "[:3]))
            assert "Dormi Q&A" in p["text"] or "Dormi" not in p["text"]

    def test_punctuated_line_not_cut_at_spaces(self):
        import core
        seg = self._seg("一二三 四五六。七八九 十一二。", 0.0, 12.0)
        out = core.split_long_segments([seg], max_seconds=8)
        assert [p["text"] for p in out] == ["一二三 四五六。", "七八九 十一二。"]

    def test_spaceless_unpunctuated_line_still_whole(self):
        import core
        seg = self._seg("一二三四五六七八九十" * 10, 0.0, 30.0)
        assert core.split_long_segments([seg]) == [seg]

    def test_short_line_untouched(self):
        import core
        seg = self._seg("好。你好。再见。", 0.0, 3.0)
        assert core.split_long_segments([seg]) == [seg]

    def test_mixed_cjk_latin_japanese_korean(self):
        import core
        for text in ("今日はいい天気ですね。Let's go to the park. Really? 行きましょう!" * 2,
                     "안녕하세요. 오늘은 날씨가 좋네요? Okay, let's go. 갑시다!" * 2):
            seg = self._seg(text, 5.0, 25.0)
            out = core.split_long_segments([seg], max_seconds=8)
            assert len(out) > 1
            self._check(seg, out)

    def test_decimal_point_is_not_a_sentence_end(self):
        import core
        seg = self._seg("Pi is 3.14159 and e is 2.71828 which is nice", 0.0, 30.0)
        assert core.split_long_segments([seg], max_seconds=8) == [seg]


class TestWordTimestampsKept:
    """Whisper's word timings ride on the segments so split_long_segments can cut
    an unpunctuated line at a real pause."""

    @staticmethod
    def _words(text, step=0.25, pauses=None):
        """One word per character: {start, end, word}, `step` seconds each, with the
        extra silences in `pauses` ({char index: seconds}) inserted before a char."""
        out, t = [], 0.0
        for i, ch in enumerate(text):
            t += (pauses or {}).get(i, 0.0)
            out.append({"start": t, "end": t + step * 0.9, "word": ch})
            t += step
        return out

    @classmethod
    def _seg(cls, text, pauses=None, **extra):
        words = cls._words(text, pauses=pauses)
        return {"start": words[0]["start"], "end": words[-1]["end"], "text": text,
                "words": words, **extra}

    def test_transcribe_keeps_the_words_as_plain_dicts(self):
        import core, sys, types

        class Word:
            def __init__(self, start, end, word):
                self.start, self.end, self.word, self.probability = start, end, word, 0.9

        class Seg:
            start, end, text = 0.0, 2.0, " 你好 "
            words = [Word(0.2, 0.6, "你"), Word(0.6, 1.0, "好")]

        class Model:
            def transcribe(self, audio_path, **kwargs):
                return iter([Seg()]), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda *a, **k: Model()
        sys.modules["faster_whisper"] = fake_fw
        core._whisper_model_cache.clear()
        assert core.transcribe_for_timing("/fake/audio.mp3") == [
            {"start": 0.2, "end": 1.0, "text": "你好",
             "words": [{"start": 0.2, "end": 0.6, "word": "你"},
                       {"start": 0.6, "end": 1.0, "word": "好"}]}]

    def test_fast_mode_gets_the_same_word_timestamps_kwarg(self):
        import core, sys, types
        seen = {}

        class Seg:
            start, end, text, words = 0.0, 1.0, "hi", []

        class Pipeline:
            def __init__(self, model):
                pass

            def transcribe(self, audio_path, **kwargs):
                seen.update(kwargs)
                return iter([Seg()]), None

        fake_fw = types.ModuleType("faster_whisper")
        fake_fw.WhisperModel = lambda *a, **k: object()
        fake_fw.BatchedInferencePipeline = Pipeline
        sys.modules["faster_whisper"] = fake_fw
        core._whisper_model_cache.clear()
        core.transcribe_for_timing("/fake/audio.mp3", fast_mode=True)
        assert seen["word_timestamps"] is True

    def test_a_line_with_no_punctuation_is_cut_at_the_real_pauses(self):
        import core
        text = "我今天去了公园然后看到很多人在那边跳舞我也跟着跳了一会儿觉得很开心" * 2
        # 66 chars at 0.25 s = 16.5 s, plus 1.6 s of real silences after chars 22 and 44
        seg = self._seg(text, pauses={22: 0.9, 44: 0.7})
        assert seg["end"] - seg["start"] > 17
        out = core.split_long_segments([seg])
        assert [p["text"] for p in out] == [text[:22], text[22:44], text[44:]]
        assert "".join(p["text"] for p in out) == text
        words = seg["words"]
        assert [(p["start"], p["end"]) for p in out] == [
            (words[0]["start"], words[21]["end"]), (words[22]["start"], words[43]["end"]),
            (words[44]["start"], words[-1]["end"])]
        assert [p["words"] for p in out] == [words[:22], words[22:44], words[44:]]

    def test_a_17_second_unpunctuated_clip_with_four_pauses_is_cut(self):
        import core
        # about 17 s and 56 characters: 56 * 0.25 s = 14 s of speech plus 3 s of pauses
        text = "我今天去了公园然后看到很多人在那边跳舞我也跟着跳了一会儿觉得很开心真的太好玩了下次还想再来而且天气特别好大家都很高兴一直玩到天黑才回家"[:56]
        assert len(text) == 56
        seg = self._seg(text, pauses={14: 0.75, 28: 1.0, 42: 0.75, 49: 0.5})
        assert 16.5 < seg["end"] - seg["start"] < 17.5
        out = core.split_long_segments([seg])
        assert len(out) >= 2 and "".join(p["text"] for p in out) == text
        assert all(p["end"] - p["start"] <= core.SPLIT_MAX_SECONDS for p in out)

    def test_cuts_happen_only_while_pieces_are_still_too_long(self):
        import core
        text = "一二三四五六七八九十" * 4
        seg = self._seg(text, pauses={10: 0.5, 20: 0.9, 30: 0.5})
        out = core.split_long_segments([seg], max_seconds=8)
        assert [p["text"] for p in out] == [text[:20], text[20:]]

    def test_pieces_near_the_middle_win_among_similar_pauses(self):
        import core
        text = "一二三四五六七八九十" * 4
        seg = self._seg(text, pauses={5: 0.9, 20: 0.8, 35: 0.9})
        out = core.split_long_segments([seg], max_seconds=8)
        assert out[0]["text"] == text[:20]

    def test_punctuation_cuts_use_the_word_times(self):
        import core
        text = "我们先去吃饭。然后再去看电影吧。"
        seg = self._seg(text, pauses={7: 1.5})
        out = core.split_long_segments([seg], max_seconds=3)
        assert [p["text"] for p in out] == ["我们先去吃饭。", "然后再去看电影吧。"]
        assert out[1]["start"] == seg["words"][7]["start"]
        assert out[0]["end"] == seg["words"][6]["end"]
        assert out[0]["end"] < out[1]["start"] - 1.4

    def test_mixed_latin_and_cjk_text_maps_exactly(self):
        import core
        words = [("我们", 0.0, 1.0), (" 用", 1.0, 1.5), (" Python", 1.5, 3.0), (" 写", 3.0, 4.0),
                 ("程序", 4.0, 5.5), (" 然后", 7.0, 8.5), (" 发布", 8.5, 10.0),
                 (" 到", 10.0, 11.0), (" GitHub", 11.0, 12.5)]
        text = "我们 用 Python 写程序 然后 发布 到 GitHub"
        seg = {"start": 0.0, "end": 12.5, "text": text,
               "words": [{"start": s, "end": e, "word": w} for w, s, e in words]}
        out = core.split_long_segments([seg], max_seconds=8)
        assert [p["text"] for p in out] == ["我们 用 Python 写程序", "然后 发布 到 GitHub"]
        assert (out[0]["end"], out[1]["start"]) == (5.5, 7.0)

    def test_words_that_do_not_spell_the_text_leave_the_line_alone(self):
        import core
        text = "我今天去了公园然后看到很多人在那边跳舞我也跟着跳了一会儿觉得很开心" * 2
        seg = self._seg(text, pauses={22: 0.9, 44: 0.7})
        seg["words"][10]["word"] = "错"
        assert core.split_long_segments([seg]) == [seg]
        seg["words"][10]["word"] = text[10]
        seg["text"] = text + "。"  # words no longer cover the text
        assert [p["text"] for p in core.split_long_segments([seg])] == [seg["text"]]

    def test_a_punctuation_cut_inside_a_word_falls_back_to_the_estimate(self):
        import core
        text = "我们先去吃饭。然后再去看电影吧。" * 2
        seg = self._seg(text)
        seg["words"][6:8] = [{"start": seg["words"][6]["start"], "end": seg["words"][7]["end"],
                             "word": text[6:8]}]
        out = core.split_long_segments([seg], max_seconds=3)
        assert len(out) > 1 and "words" not in out[0]
        assert out[0]["start"] == seg["start"] and out[-1]["end"] == seg["end"]

    def test_the_default_pause_is_035_within_its_bounds(self):
        import core
        assert core.MIN_WORD_GAP_SECONDS == 0.35
        assert (core.MIN_WORD_GAP_SECONDS_MIN, core.MIN_WORD_GAP_SECONDS_MAX) == (0.1, 2.0)

    def test_the_pause_setting_decides_which_pauses_cut(self):
        import core
        text = "一二三四五六七八九十" * 7
        seg = self._seg(text, pauses={17: 0.30, 52: 0.50})

        def cuts(**kw):
            return [len(p["text"]) for p in core.split_long_segments([seg], max_seconds=8, **kw)]
        assert cuts(min_pause=0.25) == [17, 35, 18]  # both pauses
        assert cuts(min_pause=0.35) == [52, 18]  # only the 0.50 s one
        assert cuts(min_pause=0.45) == [52, 18]
        assert cuts(min_pause=0.55) == [70]
        assert cuts() == [52, 18]  # the default is 0.35

    def test_pause_offsets_follow_the_setting(self):
        import core
        text = "一二三四五六七八九十" * 2
        index = core._WordIndex.build(text, self._seg(text, pauses={5: 0.30, 12: 0.50})["words"])
        assert core.pause_offsets(index, 0.25) == {5, 12}
        assert core.pause_offsets(index, 0.4) == {12}
        assert core.pause_offsets(index) == {12}

    def test_no_pause_long_enough_leaves_the_line_whole(self):
        import core
        text = "一二三四五六七八九十" * 4
        seg = self._seg(text, pauses={20: core.MIN_WORD_GAP_SECONDS - 0.05})
        assert core.split_long_segments([seg], max_seconds=8) == [seg]

    @staticmethod
    def _seg_with_exact_gap(text, gap):
        """Words that touch (end = next start) except `gap` before char 20; every
        time is a multiple of 1/16, exact in binary, so the gap is exact."""
        words, t = [], 0.0
        for i, ch in enumerate(text):
            t += gap if i == 20 else 0.0
            words.append({"start": t, "end": t + 0.25, "word": ch})
            t += 0.25
        return {"start": 0.0, "end": t, "text": text, "words": words}

    def test_pause_exactly_at_the_minimum_is_a_cut_point(self):
        import core
        text = "一二三四五六七八九十" * 4
        seg = self._seg_with_exact_gap(text, 0.25)  # a binary-exact gap, so >= is tested exactly
        assert len(core.split_long_segments([seg], max_seconds=8, min_pause=0.25)) == 2

    def test_pause_just_under_the_minimum_is_not_a_cut_point(self):
        import core
        text = "一二三四五六七八九十" * 4
        seg = self._seg_with_exact_gap(text, 0.25 - 0.0625)
        assert core.split_long_segments([seg], max_seconds=8, min_pause=0.25) == [seg]

    def test_floors_keep_a_stub_from_being_cut_off(self):
        import core
        text = "一二三四五六七八九十" * 4
        # the only long pauses leave 2 chars (0.5 s) on one side
        seg = self._seg(text, pauses={2: 1.0, 38: 1.0})
        assert core.split_long_segments([seg], max_seconds=8) == [seg]

    def test_re_split_rules_apply_to_word_cuts(self):
        import core
        text = "一二三四五六七八九十" * 4
        seg = self._seg(text, pauses={10: 0.4, 25: 0.6})
        rules = core.SplitRules(max_seconds=None, max_chars=25)
        out = core.split_long_segments([seg], rules=rules)
        assert [p["text"] for p in out] == [text[:25], text[25:]]
        rules = core.SplitRules(max_seconds=None, max_chars=12)
        out = core.split_long_segments([seg], rules=rules)
        assert "".join(p["text"] for p in out) == text
        assert all(len(p["text"]) >= core.MIN_PIECE_CJK_CHARS for p in out)

    def test_other_keys_are_copied_and_unused_words_stay_on_a_short_line(self):
        import core
        seg = self._seg("一二三四五", speaker="A")
        assert core.split_long_segments([seg]) == [seg]
        long_seg = self._seg("一二三四五六七八九十" * 4, pauses={20: 0.9}, speaker="B")
        assert {p["speaker"] for p in core.split_long_segments([long_seg], max_seconds=8)} == {"B"}

    def test_segments_without_words_behave_as_before(self):
        import core
        seg = {"start": 0.0, "end": 20.0, "text": "一二三四五六七八九十" * 10}
        assert core.split_long_segments([seg]) == [seg]

    def test_hallucination_filter_and_coverage_ignore_the_extra_key(self):
        import core
        from services.transcribe_service import coverage_warning
        segs = [self._seg("你好吗"), self._seg("你好吗"), self._seg("再见了")]
        assert core.filter_hallucinated_segments(segs) == segs
        coverage_warning(segs, 100.0)

    def test_a_hundred_thousand_characters_is_not_quadratic(self):
        import time
        import core
        # 100k one-character words with a pause every 20 words, in one line
        text = "一二三四五六七八九十" * 10000
        pauses = {i: 0.5 for i in range(20, len(text), 20)}
        seg = self._seg(text, pauses=pauses)
        started = time.perf_counter()
        out = core.split_long_segments([seg])
        elapsed = time.perf_counter() - started
        assert "".join(p["text"] for p in out) == text
        assert all(p["end"] - p["start"] <= 8.0 + 1e-6 for p in out)
        assert elapsed < 10.0

    def test_growing_pauses_do_not_peel_one_piece_at_a_time(self):
        import time
        import core
        # the biggest pause is always at the far end: a naive pick-the-largest scan is quadratic
        text = "一二三四五六七八九十" * 5000
        pauses = {i: 0.3 + i / 1e5 for i in range(10, len(text), 10)}
        seg = self._seg(text, pauses=pauses)
        started = time.perf_counter()
        out = core.split_long_segments([seg])
        assert "".join(p["text"] for p in out) == text
        assert time.perf_counter() - started < 10.0

    def test_random_pauses_always_give_lossless_ordered_pieces(self):
        import random
        import core
        rng = random.Random(165)
        for _ in range(60):
            text = "".join(rng.choice("我你他是不了在有人这中大来上国个到说们为") for _ in range(rng.randint(30, 400)))
            pauses = {i: rng.choice([0.0, 0.0, 0.1, 0.3, 0.6, 1.2]) for i in range(1, len(text))}
            seg = self._seg(text, pauses=pauses)
            out = core.split_long_segments([seg])
            assert "".join(p["text"] for p in out) == text
            assert all(a["end"] <= b["start"] for a, b in zip(out, out[1:]))
            assert all(p["start"] < p["end"] for p in out)
            assert sum(len(p["words"]) for p in out) == len(text)
