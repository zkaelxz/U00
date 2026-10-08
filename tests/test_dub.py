"""
tests/test_dub.py -- dub.py: voice routing per speaker (OmniVoice), dub-track
and narration-track assembly, the removed Edge TTS / Piper / F5-TTS / TADA /
Chatterbox / GPT-SoVITS engines and the clip cache.

pydub and the engines' packages aren't installed in this sandbox (no
ffmpeg-backed audio library, no network, no GPU) -- each is faked at its
own import boundary, the same way other tests here fake faster_whisper or
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


VOICE = {"engine": "omnivoice", "instruct": "female, young adult, moderate pitch"}
# Every speaker label these tests use (None: a line with no speaker) gets a voice.
ALL = {label: dict(VOICE) for label in ("A", "B", "N", None)}


def _fake_omnivoice(calls=None, fail_on=None):
    """Stands in for synthesize_line_omnivoice: records (text, instruct),
    raises for the text `fail_on`, otherwise writes an empty clip."""
    def synth(text, out_path, ref_audio_path=None, ref_text=None, instruct=None):
        if calls is not None:
            calls.append((text, instruct))
        if fail_on is not None and text == fail_on:
            raise RuntimeError("TTS down")
        open(out_path, "w").close()
    return synth


class TestRemovedEngines:
    """Edge TTS, Piper, F5-TTS, TADA, Chatterbox and GPT-SoVITS were removed:
    nothing of them is left to
    call, and anything stored under their names is refused in plain words,
    never silently handed to another engine."""

    @pytest.mark.parametrize("name", ["synthesize_line", "edge_tts_synthesize", "EdgeTTSBlockedError",
                                      "synthesize_line_offline", "piper_voices_dir", "piper_model_path",
                                      "synthesize_line_cloned", "DEFAULT_VOICE_POOL",
                                      "DEFAULT_OFFLINE_VOICE_POOL", "PARALLEL_SAFE_ENGINES",
                                      "synthesize_line_tada", "synthesize_line_chatterbox",
                                      "synthesize_line_gpt_sovits", "_get_tada", "_get_chatterbox",
                                      "GPT_SOVITS_DEFAULT_URL", "TADA_MODEL_ID", "speakers_without_voice"])
    def test_their_code_is_gone(self, name):
        assert not hasattr(dub, name)

    @pytest.mark.parametrize("key,label", [("edge_tts", "Edge TTS"), ("offline", "Piper"),
                                           ("f5tts", "F5-TTS"), ("f5", "F5-TTS"), ("tada", "TADA"),
                                           ("chatterbox", "Chatterbox"), ("gpt_sovits", "GPT-SoVITS")])
    def test_a_stored_key_gets_the_plain_removal_message(self, key, label):
        assert dub.removed_engine_message(key) == (
            f"The {label} engine was removed. Pick another voice engine in Dub.")
        assert dub.engine_refusal(key) == dub.removed_engine_message(key)

    def test_remaining_and_unknown_engines(self):
        for engine in dub.CLONE_ENGINES:
            assert dub.engine_refusal(engine) is None and dub.removed_engine_message(engine) is None
        assert dub.engine_refusal("nope") == "Unknown voice engine."
        assert set(dub.CLONE_ENGINES) == {"omnivoice"}
        assert dub.DEFAULT_CLONE_ENGINE == "omnivoice"

    @pytest.mark.parametrize("key", ["edge_tts", "offline", "f5tts", "tada", "chatterbox", "gpt_sovits"])
    def test_routing_a_removed_engine_entry_raises_the_message_not_a_traceback(self, key, tmp_path):
        with pytest.raises(RuntimeError, match="was removed. Pick another voice engine in Dub"):
            dub._synthesize_cloned({"engine": key, "ref_audio": "/r.wav", "ref_text": "x"}, "Hi",
                                   str(tmp_path / "o.wav"))

    def test_an_entry_with_no_engine_is_refused_not_sent_to_f5(self, tmp_path):
        with pytest.raises(RuntimeError, match="Unknown voice engine"):
            dub._synthesize_cloned({"ref_audio": "/r.wav", "ref_text": "x"}, "Hi", str(tmp_path / "o.wav"))

    def test_a_package_that_will_not_load_reads_as_a_plain_sentence(self, monkeypatch, tmp_path):
        def broken(*a, **k):
            raise ImportError("cannot import name 'x' from 'transformers'")
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", broken)
        with pytest.raises(RuntimeError) as err:
            dub._synthesize_cloned(dict(VOICE), "Hi", str(tmp_path / "o.wav"))
        assert str(err.value) == "The OmniVoice engine could not start. Check it in Diagnostics."

    def test_engine_blockers_name_the_run_engine_and_each_character(self):
        chars = [{"speaker_label": "A", "character_name": "Lin", "clone_engine": "f5tts"},
                 {"speaker_label": "B", "character_name": "", "clone_engine": "omnivoice"},
                 {"speaker_label": "C", "character_name": "", "clone_engine": None}]
        assert dub.engine_blockers(chars, "omnivoice") == [
            "Lin: The F5-TTS engine was removed. Pick another voice engine in Dub."]
        assert dub.engine_blockers(chars, "edge_tts")[0] == (
            "The Edge TTS engine was removed. Pick another voice engine in Dub.")
        assert dub.engine_blockers([], "omnivoice") == []

    def test_a_removed_engine_character_gets_no_clone_entry(self, tmp_path):
        chars = [{"speaker_label": "A", "clone_engine": "f5tts", "ref_audio_filename": "a.wav"}]
        assert dub.clone_map_from_characters(chars, str(tmp_path)) == {}


class TestFallbackVoices:
    """A speaker with no clip or description of their own is voiced by the
    engine picked for the run, never by a removed stock voice."""

    def test_omnivoice_designs_a_distinct_voice_per_speaker(self, tmp_path):
        out = dub.clone_map_from_characters([], str(tmp_path), default_engine="omnivoice",
                                            speaker_labels=["B", "A", None])
        assert {k: v["engine"] for k, v in out.items()} == {None: "omnivoice", "A": "omnivoice",
                                                            "B": "omnivoice"}
        assert len({v["instruct"] for v in out.values()}) == 3
        assert out[None]["instruct"] == dub.DEFAULT_VOICE_DESCRIPTIONS[0]  # unlabelled sorts first

    def test_a_described_voice_is_not_reused_for_a_fallback_speaker(self, tmp_path):
        chars = [{"speaker_label": "A", "voice_design": dub.DEFAULT_VOICE_DESCRIPTIONS[0]}]
        out = dub.clone_map_from_characters(chars, str(tmp_path), speaker_labels=["A", "B"])
        assert out["A"]["instruct"] == dub.DEFAULT_VOICE_DESCRIPTIONS[0]
        assert out["B"]["instruct"] == dub.DEFAULT_VOICE_DESCRIPTIONS[1]

    def test_the_pool_wraps_when_there_are_more_speakers_than_voices(self, tmp_path):
        labels = [f"S{i}" for i in range(len(dub.DEFAULT_VOICE_DESCRIPTIONS) + 1)]
        out = dub.clone_map_from_characters([], str(tmp_path), speaker_labels=labels)
        assert len(out) == len(labels)

    @pytest.mark.parametrize("engine", ["tada", "chatterbox", "gpt_sovits"])
    def test_a_removed_run_engine_voices_nobody(self, engine, tmp_path):
        chars = [{"speaker_label": "A", "ref_audio_filename": "a.wav", "ref_text": "hi"}]
        assert dub.clone_map_from_characters(chars, str(tmp_path), default_engine=engine,
                                             speaker_labels=["A", "B", None]) == {}

    def test_a_character_without_its_own_engine_follows_the_runs_engine(self, tmp_path):
        chars = [{"speaker_label": "A", "ref_audio_filename": "a.wav", "ref_text": "hi"},
                 {"speaker_label": "B", "ref_audio_filename": "b.wav", "clone_engine": "tada"}]
        out = dub.clone_map_from_characters(chars, str(tmp_path), default_engine="omnivoice")
        assert out["A"]["engine"] == "omnivoice"
        assert "B" not in out  # its stored engine was removed; engine_blockers refuses the run


class TestBuildDubTrackVoiceRouting:
    """The core routing logic in both build_dub_track and
    build_narration_track: each speaker is voiced by their own clone-map
    entry, and one line's synthesis failure must never lose every other
    line's already-generated audio."""

    def test_each_speaker_uses_their_own_entry(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        calls = []
        monkeypatch.setattr(dub, "synthesize_line_omnivoice",
                            lambda text, out_path, ref_audio_path=None, ref_text=None, instruct=None:
                            calls.append(("omnivoice", text, ref_audio_path, ref_text, instruct))
                            or open(out_path, "w").close())

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A"),
                 Line(idx=1, start=1, end=2, zh="y", en="Bye", speaker="B")]
        _, errors = dub.build_dub_track(lines, str(tmp_path), {
            "A": {"engine": "omnivoice", "ref_audio": "/refs/a.wav", "ref_text": "你好"},
            "B": {"engine": "omnivoice", "instruct": "male, low pitch"}})

        assert errors == []
        assert calls == [("omnivoice", "Hello", "/refs/a.wav", "你好", None),
                         ("omnivoice", "Bye", None, None, "male, low pitch")]

    def test_a_speaker_with_no_entry_stays_silent_with_a_plain_error(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        calls = []
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", _fake_omnivoice(calls))
        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A"),
                 Line(idx=1, start=1, end=2, zh="y", en="Bye", speaker="B")]
        _, errors = dub.build_dub_track(lines, str(tmp_path), {"A": dict(VOICE)})
        assert calls == [("Hello", VOICE["instruct"])]
        assert errors == [{"line_idx": 1, "error": dub.NO_VOICE_ERROR}]
        assert lines[1].dub_filename is None

    def test_a_failed_line_does_not_lose_other_lines_audio(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", _fake_omnivoice(fail_on="fails"))

        lines = [Line(idx=0, start=0, end=1, zh="x", en="ok one", speaker="A"),
                 Line(idx=1, start=1, end=2, zh="y", en="fails", speaker="A"),
                 Line(idx=2, start=2, end=3, zh="z", en="ok two", speaker="A")]
        out_path, errors = dub.build_dub_track(lines, str(tmp_path), ALL)

        assert len(errors) == 1
        assert errors[0]["line_idx"] == 1
        # the two successful lines still got a dub_filename recorded
        assert lines[0].dub_filename is not None
        assert lines[1].dub_filename is None
        assert lines[2].dub_filename is not None

    def test_lines_with_no_translation_are_skipped_silently(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        calls = []
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", _fake_omnivoice(calls))

        lines = [Line(idx=0, start=0, end=1, zh="x", en="", speaker="A")]
        out_path, errors = dub.build_dub_track(lines, str(tmp_path), ALL)

        assert calls == []
        assert errors == []

    def test_already_generated_clips_are_reused_not_resynthesized(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        clips_dir = tmp_path / "dub_clips"
        clips_dir.mkdir()
        name = f"line_0000_{dub.clip_signature('Hello', VOICE)}.wav"
        (clips_dir / name).write_text("already here")

        calls = []
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", _fake_omnivoice(calls))

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        dub.build_dub_track(lines, str(tmp_path), ALL)

        assert calls == []  # never re-synthesized
        assert lines[0].dub_filename == os.path.join("dub_clips", name)


class TestBuildNarrationTrack:
    def test_concatenates_clips_with_a_gap_and_sets_line_timing(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch, clip_lengths={
            str(tmp_path / "dub_clips" / "line_0000.wav"): 2000,
            str(tmp_path / "dub_clips" / "line_0001.wav"): 3000,
        })
        monkeypatch.setattr(dub, "synthesize_line_omnivoice",
                             lambda text, out_path, **kwargs: open(out_path, "w").close())

        # Different speakers, so each line is its own TTS call (same-speaker
        # lines would share one -- see TestNarrationTTSUnits).
        lines = [Line(idx=0, start=0, end=0, zh="x", en="First", speaker="A"),
                 Line(idx=1, start=0, end=0, zh="y", en="Second", speaker="B")]
        dub.build_narration_track(lines, str(tmp_path), ALL, gap_ms=350)

        assert lines[0].start == 0.0
        assert lines[0].end == 2.0
        assert lines[1].start == 2.35        # 2000ms clip + 350ms gap
        assert lines[1].end == 5.35

    def test_a_blank_line_advances_the_cursor_by_nothing_and_records_a_point_in_time(self, tmp_path, monkeypatch):
        _install_fake_pydub(monkeypatch)
        lines = [Line(idx=0, start=0, end=0, zh="x", en="")]
        dub.build_narration_track(lines, str(tmp_path), ALL)
        assert lines[0].start == lines[0].end == 0.0

    def test_a_failed_line_still_advances_the_timeline_with_a_silent_gap(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        monkeypatch.setattr(dub, "synthesize_line_omnivoice",
                             lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))

        lines = [Line(idx=0, start=0, end=0, zh="x", en="fails")]
        out_path, errors = dub.build_narration_track(lines, str(tmp_path), ALL, gap_ms=350)

        assert len(errors) == 1
        assert lines[0].end == pytest.approx(0.35)


class TestNarrateOriginalLanguage:
    """Step 26c: novel narration can speak the drama's own source text
    (narrate_original) instead of always speaking the translation."""

    def test_original_mode_speaks_source_text_not_translation(self, timed, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="你好", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), ALL, narrate_original=True)
        assert timed.synth == ["你好"]

    def test_translation_mode_still_speaks_the_translation_by_default(self, timed, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="你好", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), ALL)
        assert timed.synth == ["Hello"]

    def test_original_mode_generates_audio_with_no_translation_at_all(self, timed, tmp_path):
        """Exit condition: narration still works with no ln.en in original
        mode -- only the exported bilingual subtitle needs it (warned
        about at the UI level, see tabs/workspace_tab.py)."""
        lines = [Line(idx=0, start=0, end=0, zh="你好世界", en="", speaker="A")]
        out_path, errors = dub.build_narration_track(lines, str(tmp_path), ALL, narrate_original=True)
        assert timed.synth == ["你好世界"]
        assert errors == []
        assert lines[0].dub_filename is not None

    def test_a_line_with_no_source_text_is_still_treated_as_blank_in_original_mode(self, timed, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), ALL, narrate_original=True)
        assert timed.synth == []
        assert lines[0].start == lines[0].end == 0.0

    def test_switching_narration_language_changes_the_clip_cache_signature_even_with_identical_text(
            self, timed, tmp_path):
        """Isolates the signature's explicit language marker: even when the
        source and translated text happen to be identical (e.g. a proper
        noun), switching narration language must still regenerate the
        clip rather than silently reusing the other mode's cached audio."""
        lines_translation = [Line(idx=0, start=0, end=0, zh="Amy", en="Amy", speaker="A")]
        dub.build_narration_track(lines_translation, str(tmp_path), ALL)
        translation_clip = lines_translation[0].dub_filename

        lines_original = [Line(idx=0, start=0, end=0, zh="Amy", en="Amy", speaker="A")]
        dub.build_narration_track(lines_original, str(tmp_path), ALL, narrate_original=True,
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
        monkeypatch.setattr(dub, "synthesize_line_omnivoice",
                             lambda text, out_path, **kwargs: open(out_path, "w").close())
        # Source text lengths are 1:3 -- very different from the (unused)
        # English lengths, so a pass that still split by ln.en would fail.
        lines = [Line(idx=0, start=0, end=0, zh="a", en="Same length", speaker="A"),
                 Line(idx=1, start=0, end=0, zh="bbb", en="Same length", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), ALL, narrate_original=True, source_language="zh")
        assert lines[0].end < lines[1].end
        # roughly 1/4 vs 3/4 of the clip -- not a 50/50 split
        assert lines[0].end < 0.4

    def test_original_mode_speaks_the_source_text(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        calls = []
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", _fake_omnivoice(calls))
        lines = [Line(idx=0, start=0, end=0, zh="你好", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), ALL, narrate_original=True, source_language="zh")
        assert [text for text, _ in calls] == ["你好"]

    def test_translation_mode_speaks_the_translation(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        calls = []
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", _fake_omnivoice(calls))
        lines = [Line(idx=0, start=0, end=0, zh="你好", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), ALL)
        assert [text for text, _ in calls] == ["Hello"]

    def test_original_mode_narration_lines_still_export_bilingual_subtitles(self, timed, tmp_path):
        import subtitle_formats
        lines = [Line(idx=0, start=0, end=0, zh="你好", en="Hello", speaker="A")]
        dub.build_narration_track(lines, str(tmp_path), ALL, narrate_original=True, source_language="zh")
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


class TestCloneEngineOriginalLanguages:
    """Which engines are confirmed to speak zh/ja/ko well, gating novel
    narration's original-language mode in the Workspace character-engine
    picker."""

    def test_omnivoice_supports_all_three(self):
        for lang in ("zh", "ja", "ko"):
            assert dub.clone_engine_supports_language("omnivoice", lang)

    def test_a_language_outside_the_table_is_not_confirmed(self):
        assert not dub.clone_engine_supports_language("omnivoice", "en")

    def test_removed_engines_are_not_gated_here_but_still_refused(self):
        # engine_refusal, not the language table, is what stops them.
        for engine in ("tada", "chatterbox", "gpt_sovits"):
            assert dub.clone_engine_supports_language(engine, "ko")
            assert dub.engine_refusal(engine)


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
        monkeypatch.setattr(dub, "synthesize_line_omnivoice",
                             lambda text, out_path, **kwargs: open(out_path, "w").close())

        direct_lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        direct_out_path, direct_errors = dub.build_dub_track(
            direct_lines, str(tmp_path), ALL)

        worker_lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        result_queue = queue.Queue()
        dub.build_track_subprocess_worker(
            worker_lines, str(tmp_path), ALL, False, 1.4, 0.85, result_queue)
        outcome = result_queue.get_nowait()

        assert outcome == ("ok", {"lines": worker_lines, "out_path": direct_out_path,
                                  "errors": direct_errors})
        assert worker_lines[0].dub_filename == direct_lines[0].dub_filename

    def test_is_narration_true_routes_to_build_narration_track(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch, clip_lengths={
            str(tmp_path / "dub_clips" / "line_0000.wav"): 2000,
        })
        monkeypatch.setattr(dub, "synthesize_line_omnivoice",
                             lambda text, out_path, **kwargs: open(out_path, "w").close())

        lines = [Line(idx=0, start=0, end=0, zh="x", en="First")]
        result_queue = queue.Queue()
        dub.build_track_subprocess_worker(
            lines, str(tmp_path), ALL, True, 1.4, 0.85, result_queue)
        outcome = result_queue.get_nowait()

        # only build_narration_track rewrites .start/.end onto the lines
        assert lines[0].end == 2.0
        assert outcome[0] == "ok"

    def test_reports_an_exception_instead_of_raising(self, monkeypatch, tmp_path):
        _install_fake_pydub(monkeypatch)
        monkeypatch.setattr(dub, "synthesize_line_omnivoice",
                             lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))
        monkeypatch.setattr(dub, "build_dub_track",
                             lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")))

        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        result_queue = queue.Queue()
        dub.build_track_subprocess_worker(
            lines, str(tmp_path), ALL, False, 1.4, 0.85, result_queue)
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

    def fake_synth(text, out_path, ref_audio_path=None, ref_text=None, instruct=None):
        record.synth.append(text)
        _write_ms(out_path, len(text) * 100)

    def fake_stretch(in_path, out_path, factor):
        record.stretch.append(factor)
        _write_ms(out_path, _TimedAudio.from_file(in_path)._length / factor)
        return out_path

    monkeypatch.setattr(dub, "synthesize_line_omnivoice", fake_synth)
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

        def fake_run(cmd, check, capture_output, timeout):
            seen.append(cmd)
            _write_ms(cmd[-1], 1000)
        monkeypatch.setattr("subprocess.run", fake_run)
        out = str(tmp_path / "line_0000_abc_x1.200.wav")
        dub.time_stretch(str(tmp_path / "in.wav"), out, 1.2)
        assert seen[0][:3] == ["ffmpeg", "-y", "-i"]
        assert "atempo=1.200" in seen[0]
        assert os.listdir(tmp_path) == [os.path.basename(out)]

    def test_a_failed_stretch_leaves_nothing_behind_for_the_next_run(self, monkeypatch, tmp_path):
        def fake_run(cmd, check, capture_output, timeout):
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
        _, errors = dub.build_dub_track(lines, str(tmp_path), ALL)
        assert errors == [] and timed.stretch == [1.2]
        assert lines[0].dub_filename.endswith("_x1.200.wav")
        rec = dub.pacing_for_line(lines[0], dub.load_pacing(str(tmp_path)))
        assert rec["status"] == dub.PACING_STRETCHED and rec["factor"] == 1.2

    def test_a_clip_that_already_fits_is_left_untouched(self, timed, tmp_path):
        lines = [_line(0, "x" * 10)]  # exactly 1s
        dub.build_dub_track(lines, str(tmp_path), ALL)
        assert timed.stretch == []
        assert "_x" not in lines[0].dub_filename
        assert dub.load_pacing(str(tmp_path))[0]["status"] == dub.PACING_FIT

    def test_past_the_cap_it_is_capped_and_overflows_not_crushed(self, timed, tmp_path):
        lines = [_line(0, "x" * 30)]  # 3s of speech in a 1s window
        dub.build_dub_track(lines, str(tmp_path), ALL)
        assert timed.stretch == [dub.DUB_MAX_SPEEDUP]
        rec = dub.load_pacing(str(tmp_path))[0]
        assert rec["status"] == dub.PACING_OVERFLOW
        assert dub.PACING_ICONS[rec["status"]] == "🔴"

    def test_the_clamp_passed_in_is_the_one_used(self, timed, tmp_path):
        lines = [_line(0, "x" * 12)]
        dub.build_dub_track(lines, str(tmp_path), ALL, max_speedup=1.1)
        assert timed.stretch == [1.1]
        assert dub.load_pacing(str(tmp_path))[0]["status"] == dub.PACING_OVERFLOW

    def test_all_three_states_map_to_their_indicator(self, timed, tmp_path):
        lines = [_line(0, "x" * 10, 0, 1), _line(1, "x" * 12, 1, 2), _line(2, "x" * 30, 2, 3)]
        dub.build_dub_track(lines, str(tmp_path), ALL)
        pacing = dub.load_pacing(str(tmp_path))
        assert [dub.PACING_ICONS[dub.pacing_for_line(ln, pacing)["status"]] for ln in lines] == [
            "🟢", "🟡", "🔴"]

    def test_without_ffmpeg_the_line_still_plays_unstretched(self, timed, monkeypatch, tmp_path):
        monkeypatch.setattr(dub, "time_stretch",
                            lambda *a: (_ for _ in ()).throw(FileNotFoundError("ffmpeg")))
        lines = [_line(0, "x" * 12)]
        _, errors = dub.build_dub_track(lines, str(tmp_path), ALL)
        assert errors == []
        assert "_x" not in lines[0].dub_filename
        rec = dub.load_pacing(str(tmp_path))[0]
        assert (rec["status"], rec["factor"]) == (dub.PACING_OVERFLOW, 1.0)

    def test_the_pacing_rewrite_runs_first_and_the_stretch_only_closes_what_is_left(
            self, timed, monkeypatch, tmp_path):
        import translate_engines
        long_text = "x" * 30  # 3s -- would hit the cap and overflow
        lines = [_line(0, long_text)]
        dub.build_dub_track(lines, str(tmp_path), ALL)
        assert dub.load_pacing(str(tmp_path))[0]["status"] == dub.PACING_OVERFLOW

        # Section 7's pacing rewrite, with a fake LLM, shortens the line...
        monkeypatch.setattr("engine_backends.llm_tasks.call_llm_json",
                            lambda *a, **k: json.dumps({"1": "x" * 11}))
        translate_engines.rewrite_for_pacing_llm(lines, types.SimpleNamespace(supports_reference=True))
        assert lines[0].en == "x" * 11

        # ...so the next dub voices the rewritten line (not the old clip) and
        # only stretches the 10% gap the rewrite left.
        timed.stretch.clear()
        dub.build_dub_track(lines, str(tmp_path), ALL)
        assert timed.synth[-1] == "x" * 11
        assert timed.stretch == [1.1]
        assert dub.load_pacing(str(tmp_path))[0]["status"] == dub.PACING_STRETCHED

    def test_a_pacing_record_goes_stale_once_the_line_points_elsewhere(self, timed, tmp_path):
        lines = [_line(0, "x" * 12)]
        dub.build_dub_track(lines, str(tmp_path), ALL)
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
        dub.build_dub_track(lines, str(tmp_path), ALL)
        stale = lines[0].dub_filename

        lines[0].en = "Hello there"  # typo fixed after dubbing
        dub.build_dub_track(lines, str(tmp_path), ALL)

        assert timed.synth == ["Helo there", "Hello there"]
        assert lines[0].dub_filename != stale

    def test_changing_a_characters_voice_re_voices_their_lines(self, timed, tmp_path):
        lines = [_line(0, "x" * 10)]
        dub.build_dub_track(lines, str(tmp_path), {"A": {**VOICE, "instruct": "female, low pitch"}})
        dub.build_dub_track(lines, str(tmp_path), {"A": {**VOICE, "instruct": "male, high pitch"}})
        assert len(timed.synth) == 2

    def test_a_cancelled_run_still_resumes_from_its_finished_clips(self, timed, monkeypatch, tmp_path):
        lines = [_line(i, f"Line {i}.", i, i + 1) for i in range(4)]
        real_synth = dub.synthesize_line_omnivoice

        def killed_on_line_2(text, out_path, **kwargs):
            if text == "Line 2.":
                raise _Killed()
            real_synth(text, out_path, **kwargs)
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", killed_on_line_2)
        with pytest.raises(_Killed):
            dub.build_dub_track(lines, str(tmp_path), ALL)
        assert timed.synth == ["Line 0.", "Line 1."]

        monkeypatch.setattr(dub, "synthesize_line_omnivoice", real_synth)
        fresh = [_line(i, f"Line {i}.", i, i + 1) for i in range(4)]
        _, errors = dub.build_dub_track(fresh, str(tmp_path), ALL)
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
        fixture_synth = dub.synthesize_line_omnivoice  # the timed fixture's fake, restored below

        def killed_after_writing_a_header(text, out_path, **kwargs):
            with open(out_path, "w") as f:
                f.write("only-a-header")
            raise _Killed()
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", killed_after_writing_a_header)

        lines = [_line(0, "Hello")]
        with pytest.raises(_Killed):
            dub.build_dub_track(lines, str(tmp_path), ALL)

        clips_dir = tmp_path / "dub_clips"
        assert all(f.endswith(".partial.wav") for f in os.listdir(clips_dir))

        monkeypatch.setattr(dub, "synthesize_line_omnivoice", fixture_synth)
        fresh = [_line(0, "Hello")]
        _, errors = dub.build_dub_track(fresh, str(tmp_path), ALL)
        assert errors == []
        assert timed.synth == ["Hello"]  # retried for real, not reused as the corrupted clip
        assert fresh[0].dub_filename is not None

    def test_an_edited_narration_unit_is_re_voiced_too(self, timed, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="x", en="Helo.", speaker="N")]
        dub.build_narration_track(lines, str(tmp_path), ALL)
        lines[0].en = "Hello."
        dub.build_narration_track(lines, str(tmp_path), ALL)
        assert timed.synth == ["Helo.", "Hello."]
        dub.build_narration_track(lines, str(tmp_path), ALL)
        assert timed.synth == ["Helo.", "Hello."]  # unchanged -- reused


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
        clone_map = dub.clone_map_from_characters(chars, str(tmp_path), speaker_labels=["A"])
        lines = [_line(0, "x" * 10)]
        _, errors = dub.build_dub_track(lines, str(tmp_path), clone_map)
        # the hosted id is ignored: a designed voice from the run's engine, no clone
        assert errors == [] and timed.synth == ["x" * 10]
        assert set(clone_map["A"]) == {"engine", "instruct"}
        assert "has been removed" in dub.clone_removed_message(chars[0])


class TestDubWorkerArgumentBinding:
    """background_jobs appends result_queue as the LAST positional argument
    (`args=(*args, result_queue)`), but build_track_subprocess_worker
    declares result_queue right after max_slowdown, before its
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
        positional = ([], "/d", {}, False, 1.4, 0.85)
        call = inspect.signature(bound).bind(*positional, queue)
        call.apply_defaults()  # partial-bound keywords show up as defaults
        assert call.arguments["result_queue"] is queue
        assert call.arguments["narrate_original"] is True
        assert call.arguments["source_language"] == "ja"

