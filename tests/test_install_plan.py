"""What an install would change and whether to run it now (install_plan.py).
pip's report is faked JSON, loaded files are faked modules, and Windows paths
go through ntpath; no real pip."""
import json
import ntpath
import os
from importlib import metadata
from pathlib import PurePosixPath
from types import ModuleType, SimpleNamespace

import pytest

import install_plan as plan_mod
import install_registry


def report(*pairs):
    return {"version": "1", "install": [{"metadata": {"name": n, "version": v}} for n, v in pairs]}


def build(rep, installed, windows=True, in_use=lambda name: False, **kw):
    return plan_mod.build_plan(kw.pop("keys", ["paddleocr"]), report=rep, installed=installed,
                               windows=windows, in_use=in_use, mins=kw.pop("mins", {}),
                               required=kw.pop("required", set()), **kw)


def test_parse_report_reads_names_and_versions():
    doc = report(("PaddleOCR", "3.2.0"), ("opencv_contrib_python", "4.10.0.84"))
    assert plan_mod.parse_report(doc) == [("paddleocr", "3.2.0"), ("opencv-contrib-python", "4.10.0.84")]
    assert plan_mod.parse_report({}) == [] and plan_mod.parse_report(None) == []
    assert plan_mod.parse_report({"install": [{"metadata": {"name": 1}}, {}]}) == []


def test_plan_lists_installs_and_changes_in_plain_words():
    p = build(report(("paddleocr", "3.2.0"), ("paddlepaddle", "3.0.0"), ("numpy", "2.3.5"),
                     ("requests", "2.33.0")),
              {"numpy": "2.5.3", "requests": "2.33.0"}, windows=False)
    assert p["summary"] == ["Install paddleocr 3.2.0, paddlepaddle 3.0.0.",
                            "Change numpy 2.5.3 -> 2.3.5 (downgrade)."]
    assert [c["name"] for c in p["changes"]] == ["paddleocr", "paddlepaddle", "numpy"]   # requests unchanged
    assert p["before"] == {"numpy": "2.5.3"}


def test_upgrade_is_not_called_a_downgrade():
    p = build(report(("numpy", "2.6.0")), {"numpy": "2.5.3"}, windows=False)
    assert p["changes"][0]["kind"] == "upgrade" and p["needs_confirm"] == []


def test_downgrading_something_the_app_requires_needs_extra_confirmation():
    p = build(report(("numpy", "2.3.5")), {"numpy": "2.5.3"}, windows=False, required={"numpy"})
    assert p["needs_confirm"] and "numpy 2.5.3 -> 2.3.5" in p["needs_confirm"][0]
    assert p["blocked"] == []


def test_downgrading_a_package_with_a_declared_minimum_needs_confirmation():
    p = build(report(("pillow", "11.0")), {"pillow": "12.3"}, windows=False, mins={"pillow": "12.3"})
    assert p["needs_confirm"]


def test_downgrading_something_the_app_does_not_need_is_listed_but_not_blocked():
    p = build(report(("pyyaml", "5.4")), {"pyyaml": "6.0"}, windows=False)
    assert p["needs_confirm"] == [] and p["changes"][0]["kind"] == "downgrade"


def test_a_second_opencv_flavour_is_refused_with_a_plain_message():
    # A key that is not on the contrib swap list: only paddleocr gets the swap.
    p = build(report(("pytesseract", "0.3.13"), ("opencv-contrib-python", "4.10.0.84")),
              {"opencv-python": "4.9.0.80"}, windows=False, keys=["pytesseract"])
    assert p["blocked"] and "two OpenCV packages" in p["blocked"][0]
    assert "opencv-contrib-python" in p["blocked"][0] and "opencv-python" in p["blocked"][0]


def test_upgrading_the_opencv_you_have_is_not_a_clash():
    p = build(report(("opencv-python", "4.11.0.86")), {"opencv-python": "4.9.0.80"}, windows=False)
    assert p["blocked"] == []


def test_two_flavours_the_owner_already_had_do_not_block_an_unrelated_install():
    p = build(report(("paddleocr", "3.2.0")),
              {"opencv-python": "4.9", "opencv-contrib-python": "4.9"}, windows=False)
    assert p["blocked"] == []


