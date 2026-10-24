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

import dependency_updates
import diagnostics
import diagnostics_torch
from lib import proc as proc_run
from services import diagnostics_gaps_service as svc
from services import diagnostics_admin_service as admin_svc
from services import diagnostics_installs_service as installs_svc
from services import diagnostics_packages_service as packages_svc
from services import diagnostics_gpu_torch_service as torch_svc

FLAGS = ["--no-cache-dir", "--disable-pip-version-check"]
_REAL_CORE_BLOCKS = torch_svc._core_blocks


@pytest.fixture(autouse=True)
def _no_real_nvidia_smi(monkeypatch):
    # The default variant depends on the card's compute capability; never ask a real one.
    monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda n: None)
    # Its dry run asks the PyTorch index; tests of it fake pip in test_install_plan.py.
    monkeypatch.setattr(torch_svc, "_core_blocks", lambda variant, *a, **k: [])


def _versions(monkeypatch, **have):
    monkeypatch.setattr(diagnostics, "get_installed_version", lambda name: have.get(name))


def _no_jobs(monkeypatch):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: False)


def _fake_pip(monkeypatch, seen, lines=(), returncode=0, read_pins=None):
    def fake(cmd, timeout, **_kw):
        seen.append((cmd, timeout))
        if read_pins is not None and "-c" in cmd:
            for i, a in enumerate(cmd):
                if a == "-c" and os.path.basename(cmd[i + 1]).startswith("baihe-torch-pins-"):
                    with open(cmd[i + 1], encoding="utf-8") as f:
                        read_pins.append((cmd[i + 1], f.read()))
        for ln in lines:
            yield {"line": ln}
        yield {"returncode": returncode, "timed_out": False}
    monkeypatch.setattr(packages_svc, "stream_tree", fake)


# ---- the static table ----

def test_variants_are_matched_triples_from_fixed_pytorch_indexes():
    for variant, spec in diagnostics_torch.TORCH_VARIANTS.items():
        v = spec["versions"]
        assert set(v) == set(diagnostics_torch.TORCH_FAMILY)
        assert spec["index_url"] == f"https://download.pytorch.org/whl/{variant}"
        torch_mm = diagnostics_torch._mm(v["torch"])
        assert diagnostics_torch._mm(v["torchvision"]) == diagnostics_torch.TORCHVISION_FOR_TORCH[torch_mm]
        assert diagnostics_torch._mm(v["torchaudio"]) == torch_mm
        assert all(x.endswith("+" + variant) for x in v.values())
    assert diagnostics_torch.TORCH_VARIANTS["cu128"]["versions"] == {
        "torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128", "torchaudio": "2.11.0+cu128"}


def test_constraints_caps_allow_the_recommended_triple():
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    for name in ("torch", "torchaudio"):
        cap = dependency_updates._constraints_cap(name, root)
        assert cap is None or int(diagnostics_torch.TORCH_VARIANTS["cu128"]["versions"][name][0]) < cap[1]


# ---- NVIDIA driver ----

def test_nvidia_driver_info_parses_and_bounds_the_call(monkeypatch):
    monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    seen = {}

    def run(cmd, **kw):
        seen.update(kw, cmd=cmd)
        return subprocess.CompletedProcess(cmd, 0, "NVIDIA GeForce RTX 3080 Ti, 581.42\n", "")
    monkeypatch.setattr(diagnostics_torch.subprocess, "run", run)
    assert diagnostics_torch.nvidia_driver_info() == {"gpu_name": "NVIDIA GeForce RTX 3080 Ti",
                                                "driver_version": "581.42"}
    assert seen["timeout"] == 5 and seen["cmd"][0] == "nvidia-smi"


def test_nvidia_driver_info_none_without_or_with_a_failing_nvidia_smi(monkeypatch):
    monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda n: None)
    assert diagnostics_torch.nvidia_driver_info() is None
    monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda n: "/usr/bin/nvidia-smi")

    def boom(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 5)
    monkeypatch.setattr(diagnostics_torch.subprocess, "run", boom)
    assert diagnostics_torch.nvidia_driver_info() is None


