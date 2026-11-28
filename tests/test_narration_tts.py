"""
tests/test_narration_tts.py -- Step 11b: novel narration TTS quality.

The OmniVoice engine (incl. voice design), each character's engine picked in
the database and routed by dub.clone_map_from_characters;
build_narration_track's longer TTS units (several subtitle-sized lines per
call), its one-clip-at-a-time generation, and the M4B audiobook export with
chapter markers.

None of the engines, torch, soundfile, pydub or ffmpeg are needed: each is
faked at its own import boundary, the same way tests/test_dub.py does.
"""
import os
import re
import shutil
import subprocess
import sys
import types
import wave

import numpy as np
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import novel_text
from voice import dub
from voice import dub_narration
from .voice_helpers import speak_with, use_fake_ffmpeg
from core import Line


@pytest.fixture(autouse=True)
def ffmpeg(monkeypatch):
    return use_fake_ffmpeg(monkeypatch)


# --------------------------------------------------------------- fakes

VOICE = {"engine": "omnivoice", "instruct": "female, young adult, moderate pitch"}
# Every speaker label the narration tests use.
NARRATORS = {label: dict(VOICE) for label in ("A", "B", "N", "AB", None)}

def _unsigned(name):
    """A clip filename without its Step 11e content signature:
    line_0000-0002_<hash>.wav -> line_0000-0002.wav."""
    return re.sub(r"_[0-9a-f]{10}(?=\.wav$)", "", name)


class FakeAudio:
    """Just enough of pydub.AudioSegment for build_narration_track: a clip's
    length comes from clip_lengths (default 1000ms)."""
    clip_lengths = {}
    raw_data = b""

    def __init__(self, ms=0):
        self.ms = ms

    @classmethod
    def silent(cls, duration=0):
        return cls(duration)

    @classmethod
    def from_file(cls, path):
        # clip_lengths is keyed by the unsigned name ("line_0000-0002.wav")
        return cls(cls.clip_lengths.get(_unsigned(os.path.basename(path)), 1000))

    def __add__(self, other):
        return FakeAudio(self.ms + other.ms)

    # No samples: level_clip leaves a fake clip alone.
    channels, frame_rate, sample_width = 1, 16000, 2

    def get_array_of_samples(self):
        return []

    def overlay(self, other, position=0):
        return self

    def __len__(self):
        return self.ms

    def export(self, path, format="wav"):
        with open(path, "w") as f:
            f.write("fake-audio")
        return path


@pytest.fixture
def fake_pydub(monkeypatch):
    module = types.ModuleType("pydub")
    module.AudioSegment = FakeAudio
    monkeypatch.setitem(sys.modules, "pydub", module)
    monkeypatch.setattr(FakeAudio, "clip_lengths", {})
    return FakeAudio


@pytest.fixture
def fake_soundfile(monkeypatch):
    written = []
    module = types.ModuleType("soundfile")

    def write(path, data, samplerate):
        written.append((path, samplerate))
        with open(path, "wb") as f:
            f.write(b"RIFF")

    module.write = write
    module.read = lambda path, dtype=None, always_2d=False: (np.zeros((8, 2), dtype="float32"), 16000)
    monkeypatch.setitem(sys.modules, "soundfile", module)
    return written


class FakeTensor:
    def unsqueeze(self, dim):
        return self

    def squeeze(self, dim=None):
        return self

    def to(self, device):
        return self

    def cpu(self):
        return self

    def float(self):
        return self

    def numpy(self):
        return np.zeros(4)


@pytest.fixture
def fake_torch(monkeypatch):
    module = types.ModuleType("torch")
    module.cuda = types.SimpleNamespace(is_available=lambda: False)
    module.float16, module.float32, module.bfloat16 = "float16", "float32", "bfloat16"
    module.from_numpy = lambda arr: FakeTensor()
    monkeypatch.setitem(sys.modules, "torch", module)
    return module


def _touch_synth(calls, name):
    """A fake synthesize_line_* that records its call and writes out_path."""
    def fake(*args, **kwargs):
        calls.append((name, args, kwargs))
        out_path = kwargs.get("out_path") or next(
            a for a in args if isinstance(a, str) and "dub_clips" in a)
        open(out_path, "w").close()
    return fake


# ------------------------------------------------ engine functions themselves

# ---------------------------------------------- per-character engine routing