def test_fresh_opencv_install_is_not_a_clash():
    p = build(report(("opencv-python", "4.11.0.86")), {}, windows=False)
    assert p["blocked"] == [] and p["changes"][0]["kind"] == "install"


def test_loaded_package_that_pip_would_replace_waits_for_restart_on_windows():
    p = build(report(("numpy", "2.6.0"), ("paddleocr", "3.2.0")), {"numpy": "2.5.3"},
              in_use=lambda name: name == "numpy")
    assert p["mode"] == "restart" and p["loaded"] == ["numpy"]
    assert "Windows can't replace" in p["note"]


def test_nothing_loaded_installs_immediately_as_before():
    p = build(report(("numpy", "2.6.0"), ("paddleocr", "3.2.0")), {"numpy": "2.5.3"})
    assert p["mode"] == "now" and p["loaded"] == []


def test_a_new_package_needs_no_check_because_nothing_is_replaced():
    calls = []
    p = build(report(("paddleocr", "3.2.0")), {}, in_use=lambda n: calls.append(n) or True)
    assert p["mode"] == "now" and calls == []


def test_off_windows_files_can_be_replaced_so_the_fast_path_stays():
    p = build(report(("numpy", "2.6.0")), {"numpy": "2.5.3"}, windows=False, in_use=lambda n: True)
    assert p["mode"] == "now" and p["loaded"] == []


def test_a_running_job_means_restart_on_windows():
    p = build(report(("paddleocr", "3.2.0")), {}, jobs_running=True)
    assert p["mode"] == "restart" and "job is running" in p["note"]


def test_no_preview_means_restart_on_windows_and_now_elsewhere(monkeypatch):
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys, specs=None: (None, "pip could not work out the change (are you online?)."))
    win = plan_mod.build_plan(["paddleocr"], windows=True)
    assert win["available"] is False and win["mode"] == "restart" and "online" in win["note"]
    assert plan_mod.build_plan(["paddleocr"], windows=False)["mode"] == "now"


def test_running_now_without_a_preview_is_a_risk_to_accept(monkeypatch):
    """Off Windows an unpreviewed install would run now with no clash or
    downgrade check, so the owner has to accept that; on Windows it waits."""
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys, specs=None: (None, "this pip cannot preview an install."))
    assert plan_mod.build_plan(["paddleocr"], windows=False)["needs_confirm"] == [plan_mod.UNCHECKED_RISK]
    assert plan_mod.build_plan(["paddleocr"], windows=True)["needs_confirm"] == []


def test_pip_saying_the_packages_conflict_blocks_the_plan(monkeypatch):
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys, specs=None: (None, "conflict"))
    p = plan_mod.build_plan(["paddleocr"], windows=True)
    assert p["blocked"] and "can't be installed together" in p["blocked"][0]


def test_unknown_keys_and_cuda_torch_are_blocked_before_pip_runs(monkeypatch):
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys, specs=None: pytest.fail("pip must not run"))
    assert plan_mod.build_plan(["evil"])["blocked"] == ["Unknown or non-installable package."]
    monkeypatch.setattr(install_registry.shutil, "which", lambda n: "/x/nvidia-smi")
    assert "GPU PyTorch" in plan_mod.build_plan(["torch"])["blocked"][0]


# --- the dry run itself ---------------------------------------------------------

def fake_runner(doc=None, rc=0, text=""):
    seen = {}

    def run(argv, timeout):
        seen["argv"], seen["timeout"] = argv, timeout
        out = argv[argv.index("--report") + 1]
        if doc is not None:
            with open(out, "w", encoding="utf-8") as f:
                json.dump(doc, f)
        return SimpleNamespace(returncode=rc, stdout=text, stderr="", timed_out=False)
    run.seen = seen
    return run


