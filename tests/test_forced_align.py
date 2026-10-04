"""
tests/test_forced_align.py -- forced_align.py's pure logic (bucketing,
chunk-to-line reconstruction, model-loading fallback/error classification),
exercised against fake qwen_asr/torch modules and a monkeypatched audio
slicer, since the real model needs a GPU/network this sandbox doesn't have.
"""
import os
import sys
import types
import importlib

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core import Line


class FakeUnit:
    def __init__(self, text, start_time, end_time):
        self.text = text
        self.start_time = start_time
        self.end_time = end_time


class TestLanguageSupport:
    def test_language_names_are_capitalized_full_words(self):
        # Qwen3-ForcedAligner's API takes "Chinese"/"Japanese"/"Korean",
        # not the zh/ja/ko codes used everywhere else in this project.
        import forced_align as fa
        assert fa.LANGUAGE_NAMES == {"zh": "Chinese", "ja": "Japanese", "ko": "Korean"}


class TestBucketIntoChunks:
    def test_short_lines_form_a_single_chunk(self):
        import forced_align as fa
        lines = [Line(idx=i, start=i * 2.0, end=i * 2.0 + 1.5, zh="x") for i in range(5)]
        chunks = fa._bucket_into_chunks(lines, max_chunk_seconds=280.0)
        assert len(chunks) == 1
        assert chunks[0] == lines

    def test_splits_when_span_exceeds_max_chunk_seconds(self):
        import forced_align as fa
        # Three lines, 200s apart each -- first pair already exceeds a
        # 280s cap once the third is considered.
        lines = [
            Line(idx=0, start=0.0, end=10.0, zh="a"),
            Line(idx=1, start=200.0, end=210.0, zh="b"),
            Line(idx=2, start=400.0, end=410.0, zh="c"),
        ]
        chunks = fa._bucket_into_chunks(lines, max_chunk_seconds=280.0)
        assert len(chunks) == 2
        assert [ln.idx for ln in chunks[0]] == [0, 1]
        assert [ln.idx for ln in chunks[1]] == [2]

    def test_every_line_ends_up_in_exactly_one_chunk(self):
        import forced_align as fa
        lines = [Line(idx=i, start=i * 50.0, end=i * 50.0 + 5.0, zh="x") for i in range(10)]
        chunks = fa._bucket_into_chunks(lines, max_chunk_seconds=280.0)
        seen = [ln.idx for chunk in chunks for ln in chunk]
        assert seen == list(range(10))

    def test_single_oversized_line_raises_with_its_index(self):
        import forced_align as fa
        lines = [Line(idx=0, start=0.0, end=10.0, zh="a"),
                 Line(idx=1, start=20.0, end=350.0, zh="b")]
        with pytest.raises(ValueError, match=r"Line 1 spans"):
            fa._bucket_into_chunks(lines)

    def test_empty_input(self):
        import forced_align as fa
        assert fa._bucket_into_chunks([]) == []


