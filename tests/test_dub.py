"""
tests/test_dub.py -- dub.py, previously entirely untested despite being a
core, fully-wired feature (voice cloning via F5-TTS, plain
edge-tts/Piper TTS, dub-track and narration-track assembly).

pydub, edge_tts and f5_tts aren't installed in this sandbox (no ffmpeg-
backed audio library, no network, no GPU) -- each is faked at its own
import boundary, the same way other tests here fake faster_whisper or
paddleocr. FakeAudioSegment models just enough of pydub's real API
(silent/from_file/overlay/export/+=/len) to exercise build_dub_track's
and build_narration_track's actual timing/routing/error-isolation logic
for real, not just that the code compiles.
"""
import json
import os
import queue
import re
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


def _unsigned(path):
    """A clip path without its Step 11e content signature:
    dub_clips/line_0000_<hash>.wav -> dub_clips/line_0000.wav."""
    return re.sub(r"_[0-9a-f]{10}(?=\.wav$)", "", path)


def _install_fake_pydub(monkeypatch, clip_lengths=None):
    """clip_lengths: optional {path: ms} so from_file() clips report a
    specific length for narration-track cursor-math tests."""
    clip_lengths = clip_lengths or {}

    class ConfiguredFakeAudioSegment(FakeAudioSegment):
        @classmethod
        def from_file(cls, path):
            seg = cls([("clip", path)])
            # keyed by the unsigned path (.../line_0000.wav), see _unsigned
            seg._length = clip_lengths.get(_unsigned(path), 1000)
            return seg

    fake_module = types.ModuleType("pydub")
    fake_module.AudioSegment = ConfiguredFakeAudioSegment
    monkeypatch.setitem(sys.modules, "pydub", fake_module)
    return ConfiguredFakeAudioSegment


def _install_fake_edge_tts(monkeypatch, save_exception=None):
    """Registers a fake edge_tts module -- real edge_tts isn't installed
    in this sandbox (no network). Communicate(...).save(out_path) either
    writes a placeholder file, or raises save_exception if one is given,
    the same way a real blocked/failed request would."""
    fake_module = types.ModuleType("edge_tts")

    class FakeCommunicate:
        def __init__(self, text, voice, rate="+0%"):
            self.text, self.voice = text, voice

        async def save(self, out_path):
            if save_exception:
                raise save_exception
            with open(out_path, "w") as f:
                f.write("fake-audio")

    fake_module.Communicate = FakeCommunicate
    monkeypatch.setitem(sys.modules, "edge_tts", fake_module)


class TestEdgeTTSBlockedErrorHandling:
    """Regression coverage for a real, periodic Microsoft-side block:
    edge-tts's WebSocket handshake gets rejected with a 403 (latest
    reported January 2026). Before this, that surfaced as a raw,
    unhelpful exception per failed line."""

    def test_a_403_error_is_wrapped_with_a_clear_message(self, monkeypatch, tmp_path):
        _install_fake_edge_tts(monkeypatch, save_exception=Exception(
            "server rejected WebSocket connection: HTTP 403"))
        with pytest.raises(dub.EdgeTTSBlockedError, match="pip install -U edge-tts"):
            dub.synthesize_line("hello", "en-US-AvaNeural", str(tmp_path / "out.wav"))

    def test_a_non_403_error_passes_through_unwrapped(self, monkeypatch, tmp_path):
        _install_fake_edge_tts(monkeypatch, save_exception=RuntimeError("network unreachable"))
        with pytest.raises(RuntimeError, match="network unreachable"):
            dub.synthesize_line("hello", "en-US-AvaNeural", str(tmp_path / "out.wav"))

    def test_success_is_unaffected(self, monkeypatch, tmp_path):
        _install_fake_edge_tts(monkeypatch)
        out_path = str(tmp_path / "out.wav")
        dub.synthesize_line("hello", "en-US-AvaNeural", out_path)
        assert os.path.exists(out_path)