class TestCloneMapFromCharacters:
    def _char(self, label, **kw):
        row = {"speaker_label": label, "elevenlabs_voice_id": None, "ref_audio_filename": None,
               "ref_text": None, "clone_engine": None, "voice_design": None}
        row.update(kw)
        return row

    def test_a_character_with_a_clip_and_no_engine_follows_the_runs_engine(self):
        chars = [self._char("A", ref_audio_filename="a.wav", ref_text="你好")]
        m = dub.clone_map_from_characters(chars, "/d")
        assert m == {"A": {"engine": "omnivoice", "ref_audio": os.path.join("/d", "a.wav"), "ref_text": "你好"}}
        m = dub.clone_map_from_characters(chars, "/d", default_engine="tada")
        assert m == {}  # a removed run engine voices nobody

    @pytest.mark.parametrize("stored", ["f5tts", "f5", "edge_tts", "offline", "tada", "gpt_sovits"])
    def test_a_character_stored_with_a_removed_engine_is_not_cloned_by_another_one(self, stored):
        chars = [self._char("A", ref_audio_filename="a.wav", ref_text="你好", clone_engine=stored)]
        assert dub.clone_map_from_characters(chars, "/d") == {}
        assert dub.removed_engine_message(stored) in dub.engine_blockers(chars, "omnivoice")[0]

    def test_a_removed_hosted_clone_is_no_clone_at_all(self):
        # A character cloned with the removed hosted engine is simply not
        # in the map, instead of crashing a dub job...
        m = dub.clone_map_from_characters([self._char("A", elevenlabs_voice_id="v1")], "/d")
        assert m == {}

    def test_a_removed_hosted_clone_with_a_clip_uses_the_clip(self):
        # ...and one that also has a clip clones it with its local engine.
        m = dub.clone_map_from_characters(
            [self._char("A", elevenlabs_voice_id="v1", ref_audio_filename="a.wav", clone_engine="omnivoice")],
            "/d")
        assert m == {"A": {"engine": "omnivoice", "ref_audio": os.path.join("/d", "a.wav"), "ref_text": ""}}

    def test_a_clip_wins_over_a_description(self):
        m = dub.clone_map_from_characters(
            [self._char("A", ref_audio_filename="a.wav", clone_engine="omnivoice", voice_design="male")], "/d")
        assert "instruct" not in m["A"]

    def test_no_clip_gets_a_described_voice_distinct_per_description(self):
        m = dub.clone_map_from_characters([
            self._char("Hero", voice_design="male, young adult, low pitch"),
            self._char("Aunt", voice_design="  female, elderly, british accent  "),
        ], "/d")
        assert m == {"Hero": {"engine": "omnivoice", "instruct": "male, young adult, low pitch"},
                     "Aunt": {"engine": "omnivoice", "instruct": "female, elderly, british accent"}}

    def test_nothing_set_means_no_entry_for_the_character_itself(self):
        assert dub.clone_map_from_characters([self._char("N", clone_engine="omnivoice")], "/d") == {}

    def test_gpu_slot_only_for_local_engines(self):
        assert dub.clone_map_uses_local_model({"A": {"engine": "omnivoice", "instruct": "x"}})
        assert not dub.clone_map_uses_local_model({"A": {"ref_audio": "a", "ref_text": ""}})
        assert not dub.clone_map_uses_local_model({})


class TestEngineSelectionFollowsTheClonePattern:
    """Each clone-map entry reaches its engine's synthesize function,
    through both track builders."""

    @pytest.fixture
    def calls(self, monkeypatch):
        calls = []
        speak_with(monkeypatch, _touch_synth(calls, "synthesize_line_omnivoice"))
        return calls

    CASES = [
        ({"engine": "omnivoice", "ref_audio": "/r/a.wav", "ref_text": "你好"}, "synthesize_line_omnivoice"),
        ({"engine": "omnivoice", "instruct": "female, whisper"}, "synthesize_line_omnivoice"),
    ]

    @pytest.mark.parametrize("clone,expected", CASES)
    def test_narration_track(self, clone, expected, calls, fake_pydub, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="x", en="Hello", speaker="A")]
        _, errors = dub_narration.build_narration_track(lines, str(tmp_path), {"A": clone})
        assert errors == []
        assert [c[0] for c in calls] == [expected]

    def test_described_voices_reach_omnivoice_per_character(self, calls, fake_pydub, tmp_path):
        clone_map = {"Hero": {"engine": "omnivoice", "instruct": "male, low pitch"},
                     "Aunt": {"engine": "omnivoice", "instruct": "female, elderly"}}
        lines = [Line(idx=0, start=0, end=0, zh="x", en="I'm off.", speaker="Hero"),
                 Line(idx=1, start=0, end=0, zh="y", en="Take care.", speaker="Aunt")]
        dub_narration.build_narration_track(lines, str(tmp_path), clone_map)
        assert [(c[1][0], c[2]["instruct"]) for c in calls] == [
            ("I'm off.", "male, low pitch"), ("Take care.", "female, elderly")]
        assert all(c[2].get("ref_audio_path") is None for c in calls)


# ------------------------------------------ TTS unit longer than a subtitle cue

