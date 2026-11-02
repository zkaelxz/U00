"""Diagnostics' install of a voice engine into a venv of its own
(services/voice_engine_install_service.py). The steps are small local Python scripts
standing in for venv and pip: no network, no real package."""
import os
import sys
import textwrap
import time

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

import background_jobs
import voice_venv
from api.api_config import ApiSettings
from api.server import create_app
from jobs import job_runner
from services import voice_engine_install_service as svc
from tests.job_helpers import wait_for_job

SECRET = "sk-ant-api03-SECRETSECRETSECRET123456"
JOB = svc.job_id("qwen3_tts")

# One install step: "venv" makes the venv's python, the others print noise.
STEP = textwrap.dedent("""
    import os, sys, time
    step, mode, python = sys.argv[1], sys.argv[2], sys.argv[3]
    if step == "venv":
        os.makedirs(os.path.dirname(python), exist_ok=True)
        open(python, "w").close()
    print("key " + os.environ["FAKE_SECRET"] + " in " + python, flush=True)
    if mode == "hang" and step == "torch":
        time.sleep(60)
    if mode == "fail" and step == "engine":
        print("ERROR: No matching distribution found for qwen-tts", flush=True)
        sys.exit(1)
""")


@pytest.fixture
def env(isolated_db, monkeypatch, tmp_path):
    state = {"mode": "ok", "free": 50_000, "tmp": tmp_path}
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FAKE_SECRET", SECRET)
    script = tmp_path / "step.py"
    script.write_text(STEP, encoding="utf-8")
    python = voice_venv.venv_python("qwen3_tts")
    monkeypatch.setattr(svc, "_free_mb", lambda: state["free"])
    monkeypatch.setattr(svc, "_commands", lambda engine: [
        (0.1, "step", [sys.executable, str(script), step, state["mode"], python], 30)
        for step in ("venv", "torch", "engine")])
    background_jobs.clear_job(JOB)
    svc._RESULTS.clear()
    yield state
    background_jobs.request_cancel(JOB)
    job_runner.wait_for_job_threads(10, job_ids=[JOB])
    background_jobs.clear_job(JOB)


@pytest.fixture
def client(env):
    return TestClient(create_app(ApiSettings()), raise_server_exceptions=False)


def _install(client, engine="qwen3_tts", confirm=True):
    return client.post(f"/api/diagnostics/voice-engines/{engine}/install",
                       json={"confirm": confirm})


def test_status_is_booleans_sizes_and_text(client, env):
    body = client.get("/api/diagnostics/voice-engines").json()
    assert body["free_mb"] == 50_000
    assert body["engines"] == [{
        "engine": "qwen3_tts", "label": "Qwen3-TTS", "installed": False,
        "required_mb": 6000, "refusal": None, "job_id": JOB, "job": None, "last_result": None}]


def test_install_makes_a_ready_venv_and_reports_redacted_output(client, env):
    r = _install(client)
    assert r.status_code == 200 and r.json() == {"job_id": JOB, "started": True}
    assert wait_for_job(JOB, timeout=15)["status"] == "done"
    assert voice_venv.is_ready("qwen3_tts")
    body = client.get("/api/diagnostics/voice-engines").json()
    row = body["engines"][0]
    assert row["installed"] is True and row["last_result"]["ok"] is True
    assert row["last_result"]["message"] == "Qwen3-TTS installed."
    assert row["refusal"] == "Qwen3-TTS is already installed."
    assert SECRET not in str(body) and str(env["tmp"]) not in str(body)
    assert SECRET not in str(background_jobs.get_status(JOB))


def test_a_failed_install_says_so_and_leaves_no_folder(client, env):
    env["mode"] = "fail"
    _install(client)
    assert wait_for_job(JOB, timeout=15)["status"] == "error"
    last = client.get("/api/diagnostics/voice-engines").json()["engines"][0]["last_result"]
    assert last == {"ok": False, "message": "The Qwen3-TTS install failed; see the output.",
                    "output_tail": last["output_tail"]}
    assert any("No matching distribution" in line for line in last["output_tail"])
    assert not os.path.exists(voice_venv.venv_dir("qwen3_tts"))


def test_cancel_stops_the_step_and_leaves_no_folder(client, env):
    env["mode"] = "hang"
    _install(client)
    deadline = time.monotonic() + 15
    while not os.path.exists(voice_venv.venv_python("qwen3_tts")):
        assert time.monotonic() < deadline, "the venv step never ran"
        time.sleep(0.05)
    background_jobs.request_cancel(JOB)
    assert wait_for_job(JOB, timeout=20)["status"] == "cancelled"
    assert not os.path.exists(voice_venv.venv_dir("qwen3_tts"))


def test_an_interrupted_install_is_cleared_before_the_next(client, env):
    leftover = os.path.join(voice_venv.venv_dir("qwen3_tts"), "half.txt")
    os.makedirs(os.path.dirname(leftover))
    open(leftover, "w").close()
    _install(client)
    assert wait_for_job(JOB, timeout=15)["status"] == "done"
    assert not os.path.exists(leftover) and voice_venv.is_ready("qwen3_tts")


@pytest.mark.parametrize("engine, confirm, free, status, text", [
    ("qwen3_tts", False, 50_000, 422, None),
    ("qwen3_tts", True, 100, 422, "about 6 GB"),
    ("chatterbox", True, 50_000, 422, "Unknown voice engine"),
])
def test_refusals_start_nothing(client, env, engine, confirm, free, status, text):
    env["free"] = free
    r = _install(client, engine, confirm)
    assert r.status_code == status
    if text:
        assert text in r.json()["error"]["message"]
    assert background_jobs.get_status(JOB) is None


def test_installed_is_409(client, env):
    _install(client)
    wait_for_job(JOB, timeout=15)
    r = _install(client)
    assert r.status_code == 409 and "already installed" in r.json()["error"]["message"]


def test_the_steps_are_fixed_and_match_the_card(monkeypatch, tmp_path):
    import diagnostics_torch
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: {"gpu_name": "V100"})
    monkeypatch.setattr(diagnostics_torch, "recommended_gpu_variant", lambda: ("cu126", "old"))
    steps = [argv for _p, _m, argv, _t in svc._commands("qwen3_tts")]
    python = voice_venv.venv_python("qwen3_tts")
    assert steps[0] == [sys.executable, "-m", "venv", "--without-pip",
                        voice_venv.venv_dir("qwen3_tts")]
    assert steps[1][:6] == [sys.executable, "-m", "pip", "--python", python, "install"]
    assert "torch==2.11.0+cu126" in steps[1] and "torchaudio==2.11.0+cu126" in steps[1]
    assert steps[1][-2:] == ["--index-url", "https://download.pytorch.org/whl/cu126"]
    assert steps[2][-1] == "qwen-tts==0.1.1" and "--index-url" not in steps[2]
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: None)
    assert "torch==2.11.0+cpu" in svc._commands("qwen3_tts")[1][2]