@pytest.mark.parametrize("system,driver,status", [
    ("Windows", "581.42", "ok"), ("Windows", "570.65", "ok"), ("Windows", "560.94", "old"),
    ("Windows", "528.33", "old"), ("Windows", "522.06", "too_old"),
    ("Linux", "570.26", "ok"), ("Linux", "535.183.01", "old"), ("Linux", "470.256.02", "too_old"),
    ("Windows", None, "unknown"),
])
def test_driver_check(system, driver, status):
    assert diagnostics_torch.driver_check(driver, system)["status"] == status


@pytest.mark.parametrize("system,driver,status,recommended", [
    # A V100 held at 536.25 (newer drivers failed on it) gets no upgrade nudge.
    ("Windows", "536.25", "ok", "528.33"), ("Windows", "528.33", "ok", "528.33"),
    ("Windows", "522.06", "too_old", "528.33"), ("Linux", "535.183.01", "ok", "525.60.13"),
    ("Linux", "470.256.02", "too_old", "525.60.13"),
])
def test_driver_check_follows_the_cuda_version_of_the_chosen_build(system, driver, status,
                                                                   recommended):
    out = diagnostics_torch.driver_check(driver, system, variant="cu126")
    assert (out["status"], out["recommended"], out["cuda"]) == (status, recommended, "12.6")
    assert diagnostics_torch.driver_check(driver, system, variant="cpu")["cuda"] == "12.8"


# ---- installed family ----

def test_build_tags():
    assert diagnostics_torch._build_of("2.11.0+cu128") == "cuda"
    assert diagnostics_torch._build_of("2.11.0+cpu") == "cpu"
    assert diagnostics_torch._build_of("2.11.0") is None and diagnostics_torch._build_of(None) is None


def test_problems_name_the_users_torchvision_mismatch():
    fam = {"torch": {"version": "2.11.0+cu128", "build": "cuda"},
           "torchvision": {"version": "0.29.0", "build": None},
           "torchaudio": {"version": "2.11.0+cu128", "build": "cuda"}}
    (p,) = diagnostics_torch.torch_family_problems(fam)
    assert "torchvision 0.29.0" in p and "0.26" in p
    fam["torchvision"] = {"version": "0.26.0+cu128", "build": "cuda"}
    assert diagnostics_torch.torch_family_problems(fam) == []
    fam["torchaudio"] = {"version": "2.11.0+cpu", "build": "cpu"}
    assert any("mixed" in p for p in diagnostics_torch.torch_family_problems(fam))


def test_pin_lines_only_for_installed_and_well_formed_versions(monkeypatch):
    _versions(monkeypatch, torch="2.11.0+cu128", torchaudio="2.11.0+cu128")
    assert diagnostics_torch.torch_pin_lines() == ["torch==2.11.0+cu128", "torchaudio==2.11.0+cu128"]
    _versions(monkeypatch, torch="2.11.0 --index-url http://x")
    assert diagnostics_torch.torch_pin_lines() == []


def test_setup_args_pin_all_three_together(tmp_path):
    (tmp_path / "constraints.txt").write_text("torch<3\n")
    first, second = diagnostics_torch.torch_setup_pip_args("cu128", str(tmp_path))
    pins = ["torch==2.11.0+cu128", "torchvision==0.26.0+cu128", "torchaudio==2.11.0+cu128"]
    tail = ["--index-url", "https://download.pytorch.org/whl/cu128", "-c",
            str(tmp_path / "constraints.txt")]
    assert first == ["--force-reinstall", "--no-deps", *pins, *tail]
    assert second == [*pins, *tail]
    with pytest.raises(KeyError):
        diagnostics_torch.torch_setup_pip_args("https://evil.example/simple")


def test_parse_verify_output():
    assert diagnostics_torch.parse_torch_verify_output('noise\n{"torch": "2.11.0+cu128"}\n') == {
        "torch": "2.11.0+cu128"}
    assert "error" in diagnostics_torch.parse_torch_verify_output("Traceback ...")


