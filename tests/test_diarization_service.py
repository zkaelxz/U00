"""
Tests for services/diarization_service.py: Migration Slice 16's Diarize-
stage config summary and start_diarization_run action, both shared by a
later FastAPI /api/diarization router and the Streamlit Diarize tab.

The real scope guarantee this file exists to check: the HF token value
itself never leaks out of get_diarization_config (only a bool), and
start_diarization_run never actually spawns a real subprocess/model --
job_launch.start_process_job is mocked throughout.
"""
import os

import pytest

import background_jobs
from jobs import job_launch, job_runner, process_worker
import diarize
from core import Line
from services import diarization_service, settings_service
from services.service_errors import (ConflictError, DependencyUnavailableError, InvalidInputError,
                                      NotFoundError,
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
            "speaker_notice": diarization_service.NO_TOKEN_NOTICE,
            "expected_speakers": None, "min_speakers": None, "max_speakers": None,
            "last_device": None,
            "audio_available": False,
            "manual_speaker_count": 0,
            "speaker_summary": None,
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

        def fake_start_process_job(job_id, target, args=(), gpu_touching=False, description=None,
                                   on_done=None, run_settings=None, **launch):
            calls.append({"job_id": job_id, "target": target, "args": args,
                          "gpu_touching": gpu_touching, "description": description})
            return True
        monkeypatch.setattr(job_launch, "start_process_job", fake_start_process_job)

        result = diarization_service.start_diarization_run(did, expected_speakers=3)

        assert result == {"job_id": f"diarize_{did}"}
        assert len(calls) == 1
        call = calls[0]
        assert call["job_id"] == f"diarize_{did}"
        assert call["target"] is diarize.diarize_subprocess_worker
        assert call["args"] == (os.path.join(ddir, "audio.wav"), "hf-token", 3, {"use_gpu": False, "min_speakers": None, "max_speakers": None})
        assert call["gpu_touching"] is True
        assert call["description"] == f"Diarization (title #{did})"

    def test_expected_speakers_none_is_passed_through_as_none(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf-token")
        did, ddir = _drama_with_audio(isolated_db)
        calls = []

        def fake_start_process_job(job_id, target, args=(), gpu_touching=False, description=None,
                                   on_done=None, run_settings=None, **launch):
            calls.append(args)
            return True
        monkeypatch.setattr(job_launch, "start_process_job", fake_start_process_job)

        diarization_service.start_diarization_run(did)
        assert calls[0][2] is None

    def test_already_running_job_raises_conflict(self, isolated_db, monkeypatch):
        # Migration Slice 20 fix: start_process_job returning False (a job
        # with this id is already running/queued) used to be silently
        # ignored, reporting a fake success -- now it's a real ConflictError.
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf-token")
        did, _ = _drama_with_audio(isolated_db)
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda *a, **k: False)
        with pytest.raises(ConflictError):
            diarization_service.start_diarization_run(did)


class TestApplyDiarizationResult:
    """Migration Slice 49: the on_done hook's DB work."""

    def _lines(self, db, did):
        from core import Line
        a = Line(idx=0, start=0.0, end=2.0, zh="a", en="", speaker="X")
        b = Line(idx=1, start=3.0, end=5.0, zh="b", en="", speaker="Manual")
        b.speaker_manual = True
        db.save_lines(did, [a, b])

    def test_saves_turns_and_merges_speakers_keeping_manual(self, isolated_db, monkeypatch):
        did, ddir = _drama_with_audio(isolated_db)
        self._lines(isolated_db, did)
        turns = [{"start": 0.0, "end": 2.5, "speaker": "SPEAKER_00"},
                 {"start": 2.5, "end": 6.0, "speaker": "SPEAKER_01"}]
        saves = []
        real = isolated_db.save_lines
        monkeypatch.setattr(isolated_db, "save_lines",
                            lambda *a, **k: (saves.append(k), real(*a, **k))[1])

        diarization_service.apply_diarization_result(
            did, {"segments": turns, "model": "m", "embeddings": {}}, expected_speakers=2)

        assert saves == [{"fields": ("speaker", "speaker_manual"), "only_if_unchanged": True}]
        rows = isolated_db.load_line_objects(did)
        assert rows[0].speaker == "SPEAKER_00"
        assert rows[1].speaker == "Manual" and rows[1].speaker_manual
        saved = diarize.load_turns(ddir)
        assert saved is not None
        assert diarize.load_last_speaker_count(ddir) == 2

    def test_a_speaker_edited_while_applying_is_not_overwritten(self, isolated_db, monkeypatch):
        did, _ = _drama_with_audio(isolated_db)
        self._lines(isolated_db, did)
        real_merge = diarize.merge_speakers

        def merge_then_user_edits(lines, turns, **kw):
            real_merge(lines, turns, **kw)
            edited = isolated_db.load_line_objects(did)
            edited[0].speaker = "Edited"
            edited[0].speaker_manual = True
            isolated_db.save_lines(did, edited, fields=("speaker", "speaker_manual"))

        monkeypatch.setattr(diarize, "merge_speakers", merge_then_user_edits)
        diarization_service.apply_diarization_result(
            did, {"segments": [{"start": 0.0, "end": 6.0, "speaker": "S0"}], "model": "m"})

        first = isolated_db.load_line_objects(did)[0]
        assert (first.speaker, first.speaker_manual) == ("Edited", True)

    def test_no_segments_is_a_noop(self, isolated_db):
        did, ddir = _drama_with_audio(isolated_db)
        diarization_service.apply_diarization_result(did, {})
        assert not os.path.exists(os.path.join(ddir, diarize.TURNS_FILE))

    def test_idempotent_second_apply(self, isolated_db):
        did, _ = _drama_with_audio(isolated_db)
        self._lines(isolated_db, did)
        res = {"segments": [{"start": 0.0, "end": 6.0, "speaker": "S0"}], "model": "m"}
        diarization_service.apply_diarization_result(did, res)
        first = [(l.speaker, l.speaker_manual) for l in isolated_db.load_line_objects(did)]
        diarization_service.apply_diarization_result(did, res)
        assert first == [(l.speaker, l.speaker_manual) for l in isolated_db.load_line_objects(did)]

    def test_start_run_wires_on_done(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf-token")
        did, _ = _drama_with_audio(isolated_db)
        captured = {}
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda *a, **k: captured.update(k) or True)
        applied = []
        monkeypatch.setattr(diarization_service, "apply_diarization_result",
                            lambda *a, **k: applied.append((a, k)))
        diarization_service.start_diarization_run(did, expected_speakers=4)
        captured["on_done"]("j", {"segments": []})
        assert applied == [((did, {"segments": []}, 4, False),
                            {"min_speakers": None, "max_speakers": None,
                             "speakers_at_start": {}})]

    def test_a_speaker_edited_during_detection_survives_the_result(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf-token")
        did, _ = _drama_with_audio(isolated_db)
        self._lines(isolated_db, did)
        captured = {}
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda *a, **k: captured.update(k) or True)
        diarization_service.start_diarization_run(did, overwrite_manual=True, confirm=True)
        # The user corrects line 0 while pyannote runs.
        edited = isolated_db.load_line_objects(did)
        edited[0].speaker = "Alice"
        edited[0].speaker_manual = True
        isolated_db.save_lines(did, edited, fields=("speaker", "speaker_manual"))

        captured["on_done"]("j", {"segments": [{"start": 0.0, "end": 6.0, "speaker": "S0"}],
                                  "model": "m"})

        rows = isolated_db.load_line_objects(did)
        assert (rows[0].speaker, rows[0].speaker_manual) == ("Alice", True)
        assert rows[1].speaker == "S0"      # the line nobody touched is still relabelled

    def test_start_run_passes_range_to_worker_and_on_done(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf-token")
        did, _ = _drama_with_audio(isolated_db)
        captured = {}
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda *a, **k: captured.update(k) or True)
        applied = []
        monkeypatch.setattr(diarization_service, "apply_diarization_result",
                            lambda *a, **k: applied.append((a, k)))
        diarization_service.start_diarization_run(did, min_speakers=2, max_speakers=4)
        options = captured["args"][3]
        assert (options["min_speakers"], options["max_speakers"]) == (2, 4)
        captured["on_done"]("j", {"segments": []})
        assert applied[0][1] == {"min_speakers": 2, "max_speakers": 4, "speakers_at_start": {}}


class TestOverwriteManual:
    """B-13: explicit, confirmed overwrite_manual on the API diarization run."""

    TURNS = {"segments": [{"start": 0.0, "end": 2.5, "speaker": "SPEAKER_00"},
                          {"start": 2.5, "end": 6.0, "speaker": "SPEAKER_01"}], "model": "m"}

    def _setup(self, isolated_db, monkeypatch):
        from core import Line
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf-token")
        did, _ = _drama_with_audio(isolated_db)
        a = Line(idx=0, start=0.0, end=2.0, zh="a", en="", speaker="X")
        b = Line(idx=1, start=3.0, end=5.0, zh="b", en="", speaker="Manual")
        b.speaker_manual = True
        isolated_db.save_lines(did, [a, b])
        captured = {}
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda *a, **k: captured.update(k) or True)
        return did, captured

    def _run(self, isolated_db, monkeypatch, **kw):
        did, captured = self._setup(isolated_db, monkeypatch)
        diarization_service.start_diarization_run(did, **kw)
        captured["on_done"]("j", self.TURNS)
        return isolated_db.load_line_objects(did)

    def test_default_preserves_manual_speakers(self, isolated_db, monkeypatch):
        rows = self._run(isolated_db, monkeypatch)
        assert rows[0].speaker == "SPEAKER_00"
        assert rows[1].speaker == "Manual" and rows[1].speaker_manual

    def test_overwrite_manual_with_confirm_overwrites(self, isolated_db, monkeypatch):
        rows = self._run(isolated_db, monkeypatch, overwrite_manual=True, confirm=True)
        assert rows[1].speaker == "SPEAKER_01"

    def test_overwrite_manual_without_confirm_raises_and_starts_nothing(self, isolated_db, monkeypatch):
        did, captured = self._setup(isolated_db, monkeypatch)
        with pytest.raises(InvalidInputError):
            diarization_service.start_diarization_run(did, overwrite_manual=True)
        assert captured == {}

    def test_api_overwrite_without_confirm_is_422(self, isolated_db, monkeypatch):
        pytest.importorskip("fastapi")
        from fastapi.testclient import TestClient
        from api.server import app
        did, captured = self._setup(isolated_db, monkeypatch)
        c = TestClient(app, headers={"X-Baihe-Local": "1"})
        r = c.post(f"/api/diarization/dramas/{did}/run?overwrite_manual=true")
        assert r.status_code == 422 and captured == {}
        r = c.post(f"/api/diarization/dramas/{did}/run?overwrite_manual=true&confirm=true")
        assert r.status_code == 200


