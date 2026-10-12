"""A transcription that cannot run or finds nothing must say so: the CTranslate2
allowlist accepts the real anime-whisper listing, a refused model download ends the
job as Failed (and starts no coverage check), and zero lines is not plain success.

Fakes only: no network, no weights.
"""
import os
import time

import pytest

import background_jobs
import whisper_models
from lib import ct2_repo
import diagnostics
from services import (diarization_service, jobs_service, notification_service, speech_coverage_service,
                      transcribe_pipeline, transcribe_service)
from services.service_errors import UnsupportedOperationError

ANIME_LISTING = [
    ".gitattributes", "README.md", "added_tokens.json", "config.json", "generation_config.json",
    "model.bin", "normalizer.json", "preprocessor_config.json", "special_tokens_map.json",
    "tokenizer.json", "tokenizer_config.json", "vocabulary.json"]
FIVE = ("added_tokens.json", "generation_config.json", "normalizer.json",
        "special_tokens_map.json", "tokenizer_config.json")
KOTOBA_LISTING = [".gitattributes", "README.md", "config.json", "model.bin",
                  "preprocessor_config.json", "tokenizer.json", "vocabulary.json"]
WEIGHTS_SET = ["config.json", "model.bin", "preprocessor_config.json", "tokenizer.json",
               "vocabulary.json"]


class TestAllowlist:
    def test_real_anime_whisper_listing_is_accepted_and_downloads_the_same_files(self):
        assert ct2_repo.select_ct2_files(ANIME_LISTING) == WEIGHTS_SET

    def test_the_other_pinned_listings_are_accepted(self):
        assert ct2_repo.select_ct2_files(KOTOBA_LISTING) == WEIGHTS_SET

    @pytest.mark.parametrize("name", FIVE)
    def test_inert_json_is_allowed_but_never_fetched(self, name):
        assert name in ct2_repo.CT2_ALLOWED and name in ct2_repo.CT2_NOT_FETCHED

    @pytest.mark.parametrize("bad", ["modeling_x.py", "pytorch_model.bin", "sub/model.bin",
                                     "extra.json", "model.safetensors"])
    def test_everything_else_is_still_refused(self, bad):
        with pytest.raises(ct2_repo.RepoFilesRefused):
            ct2_repo.select_ct2_files(ANIME_LISTING + [bad])

    def test_the_tool_shares_the_app_allowlist(self):
        from tools import ja_whisper_models
        assert ja_whisper_models.CT2_ALLOWED is ct2_repo.CT2_ALLOWED


def _wait_finished(job_id, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        job = background_jobs.get_status(job_id)
        if job and job["status"] in ("done", "error", "cancelled"):
            # The status is set before the notification and cleanup run; a thread still
            # unwinding after the test would reach the real library once the fixtures end.
            assert background_jobs.wait_for_job_threads(10)
            return job
        time.sleep(0.02)
    raise AssertionError("job did not finish")


@pytest.fixture(autouse=True)
def _no_real_whisper_load(monkeypatch):
    monkeypatch.setattr(whisper_models, "load_whisper_model", lambda *a, **k: object())


@pytest.fixture
def drama(isolated_db):
    did = isolated_db.create_drama(title_en="D", audio_filename="audio.wav")
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, "audio.wav"), "wb").close()
    yield did, os.path.join(ddir, "audio.wav")
    for job_id in (f"transcribe_{did}", speech_coverage_service.speech_coverage_job_id(did)):
        background_jobs.clear_job(job_id)


def _run(did, audio, hf_token=None, **kwargs):
    job_id = f"transcribe_{did}"
    assert background_jobs.start_job(
        job_id, transcribe_service._run_transcribe_and_apply_job, job_id, did, audio, "whisper",
        None, "zh", "simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False,
        None, hf_token, None, description=f"Transcription (title #{did})", **kwargs)
    return job_id, _wait_finished(job_id)