class TestNarrationTTSUnits:
    @pytest.fixture
    def synth_calls(self, monkeypatch):
        calls = []

        def fake(text, out_path, **kwargs):
            calls.append(text)
            open(out_path, "w").close()
        speak_with(monkeypatch, fake)
        return calls

    def test_one_tts_call_covers_several_subtitle_cues(self, synth_calls, fake_pydub, tmp_path):
        fake_pydub.clip_lengths = {"line_0000-0002.wav": 6000}
        lines = [Line(idx=0, start=0, end=0, zh="一", en="She opened the door.", speaker="N"),
                 Line(idx=1, start=0, end=0, zh="二", en="Rain.", speaker="N"),
                 Line(idx=2, start=0, end=0, zh="三", en="Nothing but rain, all night.", speaker="N")]
        dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS)

        assert synth_calls == ["She opened the door. Rain. Nothing but rain, all night."]
        # the audio unit is longer than every exported cue for the same passage
        assert all(len(ln.en) < len(synth_calls[0]) for ln in lines)
        # each line is still its own cue, back to back inside the one clip,
        # split in proportion to its length
        assert lines[0].start == 0.0 and lines[2].end == 6.0
        assert lines[0].end == lines[1].start and lines[1].end == lines[2].start
        assert (lines[1].end - lines[1].start) < (lines[0].end - lines[0].start)
        assert len({ln.dub_filename for ln in lines}) == 1
        assert _unsigned(lines[0].dub_filename) == os.path.join("dub_clips", "line_0000-0002.wav")

    def test_speaker_change_starts_a_new_unit(self, synth_calls, fake_pydub, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="a", en="One.", speaker="A"),
                 Line(idx=1, start=0, end=0, zh="b", en="Two.", speaker="B"),
                 Line(idx=2, start=0, end=0, zh="c", en="Three.", speaker="B")]
        dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS)
        assert synth_calls == ["One.", "Two. Three."]

    def test_unit_stays_inside_the_character_budget(self, synth_calls, fake_pydub, tmp_path, monkeypatch):
        monkeypatch.setattr(dub_narration, "NARRATION_TTS_MAX_CHARS", 12)
        lines = [Line(idx=i, start=0, end=0, zh=str(i), en=f"Line {i}.", speaker="N") for i in range(3)]
        dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS)
        assert synth_calls == ["Line 0.", "Line 1.", "Line 2."]  # "Line 0. Line 1." is 15 chars

    def test_never_crosses_a_paragraph_of_the_source_text(self, synth_calls, fake_pydub, tmp_path):
        (tmp_path / dub_narration.NOVEL_SOURCE_FILENAME).write_text("第一段。第一段后半。\n\n第二段。", encoding="utf-8")
        lines = [Line(idx=0, start=0, end=0, zh="第一段。", en="Para one.", speaker="N"),
                 Line(idx=1, start=0, end=0, zh="第一段后半。", en="Still para one.", speaker="N"),
                 Line(idx=2, start=0, end=0, zh="第二段。", en="Para two.", speaker="N")]
        dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS)
        assert synth_calls == ["Para one. Still para one.", "Para two."]

    def test_a_chapter_heading_is_always_its_own_unit(self, synth_calls, fake_pydub, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="前文。", en="Before.", speaker="N"),
                 Line(idx=1, start=0, end=0, zh="第二章 雨夜", en="Chapter 2: A Rainy Night", speaker="N"),
                 Line(idx=2, start=0, end=0, zh="正文。", en="After.", speaker="N")]
        dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS)
        assert synth_calls == ["Before.", "Chapter 2: A Rainy Night", "After."]

    def test_a_failed_unit_leaves_each_of_its_lines_a_silent_gap(self, monkeypatch, fake_pydub, tmp_path):
        def fails_the_first_unit(text, out_path, **kw):
            if text == "One. Two.":
                raise RuntimeError("down")
            open(out_path, "w").close()
        speak_with(monkeypatch, fails_the_first_unit)
        # Two more lines that speak: a run where more than half fail keeps the old track instead.
        lines = [Line(idx=0, start=0, end=0, zh="a", en="One.", speaker="N"),
                 Line(idx=1, start=0, end=0, zh="b", en="Two.", speaker="N"),
                 Line(idx=2, start=0, end=0, zh="c", en="Three.", speaker="A"),
                 Line(idx=3, start=0, end=0, zh="d", en="Four.", speaker="A")]
        _, errors = dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS)
        assert [e["line_idx"] for e in errors] == [0, 1]
        assert lines[1].end == pytest.approx(0.7)
        assert not any(f.endswith(".partial.wav") for f in os.listdir(tmp_path / "dub_clips"))


class TestNarrationHeadingsAndPace:
    @pytest.fixture(autouse=True)
    def _speak(self, monkeypatch):
        speak_with(monkeypatch, lambda text, out_path, **kw: open(out_path, "w").close())

    def test_a_chapter_title_given_twice_in_a_row_is_read_once(self, fake_pydub, tmp_path, monkeypatch):
        spoken = []
        speak_with(monkeypatch, lambda text, out_path, **kw: (spoken.append(text), open(out_path, "w").close()))
        site_title = "第1章 第一章 \u201c若是我早些\u2026\u2026"
        lines = [Line(idx=0, start=0, end=0, zh=site_title, en="", speaker="N"),
                 Line(idx=1, start=0, end=0, zh=site_title, en="", speaker="N"),
                 Line(idx=2, start=0, end=0, zh="正文。", en="", speaker="N"),
                 Line(idx=3, start=0, end=0, zh="第2章 第二章 雨夜", en="", speaker="N"),
                 Line(idx=4, start=0, end=0, zh="第二章 雨夜来了", en="", speaker="N"),
                 Line(idx=5, start=0, end=0, zh="第三章 晴天", en="", speaker="N"),
                 Line(idx=6, start=0, end=0, zh="第四章 阴天", en="", speaker="N")]
        dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS, narrate_original=True)
        # Two different chapters in a row (a contents page) both stay.
        assert spoken == [site_title, "正文。", "第二章 雨夜来了", "第三章 晴天", "第四章 阴天"]
        assert lines[0].start == lines[0].end == 0.0 and not lines[0].dub_filename
        assert lines[1].dub_filename and not lines[3].dub_filename
        # The audiobook's chapter list names each chapter once, by the line that is read.
        titles = [t for _, t in dub_narration.narration_chapters(lines, narrate_original=True)]
        assert titles == [site_title, "第二章 雨夜来了", "第三章 晴天", "第四章 阴天"]

    def test_a_short_title_inside_a_longer_one_is_not_a_repeat(self, fake_pydub, tmp_path, monkeypatch):
        spoken = []
        speak_with(monkeypatch, lambda text, out_path, **kw: (spoken.append(text), open(out_path, "w").close()))
        lines = [Line(idx=0, start=0, end=0, zh="第2章 他", en="", speaker="N"),
                 Line(idx=1, start=0, end=0, zh="第3章 我和他", en="", speaker="N")]
        dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS, narrate_original=True)
        assert spoken == ["第2章 他", "第3章 我和他"]

    def test_a_slower_pace_stretches_the_speech_but_keeps_the_gap_between_lines(self, fake_pydub, tmp_path,
                                                                                 monkeypatch, ffmpeg):
        lines = [Line(idx=i, start=0, end=0, zh=str(i), en=f"Line {i}.", speaker=s)
                 for i, s in enumerate(("A", "B"))]
        dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS, pace=0.8)
        # Each 1 s clip plays for 1.25 s; the gap between them is still 350 ms.
        assert [(ln.start, ln.end) for ln in lines] == [
            pytest.approx((0.0, 1.25)), pytest.approx((1.6, 2.85))]
        assert ffmpeg.cmd[ffmpeg.cmd.index("-af") + 1] == "atempo=0.8000"

    def test_a_pace_out_of_range_is_refused_before_anything_is_spoken(self, fake_pydub, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="a", en="One.", speaker="N")]
        with pytest.raises(ValueError):
            dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS, pace=0.5)
        assert not os.listdir(tmp_path / "dub_clips")