class TestDiarizationSpeedRecording:
    def _run(self, isolated_db, monkeypatch, result):
        from services import transcribe_pipeline, transcribe_speed_service
        did = isolated_db.create_drama(title_en="D")
        monkeypatch.setattr(diarization_service, "apply_diarization_result", lambda *a, **k: None)
        monkeypatch.setattr(diarization_service, "_drama_audio_path", lambda d, dr: "/x.wav")
        monkeypatch.setattr(transcribe_pipeline, "_audio_duration_seconds", lambda p: 600.0)
        diarization_service.make_apply_on_done(did)("diarize_1", result)
        return transcribe_speed_service

    def test_a_finished_run_records_its_seconds_per_device(self, isolated_db, monkeypatch):
        ts = self._run(isolated_db, monkeypatch, {"segments": [], "device": "cuda", "seconds": 60.0})
        assert ts.measured_diarize_runs(True) == 1 and ts.measured_diarize_runs(False) == 0

    def test_a_result_without_seconds_is_not_recorded(self, isolated_db, monkeypatch):
        ts = self._run(isolated_db, monkeypatch, {"segments": [], "device": "cpu"})
        assert ts.measured_diarize_runs(False) == 0


def test_config_counts_hand_corrected_speakers(isolated_db):
    """Parity D06: how many speakers a run with overwrite_manual would replace."""
    from core import Line
    did, _ = _drama_without_audio(isolated_db)
    assert diarization_service.get_diarization_config(did)["manual_speaker_count"] == 0
    isolated_db.save_lines(did, [
        Line(idx=0, start=0, end=1, zh="a", speaker="A", speaker_manual=True),
        Line(idx=1, start=1, end=2, zh="b", speaker="B"),
        Line(idx=2, start=2, end=3, zh="c", speaker="C", speaker_manual=True)])
    assert diarization_service.get_diarization_config(did)["manual_speaker_count"] == 2


