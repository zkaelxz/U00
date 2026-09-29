"""Tests for services/diagnostics_gaps_service.py (mocked, no network)."""

import getpass
import json
import os

import pytest

import applog
import background_jobs
import db
import diagnostics
from services import diagnostics_gaps_service as svc
from services import settings_service

SECRET_KEY = "sk-ant-api03-SECRETSECRETSECRET123456"
HF_TOKEN = "hf_AbCdEfGhIjKlMnOpQrStUvWxYz0123"
ABS_PATH = "/home/someone/private/library/drama.mp4"
WIN_PATH = r"C:\Users\someone\private\drama.mp4"
DIRTY = f"failed with {SECRET_KEY} token {HF_TOKEN} at {ABS_PATH} and {WIN_PATH}"


def _assert_clean(obj):
    text = json.dumps(obj, default=str) if not isinstance(obj, str) else obj
    for bad in (SECRET_KEY, HF_TOKEN, ABS_PATH, "/home/someone", "someone\\\\private",
                "Users\\\\someone", r"C:\Users"):
        assert bad not in text, bad
    user = getpass.getuser()
    if user and len(user) > 3:
        assert user not in text


@pytest.fixture
def dirty_jobs(monkeypatch):
    jobs = {
        "emotion_999999": {"status": "failed", "message": DIRTY, "error": DIRTY,
                           "description": DIRTY, "started_at": 10.0, "finished_at": 12.5},
        "custom_job": {"status": "done", "message": "ok", "started_at": 1.0, "finished_at": 2.0},
        "live_capture": {"status": "running", "message": DIRTY},
    }
    monkeypatch.setattr(background_jobs, "list_all_jobs", lambda: dict(jobs))
    return jobs


@pytest.fixture
def dirty_log(isolated_db):
    log_dir = os.path.join(db.LIBRARY_DIR, "logs")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "app.log"), "w", encoding="utf-8") as f:
        for i in range(500):
            f.write(f"INFO line {i}\n")
        f.write(f"ERROR {DIRTY}\n")


def test_describe_job_moved():
    assert svc.describe_job("live_capture") == "🔴 Live capture"
    assert svc.describe_job("x_y") == "x_y"


class TestDescribeJob:
    """describe_job() turns a raw job_id like 'emotion_42' into a
    human-readable line for the Running jobs panel (moved from
    tests/test_diagnostics_and_export.py; the live_capture case was an
    exact duplicate of test_describe_job_moved above)."""

    def test_known_prefix_includes_the_drama_title(self, isolated_db):
        did = isolated_db.create_drama(title_en="Test Drama")
        result = svc.describe_job(f"emotion_{did}")
        assert "Detecting emotional register" in result
        assert "Test Drama" in result

    def test_deleted_drama_says_so_instead_of_crashing(self, isolated_db):
        result = svc.describe_job("flag_999999")
        assert "deleted" in result

    def test_unrecognized_job_id_falls_back_to_the_raw_string(self):
        assert svc.describe_job("some_custom_thing") == "some_custom_thing"


def test_setup_checks_have_no_paths(isolated_db, monkeypatch):
    monkeypatch.setattr(diagnostics, "check_ffmpeg",
                        lambda: {"found": True, "path": ABS_PATH, "version": "ffmpeg 6 " + ABS_PATH})
    monkeypatch.setattr(diagnostics, "check_js_runtime",
                        lambda: {"found": True, "name": "deno", "path": ABS_PATH})
    monkeypatch.setattr(diagnostics, "check_cuda",
                        lambda: {"torch_installed": False, "cuda_available": None})
    out = svc.get_setup_checks()
    assert out["ffmpeg"]["found"] is True and out["js_runtime"]["name"] == "deno"
    assert out["library_writable"] is True
    assert isinstance(out["files"]["all_present"], bool)
    assert "path" not in out["ffmpeg"] and "path" not in out["js_runtime"]
    _assert_clean(out)


def test_model_versions_shape():
    rows = svc.get_model_versions("qwen2.5:7b")
    assert rows and rows[-1]["version"] == "qwen2.5:7b"
    assert all(isinstance(r["installed"], bool) for r in rows)


def test_model_cache_names_and_sizes_only(monkeypatch, tmp_path):
    monkeypatch.setattr(diagnostics, "scan_hf_cache", lambda d=None: [
        {"repo_id": "org/model", "repo_type": "model", "revision": "abc", "size_bytes": 100}])
    (tmp_path / "en_US-voice.onnx").write_bytes(b"x" * 10)
    (tmp_path / "en_US-voice.onnx.json").write_bytes(b"x" * 5)
    out = svc.get_model_cache(piper_voices_dir=str(tmp_path))
    assert out["hf_total_bytes"] == 100
    assert out["piper_voices"] == [{"voice": "en_US-voice", "size_bytes": 15}]
    assert str(tmp_path) not in json.dumps(out)