def test_an_unparsable_version_is_neither_an_upgrade_nor_a_downgrade():
    # pip's report lists only what it will install, so a differing version
    # it can't order is still a replacement.
    for new, have in (("weird!!", "1.0"), ("1.0", "weird!!")):
        assert plan_mod._classify([("pkg", new)], {"pkg": have}) == [
            {"name": "pkg", "from": have, "to": new, "kind": "change"}]
    assert plan_mod._classify([("pkg", "weird!!")], {"pkg": "weird!!"}) == []
    assert plan_mod._classify([("pkg", "weird!!")], {})[0]["kind"] == "install"
    p = build(report(("cv2legacy", "4.0")), {"cv2legacy": "4.0-legacy!!"},
              in_use=lambda name: name == "cv2legacy", required={"cv2legacy"})
    assert p["loaded"] == ["cv2legacy"] and p["mode"] == "restart"
    assert p["before"] == {"cv2legacy": "4.0-legacy!!"} and p["needs_confirm"] == []
    assert p["summary"] == ["Change cv2legacy 4.0-legacy!! -> 4.0."]


def test_a_pip_that_could_not_be_reaped_gets_its_own_message():
    def unreaped(argv, timeout):
        return SimpleNamespace(returncode=None, stdout="", stderr="", timed_out=False)
    why = plan_mod.run_dry_run(["paddleocr"], runner=unreaped)[1]
    assert "did not stop in time" in why and "online" not in why


def test_dry_run_runs_the_registry_argv_with_dry_run_and_report():
    run = fake_runner(report(("paddleocr", "3.2.0")))
    doc, why = plan_mod.run_dry_run(["paddleocr", "paddlepaddle"], runner=run)
    argv = run.seen["argv"]
    assert why is None and doc["install"]
    assert argv[1:4] == ["-m", "pip", "install"] and "--dry-run" in argv
    assert argv[argv.index("paddleocr"):argv.index("paddleocr") + 2] == ["paddleocr", "paddlepaddle"]
    assert run.seen["timeout"] == plan_mod.PLAN_TIMEOUT_SECONDS
    assert not any(a.startswith("--index-url") for a in argv)
    assert not os.path.exists(argv[argv.index("--report") + 1])        # temp report removed


def test_dry_run_failure_reasons():
    assert plan_mod.run_dry_run(["paddleocr"], runner=fake_runner(rc=1, text="ResolutionImpossible"))[1] == "conflict"
    assert "online" in plan_mod.run_dry_run(["paddleocr"], runner=fake_runner(rc=1, text="no network"))[1]
    assert "cannot preview" in plan_mod.run_dry_run(["paddleocr"], runner=fake_runner())[1]

    def broken(argv, timeout):
        raise OSError("no pip")
    assert "could not be run" in plan_mod.run_dry_run(["paddleocr"], runner=broken)[1]

    def slow(argv, timeout):
        return SimpleNamespace(returncode=None, stdout="", stderr="", timed_out=True)
    assert "could not be run" in plan_mod.run_dry_run(["paddleocr"], runner=slow)[1]


# --- is it loaded? ----------------------------------------------------------------

SITE = "C:\\Python312\\Lib\\site-packages"


class FakeDist:
    def __init__(self, files):
        self.files = [PurePosixPath(f) for f in files]

    def locate_file(self, f):
        return ntpath.join(SITE, *PurePosixPath(f).parts)


def module(path):
    m = ModuleType("m")
    m.__file__ = path
    return m


def winnorm(p):
    return ntpath.normcase(ntpath.normpath(p))


def check(files, modules):
    return plan_mod.dist_in_use("opencv-python", modules=modules, norm=winnorm,
                                lookup=lambda n: FakeDist(files))


def test_loaded_compiled_extension_is_detected_through_its_path_case_insensitively():
    files = ["cv2/__init__.py", "cv2/cv2.pyd", "opencv_python-4.9.dist-info/METADATA"]
    mods = {"x": module("c:\\python312\\LIB\\site-packages\\cv2\\cv2.pyd")}
    assert check(files, mods) is True


def test_imported_top_level_of_a_compiled_dist_counts_even_if_the_pyd_is_not_listed():
    files = ["numpy/__init__.py", "numpy/_core/_multiarray_umath.pyd", "numpy.libs/openblas.dll"]
    assert check(files, {"numpy": module("c:\\elsewhere\\numpy\\__init__.py")}) is True


def test_a_dll_folder_marks_its_package_as_in_use_when_that_package_is_imported():
    files = ["onnxruntime/capi/onnxruntime.dll"]
    assert check(files, {"onnxruntime": module("x")}) is True


