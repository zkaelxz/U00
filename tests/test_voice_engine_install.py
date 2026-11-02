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
    monkeypatch.setattr(svc.portable, "is_installed", lambda: state.get("installed", False))
    monkeypatch.setattr(svc.diagnostics_torch, "nvidia_driver_info", lambda: state.get("nvidia"))
    monkeypatch.setattr(svc.diagnostics_torch, "compute_capabilities", lambda: state.get("caps", []))
    monkeypatch.setattr(svc, "_commands", lambda engine: [
        (0.1, "step", [sys.executable, str(script), step, state["mode"], python], 30)
        for step in ("venv", "torch", "engine", "check")])
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
        "engine": "qwen3_tts", "label": "Qwen3-TTS", "installed": False, "folder_present": False,
        "required_mb": 6000, "refusal": None, "job_id": JOB, "job": None, "last_result": None}, {
        "engine": "chatterbox", "label": "Chatterbox", "installed": False, "folder_present": False,
        "required_mb": 9000, "refusal": None, "job_id": svc.job_id("chatterbox"), "job": None,
        "last_result": None}]


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
    ("chatterbox", True, 8000, 422, "about 9 GB"),
    ("tada", True, 50_000, 422, "Unknown voice engine"),
])
def test_refusals_start_nothing(client, env, engine, confirm, free, status, text):
    env["free"] = free
    r = _install(client, engine, confirm)
    assert r.status_code == status
    if text:
        assert text in r.json()["error"]["message"]
    assert background_jobs.get_status(JOB) is None


@pytest.mark.parametrize("setup, text", [
    ({"installed": True}, "runs from start.bat"),
    ({"nvidia": {"gpu_name": "GTX", "driver_version": "450.80"}}, "too old for CUDA 12"),
])
def test_copies_it_cannot_work_on_are_refused(client, env, setup, text):
    env.update(setup)
    r = _install(client)
    assert r.status_code == 422 and text in r.json()["error"]["message"]
    assert text in client.get("/api/diagnostics/voice-engines").json()["engines"][0]["refusal"]
    assert background_jobs.get_status(JOB) is None


def test_installed_is_409(client, env):
    _install(client)
    wait_for_job(JOB, timeout=15)
    r = _install(client)
    assert r.status_code == 409 and "already installed" in r.json()["error"]["message"]


def test_the_steps_are_fixed_and_match_the_card(monkeypatch, tmp_path):
    import diagnostics_torch
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: {"gpu_name": "V100", "driver_version": "536.25"})
    monkeypatch.setattr(diagnostics_torch, "compute_capabilities", lambda: [7.0])
    steps = [argv for _p, _m, argv, _t in svc._commands("qwen3_tts")]
    assert steps[3] == [voice_venv.venv_python("qwen3_tts"), "-I", "-c",
                        "import torch, torchaudio, qwen_tts"]
    python = voice_venv.venv_python("qwen3_tts")
    assert steps[0] == [sys.executable, "-m", "venv", "--without-pip",
                        voice_venv.venv_dir("qwen3_tts")]
    assert steps[1][:6] == [sys.executable, "-m", "pip", "--python", python, "install"]
    assert "torch==2.11.0+cu126" in steps[1] and "torchaudio==2.11.0+cu126" in steps[1]
    assert steps[1][-2:] == ["--index-url", "https://download.pytorch.org/whl/cu126"]
    assert steps[2][-1] == "qwen-tts==0.1.1" and "--index-url" not in steps[2]
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: None)
    assert "torch==2.11.0+cpu" in svc._commands("qwen3_tts")[1][2]


def test_chatterbox_gets_the_torch_its_package_pins(monkeypatch, tmp_path):
    import diagnostics_torch
    monkeypatch.setenv("BAIHE_DATA_DIR", str(tmp_path))
    # Even where the app would pick CUDA 12.8: chatterbox-tts pins torch 2.6.0, built
    # for CUDA 12.6 at most.
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: {"gpu_name": "RTX 4080", "driver_version": "580.10"})
    monkeypatch.setattr(diagnostics_torch, "compute_capabilities", lambda: [8.9])
    steps = [argv for _p, _m, argv, _t in svc._commands("chatterbox")]
    assert "torch==2.6.0+cu126" in steps[1] and "torchaudio==2.6.0+cu126" in steps[1]
    assert steps[1][-2:] == ["--index-url", "https://download.pytorch.org/whl/cu126"]
    assert steps[2][-2:] == ["chatterbox-tts==0.1.7", "setuptools<81"]
    assert "--index-url" not in steps[2]
    assert "assert perth.PerthImplicitWatermarker is not None" in steps[3][-1]
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: None)
    step = svc._commands("chatterbox")[1][2]
    assert "torch==2.6.0+cpu" in step and step[-1] == "https://download.pytorch.org/whl/cpu"


