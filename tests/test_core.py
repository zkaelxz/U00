"""
tests/test_core.py -- tests for core.py, the pipeline logic shared by
the GUI and CLI (no Streamlit/database dependency).
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import pytest

import whisper_transcribe
import whisper_models
from segment_splitting import encode_line_words, line_words

from core import Line, fmt_ts, lines_to_srt, lines_to_bilingual_srt
from line_alignment import split_user_transcript, align_transcript_to_timing, merge_adjacent_short_lines
from novel_text import chunk_novel_text
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


    def test_line_ends_where_its_last_character_ends(self):
        # Characters get evenly spaced slots; a line used to end at the START
        # of its last character's slot, one character early.
        segments = [{"start": 0.0, "end": 4.0, "text": "你好世界"}]
        lines = align_transcript_to_timing(["你好世界。"], segments)
        assert lines[0].start == 0.0
        assert lines[0].end == pytest.approx(4.0)

    def test_second_line_still_starts_after_the_first_ends(self):
        segments = [{"start": 0.0, "end": 4.0, "text": "你好世界再见朋友"}]
        first, second = align_transcript_to_timing(["你好世界。", "再见朋友。"], segments)
        assert first.end == pytest.approx(2.0)
        assert second.start >= first.end


class TestMergeAdjacentShortLines:
    def test_korean_lines_keep_the_space_between_words(self):
        lines = [Line(idx=0, start=0.0, end=0.5, zh="안녕", en="Hi", speaker="A"),
                 Line(idx=1, start=0.6, end=1.0, zh="하세요", en="there", speaker="A")]
        assert merge_adjacent_short_lines(lines)[0].zh == "안녕 하세요"

    def test_an_sfx_line_never_merges_into_speech_or_the_reverse(self):
        for first_sfx, second_sfx in ((True, False), (False, True)):
            lines = [Line(idx=0, start=0.0, end=0.5, zh="[笑]", en="[laughs]", speaker="A",
                          sfx=first_sfx),
                     Line(idx=1, start=0.6, end=1.0, zh="你好", en="Hi", speaker="A",
                          sfx=second_sfx)]
            assert len(merge_adjacent_short_lines(lines)) == 2

    def test_lines_with_different_manual_speaker_marks_stay_apart(self):
        lines = [Line(idx=0, start=0.0, end=0.5, zh="你", en="You", speaker="A",
                      speaker_manual=True),
                 Line(idx=1, start=0.6, end=1.0, zh="好", en="ok", speaker="A")]
        assert len(merge_adjacent_short_lines(lines)) == 2

    def test_korean_space_survives_sentence_punctuation_on_the_first_line(self):
        lines = [Line(idx=0, start=0.0, end=0.5, zh="안녕.", en="Hi", speaker="A"),
                 Line(idx=1, start=0.6, end=1.0, zh="하세요", en="there", speaker="A")]
        assert merge_adjacent_short_lines(lines)[0].zh == "안녕. 하세요"

    def test_chinese_and_japanese_lines_join_without_a_space(self):
        for a, b in (("你好", "世界"), ("こんにちは", "元気"), ("你好。", "世界"), ("はい。", "元気")):
            lines = [Line(idx=0, start=0.0, end=0.5, zh=a, en="x", speaker="A"),
                     Line(idx=1, start=0.6, end=1.0, zh=b, en="y", speaker="A")]
            assert merge_adjacent_short_lines(lines)[0].zh == a + b

    def test_labelled_speaker_change_never_merges(self):
        lines = [Line(idx=0, start=0.0, end=0.5, zh="你好", en="x", speaker="A"),
                 Line(idx=1, start=0.6, end=1.0, zh="世界", en="y", speaker="B"),
                 Line(idx=2, start=1.1, end=1.4, zh="再见", en="z", speaker=None)]
        assert len(merge_adjacent_short_lines(lines)) == 3

    def test_unlabelled_turn_change_dash_never_merges(self):
        for mark in ("-", "—", "－"):
            lines = [Line(idx=0, start=0.0, end=0.5, zh="你骗人", en="x"),
                     Line(idx=1, start=0.6, end=1.0, zh=mark + "我没有", en="y")]
            assert len(merge_adjacent_short_lines(lines)) == 2

    def test_merged_line_keeps_id_and_words_that_spell_its_text(self):
        def words(text, start):
            return encode_line_words(text, [{"word": text, "start": start, "end": start + 0.4}])
        first = Line(idx=0, start=0.0, end=0.5, zh="안녕", en="x", speaker="A", id=7,
                     word_timings=words("안녕", 0.0))
        second = Line(idx=1, start=0.6, end=1.0, zh="하세요", en="y", speaker="A", id=8,
                      word_timings=words("하세요", 0.6))
        merged = merge_adjacent_short_lines([first, second])
        assert [ln.id for ln in merged] == [7] and merged[0].merged_ids == [8]
        assert [w["word"] for w in line_words(merged[0])] == ["안녕", "하세요"]

    def test_words_are_dropped_when_one_line_has_none(self):
        first = Line(idx=0, start=0.0, end=0.5, zh="你好", en="x", id=1,
                     word_timings=encode_line_words(
                         "你好", [{"word": "你好", "start": 0.0, "end": 0.4}]))
        second = Line(idx=1, start=0.6, end=1.0, zh="世界", en="y", id=2)
        assert merge_adjacent_short_lines([first, second])[0].word_timings is None

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


class TestInitialPromptBuilding:
    """Priming Whisper with expected proper nouns is the biggest free
    accuracy win for Chinese, where a misheard name is usually still a
    valid word and so passes silently into the translation."""

    def test_builds_from_glossary_rows(self):
        from whisper_transcribe import build_initial_prompt
        p = build_initial_prompt([{"term_original": "沈清疑"}, {"term_original": "云隐宗"}])
        assert "沈清疑" in p and "云隐宗" in p

    def test_accepts_plain_strings(self):
        from whisper_transcribe import build_initial_prompt
        assert "岳家" in build_initial_prompt(["岳家", "神机营"])

    def test_ends_with_a_full_stop(self):
        from whisper_transcribe import build_initial_prompt
        assert build_initial_prompt(["x"]).endswith("。")

    def test_empty_inputs_give_empty_prompt(self):
        from whisper_transcribe import build_initial_prompt
        assert build_initial_prompt([]) == ""
        assert build_initial_prompt(None) == ""
        assert build_initial_prompt([{"term_original": "   "}]) == ""

    def test_caps_term_count(self):
        from whisper_transcribe import build_initial_prompt
        p = build_initial_prompt([{"term_original": f"t{i}"} for i in range(100)], max_terms=40)
        assert p.count("、") == 39

    def test_skips_blank_terms(self):
        from whisper_transcribe import build_initial_prompt
        assert build_initial_prompt(["", "  ", "实名"]) == "实名。"

    def test_primes_recorded_aliases_alongside_the_canonical_original(self):
        """Step 30: a glossary row's aliases (alt spellings/transliterations
        of term_original) are included in Whisper's priming context too,
        not just the canonical original."""
        from whisper_transcribe import build_initial_prompt
        p = build_initial_prompt([{"term_original": "沈清疑", "aliases": "沈清儀|Shen Qing Yi"}])
        assert "沈清疑" in p and "沈清儀" in p and "Shen Qing Yi" in p

    def test_a_glossary_row_with_no_aliases_field_still_works(self):
        from whisper_transcribe import build_initial_prompt
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
        from engine_backends import claude as engine_claude
        default = inspect.signature(engine_claude.ClaudeEngine.__init__).parameters["model"].default
        assert default == "claude-sonnet-5-5"

    def test_every_selectable_model_has_pricing(self):
        from engine_backends import pricing
        for model in pricing.CLAUDE_MODELS:
            assert model in pricing.PRICING_PER_MILLION_TOKENS

    def test_get_engine_threads_a_chosen_model_through(self, monkeypatch):
        import types
        from engine_backends import pricing, engine_registry
        fake = types.ModuleType("anthropic")
        fake.Anthropic = lambda api_key, **kw: types.SimpleNamespace(api_key=api_key, **kw)
        fake.Timeout = lambda **kw: kw
        monkeypatch.setitem(sys.modules, "anthropic", fake)
        for model in pricing.CLAUDE_MODELS:
            eng = engine_registry.get_engine("claude", "fake-key", model)
            assert eng.model == model


class TestVadSensitivity:
    def test_transcribe_signature_accepts_min_silence_duration(self):
        import inspect
        from whisper_transcribe import transcribe_for_timing
        params = inspect.signature(transcribe_for_timing).parameters
        assert "min_silence_duration_ms" in params
        assert params["min_silence_duration_ms"].default == 2000

    def test_transcribe_signature_accepts_vad_threshold(self):
        import inspect
        from whisper_transcribe import transcribe_for_timing
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
        import whisper_transcribe
        whisper_models._whisper_model_cache.clear()

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

        whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", vad_threshold=0.7)

        assert captured["vad_parameters"]["threshold"] == 0.7

    def test_vad_threshold_defaults_to_point_five(self, monkeypatch):
        import sys, types
        import whisper_transcribe
        whisper_models._whisper_model_cache.clear()

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

        whisper_transcribe.transcribe_for_timing("/fake/audio.mp3")

        assert captured["vad_parameters"]["threshold"] == 0.5


class TestWhisperPrompt:
    def _seen(self, monkeypatch, texts=(), **call):
        import types
        import whisper_transcribe
        seen = {}

        class Seg:
            def __init__(self, start, text):
                self.start, self.end, self.text, self.words = start, start + 1, text, None

        def run(audio_path, **kwargs):
            seen.update(kwargs)
            return iter([Seg(i * 2, t) for i, t in enumerate(texts)]), types.SimpleNamespace(duration=99)

        class Model:
            pass
        model = Model()
        model.transcribe = run
        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: model)
        out = whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", **call)
        return seen, [s["text"] for s in out]

    def test_names_are_an_initial_prompt_and_the_script_hint_comes_first(self, monkeypatch):
        seen, _ = self._seen(monkeypatch, language="zh", initial_prompt="苏杉、沈清疑。",
                             chinese_script="simplified")
        assert seen["initial_prompt"] == "以下是普通话的句子。苏杉、沈清疑。"
        assert "hotwords" not in seen
        seen, _ = self._seen(monkeypatch, language="zh", chinese_script="traditional")
        assert seen["initial_prompt"] == "以下是普通話的句子。"

    def test_the_v62_gate_wraps_the_decode_only_when_asked(self, monkeypatch):
        import contextlib
        import vad_model
        entered = []

        @contextlib.contextmanager
        def gated():
            entered.append(True)
            yield True
        monkeypatch.setattr(vad_model, "gated", gated)
        self._seen(monkeypatch, language="zh")
        assert entered == []
        self._seen(monkeypatch, language="zh", standalone_extra_speech=True)
        assert entered == [True]

    def test_no_hint_outside_chinese_or_without_a_chosen_script(self, monkeypatch):
        for call in ({"language": "ja", "chinese_script": "simplified"},
                     {"language": "zh", "chinese_script": None}, {"language": "zh"}):
            seen, _ = self._seen(monkeypatch, **call)
            assert "hotwords" not in seen and "initial_prompt" not in seen, call

    def test_the_prompt_written_back_as_a_line_is_dropped(self, monkeypatch):
        _, texts = self._seen(monkeypatch, texts=["以下是普通话的句子", "苏杉来了。", "以下是普通话的句子。苏杉。"],
                              language="zh", initial_prompt="苏杉。", chinese_script="simplified")
        assert texts == ["苏杉来了。"]

    def test_a_line_that_only_says_the_one_primed_name_is_kept(self, monkeypatch):
        _, texts = self._seen(monkeypatch, texts=["アキラ！"], language="ja", initial_prompt="アキラ。")
        assert texts == ["アキラ！"]


class TestLineCoverageDiagnosis:
    """Regression cover built directly from a real uploaded file: several
    lines spanning many minutes with only a few characters of text each,
    the signature of the voice-activity detector merging multiple real
    lines of dialogue into one oversized segment."""

    def test_catches_the_real_worst_case_from_the_field(self):
        from core import Line
        from line_alignment import diagnose_line_coverage
        lines = [
            Line(idx=0, start=390.0, end=413.0, zh="还有其他吗", en="x"),
            Line(idx=1, start=431.0, end=477.0, zh="都好", en="x"),        # 46s, 2 chars
            Line(idx=2, start=2262.0, end=2668.0, zh="清除叛徒", en="x"),  # 6.4 minutes
        ]
        report = diagnose_line_coverage(lines)
        assert len(report["long_lines"]) == 3
        assert report["long_lines"][0]["idx"] == 2  # worst offender sorts first

    def test_normal_short_line_is_not_flagged(self):
        from core import Line
        from line_alignment import diagnose_line_coverage
        lines = [Line(idx=0, start=0.0, end=2.0, zh="正常对话", en="Normal.")]
        assert diagnose_line_coverage(lines)["long_lines"] == []

    def test_detects_large_silent_gaps(self):
        from core import Line
        from line_alignment import diagnose_line_coverage
        lines = [Line(idx=0, start=0.0, end=1.0, zh="a", en="a"),
                 Line(idx=1, start=10.0, end=11.0, zh="b", en="b")]
        report = diagnose_line_coverage(lines, gap_seconds=3.0)
        assert len(report["large_gaps"]) == 1
        assert report["large_gaps"][0]["gap_seconds"] == 9.0

    def test_small_gap_not_flagged(self):
        from core import Line
        from line_alignment import diagnose_line_coverage
        lines = [Line(idx=0, start=0.0, end=1.0, zh="a", en="a"),
                 Line(idx=1, start=1.5, end=2.5, zh="b", en="b")]
        assert diagnose_line_coverage(lines, gap_seconds=3.0)["large_gaps"] == []

    def test_detects_blank_source_text(self):
        from core import Line
        from line_alignment import diagnose_line_coverage
        lines = [Line(idx=0, start=0.0, end=1.0, zh="", en="")]
        report = diagnose_line_coverage(lines)
        assert len(report["blank_zh"]) == 1

    def test_detects_untranslated_lines_with_source_present(self):
        from core import Line
        from line_alignment import diagnose_line_coverage
        lines = [Line(idx=0, start=0.0, end=1.0, zh="有文本", en="")]
        report = diagnose_line_coverage(lines)
        assert len(report["blank_en"]) == 1
        assert report["blank_en"][0]["zh"] == "有文本"

    def test_empty_input_does_not_crash(self):
        from line_alignment import diagnose_line_coverage
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
        import whisper_transcribe
        segments = [self._FakeSegment(0.0, 30.0, "a"),
                    self._FakeSegment(30.0, 90.0, "b"),
                    self._FakeSegment(90.0, 120.0, "c")]
        self._stub_faster_whisper(segments, self._FakeInfo(duration=120.0))
        whisper_models._whisper_model_cache.clear()

        seen = []
        result = whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", progress_cb=seen.append)

        assert len(result) == 3
        assert seen == [0.25, 0.75, 1.0]

    def test_progress_cb_is_optional(self):
        import whisper_transcribe
        segments = [self._FakeSegment(0.0, 10.0, "a")]
        self._stub_faster_whisper(segments, self._FakeInfo(duration=10.0))
        whisper_models._whisper_model_cache.clear()

        result = whisper_transcribe.transcribe_for_timing("/fake/audio.mp3")
        assert result == [{"start": 0.0, "end": 10.0, "text": "a"}]

    def test_missing_duration_does_not_crash(self):
        """info can legitimately be None (some callers/tests stub it that
        way) or lack .duration -- progress just can't be estimated then."""
        import whisper_transcribe
        segments = [self._FakeSegment(0.0, 10.0, "a")]
        self._stub_faster_whisper(segments, info=None)
        whisper_models._whisper_model_cache.clear()

        seen = []
        result = whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", progress_cb=seen.append)
        assert result == [{"start": 0.0, "end": 10.0, "text": "a"}]
        assert seen == [0.0]

    def test_progress_cb_called_on_gpu_fallback_path_too(self):
        import whisper_transcribe
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
        whisper_models._whisper_model_cache.clear()

        seen = []
        result = whisper_transcribe.transcribe_for_timing("/fake/audio.mp3", use_gpu=True, progress_cb=seen.append)
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
        import whisper_transcribe
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        captured = {}

        def fake_post(url, ip, headers, timeout=None, method="GET", files=None, data=None):
            captured["url"], captured["headers"] = url, headers
            captured["data"], captured["timeout"] = data, timeout
            return self._FakeResponse(segments=[
                {"start": 0.0, "end": 1.5, "text": " Hello there. "},
                {"start": 1.5, "end": 3.0, "text": "Goodbye."},
            ])
        monkeypatch.setattr("lib.http.pinned_get", fake_post)

        result = whisper_transcribe.transcribe_with_groq(str(audio), "en", "fake-groq-key")

        assert result == [{"start": 0.0, "end": 1.5, "text": "Hello there."},
                          {"start": 1.5, "end": 3.0, "text": "Goodbye."}]
        assert captured["headers"] == {"Authorization": "Bearer fake-groq-key"}
        assert captured["data"]["model"] == whisper_transcribe.GROQ_DEFAULT_MODEL
        assert captured["data"]["language"] == "en"
        assert captured["timeout"] is not None  # never an unbounded hang

    def test_looped_output_is_filtered_like_the_local_path(self, tmp_path, monkeypatch):
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        looped = [{"start": float(i), "end": i + 1.0, "text": "Thank you."} for i in range(5)]
        looped.append({"start": 10.0, "end": 12.0, "text": "See you tomorrow."})
        monkeypatch.setattr("lib.http.pinned_get",
                            lambda *a, **k: self._FakeResponse(segments=looped))

        result = whisper_transcribe.transcribe_with_groq(str(audio), "en", "fake-groq-key")

        assert result == [{"start": 0.0, "end": 1.0, "text": "Thank you."},
                          {"start": 10.0, "end": 12.0, "text": "See you tomorrow."}]

    def test_non_finite_segment_times_are_dropped(self, monkeypatch, tmp_path):
        """json.loads accepts NaN and Infinity; a segment timed with them
        would crash every subtitle export later."""
        import whisper_transcribe
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        monkeypatch.setattr("lib.http.pinned_get", lambda *a, **k: self._FakeResponse(
            segments=[{"start": float("nan"), "end": 1.0, "text": "Lost."},
                      {"start": 1.0, "end": float("inf"), "text": "Also lost."},
                      {"start": 2.0, "end": 3.0, "text": "Kept."}]))

        result = whisper_transcribe.transcribe_with_groq(str(audio), "en", "fake-key")

        assert result == [{"start": 2.0, "end": 3.0, "text": "Kept."}]

    def test_negative_backwards_and_absurd_times_are_dropped(self, monkeypatch, tmp_path):
        import whisper_transcribe
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        monkeypatch.setattr("lib.http.pinned_get", lambda *a, **k: self._FakeResponse(
            segments=[{"start": -1.0, "end": 1.0, "text": "Before the start."},
                      {"start": 5.0, "end": 4.0, "text": "Backwards."},
                      {"start": 0.0, "end": 1e12, "text": "Years long."},
                      {"start": 2, "end": 3, "text": "Kept."}]))

        result = whisper_transcribe.transcribe_with_groq(str(audio), "en", "fake-key")

        assert result == [{"start": 2.0, "end": 3.0, "text": "Kept."}]

    @pytest.mark.parametrize("segments", [
        None, "x", [{"end": 1.0, "text": "no start"}], [{"start": "0", "end": 1.0, "text": "a"}],
        [{"start": 0.0, "end": 1.0, "text": None}], ["not a segment"],
        [{"start": 0, "end": 10 ** 400, "text": "too big for a float"}]])
    def test_an_unexpected_reply_shape_is_a_groq_error(self, monkeypatch, tmp_path, segments):
        import whisper_transcribe

        class Reply(self._FakeResponse):
            def json(self):
                return {"segments": segments}

        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        monkeypatch.setattr("lib.http.pinned_get", lambda *a, **k: Reply())

        with pytest.raises(whisper_transcribe.GroqTranscriptionError):
            whisper_transcribe.transcribe_with_groq(str(audio), "en", "fake-key")

    def test_blank_segments_are_dropped(self, monkeypatch, tmp_path):
        import whisper_transcribe
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        monkeypatch.setattr("lib.http.pinned_get", lambda *a, **k: self._FakeResponse(
            segments=[{"start": 0.0, "end": 1.0, "text": "   "},
                      {"start": 1.0, "end": 2.0, "text": "Real text."}]))

        result = whisper_transcribe.transcribe_with_groq(str(audio), "en", "fake-key")

        assert result == [{"start": 1.0, "end": 2.0, "text": "Real text."}]

    def test_progress_cb_is_called_once_at_completion(self, monkeypatch, tmp_path):
        import whisper_transcribe
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        monkeypatch.setattr("lib.http.pinned_get", lambda *a, **k: self._FakeResponse(segments=[]))
        seen = []

        whisper_transcribe.transcribe_with_groq(str(audio), "en", "fake-key", progress_cb=seen.append)

        assert seen == [1.0]

    def test_a_non_200_response_raises_with_the_body_and_status(self, monkeypatch, tmp_path):
        import whisper_transcribe
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")
        monkeypatch.setattr("lib.http.pinned_get", lambda *a, **k: self._FakeResponse(
            status_code=400, text="invalid api key"))

        try:
            whisper_transcribe.transcribe_with_groq(str(audio), "en", "bad-key")
            assert False, "expected GroqTranscriptionError"
        except whisper_transcribe.GroqTranscriptionError as exc:
            assert "400" in str(exc) and "invalid api key" in str(exc)

    def test_a_network_error_is_raised_as_groq_transcription_error(self, monkeypatch, tmp_path):
        import whisper_transcribe
        import requests
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")

        def fake_post(*a, **k):
            raise requests.ConnectionError("could not connect")
        monkeypatch.setattr("lib.http.pinned_get", fake_post)

        try:
            whisper_transcribe.transcribe_with_groq(str(audio), "en", "fake-key")
            assert False, "expected GroqTranscriptionError"
        except whisper_transcribe.GroqTranscriptionError as exc:
            assert str(exc) == "Couldn't reach Groq's API."

    def test_a_key_leaked_into_the_error_message_is_redacted(self, monkeypatch, tmp_path):
        """Never put an API key in a shown/stored/logged error -- see
        engine_backends.shared.redact_secrets and this project's own rule
        against reintroducing that class of bug."""
        import whisper_transcribe
        import requests
        audio = tmp_path / "audio.wav"
        audio.write_bytes(b"x")

        def fake_post(*a, **k):
            raise requests.ConnectionError(
                "failed sending header Authorization: Bearer gsk_realsecretkey1234567890")
        monkeypatch.setattr("lib.http.pinned_get", fake_post)

        try:
            whisper_transcribe.transcribe_with_groq(str(audio), "en", "gsk_realsecretkey1234567890")
            assert False, "expected GroqTranscriptionError"
        except whisper_transcribe.GroqTranscriptionError as exc:
            assert "gsk_realsecretkey1234567890" not in str(exc)

    def test_needs_a_timeout_so_a_hung_server_cannot_stick_a_job_at_running_forever(self):
        """Statically enforced too by
        tests/test_static_analysis.py::TestHttpCallsHaveTimeouts::test_core
        -- this test documents why, at the unit level."""
        import inspect
        import whisper_transcribe
        assert "timeout=" in inspect.getsource(whisper_transcribe.transcribe_with_groq)