class TestSynthesizeEdgeTTSWithPiperFallback:
    """When Piper is installed, a blocked edge-tts request should fall
    back to it automatically rather than losing the line -- an upstream
    block outside anyone's control shouldn't need per-line manual
    intervention when an offline alternative is already available."""

    def test_falls_back_to_piper_when_installed(self, monkeypatch, tmp_path):
        _install_fake_edge_tts(monkeypatch, save_exception=Exception("HTTP 403"))
        monkeypatch.setitem(sys.modules, "piper", types.ModuleType("piper"))
        offline_calls = []
        monkeypatch.setattr(
            dub, "synthesize_line_offline",
            lambda text, voice, out_path: offline_calls.append((text, voice, out_path))
            or open(out_path, "w").close())

        out_path = str(tmp_path / "out.wav")
        dub._synthesize_edge_tts_with_piper_fallback(
            "hello", "en-US-AvaNeural", out_path, {}, "SPEAKER_00")

        assert offline_calls == [("hello", dub.DEFAULT_OFFLINE_VOICE_POOL[0], out_path)]

    def test_uses_the_speakers_configured_offline_voice_if_set(self, monkeypatch, tmp_path):
        _install_fake_edge_tts(monkeypatch, save_exception=Exception("HTTP 403"))
        monkeypatch.setitem(sys.modules, "piper", types.ModuleType("piper"))
        voices_used = []
        monkeypatch.setattr(
            dub, "synthesize_line_offline",
            lambda text, voice, out_path: voices_used.append(voice) or open(out_path, "w").close())

        out_path = str(tmp_path / "out.wav")
        dub._synthesize_edge_tts_with_piper_fallback(
            "hello", "en-US-AvaNeural", out_path,
            {"SPEAKER_00": "en_GB-alba-medium"}, "SPEAKER_00")

        assert voices_used == ["en_GB-alba-medium"]

    def test_reraises_the_blocked_error_when_piper_is_not_installed(self, monkeypatch, tmp_path):
        _install_fake_edge_tts(monkeypatch, save_exception=Exception("HTTP 403"))
        # piper genuinely isn't installed in this sandbox -- no mocking needed
        # to exercise the real ImportError path.
        out_path = str(tmp_path / "out.wav")
        with pytest.raises(dub.EdgeTTSBlockedError):
            dub._synthesize_edge_tts_with_piper_fallback(
                "hello", "en-US-AvaNeural", out_path, {}, "SPEAKER_00")

    def test_a_non_blocked_error_propagates_without_trying_piper(self, monkeypatch, tmp_path):
        _install_fake_edge_tts(monkeypatch, save_exception=RuntimeError("network down"))
        out_path = str(tmp_path / "out.wav")
        with pytest.raises(RuntimeError, match="network down"):
            dub._synthesize_edge_tts_with_piper_fallback(
                "hello", "en-US-AvaNeural", out_path, {}, "SPEAKER_00")


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


