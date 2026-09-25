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
    import tabs.workspace_tab as wt

    def boom(*a, **k):
        raise AssertionError("ASR was called during a speaker-detection re-run")
    monkeypatch.setattr(core, "transcribe_for_timing", boom)
    monkeypatch.setattr(wt, "transcribe_for_timing", boom, raising=False)
    import cli
    monkeypatch.setattr(cli, "transcribe_for_timing", boom)


@pytest.fixture
def fake_process(monkeypatch):
    """Step 4d: "Re-run speaker detection" now starts a real
    multiprocessing.Process (background_jobs.start_process_job) instead
    of calling diarize.diarize() directly in the button handler -- faked
    here to run its target synchronously inside .start(), so a test can
    still assert on the result immediately after clicking, the same way
    it could before Step 4d (AppTest's own st.rerun() already re-executes
    the script synchronously within one .run() call, so the result is
    fully applied by the time control returns). No real OS process is
    ever spawned; diarize_subprocess_worker runs for real and calls the
    (separately faked, via fake_diarize) diarize.diarize() itself."""
    import background_jobs

    class _FakeProcess:
        def __init__(self, target, args, daemon=True):
            self._target, self._args = target, args

        def start(self):
            self._target(*self._args)

        def is_alive(self):
            return False

        def terminate(self):
            pass

        def join(self, timeout=None):
            pass

    monkeypatch.setattr(background_jobs.multiprocessing, "Process",
                        lambda target, args, daemon=True: _FakeProcess(target, args, daemon))


@pytest.fixture
def fake_diarize(monkeypatch):
    calls = []

    def fake(audio_path, hf_token, num_speakers=None, return_model=False, return_embeddings=False):
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
    diarize.label_lines_with_speakers(lines, TWO)
    isolated_db.save_lines(did, lines)
    return did, ddir


class TestCliDiarize:
    def _run(self, did, **kw):
        import cli
        args = argparse.Namespace(id=did, hf_token="hf", num_speakers=kw.get("num_speakers", 0),
                                  overwrite_manual=kw.get("overwrite_manual", False))
        with contextlib.redirect_stdout(io.StringIO()) as out:
            cli.cmd_diarize(args)
        return out.getvalue()

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


class TestRerunButton:
    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_hf_token"] = "hf_fake"
        at.run(timeout=30)
        at.run(timeout=30)
        return at

    def _set_expected_speakers(self, at, n):
        [box] = [n_ for n_ in at.number_input if n_.label.startswith("Expected number of speakers")]
        box.set_value(n).run(timeout=30)

    def _click(self, at, label):
        [b] = [b for b in at.button if b.label == label]
        b.click().run(timeout=30)

    def test_rerun_relabels_from_stored_audio_without_asr(
            self, isolated_db, no_asr, fake_diarize, fake_process):
        did, _ = _drama_with_audio(isolated_db)
        at = self._run(did)
        self._set_expected_speakers(at, 3)
        self._click(at, "🔁 Re-run speaker detection")
        assert [r["speaker"] for r in isolated_db.load_lines(did)] == [
            "SPEAKER_00", "SPEAKER_01", "SPEAKER_02"]
        assert fake_diarize == [3]

    def test_asks_before_touching_a_hand_correction_and_keeps_it_by_default(
            self, isolated_db, no_asr, fake_diarize, fake_process):
        did, _ = _drama_with_audio(isolated_db)
        lines = isolated_db.load_line_objects(did)
        lines[2].speaker, lines[2].speaker_manual = "Xiaoling", True
        isolated_db.save_lines(did, lines)

        at = self._run(did)
        self._set_expected_speakers(at, 3)
        self._click(at, "🔁 Re-run speaker detection")
        assert any("1 line(s) have a speaker you corrected by hand" in w.value for w in at.warning)
        assert isolated_db.load_lines(did)[2]["speaker"] == "Xiaoling"  # nothing applied yet

        self._click(at, "Apply, keeping my 1 correction(s)")
        rows = isolated_db.load_lines(did)
        assert rows[2]["speaker"] == "Xiaoling"
        assert rows[1]["speaker"] == "SPEAKER_01"

    def test_overwrites_only_after_explicit_confirmation(
            self, isolated_db, no_asr, fake_diarize, fake_process):
        did, _ = _drama_with_audio(isolated_db)
        lines = isolated_db.load_line_objects(did)
        lines[2].speaker, lines[2].speaker_manual = "Xiaoling", True
        isolated_db.save_lines(did, lines)

        at = self._run(did)
        self._set_expected_speakers(at, 3)
        self._click(at, "🔁 Re-run speaker detection")
        self._click(at, "Apply and overwrite my 1 correction(s)")
        assert isolated_db.load_lines(did)[2]["speaker"] == "SPEAKER_02"

    def test_editing_a_speaker_in_review_marks_it_as_a_manual_correction(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db)
        at = self._run(did)
        [box] = [t for t in at.text_input if t.key == "speaker_2"]
        box.set_value("Xiaoling").run(timeout=30)
        self._click(at, "💾 Save edits (this page)")
        row = isolated_db.load_lines(did)[2]
        assert row["speaker"] == "Xiaoling" and row["speaker_manual"] == 1
        assert not isolated_db.load_lines(did)[1]["speaker_manual"]


class TestExpectedSpeakersDefaultsToLastRun:
    """Step 4f: the field used to hardcode value=0 on every render, even
    right after a real detection run had used a specific count -- the
    count was already being saved (diarize.save_turns's own num_speakers
    field) but never read back."""

    def _run(self, did):
        from streamlit.testing.v1 import AppTest

        def _render():
            import tabs.workspace_tab as wt
            wt.render_workspace_tab()

        at = AppTest.from_function(_render)
        at.session_state["active_drama_id"] = did
        at.session_state["lines"] = None
        at.session_state["settings_hf_token"] = "hf_fake"
        at.run(timeout=30)
        return at

    def _box(self, at):
        [box] = [n_ for n_ in at.number_input if n_.label.startswith("Expected number of speakers")]
        return box

    def test_defaults_to_0_when_no_run_has_ever_happened(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db)
        at = self._run(did)
        assert self._box(at).value == 0

    def test_defaults_to_the_count_used_for_the_last_real_run(self, isolated_db):
        did, ddir = _drama_with_audio(isolated_db)
        diarize.save_turns(ddir, THREE, num_speakers=3)
        at = self._run(did)
        assert self._box(at).value == 3

    def test_a_prior_auto_detect_run_still_defaults_to_0(self, isolated_db):
        did, ddir = _drama_with_audio(isolated_db)
        diarize.save_turns(ddir, TWO, num_speakers=None)
        at = self._run(did)
        assert self._box(at).value == 0

    def test_manually_changing_it_survives_a_rerun_rather_than_snapping_back(self, isolated_db):
        did, ddir = _drama_with_audio(isolated_db)
        diarize.save_turns(ddir, THREE, num_speakers=3)
        at = self._run(did)
        assert self._box(at).value == 3
        self._box(at).set_value(5).run(timeout=30)
        assert self._box(at).value == 5
