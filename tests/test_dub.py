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


def _install_fake_requests(monkeypatch, captured):
    """Registers a fake requests module -- real requests calls out over the
    network. Appends each posted JSON payload to `captured`, so a test can
    assert on exactly what GPT-SoVITS's /tts endpoint was asked to say."""
    fake_module = types.ModuleType("requests")

    class FakeResponse:
        status_code = 200
        content = b"fake-audio"
        text = ""

    def fake_post(url, json=None, timeout=None):
        captured.append(json)
        return FakeResponse()

    class FakeConnectionError(Exception):
        pass

    fake_module.post = fake_post
    fake_module.ConnectionError = FakeConnectionError
    monkeypatch.setitem(sys.modules, "requests", fake_module)


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
        # The map is the speaker's own offline (Piper) voice from section 6
        # -- a separate setting since Step 25c, not the edge-tts voice map.
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

    def test_reports_when_piper_actually_rendered_it(self, monkeypatch, tmp_path):
        """Step 29 bug 2: callers need to know which engine actually
        produced the audio, not just which one was intended -- see
        _piper_fallback_path."""
        _install_fake_edge_tts(monkeypatch, save_exception=Exception("HTTP 403"))
        monkeypatch.setitem(sys.modules, "piper", types.ModuleType("piper"))
        monkeypatch.setattr(dub, "synthesize_line_offline",
                             lambda text, voice, out_path: open(out_path, "w").close())

        used_piper = dub._synthesize_edge_tts_with_piper_fallback(
            "hello", "en-US-AvaNeural", str(tmp_path / "out.wav"), {}, "SPEAKER_00")

        assert used_piper is True

    def test_reports_false_when_edge_tts_itself_succeeded(self, monkeypatch, tmp_path):
        _install_fake_edge_tts(monkeypatch)
        used_piper = dub._synthesize_edge_tts_with_piper_fallback(
            "hello", "en-US-AvaNeural", str(tmp_path / "out.wav"), {}, "SPEAKER_00")
        assert used_piper is False


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


class TestFillMissingVoices:
    """Step 25g item 4: the fallback both `cli.py dub` and Workspace
    section 8 use for characters with no voice picked."""

    def test_unvoiced_speakers_get_distinct_voices(self):
        result = dub.fill_missing_voices({}, {"A", "B"}, voice_pool=["v1", "v2", "v3"])
        assert result == {"A": "v1", "B": "v2"}

    def test_picked_voices_are_kept_and_not_reused_while_the_pool_allows(self):
        result = dub.fill_missing_voices({"A": "v1"}, {"A", "B", "C"}, voice_pool=["v1", "v2", "v3"])
        assert result == {"A": "v1", "B": "v2", "C": "v3"}

    def test_falls_back_to_the_whole_pool_once_it_is_used_up(self):
        result = dub.fill_missing_voices({"A": "v1"}, {"A", "B"}, voice_pool=["v1"])
        assert result == {"A": "v1", "B": "v1"}


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


