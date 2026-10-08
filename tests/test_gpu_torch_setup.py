"""
tests/test_gpu_torch_setup.py -- Diagnostics "GPU PyTorch": the static
torch/torchvision/torchaudio compatibility table, NVIDIA driver detection
and checks, the matched-triple pip arguments, the torch pins every other
install carries, the conflict refusal, and the setup/status service. No
network, no real pip, no real torch import: subprocess and pip are faked.
"""
import os
import subprocess

import pytest

import diagnostics
from services import diagnostics_gaps_service as svc

FLAGS = ["--no-cache-dir", "--disable-pip-version-check"]


def _versions(monkeypatch, **have):
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda name: have.get(name))


def _no_jobs(monkeypatch):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)


def _fake_pip(monkeypatch, seen, lines=(), returncode=0, read_pins=None):
    def fake(cmd, timeout):
        seen.append((cmd, timeout))
        if read_pins is not None and "-c" in cmd:
            for i, a in enumerate(cmd):
                if a == "-c" and os.path.basename(cmd[i + 1]).startswith("baihe-torch-pins-"):
                    with open(cmd[i + 1], encoding="utf-8") as f:
                        read_pins.append((cmd[i + 1], f.read()))
        for ln in lines:
            yield {"line": ln}
        yield {"returncode": returncode, "timed_out": False}
    monkeypatch.setattr(svc, "stream_tree", fake)


# ---- the static table ----

def test_variants_are_matched_triples_from_fixed_pytorch_indexes():
    for variant, spec in diagnostics.TORCH_VARIANTS.items():
        v = spec["versions"]
        assert set(v) == set(diagnostics.TORCH_FAMILY)
        assert spec["index_url"] == f"https://download.pytorch.org/whl/{variant}"
        torch_mm = diagnostics._mm(v["torch"])
        assert diagnostics._mm(v["torchvision"]) == diagnostics.TORCHVISION_FOR_TORCH[torch_mm]
        assert diagnostics._mm(v["torchaudio"]) == torch_mm
        assert all(x.endswith("+" + variant) for x in v.values())
    assert diagnostics.TORCH_VARIANTS["cu128"]["versions"] == {
        "torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128", "torchaudio": "2.11.0+cu128"}


def test_constraints_caps_allow_the_recommended_triple():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("torch", "torchaudio"):
        cap = diagnostics._constraints_cap(name, root)
        assert cap is None or int(diagnostics.TORCH_VARIANTS["cu128"]["versions"][name][0]) < cap[1]


# ---- NVIDIA driver ----

def test_nvidia_driver_info_parses_and_bounds_the_call(monkeypatch):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    seen = {}

    def run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, "NVIDIA GeForce RTX 3080 Ti, 581.42\n", "")
    monkeypatch.setattr(diagnostics.subprocess, "run", run)
    assert diagnostics.nvidia_driver_info() == {"gpu_name": "NVIDIA GeForce RTX 3080 Ti",
                                                "driver_version": "581.42"}
    assert seen["timeout"] == 5 and seen["cmd"][0] == "nvidia-smi"


def test_nvidia_driver_info_none_without_or_with_a_failing_nvidia_smi(monkeypatch):
    monkeypatch.setattr(diagnostics.shutil, "which", lambda n: None)
    assert diagnostics.nvidia_driver_info() is None
    monkeypatch.setattr(diagnostics.shutil, "which", lambda n: "/usr/bin/nvidia-smi")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)
    monkeypatch.setattr(diagnostics.subprocess, "run", boom)
    assert diagnostics.nvidia_driver_info() is None


@pytest.mark.parametrize("system,driver,status", [
    ("Windows", "581.42", "ok"), ("Windows", "570.65", "ok"), ("Windows", "560.94", "old"),
    ("Windows", "528.33", "old"), ("Windows", "522.06", "too_old"),
    ("Linux", "570.26", "ok"), ("Linux", "535.183.01", "old"), ("Linux", "470.256.02", "too_old"),
    ("Windows", None, "unknown"),
])
def test_driver_check(system, driver, status):
    assert diagnostics.driver_check(driver, system)["status"] == status


# ---- installed family ----

def test_build_tags():
    assert diagnostics._build_of("2.11.0+cu128") == "cuda"
    assert diagnostics._build_of("2.11.0+cpu") == "cpu"
    assert diagnostics._build_of("2.11.0") is None and diagnostics._build_of(None) is None


def test_problems_name_the_users_torchvision_mismatch():
    fam = {"torch": {"version": "2.11.0+cu128", "build": "cuda"},
           "torchvision": {"version": "0.29.0", "build": None},
           "torchaudio": {"version": "2.11.0+cu128", "build": "cuda"}}
    (p,) = diagnostics.torch_family_problems(fam)
    assert "torchvision 0.29.0" in p and "0.26" in p
    fam["torchvision"] = {"version": "0.26.0+cu128", "build": "cuda"}
    assert diagnostics.torch_family_problems(fam) == []
    fam["torchaudio"] = {"version": "2.11.0+cpu", "build": "cpu"}
    assert any("mixed" in p for p in diagnostics.torch_family_problems(fam))


