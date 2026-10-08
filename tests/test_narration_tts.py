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

import core
import dub
import dub_narration
from core import Line


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

class TestOmniVoice:
    def _install(self, monkeypatch):
        generated, loaded = [], []

        class FakeModel:
            def generate(self, **kwargs):
                generated.append(kwargs)
                return [np.zeros(3)]

        class OmniVoice:
            @staticmethod
            def from_pretrained(repo, **kwargs):
                loaded.append((repo, kwargs))
                return FakeModel()

        module = types.ModuleType("omnivoice")
        module.OmniVoice = OmniVoice
        monkeypatch.setitem(sys.modules, "omnivoice", module)
        monkeypatch.setattr(dub, "_omnivoice_model", None)
        return generated, loaded

    def test_clones_from_a_clip_with_its_transcript(self, monkeypatch, tmp_path, fake_soundfile, fake_torch):
        generated, loaded = self._install(monkeypatch)
        out = str(tmp_path / "a.wav")
        dub.synthesize_line_omnivoice("Hello there.", out, ref_audio_path="/refs/a.wav", ref_text="你好")
        assert generated == [{"text": "Hello there.", "ref_audio": "/refs/a.wav", "ref_text": "你好"}]
        assert fake_soundfile == [(out, dub.OMNIVOICE_SAMPLE_RATE)]
        assert loaded == [("k2-fsa/OmniVoice", {"device_map": "cpu", "dtype": "float32"})]

    def test_voice_design_needs_no_clip(self, monkeypatch, tmp_path, fake_soundfile, fake_torch):
        generated, _ = self._install(monkeypatch)
        dub.synthesize_line_omnivoice("Hello.", str(tmp_path / "a.wav"),
                                      instruct="female, low pitch, british accent")
        assert generated == [{"text": "Hello.", "instruct": "female, low pitch, british accent"}]

    def test_model_loads_once_across_lines(self, monkeypatch, tmp_path, fake_soundfile, fake_torch):
        _, loaded = self._install(monkeypatch)
        dub.synthesize_line_omnivoice("One.", str(tmp_path / "1.wav"), instruct="male")
        dub.synthesize_line_omnivoice("Two.", str(tmp_path / "2.wav"), instruct="male")
        assert len(loaded) == 1


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

    @pytest.mark.parametrize("stored", ["f5tts", "f5", "edge_tts", "offline", "tada", "chatterbox",
                                        "gpt_sovits"])
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

    def test_a_removed_hosted_clone_says_so(self):
        assert "has been removed" in dub.clone_removed_message(self._char("A", elevenlabs_voice_id="v1"))
        assert dub.clone_removed_message(self._char("A")) is None

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
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", _touch_synth(calls, "synthesize_line_omnivoice"))
        return calls

    CASES = [
        ({"engine": "omnivoice", "ref_audio": "/r/a.wav", "ref_text": "你好"}, "synthesize_line_omnivoice"),
        ({"engine": "omnivoice", "instruct": "female, whisper"}, "synthesize_line_omnivoice"),
    ]

    @pytest.mark.parametrize("clone,expected", CASES)
    def test_dub_track(self, clone, expected, calls, fake_pydub, tmp_path):
        lines = [Line(idx=0, start=0, end=1, zh="x", en="Hello", speaker="A")]
        _, errors = dub.build_dub_track(lines, str(tmp_path), {"A": clone})
        assert errors == []
        assert [c[0] for c in calls] == [expected]

    @pytest.mark.parametrize("clone,expected", CASES)
    def test_narration_track(self, clone, expected, calls, fake_pydub, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="x", en="Hello", speaker="A")]
        _, errors = dub.build_narration_track(lines, str(tmp_path), {"A": clone})
        assert errors == []
        assert [c[0] for c in calls] == [expected]

    def test_described_voices_reach_omnivoice_per_character(self, calls, fake_pydub, tmp_path):
        clone_map = {"Hero": {"engine": "omnivoice", "instruct": "male, low pitch"},
                     "Aunt": {"engine": "omnivoice", "instruct": "female, elderly"}}
        lines = [Line(idx=0, start=0, end=0, zh="x", en="I'm off.", speaker="Hero"),
                 Line(idx=1, start=0, end=0, zh="y", en="Take care.", speaker="Aunt")]
        dub.build_narration_track(lines, str(tmp_path), clone_map)
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
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", fake)
        return calls

    def test_one_tts_call_covers_several_subtitle_cues(self, synth_calls, fake_pydub, tmp_path):
        fake_pydub.clip_lengths = {"line_0000-0002.wav": 6000}
        lines = [Line(idx=0, start=0, end=0, zh="一", en="She opened the door.", speaker="N"),
                 Line(idx=1, start=0, end=0, zh="二", en="Rain.", speaker="N"),
                 Line(idx=2, start=0, end=0, zh="三", en="Nothing but rain, all night.", speaker="N")]
        dub.build_narration_track(lines, str(tmp_path), NARRATORS, gap_ms=350)

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
        dub.build_narration_track(lines, str(tmp_path), NARRATORS)
        assert synth_calls == ["One.", "Two. Three."]

    def test_unit_stays_inside_the_character_budget(self, synth_calls, fake_pydub, tmp_path, monkeypatch):
        monkeypatch.setattr(dub_narration, "NARRATION_TTS_MAX_CHARS", 12)
        lines = [Line(idx=i, start=0, end=0, zh=str(i), en=f"Line {i}.", speaker="N") for i in range(3)]
        dub.build_narration_track(lines, str(tmp_path), NARRATORS)
        assert synth_calls == ["Line 0.", "Line 1.", "Line 2."]  # "Line 0. Line 1." is 15 chars

    def test_never_crosses_a_paragraph_of_the_source_text(self, synth_calls, fake_pydub, tmp_path):
        (tmp_path / dub.NOVEL_SOURCE_FILENAME).write_text("第一段。第一段后半。\n\n第二段。", encoding="utf-8")
        lines = [Line(idx=0, start=0, end=0, zh="第一段。", en="Para one.", speaker="N"),
                 Line(idx=1, start=0, end=0, zh="第一段后半。", en="Still para one.", speaker="N"),
                 Line(idx=2, start=0, end=0, zh="第二段。", en="Para two.", speaker="N")]
        dub.build_narration_track(lines, str(tmp_path), NARRATORS)
        assert synth_calls == ["Para one. Still para one.", "Para two."]

    def test_a_chapter_heading_is_always_its_own_unit(self, synth_calls, fake_pydub, tmp_path):
        lines = [Line(idx=0, start=0, end=0, zh="前文。", en="Before.", speaker="N"),
                 Line(idx=1, start=0, end=0, zh="第二章 雨夜", en="Chapter 2: A Rainy Night", speaker="N"),
                 Line(idx=2, start=0, end=0, zh="正文。", en="After.", speaker="N")]
        dub.build_narration_track(lines, str(tmp_path), NARRATORS)
        assert synth_calls == ["Before.", "Chapter 2: A Rainy Night", "After."]

    def test_a_failed_unit_leaves_each_of_its_lines_a_silent_gap(self, monkeypatch, fake_pydub, tmp_path):
        monkeypatch.setattr(dub, "synthesize_line_omnivoice",
                            lambda *a, **k: (_ for _ in ()).throw(RuntimeError("down")))
        lines = [Line(idx=0, start=0, end=0, zh="a", en="One.", speaker="N"),
                 Line(idx=1, start=0, end=0, zh="b", en="Two.", speaker="N")]
        _, errors = dub.build_narration_track(lines, str(tmp_path), NARRATORS, gap_ms=350)
        assert [e["line_idx"] for e in errors] == [0, 1]
        assert lines[1].end == pytest.approx(0.7)
        assert not any(f.endswith(".partial.wav") for f in os.listdir(tmp_path / "dub_clips"))


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
        monkeypatch.setattr(dub, "synthesize_line_omnivoice", fake)
        os.makedirs(tmp_path / "dub_clips")
        previous = f"line_0001_{dub.clip_signature('Line 1.', VOICE)}.wav"
        (tmp_path / "dub_clips" / previous).write_text("from a previous run")

        lines = self._alternating(8)
        _, errors = dub.build_narration_track(lines, str(tmp_path), NARRATORS)

        assert errors == []
        assert spoken == [f"Line {i}." for i in range(8) if i != 1]  # reused, not regenerated
        assert [ln.start for ln in lines] == sorted(ln.start for ln in lines)
        assert lines[1].dub_filename == os.path.join("dub_clips", previous)

    def test_a_speaker_with_no_voice_is_a_failed_unit_not_a_crash(self, monkeypatch, fake_pydub, tmp_path):
        spoken = []
        monkeypatch.setattr(dub, "synthesize_line_omnivoice",
                            lambda text, out_path, **kw: spoken.append(text) or open(out_path, "w").close())
        lines = [Line(idx=0, start=0, end=0, zh="a", en="One.", speaker="A"),
                 Line(idx=1, start=0, end=0, zh="b", en="Two.", speaker="B")]
        _, errors = dub.build_narration_track(lines, str(tmp_path), {"A": dict(VOICE)}, gap_ms=350)
        assert spoken == ["One."]
        assert errors == [{"line_idx": 1, "error": dub.NO_VOICE_ERROR}]
        assert lines[1].end == pytest.approx(lines[1].start + 0.35)

    @pytest.mark.parametrize("engine", sorted(dub.LOCAL_MODEL_ENGINES))
    def test_every_engine_runs_on_the_gpu_slot(self, engine):
        assert dub.clone_map_uses_local_model({"A": {"engine": engine}})


