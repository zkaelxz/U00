"""The warning when the audio's language differs from the title's."""
import os
import subprocess

import pytest

import background_jobs
import whisper_models
import whisper_transcribe
from services import transcribe_pipeline, transcribe_service
from tests.test_transcribe_service import _clear, _drama_with_audio, _seed_running_job

NOTICE = "The audio sounds like Japanese, but this title is set to Chinese."


class FakeModel:
    def __init__(self, result=("ja", 0.95, []), error=None):
        self.result, self.error, self.calls = result, error, 0

    def detect_language(self, audio, **kwargs):
        self.calls += 1
        if self.error:
            raise self.error
        return self.result


def _check(model, title, monkeypatch):
    monkeypatch.setattr(whisper_transcribe, "_decode_opening", lambda path, seconds: [0.0] * 16000)
    return whisper_transcribe.language_mismatch_notice(model, "a.wav", title)


@pytest.mark.parametrize("model, title, expected", [
    (FakeModel(("ja", 0.95, [])), "zh", NOTICE),
    (FakeModel(("ja", 0.5, [])), "zh", None),
    (FakeModel(("zh", 0.99, [])), "zh", None),
    (FakeModel(("ru", 0.99, [])), "zh", None),
    (FakeModel(error=RuntimeError("no detector")), "zh", None),
    (FakeModel(("ja", 0.95, [])), "auto", None),
    (FakeModel(("ja", 0.95, [])), "", None),
])
def test_notice_only_for_a_confident_different_language(monkeypatch, model, title, expected):
    assert _check(model, title, monkeypatch) == expected


def test_a_failed_audio_decode_gives_no_notice(monkeypatch):
    def boom(path):
        raise OSError("unreadable")
    monkeypatch.setattr(whisper_transcribe, "_decode_opening", boom)
    assert whisper_transcribe.language_mismatch_notice(FakeModel(), "a.wav", "zh") is None


class TestPipeline:
    @pytest.fixture(autouse=True)
    def _no_real_model(self, monkeypatch):
        monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: object())

    def _run(self, isolated_db, monkeypatch, notice, groq=False):
        did, ddir = _drama_with_audio(isolated_db, transcript_mode="whisper")
        job_id = f"transcribe_{did}"
        _seed_running_job(job_id)
        calls = {"transcribe": []}

        def fake_transcribe(*a, on_notice=None, **k):
            calls["transcribe"].append(k)
            if notice and on_notice:
                on_notice(notice)
            return [{"start": 0.0, "end": 1.0, "text": "hi"}]
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", fake_transcribe)
        monkeypatch.setattr(whisper_transcribe, "transcribe_with_groq",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
        transcribe_service._run_transcribe_and_apply_job(
            job_id, did, os.path.join(ddir, "audio.wav"), "whisper", None, "zh", "simplified",
            "medium", 5, 300, 0.5, False, "auto", False, False, groq, "key" if groq else None,
            None, None)
        result = background_jobs.get_status(job_id)["result"]
        _clear(job_id)
        return result, calls

    def test_one_notice_and_the_transcription_call_is_unchanged(self, isolated_db, monkeypatch):
        result, calls = self._run(isolated_db, monkeypatch, NOTICE)
        assert result["coverage_warning"].startswith(NOTICE)
        assert result["coverage_warning"].count("The audio sounds like") == 1
        kwargs, = calls["transcribe"]
        assert kwargs["language"] == "zh"

    def test_no_notice_when_the_check_finds_nothing(self, isolated_db, monkeypatch):
        result, calls = self._run(isolated_db, monkeypatch, None)
        assert "sounds like" not in (result["coverage_warning"] or "")
        assert len(calls["transcribe"]) == 1

    def test_cloud_engine_makes_no_detection_call(self, isolated_db, monkeypatch):
        result, calls = self._run(isolated_db, monkeypatch, NOTICE, groq=True)
        assert calls["transcribe"] == []
        assert "sounds like" not in (result["coverage_warning"] or "")


def test_only_the_opening_of_a_long_file_is_decoded(monkeypatch):
    seen = {}

    def run(cmd, **kwargs):
        seen.update(cmd=cmd, timeout=kwargs.get("timeout"))
        return type("Done", (), {"stdout": b"\0" * 4 * 16000})()
    monkeypatch.setattr(subprocess, "run", run)
    audio = whisper_transcribe._decode_opening("two_hours.wav", whisper_transcribe.LANGUAGE_CHECK_WINDOW_S)
    assert len(audio) == 16000
    cmd = seen["cmd"]
    assert cmd[cmd.index("-t") + 1] == str(whisper_transcribe.LANGUAGE_CHECK_WINDOW_S)
    assert cmd.index("-t") < cmd.index("-i")
    assert seen["timeout"]