@pytest.mark.parametrize("setup", [
    # A newer card counts even beside an older one: either may be the one it runs on.
    {"caps": [8.6, 12.0]}, {"caps": [12.0]},
])
def test_chatterbox_refuses_a_card_its_torch_cannot_run(env, setup):
    env.update(setup, nvidia={"gpu_name": "RTX 5090", "driver_version": "580.10"})
    assert svc._refusal("chatterbox") == "Chatterbox does not support this graphics card yet."
    # Qwen3-TTS uses the app's own torch, which has both.
    assert svc._refusal("qwen3_tts") is None


def test_on_python_3_14_chatterbox_takes_the_apps_torch(env, monkeypatch, tmp_path):
    import diagnostics_torch
    monkeypatch.setattr(svc, "sys", type("S", (), {"version_info": (3, 14, 0),
                                                   "executable": sys.executable}))
    env.update(nvidia={"gpu_name": "RTX 5090", "driver_version": "580.10"}, caps=[12.0])
    assert svc._refusal("chatterbox") is None
    assert svc._own_torch("chatterbox") is None
    monkeypatch.setattr(diagnostics_torch, "recommended_gpu_variant", lambda cap: ("cu128", None))
    step = svc._torch_step("chatterbox")
    assert "torch==2.11.0+cu128" in step and step[-1] == "https://download.pytorch.org/whl/cu128"


REMOVE_JOB = svc.remove_job_id("qwen3_tts")


def _remove(client, confirm=True):
    return client.post("/api/diagnostics/voice-engines/qwen3_tts/remove", json={"confirm": confirm})


@pytest.fixture
def clean_remove_job():
    background_jobs.clear_job(REMOVE_JOB)
    yield
    job_runner.wait_for_job_threads(10, job_ids=[REMOVE_JOB])
    background_jobs.clear_job(REMOVE_JOB)


def test_remove_deletes_only_the_engine_folder(client, env, clean_remove_job):
    _install(client)
    wait_for_job(JOB, timeout=15)
    data = os.path.dirname(os.path.dirname(voice_venv.venv_dir("qwen3_tts")))
    keep = os.path.join(data, "library", "voice_refs", "a.wav")
    os.makedirs(os.path.dirname(keep))
    open(keep, "w").close()
    r = _remove(client)
    assert r.status_code == 200 and r.json() == {"job_id": REMOVE_JOB, "started": True}
    assert wait_for_job(REMOVE_JOB, timeout=15)["status"] == "done"
    assert not os.path.lexists(voice_venv.venv_dir("qwen3_tts")) and os.path.exists(keep)
    row = client.get("/api/diagnostics/voice-engines").json()["engines"][0]
    assert row["installed"] is False and row["folder_present"] is False
    assert row["last_result"]["message"] == "Qwen3-TTS removed."


def test_remove_needs_confirm_and_a_folder(client, env, clean_remove_job):
    assert _remove(client, confirm=False).status_code == 422
    r = _remove(client)
    assert r.status_code == 422 and "not installed" in r.json()["error"]["message"]
    assert background_jobs.get_status(REMOVE_JOB) is None


def test_a_damaged_folder_shows_and_can_be_removed(client, env, clean_remove_job):
    os.makedirs(voice_venv.venv_dir("qwen3_tts"))
    row = client.get("/api/diagnostics/voice-engines").json()["engines"][0]
    assert row["installed"] is False and row["folder_present"] is True
    _remove(client)
    assert wait_for_job(REMOVE_JOB, timeout=15)["status"] == "done"
    assert not os.path.lexists(voice_venv.venv_dir("qwen3_tts"))


@pytest.mark.skipif(os.name == "nt", reason="symlinks need privileges on Windows")
def test_remove_never_follows_a_link_out_of_the_data_folder(client, env, clean_remove_job, tmp_path):
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "precious.txt").write_text("x", encoding="utf-8")
    os.makedirs(os.path.dirname(voice_venv.venv_dir("qwen3_tts")))
    os.symlink(outside, voice_venv.venv_dir("qwen3_tts"))
    _remove(client)
    assert wait_for_job(REMOVE_JOB, timeout=15)["status"] == "done"
    assert (outside / "precious.txt").exists()
    assert not os.path.lexists(voice_venv.venv_dir("qwen3_tts"))


def test_remove_is_refused_while_a_job_runs(client, env, clean_remove_job, monkeypatch):
    from services import library_admin_service
    os.makedirs(voice_venv.venv_dir("qwen3_tts"))
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: True)
    assert _remove(client).status_code == 409
    assert os.path.exists(voice_venv.venv_dir("qwen3_tts"))