class TestNarrationTrackFormat:
    def test_the_track_is_written_as_flac_and_an_older_wav_is_dropped(self, monkeypatch, fake_pydub, tmp_path):
        speak_with(monkeypatch, lambda text, out_path, **kw: open(out_path, "w").close())
        (tmp_path / "narration_track.wav").write_bytes(b"old")
        lines = [Line(idx=0, start=0, end=0, zh="a", en="One.", speaker="N")]
        out, _ = dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS)
        assert out == str(tmp_path / "narration_track.flac")
        assert not (tmp_path / "narration_track.wav").exists()
        assert dub_narration.find_narration_track(str(tmp_path)) == "narration_track.flac"

    def test_a_failed_export_leaves_no_track_at_the_final_name(self, monkeypatch, fake_pydub, tmp_path):
        speak_with(monkeypatch, lambda text, out_path, **kw: open(out_path, "w").close())

        def half_written(chunk):
            open(tmp_path / "narration_track.flac.part", "w").write("half")
        use_fake_ffmpeg(monkeypatch, returncode=1, stderr=b"disk full", on_write=half_written)
        lines = [Line(idx=0, start=0, end=0, zh="a", en="One.", speaker="N")]
        with pytest.raises(RuntimeError, match="disk full"):
            dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS)
        assert os.listdir(tmp_path / "dub_clips") and not (tmp_path / "narration_track.flac").exists()
        assert not (tmp_path / "narration_track.flac.part").exists()

    def test_a_title_with_only_the_older_wav_still_has_a_track(self, tmp_path):
        assert dub_narration.find_narration_track(str(tmp_path)) is None
        (tmp_path / "narration_track.wav").write_bytes(b"x")
        assert dub_narration.find_narration_track(str(tmp_path)) == "narration_track.wav"
        (tmp_path / "narration_track.flac").write_bytes(b"x")
        assert dub_narration.find_narration_track(str(tmp_path)) == "narration_track.flac"


# ----------------------------------------------- one clip at a time

class TestSequentialNarration:
    def _alternating(self, n, prefix="Line"):
        # alternating speakers, so every line is its own unit
        return [Line(idx=i, start=0, end=0, zh=str(i), en=f"{prefix} {i}.", speaker="AB"[i % 2])
                for i in range(n)]

    def test_no_thread_pool_remains(self):
        assert not hasattr(dub, "ThreadPoolExecutor") and not hasattr(dub, "PARALLEL_SAFE_ENGINES")
        assert not hasattr(dub, "NARRATION_MAX_WORKERS")

    def test_existing_clips_are_reused_and_the_rest_generated_in_line_order(
            self, monkeypatch, fake_pydub, tmp_path):
        spoken = []

        def fake(text, out_path, **kwargs):
            spoken.append(text)
            open(out_path, "w").close()
        speak_with(monkeypatch, fake)
        os.makedirs(tmp_path / "dub_clips")
        previous = f"line_0001_{dub.clip_signature('Line 1.', VOICE)}.wav"
        (tmp_path / "dub_clips" / previous).write_text("from a previous run")

        lines = self._alternating(8)
        _, errors = dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS)

        assert errors == []
        assert spoken == [f"Line {i}." for i in range(8) if i != 1]  # reused, not regenerated
        assert [ln.start for ln in lines] == sorted(ln.start for ln in lines)
        assert lines[1].dub_filename == os.path.join("dub_clips", previous)

    def test_a_speaker_with_no_voice_is_a_failed_unit_not_a_crash(self, monkeypatch, fake_pydub, tmp_path):
        spoken = []
        speak_with(monkeypatch, lambda text, out_path, **kw: spoken.append(text) or open(out_path, "w").close())
        lines = [Line(idx=0, start=0, end=0, zh="a", en="One.", speaker="A"),
                 Line(idx=1, start=0, end=0, zh="b", en="Two.", speaker="B")]
        _, errors = dub_narration.build_narration_track(lines, str(tmp_path), {"A": dict(VOICE)})
        assert spoken == ["One."]
        assert errors == [{"line_idx": 1, "error": dub.NO_VOICE_ERROR}]
        assert lines[1].end == pytest.approx(lines[1].start + 0.35)

    @pytest.mark.parametrize("engine", sorted(dub.LOCAL_MODEL_ENGINES))
    def test_every_engine_runs_on_the_gpu_slot(self, engine):
        assert dub.clone_map_uses_local_model({"A": {"engine": engine}})


class _Killed(BaseException):
    """A cancel: the worker is killed, nothing after it runs."""


