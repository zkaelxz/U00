"""
tests/test_dub.py -- dub.py, previously entirely untested despite being a
core, fully-wired feature (voice cloning via F5-TTS/ElevenLabs, plain
edge-tts/Piper TTS, dub-track and narration-track assembly).

pydub, edge_tts and f5_tts aren't installed in this sandbox (no ffmpeg-
backed audio library, no network, no GPU) -- each is faked at its own
import boundary, the same way other tests here fake faster_whisper or
paddleocr. FakeAudioSegment models just enough of pydub's real API
(silent/from_file/overlay/export/+=/len) to exercise build_dub_track's
and build_narration_track's actual timing/routing/error-isolation logic
for real, not just that the code compiles.
"""
import os
import sys
import types

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dub
from core import Line


class FakeAudioSegment:
    """Tracks its own "audio" as a list of (kind, ...) tokens instead of
    real samples -- enough to assert what got placed where and in what
    order without decoding anything."""

    def __init__(self, tokens=None):
        self.tokens = tokens or []

    @classmethod
    def silent(cls, duration=0):
        return cls([("silence", duration)])

    @classmethod
    def from_file(cls, path):
        return cls([("clip", path)])

    def overlay(self, other, position=0):
        self.tokens.append(("overlay", position, other.tokens))
        return self

    def __add__(self, other):
        return FakeAudioSegment(self.tokens + other.tokens)

    def __getitem__(self, s):
        return FakeAudioSegment([("slice", s.start, s.stop)])

    def __len__(self):
        # A fake clip is treated as this many "ms" long for cursor math --
        # callers set this explicitly via _length when it matters.
        return getattr(self, "_length", 1000)

    def export(self, path, format="wav"):
        with open(path, "w") as f:
            f.write("fake-audio")
        return path


def _install_fake_pydub(monkeypatch, clip_lengths=None):
    """clip_lengths: optional {path: ms} so from_file() clips report a
    specific length for narration-track cursor-math tests."""
    clip_lengths = clip_lengths or {}

    class ConfiguredFakeAudioSegment(FakeAudioSegment):
        @classmethod
        def from_file(cls, path):
            seg = cls([("clip", path)])
            seg._length = clip_lengths.get(path, 1000)
            return seg

    fake_module = types.ModuleType("pydub")
    fake_module.AudioSegment = ConfiguredFakeAudioSegment
    monkeypatch.setitem(sys.modules, "pydub", fake_module)
    return ConfiguredFakeAudioSegment


class TestAssignVoicesToCharacters:
    def test_round_robins_through_the_pool(self):
        result = dub.assign_voices_to_characters(["A", "B", "C"], voice_pool=["v1", "v2"])
        assert result == {"A": "v1", "B": "v2", "C": "v1"}

    def test_sorted_for_deterministic_assignment(self):
        result = dub.assign_voices_to_characters(["Zed", "Amy"], voice_pool=["v1", "v2"])
        assert result == {"Amy": "v1", "Zed": "v2"}

    def test_defaults_to_the_default_voice_pool(self):
        result = dub.assign_voices_to_characters(["A"])
        assert result["A"] == dub.DEFAULT_VOICE_POOL[0]


class TestSpeedRateForLine:
    def test_line_that_fits_comfortably_gets_no_speedup(self):
        assert dub._speed_rate_for_line("a short line", duration=10.0) == "+0%"

    def test_a_line_too_long_for_its_slot_gets_sped_up(self):
        long_text = " ".join(["word"] * 30)  # ~12s at 2.5 wps
        rate = dub._speed_rate_for_line(long_text, duration=3.0)
        assert rate != "+0%"
        assert rate.startswith("+") and rate.endswith("%")

    def test_speedup_is_capped_at_sixty_percent(self):
        very_long_text = " ".join(["word"] * 200)
        rate = dub._speed_rate_for_line(very_long_text, duration=1.0)
        assert rate == "+60%"

    def test_zero_duration_does_not_crash(self):
        assert dub._speed_rate_for_line("text", duration=0) == "+0%"


