"""
tests/test_word_align.py -- word_align.py, experimental MMS word-level
forced alignment for splitting oversized/VAD-merged Whisper segments.

torch/torchaudio/uroman aren't installed in this sandbox (no GPU, no
network for the ~1.1GB MMS model download) -- align_words() itself is
faked at that exact boundary in tests that need it, the same way other
tests here fake faster_whisper.WhisperModel or paddleocr.PaddleOCR.
_group_aligned_words_into_lines() and the orchestration/graceful-
degradation logic in realign_long_segment()/realign_oversized_segments()
are pure Python and are exercised for real.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import word_align
import segment as segment_module
import core as core_module


class TestCheckDependencies:
    def test_raises_a_clear_actionable_error_when_missing(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "torch", None)
        monkeypatch.setitem(sys.modules, "torchaudio", None)
        monkeypatch.setitem(sys.modules, "uroman", None)
        with pytest.raises(word_align.WordAlignError, match="pip install torchaudio uroman"):
            word_align._check_dependencies()


class TestGroupAlignedWordsIntoLines:
    def test_empty_input_returns_empty(self):
        assert word_align._group_aligned_words_into_lines([], offset=0.0) == []

    def test_a_single_run_with_no_pauses_stays_one_line(self):
        words = [("a", 0.0, 1.0), ("b", 1.0, 2.0), ("c", 2.0, 3.0)]
        result = word_align._group_aligned_words_into_lines(words, offset=0.0, min_pause_seconds=0.6)
        assert result == [{"start": 0.0, "end": 3.0, "text": "abc"}]

    def test_a_real_pause_splits_into_two_lines(self):
        words = [("a", 0.0, 1.0), ("b", 1.0, 2.0),
                 ("c", 3.0, 4.0), ("d", 4.0, 5.0)]  # 1.0s gap between b and c
        result = word_align._group_aligned_words_into_lines(words, offset=0.0, min_pause_seconds=0.6)
        assert result == [{"start": 0.0, "end": 2.0, "text": "ab"},
                           {"start": 3.0, "end": 5.0, "text": "cd"}]

    def test_a_gap_shorter_than_the_threshold_does_not_split(self):
        words = [("a", 0.0, 1.0), ("b", 1.2, 2.0)]  # 0.2s gap, under the 0.6s threshold
        result = word_align._group_aligned_words_into_lines(words, offset=0.0, min_pause_seconds=0.6)
        assert result == [{"start": 0.0, "end": 2.0, "text": "ab"}]

    def test_offset_shifts_every_line_onto_the_original_timeline(self):
        words = [("a", 0.0, 1.0), ("b", 5.0, 6.0)]
        result = word_align._group_aligned_words_into_lines(words, offset=100.0, min_pause_seconds=0.6)
        assert result == [{"start": 100.0, "end": 101.0, "text": "a"},
                           {"start": 105.0, "end": 106.0, "text": "b"}]

    def test_multiple_splits_across_several_pauses(self):
        words = [("a", 0.0, 1.0), ("b", 2.0, 3.0), ("c", 4.0, 5.0)]
        result = word_align._group_aligned_words_into_lines(words, offset=0.0, min_pause_seconds=0.6)
        assert len(result) == 3
        assert [ln["text"] for ln in result] == ["a", "b", "c"]


class TestAlignWords:
    def _install_fakes(self, monkeypatch, num_frames=100, sample_rate=16000, num_samples=32000):
        import types as t

        class FakeTensor:
            def __init__(self, shape):
                self.shape = shape

            def to(self, device):
                return self

            def __getitem__(self, idx):
                return self

        class FakeSpan:
            def __init__(self, start, end):
                self.start, self.end = start, end

        class FakeModel:
            def to(self, device):
                return self

            def __call__(self, waveform):
                return FakeTensor((1, num_frames, 32)), None

        class FakeAligner:
            def __call__(self, emission, tokens):
                # one span per token, each 1 frame long, sequential
                return [[FakeSpan(i, i + 1)] for i in range(len(tokens))]

        _sr = sample_rate

        class FakeBundle:
            sample_rate = _sr

            def get_model(self):
                return FakeModel()

            def get_tokenizer(self):
                return lambda romanized: list(range(len(romanized)))

            def get_aligner(self):
                return FakeAligner()

        fake_torch = t.ModuleType("torch")
        fake_torch.inference_mode = lambda: _NullContext()

        class _NullContext:
            def __enter__(self):
                return None

            def __exit__(self, *a):
                return False
        fake_torch.inference_mode = lambda: _NullContext()

        fake_torchaudio = t.ModuleType("torchaudio")
        fake_torchaudio.load = lambda path: (FakeTensor((1, num_samples)), sample_rate)
        fake_torchaudio.pipelines = t.SimpleNamespace(MMS_FA=FakeBundle())
        fake_torchaudio.functional = t.SimpleNamespace(
            resample=lambda waveform, orig_sr, new_sr: waveform)

        fake_uroman = t.ModuleType("uroman")

        class FakeUroman:
            def romanize_string(self, text):
                return text  # identity -- not testing real romanization here
        fake_uroman.Uroman = FakeUroman

        monkeypatch.setitem(sys.modules, "torch", fake_torch)
        monkeypatch.setitem(sys.modules, "torchaudio", fake_torchaudio)
        monkeypatch.setitem(sys.modules, "uroman", fake_uroman)

    def test_returns_one_entry_per_word_in_order(self, monkeypatch):
        self._install_fakes(monkeypatch, num_frames=100, sample_rate=16000, num_samples=16000)
        result = word_align.align_words("/fake/audio.wav", ["hello", "world"])
        assert [w for w, _, _ in result] == ["hello", "world"]

    def test_frame_times_are_converted_to_seconds(self, monkeypatch):
        # num_samples / num_frames / sample_rate = ratio; with 16000 samples,
        # 100 frames, 16000Hz: ratio = 16000/100/16000 = 0.01 sec/frame.
        self._install_fakes(monkeypatch, num_frames=100, sample_rate=16000, num_samples=16000)
        result = word_align.align_words("/fake/audio.wav", ["a", "b"])
        # token 0 -> span(0,1) -> start=0*0.01=0.0, end=1*0.01=0.01
        # token 1 -> span(1,2) -> start=1*0.01=0.01, end=2*0.01=0.02
        assert result[0] == ("a", 0.0, pytest.approx(0.01))
        assert result[1] == ("b", pytest.approx(0.01), pytest.approx(0.02))


class TestRealignLongSegment:
    def test_falls_back_unchanged_when_fewer_than_two_words(self, monkeypatch):
        monkeypatch.setattr(segment_module, "segment_and_annotate",
                             lambda text, language, chinese_script="simplified": [("solo", None)])
        segment = {"start": 0.0, "end": 20.0, "text": "solo"}
        result = word_align.realign_long_segment("/fake/audio.wav", segment, "zh")
        assert result == [segment]

    def test_successful_alignment_returns_regrouped_lines(self, monkeypatch, tmp_path):
        monkeypatch.setattr(segment_module, "segment_and_annotate",
                             lambda text, language, chinese_script="simplified":
                             [("你好", None), ("世界", None)])
        monkeypatch.setattr(core_module, "extract_audio_slice",
                             lambda audio_path, start, end, out_path: open(out_path, "wb").close())
        monkeypatch.setattr(word_align, "align_words",
                             lambda slice_path, words, device="cpu":
                             [("你好", 0.0, 1.0), ("世界", 3.0, 4.0)])  # 2.0s gap -> splits

        segment = {"start": 100.0, "end": 130.0, "text": "你好世界"}
        result = word_align.realign_long_segment("/fake/audio.wav", segment, "zh",
                                                   min_pause_seconds=0.6)

        assert result == [{"start": 100.0, "end": 101.0, "text": "你好"},
                           {"start": 103.0, "end": 104.0, "text": "世界"}]

    def test_an_alignment_exception_falls_back_to_the_original_segment_unchanged(self, monkeypatch):
        """Regression coverage for the documented real failure mode: a CTC
        aligner can fail outright on some CJK text. One line's failure
        must never lose that line or propagate to the caller."""
        monkeypatch.setattr(segment_module, "segment_and_annotate",
                             lambda text, language, chinese_script="simplified":
                             [("你好", None), ("世界", None)])
        monkeypatch.setattr(core_module, "extract_audio_slice",
                             lambda audio_path, start, end, out_path: open(out_path, "wb").close())

        def failing_align(slice_path, words, device="cpu"):
            raise RuntimeError("no characters in this segment found in model dictionary")
        monkeypatch.setattr(word_align, "align_words", failing_align)

        segment = {"start": 100.0, "end": 130.0, "text": "你好世界"}
        result = word_align.realign_long_segment("/fake/audio.wav", segment, "zh")
        assert result == [segment]

    def test_slice_file_is_cleaned_up_even_on_failure(self, monkeypatch):
        monkeypatch.setattr(segment_module, "segment_and_annotate",
                             lambda text, language, chinese_script="simplified":
                             [("你好", None), ("世界", None)])
        created_paths = []

        def fake_extract(audio_path, start, end, out_path):
            created_paths.append(out_path)
            open(out_path, "wb").close()
        monkeypatch.setattr(core_module, "extract_audio_slice", fake_extract)

        def failing_align(slice_path, words, device="cpu"):
            raise RuntimeError("boom")
        monkeypatch.setattr(word_align, "align_words", failing_align)

        segment = {"start": 0.0, "end": 20.0, "text": "你好世界"}
        word_align.realign_long_segment("/fake/audio.wav", segment, "zh")

        assert len(created_paths) == 1
        assert not os.path.exists(created_paths[0])  # cleaned up despite the failure


class TestRealignOversizedSegments:
    def test_raises_immediately_if_dependencies_are_missing(self, monkeypatch):
        monkeypatch.setattr(word_align, "_check_dependencies",
                             lambda: (_ for _ in ()).throw(
                                 word_align.WordAlignError("pip install torchaudio uroman")))
        with pytest.raises(word_align.WordAlignError):
            word_align.realign_oversized_segments(
                [{"start": 0.0, "end": 20.0, "text": "x"}], "/fake/audio.wav", "zh")

    def test_short_segments_are_left_unchanged(self, monkeypatch):
        monkeypatch.setattr(word_align, "_check_dependencies", lambda: None)
        called = []
        monkeypatch.setattr(word_align, "realign_long_segment",
                             lambda *a, **k: called.append(1))

        segments = [{"start": 0.0, "end": 3.0, "text": "short"}]
        result = word_align.realign_oversized_segments(
            segments, "/fake/audio.wav", "zh", min_duration_to_realign=12.0)

        assert result == segments
        assert called == []

    def test_long_segments_are_realigned(self, monkeypatch):
        monkeypatch.setattr(word_align, "_check_dependencies", lambda: None)
        monkeypatch.setattr(word_align, "realign_long_segment",
                             lambda audio_path, seg, language, **k:
                             [{"start": seg["start"], "end": seg["start"] + 1, "text": "split1"},
                              {"start": seg["start"] + 2, "end": seg["end"], "text": "split2"}])

        segments = [{"start": 0.0, "end": 20.0, "text": "long merged segment"}]
        result = word_align.realign_oversized_segments(
            segments, "/fake/audio.wav", "zh", min_duration_to_realign=12.0)

        assert len(result) == 2
        assert result[0]["text"] == "split1"
        assert result[1]["text"] == "split2"

    def test_blank_segments_are_left_unchanged_even_if_long(self, monkeypatch):
        monkeypatch.setattr(word_align, "_check_dependencies", lambda: None)
        called = []
        monkeypatch.setattr(word_align, "realign_long_segment",
                             lambda *a, **k: called.append(1))

        segments = [{"start": 0.0, "end": 20.0, "text": "   "}]
        result = word_align.realign_oversized_segments(segments, "/fake/audio.wav", "zh")

        assert result == segments
        assert called == []
