"""
The per-title "sensitive" transcription preset: "normal" must leave every run
exactly as it was; "sensitive" lowers an untouched VAD threshold and decodes
without the repeat penalties but keeps the repeated-segment backstop. Whisper
is faked throughout.
"""
import argparse
import contextlib
import io
import os
import sys
import types

import pytest

import background_jobs
import cli
import core
import raw_transcript
import sensitivity_preset as presets
from services import transcribe_service
from services.service_errors import InvalidInputError


class TestPresetRules:
    def test_unknown_or_missing_is_normal(self):
        assert presets.normalize(None) == "normal"
        assert presets.normalize("turbo") == "normal"
        assert presets.normalize("sensitive") == "sensitive"

    def test_normal_never_changes_the_threshold(self):
        for stored in (None, 0, 0.5, 0.3, 0.8):
            assert presets.effective_vad_threshold(stored, "normal") == (stored or 0.5)

    def test_sensitive_lowers_only_an_untouched_threshold(self):
        assert presets.effective_vad_threshold(None, "sensitive") == 0.35
        assert presets.effective_vad_threshold(0.5, "sensitive") == 0.35
        # The owner's own value wins, higher or lower.
        assert presets.effective_vad_threshold(0.3, "sensitive") == 0.3
        assert presets.effective_vad_threshold(0.7, "sensitive") == 0.7

    def test_decode_kwargs(self):
        base = {"condition_on_previous_text": False, "no_repeat_ngram_size": 3, "repetition_penalty": 1.1}
        assert presets.decode_kwargs("normal", base) == base
        assert presets.decode_kwargs(None, base) == base
        assert presets.decode_kwargs("normal", base) is not base
        assert presets.decode_kwargs("sensitive", base) == {"condition_on_previous_text": False}
        assert core.WHISPER_ANTI_LOOP_KWARGS == {"condition_on_previous_text": False}

    @pytest.mark.parametrize("preset", ["normal", "sensitive", None])
    def test_unset_repeat_guard_adds_no_penalties_in_either_preset(self, preset):
        assert presets.decode_kwargs(preset, core.WHISPER_ANTI_LOOP_KWARGS,
                                     core.WHISPER_REPEAT_GUARD_KWARGS) == {"condition_on_previous_text": False}

    @pytest.mark.parametrize("preset", ["normal", "sensitive"])
    def test_explicit_repeat_guard_wins_over_the_preset(self, preset):
        got = presets.decode_kwargs(preset, core.WHISPER_ANTI_LOOP_KWARGS,
                                    core.WHISPER_REPEAT_GUARD_KWARGS, repeat_guard=True)
        assert got == {"condition_on_previous_text": False, **core.WHISPER_REPEAT_GUARD_KWARGS}

    def test_sensitive_preset_still_lowers_the_vad_threshold_with_the_guard_on(self):
        assert presets.effective_vad_threshold(0.5, "sensitive") == 0.35


class _Seg:
    def __init__(self, start, end, text):
        self.start, self.end, self.text = start, end, text


def _fake_whisper(monkeypatch, texts, seen):
    class Model:
        def transcribe(self, audio_path, **kwargs):
            seen.update(kwargs)
            return iter([_Seg(float(i), float(i + 1), t) for i, t in enumerate(texts)]), None
    fake = types.ModuleType("faster_whisper")
    fake.WhisperModel = lambda *a, **k: Model()
    monkeypatch.setitem(sys.modules, "faster_whisper", fake)
    core._whisper_model_cache.clear()


class TestDecode:
    def test_normal_passes_the_default_decode_settings(self, monkeypatch):
        seen = {}
        _fake_whisper(monkeypatch, ["a"], seen)
        core.transcribe_for_timing("/fake.mp3", min_silence_duration_ms=300, vad_threshold=0.5)
        assert seen == {
            "language": "zh", "vad_filter": True, "beam_size": 5,
            "vad_parameters": {"min_silence_duration_ms": 300, "threshold": 0.5},
            "word_timestamps": True, "condition_on_previous_text": False}

    @pytest.mark.parametrize("preset", ["normal", "sensitive"])
    def test_repeat_guard_toggle_decides_the_penalties_in_either_preset(self, monkeypatch, preset):
        seen = {}
        _fake_whisper(monkeypatch, ["a"], seen)
        core.transcribe_for_timing("/fake.mp3", sensitivity_preset=preset, repeat_guard=True)
        assert seen["no_repeat_ngram_size"] == 3 and seen["repetition_penalty"] == 1.1

    def test_sensitive_drops_the_repeat_penalties_only(self, monkeypatch):
        seen = {}
        _fake_whisper(monkeypatch, ["a"], seen)
        core.transcribe_for_timing("/fake.mp3", vad_threshold=0.35, sensitivity_preset="sensitive")
        assert "no_repeat_ngram_size" not in seen and "repetition_penalty" not in seen
        assert seen["condition_on_previous_text"] is False
        assert seen["vad_parameters"]["threshold"] == 0.35

    def test_sensitive_still_collapses_a_runaway_repeat(self, monkeypatch):
        _fake_whisper(monkeypatch, ["thanks for watching"] * 5 + ["好的", "好的", "real"], {})
        result = core.transcribe_for_timing("/fake.mp3", sensitivity_preset="sensitive")
        assert [r["text"] for r in result] == ["thanks for watching", "好的", "好的", "real"]