class TestBuildDubTrackClonePriority:
    """The core routing logic in both build_dub_track and
    build_narration_track: F5-TTS clone > offline/
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
        name = f"line_0000_{dub.clip_signature('Hello', {'engine': 'edge_tts', 'voice': 'en-US-AvaNeural'})}.wav"
        (clips_dir / name).write_text("already here")

        calls = []
        monkeypatch.setattr(dub, "synthesize_line", lambda *a, **k: calls.append(1))

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        dub.build_dub_track(lines, str(tmp_path), {})

        assert calls == []  # never re-synthesized
        assert lines[0].dub_filename == os.path.join("dub_clips", name)


class TestBuildNarrationTrack:
    def test_concatenates_clips_with_a_gap_and_sets_line_timing(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch, clip_lengths={
            str(tmp_path / "dub_clips" / "line_0000.wav"): 2000,
            str(tmp_path / "dub_clips" / "line_0001.wav"): 3000,
        })
        monkeypatch.setattr(dub, "synthesize_line",
                             lambda text, voice, out_path, **kwargs: open(out_path, "w").close())

        # Different speakers, so each line is its own TTS call (same-speaker
        # lines would share one -- see TestNarrationTTSUnits).
        lines = [Line(idx=0, start=0, end=0, zh="x", en="First", speaker="A"),
                 Line(idx=1, start=0, end=0, zh="y", en="Second", speaker="B")]
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
        clips, skipped = dub.extract_reference_clips("/fake/audio.wav", [], speaker_segments, str(tmp_path))
        assert clips["A"]["start"] == 10.0
        assert clips["A"]["end"] == 16.0
        assert skipped == {}

    def test_ignores_clips_outside_the_duration_bounds(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [
            {"speaker": "A", "start": 0.0, "end": 1.0},    # too short
            {"speaker": "A", "start": 5.0, "end": 25.0},   # too long
        ]
        clips, skipped = dub.extract_reference_clips("/fake/audio.wav", [], speaker_segments, str(tmp_path))
        assert "A" not in clips

    def test_each_speaker_gets_their_own_best_clip(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [
            {"speaker": "A", "start": 0.0, "end": 6.0},
            {"speaker": "B", "start": 10.0, "end": 15.0},
        ]
        clips, skipped = dub.extract_reference_clips("/fake/audio.wav", [], speaker_segments, str(tmp_path))
        assert set(clips.keys()) == {"A", "B"}
        assert skipped == {}

    def test_reports_a_specific_reason_for_a_speaker_with_no_eligible_segment(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [
            {"speaker": "A", "start": 0.0, "end": 6.0},     # in range
            {"speaker": "B", "start": 10.0, "end": 11.5},   # 1.5s -- too short, closest to the window
            {"speaker": "B", "start": 20.0, "end": 21.0},   # 1.0s -- also too short but farther
        ]
        clips, skipped = dub.extract_reference_clips("/fake/audio.wav", [], speaker_segments, str(tmp_path))
        assert "B" not in clips
        assert skipped == {"B": {"closest_duration": 1.5, "reason": "too_short"}}

    def test_reports_too_long_when_the_closest_segment_exceeds_the_max(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [{"speaker": "A", "start": 0.0, "end": 20.0}]  # 20s -- too long
        clips, skipped = dub.extract_reference_clips("/fake/audio.wav", [], speaker_segments, str(tmp_path))
        assert "A" not in clips
        assert skipped == {"A": {"closest_duration": 20.0, "reason": "too_long"}}


class TestBuildTrackSubprocessWorker:
    """Step 4e: build_track_subprocess_worker() is the entry point
    background_jobs.start_process_job() runs in its own OS process, so a
    real mid-run Cancel can terminate it. Confirmed safe to hard-stop:
    each line's clip writes to its own file, and both build_dub_track and
    build_narration_track already reuse (rather than re-synthesize) any
    clip that exists from a prior partial run. Tested here as a plain
    function call against the same fakes used elsewhere in this file --
    background_jobs.py's own tests cover the actual multiprocessing.
    Process/cancel machinery -- confirming it produces exactly what a
    direct build_dub_track()/build_narration_track() call would, routes
    on is_narration, and reports an exception instead of raising into the
    (real, separate) process."""

    def test_matches_a_direct_dub_track_call_on_success(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        monkeypatch.setattr(dub, "synthesize_line",
                             lambda text, voice, out_path, **kwargs: open(out_path, "w").close())

        direct_lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        direct_out_path, direct_errors = dub.build_dub_track(
            direct_lines, str(tmp_path), {"A": "en-US-AvaNeural"})

        worker_lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        result_queue = queue.Queue()
        dub.build_track_subprocess_worker(
            worker_lines, str(tmp_path), {"A": "en-US-AvaNeural"}, "en-US-AvaNeural",
            {}, "edge_tts", False, {}, 1.4, 0.85, result_queue)
        outcome = result_queue.get_nowait()

        assert outcome == ("ok", {"lines": worker_lines, "out_path": direct_out_path,
                                  "errors": direct_errors})
        assert worker_lines[0].dub_filename == direct_lines[0].dub_filename

    def test_is_narration_true_routes_to_build_narration_track(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch, clip_lengths={
            str(tmp_path / "dub_clips" / "line_0000.wav"): 2000,
        })
        monkeypatch.setattr(dub, "synthesize_line",
                             lambda text, voice, out_path, **kwargs: open(out_path, "w").close())

        lines = [Line(idx=0, start=0, end=0, zh="x", en="First")]
        result_queue = queue.Queue()
        dub.build_track_subprocess_worker(
            lines, str(tmp_path), {}, "en-US-AvaNeural", {}, "edge_tts", True, {}, 1.4, 0.85, result_queue)
        outcome = result_queue.get_nowait()

        # only build_narration_track rewrites .start/.end onto the lines
        assert lines[0].end == 2.0
        assert outcome[0] == "ok"

    def test_reports_an_exception_instead_of_raising(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        monkeypatch.setattr(dub, "synthesize_line",
                             lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(dub, "build_dub_track",
                             lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        result_queue = queue.Queue()
        dub.build_track_subprocess_worker(
            lines, str(tmp_path), {}, "en-US-AvaNeural", {}, "edge_tts", False, {}, 1.4, 0.85, result_queue)
        outcome = result_queue.get_nowait()

        assert outcome == ("error", "RuntimeError", "boom")


# ---------------------------------------------------------------------------
# Step 11c/11d/11e
# ---------------------------------------------------------------------------

class _TimedAudio(FakeAudioSegment):
    """A fake clip whose length is written inside its own file ("ms:1200"),
    so a synthesized clip's length can depend on its text and a stretched
    clip's on its factor."""

    @classmethod
    def from_file(cls, path):
        seg = cls([("clip", path)])
        with open(path) as f:
            seg._length = int(f.read().split(":")[1])
        return seg


