"""
tests/test_transcription_quality.py -- Step 6: Whisper anti-loop settings,
large-v3-turbo + batched fast mode, ForcedAligner reliability, vocal
separation backends, and SenseVoice's audio emotion/event tags.
"""
import os
import sys
import types

import pytest

import core


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


@pytest.fixture
def fake_whisper(monkeypatch):
    """A fake faster_whisper: records which entry point ran and its kwargs."""
    calls = []

    class FakeModel:
        def transcribe(self, audio_path, **kwargs):
            calls.append(("model", kwargs))
            return iter([_Seg(0.0, 1.0, "你好")]), None

    class FakeBatched:
        def __init__(self, model):
            self.model = model

        def transcribe(self, audio_path, **kwargs):
            calls.append(("batched", kwargs))
            return iter([_Seg(0.0, 1.0, "你好")]), None

    fw = types.ModuleType("faster_whisper")
    fw.WhisperModel = lambda *a, **k: FakeModel()
    fw.BatchedInferencePipeline = FakeBatched
    monkeypatch.setitem(sys.modules, "faster_whisper", fw)
    monkeypatch.setattr(core, "_whisper_model_cache", {})
    return calls


class TestWhisperSettings:
    def test_anti_loop_kwargs_are_sent(self, fake_whisper):
        core.transcribe_for_timing("a.wav")
        [(entry, kwargs)] = fake_whisper
        assert entry == "model"
        assert kwargs["condition_on_previous_text"] is False
        assert kwargs["no_repeat_ngram_size"] == 3
        assert kwargs["repetition_penalty"] == pytest.approx(1.1)

    def test_the_hallucination_filter_is_still_the_backstop(self, monkeypatch, fake_whisper):
        seen = []
        real = core.filter_hallucinated_segments
        monkeypatch.setattr(core, "filter_hallucinated_segments",
                            lambda segs, n: seen.append(n) or real(segs, n))
        core.transcribe_for_timing("a.wav")
        assert seen == [4]

    def test_fast_mode_uses_the_batched_pipeline_with_the_same_settings(self, fake_whisper):
        core.transcribe_for_timing("a.wav", fast_mode=True)
        [(entry, kwargs)] = fake_whisper
        assert entry == "batched"
        assert kwargs["no_repeat_ngram_size"] == 3 and kwargs["vad_filter"] is True

    def test_large_v3_turbo_is_offered_without_a_japanese_korean_weakness_claim(self):
        assert "large-v3-turbo" in core.WHISPER_MODELS
        assert "weaker" not in core.WHISPER_MODELS["large-v3-turbo"]


class _Unit:
    def __init__(self, text, start_time, end_time):
        self.text, self.start_time, self.end_time = text, start_time, end_time


class TestForcedAlignerReliability:
    def test_chunks_default_to_about_a_minute(self):
        import forced_align as fa
        from core import Line
        assert fa.MAX_CHUNK_SECONDS == 60.0
        lines = [Line(idx=i, start=i * 20.0, end=i * 20.0 + 15, zh="字") for i in range(9)]
        chunks = fa._bucket_into_chunks(lines)
        assert all(c[-1].end - c[0].start <= 60.0 for c in chunks)
        assert sum(len(c) for c in chunks) == 9

    def test_detects_zero_duration_and_backwards_units(self):
        import forced_align as fa
        times = {0: [0.0, 1.0, 1.0, 2.0],      # fine
                 1: [2.0, 2.0],                # zero duration
                 2: [5.0, 6.0, 4.0, 4.5]}      # goes backwards
        assert fa._bad_line_timings(times) == {1, 2}

    def test_a_broken_line_falls_back_to_diff_alignment_and_is_flagged(self, monkeypatch):
        import forced_align as fa
        import translate_engines as te
        user_lines = ["你好", "再见"]
        whisper_segments = [{"start": 0.0, "end": 4.0, "text": "你好再见"}]
        coarse = core.align_transcript_to_timing(user_lines, whisper_segments)

        class Aligner:
            def align(self, audio, text, language):
                # "你好" aligns fine; "再见" comes back zero-length (issue #197)
                return [[_Unit("你", 0.0, 1.0), _Unit("好", 1.0, 2.0),
                         _Unit("再", 3.0, 3.0), _Unit("见", 3.0, 3.0)]]
        monkeypatch.setattr(fa, "_extract_audio_slice", lambda a, s, e, out: open(out, "wb").close())
        monkeypatch.setattr(fa, "load_qwen3_aligner", lambda use_gpu=False: Aligner())
        # Repair would fix this input; disable it to exercise the fallback.
        monkeypatch.setattr(fa, "_repair_unit_spans", lambda spans, lo, hi: spans)

        result = fa.align_with_qwen3("/fake.wav", user_lines, whisper_segments, language="zh")
        assert (result[0].start, result[0].end, result[0].flag) == (0.0, 2.0, None)
        assert result[1].flag == "timing_uncertain" and result[1].flag_note
        assert result[1].end == pytest.approx(coarse[1].end)
        assert result[1].end > result[1].start
        assert "Timing uncertain" in te.flag_reason_label("timing_uncertain")
        assert "timing_uncertain" not in te.FLAG_REASONS  # never offered to the LLM