class TestListenWhileItIsMade:
    """The part spoken so far is joined into the track at chapter ends, so it can be played
    (and a cancelled run keeps it) before the whole title is spoken."""

    @pytest.fixture(autouse=True)
    def _every_chapter(self, monkeypatch):
        monkeypatch.setattr(dub_narration, "JOIN_SPACING", 0)
        monkeypatch.setattr(dub_narration, "MIN_JOIN_GAP_SECONDS", 0)

    def _chapters(self, n):
        lines = []
        for c in range(n):
            lines.append(Line(idx=len(lines), start=0, end=0, zh=f"第{c + 1}章", en=f"Chapter {c + 1}", speaker="N"))
            lines.append(Line(idx=len(lines), start=0, end=0, zh="正文。", en=f"Body {c + 1}.", speaker="N"))
        for i, ln in enumerate(lines):
            ln.id = 100 + i
        return lines

    def _run(self, monkeypatch, tmp_path, lines, synth=None, parts=None):
        speak_with(monkeypatch, synth or (lambda text, out_path, **kw: open(out_path, "w").close()))
        return dub_narration.build_narration_track(
            lines, str(tmp_path), NARRATORS, on_partial=None if parts is None else parts.append)

    def test_each_chapter_end_joins_the_part_spoken_so_far(self, monkeypatch, fake_pydub, tmp_path):
        track = tmp_path / "narration_track.flac"
        heard_at = {}

        def synth(text, out_path, **kw):
            heard_at[text] = track.exists()
            open(out_path, "w").close()
        parts = []
        lines = self._chapters(3)
        self._run(monkeypatch, tmp_path, lines, synth, parts)
        assert heard_at == {"Chapter 1": False, "Body 1.": False, "Chapter 2": True, "Body 2.": True,
                            "Chapter 3": True, "Body 3.": True}
        assert len(parts) == 2
        first = {line_id: (start, end, clip) for line_id, start, end, clip in parts[0]}
        # Chapter 1 is timed and has its clips; the rest waits at the part's end with none.
        assert first[100][2] and first[101][2]
        part_end = first[101][1]
        assert all(first[i] == (part_end, part_end, None) for i in range(102, 106))
        # The part's times are the ones the whole track gives those lines.
        assert [first[ln.id] for ln in lines[:2]] == [(ln.start, ln.end, ln.dub_filename) for ln in lines[:2]]

    def test_the_finished_track_is_the_same_as_without_listening_early(self, monkeypatch, fake_pydub, tmp_path):
        early, plain = self._chapters(3), self._chapters(3)
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        self._run(monkeypatch, tmp_path / "a", early, parts=[])
        self._run(monkeypatch, tmp_path / "b", plain)
        assert [(ln.start, ln.end, ln.dub_filename) for ln in early] == [
            (ln.start, ln.end, ln.dub_filename) for ln in plain]

    def test_a_cancel_keeps_the_chapters_joined_before_it(self, monkeypatch, fake_pydub, tmp_path):
        def synth(text, out_path, **kw):
            if text == "Body 3.":
                raise _Killed()
            open(out_path, "w").close()
        parts = []
        with pytest.raises(_Killed):
            self._run(monkeypatch, tmp_path, self._chapters(3), synth, parts)
        assert (tmp_path / "narration_track.flac").exists()
        last = {line_id: clip for line_id, _s, _e, clip in parts[-1]}
        assert [bool(last[i]) for i in range(100, 106)] == [True, True, True, True, False, False]

    def test_a_part_that_is_mostly_silence_is_not_joined(self, monkeypatch, fake_pydub, tmp_path):
        def synth(text, out_path, **kw):
            if text in ("Chapter 1", "Body 1."):
                raise RuntimeError("down")
            open(out_path, "w").close()
        parts = []
        self._run(monkeypatch, tmp_path, self._chapters(4), synth, parts)
        # Chapter 1 alone was all silence; with chapter 2 the part is half spoken.
        assert len(parts) == 2
        assert {line_id for line_id, _s, _e, clip in parts[0] if clip} == {102, 103}

    def test_a_title_without_chapters_joins_at_any_line_end(self, monkeypatch, fake_pydub, tmp_path):
        lines = [Line(idx=i, start=0, end=0, zh=str(i), en=f"Line {i}.", speaker="AB"[i % 2]) for i in range(3)]
        parts = []
        self._run(monkeypatch, tmp_path, lines, parts=parts)
        assert len(parts) == 2

    def test_joins_wait_in_proportion_to_how_long_the_last_took(self, monkeypatch, fake_pydub, tmp_path):
        monkeypatch.setattr(dub_narration, "JOIN_SPACING", 10 ** 6)
        parts = []
        self._run(monkeypatch, tmp_path, self._chapters(4), parts=parts)
        assert len(parts) == 1

    def test_a_title_without_chapters_waits_a_minute_between_joins(self, monkeypatch, fake_pydub, tmp_path):
        monkeypatch.setattr(dub_narration, "MIN_JOIN_GAP_SECONDS", 60.0)
        lines = [Line(idx=i, start=0, end=0, zh=str(i), en=f"Line {i}.", speaker="AB"[i % 2]) for i in range(6)]
        parts = []
        self._run(monkeypatch, tmp_path, lines, parts=parts)
        assert len(parts) == 1

    def test_an_early_join_that_fails_is_tried_again_and_the_run_finishes(self, monkeypatch, fake_pydub, tmp_path):
        real = dub_narration.replace_file
        calls = []

        def held_once(src, dst):
            calls.append(dst)
            if len(calls) == 1:
                raise PermissionError(13, "The process cannot access the file")
            return real(src, dst)
        monkeypatch.setattr(dub_narration, "replace_file", held_once)
        parts = []
        lines = self._chapters(4)
        out, errors = self._run(monkeypatch, tmp_path, lines, parts=parts)
        assert errors == [] and os.path.exists(out)
        # The first try at chapter 1 failed and was made again at the next line's end.
        assert len(calls) == 5 and len(parts) == 3
        assert {line_id for line_id, _s, _e, clip in parts[0] if clip} == {100, 101}
        assert all(ln.dub_filename for ln in lines)


    def test_a_final_track_held_open_waits_beside_it_and_a_read_swaps_it_in(
            self, monkeypatch, fake_pydub, tmp_path):
        import novel_text
        track = tmp_path / "narration_track.flac"
        real = dub_narration.replace_file

        def held(src, dst):
            if dst == str(track):
                raise PermissionError(13, "The process cannot access the file")
            return real(src, dst)
        monkeypatch.setattr(dub_narration, "replace_file", held)
        track.write_bytes(b"the part the Reader is playing")
        os.utime(track, (1, 1))
        lines = self._chapters(2)
        out, errors = self._run(monkeypatch, tmp_path, lines, parts=[])
        assert errors == [] and all(ln.dub_filename for ln in lines)
        waiting = tmp_path / novel_text.NARRATION_WAITING_NAME
        made = waiting.read_bytes()
        assert made != b"the part the Reader is playing"
        # Still held at the next read: the waiting track is the one served.
        real_replace = os.replace

        def still_held(src, dst):
            raise PermissionError(13, "The process cannot access the file")
        monkeypatch.setattr(novel_text.os, "replace", still_held)
        assert novel_text.find_narration_track(str(tmp_path)) == novel_text.NARRATION_WAITING_NAME
        monkeypatch.setattr(novel_text.os, "replace", real_replace)
        assert novel_text.find_narration_track(str(tmp_path)) == "narration_track.flac"
        assert track.read_bytes() == made and not waiting.exists()

    @pytest.fixture
    def logged(self, monkeypatch):
        import applog
        seen = []
        log = types.SimpleNamespace(warning=lambda msg: seen.append(("warning", msg)),
                                    info=lambda msg: seen.append(("info", msg)))
        monkeypatch.setattr(applog, "get_logger", lambda: log)
        return seen

    def _hold(self, monkeypatch, *held_names):
        real = dub_narration.replace_file

        def held(src, dst):
            if os.path.basename(dst) in held_names:
                raise PermissionError(13, "The process cannot access the file", dst)
            return real(src, dst)
        monkeypatch.setattr(dub_narration, "replace_file", held)

    def _work(self, monkeypatch, tmp_path, lines):
        import queue
        speak_with(monkeypatch, lambda text, out_path, **kw: open(out_path, "w").close())
        result_queue = queue.Queue()
        dub_narration.build_narration_subprocess_worker(lines, str(tmp_path), NARRATORS, result_queue)
        items = []
        while not result_queue.empty():
            items.append(result_queue.get_nowait())
        return items[-1]

    def test_a_held_final_track_is_logged_and_the_job_says_what_to_do(
            self, monkeypatch, fake_pydub, tmp_path, logged):
        from services import jobs_service
        (tmp_path / "narration_track.flac").write_bytes(b"playing")
        os.utime(tmp_path / "narration_track.flac", (1, 1))
        self._hold(monkeypatch, "narration_track.flac")
        kind, result = self._work(monkeypatch, tmp_path, self._chapters(2))
        assert kind == "ok" and result["track_waiting"] == dub_narration.TRACK_IN_USE_NOTICE
        assert logged == [("warning", "narration: the finished track could not replace the old one, which is "
                           "in use (the Reader may be playing it); it waits as narration_track.next.flac until "
                           "the old file is free (PermissionError: The process cannot access the file)")]
        outcome, message = jobs_service.derive_outcome("done", None, jobs_service.project_result(result))
        assert outcome == "ok" and dub_narration.TRACK_IN_USE_NOTICE in message
        logged.clear()
        assert novel_text.find_narration_track(str(tmp_path)) == "narration_track.flac"
        assert logged == [("info", "narration: the finished track that waited while the old one was in use "
                           "was swapped in now that the old track is free")]

    def test_a_track_swapped_in_at_once_has_no_note(self, monkeypatch, fake_pydub, tmp_path, logged):
        kind, result = self._work(monkeypatch, tmp_path, self._chapters(2))
        assert kind == "ok" and "track_waiting" not in result and logged == []

    def test_a_track_that_cannot_wait_beside_it_either_fails_the_run_saying_what_to_do(
            self, monkeypatch, fake_pydub, tmp_path, logged):
        (tmp_path / "narration_track.flac").write_bytes(b"playing")
        self._hold(monkeypatch, "narration_track.flac", novel_text.NARRATION_WAITING_NAME)
        kind, name, message, _trace = self._work(monkeypatch, tmp_path, self._chapters(2))
        assert (kind, name, message) == ("error", "RuntimeError", dub_narration.TRACK_IN_USE_ERROR)
        assert [level for level, _msg in logged] == ["warning"]
        assert "could not be saved beside it either, so the run failed" in logged[0][1]
        assert str(tmp_path) not in logged[0][1]
        assert (tmp_path / "narration_track.flac").read_bytes() == b"playing"

    def test_a_waiting_track_older_than_the_title_track_is_dropped(self, tmp_path):
        import novel_text
        waiting = tmp_path / novel_text.NARRATION_WAITING_NAME
        waiting.write_bytes(b"older")
        os.utime(waiting, (1, 1))
        (tmp_path / "narration_track.flac").write_bytes(b"newer")
        assert novel_text.find_narration_track(str(tmp_path)) == "narration_track.flac"
        assert not waiting.exists() and (tmp_path / "narration_track.flac").read_bytes() == b"newer"


