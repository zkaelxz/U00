"""
tests/test_speaker_rerun.py -- Step 4 (R2): re-run speaker detection on
its own, from stored audio, without re-transcribing -- and without ever
silently undoing a speaker someone corrected by hand.
"""
import argparse
import contextlib
import io
import os
import sys
import types

import pytest

import core
import diarize
from core import Line

TWO = [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
       {"start": 2.0, "end": 6.0, "speaker": "SPEAKER_01"}]
THREE = [{"start": 0.0, "end": 2.0, "speaker": "SPEAKER_00"},
         {"start": 2.0, "end": 4.0, "speaker": "SPEAKER_01"},
         {"start": 4.0, "end": 6.0, "speaker": "SPEAKER_02"}]


def _lines():
    return [Line(idx=0, start=0.2, end=1.8, zh="甲"),
            Line(idx=1, start=2.2, end=3.8, zh="乙"),
            Line(idx=2, start=4.2, end=5.8, zh="丙")]


class TestMergeSpeakers:
    def test_relabels_without_touching_text_or_timing(self):
        lines = _lines()
        diarize.merge_speakers(lines, TWO)
        diarize.merge_speakers(lines, THREE)
        assert [ln.speaker for ln in lines] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
        assert [(ln.zh, ln.start, ln.end) for ln in lines] == [(l.zh, l.start, l.end) for l in _lines()]

    def test_a_hand_corrected_speaker_survives_by_default(self):
        lines = _lines()
        diarize.merge_speakers(lines, TWO)
        lines[2].speaker, lines[2].speaker_manual = "Xiaoling", True
        assert [ln.idx for ln in diarize.manual_lines_that_would_change(lines, THREE)] == [2]
        result = diarize.merge_speakers(lines, THREE)
        assert lines[2].speaker == "Xiaoling" and lines[2].speaker_manual is True
        assert result == {"changed": 0, "kept_manual": 1}

    def test_overwrites_it_only_when_explicitly_told_to(self):
        lines = _lines()
        lines[2].speaker, lines[2].speaker_manual = "Xiaoling", True
        diarize.merge_speakers(lines, THREE, overwrite_manual=True)
        assert lines[2].speaker == "SPEAKER_02" and lines[2].speaker_manual is False


class TestTurnsAreStored:
    def test_round_trip(self, tmp_path):
        diarize.save_turns(str(tmp_path), THREE, num_speakers=3, model="m")
        assert diarize.load_turns(str(tmp_path)) == THREE

    def test_nothing_stored_yet(self, tmp_path):
        assert diarize.load_turns(str(tmp_path)) is None


class TestModelChoice:
    def _fake_pyannote(self, monkeypatch, fail_for=()):
        tried = []

        class Pipeline:
            @staticmethod
            def from_pretrained(model, token=None):
                tried.append(model)
                if model in fail_for:
                    raise OSError(f"can't load {model}")
                return f"pipeline:{model}"

        pkg = types.ModuleType("pyannote")
        audio = types.ModuleType("pyannote.audio")
        audio.Pipeline = Pipeline
        monkeypatch.setitem(sys.modules, "pyannote", pkg)
        monkeypatch.setitem(sys.modules, "pyannote.audio", audio)
        return tried

    def test_community_1_is_tried_first(self, monkeypatch):
        tried = self._fake_pyannote(monkeypatch)
        assert diarize.load_pipeline("hf") == ("pipeline:pyannote/speaker-diarization-community-1",
                                               "pyannote/speaker-diarization-community-1")
        assert tried == ["pyannote/speaker-diarization-community-1"]

    def test_falls_back_to_3_1(self, monkeypatch):
        self._fake_pyannote(monkeypatch, fail_for=("pyannote/speaker-diarization-community-1",))
        assert diarize.load_pipeline("hf")[1] == "pyannote/speaker-diarization-3.1"

    def test_raises_if_neither_loads(self, monkeypatch):
        self._fake_pyannote(monkeypatch, fail_for=diarize.DIARIZATION_MODELS)
        with pytest.raises(OSError):
            diarize.load_pipeline("hf")