def _write_fixture_wav(path, seconds=0.2, samplerate=8000):
    """A tiny real WAV -- Step 4g's chunking always round-trips vocal
    separation through soundfile now, even for a short file, so a
    placeholder path/bytes no longer reaches these fakes' own logic."""
    import numpy as np
    import soundfile as sf
    sf.write(path, np.zeros((int(seconds * samplerate), 1), dtype="float32"), samplerate)


def _fake_audio_separator(monkeypatch, fail=False, returns_relative=False):
    seen = {}

    class Separator:
        def __init__(self, output_dir=None, model_file_dir=None, output_single_stem=None, **kw):
            seen.update(output_dir=output_dir, stem=output_single_stem)
            self.output_dir = output_dir

        def load_model(self, model_filename=None):
            seen["model"] = model_filename

        def separate(self, audio_path):
            if fail:
                raise RuntimeError("CUDA out of memory")
            import os
            import soundfile as sf
            name = "audio_(Vocals)_vocals_mel_band_roformer.wav"
            data, sr = sf.read(audio_path, dtype="float32", always_2d=True)
            out_path = os.path.join(self.output_dir, name)
            sf.write(out_path, data, sr)
            return [name if returns_relative else out_path]

    pkg = types.ModuleType("audio_separator")
    sub = types.ModuleType("audio_separator.separator")
    sub.Separator = Separator
    monkeypatch.setitem(sys.modules, "audio_separator", pkg)
    monkeypatch.setitem(sys.modules, "audio_separator.separator", sub)
    return seen


