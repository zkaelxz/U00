"""Indonesian and Thai as source languages: Whisper-only languages, Indonesian heard without
the speech detector, Thai lines split by letters, and the text-only Thonburian model behind
the faster-whisper interface. Fakes only: no network, no weights, no GPU."""

import re
import types

import numpy as np
import pytest

import memory_headroom
import mixed_language
import segment
import segment_splitting
import text_only_whisper
import whisper_models as wm
import whisper_transcribe
from engine_backends import translation_guard
from services import asr_options_service, transcribe_options_service, transcribe_pipeline

KEY = wm.THONBURIAN_KEY
THAI = "สวัสดีครับ วันนี้เราจะมาเล่นเกมกัน ที่ทุกคนรอคอยมานานแล้ว และก็มีอะไรสนุกๆ อีกเยอะเลย ไปดูกันเลยครับ"


class TestLanguageRules:
    def test_qwen3_backends_are_refused_for_both(self):
        for lang in ("id", "th"):
            assert asr_options_service.asr_backend_language_problem("whisper", lang) is None
            for backend in ("qwen3_asr", "qwen3_asr_vad", "qwen3_asr_long", "anime_whisper"):
                assert asr_options_service.asr_backend_language_problem(backend, lang)
        assert asr_options_service.asr_backend_language_problem("qwen3_asr", "ja") is None

    def test_a_saved_qwen3_backend_reads_as_whisper(self):
        assert asr_options_service.stored_asr_backend(
            {"source_language": "th", "asr_backend_choice": "qwen3_asr_vad"}) == "whisper"

    def test_word_segmenters_split_on_spaces_not_jieba(self):
        assert segment.segment_words("Halo semua apa kabar", "id") == ["Halo", " ", "semua", " ", "apa", " ", "kabar"]
        assert [w for w, _ in segment.segment_and_annotate("สวัสดีครับ วันนี้", "th")] == ["สวัสดีครับ", " ", "วันนี้"]

    def test_mixed_language_detection_offers_them_only_on_their_own_titles(self):
        probs = [("th", 0.6), ("zh", 0.3)]
        assert mixed_language.pick_allowed_language(probs, "zh") == "zh"
        assert mixed_language.pick_allowed_language(probs, "th") == "th"
        assert mixed_language.pick_allowed_language([("id", 0.9)], None) is None
        assert mixed_language.text_matches_language("สวัสดี OK", "th")
        assert mixed_language.text_matches_language("Halo semua", "id")
        assert not mixed_language.text_matches_language("สวัสดีครับ", "id")


class TestIndonesianHeardWithoutTheDetector:
    @pytest.mark.parametrize("language,difficult,fast,vad", [
        ("id", False, False, False), ("zh", False, False, True), ("zh", True, False, False),
        ("id", False, True, True)])
    def test_vad_filter(self, monkeypatch, tmp_path, language, difficult, fast, vad):
        seen = {}

        class Model:
            def transcribe(self, audio, **kwargs):
                seen.update(kwargs)
                return iter(()), types.SimpleNamespace(duration=1.0)

        monkeypatch.setattr(wm, "load_whisper_model", lambda *a, **k: Model())
        monkeypatch.setattr(whisper_transcribe, "_fit_to_speech", lambda r, p, trim_starts=True, **_: seen.update(trim=trim_starts))
        monkeypatch.setattr(whisper_transcribe, "_decode_whole", lambda path: None)
        if fast:
            # A stand-in module: other tests leave their own faster_whisper stub in sys.modules.
            import sys
            monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(
                BatchedInferencePipeline=lambda model: types.SimpleNamespace(transcribe=model.transcribe)))
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"")
        whisper_transcribe.transcribe_for_timing(str(audio), "large-v3", language=language,
                                                 difficult_audio=difficult, fast_mode=fast)
        assert seen["vad_filter"] is vad
        assert seen["trim"] is (language != "id" and not difficult)