class TestMakeAgainKeepsTheOldTrack:
    """A title that already has a track is not given a part of the new one: with its clips cached
    the part would replace the whole track before anything is spoken."""

    @pytest.fixture(autouse=True)
    def _every_chapter(self, monkeypatch):
        monkeypatch.setattr(dub_narration, "JOIN_SPACING", 0)
        monkeypatch.setattr(dub_narration, "MIN_JOIN_GAP_SECONDS", 0)

    def _lines(self):
        return [Line(idx=i, start=0, end=0, zh=f"第{i // 2 + 1}章" if i % 2 == 0 else "正文。",
                     en=f"Chapter {i // 2 + 1}" if i % 2 == 0 else f"Body {i}.", speaker="N")
                for i in range(6)]

    def test_a_cancel_leaves_the_old_track_whole(self, monkeypatch, fake_pydub, tmp_path):
        speak_with(monkeypatch, lambda text, out_path, **kw: open(out_path, "w").close())
        dub_narration.build_narration_track(self._lines(), str(tmp_path), NARRATORS)
        track = tmp_path / "narration_track.flac"
        track.write_bytes(b"the whole old track")

        def synth(text, out_path, **kw):
            if text == "Body 5 changed.":
                raise _Killed()
            open(out_path, "w").close()
        speak_with(monkeypatch, synth)
        parts = []
        changed = self._lines()
        changed[5].en = "Body 5 changed."
        with pytest.raises(_Killed):
            dub_narration.build_narration_track(changed, str(tmp_path), NARRATORS, on_partial=parts.append)
        assert parts == [] and track.read_bytes() == b"the whole old track"

    def test_an_engine_that_fails_keeps_the_old_track(self, monkeypatch, fake_pydub, tmp_path):
        track = tmp_path / "narration_track.flac"
        track.write_bytes(b"the whole old track")

        def down(text, out_path, **kw):
            raise RuntimeError("no engine")
        speak_with(monkeypatch, down)
        parts = []
        with pytest.raises(RuntimeError, match="so the previous track was kept"):
            dub_narration.build_narration_track(self._lines(), str(tmp_path), NARRATORS, on_partial=parts.append)
        assert parts == [] and track.read_bytes() == b"the whole old track"


