"""The `smoke` CLI command (real_model_check_cli.py). Every check is faked: no
GPU, models or network."""
import sys
import time

import pytest

import background_jobs
import real_model_check_cli as smoke
from services import library_admin_service
from services import real_model_check_service as svc

SECRET = "sk-ant-api03-SECRETSECRETSECRET123456"


def _skip(**_):
    raise svc._Skip("paddleocr is not installed.")


def _unsure(**_):
    raise svc._CouldNotCheck("Could not check where the models are.")


def _boom(**_):
    raise RuntimeError(f"bad {SECRET} at /home/someone/private/models/x.py")


def _fakes(*overrides):
    base = {"asr": lambda **_: "whisper ran.", "ocr": lambda **_: "paddle read 2 character(s).",
            "translate": lambda **_: "gemma translated one line."}
    base.update(dict(overrides))
    labels = {"asr": "Transcription", "ocr": "OCR", "translate": "Translation (Ollama)"}
    return tuple((k, labels[k], fn) for k, fn in base.items())


@pytest.fixture
def env(isolated_db, monkeypatch):
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)
    monkeypatch.setattr("core.release_gpu_models", lambda: None)
    background_jobs.clear_job(svc.JOB_ID)
    svc._STATE.update(checks=[], finished=False)
    yield
    background_jobs.clear_job(svc.JOB_ID)


def _run(capsys, **kw):
    code = smoke.run(**kw)
    return code, capsys.readouterr().out


def test_all_four_statuses_are_reported_distinctly_and_only_a_fail_exits_nonzero(env, monkeypatch, capsys):
    monkeypatch.setattr(svc, "_CHECKS", _fakes(("ocr", _skip), ("translate", _unsure)))
    code, out = _run(capsys)
    assert code == 0
    assert "[PASS] Transcription" in out
    assert "[SKIPPED] OCR" in out
    assert "[COULD NOT CHECK] Translation (Ollama)" in out
    assert "1 pass, 0 fail, 1 skipped, 1 could not check" in out

    monkeypatch.setattr(svc, "_CHECKS", _fakes(("ocr", _boom)))
    code, out = _run(capsys)
    assert code == 1 and "[FAIL] OCR" in out and "1 fail" in out


def test_skipped_and_could_not_check_are_never_shown_as_passes(env, monkeypatch, capsys):
    monkeypatch.setattr(svc, "_CHECKS", (("ocr", "OCR", _skip), ("asr", "Transcription", _unsure)))
    code, out = _run(capsys)
    assert code == 0 and "0 pass" in out and "[PASS]" not in out


def test_output_has_no_keys_or_paths(env, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(svc, "_CHECKS", _fakes(("asr", _boom)))
    monkeypatch.setattr(svc, "_CLIP", str(tmp_path / "clip.wav"))
    code, out = _run(capsys)
    assert code == 1
    assert SECRET not in out and "/home/someone" not in out and str(tmp_path) not in out


def test_unexpected_errors_are_redacted_too(env, monkeypatch, capsys):
    def explode(**_):
        raise RuntimeError(f"gpu slot broke {SECRET} in /home/someone/lib")
    monkeypatch.setattr(svc, "run_checks", explode)
    code, out = _run(capsys)
    assert code == 1 and "stopped" in out
    assert SECRET not in out and "/home/someone" not in out


def test_the_gpu_slot_is_released_after_a_run(env, monkeypatch, capsys):
    import db
    monkeypatch.setattr(svc, "_CHECKS", _fakes())
    _run(capsys)
    assert db.gpu_lock_status() == (None, None)


def test_a_busy_gpu_is_reported_and_nothing_runs(env, monkeypatch, capsys):
    ran = []
    monkeypatch.setattr(svc, "_CHECKS", _fakes(("asr", lambda **_: ran.append(1))))
    monkeypatch.setattr(background_jobs, "try_take_gpu_slot", lambda *a, **k: False)
    code, out = _run(capsys)
    assert code == smoke.GPU_BUSY and "busy" in out and not ran


def test_expected_text_needs_a_speech_clip(env, capsys):
    code, out = _run(capsys, expected_text="你好")
    assert code == smoke.USAGE_ERROR and "--speech-clip" in out


def test_speech_clip_options_reach_only_the_transcription_check(env, monkeypatch, capsys):
    seen = {}

    def asr(**kw):
        seen.update(kw)
        return "ok"
    monkeypatch.setattr(svc, "_CHECKS", _fakes(("asr", asr)))
    _run(capsys, speech_clip="hello.wav", expected_text="你好")
    assert seen == {"speech_clip": "hello.wav", "expected_text": "你好"}


def test_cli_registers_the_smoke_command_and_exits_with_the_run_code(env, monkeypatch, capsys):
    import cli
    monkeypatch.setattr(svc, "_CHECKS", _fakes(("ocr", _boom)))
    monkeypatch.setattr(sys, "argv", ["cli.py", "smoke"])
    with pytest.raises(SystemExit) as exc:
        cli.main()
    assert exc.value.code == 1
    monkeypatch.setattr(svc, "_CHECKS", _fakes())
    capsys.readouterr()
    cli.main()
    assert "3 pass, 0 fail" in capsys.readouterr().out


def test_cli_and_app_report_the_same_checks_for_the_same_fakes(env, monkeypatch, capsys):
    monkeypatch.setattr(svc, "_CHECKS", _fakes(("ocr", _skip), ("translate", _unsure), ("asr", _boom)))
    svc.start(confirm=True)
    end = time.time() + 10
    while time.time() < end and not svc.get_state()["finished"]:
        time.sleep(0.02)
    app_checks = svc.get_state()["checks"]
    code, out = _run(capsys)
    assert smoke.format_report(app_checks) in out
    assert smoke.exit_code(app_checks) == code == 1
    assert [c["status"] for c in app_checks] == ["fail", "skipped", "could_not_check"]