def _write_ms(path, ms):
    with open(path, "w") as f:
        f.write(f"ms:{int(round(ms))}")


@pytest.fixture
def timed(monkeypatch):
    """Fake pydub + TTS + time_stretch: every character of a line's text
    is 100ms of speech. Returns the list of TTS texts and stretch calls."""
    fake_module = types.ModuleType("pydub")
    fake_module.AudioSegment = _TimedAudio
    monkeypatch.setitem(sys.modules, "pydub", fake_module)
    record = types.SimpleNamespace(synth=[], stretch=[])

    def fake_synth(text, voice, out_path):
        record.synth.append(text)
        _write_ms(out_path, len(text) * 100)

    def fake_stretch(in_path, out_path, factor):
        record.stretch.append(factor)
        _write_ms(out_path, _TimedAudio.from_file(in_path)._length / factor)
        return out_path

    monkeypatch.setattr(dub, "synthesize_line", fake_synth)
    monkeypatch.setattr(dub, "time_stretch", fake_stretch)
    return record


def _line(idx, text, start=0.0, end=1.0, speaker="A"):
    return Line(idx=idx, start=start, end=end, zh="x", en=text, speaker=speaker)


class TestStretchForWindow:
    """Step 11c item 1: the clamped stretch factor, as youtube-auto-dub's
    synchronize stage computes it -- clip / window, capped."""

    def test_within_the_tolerance_of_one_is_left_alone(self):
        assert dub.stretch_for_window(1000.5, 1000) == (1.0, dub.PACING_FIT)
        assert dub.stretch_for_window(1000, 1000) == (1.0, dub.PACING_FIT)

    def test_a_modest_overflow_is_sped_up_to_fit(self):
        assert dub.stretch_for_window(1200, 1000) == (1.2, dub.PACING_STRETCHED)

    def test_past_the_cap_it_gets_the_cap_and_overflows(self):
        assert dub.stretch_for_window(3000, 1000) == (dub.DUB_MAX_SPEEDUP, dub.PACING_OVERFLOW)

    def test_a_short_clip_is_slowed_no_further_than_the_floor(self):
        assert dub.stretch_for_window(900, 1000) == (0.9, dub.PACING_FIT)
        assert dub.stretch_for_window(500, 1000) == (dub.DUB_MAX_SLOWDOWN, dub.PACING_FIT)

    def test_the_clamp_is_configurable(self):
        assert dub.stretch_for_window(1200, 1000, max_speedup=1.1) == (1.1, dub.PACING_OVERFLOW)
        assert dub.stretch_for_window(500, 1000, max_slowdown=1.0) == (1.0, dub.PACING_FIT)

    def test_a_zero_length_window_never_divides_by_zero(self):
        assert dub.stretch_for_window(1000, 0) == (1.0, dub.PACING_OVERFLOW)

    def test_each_state_has_its_indicator(self):
        assert dub.PACING_ICONS == {dub.PACING_FIT: "🟢", dub.PACING_STRETCHED: "🟡",
                                    dub.PACING_OVERFLOW: "🔴"}