class TestSavedPerTitle:
    def test_default_is_normal_and_unchanged(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        cfg = transcribe_service.get_transcribe_config(did)
        assert (cfg["sensitivity_preset"], cfg["vad_threshold"], cfg["effective_vad_threshold"]) == (
            "normal", 0.5, 0.5)

    def test_save_and_read_back(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        cfg = transcribe_service.update_transcribe_config(did, sensitivity_preset="sensitive")
        assert cfg["sensitivity_preset"] == "sensitive" and cfg["effective_vad_threshold"] == 0.35
        # The form re-sends the default number with every save: it stays untouched.
        cfg = transcribe_service.update_transcribe_config(did, vad_threshold=0.5)
        assert cfg["effective_vad_threshold"] == 0.35
        cfg = transcribe_service.update_transcribe_config(did, vad_threshold=0.4)
        assert cfg["effective_vad_threshold"] == 0.4
        cfg = transcribe_service.update_transcribe_config(did, sensitivity_preset="normal", vad_threshold=0.5)
        assert cfg["effective_vad_threshold"] == 0.5

    def test_unknown_preset_is_refused(self, isolated_db):
        did = isolated_db.create_drama(title_en="D")
        with pytest.raises(InvalidInputError):
            transcribe_service.update_transcribe_config(did, sensitivity_preset="max")
        assert isolated_db.get_drama(did)["sensitivity_preset"] is None

    def test_the_run_gets_the_effective_threshold_and_preset(self, isolated_db, monkeypatch):
        did = isolated_db.create_drama(title_en="D", audio_filename="audio.wav",
                                       transcript_mode="whisper")
        open(os.path.join(isolated_db.drama_dir(did), "audio.wav"), "wb").close()
        isolated_db.update_drama(did, sensitivity_preset="sensitive")
        captured = {}

        def fake_start(job_id, target, args=(), **k):
            import inspect
            captured.update(dict(zip(inspect.signature(target).parameters, args)))
            return True
        monkeypatch.setattr(background_jobs, "start_process_job", fake_start)
        transcribe_service.start_transcribe_run(did)
        assert captured["vad_threshold"] == 0.35 and captured["sensitivity_preset"] == "sensitive"

    def test_the_run_settings_record_the_preset(self):
        assert "sensitivity_preset" in raw_transcript.SETTINGS_KEYS
        assert raw_transcript.build_run_settings(sensitivity_preset="sensitive")["sensitivity_preset"] == "sensitive"
        assert raw_transcript.build_run_settings()["sensitivity_preset"] == "normal"
        assert raw_transcript.build_run_settings(sensitivity_preset="x")["sensitivity_preset"] == "normal"
        assert set(raw_transcript.build_run_settings()) == set(raw_transcript.SETTINGS_KEYS)


class TestCli:
    def _drama(self, db, **kw):
        did = db.create_drama(title_en="A", status="aligned", audio_filename="audio.wav", **kw)
        ddir = db.drama_dir(did)
        with open(os.path.join(ddir, "audio.wav"), "wb") as f:
            f.write(b"x")
        with open(os.path.join(ddir, "transcript.txt"), "w", encoding="utf-8") as f:
            f.write("你好")
        return did

    def _align(self, did, monkeypatch):
        seen = {}

        def fake(audio_path, model_size, **kw):
            seen.update(kw)
            return [{"start": 0.0, "end": 1.0, "text": "你好"}]
        monkeypatch.setattr(cli, "transcribe_for_timing", fake)
        with contextlib.redirect_stdout(io.StringIO()):
            cli.cmd_align(argparse.Namespace(id=did, whisper_size=None, fast=False))
        return seen

    def test_align_follows_the_titles_preset(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db, sensitivity_preset="sensitive")
        seen = self._align(did, monkeypatch)
        assert seen["sensitivity_preset"] == "sensitive" and seen["vad_threshold"] == 0.35

    def test_align_default_is_unchanged(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db)
        seen = self._align(did, monkeypatch)
        assert seen["sensitivity_preset"] == "normal" and seen["vad_threshold"] == 0.5

    def test_transcribe_flag_is_saved_on_the_title(self, isolated_db, monkeypatch):
        did = self._drama(isolated_db)
        monkeypatch.setattr(cli.transcribe_service, "start_transcribe_run",
                            lambda *a, **k: {"job_id": "transcribe_x"})
        old = sys.argv
        sys.argv = ["cli.py", "transcribe", "--id", str(did), "--sensitivity", "sensitive"]
        try:
            # The run is faked, so the wait for its job ends in an exit; only the saved option matters.
            with contextlib.suppress(SystemExit):
                cli.main()
        finally:
            sys.argv = old
        assert isolated_db.get_drama(did)["sensitivity_preset"] == "sensitive"