class TestAlignChunk:
    def _stub_audio_slicing(self, monkeypatch, fa):
        # _align_chunk calls _extract_audio_slice (real ffmpeg) whenever
        # there's non-empty text to align -- these tests care about the
        # unit-to-line reconstruction logic, not real ffmpeg, so this just
        # needs to not touch a real audio file.
        monkeypatch.setattr(
            fa, "_extract_audio_slice",
            lambda audio_path, start, end, out_path: open(out_path, "wb").close(),
        )

    def test_char_level_units_map_one_to_one(self, monkeypatch, tmp_path):
        import forced_align as fa
        self._stub_audio_slicing(monkeypatch, fa)
        lines = [Line(idx=0, start=10.0, end=12.0, zh="AB"),
                 Line(idx=1, start=12.0, end=14.0, zh="CD")]

        class FakeModel:
            def align(self, audio, text, language):
                assert text == "ABCD"
                return [[FakeUnit(c, i * 0.5, i * 0.5 + 0.5) for i, c in enumerate(text)]]

        result = fa._align_chunk(FakeModel(), "/fake/audio.wav", lines, "Chinese", str(tmp_path))
        # Line 0 = chars A,B -> units at [0,0.5) and [0.5,1.0), offset by chunk_start=10.0
        assert result[0] == [10.0, 10.5, 10.5, 11.0]
        # Line 1 = chars C,D -> units at [1.0,1.5) and [1.5,2.0)
        assert result[1] == [11.0, 11.5, 11.5, 12.0]

    def test_word_level_units_spanning_multiple_chars(self, monkeypatch, tmp_path):
        import forced_align as fa
        self._stub_audio_slicing(monkeypatch, fa)
        lines = [Line(idx=0, start=0.0, end=5.0, zh="hello")]

        class FakeModel:
            def align(self, audio, text, language):
                return [[FakeUnit("hello", 1.0, 2.0)]]

        result = fa._align_chunk(FakeModel(), "/fake/audio.wav", lines, "English", str(tmp_path))
        # A multi-char unit contributes its [start,end] once per character
        # position it spans (harmless duplication -- only min/max ever get
        # used downstream), so check the resulting bounds, not exact list contents.
        assert min(result[0]) == 1.0
        assert max(result[0]) == 2.0

    def test_empty_line_text_skips_the_model_call(self, monkeypatch, tmp_path):
        import forced_align as fa
        self._stub_audio_slicing(monkeypatch, fa)
        lines = [Line(idx=0, start=0.0, end=1.0, zh="   ")]

        class ExplodingModel:
            def align(self, *a, **k):
                raise AssertionError("should not be called for empty text")

        result = fa._align_chunk(ExplodingModel(), "/fake/audio.wav", lines, "Chinese", str(tmp_path))
        assert result == {}

    def test_extra_or_missing_units_do_not_crash(self, monkeypatch, tmp_path):
        import forced_align as fa
        self._stub_audio_slicing(monkeypatch, fa)
        lines = [Line(idx=0, start=0.0, end=1.0, zh="AB")]

        class ShortModel:
            def align(self, audio, text, language):
                return [[FakeUnit("ABCDEF", 0.0, 1.0)]]  # longer than input text

        result = fa._align_chunk(ShortModel(), "/fake/audio.wav", lines, "Chinese", str(tmp_path))
        assert min(result[0]) == 0.0
        assert max(result[0]) == 1.0


class TestRepairZeroDurationSpans:
    def _chunk(self, monkeypatch, tmp_path, units, text):
        import forced_align as fa
        monkeypatch.setattr(fa, "_extract_audio_slice",
                            lambda a, s, e, out: open(out, "wb").close())

        class Model:
            def align(self, audio, text, language):
                return [[FakeUnit(c, s, e) for c, (s, e) in zip(text, units)]]
        lines = [Line(idx=0, start=10.0, end=14.0, zh=text)]
        return fa._align_chunk(Model(), "/a.wav", lines, "Chinese", str(tmp_path))[0]

    def _spans(self, times):
        return list(zip(times[0::2], times[1::2]))

    def _assert_good(self, spans):
        assert all(e > s for s, e in spans)
        assert all(b[0] >= a[1] - 1e-9 for a, b in zip(spans, spans[1:]))
        assert spans[0][0] >= 10.0 and spans[-1][1] <= 14.0

    def test_zero_span_at_start(self, monkeypatch, tmp_path):
        spans = self._spans(self._chunk(monkeypatch, tmp_path, [(0, 0), (1, 2), (2, 4)], "ABC"))
        self._assert_good(spans)
        assert spans[0] == (10.0, 11.0)

    def test_zero_span_in_middle(self, monkeypatch, tmp_path):
        spans = self._spans(self._chunk(monkeypatch, tmp_path, [(0, 1), (2, 2), (3, 4)], "ABC"))
        self._assert_good(spans)
        assert spans[1] == (11.0, 13.0)

    def test_zero_span_at_end(self, monkeypatch, tmp_path):
        spans = self._spans(self._chunk(monkeypatch, tmp_path, [(0, 1), (1, 3), (4, 4)], "ABC"))
        self._assert_good(spans)
        assert spans[2] == (13.0, 14.0)

    def test_all_zero_run_is_spread_over_the_chunk(self, monkeypatch, tmp_path):
        spans = self._spans(self._chunk(monkeypatch, tmp_path, [(0, 0)] * 4, "ABCD"))
        self._assert_good(spans)
        assert spans == [(10.0, 11.0), (11.0, 12.0), (12.0, 13.0), (13.0, 14.0)]

    def test_out_of_order_and_out_of_bounds_are_made_monotonic(self):
        import forced_align as fa
        out = fa._repair_unit_spans([(0, 2), (1, 1.5), (3, 9)], 0.0, 4.0)
        assert out[0] == (0, 2)
        assert all(b[0] >= a[1] for a, b in zip(out, out[1:]))
        assert all(0.0 <= s <= e <= 4.0 for s, e in out)

    def test_no_room_leaves_zero_so_fallback_still_triggers(self):
        import forced_align as fa
        assert fa._repair_unit_spans([(0, 0)], 0.0, 0.0) == [(0, 0)]