def test_a_compiled_dist_counts_when_only_its_top_level_is_imported_even_if_no_listed_file_is_loaded():
    # Conservative on purpose: a loaded helper DLL never shows up in sys.modules.
    files = ["scipy/__init__.py", "scipy/linalg/_flapack.pyd"]
    assert check(files, {"scipy": module("c:\\elsewhere\\scipy\\__init__.py")}) is True


def test_compiled_dist_that_was_never_imported_is_free_to_replace():
    assert check(["cv2/cv2.pyd", "cv2/__init__.py"], {"os": module("c:\\py\\os.py")}) is False


def test_pure_python_dist_is_never_in_use():
    assert check(["pyyaml_x/__init__.py", "pyyaml_x/core.py"], {"pyyaml_x": module("a.py")}) is False


def test_a_dist_that_is_not_installed_is_not_in_use():
    def missing(name):
        raise metadata.PackageNotFoundError(name)
    assert plan_mod.dist_in_use("nope", modules={}, lookup=missing) is False


def test_real_loaded_module_is_found_on_this_machine():
    import sys
    # json is pure Python and stdlib: no dist, so not in use; a real dist with
    # compiled files that this test run imported (pydantic_core) is.
    assert plan_mod.dist_in_use("definitely-not-installed-xyz") is False
    try:
        import pydantic_core  # noqa: F401
    except ImportError:
        pytest.skip("pydantic_core not installed")
    assert "pydantic_core" in sys.modules
    assert plan_mod.dist_in_use("pydantic-core") is True


def test_a_plan_with_no_preview_keeps_its_restart_mode_and_note_when_in_use_is_rechecked(monkeypatch):
    # Windows, pip cannot preview: the plan says restart and why, whatever is in use.
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys, specs=None: (None, "no network"))
    plan = plan_mod.build_plan(["paddleocr"], windows=True, installed={}, in_use=lambda n: False)
    assert plan["available"] is False and plan["mode"] == "restart"
    note = plan["note"]
    assert "install when you restart" in note
    again = plan_mod.apply_in_use(plan, jobs_running=False, windows=True, in_use=lambda n: False)
    assert again["mode"] == "restart" and again["note"] == note


# --- core packages and PyTorch -----------------------------------------------------

def test_a_fresh_install_below_the_app_minimum_is_blocked():
    p = build(report(("numpy", "2.3.5")), {}, windows=False, mins={"numpy": "2.5.4"})
    assert p["blocked"] == ["Installing numpy 2.3.5 would leave it older than the 2.5.4 Baihe "
                            "needs, so nothing was changed."]


def test_pulling_torch_from_pypi_onto_an_nvidia_pc_is_blocked():
    """demucs installed before GPU PyTorch brought in PyPI's CPU torch."""
    rep = report(("demucs", "4.0.1"), ("torch", "2.11.0"), ("torchaudio", "2.11.0"))
    p = build(rep, {}, keys=["demucs"], windows=False, nvidia=True)
    assert len(p["blocked"]) == 1
    assert "torch 2.11.0, torchaudio 2.11.0" in p["blocked"][0]
    assert "Set up GPU PyTorch first" in p["blocked"][0]
    assert build(rep, {}, keys=["demucs"], windows=False, nvidia=False)["blocked"] == []


def test_replacing_an_installed_torch_is_blocked_with_or_without_a_gpu():
    rep = report(("demucs", "4.0.1"), ("torch", "2.14.1"))
    for nvidia in (True, False):
        p = build(rep, {"torch": "2.11.0+cu126"}, keys=["demucs"], windows=False, nvidia=nvidia)
        assert p["blocked"] == ["This would replace torch 2.11.0+cu126 with 2.14.1, so nothing "
                                "was changed. PyTorch is changed only under GPU PyTorch."]



CONFLICT_OUT = """ERROR: Cannot install paddleocr==3.3.0 because these package versions have conflicting dependencies.

The conflict is caused by:
    The user requested (constraint) numpy>=2.5.4
    paddlex 3.3.0 depends on numpy<2.5.4

To fix this you could try to:
"""