@pytest.fixture
def no_asr(monkeypatch):
    """The ASR mock: re-running speaker detection must never call it."""
    def boom(*a, **k):
        raise AssertionError("ASR was called during a speaker-detection re-run")
    monkeypatch.setattr(core, "transcribe_for_timing", boom)
    # (A patch on tabs.workspace_tab.transcribe_for_timing used to sit here;
    # the tab never calls that bare name -- its per-line re-transcribe goes
    # through core_module, patched above -- so it was dropped for the
    # Streamlit retirement.)
    import cli
    monkeypatch.setattr(cli, "transcribe_for_timing", boom)
    # Migration Slice 2: run_transcribe_job/run_fix_flagged_lines_job now
    # live in services/workspace_job_service.py and resolve this name from
    # their own module globals -- patching it there too is what actually
    # covers that path now.
    from services import workspace_job_service
    monkeypatch.setattr(workspace_job_service, "transcribe_for_timing", boom)


@pytest.fixture
def fake_diarize(monkeypatch):
    calls = []

    def fake(audio_path, hf_token, num_speakers=None, return_model=False, return_embeddings=False,
             **kwargs):
        calls.append(num_speakers)
        turns = THREE if num_speakers == 3 else TWO
        if return_model and return_embeddings:
            return turns, "fake-model", {}
        if return_embeddings:
            return turns, {}
        return (turns, "fake-model") if return_model else turns
    monkeypatch.setattr(diarize, "diarize", fake)
    return calls


def _drama_with_audio(isolated_db):
    did = isolated_db.create_drama(title_en="D", media_type="audio_drama", content_mode="audio_drama",
                                   status="aligned", audio_filename="audio.wav")
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, "audio.wav"), "wb").close()
    lines = _lines()
    for ln in lines:
        ln.speaker = diarize.assign_speaker_to_line(ln.start, ln.end, TWO)
    isolated_db.save_lines(did, lines)
    return did, ddir


class TestCliDiarize:
    def _run(self, did, **kw):
        import cli
        args = argparse.Namespace(id=did, hf_token=kw.get("hf_token", "hf"), num_speakers=kw.get("num_speakers", 0),
                                  overwrite_manual=kw.get("overwrite_manual", False))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli.cmd_diarize(args)
        return out.getvalue()

    def test_saved_settings_hf_token_is_used_when_no_flag_or_env(
            self, isolated_db, no_asr, monkeypatch):
        import cli
        did, _ = _drama_with_audio(isolated_db)
        for name in ("HF_TOKEN", "BAIHE_HF_TOKEN"):
            monkeypatch.delenv(name, raising=False)
        monkeypatch.setattr(cli.settings_service, "resolve_key",
                            lambda name, *a: "saved-hf" if name == "hf_token" else None)
        tokens = []

        def fake(audio_path, hf_token, num_speakers=None, return_model=False,
                 return_embeddings=False, **kwargs):
            tokens.append(hf_token)
            return (TWO, "fake-model", {}) if return_model and return_embeddings else TWO
        monkeypatch.setattr(diarize, "diarize", fake)
        self._run(did, hf_token=None)
        assert tokens == ["saved-hf"]

    def test_changing_expected_speakers_relabels_without_asr(self, isolated_db, no_asr, fake_diarize):
        did, ddir = _drama_with_audio(isolated_db)
        before = [(r["zh"], r["start"], r["end"]) for r in isolated_db.load_lines(did)]
        self._run(did, num_speakers=3)
        rows = isolated_db.load_lines(did)
        assert [r["speaker"] for r in rows] == ["SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
        assert [(r["zh"], r["start"], r["end"]) for r in rows] == before
        assert fake_diarize == [3]
        assert diarize.load_turns(ddir) == THREE

    def test_a_manual_correction_survives_unless_overwrite_manual(self, isolated_db, no_asr, fake_diarize):
        did, _ = _drama_with_audio(isolated_db)
        lines = isolated_db.load_line_objects(did)
        lines[2].speaker, lines[2].speaker_manual = "Xiaoling", True
        isolated_db.save_lines(did, lines)

        out = self._run(did, num_speakers=3)
        assert isolated_db.load_lines(did)[2]["speaker"] == "Xiaoling"
        assert "kept 1 hand-corrected" in out

        self._run(did, num_speakers=3, overwrite_manual=True)
        row = isolated_db.load_lines(did)[2]
        assert row["speaker"] == "SPEAKER_02" and not row["speaker_manual"]