class TestBuildDubTrackClonePriority:
    """The core routing logic in both build_dub_track and
    build_narration_track: elevenlabs clone > F5-TTS clone > offline/
    edge-tts fallback, and one line's synthesis failure must never lose
    every other line's already-generated audio."""

    def test_uses_f5tts_cloning_when_a_clone_ref_is_set(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        calls = []
        monkeypatch.setattr(dub, "synthesize_line_cloned",
                             lambda text, ref_audio, ref_text, out_path: calls.append(
                                 ("cloned", text, ref_audio, ref_text)) or open(out_path, "w").close())
        monkeypatch.setattr(dub, "synthesize_line",
                             lambda *a, **k: calls.append(("plain",)) or open(a[2], "w").close())

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        out_path, errors = dub.build_dub_track(
            lines, str(tmp_path), {}, character_clone_map={
                "A": {"ref_audio": "/refs/a.wav", "ref_text": "你好"}})

        assert errors == []
        assert calls == [("cloned", "Hello", "/refs/a.wav", "你好")]

    def test_uses_elevenlabs_when_the_clone_entry_says_so(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        calls = []
        monkeypatch.setattr(dub, "synthesize_line_elevenlabs",
                             lambda api_key, text, voice_id, out_path: calls.append(
                                 ("elevenlabs", api_key, text, voice_id)) or open(out_path, "w").close())
        monkeypatch.setattr(dub, "synthesize_line_cloned",
                             lambda *a, **k: calls.append(("f5tts",)))

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        dub.build_dub_track(lines, str(tmp_path), {}, character_clone_map={
            "A": {"engine": "elevenlabs", "voice_id": "vid1", "api_key": "key1"}})

        assert calls == [("elevenlabs", "key1", "Hello", "vid1")]

    def test_falls_back_to_plain_tts_with_no_clone_ref(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        calls = []
        monkeypatch.setattr(dub, "synthesize_line",
                             lambda text, voice, out_path, rate="+0%": calls.append(
                                 ("plain", text, voice)) or open(out_path, "w").close())

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        dub.build_dub_track(lines, str(tmp_path), {"A": "en-US-AvaNeural"})

        assert calls == [("plain", "Hello", "en-US-AvaNeural")]

    def test_a_failed_line_does_not_lose_other_lines_audio(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)

        def flaky_synthesize(text, voice, out_path, rate="+0%"):
            if text == "fails":
                raise RuntimeError("TTS API down")
            open(out_path, "w").close()
        monkeypatch.setattr(dub, "synthesize_line", flaky_synthesize)

        lines = [Line(idx=0, start=0, end=1, zh="x", en="ok one", speaker="A"),
                 Line(idx=1, start=1, end=2, zh="y", en="fails", speaker="A"),
                 Line(idx=2, start=2, end=3, zh="z", en="ok two", speaker="A")]
        out_path, errors = dub.build_dub_track(lines, str(tmp_path), {})

        assert len(errors) == 1
        assert errors[0]["line_idx"] == 1
        # the two successful lines still got a dub_filename recorded
        assert lines[0].dub_filename is not None
        assert lines[1].dub_filename is None
        assert lines[2].dub_filename is not None

    def test_lines_with_no_translation_are_skipped_silently(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        calls = []
        monkeypatch.setattr(dub, "synthesize_line",
                             lambda *a, **k: calls.append(1) or open(a[2], "w").close())

        lines = [Line(idx=0, start=0, end=1, zh="x", en="", speaker="A")]
        out_path, errors = dub.build_dub_track(lines, str(tmp_path), {})

        assert calls == []
        assert errors == []

    def test_already_generated_clips_are_reused_not_resynthesized(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        clips_dir = tmp_path / "dub_clips"
        clips_dir.mkdir()
        (clips_dir / "line_0000.wav").write_text("already here")

        calls = []
        monkeypatch.setattr(dub, "synthesize_line", lambda *a, **k: calls.append(1))

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        dub.build_dub_track(lines, str(tmp_path), {})

        assert calls == []  # never re-synthesized
        assert lines[0].dub_filename == os.path.join("dub_clips", "line_0000.wav")


class TestBuildNarrationTrack:
    def test_concatenates_clips_with_a_gap_and_sets_line_timing(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch, clip_lengths={
            str(tmp_path / "dub_clips" / "line_0000.wav"): 2000,
            str(tmp_path / "dub_clips" / "line_0001.wav"): 3000,
        })
        monkeypatch.setattr(dub, "synthesize_line",
                             lambda text, voice, out_path: open(out_path, "w").close())

        lines = [Line(idx=0, start=0, end=0, zh="x", en="First"),
                 Line(idx=1, start=0, end=0, zh="y", en="Second")]
        dub.build_narration_track(lines, str(tmp_path), {}, gap_ms=350)

        assert lines[0].start == 0.0
        assert lines[0].end == 2.0
        assert lines[1].start == 2.35        # 2000ms clip + 350ms gap
        assert lines[1].end == 5.35

    def test_a_blank_line_advances_the_cursor_by_nothing_and_records_a_point_in_time(self, tmp_path, monkeypatch):
        _install_fake_pydub(monkeypatch)
        lines = [Line(idx=0, start=0, end=0, zh="x", en="")]
        dub.build_narration_track(lines, str(tmp_path), {})
        assert lines[0].start == lines[0].end == 0.0

    def test_a_failed_line_still_advances_the_timeline_with_a_silent_gap(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        monkeypatch.setattr(dub, "synthesize_line",
                             lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))

        lines = [Line(idx=0, start=0, end=0, zh="x", en="fails")]
        out_path, errors = dub.build_narration_track(lines, str(tmp_path), {}, gap_ms=350)

        assert len(errors) == 1
        assert lines[0].end == pytest.approx(0.35)


class TestExtractReferenceClips:
    def test_picks_the_clip_closest_to_six_seconds_per_speaker(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [
            {"speaker": "A", "start": 0.0, "end": 4.0},   # 4s -- 2s from target
            {"speaker": "A", "start": 10.0, "end": 16.0},  # 6s -- exact target, should win
            {"speaker": "A", "start": 20.0, "end": 30.0},  # 10s -- 4s from target
        ]
        result = dub.extract_reference_clips("/fake/audio.wav", [], speaker_segments, str(tmp_path))
        assert result["A"]["start"] == 10.0
        assert result["A"]["end"] == 16.0

    def test_ignores_clips_outside_the_duration_bounds(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [
            {"speaker": "A", "start": 0.0, "end": 1.0},    # too short
            {"speaker": "A", "start": 5.0, "end": 25.0},   # too long
        ]
        result = dub.extract_reference_clips("/fake/audio.wav", [], speaker_segments, str(tmp_path))
        assert "A" not in result

    def test_each_speaker_gets_their_own_best_clip(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [
            {"speaker": "A", "start": 0.0, "end": 6.0},
            {"speaker": "B", "start": 10.0, "end": 15.0},
        ]
        result = dub.extract_reference_clips("/fake/audio.wav", [], speaker_segments, str(tmp_path))
        assert set(result.keys()) == {"A", "B"}