# ------------------------------------------------------ paragraphs & M4B

class TestNovelParagraphEnds:
    def test_matches_lines_chunked_from_the_source(self):
        text = "短段落。\n\n" + "长句子一。" * 30 + "\n第三段。"
        chunks = novel_text.chunk_novel_text(text, max_chars=60)
        lines = [Line(idx=i, start=0, end=0, zh=c) for i, c in enumerate(chunks)]
        ends = novel_text.novel_paragraph_ends(lines, text)
        assert ends == {0, len(chunks) - 2, len(chunks) - 1}

    def test_edited_lines_mean_unknown_not_a_guess(self):
        lines = [Line(idx=0, start=0, end=0, zh="改过的。"), Line(idx=1, start=0, end=0, zh="第二段。")]
        assert novel_text.novel_paragraph_ends(lines, "第一段。\n第二段。") is None

    def test_missing_trailing_text_is_unknown(self):
        lines = [Line(idx=0, start=0, end=0, zh="第一段。")]
        assert novel_text.novel_paragraph_ends(lines, "第一段。\n第二段。") is None


class TestNarrationChapters:
    def _timed(self, rows):
        return [Line(idx=i, start=float(i), end=float(i) + 0.9, zh=zh, en=en,
                     dub_filename=f"dub_clips/line_{i:04d}.wav") for i, (zh, en) in enumerate(rows)]

    def test_the_novels_own_chapter_headings_win(self):
        lines = self._timed([("第一章 开始", "Chapter 1: The Start"), ("正文。", "Body."),
                             ("第二章 雨", "Chapter 2: Rain"), ("正文。", "More.")])
        assert dub_narration.narration_chapters(lines, paragraph_ends={0, 1, 2, 3}) == [
            (0.0, "Chapter 1: The Start"), (2.0, "Chapter 2: Rain")]

    def test_text_before_the_first_heading_gets_its_own_chapter(self):
        lines = self._timed([("楔子前的话。", "A note first."), ("第一章", "Chapter 1")])
        assert [s for s, _ in dub_narration.narration_chapters(lines)] == [0.0, 1.0]

    def test_otherwise_one_chapter_per_paragraph(self):
        lines = self._timed([("甲。", "A."), ("乙。", "B."), ("丙。", "C.")])
        assert [s for s, _ in dub_narration.narration_chapters(lines, paragraph_ends={1, 2})] == [0.0, 2.0]

    def test_without_paragraph_info_one_per_generated_clip(self):
        lines = self._timed([("甲。", "A."), ("乙。", "B."), ("丙。", "C.")])
        lines[1].dub_filename = lines[0].dub_filename  # lines 0-1 shared one clip
        assert [s for s, _ in dub_narration.narration_chapters(lines)] == [0.0, 2.0]

    def test_long_titles_are_trimmed(self):
        lines = self._timed([("甲。", "word " * 40)])
        assert len(dub_narration.narration_chapters(lines)[0][1]) == 60

    @pytest.mark.parametrize("zh,en", [
        ("第一章", ""), ("第十二章 雨夜", ""), ("第3回：重逢", ""), ("第二卷", ""), ("楔子", ""),
        ("", "Chapter 12: A Rainy Night"), ("", "Chapter One - The Start"), ("", "Prologue"),
        ("", "CHAPTER 3"),
        ("第5話", ""), ("第十二话 雨夜", ""), ("第一部：起", ""), ("プロローグ", ""),
        ("제3화", ""), ("제 12 장 비 오는 밤", ""), ("12화", ""), ("프롤로그", ""), ("외전 1", ""), ("番外篇", ""), ("番外 小雪", ""),
    ])
    def test_real_headings(self, zh, en):
        assert dub_narration.is_chapter_heading(Line(idx=0, start=0, end=0, zh=zh, en=en))

    @pytest.mark.parametrize("zh,en", [
        ("第一次见面的时候，她笑得很开心，" * 3, "The first time..."),
        ("第一回合他就输了。", "He lost in the first round."),
        ("第一部电影很好看", "The first film was good"),
        ("", "Chapter and verse, she said"),
        ("", "Prologue to a disaster, really."),
        ("第一部电影", ""), ("第三话题", ""), ("1화를 다 봤다", ""), ("프롤로그가 길었다", ""),
        ("10화 만에 끝났다.", ""), ("외전 연재가 시작됐다.", ""), ("에필로그 부분에서 그는 죽었다.", ""),
        ("제1부 공연이 끝났다.", ""), ("序章的时候他就走了", ""), ("番外篇很好看", ""),
    ])
    def test_ordinary_sentences_are_not_headings(self, zh, en):
        assert not dub_narration.is_chapter_heading(Line(idx=0, start=0, end=0, zh=zh, en=en))