def test_api_config_carries_manual_speaker_count(isolated_db):
    pytest.importorskip("fastapi")
    pytest.importorskip("httpx")
    from fastapi.testclient import TestClient
    from api.api_config import ApiSettings
    from api.server import create_app
    from core import Line
    did, _ = _drama_without_audio(isolated_db)
    isolated_db.save_lines(did, [Line(idx=0, start=0, end=1, zh="a", speaker="A",
                                      speaker_manual=True)])
    c = TestClient(create_app(ApiSettings()), raise_server_exceptions=False)
    r = c.get(f"/api/diarization/dramas/{did}/config")
    assert r.status_code == 200 and r.json()["manual_speaker_count"] == 1


def test_on_done_reports_cpu_fallback_loudly(isolated_db, monkeypatch):
    messages = []
    monkeypatch.setattr(background_jobs, "update_progress",
                        lambda job_id, frac, msg="": messages.append(msg))
    monkeypatch.setattr(diarization_service, "apply_diarization_result", lambda *a, **k: None)
    on_done = diarization_service.make_apply_on_done(1)
    assert on_done("j", {"segments": [], "fell_back_to_cpu": True,
                         "fallback_reason": "C:\\secret\\path"}) == {
        "device": "cpu", "gpu_fallback": diarize.OOM_FALLBACK_DONE_MESSAGE,
        "device_notice": diarize.OOM_FALLBACK_DONE_MESSAGE}
    assert messages[-1].startswith(diarize.OOM_FALLBACK_DONE_MESSAGE)
    assert on_done("j", {"segments": [], "fell_back_to_cpu": True, "fallback_kind": "placement"})[
        "device_notice"] == diarize.PLACEMENT_FALLBACK_DONE_MESSAGE
    assert on_done("j", {"segments": [], "fell_back_to_cpu": False}) is None
    assert messages[-1] == "Matching speakers to lines..."