# ---- every other install pins the torch family ----

def test_other_installs_carry_a_torch_pins_file_that_is_removed(monkeypatch):
    _no_jobs(monkeypatch)
    _versions(monkeypatch, torch="2.11.0+cu128", torchvision="0.26.0+cu128")
    seen, pins = [], []
    _fake_pip(monkeypatch, seen, read_pins=pins)
    upgrade = lambda n, confirm: packages_svc.upgrade_dependency(n, packages_svc.upgrade_specs(n))
    for fn in (packages_svc.install_dependency, upgrade):
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
    packages_svc.install_dependency("pydub", confirm=True)
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
    out = packages_svc.install_dependency("omnivoice", confirm=True)
    assert out["ok"] is False
    assert "nothing was changed" in out["hint"] and "torch 2.11.0+cu128" in out["hint"]


def test_torch_upgrade_goes_through_the_setup(monkeypatch):
    _no_jobs(monkeypatch)
    monkeypatch.setattr(packages_svc, "stream_tree", lambda *a, **k: pytest.fail("no pip"))
    with pytest.raises(admin_svc.AdminActionNotPossible):
        installs_svc.start_dependency_upgrade("torch", confirm=True)
    with pytest.raises(admin_svc.AdminActionUnconfirmed):
        installs_svc.start_dependency_upgrade("torch", confirm=False)


# ---- setup and status ----

GOOD_VERIFY = {"torch": "2.11.0+cu128", "torchvision": "0.26.0+cu128",
               "torchaudio": "2.11.0+cu128", "cuda_build": "12.8", "cuda_available": True,
               "device": "RTX 3080 Ti", "error": None}


def _gpu(monkeypatch, driver="581.42"):
    monkeypatch.setattr(diagnostics_torch, "nvidia_driver_info", lambda: driver and {
        "gpu_name": "NVIDIA GeForce RTX 3080 Ti", "driver_version": driver})


def test_setup_installs_the_triple_then_verifies(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch)
    _versions(monkeypatch, torch="2.11.0+cpu")
    monkeypatch.setattr(torch_svc, "verify_torch", lambda: dict(GOOD_VERIFY))
    seen = []
    _fake_pip(monkeypatch, seen)
    out = torch_svc.setup_gpu_torch(confirm=True)
    assert out["ok"] is True and out["variant"] == "cu128" and out["verify"] == GOOD_VERIFY
    (first, t1), (second, t2) = seen
    assert "--force-reinstall" in first and "--no-deps" in first
    assert "--force-reinstall" not in second
    assert t1 == t2 == torch_svc.GPU_TORCH_TIMEOUT_SECONDS
    assert not any(os.path.basename(a).startswith("baihe-torch-pins-") for a in first + second)


def test_setup_stops_before_pip_when_it_would_drop_a_core_package(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch)
    reason = "numpy 2.5.4 -> 2.3.5 would leave it older than the 2.5.4 Baihe needs, so nothing was changed."
    monkeypatch.setattr(torch_svc, "_core_blocks", lambda variant, *a, **k: [reason])
    monkeypatch.setattr(packages_svc, "stream_tree", lambda *a, **k: pytest.fail("no pip"))
    monkeypatch.setattr(torch_svc, "verify_torch", lambda: pytest.fail("no verify"))
    out = torch_svc.setup_gpu_torch("cu126", confirm=True)
    assert out["ok"] is False and out["hint"] == reason and out["variant"] == "cu126"


def test_cancel_during_the_core_package_check_stops_the_setup(monkeypatch):
    import background_jobs
    from services import diagnostics_gpu_torch_service as real
    monkeypatch.setattr(real, "_cancel_probe", lambda job_id: (lambda: True))
    monkeypatch.setattr(background_jobs, "update_progress", lambda *a, **k: None)
    monkeypatch.setattr(real.install_plan, "torch_setup_blocks", lambda variant, cancel=None: [])
    with pytest.raises(background_jobs.JobCancelled):
        _REAL_CORE_BLOCKS("cu126", "gpu_torch_job")