class TestTimeStretch:
    def test_runs_a_pitch_preserving_atempo_and_leaves_no_partial_file(self, monkeypatch, tmp_path):
        seen = []

        def fake_run(cmd, check, capture_output):
            seen.append(cmd)
            _write_ms(cmd[-1], 1000)
        monkeypatch.setattr("subprocess.run", fake_run)
        out = str(tmp_path / "line_0000_abc_x1.200.wav")
        dub.time_stretch(str(tmp_path / "in.wav"), out, 1.2)
        assert seen[0][:3] == ["ffmpeg", "-y", "-i"]
        assert "atempo=1.200" in seen[0]
        assert os.listdir(tmp_path) == [os.path.basename(out)]

    def test_a_failed_stretch_leaves_nothing_behind_for_the_next_run(self, monkeypatch, tmp_path):
        def fake_run(cmd, check, capture_output):
            _write_ms(cmd[-1], 10)  # half-written, then ffmpeg dies
            raise RuntimeError("ffmpeg died")
        monkeypatch.setattr("subprocess.run", fake_run)
        with pytest.raises(RuntimeError):
            dub.time_stretch(str(tmp_path / "in.wav"), str(tmp_path / "out_x1.200.wav"), 1.2)
        assert os.listdir(tmp_path) == []


class TestDubTrackTimeStretch:
    """Step 11c: build_dub_track stretches each clip into its window,
    within the clamp, and records the result per line."""

    def test_a_modest_overflow_is_sped_up_by_a_clamped_factor(self, timed, tmp_path):
        lines = [_line(0, "x" * 12)]  # 1.2s of speech in a 1s window
        _, errors = dub.build_dub_track(lines, str(tmp_path), {})
        assert errors == [] and timed.stretch == [1.2]
        assert lines[0].dub_filename.endswith("_x1.200.wav")
        rec = dub.pacing_for_line(lines[0], dub.load_pacing(str(tmp_path)))
        assert rec["status"] == dub.PACING_STRETCHED and rec["factor"] == 1.2

    def test_a_clip_that_already_fits_is_left_untouched(self, timed, tmp_path):
        lines = [_line(0, "x" * 10)]  # exactly 1s
        dub.build_dub_track(lines, str(tmp_path), {})
        assert timed.stretch == []
        assert "_x" not in lines[0].dub_filename
        assert dub.load_pacing(str(tmp_path))[0]["status"] == dub.PACING_FIT

    def test_past_the_cap_it_is_capped_and_overflows_not_crushed(self, timed, tmp_path):
        lines = [_line(0, "x" * 30)]  # 3s of speech in a 1s window
        dub.build_dub_track(lines, str(tmp_path), {})
        assert timed.stretch == [dub.DUB_MAX_SPEEDUP]
        rec = dub.load_pacing(str(tmp_path))[0]
        assert rec["status"] == dub.PACING_OVERFLOW
        assert dub.PACING_ICONS[rec["status"]] == "🔴"

    def test_the_clamp_passed_in_is_the_one_used(self, timed, tmp_path):
        lines = [_line(0, "x" * 12)]
        dub.build_dub_track(lines, str(tmp_path), {}, max_speedup=1.1)
        assert timed.stretch == [1.1]
        assert dub.load_pacing(str(tmp_path))[0]["status"] == dub.PACING_OVERFLOW

    def test_all_three_states_map_to_their_indicator(self, timed, tmp_path):
        lines = [_line(0, "x" * 10, 0, 1), _line(1, "x" * 12, 1, 2), _line(2, "x" * 30, 2, 3)]
        dub.build_dub_track(lines, str(tmp_path), {})
        pacing = dub.load_pacing(str(tmp_path))
        assert [dub.PACING_ICONS[dub.pacing_for_line(ln, pacing)["status"]] for ln in lines] == [
            "🟢", "🟡", "🔴"]

    def test_without_ffmpeg_the_line_still_plays_unstretched(self, timed, monkeypatch, tmp_path):
        monkeypatch.setattr(dub, "time_stretch",
                            lambda *a: (_ for _ in ()).throw(FileNotFoundError("ffmpeg")))
        lines = [_line(0, "x" * 12)]
        _, errors = dub.build_dub_track(lines, str(tmp_path), {})
        assert errors == []
        assert "_x" not in lines[0].dub_filename
        rec = dub.load_pacing(str(tmp_path))[0]
        assert (rec["status"], rec["factor"]) == (dub.PACING_OVERFLOW, 1.0)

    def test_the_pacing_rewrite_runs_first_and_the_stretch_only_closes_what_is_left(
            self, timed, monkeypatch, tmp_path):
        import translate_engines
        long_text = "x" * 30  # 3s -- would hit the cap and overflow
        lines = [_line(0, long_text)]
        dub.build_dub_track(lines, str(tmp_path), {})
        assert dub.load_pacing(str(tmp_path))[0]["status"] == dub.PACING_OVERFLOW

        # Section 7's pacing rewrite, with a fake LLM, shortens the line...
        monkeypatch.setattr(translate_engines, "call_llm_json",
                            lambda *a, **k: json.dumps({"1": "x" * 11}))
        translate_engines.rewrite_for_pacing_llm(lines, types.SimpleNamespace(supports_reference=True))
        assert lines[0].en == "x" * 11

        # ...so the next dub voices the rewritten line (not the old clip) and
        # only stretches the 10% gap the rewrite left.
        timed.stretch.clear()
        dub.build_dub_track(lines, str(tmp_path), {})
        assert timed.synth[-1] == "x" * 11
        assert timed.stretch == [1.1]
        assert dub.load_pacing(str(tmp_path))[0]["status"] == dub.PACING_STRETCHED

    def test_a_pacing_record_goes_stale_once_the_line_points_elsewhere(self, timed, tmp_path):
        lines = [_line(0, "x" * 12)]
        dub.build_dub_track(lines, str(tmp_path), {})
        pacing = dub.load_pacing(str(tmp_path))
        lines[0].dub_filename = "dub_clips/something_else.wav"
        assert dub.pacing_for_line(lines[0], pacing) is None
        assert dub.load_pacing(str(tmp_path / "nowhere")) == {}