class TestFfmetadata:
    def test_chapters_run_back_to_back_to_the_end(self):
        meta = dub_narration.narration_ffmetadata([(0.0, "One"), (2.5, "Two")], 6000, title="My Novel")
        assert meta.splitlines() == [
            ";FFMETADATA1", "title=My Novel",
            "[CHAPTER]", "TIMEBASE=1/1000", "START=0", "END=2500", "title=One",
            "[CHAPTER]", "TIMEBASE=1/1000", "START=2500", "END=6000", "title=Two"]

    def test_special_characters_are_escaped(self):
        meta = dub_narration.narration_ffmetadata([(0.0, "a=b; #c \\ d")], 1000)
        assert "title=a\\=b\\; \\#c \\\\ d" in meta

    def test_same_instant_markers_are_collapsed(self):
        meta = dub_narration.narration_ffmetadata([(0.0, "A"), (0.0, "B"), (1.0, "C")], 2000)
        assert meta.count("[CHAPTER]") == 2


def _write_wav(path, seconds):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(8000)
        w.writeframes(b"\x00\x00" * int(8000 * seconds))


class TestExportM4b:
    def test_chapters_match_the_narrations_paragraph_breaks(self, monkeypatch, fake_pydub, tmp_path):
        """End to end: narrate, then export -- the chapter markers land on the
        first line of each paragraph, at the timing narration gave it."""
        speak_with(monkeypatch, lambda text, out_path, **kw: open(out_path, "w").close())
        (tmp_path / dub_narration.NOVEL_SOURCE_FILENAME).write_text("一。二。\n三。", encoding="utf-8")
        lines = [Line(idx=0, start=0, end=0, zh="一。", en="One."),
                 Line(idx=1, start=0, end=0, zh="二。", en="Two."),
                 Line(idx=2, start=0, end=0, zh="三。", en="Three.")]
        dub_narration.build_narration_track(lines, str(tmp_path), NARRATORS)
        (tmp_path / "narration_track.flac").unlink()  # the fake encoder's stub; this run exports an older title's WAV
        _write_wav(tmp_path / "narration_track.wav", 3.0)

        ran = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ran.append(cmd))
        out = dub_narration.export_narration_m4b(lines, str(tmp_path), title="Story")

        assert out == str(tmp_path / "narration.m4b")
        cmd = ran[0]
        assert cmd[0] == "ffmpeg" and cmd[-1] == out
        # Each input opens only local files, in the formats the app writes.
        inputs = [i for i, a in enumerate(cmd) if a == "-i"]
        assert all(cmd[i - 4] == "-protocol_whitelist" and cmd[i - 2] == "-format_whitelist" for i in inputs)
        assert cmd[inputs[1] - 1] == "ffmetadata"
        assert cmd[cmd.index("-map_chapters") + 1] == "1"
        assert cmd[cmd.index("-f") + 1] == "ipod"
        meta = (tmp_path / "narration_chapters.txt").read_text(encoding="utf-8")
        starts = [int(l.split("=")[1]) for l in meta.splitlines() if l.startswith("START=")]
        assert starts == [0, int(lines[2].start * 1000)]
        assert meta.splitlines()[-2] == "END=3000"  # the real WAV's length

    def test_a_flac_track_gets_its_chapter_end_from_ffprobe(self, monkeypatch, tmp_path):
        (tmp_path / "narration_track.flac").write_bytes(b"fLaC")
        monkeypatch.setattr("media_inspect.duration_seconds", lambda path, timeout=30: 3.5)
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: None)
        lines = [Line(idx=0, start=0.0, end=1.0, zh="一", en="One", dub_filename="a")]
        dub_narration.export_narration_m4b(lines, str(tmp_path), title="T")
        meta = (tmp_path / "narration_chapters.txt").read_text(encoding="utf-8")
        assert meta.splitlines()[-2] == "END=3500"

    def test_needs_the_narration_first(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="generate the narration"):
            dub_narration.export_narration_m4b([], str(tmp_path))

    @pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                        reason="needs real ffmpeg/ffprobe")
    def test_real_ffmpeg_writes_a_playable_m4b_with_chapters(self, tmp_path):
        import json
        _write_wav(tmp_path / "narration_track.wav", 4.0)
        lines = [Line(idx=0, start=0.0, end=1.5, zh="第一章", en="Chapter 1", dub_filename="a"),
                 Line(idx=1, start=2.0, end=3.5, zh="第二章", en="Chapter 2", dub_filename="b")]
        out = dub_narration.export_narration_m4b(lines, str(tmp_path), title="T")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-print_format", "json", out],
                               capture_output=True, text=True, check=True)
        chapters = json.loads(probe.stdout)["chapters"]
        assert [c["tags"]["title"] for c in chapters] == ["Chapter 1", "Chapter 2"]
        assert float(chapters[1]["start_time"]) == pytest.approx(2.0)