def test_a_run_that_finds_no_speech_keeps_every_speaker(isolated_db, monkeypatch):
    """Silent or music-only audio: no turns must not read as "nobody speaks here"."""
    did = isolated_db.create_drama(title_en="D")
    isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="一", speaker="SPEAKER_00"),
                                 Line(idx=1, start=1.0, end=2.0, zh="二", speaker="SPEAKER_01")])
    monkeypatch.setattr(background_jobs, "update_progress", lambda *a, **k: None)
    on_done = diarization_service.make_apply_on_done(did)
    assert on_done("j", {"segments": [], "device": "cpu"}) == {"failed_reason": "empty"}
    assert [r["speaker"] for r in isolated_db.load_lines(did)] == ["SPEAKER_00", "SPEAKER_01"]
    # Nothing saved either, so Re-assign speakers can't apply the empty run later.
    assert diarize.load_turns(isolated_db.drama_dir(did)) is None


def test_a_cpu_run_after_a_gpu_worker_crash_says_it_ran_on_the_cpu(isolated_db, monkeypatch):
    from jobs import crash_retry
    import whisper_models
    monkeypatch.setattr(background_jobs, "update_progress", lambda *a, **k: None)
    did = isolated_db.create_drama(title_en="D")
    isolated_db.save_lines(did, [Line(idx=0, start=0.0, end=1.0, zh="a")])
    reason = crash_retry.crash_reason(3221225477)
    result = {"segments": [{"start": 0.0, "end": 1.0, "speaker": "SPEAKER_00"}],
              "device": "cpu", crash_retry.REASON_KEY: reason}
    out = diarization_service.make_apply_on_done(did)("j", result)
    notice = whisper_models.gpu_fallback_notice("Speaker detection", reason)
    assert out == {"device": "cpu", "gpu_fallback": notice, "device_notice": notice}
    assert crash_retry.REASON_KEY not in result


def test_only_a_gpu_speaker_run_has_a_crash_retry():
    gpu = ("a.wav", "hf", 2, {"use_gpu": True, "min_speakers": 1, "max_speakers": None})
    assert diarization_service.cpu_retry_args(gpu) == (
        "a.wav", "hf", 2, {"use_gpu": False, "min_speakers": 1, "max_speakers": None})
    assert diarization_service.cpu_retry_args(("a.wav", "hf", 2, {"use_gpu": False})) is None


def test_the_missing_pyannote_import_error_is_redacted_in_the_result(monkeypatch):
    import queue
    secret = "sk-ant-api03-" + "a" * 40

    def no_pyannote(*a, **k):
        raise ModuleNotFoundError(f"No module named 'pyannote' ({secret})", name="pyannote")
    monkeypatch.setattr(diarize, "diarize", no_pyannote)
    monkeypatch.setattr(process_worker, "start_own_process_group", lambda: None)
    q = queue.Queue()
    diarize.diarize_subprocess_worker("a.wav", "hf", None, {}, q)
    _, result = q.get_nowait()
    assert "No module named 'pyannote'" in result["import_error"]
    assert secret not in result["import_error"]