def test_pin_lines_only_for_installed_and_well_formed_versions(monkeypatch):
    _versions(monkeypatch, torch="2.11.0+cu128", torchaudio="2.11.0+cu128")
    assert diagnostics.torch_pin_lines() == ["torch==2.11.0+cu128", "torchaudio==2.11.0+cu128"]
    _versions(monkeypatch, torch="2.11.0 --index-url http://x")
    assert diagnostics.torch_pin_lines() == []


def test_setup_args_pin_all_three_together(tmp_path):
    (tmp_path / "constraints.txt").write_text("torch<3\n")
    first, second = diagnostics.torch_setup_pip_args("cu128", str(tmp_path))
    pins = ["torch==2.11.0+cu128", "torchvision==0.26.0+cu128", "torchaudio==2.11.0+cu128"]
    tail = ["--index-url", "https://download.pytorch.org/whl/cu128", "-c",
            str(tmp_path / "constraints.txt")]
    assert first == ["--force-reinstall", "--no-deps", *pins, *tail]
    assert second == [*pins, *tail]
    with pytest.raises(KeyError):
        diagnostics.torch_setup_pip_args("https://evil.example/simple")


def test_parse_verify_output():
    assert diagnostics.parse_torch_verify_output('noise\n{"torch": "2.11.0+cu128"}\n') == {
        "torch": "2.11.0+cu128"}
    assert "error" in diagnostics.parse_torch_verify_output("Traceback ...")


# ---- every other install pins the torch family ----

def test_other_installs_carry_a_torch_pins_file_that_is_removed(monkeypatch):
    _no_jobs(monkeypatch)
    _versions(monkeypatch, torch="2.11.0+cu128", torchvision="0.26.0+cu128")
    monkeypatch.setattr(svc.shutil, "which", lambda n: None)
    seen, pins = [], []
    _fake_pip(monkeypatch, seen, read_pins=pins)
    for fn in (svc.install_dependency, svc.upgrade_dependency):
        assert fn("omnivoice", confirm=True)["ok"] is True
    assert len(seen) == 2 and len(pins) == 2
    for path, text in pins:
        assert text.split() == ["torch==2.11.0+cu128", "torchvision==0.26.0+cu128"]
        assert not os.path.exists(path)
    assert seen[0][0][3:6] == ["install", *FLAGS]


CONSTRAINTS = ["-c", os.path.join(svc.default_project_root(), "constraints.txt")]


def test_no_pins_file_without_torch(monkeypatch):
    _no_jobs(monkeypatch)
    _versions(monkeypatch)
    seen = []
    _fake_pip(monkeypatch, seen)
    svc.install_dependency("pydub", confirm=True)
    assert seen[0][0][3:] == ["install", *FLAGS, "pydub", *CONSTRAINTS]


def test_a_package_needing_another_torch_is_refused_with_a_plain_hint(monkeypatch):
    _no_jobs(monkeypatch)
    _versions(monkeypatch, torch="2.11.0+cu128")
    seen = []
    _fake_pip(monkeypatch, seen, returncode=1, lines=[
        "ERROR: Cannot install omnivoice==0.2 because these package versions have "
        "conflicting dependencies.",
        "The conflict is caused by:",
        "    omnivoice 0.2 depends on torch==2.6.0",
        "    The user requested (constraint) torch==2.11.0+cu128",
        "ERROR: ResolutionImpossible"])
    out = svc.install_dependency("omnivoice", confirm=True)
    assert out["ok"] is False
    assert "nothing was changed" in out["hint"] and "torch 2.11.0+cu128" in out["hint"]


def test_torch_upgrade_goes_through_the_setup(monkeypatch):
    _no_jobs(monkeypatch)
    monkeypatch.setattr(svc, "stream_tree", lambda *a, **k: pytest.fail("no pip"))
    with pytest.raises(svc.AdminActionNotPossible):
        svc.upgrade_dependency("torch", confirm=True)
    with pytest.raises(svc.AdminActionUnconfirmed):
        svc.upgrade_dependency("torch", confirm=False)


# ---- setup and status ----

GOOD_VERIFY = {"torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128",
               "torchaudio": "2.11.0+cu128", "cuda_build": "12.8", "cuda_available": True,
               "device": "RTX 3080 Ti", "error": None}


def _gpu(monkeypatch, driver="581.42"):
    monkeypatch.setattr(diagnostics, "nvidia_driver_info", lambda: driver and {
        "gpu_name": "NVIDIA GeForce RTX 3080 Ti", "driver_version": driver})


def test_setup_installs_the_triple_then_verifies(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch)
    _versions(monkeypatch, torch="2.11.0+cpu")
    monkeypatch.setattr(svc, "verify_torch", lambda: dict(GOOD_VERIFY))
    seen = []
    _fake_pip(monkeypatch, seen)
    out = svc.setup_gpu_torch(confirm=True)
    assert out["ok"] is True and out["variant"] == "cu128" and out["verify"] == GOOD_VERIFY
    (first, t1), (second, t2) = seen
    assert "--force-reinstall" in first and "--no-deps" in first
    assert "--force-reinstall" not in second
    assert t1 == t2 == svc.GPU_TORCH_TIMEOUT_SECONDS
    assert not any(os.path.basename(a).startswith("baihe-torch-pins-") for a in first + second)