def test_a_pip_conflict_names_what_clashes():
    run = fake_runner(rc=1, text=CONFLICT_OUT)
    assert plan_mod.run_dry_run(["paddleocr"], runner=run) == (None, "conflict\npip's reason: Baihe keeps numpy>=2.5.4; "
                                                                "paddlex 3.3.0 depends on numpy<2.5.4.")


def test_the_plan_shows_the_conflict_reason(monkeypatch):
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys, specs=None: (
        None, plan_mod.CONFLICT + "\npip's reason: paddlex 3.3.0 depends on numpy<2.5.4."))
    p = plan_mod.build_plan(["paddleocr"], windows=True)
    assert p["blocked"] == ["pip says these packages can't be installed together with what you "
                            "have, so nothing was changed. pip's reason: paddlex 3.3.0 depends "
                            "on numpy<2.5.4."]


def test_a_conflict_with_the_pinned_torch_says_so():
    text = ("The conflict is caused by:\n    The user requested (constraint) torch==2.11.0+cu126\n"
            "    demucs 4.1 depends on torch>=2.12\n")
    kind, reason = plan_mod.pip_failure(text)
    assert kind == plan_mod.CONFLICT
    assert reason.startswith("It needs a different PyTorch than the one installed "
                             "(torch==2.11.0+cu126)")


# Verbatim pip 24 output (only the object address shortened), captured for these tests.
OFFLINE_OUT = """Looking in indexes: http://127.0.0.1:9/simple
WARNING: Retrying (Retry(total=0, connect=None, read=None, redirect=None, status=None)) after connection broken by 'NewConnectionError('<pip._vendor.urllib3.connection.HTTPConnection object at 0x7f>: Failed to establish a new connection: [Errno 111] Connection refused')': /simple/requests-toolbelt/
ERROR: Could not find a version that satisfies the requirement requests-toolbelt (from versions: none)
ERROR: No matching distribution found for requests-toolbelt
"""
REAL_CONFLICT_OUT = """Collecting requests==2.32.3
  Downloading requests-2.32.3-py3-none-any.whl.metadata (4.6 kB)
INFO: pip is looking at multiple versions of requests to determine which version is compatible with other requirements. This could take a while.
ERROR: Cannot install requests==2.32.3 because these package versions have conflicting dependencies.

The conflict is caused by:
    requests 2.32.3 depends on urllib3<3 and >=1.21.1
    The user requested (constraint) urllib3<1

To fix this you could try to:
1. loosen the range of package versions you've specified
2. remove package versions to allow pip attempt to solve the dependency conflict

ERROR: ResolutionImpossible: for help visit https://pip.pypa.io/en/latest/topics/dependency-resolution/#dealing-with-dependency-conflicts
"""
MODERN_PYTHON_OUT = """ERROR: Ignored the following versions that require a different python version: 0.7 Requires-Python >=3.6, <3.7; 0.8 Requires-Python >=3.6, <3.7
ERROR: Could not find a version that satisfies the requirement dataclasses==0.8 (from versions: 0.1, 0.2, 0.3, 0.4, 0.5, 0.6)
ERROR: No matching distribution found for dataclasses==0.8
"""


@pytest.mark.parametrize("text,expected", [
    ("ERROR: Package 'audio-separator' requires a different Python: 3.14.0 not in '<3.14,>=3.10'",
     "audio-separator needs Python <3.14,>=3.10, and Baihe runs on 3.14.0."),
    ("ERROR: Could not find a version that satisfies the requirement funasr (from versions: none)\n"
     "ERROR: No matching distribution found for funasr", "pip found no release of funasr"),
    (MODERN_PYTHON_OUT, "pip found no release of dataclasses that runs on Python "),
])
def test_a_package_with_no_release_for_this_python_is_blocked_not_queued(monkeypatch, text, expected):
    """Queued for the next start it would only fail the same way then."""
    kind, reason = plan_mod.explain_pip_failure(text)
    assert kind == plan_mod.UNAVAILABLE and reason.startswith(expected)
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys, specs=None: (None, kind + "\n" + reason))
    p = plan_mod.build_plan(["paddleocr"], windows=True)
    assert p["mode"] == "now" and p["blocked"] == [reason + " Nothing was changed."]


def test_an_unreachable_index_is_named():
    why = plan_mod.run_dry_run(["paddleocr"], runner=fake_runner(
        rc=1, text="WARNING: Retrying ... NewConnectionError('Failed to establish a new connection')"))[1]
    assert why == "pip couldn't reach the package index (are you online?)."