def test_unmatched_transcript_lines_share_the_gap_and_do_not_push_matched_lines():
    from line_alignment import align_transcript_to_timing

    segments = [{"start": 0.0, "end": 2.0, "text": "你好吗"},
                {"start": 20.0, "end": 22.0, "text": "再见了"}]
    lines = align_transcript_to_timing(["你好吗", "旁白一", "旁白二", "再见了"], segments)
    assert lines[3].start == 20.0
    assert lines[3].end == 22.0
    assert 2.0 <= lines[1].start < lines[1].end <= lines[2].start < lines[2].end <= 20.0


def test_lines_whose_time_was_guessed_are_flagged_and_matched_lines_are_not():
    from line_alignment import GUESSED_TIMING_NOTE, align_transcript_to_timing

    segments = [{"start": 0.0, "end": 2.0, "text": "你好吗"},
                {"start": 20.0, "end": 22.0, "text": "再见了"}]
    lines = align_transcript_to_timing(["你好吗", "旁白一", "旁白二", "再见了"], segments)
    assert [ln.flag for ln in lines] == [None, "timing_uncertain", "timing_uncertain", None]
    assert lines[1].flag_note == GUESSED_TIMING_NOTE
    assert lines[0].flag_note == lines[3].flag_note == ""


def test_sentence_splits_keep_closing_quotes_and_decimals_whole():
    import novel_text

    chunks = novel_text.chunk_novel_text("他说：「走吧。」" + "然后离开了。" * 3, max_chars=10)
    assert all(not c.startswith("」") for c in chunks)
    assert chunks[0] == "他说：「走吧。」"
    assert split_user_transcript("气温是37.5度。他走了。") == ["气温是37.5度。", "他走了。"]
    assert split_user_transcript("“走吧。”他说。") == ["“走吧。”", "他说。"]


def test_segment_texts_join_without_spaces_next_to_cjk_and_with_them_between_words():
    from line_alignment import join_segment_texts

    assert join_segment_texts(["你好，", "世界"]) == "你好，世界"
    assert join_segment_texts(["今日は", " いい天気"]) == "今日はいい天気"
    assert join_segment_texts(["hello", "world", ""]) == "hello world"
    assert join_segment_texts(["안녕하세요", "여러분"]) == "안녕하세요 여러분"
