"""
Tests for services/diarization_service.py: Migration Slice 16's Diarize-
stage config summary and start_diarization_run action, both shared by a
later FastAPI /api/diarization router and the Streamlit Diarize tab.

The real scope guarantee this file exists to check: the HF token value
itself never leaks out of get_diarization_config (only a bool), and
start_diarization_run never actually spawns a real subprocess/model --
background_jobs.start_process_job is mocked throughout.
"""
import os

import pytest

import background_jobs
import diarize
from services import diarization_service, settings_service
from services.service_errors import (DependencyUnavailableError, NotFoundError,
                                      UnsupportedOperationError)


def _drama_with_audio(isolated_db, **fields):
    fields.setdefault("title_en", "D")
    fields.setdefault("audio_filename", "audio.wav")
    did = isolated_db.create_drama(**fields)
    ddir = isolated_db.drama_dir(did)
    os.makedirs(ddir, exist_ok=True)
    open(os.path.join(ddir, fields["audio_filename"]), "wb").close()
    return did, ddir


def _drama_without_audio(isolated_db, **fields):
    fields.setdefault("title_en", "D")
    did = isolated_db.create_drama(**fields)
    return did, isolated_db.drama_dir(did)


class TestGetDiarizationConfig:
    def test_unknown_drama_raises(self, isolated_db):
        with pytest.raises(NotFoundError):
            diarization_service.get_diarization_config(999999)

    def test_no_audio_and_no_prior_run(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: None)
        did, _ = _drama_without_audio(isolated_db)
        result = diarization_service.get_diarization_config(did)
        assert result == {
            "drama_id": did,
            "hf_token_configured": False,
            "expected_speakers": None,
            "audio_available": False,
        }

    def test_audio_present_is_reported(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: None)
        did, _ = _drama_with_audio(isolated_db)
        result = diarization_service.get_diarization_config(did)
        assert result["audio_available"] is True

    def test_missing_audio_file_on_disk_is_not_available(self, isolated_db, monkeypatch):
        # audio_filename is set on the drama row, but the file was never
        # actually written to disk -- must not be reported as available.
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: None)
        did, _ = _drama_without_audio(isolated_db, audio_filename="audio.wav")
        result = diarization_service.get_diarization_config(did)
        assert result["audio_available"] is False

    def test_expected_speakers_reflects_last_real_run(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: None)
        did, ddir = _drama_with_audio(isolated_db)
        monkeypatch.setattr(diarize, "load_last_speaker_count",
                            lambda drama_dir: 3 if drama_dir == ddir else None)
        result = diarization_service.get_diarization_config(did)
        assert result["expected_speakers"] == 3

    def test_hf_token_configured_true_when_resolved(self, isolated_db, monkeypatch):
        did, _ = _drama_without_audio(isolated_db)
        monkeypatch.setattr(settings_service, "resolve_key",
                            lambda key, env_path=None: "secret-token" if key == "hf_token" else None)
        result = diarization_service.get_diarization_config(did)
        assert result["hf_token_configured"] is True

    def test_hf_token_configured_false_when_not_resolved(self, isolated_db, monkeypatch):
        did, _ = _drama_without_audio(isolated_db)
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: None)
        result = diarization_service.get_diarization_config(did)
        assert result["hf_token_configured"] is False

    def test_token_value_never_leaks_into_the_result(self, isolated_db, monkeypatch):
        did, _ = _drama_without_audio(isolated_db)
        monkeypatch.setattr(settings_service, "resolve_key",
                            lambda key, env_path=None: "super-secret-hf-token" if key == "hf_token" else None)
        result = diarization_service.get_diarization_config(did)
        assert "super-secret-hf-token" not in str(result)
        assert result["hf_token_configured"] is True


class TestStartDiarizationRun:
    def test_unknown_drama_raises(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf-token")
        with pytest.raises(NotFoundError):
            diarization_service.start_diarization_run(999999)

    def test_missing_hf_token_raises_dependency_unavailable(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: None)
        did, _ = _drama_with_audio(isolated_db)
        with pytest.raises(DependencyUnavailableError):
            diarization_service.start_diarization_run(did)

    def test_missing_audio_raises_unsupported_operation(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf-token")
        did, _ = _drama_without_audio(isolated_db)
        with pytest.raises(UnsupportedOperationError):
            diarization_service.start_diarization_run(did)

    def test_starts_the_background_job_with_expected_args(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf-token")
        did, ddir = _drama_with_audio(isolated_db)
        calls = []

        def fake_start_process_job(job_id, target, args=(), gpu_touching=False, description=None):
            calls.append({"job_id": job_id, "target": target, "args": args,
                          "gpu_touching": gpu_touching, "description": description})
            return True
        monkeypatch.setattr(background_jobs, "start_process_job", fake_start_process_job)

        result = diarization_service.start_diarization_run(did, expected_speakers=3)

        assert result == {"job_id": f"diarize_{did}"}
        assert len(calls) == 1
        call = calls[0]
        assert call["job_id"] == f"diarize_{did}"
        assert call["target"] is diarize.diarize_subprocess_worker
        assert call["args"] == (os.path.join(ddir, "audio.wav"), "hf-token", 3)
        assert call["gpu_touching"] is True
        assert call["description"] == f"Diarization (drama #{did})"

    def test_expected_speakers_none_is_passed_through_as_none(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf-token")
        did, ddir = _drama_with_audio(isolated_db)
        calls = []

        def fake_start_process_job(job_id, target, args=(), gpu_touching=False, description=None):
            calls.append(args)
            return True
        monkeypatch.setattr(background_jobs, "start_process_job", fake_start_process_job)

        diarization_service.start_diarization_run(did)
        assert calls[0][2] is None