def test_setup_not_ok_when_cuda_is_still_unavailable(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch)
    monkeypatch.setattr(torch_svc, "verify_torch", lambda: {**GOOD_VERIFY, "cuda_available": False})
    _fake_pip(monkeypatch, [])
    out = torch_svc.setup_gpu_torch("cu128", confirm=True)
    assert out["ok"] is False and "driver" in out["hint"]


def test_setup_skips_verify_when_pip_fails(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch)
    monkeypatch.setattr(torch_svc, "verify_torch", lambda: pytest.fail("no verify"))
    seen = []
    _fake_pip(monkeypatch, seen, returncode=1)
    out = torch_svc.setup_gpu_torch("cu128", confirm=True)
    assert out["ok"] is False and out["verify"] is None and len(seen) == 1


@pytest.mark.parametrize("driver,variant,msg", [
    (None, "cu128", "No NVIDIA GPU"), ("522.06", "cu128", "too old"),
    ("581.42", "rocm", "Unknown"),
])
def test_setup_refusals(monkeypatch, driver, variant, msg):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch, driver)
    monkeypatch.setattr(diagnostics.platform, "system", lambda: "Windows")
    monkeypatch.setattr(packages_svc, "stream_tree", lambda *a, **k: pytest.fail("no pip"))
    with pytest.raises(admin_svc.AdminActionNotPossible, match=msg):
        torch_svc.setup_gpu_torch(variant, confirm=True)


def test_setup_refuses_an_unsupported_python(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch)
    monkeypatch.setattr(diagnostics_torch, "TORCH_SUPPORTED_PYTHON", ((3, 99), (3, 99)))
    with pytest.raises(admin_svc.AdminActionNotPossible, match="Python"):
        torch_svc.setup_gpu_torch(confirm=True)


def test_cpu_variant_needs_no_gpu(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch, None)
    monkeypatch.setattr(torch_svc, "verify_torch", lambda: {
        **GOOD_VERIFY, "torch": "2.11.0+cpu", "cuda_available": False})
    seen = []
    _fake_pip(monkeypatch, seen)
    out = torch_svc.setup_gpu_torch(confirm=True)     # default without a GPU: cpu
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
    out = torch_svc.get_gpu_torch_status()
    assert out["state"] == state and out["probe"] is None
    assert out["recommended"]["variant"] == ("cu128" if gpu else "cpu")


def _captured(stdout="", timed_out=False, cancelled=False):
    return proc_run.CapturedRun(0, stdout, "", timed_out, cancelled)


def test_verify_runs_in_a_subprocess_and_redacts(monkeypatch):
    seen = {}

    def run(cmd, timeout, **kw):
        seen.update(kw, cmd=cmd, timeout=timeout)
        return _captured('{"torch": "2.11.0+cu128", "cuda_available": false, '
                         '"error": "RuntimeError: at /home/someone/venv/torch"}\n')
    monkeypatch.setattr(proc_run, "run_captured", run)
    out = torch_svc.verify_torch()
    assert seen["timeout"] == diagnostics_torch.TORCH_VERIFY_TIMEOUT_SECONDS
    assert seen["cmd"][1:3] == ["-c", diagnostics_torch.TORCH_VERIFY_SCRIPT]
    assert out["torch"] == "2.11.0+cu128" and out["cuda_available"] is False
    assert "/home/someone" not in out["error"]


def test_verify_timeout(monkeypatch):
    monkeypatch.setattr(proc_run, "run_captured",
                        lambda cmd, timeout, **kw: _captured(timed_out=True))
    assert "too long" in torch_svc.verify_torch()["error"]