class TestVocalSeparationBackends:
    @pytest.mark.parametrize("relative", [False, True])
    def test_auto_prefers_mel_band_roformer(self, monkeypatch, tmp_path, relative):
        pytest.importorskip("soundfile")
        import audio_preprocess as ap
        seen = _fake_audio_separator(monkeypatch, returns_relative=relative)
        monkeypatch.setattr(ap, "separate_vocals_demucs",
                            lambda *a, **k: pytest.fail("Demucs used when RoFormer worked"))
        monkeypatch.setitem(ap._BACKENDS, "demucs", ap.separate_vocals_demucs)
        in_path = str(tmp_path / "in.wav")
        _write_fixture_wav(in_path)
        out = str(tmp_path / "vocals.wav")
        assert ap.separate_vocals(in_path, out) == out
        assert os.path.exists(out)
        assert seen["model"] == ap.MEL_ROFORMER_VOCAL_MODEL and seen["stem"] == "Vocals"
        # temp separator work dir cleaned up -- only the two real WAVs remain
        assert sorted(p.name for p in tmp_path.iterdir()) == ["in.wav", "vocals.wav"]

    def test_auto_falls_back_to_demucs_when_audio_separator_is_missing(self, monkeypatch, tmp_path):
        import audio_preprocess as ap
        monkeypatch.setitem(sys.modules, "audio_separator", None)
        monkeypatch.setitem(sys.modules, "audio_separator.separator", None)
        used = []
        monkeypatch.setitem(ap._BACKENDS, "demucs", lambda a, o, **kw: used.append("demucs") or o)
        assert ap.separate_vocals("in.wav", str(tmp_path / "v.wav")) == str(tmp_path / "v.wav")
        assert used == ["demucs"]

    def test_auto_falls_back_to_demucs_when_roformer_fails(self, monkeypatch, tmp_path):
        pytest.importorskip("soundfile")
        import audio_preprocess as ap
        _fake_audio_separator(monkeypatch, fail=True)
        used = []
        monkeypatch.setitem(ap._BACKENDS, "demucs", lambda a, o, **kw: used.append("demucs") or o)
        in_path = str(tmp_path / "in.wav")
        _write_fixture_wav(in_path)
        ap.separate_vocals(in_path, str(tmp_path / "v.wav"))
        assert used == ["demucs"]

    def test_a_named_backend_uses_only_that_one(self, monkeypatch, tmp_path):
        import audio_preprocess as ap
        monkeypatch.setitem(sys.modules, "audio_separator", None)
        monkeypatch.setitem(sys.modules, "audio_separator.separator", None)
        monkeypatch.setitem(ap._BACKENDS, "demucs", lambda *a, **kw: pytest.fail("fell back"))
        with pytest.raises(ap.VocalSeparationError, match="pip install audio-separator"):
            ap.separate_vocals("in.wav", str(tmp_path / "v.wav"), backend="audio_separator")

    def test_both_failing_reports_both(self, monkeypatch, tmp_path):
        import audio_preprocess as ap
        monkeypatch.setitem(sys.modules, "audio_separator", None)
        monkeypatch.setitem(sys.modules, "audio_separator.separator", None)
        monkeypatch.setitem(sys.modules, "demucs", None)
        monkeypatch.setitem(sys.modules, "demucs.api", None)
        with pytest.raises(ap.VocalSeparationError) as err:
            ap.separate_vocals("in.wav", str(tmp_path / "v.wav"))
        assert "audio-separator" in str(err.value) and "demucs" in str(err.value)


def _fake_funasr(monkeypatch, texts_by_key):
    """generate(input=wav.scp) returns results in REVERSE order, so a
    position-based match would put every tag on the wrong line."""
    seen = {}

    class AutoModel:
        def __init__(self, model=None, device=None, **kw):
            seen["model"] = model

        def generate(self, input=None, **kw):
            with open(input, encoding="utf-8") as f:
                keys = [line.split()[0] for line in f if line.strip()]
            seen["keys"] = keys
            return [{"key": k, "text": texts_by_key[k]} for k in reversed(keys)]

    mod = types.ModuleType("funasr")
    mod.AutoModel = AutoModel
    monkeypatch.setitem(sys.modules, "funasr", mod)
    monkeypatch.setattr(core, "extract_audio_slice",
                        lambda a, s, e, out: open(out, "wb").close() or out)
    return seen