class _Killed(BaseException):
    """Stands in for Cancel killing the dub process mid-run -- not an
    Exception, so build_dub_track's per-line error handling can't catch it."""


class TestClipCacheFollowsTheText:
    """Step 11e: a clip is reused only while the text and voice it was made
    from are unchanged."""

    def test_editing_a_dubbed_line_re_voices_it(self, timed, tmp_path):
        lines = [_line(0, "Helo there")]
        dub.build_dub_track(lines, str(tmp_path), {})
        stale = lines[0].dub_filename

        lines[0].en = "Hello there"  # typo fixed after dubbing
        dub.build_dub_track(lines, str(tmp_path), {})

        assert timed.synth == ["Helo there", "Hello there"]
        assert lines[0].dub_filename != stale

    def test_changing_a_characters_voice_re_voices_their_lines(self, timed, tmp_path):
        lines = [_line(0, "x" * 10)]
        dub.build_dub_track(lines, str(tmp_path), {"A": "en-US-AvaNeural"})
        dub.build_dub_track(lines, str(tmp_path), {"A": "en-GB-SoniaNeural"})
        assert len(timed.synth) == 2

    def test_a_cancelled_run_still_resumes_from_its_finished_clips(self, timed, monkeypatch, tmp_path):
        lines = [_line(i, f"Line {i}.", i, i + 1) for i in range(4)]
        real_synth = dub.synthesize_line

        def killed_on_line_2(text, voice, out_path):
            if text == "Line 2.":
                raise _Killed()
            real_synth(text, voice, out_path)
        monkeypatch.setattr(dub, "synthesize_line", killed_on_line_2)
        with pytest.raises(_Killed):
            dub.build_dub_track(lines, str(tmp_path), {})
        assert timed.synth == ["Line 0.", "Line 1."]

        monkeypatch.setattr(dub, "synthesize_line", real_synth)
        fresh = [_line(i, f"Line {i}.", i, i + 1) for i in range(4)]
        _, errors = dub.build_dub_track(fresh, str(tmp_path), {})
        assert errors == []
        assert timed.synth == ["Line 0.", "Line 1.", "Line 2.", "Line 3."]  # 0 and 1 reused
        assert all(ln.dub_filename for ln in fresh)

    def test_an_edited_narration_unit_is_re_voiced_too(self, timed, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="x", en="Helo.", speaker="N")]
        dub.build_narration_track(lines, str(tmp_path), {})
        lines[0].en = "Hello."
        dub.build_narration_track(lines, str(tmp_path), {})
        assert timed.synth == ["Helo.", "Hello."]
        dub.build_narration_track(lines, str(tmp_path), {})
        assert timed.synth == ["Helo.", "Hello."]  # unchanged -- reused