class TestNarrateOriginalLanguage:
    """Step 26c: novel narration can speak the drama's own source text
    (narrate_original) instead of always speaking the translation."""

    def test_original_mode_speaks_source_text_not_translation(self, timed, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="你好", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), {}, narrate_original=True)
        assert timed.synth == ["你好"]

    def test_translation_mode_still_speaks_the_translation_by_default(self, timed, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="你好", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), {})
        assert timed.synth == ["Hello"]

    def test_original_mode_generates_audio_with_no_translation_at_all(self, timed, tmp_path):
        """Exit condition: narration still works with no ln.en in original
        mode -- only the exported bilingual subtitle needs it (warned
        about at the UI level, see tabs/workspace_tab.py)."""
        lines = [Line(idx=0, start=0, end=0, zh="你好世界", en="", speaker="A")]
        out_path, errors = dub.build_narration_track(lines, str(tmp_path), {}, narrate_original=True)
        assert timed.synth == ["你好世界"]
        assert errors == []
        assert lines[0].dub_filename is not None

    def test_a_line_with_no_source_text_is_still_treated_as_blank_in_original_mode(self, timed, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), {}, narrate_original=True)
        assert timed.synth == []
        assert lines[0].start == lines[0].end == 0.0

    def test_switching_narration_language_changes_the_clip_cache_signature_even_with_identical_text(
            self, timed, tmp_path):
        """Isolates the signature's explicit language marker: even when the
        source and translated text happen to be identical (e.g. a proper
        noun), switching narration language must still regenerate the
        clip rather than silently reusing the other mode's cached audio."""
        lines_translation = [Line(idx=0, start=0, end=0, zh="Amy", en="Amy", speaker="A")]
        dub.build_narration_track(lines_translation, str(tmp_path), {})
        translation_clip = lines_translation[0].dub_filename

        lines_original = [Line(idx=0, start=0, end=0, zh="Amy", en="Amy", speaker="A")]
        dub.build_narration_track(lines_original, str(tmp_path), {}, narrate_original=True,
                                  source_language="zh")
        original_clip = lines_original[0].dub_filename

        assert translation_clip != original_clip
        assert len(timed.synth) == 2  # both actually (re)synthesized, neither reused the other

    def test_original_mode_splits_unit_timing_by_the_source_text_length(self, monkeypatch, tmp_path):
        """A unit joining two same-speaker lines splits its one clip's time
        proportionally by whichever text was actually spoken."""
        _install_fake_pydub(monkeypatch, clip_lengths={
            str(tmp_path / "dub_clips" / "line_0000-0001.wav"): 1000,
        })
        monkeypatch.setattr(dub, "synthesize_line",
                             lambda text, voice, out_path, **kwargs: open(out_path, "w").close())
        # Source text lengths are 1:3 -- very different from the (unused)
        # English lengths, so a pass that still split by ln.en would fail.
        lines = [Line(idx=0, start=0, end=0, zh="a", en="Same length", speaker="A"),
                 Line(idx=1, start=0, end=0, zh="bbb", en="Same length", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), {}, narrate_original=True, source_language="zh")
        assert lines[0].end < lines[1].end
        # roughly 1/4 vs 3/4 of the clip -- not a 50/50 split
        assert lines[0].end < 0.4

    def test_original_mode_reaches_gpt_sovits_with_the_drama_source_language(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        captured = []
        _install_fake_requests(monkeypatch, captured)
        clone_map = {"A": {"engine": "gpt_sovits", "ref_audio": "/ref.wav", "ref_text": "hi",
                           "ref_language": "zh"}}
        lines = [Line(idx=0, start=0, end=0, zh="你好", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), {}, character_clone_map=clone_map,
                                  narrate_original=True, source_language="zh")
        assert captured[0]["text"] == "你好"
        assert captured[0]["text_lang"] == "zh"

    def test_translation_mode_still_reaches_gpt_sovits_with_english(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        captured = []
        _install_fake_requests(monkeypatch, captured)
        clone_map = {"A": {"engine": "gpt_sovits", "ref_audio": "/ref.wav", "ref_text": "hi",
                           "ref_language": "zh"}}
        lines = [Line(idx=0, start=0, end=0, zh="你好", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), {}, character_clone_map=clone_map)
        assert captured[0]["text"] == "Hello"
        assert captured[0]["text_lang"] == "en"

    def test_original_mode_narration_lines_still_export_bilingual_subtitles(self, timed, tmp_path):
        import subtitle_formats
        lines = [Line(idx=0, start=0, end=0, zh="你好", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), {}, narrate_original=True, source_language="zh")
        vtt = subtitle_formats.lines_to_vtt(lines, field="bilingual")
        assert "Hello" in vtt
        assert "你好" in vtt


class TestNarrationChaptersOriginalLanguage:
    def test_original_mode_uses_source_text_for_titles_and_voiced_filter(self):
        lines = [Line(idx=0, start=0.0, end=1.0, zh="第一章", en="", speaker="N"),
                 Line(idx=1, start=1.0, end=2.0, zh="正文内容", en="Body text", speaker="N")]
        chapters = dub.narration_chapters(lines, narrate_original=True)
        assert chapters[0][1] == "第一章"

    def test_translation_mode_is_unaffected(self):
        lines = [Line(idx=0, start=0.0, end=1.0, zh="第一章", en="Chapter One", speaker="N")]
        chapters = dub.narration_chapters(lines)
        assert chapters[0][1] == "Chapter One"


class TestGPTSoVITSTextLang:
    """Step 26c: text_lang (the language of the text being spoken) used to
    be hardcoded "en" -- now driven by narration mode."""

    def test_defaults_to_english(self, tmp_path, monkeypatch):
        captured = []
        _install_fake_requests(monkeypatch, captured)
        dub.synthesize_line_gpt_sovits("Hello", "/ref.wav", "ref text", str(tmp_path / "out.wav"))
        assert captured[0]["text_lang"] == "en"

    def test_an_explicit_text_lang_is_mapped_to_gpt_sovits_own_codes(self, tmp_path, monkeypatch):
        captured = []
        _install_fake_requests(monkeypatch, captured)
        dub.synthesize_line_gpt_sovits("你好", "/ref.wav", "ref text", str(tmp_path / "out.wav"),
                                       text_lang="zh")
        assert captured[0]["text_lang"] == "zh"

    def test_an_unrecognized_text_lang_falls_back_to_english(self, tmp_path, monkeypatch):
        captured = []
        _install_fake_requests(monkeypatch, captured)
        dub.synthesize_line_gpt_sovits("Hello", "/ref.wav", "ref text", str(tmp_path / "out.wav"),
                                       text_lang="fr")
        assert captured[0]["text_lang"] == "en"

    def test_synthesize_cloned_threads_text_lang_through_without_touching_ref_language(
            self, tmp_path, monkeypatch):
        captured = []
        _install_fake_requests(monkeypatch, captured)
        clone = {"engine": "gpt_sovits", "ref_audio": "/ref.wav", "ref_text": "hi", "ref_language": "ja"}
        dub._synthesize_cloned(clone, "こんにちは", str(tmp_path / "out.wav"), text_lang="ja")
        assert captured[0]["text_lang"] == "ja"
        assert captured[0]["prompt_lang"] == "ja"  # the reference clip's own language, unaffected

    def test_synthesize_cloned_defaults_text_lang_to_english(self, tmp_path, monkeypatch):
        """build_dub_track's own call site never passes text_lang -- video
        dubbing always speaks the translation (Step 26c item 5)."""
        captured = []
        _install_fake_requests(monkeypatch, captured)
        clone = {"engine": "gpt_sovits", "ref_audio": "/ref.wav", "ref_text": "hi", "ref_language": "zh"}
        dub._synthesize_cloned(clone, "Hello", str(tmp_path / "out.wav"))
        assert captured[0]["text_lang"] == "en"


class TestCloneEngineOriginalLanguages:
    """Step 26c: which of the multi-engine clone backends are confirmed to
    speak zh/ja/ko well, gating novel narration's original-language mode
    in the Workspace character-engine picker."""

    def test_omnivoice_supports_all_three(self):
        for lang in ("zh", "ja", "ko"):
            assert dub.clone_engine_supports_language("omnivoice", lang)

    def test_tada_supports_zh_and_ja_but_not_ko(self):
        assert dub.clone_engine_supports_language("tada", "zh")
        assert dub.clone_engine_supports_language("tada", "ja")
        assert not dub.clone_engine_supports_language("tada", "ko")

    def test_chatterbox_is_not_confirmed_for_any_of_them(self):
        for lang in ("zh", "ja", "ko"):
            assert not dub.clone_engine_supports_language("chatterbox", lang)

    def test_engines_outside_the_table_are_unrestricted(self):
        # F5-TTS and GPT-SoVITS are already language-aware on their own
        # terms (ref_language/text_lang) -- not gated by this table.
        assert dub.clone_engine_supports_language("f5tts", "ko")
        assert dub.clone_engine_supports_language("gpt_sovits", "ko")


class TestExtractReferenceClips:
    def test_picks_the_clip_closest_to_six_seconds_per_speaker(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [
            {"speaker": "A", "start": 0.0, "end": 4.0},   # 4s -- 2s from target
            {"speaker": "A", "start": 10.0, "end": 16.0},  # 6s -- exact target, should win
            {"speaker": "A", "start": 20.0, "end": 30.0},  # 10s -- 4s from target
        ]
        clips, skipped = dub.extract_reference_clips("/fake/audio.wav", speaker_segments, str(tmp_path))
        assert clips["A"]["start"] == 10.0
        assert clips["A"]["end"] == 16.0
        assert skipped == {}

    def test_ignores_clips_outside_the_duration_bounds(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [
            {"speaker": "A", "start": 0.0, "end": 1.0},    # too short
            {"speaker": "A", "start": 5.0, "end": 25.0},   # too long
        ]
        clips, skipped = dub.extract_reference_clips("/fake/audio.wav", speaker_segments, str(tmp_path))
        assert "A" not in clips

    def test_each_speaker_gets_their_own_best_clip(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [
            {"speaker": "A", "start": 0.0, "end": 6.0},
            {"speaker": "B", "start": 10.0, "end": 15.0},
        ]
        clips, skipped = dub.extract_reference_clips("/fake/audio.wav", speaker_segments, str(tmp_path))
        assert set(clips.keys()) == {"A", "B"}
        assert skipped == {}

    def test_reports_a_specific_reason_for_a_speaker_with_no_eligible_segment(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [
            {"speaker": "A", "start": 0.0, "end": 6.0},     # in range
            {"speaker": "B", "start": 10.0, "end": 11.5},   # 1.5s -- too short, closest to the window
            {"speaker": "B", "start": 20.0, "end": 21.0},   # 1.0s -- also too short but farther
        ]
        clips, skipped = dub.extract_reference_clips("/fake/audio.wav", speaker_segments, str(tmp_path))
        assert "B" not in clips
        assert skipped == {"B": {"closest_duration": 1.5, "reason": "too_short"}}

    def test_reports_too_long_when_the_closest_segment_exceeds_the_max(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        speaker_segments = [{"speaker": "A", "start": 0.0, "end": 20.0}]  # 20s -- too long
        clips, skipped = dub.extract_reference_clips("/fake/audio.wav", speaker_segments, str(tmp_path))
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
            {}, "edge_tts", False, {}, 1.4, 0.85, None, result_queue)
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
            lines, str(tmp_path), {}, "en-US-AvaNeural", {}, "edge_tts", True, {}, 1.4, 0.85, None, result_queue)
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
            lines, str(tmp_path), {}, "en-US-AvaNeural", {}, "edge_tts", False, {}, 1.4, 0.85, None, result_queue)
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

    def test_a_kill_after_partial_bytes_still_leaves_no_corrupted_cache_file(
            self, timed, monkeypatch, tmp_path):
        """Not the test above (which raises before any file exists at all)
        -- this covers the real Step 29 bug shape: the synth call has
        already written some bytes (e.g. a wave/soundfile placeholder
        header) to its out_path before being killed. Before the fix, that
        landed straight on clip_path and survived as a corrupted-but-
        loadable clip; now it can only ever land on a .partial.wav."""
        fixture_synth = dub.synthesize_line  # the timed fixture's fake, restored below

        def killed_after_writing_a_header(text, voice, out_path):
            with open(out_path, "w") as f:
                f.write("only-a-header")
            raise _Killed()
        monkeypatch.setattr(dub, "synthesize_line", killed_after_writing_a_header)

        lines = [_line(0, "Hello")]
        with pytest.raises(_Killed):
            dub.build_dub_track(lines, str(tmp_path), {})

        clips_dir = tmp_path / "dub_clips"
        assert all(f.endswith(".partial.wav") for f in os.listdir(clips_dir))

        monkeypatch.setattr(dub, "synthesize_line", fixture_synth)
        fresh = [_line(0, "Hello")]
        _, errors = dub.build_dub_track(fresh, str(tmp_path), {})
        assert errors == []
        assert timed.synth == ["Hello"]  # retried for real, not reused as the corrupted clip
        assert fresh[0].dub_filename is not None

    def test_an_edited_narration_unit_is_re_voiced_too(self, timed, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="x", en="Helo.", speaker="N")]
        dub.build_narration_track(lines, str(tmp_path), {})
        lines[0].en = "Hello."
        dub.build_narration_track(lines, str(tmp_path), {})
        assert timed.synth == ["Helo.", "Hello."]
        dub.build_narration_track(lines, str(tmp_path), {})
        assert timed.synth == ["Helo.", "Hello."]  # unchanged -- reused


class TestPiperFallbackClipCaching:
    """Step 29 bug 2: a clip actually rendered by the Piper fallback must
    be cached under a path distinct from its edge-tts signature, so a
    later run retries edge-tts instead of reusing the stale Piper audio
    forever once the block lifts."""

    def test_dub_track_retries_edge_tts_once_it_recovers(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        _install_fake_edge_tts(monkeypatch, save_exception=Exception("HTTP 403"))
        monkeypatch.setitem(sys.modules, "piper", types.ModuleType("piper"))
        monkeypatch.setattr(dub, "synthesize_line_offline",
                             lambda text, voice, out_path: open(out_path, "w").close())

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        _, errors = dub.build_dub_track(lines, str(tmp_path), {})
        assert errors == []
        piper_clip = lines[0].dub_filename
        assert piper_clip.endswith(".piper_fallback.wav")

        # edge-tts recovers -- must be retried, not served the stale Piper clip.
        _install_fake_edge_tts(monkeypatch)  # no save_exception this time
        fresh = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        _, errors2 = dub.build_dub_track(fresh, str(tmp_path), {})
        assert errors2 == []
        assert not fresh[0].dub_filename.endswith(".piper_fallback.wav")
        assert fresh[0].dub_filename != piper_clip
        # the old Piper clip is simply left behind, not deleted or reused
        assert os.path.exists(os.path.join(str(tmp_path), piper_clip))

    def test_narration_track_retries_edge_tts_once_it_recovers(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        _install_fake_edge_tts(monkeypatch, save_exception=Exception("HTTP 403"))
        monkeypatch.setitem(sys.modules, "piper", types.ModuleType("piper"))
        monkeypatch.setattr(dub, "synthesize_line_offline",
                             lambda text, voice, out_path: open(out_path, "w").close())

        lines = [Line(idx=0, start=0, end=0, zh="x", en="Hello there.", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), {})
        piper_clip = lines[0].dub_filename
        assert piper_clip.endswith(".piper_fallback.wav")

        _install_fake_edge_tts(monkeypatch)
        fresh = [Line(idx=0, start=0, end=0, zh="x", en="Hello there.", speaker="A")]
        dub.build_narration_track(fresh, str(tmp_path), {})
        assert not fresh[0].dub_filename.endswith(".piper_fallback.wav")


class TestHostedCloningRemoved:
    """Step 11d: nothing references the removed hosted cloning engine
    except the characters.elevenlabs_voice_id column (kept on purpose, as
    the record of how existing audio was made) and the message shown to a
    character that was cloned with it."""

    ALLOWED = ("elevenlabs_voice_id", "previously cloned via ElevenLabs")
    SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", "node_modules", ".claude", "library"}

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


# ---------------------------------------------------------------------------
# Step 25c item 1: the offline/Piper path, against both real piper-tts APIs
# ---------------------------------------------------------------------------

def _install_fake_piper(monkeypatch, api="1.3+"):
    """Fakes piper-tts at its import boundary in the shape each real
    release has: 1.3+ (piper.download_voices.download_voice, and
    PiperVoice.synthesize_wav(text, wave_writer)) or 1.2
    (piper.download.get_voices/ensure_voice_exists, and
    PiperVoice.synthesize(text, wave_writer)). PiperVoice.load only
    accepts a model path that really exists on disk, like the real one."""
    record = {"loaded": [], "downloaded": [], "synthesized": []}

    def write_model(voice, download_dir):
        record["downloaded"].append((voice, str(download_dir)))
        for ext in (".onnx", ".onnx.json"):
            with open(os.path.join(str(download_dir), voice + ext), "w") as f:
                f.write("model")

    class FakePiperVoice:
        @staticmethod
        def load(model_path, config_path=None, use_cuda=False):
            if not os.path.exists(model_path) or not os.path.exists(f"{model_path}.json"):
                raise FileNotFoundError(model_path)
            record["loaded"].append(model_path)
            return FakePiperVoice()

        def _write(self, text, wav_file):
            wav_file.setframerate(22050)
            wav_file.setsampwidth(2)
            wav_file.setnchannels(1)
            wav_file.writeframes(b"\x00\x00" * 10)
            record["synthesized"].append(text)

    piper_mod = types.ModuleType("piper")
    if api == "1.3+":
        FakePiperVoice.synthesize_wav = FakePiperVoice._write
        dv = types.ModuleType("piper.download_voices")
        dv.download_voice = lambda voice, download_dir, force_redownload=False: write_model(
            voice, download_dir)
        monkeypatch.setitem(sys.modules, "piper.download_voices", dv)
        monkeypatch.delitem(sys.modules, "piper.download", raising=False)
    else:
        FakePiperVoice.synthesize = FakePiperVoice._write
        dl = types.ModuleType("piper.download")
        dl.get_voices = lambda download_dir, update_voices=False: {}
        dl.ensure_voice_exists = lambda name, data_dirs, download_dir, voices_info: write_model(
            name, download_dir)
        monkeypatch.setitem(sys.modules, "piper.download", dl)
        monkeypatch.setitem(sys.modules, "piper.download_voices", None)  # 1.2 has no such module
    piper_mod.PiperVoice = FakePiperVoice
    monkeypatch.setitem(sys.modules, "piper", piper_mod)
    monkeypatch.setattr(dub, "_piper_voices", {})
    return record


class TestSynthesizeLineOffline:
    @pytest.mark.parametrize("api", ["1.3+", "1.2"])
    def test_downloads_the_model_then_writes_a_real_wav(self, monkeypatch, tmp_path, api):
        import wave
        monkeypatch.setattr(dub, "piper_voices_dir", lambda: str(tmp_path / "voices"))
        record = _install_fake_piper(monkeypatch, api)

        out_path = str(tmp_path / "out.wav")
        dub.synthesize_line_offline("hello", "en_US-lessac-medium", out_path)

        model = str(tmp_path / "voices" / "en_US-lessac-medium.onnx")
        assert record["downloaded"] == [("en_US-lessac-medium", str(tmp_path / "voices"))]
        assert record["loaded"] == [model]
        with wave.open(out_path, "rb") as wav:
            assert wav.getframerate() == 22050 and wav.getnframes() == 10

    def test_an_already_downloaded_model_is_not_downloaded_again(self, monkeypatch, tmp_path):
        voices = tmp_path / "voices"
        voices.mkdir()
        (voices / "en_US-amy-medium.onnx").write_text("m")
        (voices / "en_US-amy-medium.onnx.json").write_text("{}")
        monkeypatch.setattr(dub, "piper_voices_dir", lambda: str(voices))
        record = _install_fake_piper(monkeypatch)

        dub.synthesize_line_offline("hi", "en_US-amy-medium", str(tmp_path / "out.wav"))

        assert record["downloaded"] == []
        assert record["loaded"] == [str(voices / "en_US-amy-medium.onnx")]

    def test_an_edge_tts_voice_name_is_refused_with_a_clear_message(self, monkeypatch, tmp_path):
        monkeypatch.setattr(dub, "piper_voices_dir", lambda: str(tmp_path / "voices"))
        record = _install_fake_piper(monkeypatch)
        with pytest.raises(ValueError, match="isn't a Piper voice name"):
            dub.synthesize_line_offline("hi", "en-US-AvaNeural", str(tmp_path / "out.wav"))
        assert record["downloaded"] == [] and record["loaded"] == []


class TestOfflineEngineUsesOfflineVoices:
    """The real bug: section 6 only saved edge-tts names (tts_voice), and
    the offline engine / edge->Piper fallback read that same map, so Piper
    was asked to load 'en-US-AvaNeural'."""

    def test_offline_dub_calls_piper_with_a_resolvable_model_not_the_edge_voice(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        monkeypatch.setattr(dub, "piper_voices_dir", lambda: str(tmp_path / "voices"))
        record = _install_fake_piper(monkeypatch)

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A"),
                 Line(idx=1, start=1, end=2, zh="y", en="Bye", speaker="B")]
        _, errors = dub.build_dub_track(
            lines, str(tmp_path), {"A": "en-US-AvaNeural", "B": "en-GB-SoniaNeural"},
            tts_engine="offline", offline_voice_map={"A": "en_GB-alba-medium"})

        assert errors == []
        assert record["loaded"] == [
            str(tmp_path / "voices" / "en_GB-alba-medium.onnx"),
            str(tmp_path / "voices" / f"{dub.DEFAULT_OFFLINE_VOICE_POOL[0]}.onnx"),
        ]
        assert record["synthesized"] == ["Hello", "Bye"]

    def test_offline_narration_uses_the_offline_voice_too(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        voices = []
        monkeypatch.setattr(dub, "synthesize_line_offline",
                             lambda text, voice, out_path: voices.append(voice) or open(out_path, "w").close())

        lines = [Line(idx=0, start=0, end=0, zh="x", en="Once upon a time.", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), {"A": "en-US-AvaNeural"},
                                  tts_engine="offline", offline_voice_map={"A": "en_US-amy-medium"})

        assert voices == ["en_US-amy-medium"]

    def test_edge_fallback_never_hands_piper_the_edge_voice(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        _install_fake_edge_tts(monkeypatch, save_exception=Exception("HTTP 403"))
        monkeypatch.setitem(sys.modules, "piper", types.ModuleType("piper"))
        voices = []
        monkeypatch.setattr(dub, "synthesize_line_offline",
                             lambda text, voice, out_path: voices.append(voice) or open(out_path, "w").close())

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        _, errors = dub.build_dub_track(lines, str(tmp_path), {"A": "en-US-AvaNeural"})

        assert errors == []
        assert voices == [dub.DEFAULT_OFFLINE_VOICE_POOL[0]]


def test_offline_voice_is_saved_separately_from_the_edge_voice(isolated_db):
    did = isolated_db.create_drama(title_en="t")
    isolated_db.upsert_character(did, "A", tts_voice="en-US-AvaNeural")
    isolated_db.upsert_character(did, "A", offline_voice="en_GB-alba-medium")
    (c,) = isolated_db.list_characters(did)
    assert (c["tts_voice"], c["offline_voice"]) == ("en-US-AvaNeural", "en_GB-alba-medium")


class TestDubWorkerArgumentBinding:
    """background_jobs appends result_queue as the LAST positional argument
    (`args=(*args, result_queue)`), but build_track_subprocess_worker
    declares result_queue right after offline_voice_map, before its
    keyword-default parameters. The Streamlit tab used to pass
    narrate_original/source_language positionally, which put the real queue
    in the wrong slot and broke dub generation from the tab (Step 26c
    onward). Callers must bind those two by keyword (functools.partial)."""

    def test_queue_lands_in_result_queue_when_extras_are_bound_by_keyword(self):
        import functools
        import inspect
        queue = object()
        bound = functools.partial(dub.build_track_subprocess_worker,
                                  narrate_original=True, source_language="ja")
        positional = ([], "/d", {}, "v", {}, "edge_tts", False, {}, 1.4, 0.85, None)
        call = inspect.signature(bound).bind(*positional, queue)
        call.apply_defaults()  # partial-bound keywords show up as defaults
        assert call.arguments["result_queue"] is queue
        assert call.arguments["narrate_original"] is True
        assert call.arguments["source_language"] == "ja"