class TestThaiSplitting:
    def test_a_long_thai_line_is_cut_at_its_spaces_by_letter_count(self):
        pieces = segment_splitting.split_long_segments([{"start": 0.0, "end": 10.0, "text": THAI}])
        assert len(pieces) > 1
        assert " ".join(p["text"] for p in pieces) == THAI
        for p in pieces:
            assert len(segment_splitting._CJK_RE.findall(p["text"])) <= segment_splitting.SPLIT_MAX_CJK_CHARS

    def test_vowel_and_tone_marks_do_not_count_as_letters(self):
        assert len(segment_splitting._CJK_RE.findall("ที่")) == 1

    def test_a_word_cut_never_leaves_a_mark_at_the_start_of_a_line(self):
        text = "ที่ทุกคน"
        # Whisper's Thai "words" are byte-valid tokens; here one begins with the tone mark.
        words = [{"word": "ที", "start": 0.0, "end": 0.4}, {"word": "่", "start": 0.4, "end": 0.5},
                 {"word": "ทุกคน", "start": 2.0, "end": 2.6}]
        index = segment_splitting._WordIndex.build(text, words)
        assert index.cs == [0, 3] and index.ce == [3, 8]
        assert index.kept[0]["word"] == "ที่"

    def test_live_forced_cut_keeps_marks_with_their_letter(self):
        from live import live_translate
        text = THAI.replace(" ", "") * 2
        for piece in live_translate.force_cut_cjk({"start": 0.0, "end": 20.0, "text": text}):
            assert not re.match(r"[ัิ-ฺ็-๎]", piece["text"])


class TestThaiTranslationChecks:
    def test_thai_left_in_the_english_is_a_leak(self):
        assert translation_guard.unusable_reply("สวัสดีครับ", "สวัสดี hello") == translation_guard.LEFTOVER_SOURCE

    def test_a_normal_english_line_for_thai_is_not_too_long(self):
        source = "สวัสดีครับ วันนี้เราจะมาเล่นเกมกัน"
        assert translation_guard.unusable_reply(source, "Hello everyone, today we are going to play a game.") is None
        assert translation_guard.length_issue(source, "Hello everyone, today we are going to play a game.") is None
        assert translation_guard.length_issue(source, "Hi.") == "short"


class TestThonburianRegistry:
    def test_pinned_and_registered(self):
        repo, revision = wm.TEXT_ONLY_REPOS[KEY]
        assert repo == "biodatlab/whisper-th-large-v3-combined"
        assert re.fullmatch(r"[0-9a-f]{40}", revision)
        assert KEY in wm.WHISPER_MODELS and KEY in wm.LIVE_WHISPER_SIZES
        assert KEY in transcribe_options_service._allowed_whisper_sizes()
        assert transcribe_pipeline._MODEL_DOWNLOAD_SIZES[KEY] == "~3.1 GB"
        assert memory_headroom.estimate_mb("whisper", KEY)
        assert not wm.supports_word_timestamps(KEY)

    def test_thai_only(self):
        assert wm.whisper_model_fits_language(KEY, "th")
        for lang in ("zh", "ja", "ko", "id"):
            assert not wm.whisper_model_fits_language(KEY, lang)

    @pytest.mark.parametrize("language,cached,expected", [
        ("th", True, KEY), ("th", False, "large-v3"), ("id", True, "large-v3")])
    def test_default_only_once_downloaded(self, monkeypatch, language, cached, expected):
        monkeypatch.setattr(wm, "is_whisper_model_cached", lambda size: cached and size == KEY)
        assert wm.default_whisper_size_for(language) == expected

    def test_only_inert_files_are_fetched(self, monkeypatch):
        huggingface_hub = pytest.importorskip("huggingface_hub")
        seen = {}
        monkeypatch.setattr(huggingface_hub, "snapshot_download",
                            lambda repo, **kw: seen.update(repo=repo, **kw) or "/folder")
        assert text_only_whisper.fetch("r/x", "abc") == "/folder"
        assert "training_args.bin" not in seen["allow_patterns"]
        assert all(not p.endswith((".bin", ".py", ".pt", ".pkl")) for p in seen["allow_patterns"])
        assert seen["revision"] == "abc"

    def test_snapshot_needs_every_file(self, tmp_path):
        folder = tmp_path / "models--r--x" / "snapshots" / "abc"
        folder.mkdir(parents=True)
        for name in text_only_whisper.REQUIRED_FILES[:-1]:
            (folder / name).write_text("{}")
        assert text_only_whisper.snapshot_folder(str(tmp_path), "r/x", "abc") is None
        (folder / text_only_whisper.REQUIRED_FILES[-1]).write_text("{}")
        assert text_only_whisper.snapshot_folder(str(tmp_path), "r/x", "abc") == str(folder)


class _FakeTextOnly(text_only_whisper.TextOnlyWhisper):
    def __init__(self):
        super().__init__(None, None, "cpu", None, "th")
        self.batches = []

    def _decode(self, waves):
        self.batches.append(len(waves))
        return [f"ข้อความ{len(self.batches)}-{i}" if len(w) else "" for i, w in enumerate(waves)]