def test_setup_not_ok_when_cuda_is_still_unavailable(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch)
    monkeypatch.setattr(svc, "verify_torch", lambda: {**GOOD_VERIFY, "cuda_available": False})
    _fake_pip(monkeypatch, [])
    out = svc.setup_gpu_torch("cu128", confirm=True)
    assert out["ok"] is False and "driver" in out["hint"]


def test_setup_skips_verify_when_pip_fails(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch)
    monkeypatch.setattr(svc, "verify_torch", lambda: pytest.fail("no verify"))
    seen = []
    _fake_pip(monkeypatch, seen, returncode=1)
    out = svc.setup_gpu_torch("cu128", confirm=True)
    assert out["ok"] is False and out["verify"] is None and len(seen) == 1


@pytest.mark.parametrize("driver,variant,msg", [
    (None, "cu128", "No NVIDIA GPU"), ("522.06", "cu128", "too old"),
    ("581.42", "rocm", "Unknown"),
])
def test_setup_refusals(monkeypatch, driver, variant, msg):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch, driver)
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Windows")
    monkeypatch.setattr(svc, "stream_tree", lambda *a, **k: pytest.fail("no pip"))
    with pytest.raises(svc.AdminActionNotPossible, match=msg):
        svc.setup_gpu_torch(variant, confirm=True)


def test_setup_refuses_an_unsupported_python(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch)
    monkeypatch.setattr(diagnostics, "TORCH_SUPPORTED_PYTHON", ((3, 99), (3, 99)))
    with pytest.raises(svc.AdminActionNotPossible, match="Python"):
        svc.setup_gpu_torch(confirm=True)


def test_cpu_variant_needs_no_gpu(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch, None)
    monkeypatch.setattr(svc, "verify_torch", lambda: {
        **GOOD_VERIFY, "torch": "2.11.0+cpu", "cuda_available": False})
    seen = []
    _fake_pip(monkeypatch, seen)
    out = svc.setup_gpu_torch(confirm=True)     # default without a GPU: cpu
    assert out["ok"] is True and out["variant"] == "cpu"
    assert "https://download.pytorch.org/whl/cpu" in seen[0][0]


@pytest.mark.parametrize("have,gpu,state", [
    ({}, True, "missing"),
    ({"torch": "2.11.0+cpu", "torchaudio": "2.11.0+cpu"}, True, "cpu_on_gpu"),
    ({"torch": "2.11.0+cu128", "torchvision": "0.29.0"}, True, "mismatched"),
    ({"torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128", "torchaudio": "2.11.0+cu128"},
     True, "recommended"),
    ({"torch": "2.10.0+cu128", "torchaudio": "2.10.0+cu128"}, True, "different"),
    ({"torch": "2.11.0+cpu", "torchaudio": "2.11.0+cpu"}, False, "recommended"),
])
def test_status_states(monkeypatch, have, gpu, state):
    _gpu(monkeypatch, "581.42" if gpu else None)
    _versions(monkeypatch, **have)
    out = svc.get_gpu_torch_status()
    assert out["state"] == state and out["probe"] is None
    assert out["recommended"]["variant"] == ("cu128" if gpu else "cpu")


def test_verify_runs_in_a_subprocess_and_redacts(monkeypatch):
    seen = {}

    def run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(
            cmd, 0, '{"torch": "2.11.0+cu128", "cuda_available": false, '
                    '"error": "RuntimeError: at /home/someone/venv/torch"}\n', "")
    monkeypatch.setattr(subprocess, "run", run)
    out = svc.verify_torch()
    assert seen["timeout"] == diagnostics.TORCH_VERIFY_TIMEOUT_SECONDS
    assert seen["cmd"][1:3] == ["-c", diagnostics.TORCH_VERIFY_SCRIPT]
    assert out["torch"] == "2.11.0+cu128" and out["cuda_available"] is False
    assert "/home/someone" not in out["error"]


def test_verify_timeout(monkeypatch):
    def run(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 1)
    monkeypatch.setattr(subprocess, "run", run)
    assert "too long" in svc.verify_torch()["error"]


def test_cuda_check_never_queues_behind_another(monkeypatch):
    monkeypatch.setattr(svc, "_verify_torch_once", lambda: pytest.fail("no second check"))
    assert svc._VERIFY_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(svc.AdminActionStale):
            svc.verify_torch(blocking=False)
    finally:
        svc._VERIFY_LOCK.release()


def test_cuda_check_refused_while_a_job_runs(monkeypatch):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: True)
    monkeypatch.setattr(svc, "_verify_torch_once", lambda: pytest.fail("no check"))
    with pytest.raises(svc.AdminActionJobsRunning):
        svc.check_gpu_torch()