def test_pyannote_readiness_booleans_no_token(monkeypatch):
    monkeypatch.setattr(settings_service, "resolve_key", lambda k, env_path=None: HF_TOKEN)
    monkeypatch.setattr(diagnostics, "check_dependency", lambda name: True)
    out = svc.get_pyannote_readiness()
    assert out == {"pyannote_installed": True, "hf_token_configured": True,
                   "models": None, "ready": True}

    class FakeApi:
        def model_info(self, model, token=None):
            assert token == HF_TOKEN
            raise RuntimeError(f"403 for {token} at {ABS_PATH}")

    out = svc.get_pyannote_readiness(check_access=True, api=FakeApi())
    assert out["models"] and all(m["accessible"] is False for m in out["models"])
    assert out["ready"] is False
    _assert_clean(out)


def test_job_history_redacted(isolated_db, dirty_jobs):
    hist = svc.get_job_history()
    assert [h["job_id"] for h in hist] == ["emotion_999999", "custom_job"]
    assert hist[0]["duration_seconds"] == 2.5
    assert "[REDACTED]" in hist[0]["error"]
    _assert_clean(hist)


def test_log_tail_capped_and_redacted(dirty_log):
    assert len(svc.get_log_tail(10_000)) == svc.LOG_TAIL_MAX
    assert len(svc.get_log_tail(5)) == 5
    assert svc.get_log_tail(-3) == []
    errs = svc.get_log_tail(5, keyword="ERROR")
    assert len(errs) == 1
    _assert_clean(svc.get_log_tail(10_000))


def test_support_report_clean(dirty_log, monkeypatch):
    monkeypatch.setattr(settings_service, "key_status",
                        lambda env_path=None: {"claude": True, "hf_token": False})
    monkeypatch.setattr(diagnostics, "scan_hf_cache", lambda d=None: [])
    report = svc.build_support_report()
    assert "OS:" in report and "Python:" in report and "Recent errors:" in report
    assert "API keys set: claude" in report
    _assert_clean(report)


@pytest.mark.parametrize("call", [
    lambda c: svc.install_dependency("edge_tts", confirm=c),
    lambda c: svc.upgrade_dependency("edge_tts", confirm=c),
    lambda c: svc.reset_library(confirm=c),
])
def test_admin_requires_confirm(call, monkeypatch):
    monkeypatch.setattr(db, "reset_library", lambda: pytest.fail("must not run"))
    monkeypatch.setattr(diagnostics, "stream_dependency_install",
                        lambda *a, **k: pytest.fail("must not run"))
    monkeypatch.setattr(diagnostics, "stream_pip_install", lambda *a, **k: pytest.fail("must not run"))
    monkeypatch.setattr(background_jobs, "list_running_jobs", lambda: {})
    for bad in (False, None, "yes", 1):
        with pytest.raises(svc.AdminActionRefused):
            call(bad)


def test_admin_refuses_while_jobs_run(monkeypatch):
    monkeypatch.setattr(background_jobs, "list_running_jobs", lambda: {"translate_1": {}})
    monkeypatch.setattr(db, "reset_library", lambda: pytest.fail("must not run"))
    with pytest.raises(svc.AdminActionRefused):
        svc.reset_library(confirm=True)
    with pytest.raises(svc.AdminActionRefused):
        svc.install_dependency("edge_tts", confirm=True)


def test_install_rejects_unknown_package(monkeypatch):
    monkeypatch.setattr(background_jobs, "list_running_jobs", lambda: {})
    with pytest.raises(svc.AdminActionRefused):
        svc.install_dependency("evil-package; rm -rf /", confirm=True)
    with pytest.raises(svc.AdminActionRefused):
        svc.install_dependency("streamlit", confirm=True)  # required tier


def test_install_and_upgrade_run_and_redact(monkeypatch):
    monkeypatch.setattr(background_jobs, "list_running_jobs", lambda: {})

    def fake_stream(*a, **k):
        yield {"line": DIRTY}
        yield {"done": True, "ok": True, "returncode": 0}

    monkeypatch.setattr(diagnostics, "stream_dependency_install", fake_stream)
    monkeypatch.setattr(diagnostics, "stream_pip_install", fake_stream)
    for fn in (svc.install_dependency, svc.upgrade_dependency):
        out = fn("edge_tts", confirm=True)
        assert out["ok"] is True and out["package"] == "edge_tts"
        _assert_clean(out)


def test_reset_runs_when_confirmed(monkeypatch):
    calls = []
    monkeypatch.setattr(background_jobs, "list_running_jobs", lambda: {})
    monkeypatch.setattr(db, "reset_library", lambda: calls.append("reset"))
    monkeypatch.setattr(background_jobs, "clear_all_jobs", lambda: calls.append("clear"))
    assert svc.reset_library(confirm=True)["ok"] is True
    assert calls == ["reset", "clear"]