class TestHostedCloningRemoved:
    """Step 11d: nothing references the removed hosted cloning engine
    except the characters.elevenlabs_voice_id column (kept on purpose, as
    the record of how existing audio was made) and the message shown to a
    character that was cloned with it."""

    ALLOWED = ("elevenlabs_voice_id", "previously cloned via ElevenLabs")
    SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules"}

    def test_no_references_remain(self):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in self.SKIP_DIRS]
            for name in filenames:
                path = os.path.join(dirpath, name)
                if path == os.path.abspath(__file__):
                    continue
                try:
                    with open(path, encoding="utf-8") as f:
                        text = f.read()
                except (UnicodeDecodeError, OSError):
                    continue
                for n, line in enumerate(text.splitlines(), 1):
                    if "elevenlabs" in line.lower() and not any(a in line for a in self.ALLOWED):
                        found.append(f"{os.path.relpath(path, root)}:{n}: {line.strip()}")
        assert found == []

    def test_a_character_cloned_with_it_dubs_without_crashing(self, timed, isolated_db, tmp_path):
        did = isolated_db.create_drama(title_en="Old dub")
        isolated_db.upsert_character(did, "A", elevenlabs_voice_id="v1")
        chars = isolated_db.list_characters(did)
        clone_map = dub.clone_map_from_characters(chars, str(tmp_path))
        lines = [_line(0, "x" * 10)]
        _, errors = dub.build_dub_track(lines, str(tmp_path), {}, character_clone_map=clone_map)
        assert errors == [] and timed.synth == ["x" * 10]  # plain TTS, no clone
        assert "has been removed" in dub.clone_removed_message(chars[0])