def test_verify_passes_the_cancel_probe_to_the_runner(monkeypatch):
    seen = {}

    def run(cmd, timeout, cancel=None, **kw):
        seen["cancel"] = cancel
        return _captured(cancelled=True)
    monkeypatch.setattr(proc_run, "run_captured", run)
    probe = lambda: True      # noqa: E731
    assert torch_svc.verify_torch(cancel=probe)["error"] == "cancelled" and seen["cancel"] is probe


def test_cuda_check_never_queues_behind_another(monkeypatch):
    monkeypatch.setattr(torch_svc, "_verify_torch_once", lambda: pytest.fail("no second check"))
    assert torch_svc._VERIFY_LOCK.acquire(blocking=False)
    try:
        with pytest.raises(admin_svc.AdminActionStale):
            torch_svc.verify_torch(blocking=False)
    finally:
        torch_svc._VERIFY_LOCK.release()


def test_cuda_check_refused_while_a_job_runs(monkeypatch):
    from services import library_admin_service
    monkeypatch.setattr(library_admin_service, "any_job_running", lambda: True)
    monkeypatch.setattr(torch_svc, "_verify_torch_once", lambda: pytest.fail("no check"))
    with pytest.raises(admin_svc.AdminActionJobsRunning):
        torch_svc.check_gpu_torch()


# ---- Volta (compute 7.0): the cu126 build ----

def _smi(monkeypatch, stdout, returncode=0):
    monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda n: "/usr/bin/nvidia-smi")

    def run(cmd, **kw):
        if returncode:
            raise subprocess.CalledProcessError(returncode, cmd)
        return subprocess.CompletedProcess(cmd, 0, stdout, "")
    monkeypatch.setattr(diagnostics_torch.subprocess, "run", run)


def test_cu126_is_the_same_triple_from_its_own_index():
    spec = diagnostics_torch.TORCH_VARIANTS["cu126"]
    assert spec["versions"] == {"torch": "2.11.0+cu126", "torchvision": "0.26.0+cu126",
                                "torchaudio": "2.11.0+cu126"}
    assert spec["needs_nvidia"] is True
    first, second = diagnostics_torch.torch_setup_pip_args("cu126")
    assert "https://download.pytorch.org/whl/cu126" in first
    assert "torch==2.11.0+cu126" in second


@pytest.mark.parametrize("stdout,cap", [("7.0\n", 7.0), ("8.6\n7.0\n", 7.0), ("8.9\n", 8.9),
                                         ("[N/A]\n", None), ("", None)])
def test_lowest_compute_capability_reads_nvidia_smi(monkeypatch, stdout, cap):
    _smi(monkeypatch, stdout)
    assert diagnostics_torch.lowest_compute_capability() == cap


def test_lowest_compute_capability_is_none_when_nvidia_smi_is_missing_or_old(monkeypatch):
    monkeypatch.setattr(diagnostics_torch.shutil, "which", lambda n: None)
    assert diagnostics_torch.lowest_compute_capability() is None
    _smi(monkeypatch, "", returncode=2)     # old drivers reject compute_cap
    assert diagnostics_torch.lowest_compute_capability() is None


def test_recommendation_is_cu126_below_compute_7_5_and_says_why(monkeypatch):
    _gpu(monkeypatch)
    monkeypatch.setattr(diagnostics_torch, "lowest_compute_capability", lambda: 7.0)
    variant, reason = diagnostics_torch.recommended_gpu_variant()
    assert variant == "cu126" and "7.0" in reason and "CUDA 12.8" in reason
    monkeypatch.setattr(diagnostics_torch, "lowest_compute_capability", lambda: 7.5)
    assert diagnostics_torch.recommended_gpu_variant() == ("cu128", None)
    monkeypatch.setattr(diagnostics_torch, "lowest_compute_capability", lambda: None)
    assert diagnostics_torch.recommended_gpu_variant() == ("cu128", None)