# ------------------------------------------------------ paragraphs & M4B

class TestNovelParagraphEnds:
    def test_matches_lines_chunked_from_the_source(self):
        text = "短段落。\n\n" + "长句子一。" * 30 + "\n第三段。"
        chunks = core.chunk_novel_text(text, max_chars=60)
        lines = [Line(idx=i, start=0, end=0, zh=c) for i, c in enumerate(chunks)]
        ends = core.novel_paragraph_ends(lines, text)
        assert ends == {0, len(chunks) - 2, len(chunks) - 1}

    def test_edited_lines_mean_unknown_not_a_guess(self):
        lines = [Line(idx=0, start=0, end=0, zh="改过的。"), Line(idx=1, start=0, end=0, zh="第二段。")]
        assert core.novel_paragraph_ends(lines, "第一段。\n第二段。") is None

    def test_missing_trailing_text_is_unknown(self):
        lines = [Line(idx=0, start=0, end=0, zh="第一段。")]
        assert core.novel_paragraph_ends(lines, "第一段。\n第二段。") is None


class TestNarrationChapters:
    def _timed(self, rows):
        return [Line(idx=i, start=float(i), end=float(i) + 0.9, zh=zh, en=en,
                     dub_filename=f"dub_clips/line_{i:04d}.wav") for i, (zh, en) in enumerate(rows)]

    def test_the_novels_own_chapter_headings_win(self):
        lines = self._timed([("第一章 开始", "Chapter 1: The Start"), ("正文。", "Body."),
                             ("第二章 雨", "Chapter 2: Rain"), ("正文。", "More.")])
        assert dub.narration_chapters(lines, paragraph_ends={0, 1, 2, 3}) == [
            (0.0, "Chapter 1: The Start"), (2.0, "Chapter 2: Rain")]

    def test_text_before_the_first_heading_gets_its_own_chapter(self):
        lines = self._timed([("楔子前的话。", "A note first."), ("第一章", "Chapter 1")])
        assert [s for s, _ in dub.narration_chapters(lines)] == [0.0, 1.0]

    def test_otherwise_one_chapter_per_paragraph(self):
        lines = self._timed([("甲。", "A."), ("乙。", "B."), ("丙。", "C.")])
        assert [s for s, _ in dub.narration_chapters(lines, paragraph_ends={1, 2})] == [0.0, 2.0]

    def test_without_paragraph_info_one_per_generated_clip(self):
        lines = self._timed([("甲。", "A."), ("乙。", "B."), ("丙。", "C.")])
        lines[1].dub_filename = lines[0].dub_filename  # lines 0-1 shared one clip
        assert [s for s, _ in dub.narration_chapters(lines)] == [0.0, 2.0]

    def test_long_titles_are_trimmed(self):
        lines = self._timed([("甲。", "word " * 40)])
        assert len(dub.narration_chapters(lines)[0][1]) == 60

    @pytest.mark.parametrize("zh,en", [
        ("第一章", ""), ("第十二章 雨夜", ""), ("第3回：重逢", ""), ("第二卷", ""), ("楔子", ""),
        ("", "Chapter 12: A Rainy Night"), ("", "Chapter One - The Start"), ("", "Prologue"),
        ("", "CHAPTER 3"),
    ])
    def test_real_headings(self, zh, en):
        assert dub.is_chapter_heading(Line(idx=0, start=0, end=0, zh=zh, en=en))

    @pytest.mark.parametrize("zh,en", [
        ("第一次见面的时候，她笑得很开心，" * 3, "The first time..."),
        ("第一回合他就输了。", "He lost in the first round."),
        ("第一部电影很好看", "The first film was good"),
        ("", "Chapter and verse, she said"),
        ("", "Prologue to a disaster, really."),
    ])
    def test_ordinary_sentences_are_not_headings(self, zh, en):
        assert not dub.is_chapter_heading(Line(idx=0, start=0, end=0, zh=zh, en=en))