class TestAlignWithQwen3:
    def _fake_model_returning(self, per_chunk_text_to_units):
        class FakeModel:
            def align(self, audio, text, language):
                return [per_chunk_text_to_units[text]]
        return FakeModel()

    def test_rejects_unsupported_language(self):
        import forced_align as fa
        with pytest.raises(ValueError, match="doesn't cover language"):
            fa.align_with_qwen3("/fake.wav", ["hi"], [{"start": 0, "end": 1, "text": "hi"}],
                                 language="en")

    def test_rejects_missing_whisper_segments(self):
        import forced_align as fa
        with pytest.raises(ValueError, match="needs whisper_segments"):
            fa.align_with_qwen3("/fake.wav", ["你好"], [], language="zh")

    def test_end_to_end_with_fake_model(self, monkeypatch, tmp_path):
        import forced_align as fa

        user_lines = ["你好", "再见"]
        whisper_segments = [{"start": 0.0, "end": 4.0, "text": "你好再见"}]

        monkeypatch.setattr(
            fa, "_extract_audio_slice",
            lambda audio_path, start, end, out_path: open(out_path, "wb").close(),
        )
        monkeypatch.setattr(
            fa, "load_qwen3_aligner",
            lambda use_gpu=False: self._fake_model_returning({
                "你好再见": [FakeUnit(c, i * 1.0, i * 1.0 + 1.0)
                             for i, c in enumerate("你好再见")],
            }),
        )

        result = fa.align_with_qwen3("/fake.wav", user_lines, whisper_segments, language="zh")

        assert [ln.zh for ln in result] == user_lines
        assert result[0].start == 0.0
        assert result[0].end == 2.0
        assert result[1].start == 2.0
        assert result[1].end == 4.0


class TestLoadQwen3Aligner:
    def _install_fake_qwen_asr(self, from_pretrained):
        fake_torch = types.ModuleType("torch")
        fake_torch.bfloat16 = "bfloat16"
        sys.modules["torch"] = fake_torch

        fake_module = types.ModuleType("qwen_asr")

        class FakeAligner:
            pass
        FakeAligner.from_pretrained = staticmethod(from_pretrained)
        fake_module.Qwen3ForcedAligner = FakeAligner
        sys.modules["qwen_asr"] = fake_module

    def teardown_method(self):
        sys.modules.pop("torch", None)
        sys.modules.pop("qwen_asr", None)
        import forced_align
        forced_align._aligner_model_cache.clear()

    def test_loads_and_caches_on_cpu(self):
        calls = []

        def from_pretrained(model_id, dtype, device_map):
            calls.append(device_map)
            return object()

        self._install_fake_qwen_asr(from_pretrained)
        import forced_align
        importlib.reload(forced_align)
        forced_align._aligner_model_cache.clear()

        m1 = forced_align.load_qwen3_aligner(use_gpu=False)
        m2 = forced_align.load_qwen3_aligner(use_gpu=False)
        assert m1 is m2
        assert calls == ["cpu"]  # second call served from cache, no reload

    def test_gpu_error_falls_back_to_cpu(self):
        calls = []

        def from_pretrained(model_id, dtype, device_map):
            calls.append(device_map)
            if device_map == "cuda:0":
                raise RuntimeError("CUDA out of memory")
            return object()

        self._install_fake_qwen_asr(from_pretrained)
        import forced_align
        importlib.reload(forced_align)
        forced_align._aligner_model_cache.clear()

        model = forced_align.load_qwen3_aligner(use_gpu=True)
        assert model is not None
        assert calls == ["cuda:0", "cpu"]

    def test_network_error_raises_model_download_error(self):
        def from_pretrained(model_id, dtype, device_map):
            raise OSError("Connection timed out while downloading from huggingface.co")

        self._install_fake_qwen_asr(from_pretrained)
        import forced_align
        importlib.reload(forced_align)
        forced_align._aligner_model_cache.clear()

        from core import ModelDownloadError
        with pytest.raises(ModelDownloadError):
            forced_align.load_qwen3_aligner(use_gpu=False)

    def test_unrelated_error_propagates(self):
        def from_pretrained(model_id, dtype, device_map):
            raise ValueError("some genuinely unrelated bug")

        self._install_fake_qwen_asr(from_pretrained)
        import forced_align
        importlib.reload(forced_align)
        forced_align._aligner_model_cache.clear()

        with pytest.raises(ValueError, match="genuinely unrelated"):
            forced_align.load_qwen3_aligner(use_gpu=False)