def test_status_and_default_setup_pick_cu126_for_a_v100(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch)
    monkeypatch.setattr(diagnostics_torch, "lowest_compute_capability", lambda: 7.0)
    _versions(monkeypatch)
    status = torch_svc.get_gpu_torch_status()
    assert status["recommended"]["variant"] == "cu126" and "7.0" in status["recommended_reason"]
    assert [v["variant"] for v in status["variants"]] == ["cu128", "cu126", "cpu"]
    # The driver is judged against the build it recommends, not CUDA 12.8.
    monkeypatch.setattr(diagnostics_torch.platform, "system", lambda: "Windows")
    _gpu(monkeypatch, driver="536.25")
    nvidia = torch_svc.get_gpu_torch_status()["nvidia"]
    assert (nvidia["status"], nvidia["recommended"], nvidia["cuda"]) == ("ok", "528.33", "12.6")
    seen = []
    _fake_pip(monkeypatch, seen)
    monkeypatch.setattr(torch_svc, "verify_torch", lambda: {
        **GOOD_VERIFY, "torch": "2.11.0+cu126"})
    out = torch_svc.setup_gpu_torch(confirm=True)
    assert out["variant"] == "cu126" and out["ok"] is True
    assert "https://download.pytorch.org/whl/cu126" in seen[0][0]


def test_the_driver_is_judged_against_the_installed_cuda_build(monkeypatch):
    # A newer card with cu126 installed on purpose needs only CUDA 12.6's driver.
    monkeypatch.setattr(diagnostics_torch.platform, "system", lambda: "Windows")
    _gpu(monkeypatch, driver="536.25")
    monkeypatch.setattr(diagnostics_torch, "lowest_compute_capability", lambda: 8.6)
    _versions(monkeypatch, torch="2.11.0+cu126", torchaudio="2.11.0+cu126")
    status = torch_svc.get_gpu_torch_status()
    assert status["recommended"]["variant"] == "cu128"
    assert (status["nvidia"]["status"], status["nvidia"]["cuda"]) == ("ok", "12.6")
    # And a V100 that has cu128 installed is told what CUDA 12.8 needs.
    monkeypatch.setattr(diagnostics_torch, "lowest_compute_capability", lambda: 7.0)
    _versions(monkeypatch, torch="2.11.0+cu128", torchaudio="2.11.0+cu128")
    nvidia = torch_svc.get_gpu_torch_status()["nvidia"]
    assert (nvidia["status"], nvidia["cuda"]) == ("old", "12.8")


@pytest.mark.parametrize("version, variant", [
    ("2.11.0+cu126", "cu126"), ("2.11.0+CU128", "cu128"), ("2.11.0+cpu", None),
    ("2.11.0", None), ("2.11.0+cu118", None), (None, None)])
def test_installed_cuda_variant(version, variant):
    assert diagnostics_torch.installed_cuda_variant({"torch": {"version": version}}) == variant


class _FakeTorch:
    """torch as the verify script sees it: `cards` is [(name, (major, minor))]."""
    __version__ = "2.11.0+cu128"

    def __init__(self, cards, arch_list):
        class _V:
            cuda = "12.8"
        self.version = _V()
        self.zeros_on = []
        outer = self

        class _Cuda:
            is_available = staticmethod(lambda: bool(cards))
            device_count = staticmethod(lambda: len(cards))
            get_device_name = staticmethod(lambda i: cards[i][0])
            get_device_capability = staticmethod(lambda i: cards[i][1])
            get_arch_list = staticmethod(lambda: arch_list)
        self.cuda = _Cuda()
        self.zeros = lambda n, device=None: outer.zeros_on.append(device)


def _run_verify(monkeypatch, capsys, fake):
    monkeypatch.setitem(__import__("sys").modules, "torch", fake)
    exec(diagnostics_torch.TORCH_VERIFY_SCRIPT, {})
    return diagnostics_torch.parse_torch_verify_output(capsys.readouterr().out)


def test_verify_script_reports_a_gpu_the_build_has_no_kernels_for(monkeypatch, capsys):
    fake = _FakeTorch([("Tesla V100-PCIE-32GB", (7, 0))], ["sm_75", "sm_80", "sm_86", "sm_120"])
    data = _run_verify(monkeypatch, capsys, fake)
    assert data["cuda_available"] is True and "error" not in data
    assert data["devices"] == [{"index": 0, "name": "Tesla V100-PCIE-32GB",
                                "capability": "7.0", "supported": False}]
    assert fake.zeros_on == []     # a kernel launch on it would only fail later