def test_a_missing_pyannote_reads_as_the_install_sentence(isolated_db, monkeypatch):
    import builtins
    import queue
    import applog
    real_import = builtins.__import__

    def no_pyannote(name, *a, **k):
        if name.startswith("pyannote"):
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)
        return real_import(name, *a, **k)
    monkeypatch.setattr(diarize, "diarize",
                        lambda *a, **k: no_pyannote("pyannote.audio"))
    monkeypatch.setattr(process_worker, "start_own_process_group", lambda: None)
    q = queue.Queue()
    diarize.diarize_subprocess_worker("a.wav", "hf", None, {}, q)
    kind, result = q.get_nowait()
    assert kind == "ok"
    assert result["failed_reason"] == "dependency_missing"
    assert result["detail"] == diarization_service.NO_PYANNOTE_NOTICE
    monkeypatch.setattr(background_jobs, "update_progress", lambda *a, **k: None)
    did = isolated_db.create_drama(title_en="D")
    diarization_service.make_apply_on_done(did)("j", result)
    assert "import_error" not in result
    for h in applog.get_logger().handlers:
        h.flush()
    with open(os.path.join(isolated_db.LIBRARY_DIR, "logs", "app.log"), encoding="utf-8") as f:
        assert "No module named 'pyannote.audio'" in f.read()
    status_error = job_runner._failed_outcome_error(result)
    assert status_error == diarization_service.NO_PYANNOTE_NOTICE


class TestRunSettings:
    def test_the_settings_travel_with_the_start_not_after_it(self, isolated_db, monkeypatch):
        monkeypatch.setattr(settings_service, "resolve_key", lambda key, env_path=None: "hf-token")
        did, _ = _drama_with_audio(isolated_db)
        seen = []
        monkeypatch.setattr(job_launch, "start_process_job",
                            lambda job_id, target, **k: seen.append(k["run_settings"]) or False)
        with pytest.raises(ConflictError):
            diarization_service.start_diarization_run(did, expected_speakers=3)
        assert seen[0]["expected_speakers"] == 3


class GatedRepoError(OSError):
    """Named as huggingface_hub's own: the worker matches it by name."""


def test_a_refused_hugging_face_model_reads_as_the_token_sentence(isolated_db, monkeypatch):
    import queue
    import applog
    url = "https://huggingface.co/pyannote/speaker-diarization-community-1/resolve/main/config.yaml"

    def gated(*a, **k):
        try:
            raise GatedRepoError(f"Cannot access gated repo for url {url}.")
        except GatedRepoError as exc:
            raise RuntimeError("pipeline load failed") from exc
    monkeypatch.setattr(diarize, "diarize", gated)
    monkeypatch.setattr(process_worker, "start_own_process_group", lambda: None)
    q = queue.Queue()
    diarize.diarize_subprocess_worker("a.wav", "hf", None, {}, q)
    kind, result = q.get_nowait()
    assert kind == "ok" and result["failed_reason"] == "dependency_missing"
    assert result["detail"] == diarization_service.NO_TOKEN_NOTICE
    assert job_runner._failed_outcome_error(result) == diarization_service.NO_TOKEN_NOTICE
    monkeypatch.setattr(background_jobs, "update_progress", lambda *a, **k: None)
    did = isolated_db.create_drama(title_en="D")
    diarization_service.make_apply_on_done(did)("j", result)
    assert url not in str(result)
    for h in applog.get_logger().handlers:
        h.flush()
    with open(os.path.join(isolated_db.LIBRARY_DIR, "logs", "app.log"), encoding="utf-8") as f:
        assert "Hugging Face refused the model" in f.read()


def test_any_other_load_failure_is_still_the_workers_error(monkeypatch):
    import queue
    monkeypatch.setattr(diarize, "diarize", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    monkeypatch.setattr(process_worker, "start_own_process_group", lambda: None)
    q = queue.Queue()
    diarize.diarize_subprocess_worker("a.wav", "hf", None, {}, q)
    assert q.get_nowait()[:3] == ("error", "OSError", "disk")


def test_pyannote_3_returning_no_pipeline_reads_as_the_token_sentence(monkeypatch):
    # pyannote 3.x prints Hugging Face's refusal and returns None instead of raising.
    import queue

    def none_returned(*a, **k):
        raise diarize.NoPipelineReturned("pyannote/speaker-diarization-3.1 returned no pipeline")
    monkeypatch.setattr(diarize, "diarize", none_returned)
    monkeypatch.setattr(process_worker, "start_own_process_group", lambda: None)
    q = queue.Queue()
    diarize.diarize_subprocess_worker("a.wav", "hf", None, {}, q)
    kind, result = q.get_nowait()
    assert kind == "ok" and result["detail"] == diarization_service.NO_TOKEN_NOTICE


def test_a_refusal_seen_only_as_implicit_context_still_counts():
    # The fallback chain sets __cause__ to the first model's error; the
    # last model's own context (here the 401) must still be looked at.
    first = ValueError("community-1: older pyannote")
    try:
        try:
            raise GatedRepoError("401")
        except GatedRepoError:
            raise OSError("3.1 load failed")
    except OSError as last:
        last.__cause__ = first
        assert diarize._hf_access_refused(last)
    assert not diarize._hf_access_refused(first)