class TestRefusedDownloadFailsTheJob:
    @pytest.fixture(autouse=True)
    def _refused(self, monkeypatch):
        def refuse(*a, **k):
            raise whisper_models.ModelDownloadError(
                "Not downloading anime-whisper: the repository contains 'added_tokens.json', "
                "which is not on the allowlist. Nothing was downloaded.")
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", refuse)
        self.sent = []
        monkeypatch.setattr(notification_service, "notify_job_finished",
                            lambda *a, **k: self.sent.append((a, k)))

    def test_job_status_is_failed_with_a_plain_reason(self, drama):
        job_id, job = _run(*drama)
        assert job["status"] == "error"
        assert "could not be downloaded" in job["error"] and "added_tokens.json" in job["error"]
        assert "/" not in job["error"] and "http" not in job["error"]
        assert jobs_service.derive_outcome(job["status"], job["error"], job["result"])[0] == "failed"

    def test_the_bell_says_failed_with_the_reason(self, drama):
        _run(*drama)
        (args, kwargs), = self.sent
        assert args[1] == "error"
        message = notification_service.build_message(args[0], args[1], reason=kwargs["reason"])
        assert message.startswith("Failed: Transcription") and "could not be downloaded" in message

    def test_no_coverage_check_follows(self, drama):
        did, _ = drama
        _run(*drama)
        with pytest.raises(UnsupportedOperationError):
            speech_coverage_service.start_speech_coverage(did)
        assert background_jobs.get_status(speech_coverage_service.speech_coverage_job_id(did)) is None


class TestZeroLines:
    def test_no_segments_is_finished_but_found_no_lines(self, drama, monkeypatch):
        did, audio = drama
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", lambda *a, **k: [])
        job_id, job = _run(did, audio)
        assert job["status"] == "done"
        assert job["result"]["failed_reason"] == "no_lines"
        assert "ASR output" in job["result"]["stage_summary"] and ": 0" in job["result"]["stage_summary"]
        outcome, message = jobs_service.derive_outcome(job["status"], None, job["result"])
        assert outcome == "partial"
        assert message.startswith("Finished but found no lines")
        for hint in ("More sensitive", "large-v3-turbo", "silent"):
            assert hint in message

    def test_blank_segments_count_as_no_lines_too(self, drama, monkeypatch):
        did, audio = drama
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "  "}])
        _, job = _run(did, audio)
        assert job["result"]["failed_reason"] == "no_lines"

    def test_no_coverage_check_after_zero_lines(self, drama, monkeypatch):
        did, audio = drama
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", lambda *a, **k: [])
        _run(did, audio)
        with pytest.raises(UnsupportedOperationError):
            speech_coverage_service.start_speech_coverage(did)