class TestSenseVoiceTags:
    def test_parses_emotion_and_events_from_raw_tokens(self):
        import sensevoice_tags as sv
        assert sv.parse_rich_text("<|zh|><|HAPPY|><|Laughter|><|withitn|>好啊") == {
            "emotion": "happy", "events": ["Laughter"]}
        assert sv.parse_rich_text("<|ja|><|EMO_UNKNOWN|><|BGM|><|woitn|>") == {
            "emotion": None, "events": ["BGM"]}
        assert len(sv.EMOTIONS) == 7 and len(sv.EVENTS) == 8

    def test_results_are_matched_to_lines_by_id_not_position(self, monkeypatch):
        import sensevoice_tags as sv
        from core import Line
        lines = [Line(idx=0, start=0, end=1, zh="a", id=41),
                 Line(idx=1, start=1, end=2, zh="b", id=42)]
        seen = _fake_funasr(monkeypatch, {"line_41": "<|zh|><|SAD|><|Speech|>a",
                                          "line_42": "<|zh|><|ANGRY|><|Cry|>b"})
        tags = sv.tag_lines("audio.wav", lines)
        assert seen["model"] == "iic/SenseVoiceSmall"
        assert tags == {41: {"emotion": "sad", "events": ["Speech"]},
                        42: {"emotion": "angry", "events": ["Cry"]}}

    def test_missing_funasr_is_a_clear_error(self, monkeypatch):
        import sensevoice_tags as sv
        from core import Line
        monkeypatch.setitem(sys.modules, "funasr", None)
        with pytest.raises(sv.SenseVoiceUnavailable, match="pip install funasr"):
            sv.tag_lines("a.wav", [Line(idx=0, start=0, end=1, zh="a", id=1)])

    def test_surfaced_alongside_text_emotions_never_merged(self, isolated_db, monkeypatch):
        """The roadmap's exit condition: audio tags sit next to the
        text-based ones, and don't change them or what feeds translation."""
        import background_jobs
        import emotion
        import sensevoice_tags as sv
        from core import Line
        from services.workspace_job_service import run_sensevoice_job
        did = isolated_db.create_drama(title_en="D")
        isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="你真行"),
                                     Line(idx=1, start=1, end=2, zh="好的")])
        isolated_db.save_emotions(did, {0: {"emotion": "sarcastic", "intensity": 0.8, "note": ""},
                                        1: {"emotion": "sad", "intensity": 0.5, "note": ""}})
        text_before = isolated_db.load_emotions(did)
        guidance_before = emotion.build_emotion_guidance(text_before, [0, 1])
        lines = isolated_db.load_line_objects(did)
        _fake_funasr(monkeypatch, {f"line_{lines[0].id}": "<|zh|><|HAPPY|><|Laughter|>",
                                   f"line_{lines[1].id}": "<|zh|><|HAPPY|><|Speech|>"})
        ddir = isolated_db.drama_dir(did)
        job_id = "test_sensevoice_job"
        background_jobs._jobs[job_id] = {"status": "running", "progress": 0.0, "message": "",
                                          "error": None, "cancel_requested": False, "result": None}
        run_sensevoice_job(job_id, did, lines, "a.wav", ddir, False)
        background_jobs._jobs.pop(job_id, None)

        assert isolated_db.load_emotions(did) == text_before  # untouched
        assert emotion.build_emotion_guidance(isolated_db.load_emotions(did), [0, 1]) == guidance_before
        rows = sv.side_by_side(lines, isolated_db.load_emotions(did), sv.load_audio_tags(ddir))
        assert [(r["text_emotion"], r["audio_emotion"]) for r in rows] == [
            ("sarcastic", "happy"), ("sad", "happy")]
        assert rows[0]["audio_events"] == "laughter"
        # sarcasm sounding happy isn't a contradiction; sad text read as happy is
        assert [r["disagree"] for r in rows] == [False, True]



class TestSensevoiceCancelAndTimeout:
    def test_tag_lines_checks_cancel_per_line_and_slices_with_a_timeout(self, monkeypatch):
        import core
        import sensevoice_tags as sv
        from core import Line
        _fake_funasr(monkeypatch, {})
        slices = []
        monkeypatch.setattr(core, "extract_audio_slice",
                            lambda *a, **k: slices.append(k) or a[3])

        class Stop(Exception):
            pass

        def cancel():
            if slices:
                raise Stop()
        lines = [Line(idx=i, start=i, end=i + 1, zh="x", id=i + 1) for i in range(3)]
        with pytest.raises(Stop):
            sv.tag_lines("a.wav", lines, cancel_check=cancel)
        assert len(slices) == 1

    def test_slice_default_timeout_is_finite(self):
        import inspect
        import core
        assert inspect.signature(core.extract_audio_slice).parameters["timeout"].default \
            == core.SLICE_TIMEOUT_SECONDS