def test_verify_script_checks_every_gpu_not_just_device_0(monkeypatch, capsys):
    fake = _FakeTorch([("RTX 3080 Ti", (8, 6)), ("Tesla V100", (7, 0))], ["sm_75", "sm_86"])
    data = _run_verify(monkeypatch, capsys, fake)
    assert [d["supported"] for d in data["devices"]] == [True, False]
    assert fake.zeros_on == ["cuda:0"]


def test_verify_script_accepts_ptx_from_an_older_arch(monkeypatch, capsys):
    fake = _FakeTorch([("Tesla V100", (7, 0))], ["sm_80", "compute_70"])
    assert _run_verify(monkeypatch, capsys, fake)["devices"][0]["supported"] is True


def test_no_kernels_message_names_the_card_and_the_cu126_fix():
    msg = diagnostics_torch.unsupported_gpu_message(
        [{"index": 0, "name": "Tesla V100", "capability": "7.0", "supported": False},
         {"index": 1, "name": "RTX 3080 Ti", "capability": "8.6", "supported": True}])
    assert msg == ("This PyTorch build has no kernels for your GPU Tesla V100 (compute 7.0); "
                   "install the cu126 build.")
    assert diagnostics_torch.unsupported_gpu_message([{"supported": True}]) is None
    assert diagnostics_torch.unsupported_gpu_message(None) is None


def test_setup_fails_with_the_no_kernels_message_instead_of_passing(monkeypatch):
    _no_jobs(monkeypatch)
    _gpu(monkeypatch)
    monkeypatch.setattr(torch_svc, "verify_torch", lambda: {
        **GOOD_VERIFY, "error": diagnostics_torch.unsupported_gpu_message(
            [{"name": "Tesla V100", "capability": "7.0", "supported": False}])})
    _fake_pip(monkeypatch, [])
    out = torch_svc.setup_gpu_torch("cu128", confirm=True)
    assert out["ok"] is False and "can't run on your GPU" in out["hint"]
    assert "install the cu126 build" in out["verify"]["error"]


def test_verify_torch_surfaces_the_no_kernels_message(monkeypatch):
    devices = [{"index": 0, "name": "Tesla V100", "capability": "7.0", "supported": False}]
    monkeypatch.setattr(torch_svc.proc_run, "run_captured", lambda *a, **k: _captured(
        __import__("json").dumps({"torch": "2.11.0+cu128", "cuda_available": True,
                                  "devices": devices})))
    out = torch_svc.verify_torch()
    assert "no kernels for your GPU" in out["error"]


def test_verify_script_ignores_the_suffix_on_ptx_arch_names(monkeypatch, capsys):
    fake = _FakeTorch([("Tesla V100", (7, 0))], ["sm_90a", "compute_90a"])
    data = _run_verify(monkeypatch, capsys, fake)
    assert "error" not in data and data["devices"][0]["supported"] is False


def test_no_kernels_message_on_the_cu126_build_does_not_suggest_it_again():
    devices = [{"index": 0, "name": "Tesla V100", "capability": "7.0", "supported": False}]
    msg = diagnostics_torch.unsupported_gpu_message(devices, "2.11.0+cu126")
    assert msg == "This PyTorch build has no kernels for your GPU Tesla V100 (compute 7.0)."


def test_the_module_and_its_embedded_verify_script_compile_without_warnings():
    # A "\d" in a non-raw string only warns at import, and start.bat shows that
    # warning to the owner; the script string is compiled again by the child.
    import warnings
    path = diagnostics_torch.__file__
    with open(path, encoding="utf-8") as f:
        source = f.read()
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(source, path, "exec")
        compile(diagnostics_torch.TORCH_VERIFY_SCRIPT, "<verify script>", "exec")