class TestSpeakerDetectionSkipped:
    @pytest.fixture(autouse=True)
    def _asr(self, monkeypatch):
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing",
                            lambda *a, **k: [{"start": 0.0, "end": 1.0, "text": "hi"}])
        self.started = []
        monkeypatch.setattr(background_jobs, "start_process_job",
                            lambda job_id, target, args=(), **k: self.started.append(job_id) or True)
        self.monkeypatch = monkeypatch

    def _installed(self, yes):
        real = diagnostics.check_dependency
        self.monkeypatch.setattr(diagnostics, "check_dependency",
                                 lambda name: yes if name == "pyannote.audio" else real(name))

    def _bell(self, job):
        return notification_service.build_message(
            job["description"], job["status"], reason=transcribe_service.background_jobs._done_note(job["result"]))

    def test_no_token_says_so_in_outcome_and_bell(self, drama):
        self._installed(True)
        _, job = _run(*drama, diarize_requested=True)
        assert job["result"]["diarize_skipped_reason"] == "no Hugging Face token"
        outcome, message = jobs_service.derive_outcome(job["status"], None, job["result"])
        assert outcome == "partial"
        assert "Speaker detection did not run: no Hugging Face token." in message
        assert self._bell(job).endswith("Speaker detection did not run: no Hugging Face token.")
        assert not self.started

    def test_pyannote_missing(self, drama):
        self._installed(False)
        did, audio = drama
        _, job = _run(did, audio, hf_token="hf_x", diarize_requested=True, diarize_audio_path=audio)
        assert job["result"]["diarize_skipped_reason"] == "pyannote.audio is not installed"
        assert not self.started

    def test_no_audio_for_the_title(self, drama):
        self._installed(True)
        _, job = _run(*drama, hf_token="hf_x", diarize_requested=True, diarize_audio_path=None)
        assert job["result"]["diarize_skipped_reason"] == "no audio file for this title"

    def test_zero_lines_names_it(self, drama, monkeypatch):
        monkeypatch.setattr(transcribe_pipeline, "transcribe_for_timing", lambda *a, **k: [])
        _, job = _run(*drama, hf_token="hf_x", diarize_requested=True)
        _, message = jobs_service.derive_outcome(job["status"], None, job["result"])
        assert "Speaker detection did not run: the transcription produced no lines." in message

    def test_switch_off_says_nothing(self, drama):
        _, job = _run(*drama)
        assert job["result"]["diarize_skipped_reason"] is None
        assert "Speaker detection" not in jobs_service.derive_outcome("done", None, job["result"])[1]

    def test_token_and_audio_still_start_diarization(self, drama):
        self._installed(True)
        did, audio = drama
        _, job = _run(did, audio, hf_token="hf_x", diarize_requested=True, diarize_audio_path=audio)
        assert job["result"]["diarize_started"] is True and self.started == [f"diarize_{did}"]
        assert job["result"]["diarize_skipped_reason"] is None


class TestSpeakerNotice:
    def test_no_token_notice_is_actionable_and_has_no_secret(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diarization_service.settings_service, "resolve_key", lambda name: "")
        code, text = diarization_service.speaker_detection_problem()
        assert code == "no_token"
        assert "Hugging Face token" in text and "Settings" in text and "terms" in text

    def test_token_but_no_package(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diarization_service.settings_service, "resolve_key", lambda name: "hf_x")
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: False)
        assert diarization_service.speaker_detection_problem()[0] == "no_pyannote"

    def test_nothing_in_the_way(self, isolated_db, monkeypatch):
        monkeypatch.setattr(diarization_service.settings_service, "resolve_key", lambda name: "hf_x")
        monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
        assert diarization_service.speaker_detection_problem() is None


class TestCoverageAfterAFailedRerun:
    def test_existing_lines_keep_the_check_available(self, isolated_db, drama, monkeypatch):
        did, audio = drama
        from core import Line
        isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="old")])
        monkeypatch.setattr(background_jobs, "start_job", lambda *a, **k: True)
        assert speech_coverage_service.start_speech_coverage(did) == {
            "job_id": speech_coverage_service.speech_coverage_job_id(did)}


class TestModelDownloadWordingPerJobKind:
    @pytest.mark.parametrize("job_id,start", [
        ("transcribe_1", "The speech model could not be downloaded."),
        ("retime_1", "The aligner model could not be downloaded."),
        ("resegment_1", "The aligner model could not be downloaded."),
        ("comparetx_1", "A model could not be downloaded."),
        ("something_1", "A model could not be downloaded.")])
    def test_wording(self, job_id, start):
        text = background_jobs._missing_dependency_error(
            {"failed_reason": "model_download", "detail": "Not downloading the 'x' model."}, job_id)
        assert text == f"{start} Not downloading the 'x' model."


class TestHardsubNoText:
    def test_outcome_is_partial_with_reasons(self):
        outcome, message = jobs_service.derive_outcome("done", None, {"failed_reason": "no_text"})
        assert outcome == "partial"
        assert message.startswith("Finished but found no on-screen text")
        assert "OCR language" in message and "burned-in" in message