class TestFfmetadata:
    def test_chapters_run_back_to_back_to_the_end(self):
        meta = dub.narration_ffmetadata([(0.0, "One"), (2.5, "Two")], 6000, title="My Novel")
        assert meta.splitlines() == [
            ";FFMETADATA1", "title=My Novel",
            "[CHAPTER]", "TIMEBASE=1/1000", "START=0", "END=2500", "title=One",
            "[CHAPTER]", "TIMEBASE=1/1000", "START=2500", "END=6000", "title=Two"]

    def test_special_characters_are_escaped(self):
        meta = dub.narration_ffmetadata([(0.0, "a=b; #c \\ d")], 1000)
        assert "title=a\\=b\\; \\#c \\\\ d" in meta

    def test_same_instant_markers_are_collapsed(self):
        meta = dub.narration_ffmetadata([(0.0, "A"), (0.0, "B"), (1.0, "C")], 2000)
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
        monkeypatch.setattr(dub, "synthesize_line_omnivoice",
                            lambda text, out_path, **kw: open(out_path, "w").close())
        (tmp_path / dub.NOVEL_SOURCE_FILENAME).write_text("一。二。\n三。", encoding="utf-8")
        lines = [Line(idx=0, start=0, end=0, zh="一。", en="One."),
                 Line(idx=1, start=0, end=0, zh="二。", en="Two."),
                 Line(idx=2, start=0, end=0, zh="三。", en="Three.")]
        dub.build_narration_track(lines, str(tmp_path), NARRATORS, gap_ms=350)
        _write_wav(tmp_path / "narration_track.wav", 3.0)

        ran = []
        monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: ran.append(cmd))
        out = dub.export_narration_m4b(lines, str(tmp_path), title="Story")

        assert out == str(tmp_path / "narration.m4b")
        cmd = ran[0]
        assert cmd[0] == "ffmpeg" and cmd[-1] == out
        assert cmd[cmd.index("-map_chapters") + 1] == "1"
        assert cmd[cmd.index("-f") + 1] == "ipod"
        meta = (tmp_path / "narration_chapters.txt").read_text(encoding="utf-8")
        starts = [int(l.split("=")[1]) for l in meta.splitlines() if l.startswith("START=")]
        assert starts == [0, int(lines[2].start * 1000)]
        assert meta.splitlines()[-2] == "END=3000"  # the real WAV's length

    def test_needs_the_narration_first(self, tmp_path):
        with pytest.raises(FileNotFoundError, match="generate the narration"):
            dub.export_narration_m4b([], str(tmp_path))

    @pytest.mark.skipif(not (shutil.which("ffmpeg") and shutil.which("ffprobe")),
                        reason="needs real ffmpeg/ffprobe")
    def test_real_ffmpeg_writes_a_playable_m4b_with_chapters(self, tmp_path):
        import json
        _write_wav(tmp_path / "narration_track.wav", 4.0)
        lines = [Line(idx=0, start=0.0, end=1.5, zh="第一章", en="Chapter 1", dub_filename="a"),
                 Line(idx=1, start=2.0, end=3.5, zh="第二章", en="Chapter 2", dub_filename="b")]
        out = dub.export_narration_m4b(lines, str(tmp_path), title="T")
        probe = subprocess.run(["ffprobe", "-v", "error", "-show_chapters", "-print_format", "json", out],
                               capture_output=True, text=True, check=True)
        chapters = json.loads(probe.stdout)["chapters"]
        assert [c["tags"]["title"] for c in chapters] == ["Chapter 1", "Chapter 2"]
        assert float(chapters[1]["start_time"]) == pytest.approx(2.0)
