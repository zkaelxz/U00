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
from services import (jobs_service, notification_service, speech_coverage_service,
                      transcribe_pipeline, transcribe_service)
from services.service_errors import ConflictError

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


def _run(did, audio):
    job_id = f"transcribe_{did}"
    assert background_jobs.start_job(
        job_id, transcribe_service._run_transcribe_and_apply_job, job_id, did, audio, "whisper",
        None, "zh", "simplified", "medium", 5, 300, 0.5, False, "auto", False, False, False,
        None, None, None, description=f"Transcription (title #{did})")
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
        with pytest.raises(ConflictError):
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
        with pytest.raises(ConflictError):
            speech_coverage_service.start_speech_coverage(did)