class TestTextOnlyModel:
    def test_lines_take_the_speech_spans_times(self, monkeypatch):
        import vad_segments
        sr = text_only_whisper.SAMPLE_RATE
        audio = np.zeros(sr * 40, dtype="float32")
        monkeypatch.setattr(vad_segments, "speech_spans", lambda a, r, **kw: [
            vad_segments.Span(1.0, 3.0), vad_segments.Span(5.0, 25.0), vad_segments.Span(30.0, 32.0)])
        model = _FakeTextOnly()
        segments, info = model.transcribe(audio, language="th", vad_filter=True,
                                          vad_parameters={"threshold": 0.5, "min_silence_duration_ms": 2000})
        segs = list(segments)
        assert info.duration == 40.0
        # The 20 s span is capped at 15 s, so four spans in one batch.
        assert [round(s.start, 1) for s in segs][:2] == [1.0, 5.0]
        assert all(s.end - s.start <= text_only_whisper.SPAN_CAP_S for s in segs)
        assert len(segs) == 4 and model.batches == [4]
        assert all(s.words is None for s in segs)

    def test_without_the_detector_it_hears_fixed_windows(self):
        audio = np.zeros(text_only_whisper.SAMPLE_RATE * 65, dtype="float32")
        segs = list(_FakeTextOnly().transcribe(audio, vad_filter=False)[0])
        assert [(s.start, s.end) for s in segs] == [(0.0, 30.0), (30.0, 60.0), (60.0, 65.0)]

    def test_clip_timestamps_hear_only_those_stretches(self):
        audio = np.zeros(text_only_whisper.SAMPLE_RATE * 60, dtype="float32")
        segs = list(_FakeTextOnly().transcribe(audio, clip_timestamps=[10.0, 12.0, 40.0, 44.0])[0])
        assert [(s.start, s.end) for s in segs] == [(10.0, 12.0), (40.0, 44.0)]

    def test_language_detection_reports_its_own_language(self):
        assert _FakeTextOnly().detect_language(np.zeros(10)) == ("th", 1.0, [("th", 1.0)])

    def test_transcribe_for_timing_runs_it_like_any_size(self, monkeypatch, tmp_path):
        model = _FakeTextOnly()
        monkeypatch.setattr(wm, "load_whisper_model", lambda *a, **k: model)
        monkeypatch.setattr(text_only_whisper, "_audio_array",
                            lambda audio: np.zeros(text_only_whisper.SAMPLE_RATE * 20, dtype="float32"))
        monkeypatch.setattr(whisper_transcribe, "_fit_to_speech", lambda r, p, trim_starts=True, **_: r)
        monkeypatch.setattr(whisper_transcribe, "_decode_whole", lambda path: None)
        import vad_segments
        monkeypatch.setattr(vad_segments, "speech_spans", lambda a, r, **kw: [vad_segments.Span(2.0, 6.0)])
        audio = tmp_path / "a.wav"
        audio.write_bytes(b"")
        segs = whisper_transcribe.transcribe_for_timing(str(audio), KEY, language="th")
        assert [(s["start"], s["end"]) for s in segs] == [(2.0, 6.0)]
        assert all("words" not in s for s in segs)


class TestLoadingThonburian:
    def test_load_whisper_model_hands_the_download_to_the_text_only_loader(self, monkeypatch):
        import sys
        monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=None))
        monkeypatch.setattr(memory_headroom, "before_load", lambda *a, **k: None)
        monkeypatch.setattr(wm, "fetch_text_only_model", lambda size, token: f"/hub/{size}")
        seen = {}

        def fake_load(folder, use_gpu, language):
            seen.update(folder=folder, use_gpu=use_gpu, language=language)
            return "model", {"device": "cuda", "compute_type": "float16", "gpu_error": None}

        monkeypatch.setattr(text_only_whisper, "load", fake_load)
        monkeypatch.setattr(wm, "_whisper_model_cache", {})
        monkeypatch.setattr(wm, "_whisper_device_info", {})
        assert wm.load_whisper_model(KEY, use_gpu=True, local_model_path=None) == "model"
        assert seen == {"folder": f"/hub/{KEY}", "use_gpu": True, "language": "th"}
        assert wm.get_whisper_device_info(KEY, use_gpu=True)["compute_type"] == "float16"