# --- GPU PyTorch setup --------------------------------------------------------------

def test_torch_setup_preview_runs_its_dependency_step_from_the_fixed_index():
    run = fake_runner(report(("torch", "2.11.0+cu126")))
    assert plan_mod.torch_setup_blocks("cu126", runner=run, installed={}, mins={}, required=set()) == []
    argv = run.seen["argv"]
    assert "torch==2.11.0+cu126" in argv and "--dry-run" in argv and "--force-reinstall" not in argv
    assert argv[argv.index("--index-url") + 1] == "https://download.pytorch.org/whl/cu126"


def test_torch_setup_refuses_to_drop_a_core_package():
    run = fake_runner(report(("torch", "2.11.0+cu126"), ("numpy", "2.3.5"), ("pillow", "12.0"),
                             ("sympy", "1.13")))
    blocks = plan_mod.torch_setup_blocks(
        "cu126", runner=run, installed={"torch": "2.14.1", "numpy": "2.5.4", "pillow": "12.3",
                                        "sympy": "1.14"},
        mins={"numpy": "2.5.4"}, required={"numpy", "pillow"})
    assert blocks == [
        "numpy 2.5.4 -> 2.3.5 would leave it older than the 2.5.4 Baihe needs, so nothing was changed.",
        "pillow 12.3 -> 12.0 would downgrade a package Baihe itself needs, so nothing was changed."]


def test_torch_setup_refuses_a_conflict_and_lets_an_offline_preview_through():
    blocks = plan_mod.torch_setup_blocks("cu126", runner=fake_runner(rc=1, text=CONFLICT_OUT),
                                         installed={}, mins={}, required=set())
    assert len(blocks) == 1 and "paddlex 3.3.0 depends on numpy<2.5.4" in blocks[0]
    assert plan_mod.torch_setup_blocks("cu126", runner=fake_runner(rc=1, text="offline"),
                                       installed={}, mins={}, required=set()) == []


def test_the_dry_run_keeps_the_lines_pip_explains_a_conflict_with():
    run = fake_runner(rc=1, text=REAL_CONFLICT_OUT)
    assert plan_mod.run_dry_run(["paddleocr"], runner=run) == (
        None, "conflict\npip's reason: requests 2.32.3 depends on urllib3<3 and >=1.21.1; "
              "Baihe keeps urllib3<1.")
    assert "--quiet" not in run.seen["argv"]       # it hides "The conflict is caused by"


def test_a_modern_pip_python_mismatch_names_the_newest_release():
    kind, reason = plan_mod.pip_failure(MODERN_PYTHON_OUT)
    assert kind == plan_mod.UNAVAILABLE
    assert reason.endswith("(newest: 0.8 Requires-Python >=3.6, <3.7).")


def test_being_offline_is_not_read_as_no_release(monkeypatch):
    """pip prints "No matching distribution" after failing to reach the index."""
    assert plan_mod.pip_failure(OFFLINE_OUT) == (
        None, "pip couldn't reach the package index (are you online?).")
    monkeypatch.setattr(plan_mod, "run_dry_run", lambda keys, specs=None: plan_mod._dry_run(
        ["pip"], fake_runner(rc=1, text=OFFLINE_OUT)))
    p = plan_mod.build_plan(["paddleocr"], windows=True)
    assert p["blocked"] == [] and p["mode"] == "restart" and "are you online" in p["note"]
    assert plan_mod.torch_setup_blocks("cu126", runner=fake_runner(rc=1, text=OFFLINE_OUT),
                                       installed={}, mins={}, required=set()) == []


def test_the_torch_setup_preview_can_be_cancelled():
    seen = {}

    def cancelled_run(argv, timeout, cancel=None):
        seen["cancel"] = cancel
        return SimpleNamespace(returncode=None, stdout="", stderr="", timed_out=False,
                               cancelled=True)
    probe = lambda: True        # noqa: E731
    assert plan_mod.torch_setup_blocks("cu126", runner=cancelled_run, installed={}, mins={},
                                       required=set(), cancel=probe) == []
    assert seen["cancel"] is probe
